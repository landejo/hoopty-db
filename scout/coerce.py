"""Validation bounds for anything that comes back from a model. Never persist raw."""
from __future__ import annotations

import json
import re
from typing import Any

from scout.config import AXES

YEAR_RANGE = (1950, 2030)
MILEAGE_RANGE = (0, 500_000)
PRICE_RANGE = (0, 2_000_000)
VIN_RE = re.compile(r"[A-HJ-NPR-Z0-9]{17}")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return {}
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return {}
    return data if isinstance(data, dict) else {}


def to_int(v: Any, lo: int, hi: int) -> int | None:
    if v in (None, ""):
        return None
    try:
        iv = int(float(str(v).replace(",", "").replace("$", "").strip()))
    except (TypeError, ValueError):
        return None
    return iv if lo <= iv <= hi else None


def to_float(v: Any, lo: float, hi: float) -> float | None:
    if v in (None, ""):
        return None
    try:
        fv = float(str(v).lower().replace("l", "").strip())
    except (TypeError, ValueError):
        return None
    return fv if lo <= fv <= hi else None


def str_list(v: Any, cap: int = 20, maxlen: int = 400) -> list[str]:
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, list):
        return []
    out = [str(x).strip()[:maxlen] for x in v if str(x).strip()]
    return out[:cap]


def vin(v: Any) -> str | None:
    if v in (None, ""):
        return None
    s = re.sub(r"[^A-Za-z0-9]", "", str(v)).upper()
    return s if VIN_RE.fullmatch(s) else None


def transmission(v: Any) -> str | None:
    if v in (None, ""):
        return None
    t = str(v).strip().lower()
    if "manual" in t or t in {"mt", "stick", "6mt", "5mt"}:
        return "Manual"
    if "auto" in t or t in {"at", "cvt", "dct", "pdk"} or "tiptronic" in t:
        return "Automatic"
    return "Unknown"


def scores(v: Any) -> dict[str, int]:
    if not isinstance(v, dict):
        return {}
    out = {}
    for k, val in v.items():
        if k not in AXES:
            continue
        iv = to_int(val, 1, 5)
        if iv is not None:
            out[k] = iv
    return out


def weights(v: Any) -> dict[str, float]:
    """Weights over the fixed axis vocabulary, renormalized to sum to 1."""
    if not isinstance(v, dict):
        return {}
    raw = {}
    for k, val in v.items():
        if k not in AXES:
            continue
        try:
            f = float(val)
        except (TypeError, ValueError):
            continue
        if f > 0:
            raw[k] = f
    total = sum(raw.values())
    if not total:
        return {}
    return {k: round(f / total, 4) for k, f in raw.items()}


def checks(v: Any, valid_keys: set[str] | None) -> list[dict[str, str]]:
    if not isinstance(v, list):
        return []
    out = []
    for c in v:
        if not isinstance(c, dict):
            continue
        k = str(c.get("key", "")).strip()
        s = str(c.get("status", "")).strip().lower()
        n = str(c.get("notes", "")).strip()[:500]
        if not k or s not in {"pass", "concern", "fail", "unknown", "n/a"}:
            continue
        if valid_keys and k not in valid_keys:
            continue
        out.append({"key": k, "status": s, "notes": n})
    return out


def normalized_listing(data: dict[str, Any]) -> dict[str, Any]:
    """Coerce the Haiku normalization payload."""
    out: dict[str, Any] = {}
    out["year"] = to_int(data.get("year"), *YEAR_RANGE)
    out["mileage"] = to_int(data.get("mileage"), *MILEAGE_RANGE)
    out["price"] = to_int(data.get("price"), *PRICE_RANGE)
    for k in ("make", "model", "generation", "trim", "engine", "location",
              "seller_type", "seller_name", "title_status", "exterior_color",
              "interior_color", "drivetrain", "body_style"):
        val = data.get(k)
        out[k] = str(val).strip()[:120] if val not in (None, "") else None
    out["engine_liters"] = to_float(data.get("engine_liters"), 0.5, 10.0)
    out["transmission"] = transmission(data.get("transmission"))
    out["vin"] = vin(data.get("vin"))
    d = str(data.get("listing_date") or "").strip()
    out["listing_date"] = d if DATE_RE.match(d) else None
    av = str(data.get("availability") or "").strip().lower()
    out["availability"] = av if av in {"active", "sold", "ended", "removed"} else None
    pk = str(data.get("price_kind") or "").strip().lower()
    out["price_kind"] = pk if pk in {"asking", "current_bid", "sold", "reserve_not_met", "no_reserve"} else None
    out["sold_price"] = to_int(data.get("sold_price"), *PRICE_RANGE)
    out["options"] = str_list(data.get("options"), cap=30)
    out["red_flags"] = str_list(data.get("red_flags"), cap=12)
    out["highlights"] = str_list(data.get("highlights"), cap=12)
    out["prelim_scores"] = scores(data.get("scores"))
    out["prelim_summary"] = str(data.get("summary") or "").strip()[:1200] or None
    out["profile_key"] = str(data.get("profile_key") or "").strip()[:80] or None
    out["profile_confidence"] = to_int(data.get("profile_confidence"), 1, 5)
    ap = data.get("accidents")
    out["accidents"] = ap if ap in {"yes", "no", "unknown"} else None
    out["num_owners"] = to_int(data.get("num_owners"), 1, 20)
    return {k: v for k, v in out.items() if v not in (None, [], {})}


