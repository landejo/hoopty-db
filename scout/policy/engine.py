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
    gates = evaluate_gates(listing, profile, evidence, mission, state)
    costs = compute_costs(listing, profile, evidence, gates, state, comps_median)
    gates = evaluate_gates(listing, profile, evidence, mission, state, all_in_high=costs.all_in_high)
    costs = compute_costs(listing, profile, evidence, gates, state, comps_median)
    score = compute_score(evidence, gates, listing, mission, state, vin_history)
    confidence = compute_confidence(evidence, gates, listing)
    verdict, reason = verdict_from(score, confidence, gates)
    return Assessment(
        policy_version=POLICY_VERSION, mission=mission, urgency_mode=state.get("urgency_mode", "accelerated_bridge"),
        gates=gates, score=score, confidence=confidence, verdict=verdict, verdict_reason=reason,
        costs=costs, evidence=evidence, vin_history=vin_history,
        assessed_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(), model=model,
    )
