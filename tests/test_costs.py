"""scout.policy.costs: fair-value-anchored offers and the early-bid price basis."""
from datetime import datetime, timezone

from scout import db
from scout.policy.costs import compute_costs, price_basis
from scout.policy.schema import CategoryRating, EvidenceInterpretation, MoneyRange, Ratings
from scout.policy.state import DEFAULT_STATE

STATE = dict(DEFAULT_STATE)


def _profile(key):
    p = db.get_profile(key)
    assert p, key
    return p


def _ev(critical=None, flags=None, imm=(500, 1500), expected_hammer=None, **kw):
    r = lambda v: CategoryRating(rating=v, rationale="test")
    return EvidenceInterpretation(
        ratings=Ratings(documentation=r(8), condition=r(8), price_value=r(7), mission_fit=r(8), logistics=r(8), emotional_spec_fit=r(8)),
        evidence_quality=7, immediate_service_estimate=MoneyRange(low=imm[0], high=imm[1]),
        critical_evidence=[{"key": k, "status": v, "evidence": "", "source": "ai_inference"} for k, v in (critical or {}).items()],
        flags=flags or {}, expected_hammer=expected_hammer, **kw,
    )


def _listing(**kw):
    base = {"id": 1, "site": "facebook", "url": "u", "year": 2001, "make": "BMW", "model": "Z3 3.0i roadster",
            "transmission": "Manual", "mileage": 80000, "price": 15000, "price_kind": "asking", "location": "Santa Cruz, CA"}
    base.update(kw)
    return base


def test_offer_anchored_on_fair_value_below_ask():
    l = _listing(price=18000)
    ev = _ev()
    fair = {"mid": 14000, "low": 13000, "high": 15000, "n": 5, "basis": "sold", "relaxed": [], "note": "5 sales"}
    c = compute_costs(l, _profile("z3_30i"), ev, [], STATE, fair)
    kw_mid = 0  # no known work in this evidence
    assert c.offer_high == 14000 - kw_mid
    assert c.offer_low <= c.offer_high
    assert any("fair value" in n for n in c.notes)


def test_offer_never_above_ask():
    l = _listing(price=12000)
    ev = _ev()
    fair = {"mid": 20000, "low": 18000, "high": 22000, "n": 5, "basis": "sold", "relaxed": [], "note": "5 sales"}
    c = compute_costs(l, _profile("z3_30i"), ev, [], STATE, fair)
    assert c.offer_high == 12000       # fair value is above ask; never offer more than the ask
    assert c.offer_low <= c.offer_high


def test_offer_never_negative_even_with_heavy_known_work():
    l = _listing(price=9000)
    ev = _ev(known_work_estimate=MoneyRange(low=8000, high=12000), known_work_items=["engine"])
    fair = {"mid": 9500, "low": 9000, "high": 10000, "n": 4, "basis": "sold", "relaxed": [], "note": "4 sales"}
    c = compute_costs(l, _profile("z3_30i"), ev, [], STATE, fair)
    assert c.offer_high >= 0 and c.offer_low >= 0


def test_unpriced_early_auction_basis_with_no_expected_hammer():
    now = datetime.now(timezone.utc)
    l = _listing(site="bat", price=3600, price_kind="current_bid", availability="active",
                 raw={"time_left": "5 days", "time_left_seen_at": now.isoformat()})
    ev = _ev(flags={"reserve_auction": "yes"})
    basis, price, notes = price_basis(l, ev, None, STATE)
    assert basis == "unpriced" and price == 3600
    assert any("no expected hammer or comps" in n for n in notes)


def test_early_bid_uses_fair_value_when_no_expected_hammer_given():
    now = datetime.now(timezone.utc)
    l = _listing(site="bat", price=3600, price_kind="current_bid", availability="active",
                 raw={"time_left": "5 days", "time_left_seen_at": now.isoformat()})
    ev = _ev(flags={"reserve_auction": "yes"})
    fair = {"mid": 15000, "low": 14000, "high": 16000, "n": 4, "basis": "sold", "relaxed": [], "note": "4 sales"}
    basis, price, notes = price_basis(l, ev, fair, STATE)
    assert basis == "expected_hammer" and price == 15000
