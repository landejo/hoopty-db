from scout import db
from scout.profiles import match_profile, suggest_key
from scout.scoring import locality_hint, market_stats, price_percentile, weighted_score


def test_seed_profiles_loaded_and_verified():
    keys = {p["key"]: p for p in db.list_profiles()}
    assert {"z3_30i", "z3_m", "gx470", "gx460"} <= set(keys)
    assert all(keys[k]["verified"] == 1 for k in ("z3_30i", "z3_m", "gx470", "gx460"))
    assert keys["z3_m"]["checks"][0]["key"] == "rear_subframe"


def test_match_profile_prefers_longest_model_match_and_year_window():
    profiles = db.list_profiles()
    assert match_profile(profiles, "BMW", "Z3 M coupe", 2001)["key"] == "z3_m"
    assert match_profile(profiles, "BMW", "Z3 3.0i roadster", 2001)["key"] == "z3_30i"
    assert match_profile(profiles, "Lexus", "GX 470", 2007)["key"] == "gx470"
    assert match_profile(profiles, "Lexus", "GX460", 2003) is None  # outside year window
    assert match_profile(profiles, "Porsche", "911", 2008) is None


def test_suggest_key():
    assert suggest_key("Porsche", "911 Carrera S", "997.2") == "porsche_911_carrera_s_997_2"


def test_weighted_score_renormalizes():
    assert weighted_score({"reliability": 4, "value": 2}, {"reliability": 0.5, "value": 0.5, "locality": 0.5}) == 3.0
    assert weighted_score({}, {"reliability": 1}) is None


def test_locality_hint():
    assert locality_hint("Monterey, CA") == 5
    assert locality_hint("Los Angeles, CA") == 4
    assert locality_hint("Phoenix, AZ") == 3
    assert locality_hint("Boston, MA") == 1
    assert locality_hint("") is None


def test_market_stats_and_percentile():
    comps = [{"sold_price": 30000, "availability": "sold", "mileage": 100000},
             {"sold_price": 40000, "availability": "sold", "mileage": 60000},
             {"price": 35000, "availability": "ended", "mileage": 80000}]
    actives = [{"price": 38000, "mileage": 70000}]
    s = market_stats(comps, actives)
    assert s["sold_count"] == 2 and s["sold_median"] == 35000 and s["asking_median"] == 38000
    assert s["mileage_median"] == 75000
    assert price_percentile(38000, [30000, 40000]) == 50


def test_preliminary_score_separates_similar_cars():
    from scout.scoring import preliminary_score
    from scout.policy.state import DEFAULT_STATE
    prof = db.get_profile("z3_30i")
    base = {"id": 1, "site": "facebook", "year": 2001, "make": "BMW", "model": "Z3 3.0i roadster", "transmission": "Manual",
            "mileage": 80000, "price": 12500, "location": "San Jose, CA", "vin": "WBACN53431LJ58954", "mission": "enthusiast_bridge",
            "normalized": {"ratings": {"documentation": {"score": 6, "why": "receipts listed"}, "condition": {"score": 7, "why": "clean"}, "spec": {"score": 7, "why": "sport pkg"}}, "red_flags": []}}
    peers = [{"id": 2, "price": 14000, "mileage": 90000}, {"id": 3, "price": 15500, "mileage": 70000}, {"id": 4, "price": 13000, "mileage": 85000}]
    local_cheap, b1 = preliminary_score(base, prof, DEFAULT_STATE, [], peers)
    remote_auto_pricey, b2 = preliminary_score({**base, "id": 5, "transmission": "Automatic", "price": 17500, "location": "Boston, MA", "vin": None}, prof, DEFAULT_STATE, [], peers)
    same_but_remote, b3 = preliminary_score({**base, "id": 6, "location": "Boston, MA"}, prof, DEFAULT_STATE, [], peers)
    assert local_cheap > same_but_remote > remote_auto_pricey
    assert local_cheap - remote_auto_pricey >= 25          # real distance, not 3.9 vs 3.8
    assert b1["price_value"]["points"] >= 9 and b2["price_value"]["points"] <= 6   # cheap vs over budget
    assert b2["mission_fit"]["points"] <= 3 and b1["mission_fit"]["points"] >= 11
    assert b1["documentation"]["points"] <= 15                                        # capped until critical evidence is examined
    assert b1["logistics"]["points"] == 10 and b3["logistics"]["points"] == 2
    assert sum(v["max"] for v in b1.values()) == 100


