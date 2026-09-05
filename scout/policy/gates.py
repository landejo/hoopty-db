"""Gates (guide §8). Deterministic; evaluated before any arithmetic.
Hard gates override the score. Conditional gates cap the verdict at
Maybe / verify. Configuration and strategy gates come from missions and
explicit exclusions."""
from __future__ import annotations

from typing import Any

from scout.policy.preferences import EXCLUDED_MODELS, MANUAL_REQUIRED_MISSIONS
from scout.policy.schema import EvidenceInterpretation, Gate
from scout.scoring import is_early_bid, listing_age_days

HARD_FLAGS = {
    "seller_refuses_vin_or_ppi": "Seller refuses VIN or a reasonable independent inspection",
    "identity_or_odometer_fraud": "Unresolved title fraud, odometer inconsistency, or identity mismatch",
    "active_overheating_or_coolant_loss": "Active overheating or unexplained coolant loss",
    "serious_brake_or_oil_pressure_issue": "Serious brake or oil-pressure concern",
    "unsafe_structure_or_heavy_rust": "Heavy structural rust or collision repair of unknown quality",
    "emissions_or_registration_infeasible": "California registration or emissions appears infeasible",
}
CONDITIONAL_FLAGS = {
    "salvage_or_rebuilt_title": "Salvage / rebuilt title",
    "accident_without_repair_docs": "Accident history without repair records and measurements",
    "permanent_warning_lights": "Permanent warning lights",
    "modified_powertrain_undocumented": "Non-stock powertrain or software without records and smog proof",
    "remote_auction_no_ppi": "Remote auction with no opportunity for a PPI",
    "major_service_claimed_undocumented": "Major service claimed but not documented",
}


def is_excluded(make: str | None, model: str | None, active_exclusions: list[str]) -> str | None:
    name = f"{make or ''} {model or ''}".lower().replace("-", "").replace(" ", "")
    for ex in EXCLUDED_MODELS + [e for e in active_exclusions]:
        if ex.lower().replace("-", "").replace(" ", "") in name:
            return ex
    return None


def evaluate_gates(listing: dict[str, Any], profile: dict[str, Any], evidence: EvidenceInterpretation,
                   mission: str, state: dict[str, Any], all_in_high: int | None = None,
                   provenance: dict[str, Any] | None = None) -> list[Gate]:
    gates: list[Gate] = []
    flags = evidence.flags

    # Provenance: a seller who says the car is not for sale ends the evaluation.
    cs = (provenance or {}).get("current_status") or {}
    if provenance and cs.get("available") is False:
        gates.append(Gate(kind="strategy", key="not_actively_available", reason=cs.get("note") or "Seller indicates the car is no longer for sale"))
    if "mileage_decreased" in ((provenance or {}).get("flags") or []):
        gates.append(Gate(kind="hard", key="odometer_inconsistency", reason="Reported mileage is lower than an earlier same-car listing (odometer inconsistency unresolved)"))

    ex = is_excluded(listing.get("make"), listing.get("model"), state.get("active_exclusions", []))
    if ex:
        gates.append(Gate(kind="strategy", key="explicit_exclusion", reason=f"Explicitly excluded model ({ex})"))

    for key, reason in HARD_FLAGS.items():
        if getattr(flags, key) == "yes":
            gates.append(Gate(kind="hard", key=key, reason=reason))
    for c in evidence.contradictions:
        if c.severity == "identity":
            gates.append(Gate(kind="hard", key="identity_contradiction", reason=f"{c.topic}: {c.detail}"))

    # Model-critical evidence from the profile (single source: profile YAML / generated profile).
    # Models sometimes rename the keys they were given; match on meaning before giving up.
    for req in profile.get("critical_evidence") or []:
        if not _applies(req, listing):
            continue
        ce = match_reported(req, evidence.critical_evidence)
        st = ce.status if ce else "missing"
        if st == "satisfied":
            continue
        label = req.get("label", req["key"])
        detail = f" ({ce.evidence})" if ce and ce.evidence else ""
        if st == "failed":
            gates.append(Gate(kind="hard", key=f"critical_failed:{req['key']}", reason=f"{label}: failed{detail}"))
        elif req.get("severity") == "hard":
            gates.append(Gate(kind="hard", key=f"critical_missing:{req['key']}",
                              reason=f"{label}: {'seller assurance only' if st == 'claimed_only' else 'missing'}{detail}"))
        else:
            gates.append(Gate(kind="conditional", key=f"critical_missing:{req['key']}",
                              reason=f"{label}: {'seller assurance only' if st == 'claimed_only' else 'missing'}{detail}"))

    for key, reason in CONDITIONAL_FLAGS.items():
        if getattr(flags, key) == "yes":
            if key == "salvage_or_rebuilt_title" and flags.salvage_fully_documented_with_specialist_ppi == "yes":
                continue
            gates.append(Gate(kind="conditional", key=key, reason=reason))

    # A listing that has sat for months is probably sold, withdrawn or mispriced:
    # availability must be confirmed with the seller before it counts.
    age = listing_age_days(listing)
    stale_after = int((state.get("listing_age") or {}).get("stale_after_days", 120))
    if age is not None and age > stale_after:
        gates.append(Gate(kind="conditional", key="stale_listing",
                          reason=f"Listed about {round(age / 30)} months ago; confirm it is still available and why it has not sold"))

    # Manual-transmission gate (§8): fun-car missions only; SUVs / utility never.
    trans = (listing.get("transmission") or "").lower()
    if mission in MANUAL_REQUIRED_MISSIONS and trans == "automatic" and not profile.get("automatic_ok"):
        gates.append(Gate(kind="configuration", key="automatic_in_manual_search",
                          reason="Automatic transmission in a manual-required search (wrong configuration)"))

    # Contradictions the model reported AND the VIN decode found show once.
    seen: set[tuple[str, str]] = set()
    gates = [g for g in gates if not ((g.kind, g.reason) in seen or seen.add((g.kind, g.reason)))]

    # Total expected cost defeats the bridge strategy (§8 hard gate).
    cap = (state.get("budget") or {}).get("defeats_purpose_all_in")
    if all_in_high is not None and cap and mission in {"enthusiast_bridge", "pragmatic_bridge"} and all_in_high > cap:
        gates.append(Gate(kind="hard", key="cost_defeats_bridge_purpose",
                          reason=f"Risk-adjusted all-in ${all_in_high:,} exceeds the bridge ceiling ${cap:,}"))
    return gates


