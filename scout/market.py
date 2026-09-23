"""Deterministic market value from comps: sold-price segmentation, mileage
adjustment, and asking-price fallback. No AI; pure functions over listing rows."""
from __future__ import annotations

import math
import statistics
from datetime import date, timedelta
from typing import Any

_ENDED_SALE_KINDS = {"sold", "asking"}   # price_kind values that still count as a sale when availability=ended

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


def _adjusted_prices(listing: dict[str, Any], rows: list[dict[str, Any]]) -> list[float]:
    l_miles = listing.get("mileage")
    out = []
    for r in rows:
        price = sale_price(r)
        if not price:
            continue
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
        if lt:
            out = [r for r in out if (r.get("transmission") or "").strip().lower() == lt]
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
        d = r.get("sold_at") or r.get("listing_date") or (r.get("last_seen") or "")
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
    others = [r for r in rows if r.get("id") != lid and not (lvid and r.get("vehicle_id") and r.get("vehicle_id") == lvid)]

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
        adj = sorted(p * discount for p in _adjusted_prices(listing, recent))
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
        note_bits = [f"{n} {'sales' if basis == 'sold' else 'asking prices'}", klass, yr_span, "mileage-adjusted"]
        return {
            "mid": int(round(mid)), "low": int(round(low)), "high": int(round(high)), "n": n,
            "basis": basis, "relaxed": relaxed, "note": ", ".join(note_bits),
        }

    result = build(sold_all, "sold", 1.0)
    if result is None:
        asking = [r for r in others if r.get("price_kind") == "asking" and r.get("price") and r.get("price") > 0
                 and r.get("availability") in ("active", "pending")]
        result = build(asking, "asking", 0.93)
    if result is not None:
        result["failed_high_bid_max"] = failed_high_bid_max
    return result
