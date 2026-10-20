"""Fit score (0-100): how good a candidate the car looks on what is knowable now.

Jason, 2026-10-18: the assessment score bunches up (41-64 across the board, sd ~5)
because at the listing stage nearly every car is "typical, unproven", so it mostly
measures how far the evidence has got. Fit ranks the cars instead, from parts that
differ between listings and are each scored across their full range. It sits beside
the assessment score and changes no verdict or gate. Deterministic: computed from
the stored assessment and the listing's current price, no AI call.
"""
from __future__ import annotations

import re
from typing import Any

from scout.policy.preferences import MANUAL_REQUIRED_MISSIONS
from scout.scoring import locality_hint

FIT_POINTS = {"value": 25, "history": 20, "records": 10, "mileage": 15, "spec": 15, "condition": 7, "distance": 8}
assert sum(FIT_POINTS.values()) == 100

# Miles that score full / zero for the mileage part, by mission (a GX lives longer).
MILEAGE_SCALE = {"utility_capability": (60_000, 220_000)}
MILEAGE_SCALE_DEFAULT = (25_000, 150_000)


def _lerp(x: float, x0: float, x1: float, y0: float, y1: float) -> float:
    if x1 == x0:
        return y1
    t = max(0.0, min(1.0, (x - x0) / (x1 - x0)))
    return y0 + t * (y1 - y0)


def _fact(assessment: dict[str, Any], key: str) -> tuple[str, str]:
    for f in (assessment.get("evidence") or {}).get("facts") or []:
        if f.get("key") == key:
            return (f.get("status") or "unknown"), str(f.get("value") or "")
    return "unknown", ""


def value_points(listing: dict[str, Any], assessment: dict[str, Any]) -> tuple[float, str]:
    """Price against the car's walk-away: 15% under or better = full, at the
    walk-away = 60%, 30% over or worse = none. A budget-only walk-away (too few
    sales to value the car) is held to the middle: the price is not proven good."""
    pts = FIT_POINTS["value"]
    c = assessment.get("costs") or {}
    walk = c.get("max_price")
    price = listing.get("price") if c.get("price_basis") == "asking" and listing.get("price") else c.get("price")
    if not walk or not price:
        return pts * 0.5, "no walk-away or price yet"
    margin = (walk - price) / walk          # + = under the walk-away
    if margin >= 0:
        v = _lerp(margin, 0, 0.15, 0.6, 1.0)
    else:
        v = _lerp(-margin, 0, 0.30, 0.6, 0.0)
    note = f"{abs(margin):.0%} {'under' if margin >= 0 else 'over'} the walk-away"
    if c.get("max_price_basis") == "budget":
        v = min(v, 0.6)
        note += " (budget limit: too few sales to value it)"
    return pts * v, note


_OWNERS_RE = re.compile(r"^\D{0,12}?(\d+)")
# Read from the assessment's accident fact, e.g. "No accident or damage reported to CARFAX",
# "0 accidents reported", "Carfax reports 'minor damage' to the front", "Damage reported
# 2020; no structural damage or airbag deployment reported" (that last one is not serious).
_CLEAN_RE = re.compile(r"\bno (reported )?(accidents?|damage)\b|\b0 (reported )?accidents?\b|none reported|accident[- ]free|clean (carfax|autocheck)")
_SELLER_NONE_RE = re.compile(r"no (significant|major) damage|no significant damage or problems")
_SERIOUS_RE = re.compile(r"structural|frame damage|airbag|total[- ]loss|salvage|rebuilt")


def _negated(before: str) -> bool:
    """A "no" shortly before, or one that heads a list running up to it: "No accident,
    damage, structural damage or airbag deployment reported" negates "airbag" (#515)."""
    m = re.search(r"\bno\b([^.;]{0,80})$", before)
    if not m:
        return False
    gap = m.group(1)
    if len(gap) <= 30:
        return True
    return bool(re.search(r",|\bor\b", gap)) and not re.search(r"\b(until|then|but|except|however|before|after|when|later)\b", gap)


def accident_points(status: str, value: str) -> tuple[int, str]:
    """0-8: none reported (history report) 8, the seller's word only 6, unknown 4,
    minor 3, an accident 2, serious (structural, airbag, total loss) 0."""
    t = (value or "").lower()
    if status == "unknown" or not t or t in {"unknown", "none claimed", "not stated"}:
        return 4, "accident record unknown"
    serious = [m for m in _SERIOUS_RE.finditer(t) if not _negated(t[:m.start()])]
    if serious:
        return 0, "serious accident on record"
    if _CLEAN_RE.search(t) and not re.search(r"\b(minor|moderate)[- ](damage|collision|accident)|damage reported \d|accidents? reported on|reports '", t):
        return 8, "no accidents reported"
    if re.search(r"no major (accidents?|damage)", t):
        return 4, "seller hedges ('no major accidents')"
    if _SELLER_NONE_RE.search(t):
        return 6, "seller says no damage"
    if "minor" in t:
        return 3, "minor accident on record"
    return 2, "accident on record"


