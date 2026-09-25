"""Deterministic assessment: gates -> score -> confidence -> costs -> verdict.
The model's EvidenceInterpretation is the only non-deterministic input."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from scout.policy import POLICY_VERSION
from scout.policy.preferences import CATEGORY_POINTS
from scout.policy.costs import compute_costs
from scout.policy.gates import evaluate_gates
from scout.policy.schema import Assessment, CostBreakdown, EvidenceInterpretation, Gate
from scout.policy.scoring import (classify_conditionals, compute_confidence, compute_score, compute_upside, defer_required,
                                  required_open, verdict_from)


def default_mission(profile: dict[str, Any] | None) -> str:
    return (profile or {}).get("mission_default") or "enthusiast_bridge"


def compute_merit(score) -> int:
    """1.8.0: the score over what can be judged before the seller is asked for
    anything: every category except documentation, scaled to 0-100. Missing
    records are not scored as good; they are the to-do list (open_questions)."""
    doc_max = CATEGORY_POINTS["documentation"]
    return round(100 * (score.total - score.documentation) / (100 - doc_max))


def over_walkaway(costs: CostBreakdown) -> float | None:
    """How far the price is over the walk-away (0.12 = 12% over); None when unknown."""
    if not costs.max_price or not costs.price or costs.price_basis == "unpriced":
        return None
    return costs.price / costs.max_price - 1


def compute_priority(score, upside: int, gates: list[Gate], classified: dict[str, list], costs: CostBreakdown,
                     stage: str = "listing", merit: int | None = None) -> int:
    """0-100 "pursue next" rank: worth investing the next step in this car
    right now, relative to the others (policy 1.4.0; 1.8.0: known merit and the
    walk-away price drive it until the seller has sent documents)."""
    if any(g.kind in {"hard", "strategy", "configuration"} for g in gates):
        return 0
    if stage in {"listing", "questions"} and merit is not None:
        p = float(merit)
        over = over_walkaway(costs)
        if over and over > 0:
            p -= min(25, round(100 * over))
    else:
        p = 0.6 * score.total + 0.4 * upside   # proven evidence outweighs hoped-for evidence
    p -= 10 * len(classified["observed"])
    if costs.price_basis in {"unpriced", "expected_hammer"}:
        p -= 8
    if any(g.key == "stale_listing" for g in gates):
        p -= 5
    return max(0, min(100, round(p)))


def _clip(s: str, n: int = 160) -> str:
    s = s.strip()
    return s if len(s) <= n else s[:n - 1].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def _short_label(label: str) -> str:
    return label.split(" (")[0].split("; ")[0].split(", ")[0].strip()


def compute_headline(verdict: str, reason: str, score: Score, gates: list[Gate],
                     classified: dict[str, list], upside: int | None) -> str:
    """One deterministic sentence, <=160 chars: verdict + stage + the main
    driver. Built from data already computed for gates/score/classified/upside,
    never from the model's prose - so it can never contradict the verdict."""
    hard = next((g for g in gates if g.kind in {"hard", "strategy", "configuration"}), None)
    if hard:
        return _clip(f"{verdict} — {hard.reason}")

    req = required_open(gates)
    if req and verdict == "Maybe / verify":
        n = len(classified.get("document") or []) + len(classified.get("inspection") or [])
        return _clip(f"{verdict} — required first: {_short_label(req[0].reason.split(': ', 1)[0])}; {n} open.")

    doc_items = classified.get("document") or []
    insp_items = classified.get("inspection") or []
    observed = classified.get("observed") or []
    open_n = len(doc_items) + len(insp_items)

    if verdict == "Pursue conditionally" and upside is not None and upside > score.total:
        labels = " and ".join(_short_label(it["label"]) for it in (doc_items + insp_items)[:2])
        if labels:
            return _clip(f"{verdict} — could reach {upside} if {labels} come back clean.")
        return _clip(f"{verdict} — could reach {upside}/100 if the open items check out.")

    if verdict == "Maybe / verify":
        questions = f"{open_n} question{'s' if open_n != 1 else ''} open."
        if observed:
            return _clip(f"{verdict} — observed: {observed[0]}" + (f"; {questions}" if open_n else "."))
        if open_n and "nothing observed wrong" not in reason:
            return _clip(f"{verdict} — {questions}")

    if verdict == "Pursue":
        return _clip(f"{verdict} — score {score.total}/100, no open gates.")

    if reason.startswith("Score "):   # band-only verdict: name what pulled the score down
        from scout.policy.preferences import CATEGORY_LABELS, CATEGORY_POINTS
        weak = sorted(CATEGORY_POINTS, key=lambda k: getattr(score, k) / CATEGORY_POINTS[k])[:2]
        names = " and ".join(CATEGORY_LABELS[k].split(" & ")[0].split(" / ")[0].lower() for k in weak)
        if observed and verdict == "Reject":
            return _clip(f"{verdict} — score {score.total}/100 and observed: {observed[0]}")
        tail = ("; nothing observed wrong, low priority" + (f", {open_n} open" if open_n else "")) if "nothing observed wrong" in reason else ""
        return _clip(f"{verdict} — score {score.total}/100, weakest on {names}{tail}.")
    return _clip(f"{verdict} — {reason}")


