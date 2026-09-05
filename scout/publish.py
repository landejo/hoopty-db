"""Export the DB to docs/data/*.json for the static viewer, then commit + push.
Seller contact details and private-party seller names never leave the DB."""
from __future__ import annotations

import json
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
    "normalized", "prelim_score", "analyzed_at", "status", "notes", "pinned", "raw", "mission", "provenance", "vehicle_id",
]
PRIVATE_FIELDS = {"seller_contact", "raw_text", "vin"}


def scrub_listing(row: dict[str, Any]) -> dict[str, Any]:
    out = {k: row.get(k) for k in PUBLIC_LISTING_FIELDS if k in row}
    if (row.get("seller_type") or "").lower() == "dealer" and row.get("seller_name"):
        out["seller_name"] = row["seller_name"]
    raw = dict(row.get("raw") or {})
    for k in list(raw):
        if any(s in k.lower() for s in ("phone", "email", "contact", "seller_url", "profile")):
            raw.pop(k)
    out["raw"] = raw
    return out


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
    assessments = db.latest_assessments()
    timelines: dict[int, list] = {}
    for l in listings:
        if l.get("vehicle_id"):
            if l["vehicle_id"] not in timelines:
                timelines[l["vehicle_id"]] = [{k: e.get(k) for k in ("event_date", "venue", "url", "mileage", "price", "price_type", "status", "evidence", "identity_confidence", "listing_id")} for e in db.vehicle_events(l["vehicle_id"])]
            l["timeline"] = timelines[l["vehicle_id"]]
        a = assessments.get(l["id"])
        l["assessment"] = scrub_assessment(a) if a else None
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
    return {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "policy_version": POLICY_VERSION,
        "calibration": calibration,
        "sites": SITES,
        "profiles": profiles,
        "markets": markets,
        "listings": listings,
    }


def write_export(data: dict[str, Any] | None = None, out_dir: Path | None = None) -> Path:
    data = data or build_export()
    out_dir = out_dir or SITE_DATA_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "scout.json"
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    return path


def git_publish(message: str = "Publish scout data") -> str:
    """Commit docs/data and push. Returns the git output."""
    path = write_export()
    rel = str(path.relative_to(ROOT))
    out = []
    for cmd in (["git", "add", rel], ["git", "commit", "-m", message, "--", rel], ["git", "push"]):
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        out.append(f"$ {' '.join(cmd)}\n{r.stdout}{r.stderr}")
        if r.returncode != 0 and "nothing to commit" not in (r.stdout + r.stderr):
            break
    return "\n".join(out)
