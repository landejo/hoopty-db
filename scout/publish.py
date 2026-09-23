"""Export the DB to docs/data/*.json for the static viewer, then commit + push.
Seller contact details and private-party seller names never leave the DB."""
from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scout import db
from scout.config import DOCS_DIR, ROOT, SITE_DATA_DIR, SITES
from scout.policy import POLICY_VERSION
from scout.scoring import market_stats, price_percentile

PUBLIC_LISTING_FIELDS = [
    "id", "site", "url", "role", "availability", "first_seen", "last_seen", "title", "thumb",
    "photos", "year", "make", "model", "generation", "trim", "engine", "engine_liters",
    "transmission", "drivetrain", "body_style", "exterior_color", "interior_color", "mileage",
    "price", "price_kind", "sold_price", "location", "seller_type", "title_status", "accidents",
    "num_owners", "listing_date", "auction_end", "options", "profile_key", "profile_confidence",
    "normalized", "prelim_score", "analyzed_at", "status", "notes", "pinned", "raw", "mission", "provenance", "vehicle_id", "verdict_override", "verdict_override_reason",
]
# Only the raw scraper fields docs/app.js actually reads (raw?.time_left, "Auction ends").
RAW_PUBLIC_FIELDS = {"time_left"}

VIN_RE = re.compile(r"\b(?=[A-HJ-NPR-Z0-9]{17}\b)(?=[A-HJ-NPR-Z0-9]*[0-9])(?=[A-HJ-NPR-Z0-9]*[A-HJ-NPR-Z])[A-HJ-NPR-Z0-9]{17}\b")
PHONE_RE = re.compile(r"(?<!\d)(\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}(?!\d)")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def scrub_listing(row: dict[str, Any]) -> dict[str, Any]:
    out = {k: row.get(k) for k in PUBLIC_LISTING_FIELDS if k in row}
    if (row.get("seller_type") or "").lower() == "dealer" and row.get("seller_name"):
        out["seller_name"] = row["seller_name"]
    raw = row.get("raw") or {}
    out["raw"] = {k: raw[k] for k in RAW_PUBLIC_FIELDS if k in raw}
    return out


def _redact_str(s: str) -> str:
    return EMAIL_RE.sub("[email]", PHONE_RE.sub("[phone]", VIN_RE.sub("[VIN]", s)))


