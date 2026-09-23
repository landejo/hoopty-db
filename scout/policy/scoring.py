"""100-point score, caps, confidence, verdict (guide §9). Pure arithmetic."""
from __future__ import annotations

from typing import Any

from scout.policy.preferences import (
    CATEGORY_POINTS, CONFIDENCE_PROVISIONAL, DOC_CAP_CONDITIONAL_MISSING, DOC_CAP_HARD_MISSING,
    LOGISTICS_CAP_BY_BAND, RELIST_MARKUP_FLAG, RELIST_PRICE_VALUE_CAP, SCORE_BANDS, VERDICT_RANK,
)
from scout.policy.schema import CostBreakdown, EvidenceInterpretation, Gate, Score
from scout.scoring import locality_hint

DECISION_FACTS = {"title_status", "accident_history", "records_available", "mileage", "vin", "owners"}


def compute_score(evidence: EvidenceInterpretation, gates: list[Gate], listing: dict[str, Any],
                  mission: str, state: dict[str, Any], vin_history: dict[str, Any], costs: CostBreakdown | None = None) -> Score:
    r = evidence.ratings
    pts = {k: round(CATEGORY_POINTS[k] * getattr(r, k).rating / 10) for k in CATEGORY_POINTS}
    caps: list[str] = []

    # Documentation caps when model-critical evidence is not satisfied.
    keys = [g.key for g in gates]
    if any(k.startswith("critical_") and g.kind == "hard" for k, g in zip(keys, gates)):
        if pts["documentation"] > DOC_CAP_HARD_MISSING:
            pts["documentation"] = DOC_CAP_HARD_MISSING
            caps.append(f"documentation capped at {DOC_CAP_HARD_MISSING}: model-critical evidence missing")
    elif any(k.startswith("critical_") for k in keys):
        if pts["documentation"] > DOC_CAP_CONDITIONAL_MISSING:
            pts["documentation"] = DOC_CAP_CONDITIONAL_MISSING
            caps.append(f"documentation capped at {DOC_CAP_CONDITIONAL_MISSING}: model-specific evidence unresolved")

    # Logistics never exceeds what the location allows.
    band = locality_hint(listing.get("location"))
    lcap = LOGISTICS_CAP_BY_BAND.get(band, LOGISTICS_CAP_BY_BAND[None])
    if pts["logistics"] > lcap:
        pts["logistics"] = lcap
        caps.append(f"logistics capped at {lcap}: location band {band or 'unknown'}")

    # Mission fit: price over the bridge budget cannot score as a good fit.
    budget = state.get("budget") or {}
    if costs is not None and costs.price_basis == "unpriced":
        # Auction price unknown until closing; cap mission fit at 9
        if pts["mission_fit"] > 9:
            pts["mission_fit"] = 9
            caps.append("mission fit capped at 9: auction price unknown until closing")
    elif mission in {"enthusiast_bridge", "pragmatic_bridge"}:
        # Use costs.price if available, otherwise fall back to listing price
        price = costs.price if costs is not None else (listing.get("price") or 0)
        max_price = budget.get("max_price")
        if max_price and price > max_price:
            # Compare all-in midpoint to defeats_purpose_all_in for the harsher cap
            if costs is not None:
                all_in_mid = (costs.all_in_low + costs.all_in_high) // 2
                defeats_purpose = budget.get("defeats_purpose_all_in", 10**9)
                cap = 6 if all_in_mid > defeats_purpose else 9
            else:
                cap = 6 if price > budget.get("defeats_purpose_all_in", 10**9) else 9
            if pts["mission_fit"] > cap:
                pts["mission_fit"] = cap
                caps.append(f"mission fit capped at {cap}: price above the bridge budget")
    if mission == "pragmatic_bridge" and pts["mission_fit"] > 11:
        pts["mission_fit"] = 11
        caps.append("mission fit capped at 11: pragmatic bridge solves the immediate problem, not the enthusiast brief")

    # Relist markup without a documented transformation.
    markup = vin_history.get("markup_vs_last_sale")
    if markup is not None and markup >= RELIST_MARKUP_FLAG and evidence.flags.transformation_documented_since_last_sale != "yes":
        if pts["price_value"] > RELIST_PRICE_VALUE_CAP:
            pts["price_value"] = RELIST_PRICE_VALUE_CAP
            caps.append(f"price/value capped at {RELIST_PRICE_VALUE_CAP}: relisted {markup:.0%} above the last sale with no documented transformation")

    total = sum(pts.values())
    return Score(**pts, total=total, caps_applied=caps)


def compute_confidence(evidence: EvidenceInterpretation, gates: list[Gate], listing: dict[str, Any]) -> int:
    """Confidence in the assessment, not the car (§9)."""
    c = 30 + evidence.evidence_quality * 6          # 30..90 from verifiable evidence

    # Unknown-fact penalty only for decision-relevant keys
    decision_unknowns = sum(1 for f in evidence.facts if f.status == "unknown" and f.key in DECISION_FACTS)
    c -= min(12, 3 * decision_unknowns)

    # Critical missing gates: penalty for each
    critical_missing = sum(1 for g in gates if g.key.startswith("critical_missing"))
    c -= min(12, 4 * critical_missing)

    # Contradictions: penalty per contradiction
    c -= min(9, 3 * len(evidence.contradictions))

    # No photos penalty
    if not (listing.get("photos") or []):
        c -= 5

    # Short raw text penalty
    if len(listing.get("raw_text") or "") < 400:
        c -= 10

    return max(5, min(100, int(c)))


def verdict_from(score: Score, confidence: int, gates: list[Gate]) -> tuple[str, str]:
    kinds = {g.kind for g in gates}
    if "strategy" in kinds:
        g = next(g for g in gates if g.kind == "strategy")
        return "Do not pursue", g.reason
    if "hard" in kinds:
        g = next(g for g in gates if g.kind == "hard")
        return "Reject", g.reason
    if "configuration" in kinds:
        g = next(g for g in gates if g.kind == "configuration")
        return "Reject", g.reason
    verdict = next(v for floor, v in SCORE_BANDS if score.total >= floor)
    reason = f"Score {score.total}/100"
    conds = [g for g in gates if g.kind == "conditional"]
    if conds and VERDICT_RANK[verdict] < VERDICT_RANK["Maybe / verify"]:
        verdict = "Maybe / verify"
        reason += "; capped until resolved: " + "; ".join(g.reason for g in conds)
    if confidence < CONFIDENCE_PROVISIONAL and VERDICT_RANK[verdict] < VERDICT_RANK["Maybe / verify"]:
        verdict = "Maybe / verify"
        reason += f"; capped: assessment confidence {confidence} below {CONFIDENCE_PROVISIONAL}"
    return verdict, reason