def analysis(data: dict[str, Any], valid_check_keys: set[str] | None) -> dict[str, Any]:
    """Coerce the Opus deep-analysis payload."""
    out: dict[str, Any] = {}
    out["verdict"] = str(data.get("verdict") or "").strip()[:40] or "Undecided"
    out["deal_score"] = to_int(data.get("deal_score"), 0, 100)
    out["confidence"] = to_int(data.get("confidence"), 1, 5)
    out["summary"] = str(data.get("summary") or "").strip()[:4000]
    out["market_position"] = str(data.get("market_position") or "").strip()[:2000]
    out["scores"] = scores(data.get("scores"))
    out["positives"] = str_list(data.get("positives"))
    out["concerns"] = str_list(data.get("concerns"))
    out["dealbreakers"] = str_list(data.get("dealbreakers"), cap=6)
    out["seller_questions"] = str_list(data.get("seller_questions"), cap=15)
    out["inspection_focus"] = str_list(data.get("inspection_focus"), cap=15)
    out["checks"] = checks(data.get("checks"), valid_check_keys)
    pricing = data.get("pricing") if isinstance(data.get("pricing"), dict) else {}
    out["pricing"] = {
        k: to_int(pricing.get(k), 0, 2_000_000)
        for k in ("fair_value", "target_offer", "walk_away", "immediate_repairs", "twelve_month_repairs")
        if to_int(pricing.get(k), 0, 2_000_000) is not None
    }
    out["negotiation"] = str_list(data.get("negotiation"), cap=8, maxlen=600)
    out["verdict_reasoning"] = str(data.get("verdict_reasoning") or "").strip()[:2000]
    return out


def profile(data: dict[str, Any]) -> dict[str, Any] | None:
    """Coerce an AI-generated profile."""
    key = re.sub(r"[^a-z0-9_]", "", str(data.get("key") or "").strip().lower().replace("-", "_").replace(" ", "_"))
    label = str(data.get("label") or "").strip()[:120]
    if not key or not label:
        return None
    w = weights(data.get("weights"))
    if len(w) < 3:
        return None
    raw_checks = data.get("checks") if isinstance(data.get("checks"), list) else []
    chk = []
    seen = set()
    for c in raw_checks:
        if not isinstance(c, dict):
            continue
        ck = re.sub(r"[^a-z0-9_]", "", str(c.get("key") or "").lower().replace(" ", "_"))
        lbl = str(c.get("label") or "").strip()[:200]
        if ck and lbl and ck not in seen:
            seen.add(ck)
            chk.append({"key": ck, "label": lbl})
    years = data.get("years") if isinstance(data.get("years"), list) else []
    yrs = [y for y in (to_int(x, *YEAR_RANGE) for x in years[:2]) if y is not None]
    return {
        "key": key,
        "label": label,
        "make": str(data.get("make") or "").strip()[:60],
        "models": str_list(data.get("models"), cap=12, maxlen=60),
        "years": yrs if len(yrs) == 2 else [],
        "framing": str(data.get("framing") or "").strip()[:2000],
        "weak_points": str(data.get("weak_points") or "").strip()[:4000],
        "immediate_repairs": str(data.get("immediate_repairs") or "").strip()[:800],
        "repairs_12mo": str(data.get("repairs_12mo") or "").strip()[:800],
        "market_notes": str(data.get("market_notes") or "").strip()[:2000],
        "weights": w,
        "checks": chk[:25],
        "dealbreaker_rules": str_list(data.get("dealbreaker_rules"), cap=8, maxlen=300),
    }
