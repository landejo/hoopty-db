"""Deterministic assessment: gates -> score -> confidence -> costs -> verdict.
The model's EvidenceInterpretation is the only non-deterministic input."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from scout.policy import POLICY_VERSION
from scout.policy.costs import compute_costs
from scout.policy.gates import evaluate_gates
from scout.policy.schema import Assessment, EvidenceInterpretation
from scout.policy.scoring import compute_confidence, compute_score, verdict_from


def default_mission(profile: dict[str, Any] | None) -> str:
    return (profile or {}).get("mission_default") or "enthusiast_bridge"


def assess(listing: dict[str, Any], profile: dict[str, Any], evidence: EvidenceInterpretation,
           state: dict[str, Any], vin_history: dict[str, Any] | None = None,
           comps_median: int | None = None, mission: str | None = None, model: str = "") -> Assessment:
    vin_history = vin_history or {}
    mission = mission or listing.get("mission") or default_mission(profile)
    # First pass without the cost gate, then costs, then the cost gate.
    prov = vin_history.get("provenance") or {}
    gates = evaluate_gates(listing, profile, evidence, mission, state, provenance=prov)
    costs = compute_costs(listing, profile, evidence, gates, state, comps_median)
    gates = evaluate_gates(listing, profile, evidence, mission, state, all_in_high=costs.all_in_high, provenance=prov,
                           all_in_mid=(costs.all_in_low + costs.all_in_high) // 2)
    costs = compute_costs(listing, profile, evidence, gates, state, comps_median)
    cap = (state.get("budget") or {}).get("defeats_purpose_all_in")
    if cap and mission in {"enthusiast_bridge", "pragmatic_bridge"} and costs.all_in_high > cap >= (costs.all_in_low + costs.all_in_high) // 2:
        costs.notes.append(f"High end of the all-in range (${costs.all_in_high:,}) is above the bridge ceiling ${cap:,}; the midpoint is under it. The PPI decides.")
    # Price ceiling anchors to the last documented price when the car was
    # recently resold/relisted at a markup (guide: transaction costs are not
    # improvements; only documented post-sale work moves the ceiling).
    pp = prov.get("price_progression") or {}
    ref_price = (pp.get("reference") or {}).get("price")
    if ref_price and any(f in (prov.get("flags") or []) for f in ("material_markup", "major_markup")):
        allowance = 0.20 if (prov.get("what_changed") or {}).get("work_after_prior_sale") else 0.10
        ceiling = int(ref_price * (1 + allowance))
        capped = costs.max_price > ceiling
        if capped:
            costs.max_price = ceiling
            costs.offer_high = min(costs.offer_high, ceiling)
            costs.offer_low = min(costs.offer_low, int(ceiling * 0.92))
        costs.notes.append(f"Ceiling anchored to the last documented price ${ref_price:,} plus {int(allowance * 100)}% "
                           f"({'documented post-sale work' if allowance > 0.1 else 'no documented post-sale work'}) = ${ceiling:,}; "
                           f"the new ${listing.get('price') or 0:,} ask is not the anchor" + (" (cap applied)." if capped else "."))
    score = compute_score(evidence, gates, listing, mission, state, vin_history)
    confidence = compute_confidence(evidence, gates, listing)
    if not (evidence.next_action or "").strip():
        # The model must always leave one concrete step; derive it from its own lists.
        first_q = next(iter(evidence.seller_questions), None)
        first_u = next(iter(evidence.unknowns), None)
        evidence.next_action = (f"Ask the seller: {first_q}" if first_q else f"Resolve first: {first_u}" if first_u
                                else "Arrange an independent PPI before any money moves.")
    verdict, reason = verdict_from(score, confidence, gates)
    return Assessment(
        policy_version=POLICY_VERSION, mission=mission, urgency_mode=state.get("urgency_mode", "accelerated_bridge"),
        gates=gates, score=score, confidence=confidence, verdict=verdict, verdict_reason=reason,
        costs=costs, evidence=evidence, vin_history=vin_history,
        context={"budget": dict(state.get("budget") or {}), "urgency_mode": state.get("urgency_mode")},
        assessed_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(), model=model,
    )


def rescore_assessment(listing: dict[str, Any], profile: dict[str, Any], stored: dict[str, Any],
                       state: dict[str, Any]) -> dict[str, Any] | None:
    """Recompute score/verdict/costs from a stored assessment's evidence under the
    current policy. Keeps the original model and evidence; bumps policy_version."""
    try:
        evidence = EvidenceInterpretation.model_validate(stored.get("evidence") or {})
    except Exception:
        return None
    vh = stored.get("vin_history") or {}
    a = assess(listing, profile, evidence, state, vin_history=vh, comps_median=None,
               mission=stored.get("mission"), model=stored.get("model", ""))
    d = a.model_dump()
    d["assessed_at"] = stored.get("assessed_at", d["assessed_at"])
    d["rescored_from"] = stored.get("policy_version")
    if stored.get("context"):
        d["context"] = stored["context"]
    d["mission"] = stored.get("mission", d["mission"])   # what the model was told, not what the listing says now
    return d
