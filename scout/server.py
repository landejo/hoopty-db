"""Local FastAPI service: extension ingest, AI actions, edits, publish.
Also serves docs/ so the viewer runs locally with write access."""
from __future__ import annotations

import asyncio
import os
import re
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from scout import autopublish, db
from scout.config import CONFIG, DOCS_DIR, SITES, STATUSES
from scout.ingest import ai_queue_depth, ingest_items
from scout.profiles import sync_seed_profiles
from scout.publish import build_export, git_publish, write_export
from scout.policy import POLICY_VERSION
from scout.policy.preferences import MISSIONS
from scout.policy.state import load_state, reset_state, save_state
from scout.stage import stage_for

app = FastAPI(title="Hoopty-Matic")

# Only the local viewer (served by this same process) and the Chrome extension
# are legitimate callers. No wildcard: /api/* can push to a public repo,
# trigger paid AI calls, and delete/read listings (including VINs/seller data).
_ALLOWED_ORIGINS = [f"http://127.0.0.1:{CONFIG.port}", f"http://localhost:{CONFIG.port}"]
_EXTENSION_ORIGIN_RE = r"^chrome-extension://[a-p]{32}$"
_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "testserver"}

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_origin_regex=_EXTENSION_ORIGIN_RE,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
    allow_headers=["Content-Type"],
)


def _origin_allowed(origin: str) -> bool:
    return origin in _ALLOWED_ORIGINS or bool(re.match(_EXTENSION_ORIGIN_RE, origin))


@app.middleware("http")
async def _api_guard(request, call_next):
    """Anti DNS-rebinding + anti cross-site-request guard for the whole API.
    Runs on every method, including GET, so a hostile page can't trigger
    side-effect GETs or read /api/export either."""
    if request.url.path.startswith("/api/"):
        host = (request.headers.get("host") or "").split(":")[0]
        if host not in _ALLOWED_HOSTS:
            return JSONResponse({"detail": "forbidden host"}, status_code=403)
        origin = request.headers.get("origin")
        if origin is not None:
            if not _origin_allowed(origin):
                return JSONResponse({"detail": "forbidden origin"}, status_code=403)
        elif request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "forbidden"}, status_code=403)
    return await call_next(request)


@app.middleware("http")
async def _no_cache_static(request, call_next):
    """The viewer is edited often; make every load fetch the current files."""
    resp = await call_next(request)
    if not request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp

# Activity for auto-publish (scout/autopublish.py): the viewer's heartbeat marks
# presence; any successful data-changing API call marks a change to publish.
_NOT_CHANGES = {"/api/activity", "/api/publish", "/api/export/write", "/api/handoff"}


@app.middleware("http")
async def _track_activity(request, call_next):
    resp = await call_next(request)
    path = request.url.path
    if path.startswith("/api/") and request.method != "GET" and resp.status_code < 400:
        if path == "/api/activity":
            autopublish.note_activity(changed=False)
        elif path not in _NOT_CHANGES:
            autopublish.note_activity(changed=True)
    return resp


_ai_lock = asyncio.Lock()
_publish_lock = asyncio.Lock()
_task: dict[str, Any] = {"active": False}


def _task_start(name: str, total: int | None = None) -> str:
    """Begin a visible task. Returns a token; only the holder may end it, so a
    one-off assessment that overlaps a batch never clobbers the batch's status."""
    import secrets
    token = secrets.token_hex(4)
    _task.update({"active": True, "name": name, "done": 0, "total": total, "current": "", "started": db.now(), "errors": 0, "result": None, "token": token,
                  "heartbeat": None})   # only extension-driven runs set one; a stale one would "stall" this task
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


TASK_STALL_SECONDS = 300   # an extension-driven task with no progress this long has died


