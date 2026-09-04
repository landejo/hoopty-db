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
             ", ks", ", ne", ", mo", ", ar", ", la", ", ga", ", fl", ", nc", ", sc", ", tn"]),
        (1, ["new york", ", ny", "new jersey", ", nj", "massachusetts", ", ma", "connecticut", ", ct",
             "vermont", ", vt", "maine", ", me", "pennsylvania", ", pa", "wisconsin", ", wi",
             "indiana", ", in", "new hampshire", ", nh", "rhode island", ", ri", ", md", ", de",
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

    sold = [r for r in comps if r.get("sold_price") or (r.get("availability") == "sold" and r.get("price"))]
    sold_prices = sorted((r.get("sold_price") or r.get("price")) for r in sold)
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
