"""Deterministic assessment: gates -> score -> confidence -> costs -> verdict.
The model's EvidenceInterpretation is the only non-deterministic input."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from scout.policy import POLICY_VERSION
from scout.policy.costs import compute_costs
from scout.policy.gates import evaluate_gates
from scout.policy.schema import Assessment, CostBreakdown, EvidenceInterpretation, Gate
from scout.policy.scoring import classify_conditionals, compute_confidence, compute_score, compute_upside, verdict_from


def default_mission(profile: dict[str, Any] | None) -> str:
    return (profile or {}).get("mission_default") or "enthusiast_bridge"


def compute_priority(score, upside: int, gates: list[Gate], classified: dict[str, list], costs: CostBreakdown) -> int:
    """0-100 "pursue next" rank: worth investing the next step in this car
    right now, relative to the others (policy 1.4.0)."""
    if any(g.kind in {"hard", "strategy", "configuration"} for g in gates):
        return 0
    p = 0.4 * score.total + 0.6 * upside
    p -= 10 * len(classified["observed"])
    if costs.price_basis in {"unpriced", "expected_hammer"}:
        p -= 8
    if any(g.key == "stale_listing" for g in gates):
        p -= 5
    return max(0, min(100, round(p)))


def compute_next_steps(listing: dict[str, Any], classified: dict[str, list], stage: str,
                       evidence: EvidenceInterpretation) -> list[str]:
    """Up to 3 concrete actions, in priority order."""
    steps: list[str] = []

    def add(s: str) -> None:
        if s and len(steps) < 3:
            steps.append(s[:159])

    if not listing.get("vin"):
        add("Ask for the VIN")
    if stage == "listing" and classified["document"]:
        add(f"Request records: {', '.join(it['label'] for it in classified['document'][:3])}")
    for q in evidence.seller_questions:
        q = q.strip()
        if q and not any(q.lower() == s.lower() for s in steps):
            add(q)
            break
    if stage == "docs" and classified["inspection"]:
        add(f"Book a PPI focused on: {', '.join(it['label'] for it in classified['inspection'][:3])}")
    return steps


def assess(listing: dict[str, Any], profile: dict[str, Any], evidence: EvidenceInterpretation,
           state: dict[str, Any], vin_history: dict[str, Any] | None = None,
           fair: dict[str, Any] | None = None, mission: str | None = None, model: str = "",
           stage: str = "listing") -> Assessment:
    vin_history = vin_history or {}
    mission = mission or listing.get("mission") or default_mission(profile)
    # First pass without the cost gate, then costs, then the cost gate.
    prov = vin_history.get("provenance") or {}
    gates = evaluate_gates(listing, profile, evidence, mission, state, provenance=prov)
    costs = compute_costs(listing, profile, evidence, gates, state, fair)
    gates = evaluate_gates(listing, profile, evidence, mission, state, all_in_high=costs.all_in_high, provenance=prov,
                           all_in_mid=(costs.all_in_low + costs.all_in_high) // 2)
    costs = compute_costs(listing, profile, evidence, gates, state, fair)
    cap = (state.get("budget") or {}).get("defeats_purpose_all_in")
    if cap and mission in {"enthusiast_bridge", "pragmatic_bridge"} and costs.all_in_high > cap >= (costs.all_in_low + costs.all_in_high) // 2:
        costs.notes.append(f"High end of the all-in range (${costs.all_in_high:,}, with known work) is above the bridge ceiling ${cap:,}; the midpoint is under it.")
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
    score = compute_score(evidence, gates, listing, mission, state, vin_history, costs=costs)
    confidence = compute_confidence(evidence, gates, listing)
    if not (evidence.next_action or "").strip():
        # The model must always leave one concrete step; derive it from its own lists.
        first_q = next(iter(evidence.seller_questions), None)
        first_u = next(iter(evidence.unknowns), None)
        evidence.next_action = (f"Ask the seller: {first_q}" if first_q else f"Resolve first: {first_u}" if first_u
                                else "Arrange an independent PPI before any money moves.")
    verdict, reason = verdict_from(score, confidence, gates, stage)
    classified = classify_conditionals(gates, stage)
    upside = compute_upside(score, classified)
    priority = compute_priority(score, upside, gates, classified, costs)
    next_steps = compute_next_steps(listing, classified, stage, evidence)
    return Assessment(
        policy_version=POLICY_VERSION, mission=mission, urgency_mode=state.get("urgency_mode", "accelerated_bridge"),
        gates=gates, score=score, confidence=confidence, verdict=verdict, verdict_reason=reason,
        costs=costs, evidence=evidence, vin_history=vin_history,
        context={"budget": dict(state.get("budget") or {}), "urgency_mode": state.get("urgency_mode")},
        assessed_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(), model=model,
        stage=stage, upside=upside, priority=priority, open_questions=classified, next_steps=next_steps,
    )


def rescore_assessment(listing: dict[str, Any], profile: dict[str, Any], stored: dict[str, Any],
                       state: dict[str, Any], fair: dict[str, Any] | None = None,
                       stage: str = "listing") -> dict[str, Any] | None:
    """Recompute score/verdict/costs from a stored assessment's evidence under the
    current policy. Keeps the original model and evidence; bumps policy_version."""
    try:
        evidence = EvidenceInterpretation.model_validate(stored.get("evidence") or {})
    except Exception:
        return None
    vh = stored.get("vin_history") or {}
    a = assess(listing, profile, evidence, state, vin_history=vh, fair=fair,
               mission=stored.get("mission"), model=stored.get("model", ""), stage=stage)
    d = a.model_dump()
    d["assessed_at"] = stored.get("assessed_at", d["assessed_at"])
    d["rescored_from"] = stored.get("policy_version")
    if stored.get("context"):
        d["context"] = stored["context"]
    d["mission"] = stored.get("mission", d["mission"])   # what the model was told, not what the listing says now
    return d