@app.get("/api/task")
def task_status() -> dict[str, Any]:
    beat = _task.get("heartbeat")
    if _task.get("active") and beat:
        from datetime import datetime
        if (datetime.fromisoformat(db.now()) - datetime.fromisoformat(beat)).total_seconds() > TASK_STALL_SECONDS:
            _task_end(f"stalled after {_task.get('done')}/{_task.get('total')}: the extension stopped reporting", _task.get("token"))
    return _task


def _rederive_assessment(listing_id: int) -> None:
    """Re-run the deterministic policy engine (free, no AI) on a listing's latest
    stored assessment so stage/verdict/priority stay current after something
    that changes stage_for()'s inputs (a status change, a document attached or
    removed). No-op if the listing has never been assessed."""
    a = db.latest_assessment(listing_id)
    row = db.get_listing(listing_id)
    if not a or not row:
        return
    prof = db.get_profile(row["profile_key"]) if row.get("profile_key") else None
    if not prof:
        return
    from scout import market
    from scout.policy.engine import rescore_assessment
    state = load_state()
    fair = market.fair_value(row, db.list_listings(profile_key=prof["key"]))
    stage = stage_for(row, db.list_documents(listing_id))
    d = rescore_assessment(row, prof, a, state, fair=fair, stage=stage)
    if d:
        db.add_assessment(listing_id, d)


@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    sync_seed_profiles()
    if os.environ.get("SCOUT_BACKUP_DIR") != "off":
        try:
            from scout.backup import backup_db
            backup_db("startup")
        except Exception as e:
            print(f"warning: startup backup failed: {e}")
    autopublish.restore()
    if os.environ.get("SCOUT_STARTUP_RESCORE", "1") != "0":
        # Free: re-derive assessments stored under an older policy so a policy change
        # (e.g. 1.7.0's sold/ended gate) reaches every car without pressing Recompute.
        import threading

        def _rescore_stale() -> None:
            # Cars you marked sold / gone before that moved them off the board.
            from scout.availability import mark_off_market_by_user
            for r in db.list_listings():
                if r["availability"] in ("active", "pending", "removed") and mark_off_market_by_user(r["id"]):
                    _rederive_assessment(r["id"])
            stale = sum(1 for a in db.latest_assessments().values() if a.get("policy_version") != POLICY_VERSION)
            # A visible task: the board shows the banner and refreshes itself when it ends.
            tok = _task_start(f"Updating {stale} assessment(s) to policy {POLICY_VERSION} (free)", None) if stale and not _task.get("active") else None
            try:
                if tok:   # E2E only: hold the update open so a page can load mid-update
                    import time
                    time.sleep(float(os.environ.get("SCOUT_STARTUP_RESCORE_DELAY", "0")))
                r = rescore(assessments=True)
                db.log_event("startup_rescore", None, str(r))
                if tok:
                    _task_end(f"{r.get('assessments_rederived', 0)} assessment(s) now on policy {POLICY_VERSION}", tok)
            except Exception as e:
                db.log_event("startup_rescore_error", None, str(e))
                if tok:
                    _task_end(f"failed: {e}", tok)
        threading.Thread(target=_rescore_stale, name="scout-startup-rescore", daemon=True).start()
    if autopublish.enabled():
        asyncio.get_event_loop().create_task(_autopublish_loop())


async def _autopublish_loop() -> None:
    while True:
        await asyncio.sleep(30)
        reason = autopublish.due()
        if not reason or _task.get("active") or _publish_lock.locked():
            continue
        async with _publish_lock:
            covered = autopublish.set_publishing(True)
            try:
                result = await asyncio.to_thread(git_publish, f"Auto-publish after a workbench sitting ({reason})")
            except Exception as e:
                result = {"ok": False, "changed": False, "detail": str(e)}
            finally:
                autopublish.set_publishing(False)
        autopublish.mark_published(result, reason, covered=covered)
        db.log_event("autopublish", None, f"{reason}: " + ("pushed" if result.get("changed") and result.get("ok")
                     else "nothing changed" if result.get("ok") else "failed: " + str(result.get("detail"))[-200:]))


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "ai": CONFIG.ai_enabled, "models": {"deep": CONFIG.model_deep, "mid": CONFIG.model_mid, "fast": CONFIG.model_fast, "top": CONFIG.model_top},
            "skip_sold": CONFIG.skip_sold, "policy_version": POLICY_VERSION, "ai_queue": ai_queue_depth(),
            "autopublish": autopublish.status()}