def compute_next_step(listing: dict[str, Any], stage: str, gates: list[Gate], classified: dict[str, list],
                      costs: CostBreakdown, priority: int) -> dict | None:
    """1.8.0: one action before and just after contact. Later stages go by the verdict."""
    from scout.policy.preferences import (NEXT_STEP_AUCTION_HOURS, NEXT_STEP_CONTACT_OVER_WALKAWAY, NEXT_STEP_CONTACT_PRIORITY,
                                          NEXT_STEP_SKIP_OBSERVED_PRIORITY, NEXT_STEP_SKIP_OVER_WALKAWAY)
    from scout.scoring import auction_hours_left
    if stage not in {"listing", "questions"}:
        return None
    block = next((g for g in gates if g.kind in {"hard", "strategy", "configuration"}), None)
    if block:
        return {"action": "Skip", "reason": _clip(block.reason, 140)}
    over = over_walkaway(costs)
    if over is not None and over > NEXT_STEP_SKIP_OVER_WALKAWAY:
        return {"action": "Skip", "reason": f"${costs.price:,} is {round(100 * over)}% over your walk-away ${costs.max_price:,}"}
    observed = classified.get("observed") or []
    if observed and priority < NEXT_STEP_SKIP_OBSERVED_PRIORITY:
        return {"action": "Skip", "reason": _clip("Observed: " + observed[0], 140)}
    open_items = (classified.get("document") or []) + (classified.get("inspection") or [])
    ask = _short_label(open_items[0]["label"]) if open_items else ""
    if stage == "questions":
        return {"action": "Follow up", "reason": f"Waiting on the seller{': ' + ask if ask else ''}"}
    hrs = auction_hours_left(listing)
    if hrs is not None and 0 < hrs <= NEXT_STEP_AUCTION_HOURS and priority >= NEXT_STEP_CONTACT_PRIORITY - 5:
        return {"action": "Contact now", "reason": f"Auction closes in about {round(hrs)}h; ask before bidding" + (f": {ask}" if ask else "")}
    if priority >= NEXT_STEP_CONTACT_PRIORITY and (over is None or over <= NEXT_STEP_CONTACT_OVER_WALKAWAY):
        return {"action": "Contact now", "reason": "Ranks near the top" + (f"; ask for: {ask}" if ask else "")}
    why = (f"${costs.price:,} is {round(100 * over)}% over your walk-away ${costs.max_price:,}" if over and over > 0
           else f"observed: {observed[0]}" if observed else f"ranks {priority}; others come first")
    return {"action": "Watch", "reason": _clip(why, 140)}


def compute_next_steps(listing: dict[str, Any], classified: dict[str, list], stage: str,
                       evidence: EvidenceInterpretation, gates: list[Gate] | None = None) -> list[str]:
    """Up to 3 concrete actions, in priority order."""
    steps: list[str] = []

    def add(s: str) -> None:
        if s and len(steps) < 3:
            steps.append(s if len(s) < 160 else s[:158].rsplit(" ", 1)[0].rstrip(",;:") + "…")

    def short(label: str) -> str:   # "Documented timing-belt ... (date and mileage)" -> the part before the detail
        return label.split(" (")[0].split("; ")[0].split(", ")[0].strip()

    for g in required_open(gates or [])[:1]:
        add(f"Get this first (required before purchase): {short(g.reason.split(': ', 1)[0])}")
    if not listing.get("vin"):
        add("Ask for the VIN")
    if stage == "listing" and classified["document"]:
        add(f"Request records: {'; '.join(short(it['label']) for it in classified['document'][:3])}")
    for q in evidence.seller_questions:
        q = q.strip()
        if q and not any(q.lower() == s.lower() for s in steps):
            add(q)
            break
    if stage == "docs" and classified["inspection"]:
        add(f"Book a PPI focused on: {'; '.join(short(it['label']) for it in classified['inspection'][:3])}")
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
    costs = compute_costs(listing, profile, evidence, gates, state, fair, mission)
    gates = evaluate_gates(listing, profile, evidence, mission, state, all_in_high=costs.all_in_high, provenance=prov,
                           all_in_mid=(costs.all_in_low + costs.all_in_high) // 2)
    costs = compute_costs(listing, profile, evidence, gates, state, fair, mission)
    from scout.policy.state import budget_for
    budget = budget_for(state, mission)
    cap = budget.get("defeats_purpose_all_in")
    if cap and costs.all_in_high > cap >= (costs.all_in_low + costs.all_in_high) // 2:
        costs.notes.append(f"High end of the all-in range (${costs.all_in_high:,}, with known work) is above the {mission.replace('_', ' ')} ceiling ${cap:,}; the midpoint is under it.")
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
    gates = defer_required(gates, stage)   # after score/confidence: the documentation cap for a hard item still applies
    verdict, reason = verdict_from(score, confidence, gates, stage)
    classified = classify_conditionals(gates, stage)
    upside = compute_upside(score, classified)
    merit = compute_merit(score)
    priority = compute_priority(score, upside, gates, classified, costs, stage, merit)
    next_step = compute_next_step(listing, stage, gates, classified, costs, priority)
    next_steps = compute_next_steps(listing, classified, stage, evidence, gates)
    headline = compute_headline(verdict, reason, score, gates, classified, upside)
    return Assessment(
        policy_version=POLICY_VERSION, mission=mission, urgency_mode=state.get("urgency_mode", "accelerated_bridge"),
        gates=gates, score=score, confidence=confidence, verdict=verdict, verdict_reason=reason, headline=headline,
        costs=costs, evidence=evidence, vin_history=vin_history,
        context={"budget": budget, "urgency_mode": state.get("urgency_mode")},
        assessed_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(), model=model,
        stage=stage, upside=upside, priority=priority, open_questions=classified, next_steps=next_steps,
        merit=merit, next_step=next_step,
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