def history_points(listing: dict[str, Any], assessment: dict[str, Any]) -> tuple[float, str]:
    """Owners (8), accident record (8), title (4)."""
    notes = []
    st, v = _fact(assessment, "owners")
    m = _OWNERS_RE.match(v) if st != "unknown" else None
    n = int(m.group(1)) if m else listing.get("num_owners")
    owners = {1: 8, 2: 7, 3: 3}.get(n, 0 if n else 4)
    notes.append(f"{n} owner{'s' if n != 1 else ''}" if n else "owners unknown")

    st, v = _fact(assessment, "accident_history")
    acc, an = accident_points(st, v or listing.get("accidents") or "")
    notes.append(an)

    st, v = _fact(assessment, "title_status")
    t = (v or listing.get("title_status") or "").lower()
    title = 0 if re.search(r"salvage|rebuilt|branded|lemon|flood", t) else 4 if "clean" in t else 2
    return owners + acc + title, ", ".join(notes)


def mileage_points(listing: dict[str, Any], mission: str | None) -> tuple[float, str]:
    miles = listing.get("mileage")
    pts = FIT_POINTS["mileage"]
    if not miles:
        return pts * 0.5, "mileage unknown"
    lo, hi = MILEAGE_SCALE.get(mission or "", MILEAGE_SCALE_DEFAULT)
    return pts * _lerp(miles, lo, hi, 1.0, 0.0), f"{int(miles):,} mi"


def spec_points(listing: dict[str, Any], assessment: dict[str, Any], mission: str | None) -> tuple[float, str]:
    """The assessment's spec/enthusiasm rating (10) + the manual where the mission wants one (5)."""
    rating = (((assessment.get("evidence") or {}).get("ratings") or {}).get("emotional_spec_fit") or {}).get("rating")
    spec = 10 * (rating / 10) if rating is not None else 5
    trans = (listing.get("transmission") or _fact(assessment, "transmission")[1] or "").lower()
    if (mission or "") in MANUAL_REQUIRED_MISSIONS:
        gear = 5 if "manual" in trans else 0 if trans else 2.5
    else:
        gear = 5
    return spec + gear, ("manual" if "manual" in trans else trans or "gearbox unknown")


def _rating(assessment: dict[str, Any], key: str) -> int | None:
    return (((assessment.get("evidence") or {}).get("ratings") or {}).get(key) or {}).get("rating")


def records_points(assessment: dict[str, Any]) -> tuple[float, str]:
    """The assessment's documentation rating, stretched the same way: most listings
    rate 3-5 (a history-report summary plus claims), 8 = window sticker, dated
    service history and the model's critical items (only 8 of 186 cars so far)."""
    rating = _rating(assessment, "documentation")
    if rating is None:
        return FIT_POINTS["records"] * 0.4, "records not rated"
    return FIT_POINTS["records"] * _lerp(rating, 2, 8, 0.0, 1.0), f"records {rating}/10"


def condition_points(assessment: dict[str, Any]) -> tuple[float, str]:
    """The assessment's condition rating, stretched: listing-stage ratings land 3-7,
    so 3 = none and 8+ = full (a 5 'typical, unproven' car gets 40%)."""
    rating = (((assessment.get("evidence") or {}).get("ratings") or {}).get("condition") or {}).get("rating")
    if rating is None:
        return FIT_POINTS["condition"] * 0.4, "condition not rated"
    return FIT_POINTS["condition"] * _lerp(rating, 3, 8, 0.0, 1.0), f"condition {rating}/10"


def distance_points(listing: dict[str, Any]) -> tuple[float, str]:
    band = locality_hint(listing.get("location"))
    v = {5: 1.0, 4: 0.85, 3: 0.6, 2: 0.4, 1: 0.25}.get(band, 0.5)
    return FIT_POINTS["distance"] * v, {5: "local", 4: "in California", 3: "West", 2: "far", 1: "Northeast"}.get(band, "location unknown")


def fit_score(listing: dict[str, Any], assessment: dict[str, Any] | None) -> dict[str, Any] | None:
    """{"total": 0-100, "parts": {name: points}, "notes": {name: why}} or None without an assessment."""
    if not assessment:
        return None
    mission = listing.get("mission") or assessment.get("mission")
    parts = {
        "value": value_points(listing, assessment),
        "history": history_points(listing, assessment),
        "mileage": mileage_points(listing, mission),
        "spec": spec_points(listing, assessment, mission),
        "records": records_points(assessment),
        "condition": condition_points(assessment),
        "distance": distance_points(listing),
    }
    return {"total": int(round(sum(p for p, _ in parts.values()))),
            "parts": {k: round(p, 1) for k, (p, _) in parts.items()},
            "max": FIT_POINTS,
            "notes": {k: n for k, (_, n) in parts.items()}}