@app.post("/api/activity")
def activity() -> dict[str, Any]:
    """Viewer heartbeat while you are actively using it (see autopublish.py)."""
    return {"ok": True, "autopublish": autopublish.status()}


@app.get("/api/ai-spend")
def ai_spend(days: int = 30) -> dict[str, Any]:
    return db.ai_spend(days=days)


class IngestPayload(BaseModel):
    site: str
    items: list[dict[str, Any]]
    include_sold: bool | None = None
    full_sync: bool = False
    defer_ai: bool = True   # batch syncs must return fast; single adds send False


@app.post("/api/ingest")
async def ingest(payload: IngestPayload) -> dict[str, Any]:
    if payload.site not in SITES:
        raise HTTPException(400, f"unknown site {payload.site!r}")
    if payload.defer_ai:
        # No _ai_lock: this path does no AI, and must never wait behind an assessment.
        stats = await asyncio.to_thread(ingest_items, payload.site, payload.items, payload.include_sold,
                                        True, payload.full_sync, True)
    else:
        async with _ai_lock:
            stats = await asyncio.to_thread(ingest_items, payload.site, payload.items, payload.include_sold,
                                            True, payload.full_sync)
    return {"ok": True, **stats, "ai_queue": ai_queue_depth()}


@app.get("/api/export")
def export() -> JSONResponse:
    return JSONResponse(build_export())


@app.get("/api/listings/by-url")
def listing_by_url(url: str) -> dict[str, Any]:
    row = db.get_listing_by_url(url.rstrip("/")) or db.get_listing_by_url(url.rstrip("/") + "/") or db.get_listing_by_url(url)
    if not row:
        raise HTTPException(404, "not tracked")
    return {"id": row["id"], "title": row.get("title"), "site": row["site"]}


@app.get("/api/listings/by-vin/{vin}")
def listing_by_vin(vin: str) -> dict[str, Any]:
    vin = (vin or "").strip().upper()
    for r in db.list_listings():
        if (r.get("vin") or "").upper() == vin:
            return {"id": r["id"], "title": r.get("title"), "site": r["site"]}
    raise HTTPException(404, "no tracked listing with that VIN")


@app.get("/api/listings/{listing_id}")
def get_listing(listing_id: int) -> dict[str, Any]:
    row = db.get_listing(listing_id)
    if not row:
        raise HTTPException(404, "not found")
    row["history"] = db.list_snapshots(listing_id)
    row["assessment"] = db.latest_assessment(listing_id)
    row["documents"] = [{k: v for k, v in d.items() if k != "text"} | {"chars": len(d.get("text") or "")}
                        for d in db.list_documents(listing_id)]
    row["timeline"] = db.vehicle_events(row["vehicle_id"]) if row.get("vehicle_id") else []
    return row