_GENERIC = {"records", "record", "receipts", "receipt", "photos", "photo", "inspection", "inspected", "history", "evidence",
            "service", "documentation", "documented", "status", "condition", "check", "report", "proof", "with", "and", "the"}
_ALIASES = {"subframe": "rear_structure", "trunk": "rear_structure", "differential": "rear_structure", "diff": "rear_structure",
            "floor": "rear_structure", "welds": "rear_structure", "weld": "rear_structure",
            "cooling": "cooling_history", "coolant": "cooling_history", "radiator": "cooling_history", "water_pump": "cooling_history",
            "bearing": "s54_rod_bearings", "bearings": "s54_rod_bearings", "rod": "s54_rod_bearings",
            "borescope": "borescope", "bore": "borescope", "scoring": "borescope", "timing": "timing_belt_water_pump", "belt": "timing_belt_water_pump",
            "tune": "tune_status", "tuned": "tune_status", "ecu": "tune_status", "leak": "oil_leaks", "leaks": "oil_leaks", "tires": "matching_tires",
            "tire": "matching_tires", "rust": "rust_evaluation", "underbody": "rust_evaluation", "warning": "warning_lights", "lights": "warning_lights",
            "kdss": "kdss_status", "air_injection": "secondary_air_injection", "dme": "dme_overrev", "overrev": "dme_overrev", "over_rev": "dme_overrev"}


def _tokens(text: str) -> set[str]:
    import re
    return {t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if len(t) > 2 and t not in _GENERIC}


def match_reported(req: dict[str, Any], reported: list[Any]):
    """Find the reported critical-evidence item for a profile requirement: exact
    key first, then alias words, then token overlap with the key and label."""
    for c in reported:
        if c.key == req["key"]:
            return c
    want = _tokens(req["key"]) | _tokens(req.get("label", ""))
    best, best_score = None, 0
    for c in reported:
        ctoks = _tokens(c.key)
        score = len(ctoks & want)
        if any(_ALIASES.get(t) == req["key"] for t in ctoks) or any(_ALIASES.get(t) == req["key"] for t in _tokens(c.key.replace("_", " "))):
            score += 2
        if score > best_score:
            best, best_score = c, score
    return best if best_score >= 1 else None


def _applies(req: dict[str, Any], listing: dict[str, Any]) -> bool:
    """Optional applicability: e.g. the Cayman S borescope applies only to the 3.4 S."""
    cond = req.get("applies_when") or {}
    if not cond:
        return True
    year = listing.get("year")
    if "min_year" in cond and (year is None or int(year) < int(cond["min_year"])):
        return False
    if "max_year" in cond and (year is None or int(year) > int(cond["max_year"])):
        return False
    if "min_engine_liters" in cond:
        try:
            if float(listing.get("engine_liters") or 0) < float(cond["min_engine_liters"]):
                # Fall back to trim text when displacement is unknown.
                if not (listing.get("engine_liters") is None and cond.get("or_trim_contains")
                        and cond["or_trim_contains"].lower() in f"{listing.get('trim') or ''} {listing.get('model') or ''}".lower()):
                    return False
        except (TypeError, ValueError):
            return False
    if "trim_contains" in cond:
        if cond["trim_contains"].lower() not in f"{listing.get('trim') or ''} {listing.get('model') or ''}".lower():
            return False
    return True


def quick_gates(listing: dict[str, Any], profile: dict[str, Any] | None, mission: str, state: dict[str, Any]) -> list[str]:
    """Sync-time, no-AI flags shown on cards: exclusions, configuration, budget."""
    out = []
    ex = is_excluded(listing.get("make"), listing.get("model"), state.get("active_exclusions", []))
    if ex:
        out.append(f"excluded: {ex}")
    trans = (listing.get("transmission") or "").lower()
    if mission in MANUAL_REQUIRED_MISSIONS and trans == "automatic" and not (profile or {}).get("automatic_ok"):
        out.append("automatic (manual brief)")
    price = listing.get("price")
    mx = (state.get("budget") or {}).get("max_price")
    if price and mx and mission != "future_keeper" and price > mx:
        out.append(f"over ${mx:,} budget")
    age = listing_age_days(listing)
    if age is not None and age > int((state.get("listing_age") or {}).get("stale_after_days", 120)):
        out.append(f"stale: {round(age / 30)} mo")
    early, hrs = is_early_bid(listing, state)
    if early:
        out.append("early bid" + (f": {round(hrs / 24)}d left" if hrs is not None else ""))
    return out
