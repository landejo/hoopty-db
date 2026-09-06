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
from scout.policy import POLICY_VERSION
from scout.policy.preferences import MISSIONS
from scout.policy.state import load_state, reset_state, save_state

app = FastAPI(title="Hoopty Scout")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def _no_cache_static(request, call_next):
    """The viewer is edited often; make every load fetch the current files."""
    resp = await call_next(request)
    if not request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp

_ai_lock = asyncio.Lock()
_task: dict[str, Any] = {"active": False}


def _task_start(name: str, total: int | None = None) -> str:
    """Begin a visible task. Returns a token; only the holder may end it, so a
    one-off assessment that overlaps a batch never clobbers the batch's status."""
    import secrets
    token = secrets.token_hex(4)
    _task.update({"active": True, "name": name, "done": 0, "total": total, "current": "", "started": db.now(), "errors": 0, "result": None, "token": token})
    return token


def _task_step(current: str = "", done: int | None = None) -> None:
    if done is not None:
        _task["done"] = done
    else:
        _task["done"] = int(_task.get("done") or 0) + 1
    _task["current"] = current


def _task_end(result: str = "", token: str | None = None) -> None:
    if token is not None and _task.get("token") != token:
        return   # someone else's task is showing; leave it alone
    _task.update({"active": False, "result": result, "ended": db.now()})


@app.get("/api/task")
def task_status() -> dict[str, Any]:
    return _task


@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    sync_seed_profiles()


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "ai": CONFIG.ai_enabled, "models": {"deep": CONFIG.model_deep, "mid": CONFIG.model_mid, "fast": CONFIG.model_fast},
            "skip_sold": CONFIG.skip_sold, "policy_version": POLICY_VERSION}


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


@app.get("/api/listings/by-url")
def listing_by_url(url: str) -> dict[str, Any]:
    row = db.get_listing_by_url(url.rstrip("/")) or db.get_listing_by_url(url.rstrip("/") + "/") or db.get_listing_by_url(url)
    if not row:
        raise HTTPException(404, "not tracked")
    return {"id": row["id"], "title": row.get("title"), "site": row["site"]}


@app.get("/api/listings/{listing_id}")
def get_listing(listing_id: int) -> dict[str, Any]:
    row = db.get_listing(listing_id)
    if not row:
        raise HTTPException(404, "not found")
    row["history"] = db.list_snapshots(listing_id)
    row["assessment"] = db.latest_assessment(listing_id)
    row["timeline"] = db.vehicle_events(row["vehicle_id"]) if row.get("vehicle_id") else []
    return row


class ListingPatch(BaseModel):
    status: str | None = None
    notes: str | None = None
    pinned: bool | None = None
    role: str | None = None
    profile_key: str | None = None
    mission: str | None = None


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
    if patch.role in {"candidate", "comp", "ignored"}:
        updates["role"] = patch.role
    if patch.profile_key is not None:
        if patch.profile_key and not db.get_profile(patch.profile_key):
            raise HTTPException(400, "unknown profile")
        updates["profile_key"] = patch.profile_key or None
    if patch.mission is not None:
        if patch.mission not in MISSIONS:
            raise HTTPException(400, f"mission must be one of {MISSIONS}")
        updates["mission"] = patch.mission
        updates["mission_user_set"] = 1
    db.update_listing(listing_id, updates)
    db.log_event("edit", listing_id, str(updates))
    return {"ok": True, **updates}


@app.delete("/api/listings/{listing_id}")
def delete_listing(listing_id: int) -> dict[str, Any]:
    if not db.delete_listing(listing_id):
        raise HTTPException(404, "not found")
    db.log_event("deleted", listing_id, "")
    return {"ok": True}


