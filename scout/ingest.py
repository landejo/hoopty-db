"""Ingest pipeline: extension payload -> DB rows -> normalization -> profile."""
from __future__ import annotations

import re
from typing import Any

from scout import db
from scout.config import AUCTION_SITES, CONFIG
from scout.profiles import match_profile, suggest_key
from scout.scoring import locality_hint, weighted_score

SOLD_RE = re.compile(r"\b(sold|sold for|no longer available|this listing has ended|listing ended)\b", re.I)
ENDED_RE = re.compile(r"\b(bid to|reserve not met|auction ended|ended)\b", re.I)
PRICE_RE = re.compile(r"\$\s?([\d,]{3,})")


def detect_availability(item: dict[str, Any], site: str) -> str:
    """Cheap, deterministic read of the scraper's flags + text before any AI."""
    if item.get("sold") is True:
        return "sold"
    if item.get("ended") is True:
        return "ended"
    head = ((item.get("price_text") or "") + " " + (item.get("badge") or "") + " " +
            (item.get("detail", {}) or {}).get("status_text", "")).strip()
    if SOLD_RE.search(head):
        return "sold"
    if site in AUCTION_SITES and ENDED_RE.search(head):
        return "ended"
    return "active"


def parse_price(text: str | None) -> int | None:
    if not text:
        return None
    m = PRICE_RE.search(text)
    if not m:
        return None
    try:
        v = int(m.group(1).replace(",", ""))
    except ValueError:
        return None
    return v if 0 < v < 2_000_000 else None


def _needs_normalize(existing: dict[str, Any] | None, raw_text: str, availability: str) -> bool:
    if existing is None or not existing.get("normalized_at"):
        return True
    if existing.get("availability") != availability:
        return True
    old = (existing.get("raw_text") or "").strip()
    return abs(len(old) - len(raw_text.strip())) > 200


def ingest_items(site: str, items: list[dict[str, Any]], include_sold: bool | None = None,
                 run_ai: bool = True, full_sync: bool = False) -> dict[str, Any]:
    """Upsert every item; normalize new/changed ones; assign profiles.
    full_sync=True means `items` is the complete saved list for `site`, so
    active listings missing from it are marked removed. Single-listing adds
    and re-normalizations must leave that False."""
    if include_sold is None:
        include_sold = not CONFIG.skip_sold
    profiles = db.list_profiles()
    seen_urls: set[str] = set()
    stats = {"received": len(items), "created": 0, "updated": 0, "skipped_sold": 0,
             "normalized": 0, "comps": 0, "candidates": 0, "profiles_created": 0, "errors": []}

    for item in items:
        url = (item.get("url") or "").strip()
        if not url:
            continue
        seen_urls.add(url)
        availability = detect_availability(item, site)
        if item.get("_vanished") and availability == "active":
            availability = "removed"  # gone from the saved page and its page shows no result
        if availability in {"sold", "ended"} and not include_sold:
            stats["skipped_sold"] += 1
            continue
        existing = db.get_listing_by_url(url)
        if item.get("_touch"):
            if existing:
                db.update_listing(existing["id"], {"last_seen": db.now()})
            continue
        detail = item.get("detail") or {}
        raw_text = (detail.get("text") or item.get("card_text") or "")[:120_000]
        role = "comp" if availability in {"sold", "ended"} else "candidate"
        if existing and existing.get("role") == "comp":
            role = "comp"  # never promote a comp back to candidate
        values: dict[str, Any] = {
            "site": site, "site_id": item.get("site_id"), "url": url, "role": role,
            "availability": availability, "title": (item.get("title") or existing and existing.get("title") or "")[:300],
            "thumb": item.get("thumb") or (existing or {}).get("thumb"),
            "raw_text": raw_text or (existing or {}).get("raw_text"),
            "raw": {k: v for k, v in detail.items() if k not in {"text", "photos"}},
            "photos": (detail.get("photos") or (existing or {}).get("photos") or [])[:40],
        }
        card_price = parse_price(item.get("price_text"))
        if card_price and not (existing and existing.get("price") and not _needs_normalize(existing, raw_text, availability)):
            values["price"] = card_price
        if detail.get("auction_end"):
            values["auction_end"] = detail["auction_end"]
        lid, created = db.upsert_listing(values)
        stats["created" if created else "updated"] += 1

        if run_ai and CONFIG.ai_enabled and _needs_normalize(existing, raw_text, availability) and raw_text:
            try:
                from scout.ai.normalize import normalize_listing  # lazy
                hints = {"title": item.get("title"), "price_text": item.get("price_text"),
                         "card_text": item.get("card_text"), "url": url,
                         "scraper_availability": availability}
                norm = normalize_listing(raw_text, hints, site, profiles)
                _apply_normalization(lid, norm, availability, profiles, stats, raw_text)
                stats["normalized"] += 1
            except Exception as e:  # keep syncing even if one call fails
                stats["errors"].append(f"{url}: {e}")
                db.log_event("normalize_error", lid, str(e))
        row = db.get_listing(lid)
        db.add_snapshot(lid, row.get("price"), row.get("price_kind"), row.get("availability"),
                        (detail.get("bid_count") if isinstance(detail.get("bid_count"), int) else None))
        stats["comps" if row["role"] == "comp" else "candidates"] += 1

    if full_sync and items:
        stats["marked_removed"] = db.mark_unseen_removed(site, seen_urls)
    db.log_event("sync", None, f"{site}: {stats}")
    return stats


