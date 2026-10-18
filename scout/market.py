"""Deterministic market value from comps: sold-price segmentation, mileage
adjustment, and asking-price fallback. No AI; pure functions over listing rows."""
from __future__ import annotations

import math
import statistics
from datetime import date, timedelta
from typing import Any

_ENDED_SALE_KINDS = {"sold", "asking"}   # price_kind values that still count as a sale when availability=ended
# 1.9.0: a sold or delisted classified ad shows its last asking price, not what it sold for
# (BaT and Cars & Bids hammers are transaction prices).
_CLASSIFIED_SITES = {"facebook", "cargurus", "carscom", "autotrader", "carfax"}
LAST_ASK_DISCOUNT = 0.93

# Badge-engineered twins are one market: a BRZ, an 86 and an FR-S are the same
# car, so each is valued against all three (2026-10-14).
COMP_TWINS = [frozenset({"subaru_brz", "toyota_86", "scion_frs"})]


def comp_keys(profile_key: str) -> list[str]:
    """The profile keys whose listings price this one: itself plus any twins."""
    for group in COMP_TWINS:
        if profile_key in group:
            return sorted(group)
    return [profile_key]


_OPEN_BODY = ("roadster", "convertible", "cabrio", "spyder", "targa")
_CLOSED_BODY = ("coupe", "hatch", "sedan", "wagon", "suv")


def is_sale(r: dict[str, Any]) -> bool:
    kind = r.get("price_kind")
    avail = r.get("availability")
    if kind == "sold" or avail == "sold":
        pass
    elif avail == "ended" and kind in _ENDED_SALE_KINDS:
        pass
    else:
        return False
    price = r.get("sold_price") or r.get("price")
    return bool(price and price > 0)


def sale_price(r: dict[str, Any]) -> int | None:
    return r.get("sold_price") or r.get("price")


def realized_factor(r: dict[str, Any]) -> float:
    """1.9.0: an auction hammer counts in full; a classified ad that sold or vanished
    (Facebook, CarGurus, Cars.com, Autotrader) shows only its last asking price."""
    return LAST_ASK_DISCOUNT if r.get("site") in _CLASSIFIED_SITES else 1.0


def body_class(r: dict[str, Any]) -> str | None:
    text = " ".join(str(r.get(k) or "") for k in ("body_style", "model", "trim")).lower()
    for w in _OPEN_BODY:
        if w in text:
            return "open"
    for w in _CLOSED_BODY:
        if w in text:
            return "closed"
    return None


def _mileage_factor(listing_miles: int, comp_miles: int) -> float:
    factor = math.exp(-0.004 * (listing_miles - comp_miles) / 1000)
    return max(0.70, min(1.35, factor))


# Model year (2026-10-18): a comp from another year is moved to the listing's year at a
# per-year rate learned from the model's own sales (log price vs year, after mileage),
# or a modest default when there are too few. Before, years only chose the pool, so a
# 2015 Miata priced from 2008-2011 sales got no credit for being newer.
DEFAULT_YEAR_RATE = 0.03
YEAR_RATE_BOUNDS = (-0.03, 0.08)      # a collectible older year can be worth more; cap the noise
YEAR_FACTOR_BOUNDS = (0.70, 1.40)
YEAR_RATE_MIN_SALES, YEAR_RATE_MIN_YEARS = 6, 3


def year_rate(listing: dict[str, Any], sales: list[dict[str, Any]]) -> tuple[float, str]:
    """(rate per year, "learned from n sales" | "default")."""
    l_miles = listing.get("mileage")
    pts = []
    for r in sales:
        price, yr = sale_price(r), r.get("year")
        if not price or not yr:
            continue
        if l_miles and r.get("mileage"):
            price = price * _mileage_factor(int(l_miles), int(r["mileage"]))
        pts.append((int(yr), math.log(price)))
    if len(pts) < YEAR_RATE_MIN_SALES or len({y for y, _ in pts}) < YEAR_RATE_MIN_YEARS:
        return DEFAULT_YEAR_RATE, "default"
    my = statistics.mean(y for y, _ in pts)
    mp = statistics.mean(v for _, v in pts)
    var = sum((y - my) ** 2 for y, _ in pts)
    slope = sum((y - my) * (v - mp) for y, v in pts) / var if var else 0.0
    rate = max(YEAR_RATE_BOUNDS[0], min(YEAR_RATE_BOUNDS[1], math.exp(slope) - 1))
    return rate, f"learned from {len(pts)} sales"


def _year_factor(listing_year: int, comp_year: int, rate: float) -> float:
    return max(YEAR_FACTOR_BOUNDS[0], min(YEAR_FACTOR_BOUNDS[1], (1 + rate) ** (listing_year - comp_year)))


def _adjusted_prices(listing: dict[str, Any], rows: list[dict[str, Any]], realized: bool = False,
                     year_rate_: float | None = None) -> list[float]:
    l_miles = listing.get("mileage")
    l_year = listing.get("year")
    out = []
    for r in rows:
        price = sale_price(r)
        if not price:
            continue
        if realized:
            price = price * realized_factor(r)
        if year_rate_ is not None and l_year and r.get("year"):
            price = price * _year_factor(int(l_year), int(r["year"]), year_rate_)
        c_miles = r.get("mileage")
        if l_miles and c_miles:
            out.append(price * _mileage_factor(int(l_miles), int(c_miles)))
        else:
            out.append(float(price))
    return out