@app.post("/api/listings/{listing_id}/assess")
@app.post("/api/listings/{listing_id}/analyze")  # back-compat alias
async def assess_listing(listing_id: int, tier: str = "full") -> dict[str, Any]:
    """Deep assessment: the model interprets evidence; the policy engine gates,
    scores, costs, and decides. Stored with the policy version."""
    row = db.get_listing(listing_id)
    if not row:
        raise HTTPException(404, "not found")
    if not CONFIG.ai_enabled:
        raise HTTPException(400, "ANTHROPIC_API_KEY not set")
    prof = db.get_profile(row["profile_key"]) if row.get("profile_key") else None
    if not prof:
        raise HTTPException(400, "listing has no profile yet; run a sync or assign one")
    from scout.ai.assess import interpret_listing  # lazy
    from scout.policy.engine import assess, default_mission
    from scout.vin import compare_decode, decode_vin, decoded_facts
    state = load_state()
    mission = row.get("mission") or default_mission(prof)
    peers = [p for p in db.list_listings(role="candidate", profile_key=prof["key"])
             if p["id"] != listing_id and p["availability"] == "active"]
    comps = db.list_listings(role="comp", profile_key=prof["key"])
    pool = sorted(c.get("sold_price") or c.get("price") for c in comps if (c.get("sold_price") or c.get("price")))
    comps_median = pool[len(pool) // 2] if pool else None
    snaps = db.list_snapshots(listing_id)
    history = db.vin_history(row.get("vin"), exclude_listing_id=listing_id)
    history["provenance"] = row.get("provenance")
    history["timeline"] = db.vehicle_events(row["vehicle_id"]) if row.get("vehicle_id") else []
    pp = (row.get("provenance") or {}).get("price_progression") or {}
    if pp.get("percent_change") is not None:
        history["markup_vs_last_sale"] = pp["percent_change"]
        history["last_documented_price"] = (pp.get("reference") or {}).get("price")
    decoded = await asyncio.to_thread(decode_vin, row.get("vin")) if row.get("vin") else None
    history["vin_decode"] = decoded and {k: decoded.get(k) for k in ("year", "make", "model", "series", "trim", "engine_liters", "cylinders", "body_class")}
    history["vin_decode_contradictions"] = compare_decode(decoded, row)
    history["recalls"] = (decoded or {}).get("recalls") or []
    model = CONFIG.model_mid if tier == "quick" else CONFIG.model_deep
    nested = bool(_task.get("active"))      # a batch (or another run) is already showing; don't take over the banner
    token = None if nested else _task_start(f"{'Quick' if tier == 'quick' else 'Full'} assessment · {row.get('title') or listing_id} · {model}", 1)
    async with _ai_lock:
        try:
            evidence = await asyncio.to_thread(interpret_listing, row, prof, mission, state, history, snaps, peers, comps, model)
        except Exception as e:
            db.log_event("assess_error", listing_id, str(e))
            if token:
                _task_end(f"failed: {e}", token)
            raise HTTPException(500, f"assessment failed: {e}")
    # External VIN facts + deterministic decode contradictions join the model's interpretation.
    from scout.policy.schema import Contradiction, Fact
    evidence.facts.extend(Fact(**f) for f in decoded_facts(decoded))
    for c in history["vin_decode_contradictions"]:
        evidence.contradictions.append(Contradiction(**c))
    result = assess(row, prof, evidence, state, vin_history=history, comps_median=comps_median,
                    mission=mission, model=model)
    data = result.model_dump()
    db.add_assessment(listing_id, data)
    db.update_listing(listing_id, {"analyzed_at": db.now(), "analysis_model": model, "mission": mission})
    db.log_event("assessed", listing_id, f"{result.verdict} {result.score.total}/100 c{result.confidence}")
    if token:
        _task_end(f"{result.verdict} · {result.score.total}/100", token)
    return {"ok": True, "assessment": data}


@app.post("/api/assess-all")
async def assess_all(tier: str = "quick", only_unassessed: bool = True) -> dict[str, Any]:
    """Assess every active candidate (quick tier by default). Serial, so it can take a while."""
    rows = [r for r in db.list_listings(role="candidate") if r["availability"] in ("active", "pending") and r.get("profile_key")]
    shared = db.latest_assessments_by_vehicle()
    if only_unassessed:
        rows = [r for r in rows if r["id"] not in shared]
    # One assessment per car: skip a listing whose VIN twin is already in this batch.
    seen_vehicles: set[int] = set()
    deduped = []
    for r in rows:
        vid = r.get("vehicle_id")
        if vid and vid in seen_vehicles and r.get("vin"):
            continue
        if vid and r.get("vin"):
            seen_vehicles.add(vid)
        deduped.append(r)
    rows = deduped
    done, errors = 0, []
    tok = _task_start(f"Quick-assessing {len(rows)} listing(s) · {CONFIG.model_mid if tier == 'quick' else CONFIG.model_deep}", len(rows))
    for i, r in enumerate(rows):
        _task_step(r.get("title") or r["url"], i)
        try:
            await assess_listing(r["id"], tier=tier)
            done += 1
        except HTTPException as e:
            errors.append(f"{r.get('title')}: {e.detail}")
            _task["errors"] = len(errors)
    _task_end(f"{done} assessed" + (f", {len(errors)} failed" if errors else ""), tok)
    return {"ok": True, "assessed": done, "errors": errors[:10], "tier": tier}


@app.get("/api/listings/{listing_id}/assessments")
def assessment_history(listing_id: int) -> list[dict[str, Any]]:
    return db.list_assessments(listing_id)


@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    return {"policy_version": POLICY_VERSION, "state": load_state(), "missions": MISSIONS}


@app.put("/api/settings")
def put_settings(update: dict[str, Any]) -> dict[str, Any]:
    try:
        return {"ok": True, "state": save_state(update)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/settings/reset")
def reset_settings() -> dict[str, Any]:
    return {"ok": True, "state": reset_state()}


@app.post("/api/vin/{vin}/decode")
async def vin_decode(vin: str) -> dict[str, Any]:
    from scout.vin import decode_vin
    d = await asyncio.to_thread(decode_vin, vin, None, True)
    if not d:
        raise HTTPException(404, "VIN did not decode")
    return d


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


# ---------- provenance ----------

@app.post("/api/listings/{listing_id}/provenance/queue")
def queue_provenance(listing_id: int) -> dict[str, Any]:
    from scout.provenance import build_queries, link_listing_vehicle
    row = db.get_listing(listing_id)
    if not row:
        raise HTTPException(404, "not found")
    link_listing_vehicle(listing_id)
    job_id = db.create_provenance_job(listing_id, build_queries(row))
    db.log_event("provenance_queued", listing_id, str(job_id))
    return {"ok": True, "job": next(j for j in db.list_provenance_jobs("queued") if j["id"] == job_id)}


@app.get("/api/provenance/jobs")
def provenance_jobs(status: str = "queued") -> list[dict[str, Any]]:
    return db.list_provenance_jobs(None if status == "all" else status)


class HitsPayload(BaseModel):
    hits: list[dict[str, Any]]


@app.post("/api/provenance/jobs/{job_id}/hits")
def provenance_hits(job_id: int, payload: HitsPayload) -> dict[str, Any]:
    job = next((j for j in db.list_provenance_jobs(None) if j["id"] == job_id), None)
    if not job:
        raise HTTPException(404, "job not found")
    n = db.add_provenance_hits(job["listing_id"], payload.hits)
    db.update_provenance_job(job_id, status="running", hits=(job.get("hits") or 0) + n)
    return {"ok": True, "stored": n}


class FailPayload(BaseModel):
    error: str = ""


@app.post("/api/provenance/jobs/{job_id}/fail")
def provenance_fail(job_id: int, payload: FailPayload) -> dict[str, Any]:
    db.update_provenance_job(job_id, status="failed", error=payload.error[:500])
    return {"ok": True}


@app.post("/api/provenance/jobs/{job_id}/complete")
async def provenance_complete(job_id: int) -> dict[str, Any]:
    """Classify the gathered hits (deep model), write same-car events to the
    VIN record, run the deterministic analysis, store it on the listing."""
    from scout.provenance import analyze, link_listing_vehicle
    job = next((j for j in db.list_provenance_jobs(None) if j["id"] == job_id), None)
    if not job:
        raise HTTPException(404, "job not found")
    lid = job["listing_id"]
    row = db.get_listing(lid)
    vid = link_listing_vehicle(lid)
    events = db.vehicle_events(vid) if vid else []
    hits = db.provenance_hits(lid)
    interp = None
    if CONFIG.ai_enabled and hits:
        from scout.ai.provenance import interpret_hits  # lazy
        ptok = None if _task.get("active") else _task_start(f"Provenance · classifying {len(hits)} hit(s) · {row.get('title') or lid}", 1)
        async with _ai_lock:
            try:
                interp = await asyncio.to_thread(interpret_hits, row, events, hits)
            except Exception as e:
                db.update_provenance_job(job_id, status="failed", error=str(e)[:500])
                raise HTTPException(500, f"provenance interpretation failed: {e}")
    statements: list[dict[str, Any]] = []
    if interp:
        for ev in interp.events:
            if ev.identity_confidence == "not_established" or not vid:
                continue
            if ev.identity_confidence == "confirmed" and ev.url:
                other = db.get_listing_by_url(ev.url) or db.get_listing_by_url(ev.url.rstrip("/") + "/")
                if other and other.get("vehicle_id") and other["vehicle_id"] != vid:
                    db.merge_vehicles(other["vehicle_id"], vid)
                    db.log_event("vehicle_merged", lid, f"{other['vehicle_id']} -> {vid} via confirmed VIN match at {ev.url}")
            db.add_vehicle_event(vid, {"event_date": ev.date, "venue": ev.venue, "url": ev.url or None, "mileage": ev.mileage,
                                       "price": ev.price, "price_type": ev.price_type, "status": ev.status,
                                       "evidence": ev.evidence, "source": "search", "identity_confidence": ev.identity_confidence,
                                       "seller": ev.seller})
        statements = [s.model_dump() for s in interp.seller_statements]
        for st in interp.seller_statements:
            if st.kind in {"withdrawn", "keep"} and vid:
                db.add_vehicle_event(vid, {"event_date": st.date, "venue": st.venue, "url": st.url or None,
                                           "status": "Seller decided to keep" if st.kind == "keep" else "Withdrawn",
                                           "evidence": st.quote, "source": "search", "identity_confidence": "confirmed" if row.get("vin") else "strongly_likely"})
    events = db.vehicle_events(vid) if vid else []
    result = analyze(row, events, statements, interp.model_dump() if interp else None)
    if interp:
        result["identity_notes"] = interp.identity_notes
        result["summary"] = interp.summary
    db.update_listing(lid, {"provenance": result})
    if not result["current_status"]["available"] and row.get("availability") == "active":
        db.update_listing(lid, {"availability": "withdrawn"})
    db.update_provenance_job(job_id, status="done", result={"flags": result["flags"], "available": result["current_status"]["available"]})
    db.log_event("provenance_done", lid, ", ".join(result["flags"]) or "no flags")
    _task_end(", ".join(result["flags"]) or "no same-car flags", locals().get("ptok"))
    return {"ok": True, "flags": result["flags"], "available": result["current_status"]["available"], "summary": result.get("summary", "")}


@app.get("/api/listings/{listing_id}/provenance")
def get_provenance(listing_id: int) -> dict[str, Any]:
    row = db.get_listing(listing_id)
    if not row:
        raise HTTPException(404, "not found")
    return {"provenance": row.get("provenance"), "timeline": db.vehicle_events(row["vehicle_id"]) if row.get("vehicle_id") else [],
            "vehicle": db.get_vehicle(row["vehicle_id"]) if row.get("vehicle_id") else None,
            "jobs": [j for j in db.list_provenance_jobs(None) if j["listing_id"] == listing_id][:5]}


@app.post("/api/rescore")
def rescore(assessments: bool = True) -> dict[str, Any]:
    """Recompute every preliminary score from stored data, and re-derive stored
    assessments under the current policy from their stored evidence. Free."""
    from scout.ingest import rescore_all
    from scout.policy.engine import rescore_assessment
    n = rescore_all()
    redone = 0
    if assessments:
        state = load_state()
        for lid, a in db.latest_assessments().items():
            if a.get("policy_version") == POLICY_VERSION:
                continue
            row = db.get_listing(lid)
            prof = db.get_profile(row["profile_key"]) if row and row.get("profile_key") else None
            if not (row and prof):
                continue
            d = rescore_assessment(row, prof, a, state)
            if d:
                db.add_assessment(lid, d)
                redone += 1
    return {"ok": True, "rescored": n, "assessments_rederived": redone, "policy_version": POLICY_VERSION}


@app.post("/api/renormalize-all")
async def renormalize_all(only_missing_ratings: bool = True) -> dict[str, Any]:
    """Re-run the fast model on candidates (all, or only those without the new
    ratings). Roughly a cent per listing."""
    if not CONFIG.ai_enabled:
        raise HTTPException(400, "ANTHROPIC_API_KEY not set")
    rows = [r for r in db.list_listings() if r["role"] != "ignored"
            and (not only_missing_ratings or not (r.get("normalized") or {}).get("ratings"))]
    done, errors = 0, []
    tok = _task_start(f"Re-normalizing {len(rows)} listing(s) · {CONFIG.model_fast}", len(rows))
    async with _ai_lock:
        for i, r in enumerate(rows):
            _task_step(r.get("title") or r["url"], i)
            db.update_listing(r["id"], {"normalized_at": None})
            item = {"url": r["url"], "title": r.get("title"), "price_text": f"${r['price']:,}" if r.get("price") else "",
                    "detail": {"text": r.get("raw_text") or "", "photos": r.get("photos") or []}, "sold": r["availability"] == "sold"}
            try:
                st = await asyncio.to_thread(ingest_items, r["site"], [item], True)
                done += 1
                errors += st.get("errors") or []
            except Exception as e:
                errors.append(f"{r['url']}: {e}")
                _task["errors"] = len(errors)
    _task_end(f"{done} re-normalized" + (f", {len(errors)} failed" if errors else ""), tok)
    return {"ok": True, "renormalized": done, "errors": errors[:10]}


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


@app.post("/api/handoff")
def make_handoff(n: int = 10) -> dict[str, Any]:
    """Write the full-fidelity handoff bundle (Markdown + JSON) for the top-n candidates."""
    from scout.handoff import write
    md, js = write(n)
    return {"ok": True, "markdown": str(md), "json": str(js)}


@app.get("/api/events")
def events() -> list[dict[str, Any]]:
    return db.recent_events()


# Static viewer (local mode). Mounted last so /api wins.
@app.get("/")
def index() -> FileResponse:
    return FileResponse(DOCS_DIR / "index.html")


app.mount("/", StaticFiles(directory=str(DOCS_DIR), html=True), name="site")