class ListingPatch(BaseModel):
    status: str | None = None
    notes: str | None = None
    pinned: bool | None = None
    role: str | None = None
    profile_key: str | None = None
    mission: str | None = None
    verdict_override: str | None = None
    verdict_override_reason: str | None = None


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
        updates["role_user_set"] = 1   # syncs no longer flip it back
    if patch.profile_key is not None:
        if patch.profile_key and not db.get_profile(patch.profile_key):
            raise HTTPException(400, "unknown profile")
        updates["profile_key"] = patch.profile_key or None
    if patch.verdict_override is not None:
        from scout.policy.preferences import VERDICTS
        if patch.verdict_override and patch.verdict_override not in VERDICTS:
            raise HTTPException(400, f"verdict_override must be one of {VERDICTS}")
        updates["verdict_override"] = patch.verdict_override or None
    if patch.verdict_override_reason is not None:
        updates["verdict_override_reason"] = patch.verdict_override_reason[:2000] or None
    if patch.mission is not None:
        if patch.mission not in MISSIONS:
            raise HTTPException(400, f"mission must be one of {MISSIONS}")
        updates["mission"] = patch.mission
        updates["mission_user_set"] = 1
    db.update_listing(listing_id, updates)
    db.log_event("edit", listing_id, str(updates))
    if {"status", "verdict_override", "verdict_override_reason"} & set(updates):
        from scout.availability import mark_off_market_by_user
        if mark_off_market_by_user(listing_id):   # you said it is sold / gone: off the board, into the comps
            row = db.get_listing(listing_id)
            updates.update({"availability": row["availability"], "role": row["role"]})
            _rederive_assessment(listing_id)
    if "status" in updates and "availability" not in updates:
        _rederive_assessment(listing_id)
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
    from scout import market
    fair = market.fair_value(row, db.list_listings(profile_key=prof["key"]))
    stage = stage_for(row, db.list_documents(listing_id))
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
    model = {"quick": CONFIG.model_mid, "top": CONFIG.model_top}.get(tier, CONFIG.model_deep)
    nested = bool(_task.get("active"))      # a batch (or another run) is already showing; don't take over the banner
    token = None if nested else _task_start(f"{'Quick' if tier == 'quick' else 'Full'} assessment · {row.get('title') or listing_id} · {model}", 1)
    async with _ai_lock:
        try:
            evidence = await asyncio.to_thread(interpret_listing, row, prof, mission, state, history, snaps, peers, comps, model, fair=fair)
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
    result = assess(row, prof, evidence, state, vin_history=history, fair=fair,
                    mission=mission, model=model, stage=stage)
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


class ReassessPayload(BaseModel):
    ids: list[int]          # the board's ranking, best first (all of it for a tiered run)
    tier: str = "top"       # model tier: top | full | quick
    cycle: bool = True      # tiered cycle (below); False re-assesses exactly `ids`


REASSESS_MAX = 25
TIER_SIZE = 15
CYCLE_DAYS = 3
_CYCLE_KEY = "reassess_cycle"


def _cycle_state(now: str | None = None) -> dict[str, Any]:
    """Tiered re-assessment: each press takes the next TIER_SIZE cars in board
    order that this cycle has not re-assessed yet. A cycle starts with tier 1
    and restarts at tier 1 once CYCLE_DAYS have passed since tier 1 ran, or
    once every car on the board has been done."""
    from datetime import datetime, timedelta
    c = db.get_setting(_CYCLE_KEY) or {}
    started = c.get("started_at")
    expired = not started or datetime.fromisoformat(now or db.now()) - datetime.fromisoformat(started) > timedelta(days=CYCLE_DAYS)
    if expired:
        return {"started_at": None, "tier": 0, "done": [], "expired_previous": bool(started)}
    return {"started_at": started, "tier": int(c.get("tier") or 0), "done": list(c.get("done") or [])}


@app.get("/api/reassess/cycle")
def reassess_cycle() -> dict[str, Any]:
    from datetime import datetime, timedelta
    c = _cycle_state()
    restarts = (datetime.fromisoformat(c["started_at"]) + timedelta(days=CYCLE_DAYS)).isoformat() if c["started_at"] else None
    return {"next_tier": c["tier"] + 1, "tier_size": TIER_SIZE, "cycle_days": CYCLE_DAYS, "started_at": c["started_at"],
            "restarts_at": restarts, "done": c["done"], "model": CONFIG.model_top}


def _eligible(ids: list[int]) -> tuple[list[dict[str, Any]], list[int]]:
    rows, seen_vehicles, skipped = [], set(), []
    for lid in ids:
        r = db.get_listing(lid)
        if not r or r["role"] != "candidate" or r["availability"] not in ("active", "pending") or not r.get("profile_key"):
            skipped.append(lid)
            continue
        if r.get("vehicle_id") and r.get("vin"):
            if r["vehicle_id"] in seen_vehicles:
                continue
            seen_vehicles.add(r["vehicle_id"])
        rows.append(r)
    return rows, skipped