def _pct(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * pct
    f, c = math.floor(k), math.ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def _segment(rows: list[dict[str, Any]], listing: dict[str, Any], years: int | None,
            need_body: bool, need_trans: bool) -> list[dict[str, Any]]:
    out = rows
    l_year = listing.get("year")
    if years is not None and l_year:
        out = [r for r in out if r.get("year") and abs(int(r["year"]) - int(l_year)) <= years]
    if need_body:
        lb = body_class(listing)
        if lb:
            out = [r for r in out if body_class(r) == lb]
    if need_trans:
        lt = (listing.get("transmission") or "").strip().lower()
        if lt and lt != "unknown":
            # 1.9.0: a comp whose transmission was never read is not a mismatch.
            out = [r for r in out if (r.get("transmission") or "").strip().lower() in {lt, "", "unknown"}]
    return out


_RELAX_STEPS = [
    (3, True, True, []),
    (6, True, True, ["widened years to ±6"]),
    (6, True, False, ["widened years to ±6", "dropped transmission"]),
    (6, False, False, ["widened years to ±6", "dropped transmission", "dropped body class"]),
    (None, False, False, ["widened years to ±6", "dropped transmission", "dropped body class", "dropped year filter"]),
]


def _relax(rows: list[dict[str, Any]], listing: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str], tuple]:
    """Start with year(±3)+body+transmission, then relax: widen years to ±6,
    then drop transmission, then drop body, then drop years entirely -- until
    the pool has >=4 rows. Returns (pool, relaxed_labels, level_used)."""
    l_body = body_class(listing)
    l_trans = (listing.get("transmission") or "").strip().lower()
    result = None
    for years, want_body, want_trans, relaxed in _RELAX_STEPS:
        need_body, need_trans = want_body and bool(l_body), want_trans and bool(l_trans)
        pool = _segment(rows, listing, years, need_body, need_trans)
        level = (years, need_body, need_trans)
        if result is None or len(pool) > len(result[0]):
            result = (pool, relaxed, level)
        if len(pool) >= 4:
            return pool, relaxed, level
    return result


def _recency_filter(rows: list[dict[str, Any]], now: date) -> list[dict[str, Any]]:
    cutoff = now - timedelta(days=365 * 3)
    recent = []
    for r in rows:
        # When the car sold or was listed. Never last_seen: every sync refreshes it,
        # which made a years-old comp look recent.
        d = r.get("auction_end") or r.get("listing_date") or ""
        d = str(d)[:10] if d else ""
        try:
            dt = date.fromisoformat(d) if d else None
        except ValueError:
            dt = None
        if dt is None or dt >= cutoff:
            recent.append(r)
    return recent if len(recent) >= 4 else rows


def fair_value(listing: dict[str, Any], rows: list[dict[str, Any]], now: date | None = None) -> dict[str, Any] | None:
    now = now or date.today()
    lid = listing.get("id")
    lvid = listing.get("vehicle_id")
    # An ignored listing (not a car, or not a fair comparison, e.g. modified) prices nothing.
    others = [r for r in rows if r.get("id") != lid and r.get("role") != "ignored"
              and not (lvid and r.get("vehicle_id") and r.get("vehicle_id") == lvid)]

    sold_all = [r for r in others if is_sale(r)]
    is_failed = lambda r: (r.get("availability") == "ended" and r.get("price_kind") == "reserve_not_met" and r.get("price"))
    failed_all = [r for r in others if is_failed(r)]
    failed_high_bid_max = max((r["price"] for r in failed_all), default=None)

    def build(pool_rows: list[dict[str, Any]], basis: str, discount: float) -> dict[str, Any] | None:
        nonlocal failed_high_bid_max
        if len(pool_rows) < 2:
            return None
        segmented, relaxed, level = _relax(pool_rows, listing)
        if failed_all:
            years, need_body, need_trans = level
            seg_failed = _segment(failed_all, listing, years, need_body, need_trans)
            if seg_failed:
                failed_high_bid_max = max(r["price"] for r in seg_failed)
        recent = _recency_filter(segmented, now)
        rate, rate_src = year_rate(listing, sold_all)
        adj = sorted(p * discount for p in _adjusted_prices(listing, recent, realized=basis == "sold", year_rate_=rate))
        if not adj:
            return None
        n = len(adj)
        mid = statistics.median(adj)
        if n < 4:
            low, high = adj[0], adj[-1]
        else:
            low, high = _pct(adj, 0.25), _pct(adj, 0.75)
        bc = body_class(listing)
        years = sorted(r["year"] for r in segmented if r.get("year"))
        yr_span = f"{years[0]}–{years[-1]}" if years else "unknown years"
        klass = f"{bc}-class" if bc else "any body"
        yr_note = (f"year-adjusted {rate:+.1%}/yr ({rate_src})"
                   if listing.get("year") and any(r.get("year") and r["year"] != listing["year"] for r in recent) else None)
        note_bits = [f"{n} {'sales' if basis == 'sold' else 'asking prices'}", klass, yr_span, "mileage-adjusted"] + ([yr_note] if yr_note else [])
        return {
            "mid": int(round(mid)), "low": int(round(low)), "high": int(round(high)), "n": n,
            "basis": basis, "relaxed": relaxed, "note": ", ".join(note_bits), "year_rate": round(rate, 4),
        }

    result = build(sold_all, "sold", 1.0)
    if result is None:
        asking = [r for r in others if r.get("price_kind") == "asking" and r.get("price") and r.get("price") > 0
                 and r.get("availability") in ("active", "pending")]
        result = build(asking, "asking", 0.93)
    if result is not None:
        result["failed_high_bid_max"] = failed_high_bid_max
    return result
