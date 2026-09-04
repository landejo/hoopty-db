"""Vehicle provenance: one record per VIN, a timeline of events across venues,
and the deterministic same-car analysis (markup, elapsed time, mileage added,
resale/relist flags, withdrawal detection). The extension gathers search hits
in the user's browser; scout.ai.provenance classifies them; this module does
the arithmetic and the cautious wording."""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

from scout import db
from scout.config import SITES

PRICE_TYPES = ["verified_sale", "winning_bid", "high_bid_reserve_not_met", "advertised_sold", "asking", "estimated"]
# Precedence when choosing the "last documented price" (higher wins).
PRICE_TYPE_RANK = {"verified_sale": 5, "winning_bid": 4, "advertised_sold": 3, "high_bid_reserve_not_met": 2, "asking": 1, "estimated": 0}
STATUSES = ["Listed", "Sold", "Bid to / reserve not met", "Withdrawn", "Relisted", "Price reduced",
            "Seller decided to keep", "Dealer acquisition", "Auction or wholesale movement", "Unknown"]
IDENTITY = ["confirmed", "strongly_likely", "possible", "not_established"]
IDENTITY_LABEL = {"confirmed": "Confirmed by an exact VIN match", "strongly_likely": "Strongly likely the same car",
                  "possible": "Possible match only", "not_established": "Not established"}

VERY_RECENT_DAYS, RECENT_DAYS, RAPID_RELIST_DAYS = 180, 730, 90
MATERIAL_MARKUP, MAJOR_MARKUP = 0.10, 0.20


# ---------- identity ----------

def fingerprint(l: dict[str, Any]) -> str | None:
    if not (l.get("year") and l.get("make") and l.get("model")):
        return None
    color = re.sub(r"[^a-z]", "", (l.get("exterior_color") or "").lower())[:12]
    model = re.sub(r"[^a-z0-9]", "", (l.get("model") or "").lower())
    return f"{l['year']}|{l['make'].lower()}|{model}|{color}"


def link_listing_vehicle(listing_id: int, path=None) -> int | None:
    """Attach the listing to its VIN record (or a provisional fingerprint record)
    and write its own events (listed, price reduced, sold, bid to, withdrawn)."""
    l = db.get_listing(listing_id, path)
    if not l:
        return None
    fp = fingerprint(l)
    if not (l.get("vin") or fp):
        return None
    vid = db.upsert_vehicle(l.get("vin"), fp, l, path, current_vehicle_id=l.get("vehicle_id"))
    db.update_listing(listing_id, {"vehicle_id": vid}, path)
    record_listing_events(l, vid, path)
    return vid


def record_listing_events(l: dict[str, Any], vehicle_id: int, path=None) -> int:
    venue = SITES.get(l.get("site"), l.get("site"))
    base = {"listing_id": l["id"], "venue": venue, "url": l["url"], "mileage": l.get("mileage"),
            "source": "tracker", "identity_confidence": "confirmed" if l.get("vin") else "strongly_likely",
            "seller": l.get("seller_name")}
    auction = l.get("site") in {"bat", "carsandbids"}
    n = 0
    snaps = db.list_snapshots(l["id"], path)
    first_price = next((s["price"] for s in snaps if s.get("price")), l.get("price"))
    listed_date = (l.get("listing_date") or l.get("first_seen") or "")[:10]
    if first_price:
        n += db.add_vehicle_event(vehicle_id, {**base, "event_date": listed_date, "price": first_price,
                                               "price_type": "asking", "status": "Listed",
                                               "evidence": f"Advertised at ${first_price:,}" + (" (opening bid)" if auction else "")}, path)
    prev = first_price
    for s in snaps:
        p = s.get("price")
        if p and prev and p < prev and s.get("availability") == "active":
            n += db.add_vehicle_event(vehicle_id, {**base, "event_date": s["seen_at"][:10], "price": p, "price_type": "asking",
                                                   "status": "Price reduced", "evidence": f"Asking reduced from ${prev:,} to ${p:,}"}, path)
        if p:
            prev = p
    av = l.get("availability")
    last_date = (l.get("auction_end") or l.get("last_seen") or "")[:10]
    if av == "sold":
        price = l.get("sold_price") or l.get("price")
        ptype = "winning_bid" if auction and l.get("sold_price") else ("verified_sale" if l.get("sold_price") and auction else "advertised_sold")
        if auction and l.get("sold_price"):
            ptype = "winning_bid"
        n += db.add_vehicle_event(vehicle_id, {**base, "event_date": last_date, "price": price, "price_type": ptype, "status": "Sold",
                                               "evidence": (f"Sold for ${price:,} (auction result)" if auction and l.get("sold_price")
                                                            else f"Tracked as sold after being advertised for ${price:,}; the negotiated transaction price is unknown.") if price else "Marked sold"}, path)
    elif av == "ended":
        n += db.add_vehicle_event(vehicle_id, {**base, "event_date": last_date, "price": l.get("price"), "price_type": "high_bid_reserve_not_met",
                                               "status": "Bid to / reserve not met", "evidence": f"Bid to ${l.get('price'):,}, reserve not met" if l.get("price") else "Auction ended without a sale"}, path)
    elif av == "withdrawn":
        n += db.add_vehicle_event(vehicle_id, {**base, "event_date": last_date, "status": "Withdrawn", "evidence": "Seller withdrew the listing"}, path)
    return n


