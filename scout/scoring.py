"""Scores derived from structured fields. Never overrides the AI verdict."""
from __future__ import annotations

import statistics
from typing import Any


def weighted_score(scores: dict[str, int], weights: dict[str, float]) -> float | None:
    """1-5 weighted average over the axes present; weights renormalized."""
    num = 0.0
    den = 0.0
    for axis, w in weights.items():
        v = scores.get(axis)
        if v is None:
            continue
        num += float(v) * w
        den += w
    if den == 0:
        return None
    return round(num / den, 2)


def locality_hint(location: str | None) -> int | None:
    if not location:
        return None
    s = location.lower()
    bands = [
        (5, ["carmel", "monterey", "santa cruz", "san jose", "palo alto", "sunnyvale",
             "mountain view", "san francisco", "oakland", "berkeley", "san mateo",
             "santa clara", "bay area", "fremont", "salinas", "gilroy", "morgan hill"]),
        (4, ["sacramento", "napa", "fresno", "los angeles", "san diego", "orange county",
             "reno", "modesto", "stockton", "tahoe", "santa barbara", "ventura", ", ca"]),
        (3, [", az", " arizona", ", nv", " nevada", ", or", " oregon", ", wa",
             " washington", ", ut", " utah"]),
        (2, [", tx", " texas", ", co", " colorado", " illinois", ", il", " michigan", ", mi",
             " ohio", ", oh", " minnesota", ", mn", ", id", ", nm", ", ok", ", mt", ", wy",
             ", ks", ", ne", ", mo", ", ar", ", la", ", ga", ", fl", ", nc", ", sc", ", tn",
             ", ky", ", al", ", ms", ", ia", ", sd", ", nd"]),
        (1, ["new york", ", ny", "new jersey", ", nj", "massachusetts", ", ma", "connecticut", ", ct",
             "vermont", ", vt", "maine", ", me", "pennsylvania", ", pa", "wisconsin", ", wi",
             "indiana", ", in", "new hampshire", ", nh", "rhode island", ", ri", ", md", ", de",
             "virginia", ", va", "west virginia", ", wv", ", dc",
             "ontario", "quebec"]),
    ]
    for score, needles in bands:
        if any(n in s for n in needles):
            return score
    return None


def market_stats(comps: list[dict[str, Any]], actives: list[dict[str, Any]]) -> dict[str, Any]:
    """Summary numbers for a profile's market view."""
    def prices(rows, key):
        return sorted(p for p in (r.get(key) for r in rows) if isinstance(p, int) and p > 0)

    from scout.market import is_sale, sale_price   # one definition of a sale everywhere
    sold = [r for r in comps if is_sale(r)]
    sold_prices = sorted(sale_price(r) for r in sold)
    asking = prices(actives, "price")
    out: dict[str, Any] = {
        "active_count": len(actives),
        "comp_count": len(comps),
        "sold_count": len(sold),
    }
    if sold_prices:
        out["sold_median"] = int(statistics.median(sold_prices))
        out["sold_low"] = sold_prices[0]
        out["sold_high"] = sold_prices[-1]
    if asking:
        out["asking_median"] = int(statistics.median(asking))
        out["asking_low"] = asking[0]
        out["asking_high"] = asking[-1]
    mileages = sorted(m for m in (r.get("mileage") for r in comps + actives) if isinstance(m, int) and m > 0)
    if mileages:
        out["mileage_median"] = int(statistics.median(mileages))
    return out


def price_percentile(price: int | None, pool: list[int]) -> int | None:
    """Where an asking price sits among a pool (0 = cheapest, 100 = priciest)."""
    if price is None or not pool:
        return None
    below = sum(1 for p in pool if p < price)
    return int(round(100 * below / len(pool)))


def listing_age_days(l: dict, today=None) -> int | None:
    """Days since the site's listing date, else since we first saw it. None for live auctions."""
    from datetime import date
    if l.get("site") in {"bat", "carsandbids"} and l.get("availability") == "active":
        return None
    src = l.get("listing_date") or (l.get("first_seen") or "")[:10]
    if not src:
        return None
    try:
        d = date.fromisoformat(str(src)[:10])
    except ValueError:
        return None
    return max(0, ((today or date.today()) - d).days)


