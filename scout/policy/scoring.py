"""100-point score, caps, confidence, verdict (guide §9). Pure arithmetic."""
from __future__ import annotations

from typing import Any

from scout.evidence import COND_GAIN_CAP, COND_GAIN_PER_ITEM, DOC_GAIN_CAP, GAIN_PER_ITEM, classify
from scout.policy.preferences import (
    CATEGORY_POINTS, CONFIDENCE_PROVISIONAL, REJECT_FLOOR_NOTHING_OBSERVED, DOC_CAP_CONDITIONAL_MISSING, DOC_CAP_HARD_MISSING,
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


# Conditional keys that are an open question (evidence not gathered *yet*) rather
# than an observed negative. Everything else conditional (critical_reservation:*,
# salvage_or_rebuilt_title, permanent_warning_lights, modified_powertrain_undocumented,
# remote_auction_no_ppi, ...) is an observed finding: it caps the verdict regardless of stage.
_OPEN_FLAG_LABELS = {
    "major_service_claimed_undocumented": "Major service claimed but not documented",
    "accident_without_repair_docs": "Accident history without repair records and measurements",
}
_OPEN_FLAG_KEYS = set(_OPEN_FLAG_LABELS) | {"stale_listing"}


REQUIRED_TAG = " (required before purchase)"


def defer_required(gates: list[Gate], stage: str) -> list[Gate]:
    """Policy 1.6.0: a HARD model-critical item that is merely missing (not failed)
    is an open question until the PPI stage - required before purchase, so it caps
    the verdict at Maybe / verify, but it is not yet a finding against the car.
    Still missing at the PPI stage, it stays hard (Reject)."""
    if stage == "ppi":
        return gates
    return [Gate(kind="conditional", key=g.key, reason=g.reason + REQUIRED_TAG)
            if g.kind == "hard" and g.key.startswith("critical_missing:") else g for g in gates]


def required_open(gates: list[Gate]) -> list[Gate]:
    return [g for g in gates if g.kind == "conditional" and g.reason.endswith(REQUIRED_TAG)]


def classify_conditionals(gates: list[Gate], stage: str) -> dict[str, list]:
    """Split conditional gates into resolvable open questions (document /
    inspection, with stage relevance applied) vs. observed negatives (policy 1.4.0)."""
    doc_items: list[dict] = []
    insp_items: list[dict] = []
    observed: list[str] = []
    for g in gates:
        if g.kind != "conditional":
            continue
        is_critical_missing = g.key.startswith("critical_missing:")
        if not (is_critical_missing or g.key in _OPEN_FLAG_KEYS):
            observed.append(g.reason)
            continue
        if is_critical_missing:
            item_key = g.key.split(":", 1)[1]
            label = g.reason.split(": ", 1)[0] if ": " in g.reason else g.reason
            status = "claimed_only" if "seller assurance only" in g.reason else "missing"
            doc_type = classify(item_key, label) in ("document", "both")
        else:
            item_key, label, status, doc_type = g.key, _OPEN_FLAG_LABELS.get(g.key, g.reason), "open", True
        # Stage relevance: a document-resolvable item still open after docs, or
        # any item still open at the PPI stage, is treated as an observed negative.
        if stage == "ppi" or (stage == "docs" and doc_type):
            observed.append(g.reason)
        else:
            (doc_items if doc_type else insp_items).append({"key": item_key, "label": label, "status": status})
    return {"document": doc_items, "inspection": insp_items, "observed": observed}


def compute_upside(score: Score, classified: dict[str, list]) -> int:
    """Score the car could reach if its still-open questions resolve favourably."""
    n_doc, n_insp = len(classified["document"]), len(classified["inspection"])
    bonus = 5 if any(it["key"] in _OPEN_FLAG_LABELS for it in classified["document"]) else 0
    doc_gain = min(max(0, 25 - score.documentation), GAIN_PER_ITEM * n_doc + bonus, DOC_GAIN_CAP) if (n_doc or bonus) else 0
    cond_gain = min(max(0, 25 - score.condition), COND_GAIN_PER_ITEM * n_insp, COND_GAIN_CAP) if n_insp else 0
    return min(100, score.total + doc_gain + cond_gain)


def verdict_from(score: Score, confidence: int, gates: list[Gate], stage: str = "listing") -> tuple[str, str]:
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
    observed = classify_conditionals(conds, stage)["observed"] if conds else []
    if verdict == "Reject" and not observed and score.total >= REJECT_FLOOR_NOTHING_OBSERVED:
        # A low score from unproven evidence is not a finding against the car (policy 1.5.0).
        verdict, reason = "Maybe / verify", f"Score {score.total}/100: low, but nothing observed wrong; low priority"
    if conds:
        classified = classify_conditionals(conds, stage)
        if classified["observed"]:
            if VERDICT_RANK[verdict] < VERDICT_RANK["Maybe / verify"]:
                verdict = "Maybe / verify"
                reason += "; capped until resolved: " + "; ".join(classified["observed"])
        else:
            # Nothing observed: every conditional here is an unanswered question,
            # not a negative finding. Worth pursuing conditionally, early, if the
            # upside is real; still capped once evidence should have arrived.
            open_items = classified["document"] + classified["inspection"]
            upside = compute_upside(score, classified)
            if score.total >= 45 and upside >= 75 and stage in {"listing", "questions"} and not required_open(conds):
                labels = "; ".join(it["label"] for it in open_items[:2])
                return "Pursue conditionally", (f"Worth pursuing if the open questions check out: could reach "
                                                f"{upside}/100 ({len(open_items)} open: {labels})")
            if VERDICT_RANK[verdict] < VERDICT_RANK["Maybe / verify"]:
                verdict = "Maybe / verify"
                reason += "; capped until resolved: " + "; ".join(g.reason for g in conds)
    if confidence < CONFIDENCE_PROVISIONAL and VERDICT_RANK[verdict] < VERDICT_RANK["Maybe / verify"]:
        verdict = "Maybe / verify"
        reason += f"; capped: assessment confidence {confidence} below {CONFIDENCE_PROVISIONAL}"
    return verdict, reason