def test_preliminary_score_uses_sold_comps_and_penalizes_red_flags():
    from scout.scoring import preliminary_score
    from scout.policy.state import DEFAULT_STATE
    prof = db.get_profile("z3_30i")
    l = {"id": 1, "site": "cargurus", "year": 2002, "make": "BMW", "model": "Z3 3.0i", "transmission": "Manual", "mileage": 60000,
         "price": 20000, "location": "Reno, NV", "mission": "enthusiast_bridge",
         "normalized": {"ratings": {"condition": {"score": 8, "why": ""}}, "red_flags": ["a", "b", "c"]}}
    comps = [{"sold_price": 15000, "mileage": 70000}, {"sold_price": 16000, "mileage": 60000}, {"sold_price": 14000, "mileage": 90000}]
    total, b = preliminary_score(l, prof, DEFAULT_STATE, comps, [])
    assert "sold comps" in b["price_value"]["why"] and b["price_value"]["points"] <= 4
    assert b["condition"]["points"] == 20      # red flags are noted, not double-counted
    assert b["documentation"]["points"] == 3   # unread listing starts at 1/10, no VIN


def test_unread_listing_scores_below_a_typical_assessment():
    """The preliminary must not out-score the assessment by default."""
    from scout.scoring import preliminary_score
    from scout.policy.state import DEFAULT_STATE
    prof = db.get_profile("z3_30i")
    l = {"id": 1, "site": "facebook", "year": 2001, "make": "BMW", "model": "Z3 3.0i", "transmission": "Manual", "mileage": 80000,
         "price": 12500, "location": "San Jose, CA", "mission": "enthusiast_bridge", "normalized": {}}
    total, b = preliminary_score(l, prof, DEFAULT_STATE, [], [])
    assert total <= 55 and b["documentation"]["points"] == 3


def test_listing_age_penalty_and_stale_gate():
    from datetime import date, timedelta
    from scout.scoring import age_penalty, listing_age_days, preliminary_score
    from scout.policy.state import DEFAULT_STATE
    from scout.policy.gates import quick_gates
    prof = db.get_profile("z3_30i")
    base = {"id": 1, "site": "facebook", "year": 2001, "make": "BMW", "model": "Z3 3.0i", "transmission": "Manual", "mileage": 80000,
            "price": 12500, "location": "San Jose, CA", "mission": "enthusiast_bridge", "normalized": {}, "availability": "active"}
    fresh = {**base, "listing_date": (date.today() - timedelta(days=21)).isoformat()}
    old = {**base, "listing_date": (date.today() - timedelta(days=365)).isoformat()}
    assert age_penalty(listing_age_days(fresh), DEFAULT_STATE) == (0, "")
    assert age_penalty(listing_age_days(old), DEFAULT_STATE)[0] == 6
    assert preliminary_score(fresh, prof, DEFAULT_STATE, [], [])[0] - preliminary_score(old, prof, DEFAULT_STATE, [], [])[0] == 6
    assert any(g.startswith("stale") for g in quick_gates(old, prof, "enthusiast_bridge", DEFAULT_STATE))
    assert not any(g.startswith("stale") for g in quick_gates(fresh, prof, "enthusiast_bridge", DEFAULT_STATE))
    live_auction = {**old, "site": "bat"}
    assert listing_age_days(live_auction) is None  # auctions end on a clock; age does not apply
