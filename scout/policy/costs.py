"""Risk-adjusted acquisition cost and the backward-solved maximum price (§10)."""
from __future__ import annotations

from datetime import date
from typing import Any

from scout.policy.schema import CostBreakdown, EvidenceInterpretation, Gate
from scout.scoring import is_early_bid, locality_hint

VALUE_CEILING_MIN_COMPS = 3   # fewer comparable sales than this: the budget alone sets the walk-away


def buyer_fee(site: str, price: int, state: dict[str, Any]) -> int:
    f = (state.get("fees") or {}).get(site) or {"pct": 0, "min": 0, "max": 0}
    if not f.get("pct"):
        return 0
    return int(min(max(price * f["pct"], f.get("min", 0)), f.get("max", 10**9)))


def transport_cost(location: str | None, state: dict[str, Any]) -> int:
    band = locality_hint(location)
    table = state.get("transport_by_locality_band") or {}
    return int(table.get(str(band), table.get("unknown", 0)) if band else table.get("unknown", 0))


def overdue_allowance(listing: dict[str, Any], evidence: EvidenceInterpretation, state: dict[str, Any]) -> int:
    o = state.get("overdue_allowance") or {}
    year = listing.get("year") or date.today().year
    age = date.today().year - int(year)
    miles = listing.get("mileage") or 0
    if age >= o.get("old_years", 15) or miles >= o.get("high_mileage", 100000):
        amt = o.get("old_or_high_mileage", 1500)
    elif age >= o.get("middle_years", 8):
        amt = o.get("middle_aged", 800)
    else:
        amt = o.get("recent", 300)
    if evidence.flags.age_related_service_documented == "yes":
        amt = int(amt * o.get("documented_discount", 0.5))
    return int(amt)


def risk_reserve(profile: dict[str, Any], gates: list[Gate], state: dict[str, Any]) -> int:
    base = int(profile.get("risk_reserve") or state.get("default_risk_reserve", 1500))
    unresolved = sum(1 for g in gates if g.kind == "conditional")
    counted = min(unresolved, int(state.get("reserve_max_unresolved_counted", 2)))
    return base + counted * int(state.get("reserve_per_unresolved_conditional", 1000))


def price_basis(listing: dict[str, Any], evidence: EvidenceInterpretation, fair: dict[str, Any] | None,
                state: dict[str, Any]) -> tuple[str, int, list[str]]:
    """An early bid on a reserve auction is not a price (§10)."""
    notes: list[str] = []
    price = int(listing.get("price") or 0)
    kind = listing.get("price_kind") or ("current_bid" if listing.get("site") in {"bat", "carsandbids"} else "asking")
    if kind == "sold" and listing.get("sold_price"):
        return "sold", int(listing["sold_price"]), notes
    if kind in {"current_bid", "reserve_not_met", "no_reserve"}:
        expected = None
        if evidence.expected_hammer:
            expected = (evidence.expected_hammer.low + evidence.expected_hammer.high) // 2
        elif fair and fair.get("basis") == "sold":
            expected = fair.get("mid")
        reserve = evidence.flags.reserve_auction
        early, hrs = is_early_bid(listing, state)
        left = f" ({round(hrs / 24, 1)} days left)" if hrs is not None else ""
        if early and expected:
            notes.append(f"Current bid ${price:,} is an early bid{left}" + (" on a reserve auction" if reserve == "yes" else "")
                         + f"; it carries no weight until the closing day. Using expected hammer ${expected:,} for cost and value.")
            return "expected_hammer", int(expected), notes
        if early:
            notes.append(f"Current bid ${price:,} is an early bid{left}; no expected hammer or comps: price unknown.")
            return "unpriced", price, notes
        if expected and expected > price:
            notes.append(f"Current bid ${price:,} treated as an early bid" + (" on a reserve auction" if reserve == "yes" else "")
                         + f"; using expected hammer ${expected:,} for cost and value.")
            return "expected_hammer", int(expected), notes
        if reserve == "unknown":
            notes.append("Reserve status unknown; the current bid may not reflect the sale price.")
        return "current_bid", price, notes
    return "asking", price, notes