def auction_hours_left(l: dict, now=None) -> float | None:
    """Hours until an auction closes, from auction_end (Pacific) or from the site's
    "5 days" / "2:39:23" text plus when we read it. None when unknown / not live."""
    import re
    from datetime import datetime, timedelta, timezone
    if l.get("site") not in {"bat", "carsandbids"} or l.get("availability") != "active":
        return None
    now = now or datetime.now(timezone.utc)
    if l.get("auction_end"):
        try:
            end = datetime.fromisoformat(l["auction_end"])
            if end.tzinfo is None:   # auction sites show Pacific time; PDT or PST by date
                from zoneinfo import ZoneInfo
                end = end.replace(tzinfo=ZoneInfo("America/Los_Angeles"))
            return (end - now).total_seconds() / 3600
        except ValueError:
            pass
    raw = l.get("raw") or {}
    text = str(raw.get("time_left") or "")
    seen = raw.get("time_left_seen_at") or raw.get("scraped_at") or l.get("last_seen")
    if not text or not seen:
        return None
    try:
        t0 = datetime.fromisoformat(str(seen).replace("Z", "+00:00"))
        if t0.tzinfo is None:
            t0 = t0.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    hours = None
    m = re.search(r"(\d+)\s*day", text, re.I)
    if m:
        hours = int(m.group(1)) * 24
    elif re.match(r"^\s*(\d+):(\d{2}):(\d{2})", text):
        h, mnt, _ = re.match(r"^\s*(\d+):(\d{2}):(\d{2})", text).groups()
        hours = int(h) + int(mnt) / 60
    elif re.search(r"(\d+)\s*hour", text, re.I):
        hours = int(re.search(r"(\d+)\s*hour", text, re.I).group(1))
    elif re.search(r"(\d+)\s*min", text, re.I):
        hours = int(re.search(r"(\d+)\s*min", text, re.I).group(1)) / 60
    if hours is None:
        return None
    return hours - (now - t0).total_seconds() / 3600


def is_early_bid(l: dict, state: dict, now=None) -> tuple[bool, float | None]:
    """A live auction more than a day out: the current bid says little about the price."""
    hrs = auction_hours_left(l, now)
    if hrs is None:
        return (l.get("price_kind") in {"current_bid", "no_reserve"} and l.get("site") in {"bat", "carsandbids"} and l.get("availability") == "active"), None
    return hrs > float(state.get("early_bid_hours_before_close", 24)), hrs


def age_penalty(age: int | None, state: dict) -> tuple[int, str]:
    cfg = state.get("listing_age") or {}
    if age is None or age <= cfg.get("fresh_days", 45):
        return 0, ""
    for limit, pts in cfg.get("steps") or [[90, 2], [180, 4], [365, 6], [99999, 8]]:
        if age <= limit:
            months = round(age / 30)
            return int(pts), f"listed {months} month{'s' if months != 1 else ''} ago"
    return 8, "very old listing"


# ---------- preliminary score: the guide's 100-point rubric, cheap inputs ----------
# documentation 25 · condition 25 · price/value 15 · mission fit 15 · logistics 10 · spec 10
# Haiku rates documentation / condition / spec (0-10); everything else is arithmetic
# so that similar cars separate on price, budget, configuration and location.

LOGISTICS_BY_BAND = {5: 10, 4: 8, 3: 6, 2: 4, 1: 2, None: 5}


def _price_value_points(price: int | None, fair_mid: int | None) -> tuple[int, str]:
    if not price:
        return 7, "no price"
    if not fair_mid:
        return 8, "no comps to compare against"
    ratio = price / fair_mid
    # fair_mid is already mileage-adjusted to this car; no second adjustment here.
    note = f"price is {ratio:.0%} of fair value ${fair_mid:,}"
    # Deliberately no easy 15: the assessment weighs risk the arithmetic cannot see.
    pts = 13 if ratio <= 0.75 else 11 if ratio <= 0.85 else 9 if ratio <= 0.95 else 7 if ratio <= 1.05 else 5 if ratio <= 1.15 else 3 if ratio <= 1.30 else 1
    return pts, note