# ---------- query set (guide §2 of the provenance brief) ----------

def build_queries(l: dict[str, Any]) -> list[dict[str, str]]:
    vin = (l.get("vin") or "").strip().upper()
    model = " ".join(str(x) for x in (l.get("make"), l.get("model")) if x)
    title = (l.get("title") or f"{l.get('year') or ''} {model}").strip()
    miles = f"{l['mileage']:,}" if l.get("mileage") else ""
    miles_k = f"{round(l['mileage'] / 1000)}k" if l.get("mileage") else ""
    seller = (l.get("seller_name") or "").strip()
    color = (l.get("exterior_color") or "").strip()
    loc = (l.get("location") or "").split(",")[0].strip()
    site_id = str(l.get("site_id") or "").strip()
    q: list[dict[str, str]] = []
    web = ["duckduckgo", "bing", "google"]
    def add(engine: str, text: str, purpose: str):
        text = re.sub(r"\s+", " ", text).strip()
        if text and not any(x["engine"] == engine and x["q"] == text for x in q):
            q.append({"engine": engine, "q": text, "purpose": purpose})
    if vin:
        for e in web:
            add(e, f'"{vin}"', "exact VIN")
        add("bat", vin, "BaT site search by VIN")
        add("ebay_sold", vin, "eBay completed/sold by VIN")
        add("classic", vin, "Classic.com by VIN")
        add("reddit", f'"{vin}"', "Reddit by VIN")
        add("facebook_posts", vin, "Facebook posts by VIN")
        for e in web:
            add(e, f'"{vin}" {model}', "VIN + model")
    add("facebook_posts", f"{title} {miles_k}".strip(), "Facebook posts by title")
    add("facebook_marketplace", f"{l.get('year') or ''} {model} {color}".strip(), "Facebook Marketplace by model + color")
    add("bat", f"{l.get('year') or ''} {model}".strip(), "BaT site search by model")
    add("ebay_sold", f"{l.get('year') or ''} {model} {color}".strip(), "eBay sold by model + color")
    add("reddit", f'"{title}"', "Reddit by title")
    for e in web:
        add(e, f'"{title}" {miles}'.strip(), "title + mileage")
        if seller:
            add(e, f'"{seller}" {model}', "seller + model")
    if site_id and len(site_id) >= 6:
        add(web[0], f'"{site_id}"', "listing / lot id")
    for e in web:
        if color or miles_k:
            add(e, f'{l.get("year") or ""} {model} {color} {miles_k} {loc}'.strip(), "distinctive combination")
    return q[:28]


# ---------- deterministic analysis ----------

