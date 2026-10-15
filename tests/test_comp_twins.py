"""BRZ / 86 / FR-S are one car: each prices against all three (2026-10-14)."""
from scout import db, market


def _add(url, key, role="comp", **kw):
    vals = {"url": url, "site": "carsandbids", "title": url, "profile_key": key, "role": role,
            "year": 2016, "transmission": "Manual", "mileage": 50000, "price": 18000,
            "price_kind": "sold", "availability": "sold", "sold_price": 18000}
    vals.update(kw)
    return db.upsert_listing(vals)[0]


def test_comp_keys_groups_twins_and_leaves_others_alone():
    assert market.comp_keys("subaru_brz") == ["scion_frs", "subaru_brz", "toyota_86"]
    assert market.comp_keys("toyota_86") == market.comp_keys("scion_frs") == market.comp_keys("subaru_brz")
    assert market.comp_keys("z3_m") == ["z3_m"]


def test_list_listings_takes_a_list_of_profile_keys():
    _add("u1", "toyota_86"); _add("u2", "scion_frs"); _add("u3", "z3_m")
    got = {r["url"] for r in db.list_listings(profile_key=market.comp_keys("subaru_brz"))}
    assert got == {"u1", "u2"}
    assert {r["url"] for r in db.list_listings(profile_key="z3_m")} == {"u3"}


def test_a_brz_values_against_86_and_frs_sales():
    for i, p in enumerate([17000, 18000, 19000, 20000]):
        _add(f"c{i}", "toyota_86" if i % 2 else "scion_frs", sold_price=p, price=p)
    brz = db.get_listing(_add("brz", "subaru_brz", role="candidate", price_kind="asking",
                              availability="active", sold_price=None))
    assert market.fair_value(brz, db.list_listings(profile_key="subaru_brz")) is None
    fair = market.fair_value(brz, db.list_listings(profile_key=market.comp_keys("subaru_brz")))
    assert fair and fair["n"] == 4 and fair["basis"] == "sold"


def test_an_ignored_listing_is_not_a_comp():
    for i, p in enumerate([17000, 18000, 19000, 20000]):
        _add(f"c{i}", "toyota_86", sold_price=p, price=p)
    _add("modded", "toyota_86", role="ignored", sold_price=5000, price=5000)
    car = db.get_listing(_add("car", "toyota_86", role="candidate", price_kind="asking",
                              availability="active", sold_price=None))
    fair = market.fair_value(car, db.list_listings(profile_key="toyota_86"))
    assert fair["n"] == 4 and fair["low"] >= 17000