@app.post("/api/reassess")
async def reassess(payload: ReassessPayload) -> dict[str, Any]:
    """Re-assess on the top-tier model, serially, one assessment per car. With
    cycle=True (the board button) this is the next tier of the ranking."""
    if not CONFIG.ai_enabled:
        raise HTTPException(400, "ANTHROPIC_API_KEY not set")
    if payload.tier not in {"top", "full", "quick"}:
        raise HTTPException(400, "tier must be top, full or quick")
    if _task.get("active"):
        raise HTTPException(409, "another run is in progress; wait for it to finish")
    eligible, skipped = _eligible(payload.ids if payload.cycle else payload.ids[:REASSESS_MAX])
    cycle = _cycle_state() if payload.cycle else None
    restarted = False
    if cycle is not None:
        pending = [r for r in eligible if r["id"] not in set(cycle["done"])]
        if not pending and eligible:          # every car done: start a fresh cycle
            cycle, pending, restarted = {"started_at": None, "tier": 0, "done": []}, eligible, True
        rows = pending[:TIER_SIZE]
        tier_no = cycle["tier"] + 1
        if tier_no == 1:
            cycle["started_at"] = db.now()
    else:
        rows, tier_no = eligible, None
    model = {"quick": CONFIG.model_mid, "top": CONFIG.model_top}.get(payload.tier, CONFIG.model_deep)
    label = f"tier {tier_no}" if tier_no else "top"
    tok = _task_start(f"Re-assessing {label} · {len(rows)} car(s) · {model}", len(rows))
    done_ids, errors = [], []
    for i, r in enumerate(rows):
        _task_step(r.get("title") or r["url"], i)
        try:
            await assess_listing(r["id"], tier=payload.tier)
            done_ids.append(r["id"])
        except HTTPException as e:
            errors.append(f"{r.get('title')}: {e.detail}")
            _task["errors"] = len(errors)
    if cycle is not None:
        cycle["tier"] = tier_no
        cycle["done"] = cycle["done"] + done_ids   # failures stay pending and lead the next tier
        db.set_setting(_CYCLE_KEY, {k: cycle[k] for k in ("started_at", "tier", "done")})
    _task_end(f"{label}: {len(done_ids)} re-assessed on {model}" + (f", {len(errors)} failed" if errors else ""), tok)
    return {"ok": True, "assessed": len(done_ids), "ids": [r["id"] for r in rows], "errors": errors[:10],
            "skipped": skipped, "model": model, "tier": tier_no, "cycle_restarted": restarted or bool(cycle and cycle.get("expired_previous"))}


@app.get("/api/assess-cost")
def assess_cost(tier: str = "top") -> dict[str, Any]:
    """Measured cost of one assessment on the tier's model, from ai_calls.
    With fewer than 3 calls on that model, scales the other Opus average by the
    per-token price ratio and says so."""
    from scout.config import PRICES
    model = {"quick": CONFIG.model_mid, "top": CONFIG.model_top}.get(tier, CONFIG.model_deep)
    with db.connect() as c:
        rows = c.execute("SELECT model, COUNT(*) n, AVG(cost_usd) avg FROM ai_calls WHERE kind='last_assess' GROUP BY model").fetchall()
    by = {r["model"]: (r["n"], r["avg"]) for r in rows}
    if by.get(model, (0, 0))[0] >= 3:
        n, avg = by[model]
        return {"model": model, "per_listing": round(avg, 3), "basis": f"average of {n} measured assessments on {model}"}
    ref = max(((m, v) for m, v in by.items() if m.startswith("claude-opus") and v[0] >= 3), key=lambda x: x[1][0], default=None)
    if ref and model in PRICES and ref[0] in PRICES:
        ratio = PRICES[model][0] / PRICES[ref[0]][0]
        return {"model": model, "per_listing": round(ref[1][1] * ratio, 3),
                "basis": f"{ref[1][0]} measured {ref[0]} assessments averaging ${ref[1][1]:.2f}, scaled by the per-token price ratio ({ratio:.2f})"}
    return {"model": model, "per_listing": None, "basis": "no measured assessments yet"}


