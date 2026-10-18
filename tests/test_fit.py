"""Fit score (scout/policy/fit.py): spreads the board on what is knowable now."""
from scout.policy.fit import FIT_POINTS, accident_points, fit_score


def _a(walk=20000, price=18000, basis="value", owners="1", accident="No accident or damage reported",
       title="Clean", spec=7, cond=5, docs=5, mission="enthusiast_bridge"):
    return {"mission": mission,
            "costs": {"max_price": walk, "price": price, "price_basis": "asking", "max_price_basis": basis},
            "evidence": {"facts": [{"key": "owners", "status": "claimed", "value": owners},
                                   {"key": "accident_history", "status": "claimed", "value": accident},
                                   {"key": "title_status", "status": "claimed", "value": title}],
                         "ratings": {"emotional_spec_fit": {"rating": spec}, "condition": {"rating": cond},
                                     "documentation": {"rating": docs}}}}


def _l(price=18000, miles=40000, loc="Carmel, CA", trans="Manual"):
    return {"price": price, "mileage": miles, "location": loc, "transmission": trans, "mission": "enthusiast_bridge"}


def test_parts_add_up_and_stay_in_range():
    f = fit_score(_l(), _a())
    assert set(f["parts"]) == set(FIT_POINTS) and 0 <= f["total"] <= 100
    assert all(0 <= f["parts"][k] <= FIT_POINTS[k] for k in FIT_POINTS)
    assert fit_score(_l(), None) is None


def test_value_uses_the_full_range_and_the_current_price():
    under = fit_score(_l(price=16000), _a(price=16000))["parts"]["value"]
    at = fit_score(_l(price=20000), _a(price=20000))["parts"]["value"]
    over = fit_score(_l(price=26000), _a(price=26000))["parts"]["value"]
    assert under == FIT_POINTS["value"] and over == 0 and 0 < at < under
    # A price drop after the assessment counts: the listing's own asking price wins.
    assert fit_score(_l(price=16000), _a(price=20000))["parts"]["value"] == under


def test_budget_only_walkaway_is_not_proof_of_value():
    proven = fit_score(_l(price=14000), _a(price=14000))["parts"]["value"]
    unproven = fit_score(_l(price=14000), _a(price=14000, basis="budget"))["parts"]["value"]
    assert unproven < proven and unproven <= FIT_POINTS["value"] * 0.6


def test_history_rewards_few_owners_and_a_clean_record():
    good = fit_score(_l(), _a())["parts"]["history"]
    bad = fit_score(_l(), _a(owners="6 previous owners", accident="Accident or damage reported"))["parts"]["history"]
    assert good == FIT_POINTS["history"] and bad < good / 2


def test_manual_matters_only_where_the_mission_wants_one():
    man = fit_score(_l(trans="Manual"), _a())["parts"]["spec"]
    auto = fit_score(_l(trans="Automatic"), _a())["parts"]["spec"]
    assert man - auto == 5
    gx = _a(mission="utility_capability")
    assert fit_score({**_l(trans="Automatic"), "mission": "utility_capability"}, gx)["parts"]["spec"] == man


def test_accident_wording_from_real_listings():
    assert accident_points("claimed", "No accident or damage reported to CARFAX")[0] == 8
    assert accident_points("claimed", "0 accidents reported")[0] == 8
    assert accident_points("claimed", "Clean Carfax claimed")[0] == 8
    assert accident_points("claimed", "Seller states 'no significant damage or problems'")[0] == 6
    assert accident_points("claimed", "'No major accidents'")[0] == 4
    assert accident_points("unknown", "unknown")[0] == 4
    assert accident_points("claimed", "Carfax reports 'minor damage' to the front, February 2018")[0] == 3
    assert accident_points("claimed", "Accident or damage reported")[0] == 2
    # "no structural damage or airbag deployment" is not a serious accident (#253).
    assert accident_points("verified", "Damage reported 03/24/2020; no structural damage or airbag deployment reported")[0] == 2
    assert accident_points("claimed", "Front-end collision leading to a total-loss branding")[0] == 0


def test_fit_spreads_cars_the_assessment_score_bunches():
    """Two listing-stage cars a few assessment points apart land far apart on Fit."""
    good = fit_score(_l(price=17000, miles=30000, loc="San Jose, CA"), _a(price=17000))
    weak = fit_score(_l(price=23000, miles=120000, loc="Boston, MA"),
                     _a(price=23000, owners="5", accident="Accident or damage reported"))
    assert good["total"] - weak["total"] >= 40