def _apply_normalization(lid: int, norm: dict[str, Any], scraper_availability: str,
                         profiles: list[dict[str, Any]], stats: dict[str, Any], raw_text: str) -> None:
    updates: dict[str, Any] = {}
    for k in ("year", "make", "model", "generation", "trim", "engine", "engine_liters",
              "transmission", "drivetrain", "body_style", "exterior_color", "interior_color",
              "mileage", "price", "price_kind", "sold_price", "location", "vin", "seller_type",
              "seller_name", "title_status", "accidents", "num_owners", "listing_date", "options"):
        if norm.get(k) is not None:
            updates[k] = norm[k]
    # The model reading "sold" in the text beats the scraper's "active" but never the reverse.
    if norm.get("availability") in {"sold", "ended"} and scraper_availability == "active":
        updates["availability"] = norm["availability"]
        updates["role"] = "comp"
    updates["normalized"] = {k: norm.get(k) for k in ("highlights", "red_flags", "summary" if False else "prelim_summary", "prelim_scores", "profile_confidence")}
    updates["normalized_at"] = db.now()

    # Profile: deterministic match first, then the model's pick, then generate.
    prof = match_profile(profiles, norm.get("make"), norm.get("model"), norm.get("year"))
    pk = norm.get("profile_key")
    if prof is None and pk and pk not in {"new", "skip"}:
        prof = next((p for p in profiles if p["key"] == pk), None)
    if prof is None and pk != "skip" and norm.get("make") and norm.get("model") and CONFIG.ai_enabled:
        key = suggest_key(norm.get("make"), norm.get("model"), norm.get("generation"))
        prof = db.get_profile(key)
        if prof is None:
            try:
                from scout.ai.profile_gen import generate_profile  # lazy
                gen = generate_profile(norm["make"], norm["model"], norm.get("generation"),
                                       norm.get("year"), raw_text)
                if gen:
                    gen["key"] = key  # deterministic key so retries dedupe
                    gen["source"] = "ai"
                    gen["verified"] = False
                    db.upsert_profile(gen)
                    prof = db.get_profile(key)
                    profiles.append(prof)
                    stats["profiles_created"] += 1
                    db.log_event("profile_created", lid, key)
            except Exception as e:
                stats["errors"].append(f"profile {key}: {e}")
    if prof:
        updates["profile_key"] = prof["key"]
        updates["profile_confidence"] = norm.get("profile_confidence")
        scores = dict(norm.get("prelim_scores") or {})
        loc = locality_hint(norm.get("location"))
        if loc and "locality" not in scores:
            scores["locality"] = loc
        updates["prelim_score"] = weighted_score(scores, prof.get("weights") or {})
        updates["normalized"]["prelim_scores"] = scores

    # Free VIN decode (NHTSA) + deterministic identity checks, and the sync-time
    # policy flags shown on cards. None of this needs the paid model.
    from scout.policy.engine import default_mission  # lazy: policy imports pydantic
    from scout.policy.gates import quick_gates
    from scout.policy.state import load_state
    from scout.vin import compare_decode, decode_vin
    current = db.get_listing(lid) or {}
    merged = {**current, **{k: v for k, v in updates.items() if k != "normalized"}}
    if norm.get("vin"):
        decoded = decode_vin(norm["vin"])
        if decoded:
            updates["normalized"]["vin_decode"] = {k: decoded.get(k) for k in
                ("year", "make", "model", "series", "trim", "engine_liters", "cylinders", "body_class", "plant_country")}
            updates["normalized"]["vin_recall_count"] = len(decoded.get("recalls") or [])
            updates["normalized"]["vin_contradictions"] = compare_decode(decoded, merged)
    state = load_state()
    mission = current.get("mission") or default_mission(prof)
    if not current.get("mission"):
        updates["mission"] = mission
    updates["normalized"]["quick_gates"] = quick_gates(merged, prof, mission, state)
    db.update_listing(lid, updates)