def redact_public(obj: Any) -> Any:
    """Defensive final pass over the whole export: strips VINs, phone numbers and
    emails out of every string, and drops the value of any {"key": "vin", ...} fact."""
    if isinstance(obj, dict):
        if obj.get("key") == "vin" and "value" in obj:
            obj = {**obj, "value": None}
        return {k: redact_public(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_public(v) for v in obj]
    if isinstance(obj, str):
        return _redact_str(obj)
    return obj


def find_leaks(obj: Any, path: str = "$") -> list[str]:
    """Paths of any VIN/phone/email still present in obj. Empty on a properly scrubbed export."""
    leaks: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            leaks += find_leaks(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            leaks += find_leaks(v, f"{path}[{i}]")
    elif isinstance(obj, str):
        if VIN_RE.search(obj) or PHONE_RE.search(obj) or EMAIL_RE.search(obj):
            leaks.append(path)
    return leaks


def _budget_signature(budget: dict[str, Any], urgency: str | None) -> str:
    import hashlib
    keys = ("ideal_low", "ideal_high", "max_price", "acceptable_all_in", "defeats_purpose_all_in")
    return hashlib.sha1(("|".join(str(budget.get(k)) for k in keys) + "|" + str(urgency)).encode()).hexdigest()[:12]


def scrub_assessment(a: dict[str, Any]) -> dict[str, Any]:
    """Drop the VIN itself and seller-identifying facts; keep everything else."""
    out = dict(a)
    vh = dict(out.get("vin_history") or {})
    vh.pop("vin", None)
    out["vin_history"] = vh
    ev = dict(out.get("evidence") or {})
    ev["facts"] = [f for f in ev.get("facts") or [] if f.get("key") not in {"vin", "seller_name", "seller_contact"}]
    out["evidence"] = ev
    return out


def build_export() -> dict[str, Any]:
    listings = [scrub_listing(r) for r in db.list_listings()]
    snaps = db.all_snapshots()
    assessments = db.latest_assessments_by_vehicle()
    errors = db.last_errors()
    docs = db.documents_by_listing()
    timelines: dict[int, list] = {}
    for l in listings:
        if l.get("vehicle_id"):
            if l["vehicle_id"] not in timelines:
                timelines[l["vehicle_id"]] = [{k: e.get(k) for k in ("event_date", "venue", "url", "mileage", "price", "price_type", "status", "evidence", "identity_confidence", "listing_id")} for e in db.vehicle_events(l["vehicle_id"])]
            l["timeline"] = timelines[l["vehicle_id"]]
        a = assessments.get(l["id"])
        l["assessment"] = scrub_assessment(a) if a else None
        l["last_error"] = errors.get(l["id"])
        l["documents"] = docs.get(l["id"], [])   # metadata only; document text stays local
        l["history"] = [
            {"t": s["seen_at"], "price": s.get("price"), "kind": s.get("price_kind"),
             "availability": s.get("availability"), "bids": s.get("bid_count")}
            for s in snaps.get(l["id"], [])
        ]
    profiles = db.list_profiles()
    markets = {}
    for p in profiles:
        comps = [l for l in listings if l.get("profile_key") == p["key"] and l["role"] == "comp"]
        actives = [l for l in listings if l.get("profile_key") == p["key"] and l["role"] == "candidate"
                   and l["availability"] == "active"]
        stats = market_stats(comps, actives)
        pool = [c.get("sold_price") or c.get("price") for c in comps if (c.get("sold_price") or c.get("price"))]
        for l in actives:
            l["price_pct_vs_sold"] = price_percentile(l.get("price"), pool)
        markets[p["key"]] = stats
    # Calibration: how far assessed scores land from their preliminary ones.
    # Applied to unassessed cards for sorting once there are enough samples.
    gaps = sorted(
        (l["assessment"]["score"]["total"] - l["prelim_score"])
        for l in listings if l.get("assessment") and l.get("prelim_score") is not None
        and (l["assessment"].get("score") or {}).get("total") is not None
    )
    calibration = {"samples": len(gaps), "offset": int(gaps[len(gaps) // 2]) if len(gaps) >= 3 else None,
                   "note": "median(assessed - preliminary) over assessed listings; applied to unassessed cards for sorting when samples >= 3"}
    from scout.policy.state import load_state
    st = load_state()
    budget_sig = _budget_signature(st.get("budget") or {}, st.get("urgency_mode"))
    for l in listings:
        a = l.get("assessment")
        if a:
            ctx = a.get("context") or {}
            a["context_changed"] = []
            if ctx.get("budget") and _budget_signature(ctx["budget"], ctx.get("urgency_mode")) != budget_sig:
                a["context_changed"].append("budget or urgency")
            if a.get("mission") and l.get("mission") and a["mission"] != l["mission"]:
                a["context_changed"].append(f"mission ({a['mission'].replace('_', ' ')} → {l['mission'].replace('_', ' ')})")
            a.pop("context", None)   # keep the numbers off the public page
    return redact_public({
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "policy_version": POLICY_VERSION,
        "calibration": calibration,
        "sites": SITES,
        "profiles": profiles,
        "markets": markets,
        "listings": listings,
    })


def write_export(data: dict[str, Any] | None = None, out_dir: Path | None = None) -> Path:
    data = data or build_export()
    out_dir = out_dir or SITE_DATA_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "scout.json"
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    return path


def _run(*cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), cwd=ROOT, capture_output=True, text=True)


def _rev_count(range_: str) -> int:
    r = _run("git", "rev-list", "--count", range_)
    return int(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip().isdigit() else 0


def git_publish(message: str = "Publish scout data") -> dict[str, Any]:
    """Build + scrub the export, back up the DB, commit docs/data and push.
    Returns {ok, changed, detail}; aborts before writing/committing on any leak."""
    export = build_export()
    leaks = find_leaks(export)
    if leaks:
        return {"ok": False, "changed": False, "detail": "aborted: possible leak at " + ", ".join(leaks[:5])}

    log = []
    try:
        from scout.backup import backup_db
        backup_db("publish")
        log.append("backup ok")
    except Exception as e:
        log.append(f"backup failed: {e}")

    path = write_export(export)
    rel = str(path.relative_to(ROOT))
    r = _run("git", "add", rel)
    log.append(f"$ git add {rel}\n{r.stdout}{r.stderr}")
    if r.returncode != 0:
        return {"ok": False, "changed": False, "detail": "\n".join(log)}

    r = _run("git", "commit", "-m", message, "--", rel)
    log.append(f"$ git commit -m {message!r} -- {rel}\n{r.stdout}{r.stderr}")
    changed = r.returncode == 0
    if r.returncode != 0 and "nothing to commit" not in (r.stdout + r.stderr):
        return {"ok": False, "changed": False, "detail": "\n".join(log)}

    r = _run("git", "fetch")
    log.append(f"$ git fetch\n{r.stdout}{r.stderr}")
    if not changed and _rev_count("@{u}..HEAD") == 0:
        log.append("nothing to push")
        return {"ok": True, "changed": False, "detail": "\n".join(log)}
    if _rev_count("HEAD..@{u}") > 0:
        log.append("behind upstream; not pushing")
        return {"ok": False, "changed": changed, "detail": "\n".join(log)}

    r = _run("git", "push")
    log.append(f"$ git push\n{r.stdout}{r.stderr}")
    if r.returncode != 0:
        return {"ok": False, "changed": changed, "detail": "\n".join(log)}
    return {"ok": True, "changed": changed, "detail": "\n".join(log)}
