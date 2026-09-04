"""Local FastAPI service: extension ingest, AI actions, edits, publish.
Also serves docs/ so the viewer runs locally with write access."""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from scout import db
from scout.config import CONFIG, DOCS_DIR, SITES, STATUSES
from scout.ingest import ingest_items
from scout.profiles import sync_seed_profiles
from scout.publish import build_export, git_publish, write_export

app = FastAPI(title="Hoopty Scout")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_ai_lock = asyncio.Lock()


@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    sync_seed_profiles()


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "ai": CONFIG.ai_enabled, "models": {"deep": CONFIG.model_deep, "fast": CONFIG.model_fast},
            "skip_sold": CONFIG.skip_sold}


class IngestPayload(BaseModel):
    site: str
    items: list[dict[str, Any]]
    include_sold: bool | None = None
    full_sync: bool = False


@app.post("/api/ingest")
async def ingest(payload: IngestPayload) -> dict[str, Any]:
    if payload.site not in SITES:
        raise HTTPException(400, f"unknown site {payload.site!r}")
    async with _ai_lock:
        stats = await asyncio.to_thread(ingest_items, payload.site, payload.items, payload.include_sold, True, payload.full_sync)
    return {"ok": True, **stats}


@app.get("/api/export")
def export() -> JSONResponse:
    return JSONResponse(build_export())


@app.get("/api/listings/{listing_id}")
def get_listing(listing_id: int) -> dict[str, Any]:
    row = db.get_listing(listing_id)
    if not row:
        raise HTTPException(404, "not found")
    row["history"] = db.list_snapshots(listing_id)
    return row


class ListingPatch(BaseModel):
    status: str | None = None
    notes: str | None = None
    pinned: bool | None = None
    role: str | None = None
    profile_key: str | None = None


@app.patch("/api/listings/{listing_id}")
def patch_listing(listing_id: int, patch: ListingPatch) -> dict[str, Any]:
    if not db.get_listing(listing_id):
        raise HTTPException(404, "not found")
    updates: dict[str, Any] = {}
    if patch.status is not None:
        if patch.status not in STATUSES:
            raise HTTPException(400, f"status must be one of {STATUSES}")
        updates["status"] = patch.status
    if patch.notes is not None:
        updates["notes"] = patch.notes[:20_000]
    if patch.pinned is not None:
        updates["pinned"] = 1 if patch.pinned else 0
    if patch.role in {"candidate", "comp"}:
        updates["role"] = patch.role
    if patch.profile_key is not None:
        if patch.profile_key and not db.get_profile(patch.profile_key):
            raise HTTPException(400, "unknown profile")
        updates["profile_key"] = patch.profile_key or None
    db.update_listing(listing_id, updates)
    db.log_event("edit", listing_id, str(updates))
    return {"ok": True, **updates}


@app.post("/api/listings/{listing_id}/analyze")
async def analyze(listing_id: int) -> dict[str, Any]:
    row = db.get_listing(listing_id)
    if not row:
        raise HTTPException(404, "not found")
    if not CONFIG.ai_enabled:
        raise HTTPException(400, "ANTHROPIC_API_KEY not set")
    prof = db.get_profile(row["profile_key"]) if row.get("profile_key") else None
    if not prof:
        raise HTTPException(400, "listing has no profile yet; run a sync or assign one")
    from scout.ai.analyze import analyze_listing  # lazy
    peers = [p for p in db.list_listings(role="candidate", profile_key=prof["key"])
             if p["id"] != listing_id and p["availability"] == "active"]
    comps = db.list_listings(role="comp", profile_key=prof["key"])
    snaps = db.list_snapshots(listing_id)
    async with _ai_lock:
        try:
            result = await asyncio.to_thread(analyze_listing, row, prof, snaps, peers, comps)
        except Exception as e:
            db.log_event("analyze_error", listing_id, str(e))
            raise HTTPException(500, f"analysis failed: {e}")
    db.update_listing(listing_id, {"analysis": result, "analyzed_at": db.now(), "analysis_model": CONFIG.model_deep})
    db.log_event("analyzed", listing_id, result.get("verdict", ""))
    return {"ok": True, "analysis": result}


@app.post("/api/listings/{listing_id}/renormalize")
async def renormalize(listing_id: int) -> dict[str, Any]:
    row = db.get_listing(listing_id)
    if not row:
        raise HTTPException(404, "not found")
    db.update_listing(listing_id, {"normalized_at": None})
    item = {"url": row["url"], "title": row.get("title"), "detail": {"text": row.get("raw_text") or "",
            "photos": row.get("photos") or []}, "sold": row["availability"] == "sold"}
    async with _ai_lock:
        stats = await asyncio.to_thread(ingest_items, row["site"], [item], True)
    return {"ok": True, **stats}


@app.get("/api/profiles")
def profiles() -> list[dict[str, Any]]:
    return db.list_profiles()


class ProfilePatch(BaseModel):
    verified: bool | None = None


@app.patch("/api/profiles/{key}")
def patch_profile(key: str, patch: ProfilePatch) -> dict[str, Any]:
    if not db.get_profile(key):
        raise HTTPException(404, "not found")
    if patch.verified is not None:
        db.set_profile_verified(key, patch.verified)
    return {"ok": True}


@app.post("/api/publish")
async def publish() -> dict[str, Any]:
    out = await asyncio.to_thread(git_publish)
    return {"ok": True, "git": out}


@app.post("/api/export/write")
def export_write() -> dict[str, Any]:
    return {"ok": True, "path": str(write_export())}


@app.get("/api/events")
def events() -> list[dict[str, Any]]:
    return db.recent_events()


# Static viewer (local mode). Mounted last so /api wins.
@app.get("/")
def index() -> FileResponse:
    return FileResponse(DOCS_DIR / "index.html")


app.mount("/", StaticFiles(directory=str(DOCS_DIR), html=True), name="site")