# ---------- availability check (sold / delisted / ended detection) ----------

_avail_run: dict[str, Any] = {"profiles": set(), "results": [], "token": None}


@app.post("/api/availability/start")
def availability_start(limit: int | None = None) -> dict[str, Any]:
    """The extension asks what to check. Starts the banner task."""
    from scout.availability import targets
    t = targets(limit)
    _avail_run.update({"profiles": set(), "results": [],
                       "token": None if _task.get("active") else _task_start(f"Checking availability of {len(t)} listing(s) in your browser", len(t))})
    if _avail_run["token"]:
        _task["heartbeat"] = db.now()
    return {"ok": True, "targets": t}


class AvailabilityResult(BaseModel):
    id: int
    detail: dict[str, Any] | None = None


class AvailabilityPayload(BaseModel):
    results: list[AvailabilityResult]


@app.post("/api/availability/results")
def availability_results(payload: AvailabilityPayload) -> dict[str, Any]:
    """Classify the pages the extension read and apply sold / ended / delisted."""
    from scout.availability import apply
    out = []
    for res in payload.results:
        r = apply(res.id, res.detail)
        out.append(r)
        _avail_run["results"].append(r)
        if r.get("changed"):
            _rederive_assessment(res.id)
            if r.get("profile_key"):
                _avail_run["profiles"].add(r["profile_key"])
        if _avail_run.get("token") and _task.get("token") == _avail_run["token"]:
            _task_step(r.get("title") or str(res.id))
            _task["heartbeat"] = db.now()
    return {"ok": True, "results": out}


@app.post("/api/availability/finish")
def availability_finish() -> dict[str, Any]:
    """Refresh what depends on the comp pool: preliminary scores and stored
    assessments (fair value, offers) in every profile that gained a comp."""
    from scout.availability import summarize
    from scout.ingest import rescore_listing
    refreshed = 0
    state = load_state()
    for pk in sorted(_avail_run["profiles"]):
        for r in db.list_listings(profile_key=pk):
            rescore_listing(r["id"], state)
            if r["role"] == "candidate" and r["availability"] in ("active", "pending"):
                _rederive_assessment(r["id"])
                refreshed += 1
    summary = summarize(_avail_run["results"])
    db.log_event("availability_check", None, summary)
    changed = [r for r in _avail_run["results"] if r.get("changed")]
    _task_end(summary, _avail_run.get("token"))
    _avail_run.update({"profiles": set(), "results": [], "token": None})
    return {"ok": True, "summary": summary, "changed": changed, "reassessed_deterministically": refreshed}


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


def _availability_flags(row: dict[str, Any]) -> dict[str, Any]:
    """A re-read of stored text must keep the listing's known availability:
    without these flags detect_availability reads it as active."""
    a = row["availability"]
    flags: dict[str, Any] = {"sold": a == "sold", "ended": a == "ended", "pending": a == "pending"}
    if a in {"removed", "withdrawn"}:
        flags["_keep_availability"] = a   # not re-derivable from text
    return flags


