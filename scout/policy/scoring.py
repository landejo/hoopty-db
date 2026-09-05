"""100-point score, caps, confidence, verdict (guide §9). Pure arithmetic."""
from __future__ import annotations

from typing import Any

from scout.policy.preferences import (
    CATEGORY_POINTS, CONFIDENCE_PROVISIONAL, DOC_CAP_CONDITIONAL_MISSING, DOC_CAP_HARD_MISSING,
    LOGISTICS_CAP_BY_BAND, RELIST_MARKUP_FLAG, RELIST_PRICE_VALUE_CAP, SCORE_BANDS, VERDICT_RANK,
)
from scout.policy.schema import EvidenceInterpretation, Gate, Score
from scout.scoring import locality_hint


def compute_score(evidence: EvidenceInterpretation, gates: list[Gate], listing: dict[str, Any],
                  mission: str, state: dict[str, Any], vin_history: dict[str, Any]) -> Score:
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
    price = listing.get("price") or 0
    if mission in {"enthusiast_bridge", "pragmatic_bridge"} and budget.get("max_price") and price > budget["max_price"]:
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
    c = 25 + evidence.evidence_quality * 6          # 25..85 from verifiable evidence
    unknown_facts = sum(1 for f in evidence.facts if f.status == "unknown")
    c -= min(12, 2 * unknown_facts)
    c -= min(20, 5 * sum(1 for g in gates if g.key.startswith("critical_missing")))   # capped: profiles differ in item count
    c -= min(9, 3 * len(evidence.contradictions))
    if not (listing.get("photos") or []):
        c -= 5
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