def compute_costs(listing: dict[str, Any], profile: dict[str, Any], evidence: EvidenceInterpretation,
                  gates: list[Gate], state: dict[str, Any], fair: dict[str, Any] | None,
                  mission: str | None = None) -> CostBreakdown:
    basis, price, notes = price_basis(listing, evidence, fair, state)
    site = listing.get("site") or ""
    fee = buyer_fee(site, price, state)
    transport = transport_cost(listing.get("location"), state)
    imm_lo, imm_hi = evidence.immediate_service_estimate.low, evidence.immediate_service_estimate.high
    kw = evidence.known_work_estimate
    kw_lo, kw_hi = (kw.low, kw.high) if kw else (0, 0)
    overdue = overdue_allowance(listing, evidence, state)
    reserve = risk_reserve(profile, gates, state)
    tax = int(price * float(state.get("tax_rate", 0)) + int(state.get("registration_fee", 0)))
    # All-in = what it costs to own the car as listed: price, fee, transport, tax,
    # plus work the listing itself establishes as needed. Generic catch-up and the
    # risk reserve are reported alongside but not counted (policy 1.2.1).
    all_in_lo = price + fee + transport + tax + kw_lo
    all_in_hi = price + fee + transport + tax + kw_hi
    catch_lo = all_in_lo + imm_lo + overdue + reserve
    catch_hi = all_in_hi + imm_hi + overdue + reserve

    # maximum hammer = acceptable all-in - fee - transport - tax - known work (high)
    from scout.policy.state import budget_for
    acceptable = int(budget_for(state, mission or listing.get("mission")).get("acceptable_all_in") or 0)
    max_price = 0
    if acceptable:
        fixed = transport + kw_hi
        h = acceptable - fixed
        for _ in range(12):   # fee and tax depend on the hammer; iterate to a fixed point
            h2 = acceptable - fixed - buyer_fee(site, max(h, 0), state) - int(max(h, 0) * float(state.get("tax_rate", 0)) + int(state.get("registration_fee", 0)))
            if abs(h2 - h) < 5:
                break
            h = h2
        max_price = max(0, int(h))
    kw_mid = (kw_lo + kw_hi) // 2
    fair_mid = fair.get("mid") if fair else None

    # 1.8.0 walk-away: the lower of what the budget allows and what THIS car is worth:
    # the top of its fair range (75th percentile of mileage-adjusted sales), less its
    # known work, less the reserve for its still-unresolved questions.
    max_budget = max_price
    max_value = None
    unresolved_part = reserve - int(profile.get("risk_reserve") or state.get("default_risk_reserve", 1500))
    if fair and (fair.get("n") or 0) >= VALUE_CEILING_MIN_COMPS and (fair.get("high") or fair_mid):
        top = int(fair.get("high") or fair_mid)
        max_value = max(0, top - kw_mid - max(0, unresolved_part))
        basis_word = "sales" if fair.get("basis") == "sold" else "asking prices"
        notes.append(f"Walk-away from value: top of the fair range ${top:,} ({fair.get('n')} {basis_word})"
                     + (f" less known work ${kw_mid:,}" if kw_mid else "")
                     + (f" less ${unresolved_part:,} for unresolved questions" if unresolved_part > 0 else "")
                     + f" = ${max_value:,}.")
    elif fair:
        notes.append(f"Only {fair.get('n')} comparable sale(s): walk-away set by the budget alone.")
    if max_value is not None and (not max_budget or max_value < max_budget):
        max_price, max_basis = max_value, "value"
    else:
        max_basis = "budget"
    if fair_mid is not None and price:
        # Never above the ask or the max price; the opening offer sits at least 5% under the top.
        offer_hi = max(0, int(min(v for v in (price, max_price or None, fair_mid - kw_mid) if v is not None)))
        fair_low_net = (fair.get("low") if fair.get("low") is not None else fair_mid) - kw_mid
        offer_lo = max(0, int(min(max(0.85 * offer_hi, fair_low_net), 0.95 * offer_hi)))
        basis_word = "sales" if fair.get("basis") == "sold" else "asking prices"
        thin = " Thin or widened comp set: treat as a rough guide." if (fair.get("n") or 0) < 4 or fair.get("relaxed") else ""
        notes.append(f"Offer anchored on fair value ${fair_mid:,} ({fair.get('n')} {basis_word}) less known work ${kw_mid:,}.{thin}")
    else:
        anchor = min(price, max_price) if price and max_price else (price or max_price)
        offer_hi = int(anchor)
        offer_lo = int(anchor * 0.92)
    if price and max_price and max_price < 0.6 * price:
        notes.append(f"Maximum price ${max_price:,} is far below the ${price:,} {basis.replace('_', ' ')}; price mismatch.")
    if kw_hi:
        notes.append(f"Known repairs counted in the all-in: {', '.join(evidence.known_work_items[:5]) or 'stated by the listing'} (${kw_lo:,}–${kw_hi:,}).")
    if imm_hi - imm_lo > 2500:
        notes.append("Likely catch-up estimate has a wide swing; the PPI decides it. It is shown for planning and not counted in the all-in.")
    return CostBreakdown(price_basis=basis, price=price, buyer_fee=fee, transport=transport,
                         known_work_low=kw_lo, known_work_high=kw_hi, known_work_items=list(evidence.known_work_items),
                         immediate_service_low=imm_lo, immediate_service_high=imm_hi,
                         overdue_allowance=overdue, risk_reserve=reserve, tax_and_registration=tax,
                         all_in_low=all_in_lo, all_in_high=all_in_hi, with_catchup_low=catch_lo, with_catchup_high=catch_hi,
                         max_price=max_price, offer_low=offer_lo, offer_high=offer_hi, notes=notes,
                         max_price_budget=max_budget or None, max_price_value=max_value, max_price_basis=max_basis,
                         fair_mid=fair_mid, fair_low=fair.get("low") if fair else None,
                         fair_high=fair.get("high") if fair else None, fair_note=((fair.get("note") or "") if fair else ""))