@app.post("/api/listings/{listing_id}/renormalize")
async def renormalize(listing_id: int) -> dict[str, Any]:
    row = db.get_listing(listing_id)
    if not row:
        raise HTTPException(404, "not found")
    db.update_listing(listing_id, {"normalized_at": None})
    item = {"url": row["url"], "title": row.get("title"), "detail": {"text": row.get("raw_text") or "",
            "photos": row.get("photos") or []}, **_availability_flags(row)}
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
    ptok = None
    if CONFIG.ai_enabled and hits:
        from scout.ai.provenance import interpret_hits  # lazy
        ptok = None if _task.get("active") else _task_start(f"Provenance · classifying {len(hits)} hit(s) · {row.get('title') or lid}", 1)
        async with _ai_lock:
            try:
                interp = await asyncio.to_thread(interpret_hits, row, events, hits)
            except Exception as e:
                db.update_provenance_job(job_id, status="failed", error=str(e)[:500])
                if ptok:
                    _task_end(f"failed: {e}", ptok)
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
    if ptok:   # never end someone else's task (token None would end whatever is showing)
        _task_end(", ".join(result["flags"]) or "no same-car flags", ptok)
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
    from scout import market
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
            fair = market.fair_value(row, db.list_listings(profile_key=prof["key"]))
            stage = stage_for(row, db.list_documents(lid))
            d = rescore_assessment(row, prof, a, state, fair=fair, stage=stage)
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
                    "detail": {"text": r.get("raw_text") or "", "photos": r.get("photos") or []}, **_availability_flags(r)}
            try:
                st = await asyncio.to_thread(ingest_items, r["site"], [item], True)
                done += 1
                errors += st.get("errors") or []
            except Exception as e:
                errors.append(f"{r['url']}: {e}")
                _task["errors"] = len(errors)
    _task_end(f"{done} re-normalized" + (f", {len(errors)} failed" if errors else ""), tok)
    return {"ok": True, "renormalized": done, "errors": errors[:10]}


class DocumentPayload(BaseModel):
    kind: str = "other"
    text: str
    title: str = ""
    source: str = ""
    url: str = ""


@app.post("/api/listings/{listing_id}/documents")
def add_document(listing_id: int, payload: DocumentPayload) -> dict[str, Any]:
    """Attach a history report, invoice or service record. It becomes gold-tier
    evidence in the next assessment."""
    row = db.get_listing(listing_id)
    if not row:
        raise HTTPException(404, "not found")
    if not (payload.text or "").strip():
        raise HTTPException(400, "text is empty")
    if payload.kind not in db.DOC_KINDS:
        raise HTTPException(400, f"kind must be one of {db.DOC_KINDS}")
    doc_id = db.add_document(listing_id, payload.kind, payload.text, payload.title,
                             payload.source, payload.url, row.get("vin"))
    db.log_event("document_added", listing_id, f"{payload.kind} {len(payload.text)} chars")
    _rederive_assessment(listing_id)
    return {"ok": True, "document_id": doc_id, "kind": payload.kind, "chars": len(payload.text),
            "note": "Re-assess this listing to fold the document into its verdict."}


@app.get("/api/listings/{listing_id}/documents")
def get_documents(listing_id: int) -> list[dict[str, Any]]:
    return [{k: v for k, v in d.items() if k != "text"} | {"chars": len(d.get("text") or "")}
            for d in db.list_documents(listing_id)]


@app.delete("/api/documents/{doc_id}")
def remove_document(doc_id: int) -> dict[str, Any]:
    doc = db.get_document(doc_id)
    if not doc or not db.delete_document(doc_id):
        raise HTTPException(404, "not found")
    _rederive_assessment(doc["listing_id"])
    return {"ok": True}


@app.get("/api/evidence-gaps")
def evidence_gaps(listing_id: int | None = None) -> Any:
    """What is missing, whether a document or only an inspection can settle it,
    and a drafted records request."""
    from scout.evidence import gaps, report, request_message
    if listing_id:
        g = gaps(listing_id)
        if not g:
            raise HTTPException(404, "not found")
        return {**g, "message": request_message(listing_id)}
    return report()


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
    async with _publish_lock:
        covered = autopublish.set_publishing(True)
        try:
            result = await asyncio.to_thread(git_publish)
        finally:
            autopublish.set_publishing(False)
    autopublish.mark_published(result, "manual", manual=True, covered=covered)
    if not result["ok"]:
        raise HTTPException(502, result["detail"])
    return {"ok": True, "changed": result["changed"], "git": result["detail"]}


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