def preliminary_score(listing: dict, profile: dict | None, state: dict, fair: dict | None) -> tuple[int, dict]:
    n = listing.get("normalized") or {}
    r = n.get("ratings") or {}
    breakdown: dict = {}
    blocked = bool((listing.get("raw") or {}).get("blocked"))
    flags = len(n.get("red_flags") or [])

    # Documentation (25): the listing as presented. Unread listings start at 1/10.
    # Model-critical unknowns are the gates' job (they cap the verdict), not a
    # second penalty here.
    doc = round((r.get("documentation") or {}).get("score", 1) * 2.5)
    if listing.get("vin"):
        doc = min(25, doc + 2)
    if blocked:
        doc = min(doc, 4)
    breakdown["documentation"] = {"points": doc, "max": 25, "why": (r.get("documentation") or {}).get("why", "not read yet") + (" · VIN present" if listing.get("vin") else " · no VIN") + (" · page blocked" if blocked else "")}

    # Condition: the reader's evidence-based score only. Red flags are listed, not
    # double-counted here (many are documentation or logistics, not condition).
    cond = round((r.get("condition") or {}).get("score", 4) * 2.5)
    breakdown["condition"] = {"points": cond, "max": 25, "why": (r.get("condition") or {}).get("why", "not read yet") + (f" · {flags} red flag(s) noted" if flags else "")}

    price = listing.get("sold_price") or listing.get("price")
    fair_mid = fair.get("mid") if fair else None
    basis_word = ("sold comps" if fair.get("basis") == "sold" else "asking prices") if fair else "no comps"
    relaxed_note = f", relaxed: {', '.join(fair['relaxed'])}" if fair and fair.get("relaxed") else ""
    src_note = f"{basis_word}: {fair.get('n')}{relaxed_note}" if fair else "no comps"
    early, hrs = is_early_bid(listing, state)
    if early:
        # The bid is not a price yet. Value against fair value if we have a
        # sold-basis estimate, else stay neutral; either way say so.
        est = fair_mid if fair and fair.get("basis") == "sold" else None
        pv = 7 if est is None else _price_value_points(est, fair_mid)[0]
        why = f"early bid ${price:,} ignored" + (f" ({round(hrs / 24, 1)} days left)" if hrs is not None else "") + (f"; valued at fair value ${est:,}" if est else "; no sold comps, neutral")
    else:
        pv, why = _price_value_points(price, fair_mid)
    mission = listing.get("mission") or (profile or {}).get("mission_default") or "enthusiast_bridge"
    from scout.policy.state import budget_for
    budget = budget_for(state, mission)
    breakdown["price_value"] = {"points": pv, "max": 15, "why": f"{why} ({src_note})"}

    trans = (listing.get("transmission") or "").lower()
    auto_ok = bool((profile or {}).get("automatic_ok"))
    if mission in {"enthusiast_bridge", "future_keeper"} and not auto_ok:
        fit = 10 if trans == "manual" else 3 if trans == "automatic" else 7
        fit_why = {"manual": "manual, fits the brief", "automatic": "automatic in a manual brief", "": "transmission unknown"}.get(trans, "transmission unknown")
    elif mission == "pragmatic_bridge":
        fit, fit_why = 9, "pragmatic bridge: solves the immediate problem"
    else:
        fit, fit_why = 10, "utility / capability mission"
    if price and budget:
        if budget.get("ideal_low", 0) <= price <= budget.get("ideal_high", 10**9):
            fit += 2; fit_why += " · inside the ideal band"
        elif price <= budget.get("max_price", 10**9):
            fit += 1; fit_why += " · under the max"
        elif price > budget.get("defeats_purpose_all_in", 10**9):
            fit -= 6; fit_why += " · far over the budget"
        else:
            fit -= 3; fit_why += " · over the max"
    year = listing.get("year")
    if year and __import__("datetime").date.today().year - int(year) > 25:
        fit -= 1; fit_why += " · 25+ years old"
    pen, pen_why = age_penalty(listing_age_days(listing), state)
    if pen:
        fit -= pen; fit_why += f" · {pen_why} (−{pen})"
    fit = max(0, min(12, fit))  # 13-15 is earned only by evidence the assessment sees
    breakdown["mission_fit"] = {"points": fit, "max": 15, "why": f"{fit_why} ({mission.replace('_', ' ')})"}

    band = locality_hint(listing.get("location"))
    log = LOGISTICS_BY_BAND.get(band, 5)
    if listing.get("site") in {"bat", "carsandbids"} and (band or 0) < 4:
        log = max(0, log - 1)
    breakdown["logistics"] = {"points": log, "max": 10, "why": f"location band {band or 'unknown'}: {listing.get('location') or 'unknown'}"}

    spec = (r.get("spec") or {}).get("score", 5)
    breakdown["emotional_spec_fit"] = {"points": int(spec), "max": 10, "why": (r.get("spec") or {}).get("why", "not read yet")}

    total = sum(v["points"] for v in breakdown.values())
    return int(total), breakdown
