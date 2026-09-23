"""Export the DB, scrub it, split it for the static viewer, then publish it to
an orphan `gh-pages` branch (single commit, force-pushed). `main` never carries
data; each publish replaces the branch's whole history with one commit built
from git plumbing, so the user's working tree/index on `main` is never touched.
Seller contact details and private-party seller names never leave the DB."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
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

# Top-level export keys that belong in the light index (everything the board,
# market and profile views read). "listings" is handled separately.
INDEX_TOP_LEVEL_KEYS = ["generated_at", "policy_version", "calibration", "sites", "profiles", "markets"]

# Fields dropped from each listing in the index: full assessment evidence,
# photos, normalized text blobs, timeline, provenance, raw listing text, and
# local-workbench-only debug info. The detail view fetches these on demand.
INDEX_LISTING_DROP = {"photos", "provenance", "timeline", "last_error"}
# Assessment fields the board/market/profile views actually read (verdict
# chip, score badge, confidence, model tag, staleness note) — not the full
# evidence/gates/costs payload.
INDEX_ASSESSMENT_FIELDS = ["verdict", "model", "assessed_at", "policy_version", "shared_from", "context_changed", "confidence"]
# Normalized fields the board reads (search text, red-flag/quick-gate chips,
# price-drop total) — not the full ratings/breakdown/vin-decode blobs.
INDEX_NORMALIZED_FIELDS = ["prelim_summary", "red_flags", "quick_gates", "price_drops"]

DETAIL_PHOTO_CAP = 16

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
    """Write the full (unsplit) export. Kept for local/manual use; the publish
    flow below uses split_export() instead."""
    data = data or build_export()
    out_dir = out_dir or SITE_DATA_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "scout.json"
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    return path


def _index_listing(l: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in l.items() if k not in INDEX_LISTING_DROP}
    a = l.get("assessment")
    if a:
        summary = {k: a[k] for k in INDEX_ASSESSMENT_FIELDS if k in a}
        summary["score"] = {"total": (a.get("score") or {}).get("total")}
        out["assessment"] = summary
    n = l.get("normalized")
    if n:
        out["normalized"] = {k: n[k] for k in INDEX_NORMALIZED_FIELDS if k in n}
    return out


def _detail_listing(l: dict[str, Any]) -> dict[str, Any]:
    out = dict(l)
    if out.get("photos"):
        out["photos"] = out["photos"][:DETAIL_PHOTO_CAP]
    return out


def split_export(export: dict[str, Any]) -> tuple[dict[str, Any], dict[Any, dict[str, Any]]]:
    """Split an already-redacted export into a light index (board/market/profile
    views) and per-listing detail payloads (detail view, fetched on demand)."""
    listings = export.get("listings", [])
    index = {k: export[k] for k in INDEX_TOP_LEVEL_KEYS if k in export}
    index["listings"] = [_index_listing(l) for l in listings]
    details = {l["id"]: _detail_listing(l) for l in listings}
    return index, details


def _dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _stage_site(staging: Path, index: dict[str, Any], details: dict[Any, dict[str, Any]]) -> dict[str, int]:
    """Populate the staging dir with static assets + split data. Returns a
    sizes report."""
    for item in DOCS_DIR.iterdir():
        if item.name == "data":
            continue
        dest = staging / item.name
        if item.is_dir():
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)
    (staging / ".nojekyll").write_text("")

    data_dir = staging / "data"
    l_dir = data_dir / "l"
    l_dir.mkdir(parents=True, exist_ok=True)

    index_json = _dump(index)
    (data_dir / "index.json").write_text(index_json)

    detail_sizes: dict[Any, int] = {}
    for lid, d in details.items():
        s = _dump(d)
        (l_dir / f"{lid}.json").write_text(s)
        detail_sizes[lid] = len(s.encode())

    total = len(index_json.encode()) + sum(detail_sizes.values())
    largest = max(detail_sizes.values(), default=0)
    return {
        "index_bytes": len(index_json.encode()),
        "detail_files": len(detail_sizes),
        "largest_detail_bytes": largest,
        "total_bytes": total,
    }


def _run(*cmd: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run(list(cmd), cwd=ROOT, capture_output=True, text=True, env=full_env)


def git_publish(message: str | None = None) -> dict[str, Any]:
    """Build + scrub the export, split it, back up the DB, and publish a single
    orphan commit to `gh-pages` (force-pushed). `main`'s working tree and index
    are never touched — everything below uses git plumbing against a temporary
    index file and a temporary work-tree.
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

    index, details = split_export(export)

    with tempfile.TemporaryDirectory(prefix="hoopty-publish-") as tmp:
        tmp_path = Path(tmp)
        staging = tmp_path / "site"
        staging.mkdir()
        sizes = _stage_site(staging, index, details)
        log.append(f"sizes: {sizes}")

        index_file = tmp_path / "index"
        env = {"GIT_INDEX_FILE": str(index_file)}

        r = _run("git", f"--work-tree={staging}", "add", "-A", env=env)
        log.append(f"$ git --work-tree={staging} add -A\n{r.stdout}{r.stderr}")
        if r.returncode != 0:
            return {"ok": False, "changed": False, "detail": "\n".join(log)}

        r = _run("git", "write-tree", env=env)
        log.append(f"$ git write-tree\n{r.stdout}{r.stderr}")
        if r.returncode != 0:
            return {"ok": False, "changed": False, "detail": "\n".join(log)}
        tree_sha = r.stdout.strip()

        r = _run("git", "fetch", "origin", "gh-pages")
        log.append(f"$ git fetch origin gh-pages\n{r.stdout}{r.stderr}")

        r = _run("git", "rev-parse", "origin/gh-pages^{tree}")
        remote_tree = r.stdout.strip() if r.returncode == 0 else None
        if remote_tree == tree_sha:
            log.append("nothing changed; not pushing")
            return {"ok": True, "changed": False, "detail": "\n".join(log)}

        commit_message = message or f"Publish scout data {datetime.now(timezone.utc).replace(microsecond=0).isoformat()}"
        r = _run("git", "commit-tree", tree_sha, "-m", commit_message)
        log.append(f"$ git commit-tree {tree_sha} -m {commit_message!r}\n{r.stdout}{r.stderr}")
        if r.returncode != 0:
            return {"ok": False, "changed": False, "detail": "\n".join(log)}
        commit_sha = r.stdout.strip()

        r = _run("git", "push", "--force", "origin", f"{commit_sha}:refs/heads/gh-pages")
        log.append(f"$ git push --force origin {commit_sha}:refs/heads/gh-pages\n{r.stdout}{r.stderr}")
        if r.returncode != 0:
            return {"ok": False, "changed": True, "detail": "\n".join(log)}

        return {"ok": True, "changed": True, "detail": "\n".join(log)}
