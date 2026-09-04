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