def _d(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def describe_price(ev: dict[str, Any]) -> str:
    p, t = ev.get("price"), ev.get("price_type")
    if not p:
        return ev.get("evidence") or ev.get("status") or ""
    money = f"${int(p):,}"
    return {
        "verified_sale": f"Verified transaction price {money}.",
        "winning_bid": f"Winning bid {money}.",
        "high_bid_reserve_not_met": f"High bid {money} with the reserve not met.",
        "advertised_sold": f"Previously advertised at {money} and later marked sold; the actual transaction price is not public.",
        "asking": f"Asking price {money}.",
        "estimated": f"Estimated or unverified price {money}.",
    }.get(t or "", f"{money} ({t or 'unknown type'}).")


def analyze(current: dict[str, Any], events: list[dict[str, Any]], statements: list[dict[str, Any]],
            interpretation: dict[str, Any] | None = None, today: date | None = None) -> dict[str, Any]:
    """Same-car findings from a timeline. Only confirmed / strongly_likely events
    count toward price progression; possible matches are listed but never used."""
    today = today or date.today()
    interpretation = interpretation or {}
    cur_price = current.get("price") or 0
    cur_date = _d(current.get("listing_date") or current.get("first_seen")) or today
    usable = [e for e in events if e.get("identity_confidence") in {"confirmed", "strongly_likely"}]
    others = [e for e in events if e.get("identity_confidence") not in {"confirmed", "strongly_likely"}]
    prior = [e for e in usable if e.get("listing_id") != current.get("id") and e.get("url") != current.get("url")
             and (_d(e.get("event_date")) is None or _d(e.get("event_date")) <= cur_date)]

    # Last documented price: prefer the most recent transaction-grade price, else the latest advertised.
    with_price = [e for e in prior if e.get("price")]
    last_txn = [e for e in with_price if e.get("price_type") in {"verified_sale", "winning_bid", "advertised_sold"}]
    ref = None
    if last_txn:
        ref = max(last_txn, key=lambda e: (_d(e.get("event_date")) or date.min, PRICE_TYPE_RANK.get(e.get("price_type"), 0)))
    elif with_price:
        ref = max(with_price, key=lambda e: (_d(e.get("event_date")) or date.min, PRICE_TYPE_RANK.get(e.get("price_type"), 0)))

    findings: dict[str, Any] = {"flags": [], "effect": [], "sources": []}
    progression = None
    if ref and cur_price:
        delta = cur_price - int(ref["price"])
        pct = delta / int(ref["price"]) if ref["price"] else None
        rd = _d(ref.get("event_date"))
        elapsed = (cur_date - rd).days if rd else None
        miles_added = (current.get("mileage") - ref["mileage"]) if (current.get("mileage") and ref.get("mileage")) else None
        progression = {"reference": ref, "reference_description": describe_price(ref), "current_price": cur_price,
                       "dollar_change": delta, "percent_change": round(pct, 4) if pct is not None else None,
                       "elapsed_days": elapsed, "mileage_added": miles_added}
        if pct is not None and pct > MAJOR_MARKUP:
            findings["flags"].append("major_markup")
        elif pct is not None and pct > MATERIAL_MARKUP:
            findings["flags"].append("material_markup")
        if ref.get("status") == "Sold" and elapsed is not None:
            if elapsed <= VERY_RECENT_DAYS:
                findings["flags"].append("very_recent_resale")
            elif elapsed <= RECENT_DAYS:
                findings["flags"].append("recent_resale")
        if elapsed is not None and elapsed <= RAPID_RELIST_DAYS and ref.get("status") in {"Sold", "Listed", "Withdrawn", "Bid to / reserve not met", "Price reduced"}:
            findings["flags"].append("rapid_relisting")
        if miles_added is not None and miles_added < 0:
            findings["flags"].append("mileage_decreased")

    # Withdrawal / keep statements that post-date the listing mean it is not actively available.
    withdrawals = [s for s in statements if s.get("kind") in {"withdrawn", "keep", "sold"}
                   and (_d(s.get("date")) is None or _d(s.get("date")) >= cur_date)]
    withdrawn_events = [e for e in usable if e.get("status") in {"Withdrawn", "Seller decided to keep"}
                        and (_d(e.get("event_date")) is None or _d(e.get("event_date")) >= cur_date)]
    currently_available = not (withdrawals or withdrawn_events)
    status_note = None
    if not currently_available:
        s0 = withdrawals[0] if withdrawals else withdrawn_events[0]
        quote = s0.get("quote") or s0.get("evidence") or ""
        when = s0.get("date") or s0.get("event_date") or "an undated"
        status_note = f"The seller subsequently updated a public post ({when}): \"{quote}\". Do not present this car as actively available."
        findings["flags"].append("not_actively_available")

    conf = "not_established"
    for lvl in ("confirmed", "strongly_likely", "possible"):
        if any(e.get("identity_confidence") == lvl for e in prior) or (lvl == "confirmed" and current.get("vin") and prior):
            conf = lvl
            break

    if progression:
        p = progression
        sign = "increase" if p["dollar_change"] >= 0 else "decrease"
        el = f" after approximately {_human_days(p['elapsed_days'])}" if p["elapsed_days"] is not None else ""
        findings["effect"].append(
            f"The current ${cur_price:,} ask represents a {abs(p['percent_change'] or 0):.1%} gross {sign} (${abs(p['dollar_change']):,}) over the last documented price{el}."
            + (f" {p['mileage_added']:,} miles were added." if p["mileage_added"] is not None and p["mileage_added"] >= 0
               else f" The reported mileage is {abs(p['mileage_added']):,} LOWER than the earlier listing: an odometer inconsistency that must be resolved before any purchase." if p["mileage_added"] is not None else ""))
        if "major_markup" in findings["flags"] or "material_markup" in findings["flags"]:
            findings["effect"].append("This creates negotiating leverage but does not by itself prove dishonesty. Ask what materially improved since the previous sale; transaction costs are not improvements.")
            findings["effect"].append(f"Anchor the price ceiling to the last documented price (${int(ref['price']):,}) plus documented post-sale work, not to the new ask.")
    if not currently_available:
        findings["effect"].append("Verdict: Do not pursue while the seller says the car is not for sale; re-open only if it is relisted.")
    findings["sources"] = sorted({e.get("url") for e in events if e.get("url")} | {s.get("url") for s in statements if s.get("url")})

    return {
        "current_status": {"available": currently_available, "note": status_note, "listing_availability": current.get("availability")},
        "same_car_history": [_event_view(e) for e in usable],
        "possible_matches": [_event_view(e) for e in others],
        "price_progression": progression,
        "what_changed": {k: interpretation.get(k) or [] for k in ("work_before_prior_sale", "work_after_prior_sale", "cosmetic_or_preference", "repairs_correcting_faults")},
        "cross_post_findings": statements,
        "confidence": conf,
        "confidence_label": IDENTITY_LABEL[conf] if prior else "No prior same-car history established",
        "flags": findings["flags"],
        "effect": findings["effect"],
        "sources": findings["sources"],
        "analyzed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }


def _human_days(d: int) -> str:
    if d < 14:
        return f"{d} days"
    if d < 60:
        return f"{round(d / 7)} weeks"
    if d < 365:
        return f"{round(d / 30)} month{'s' if round(d / 30) != 1 else ''}"
    return f"{d / 365:.1f} years"


def _event_view(e: dict[str, Any]) -> dict[str, Any]:
    return {"date": e.get("event_date"), "venue": e.get("venue"), "mileage": e.get("mileage"), "price": e.get("price"),
            "price_type": e.get("price_type"), "status": e.get("status"), "evidence": e.get("evidence"),
            "description": describe_price(e), "url": e.get("url"), "identity_confidence": e.get("identity_confidence"),
            "seller": e.get("seller"), "listing_id": e.get("listing_id")}


def timeline_for_listing(listing_id: int, path=None) -> list[dict[str, Any]]:
    l = db.get_listing(listing_id, path)
    if not l or not l.get("vehicle_id"):
        return []
    return db.vehicle_events(l["vehicle_id"], path)


def repair_vehicle_links(path=None) -> dict[str, int]:
    """Undo fingerprint merges and relink every listing (safe to re-run)."""
    split = db.split_provisional_vehicles(path)
    relinked = 0
    for l in db.list_listings(path=path):
        if link_listing_vehicle(l["id"], path):
            relinked += 1
    return {"split": split, "relinked": relinked}
