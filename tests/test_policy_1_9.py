"""Policy 1.9.0 and the "sold in the seller's words" fix (2026-09-29): a seller's
description never makes a live listing a comp; a VIN-less identity conflict is a
question; assessment evidence can re-profile a car; classified comps count at a
discount; thin comp sets cannot Skip; keeper mileage caps mission fit."""
from fastapi.testclient import TestClient

from scout import db
from scout.ingest import ingest_items, repair_roles
from scout.market import fair_value
from scout.policy.engine import assess
from scout.policy.schema import CategoryRating, Contradiction, EvidenceInterpretation, Fact, MoneyRange, Ratings
from scout.policy.state import DEFAULT_STATE
from scout.profiles import profile_from_evidence

STATE = dict(DEFAULT_STATE)

# A live Facebook listing page as the extension reads it: the "Message" button
# line, then a seller description that happens to say "sold".
FB_LIVE = ("Marketplace\n2001 BMW z3 3.0i\n$23,999\nListed 4 weeks ago in Arnold, MD\nMessage\n"
           "About this vehicle\nDriven 29,575 miles\nManual transmission\nSeller's description\n"
           "The previous owner was an elderly gentleman who sold it due to his wife. "
           "I will take the ad down when it's sold.\n") + "More detail. " * 60
FB_SOLD = "Marketplace\n2001 BMW z3 3.0i\n$23,999\nSold\nListed 4 weeks ago in Arnold, MD\n" + "More detail. " * 60


def _fb(url, text):
    return {"url": url, "title": "2001 BMW z3 3.0i", "price_text": "$23,999", "card_text": "2001 BMW z3 3.0i\n$23,999",
            "detail": {"text": text, "photos": ["https://x/1.jpg"], "is_detail_page": True}}


def _normalizer_says(monkeypatch, **out):
    from scout.config import CONFIG
    import scout.ai.normalize as nz
    monkeypatch.setattr(CONFIG, "anthropic_api_key", "test-only-never-called")
    monkeypatch.setattr("scout.vin.decode_vin", lambda *a, **k: None)
    base = {"is_vehicle": True, "year": 2001, "make": "BMW", "model": "Z3 3.0i", "profile_key": "z3_30i"}
    monkeypatch.setattr(nz, "normalize_listing", lambda *a, **k: {**base, **out})


# --- the bug: "sold" in the seller's own words ----------------------------------

def test_sold_in_the_seller_description_does_not_make_a_live_listing_a_comp(monkeypatch):
    _normalizer_says(monkeypatch, availability="sold")          # the fast model misreads the description
    url = "https://www.facebook.com/marketplace/item/97/"
    ingest_items("facebook", [_fb(url, FB_LIVE)], run_ai=True)
    row = db.get_listing_by_url(url)
    assert row["role"] == "candidate" and row["availability"] == "active"


def test_the_page_status_line_still_makes_it_a_comp(monkeypatch):
    _normalizer_says(monkeypatch, availability="sold")
    url = "https://www.facebook.com/marketplace/item/98/"
    item = _fb(url, FB_SOLD)
    ingest_items("facebook", [item], run_ai=True)
    assert db.get_listing_by_url(url)["role"] == "comp"


def test_a_live_page_read_frees_a_comp_that_a_misread_once_marked_sold():
    url = "https://www.facebook.com/marketplace/item/71/"
    ingest_items("facebook", [dict(_fb(url, FB_LIVE), sold=True)], run_ai=False)   # recorded sold once
    assert db.get_listing_by_url(url)["role"] == "comp"
    ingest_items("facebook", [_fb(url, FB_LIVE)], run_ai=False)                     # its own page, read now: live
    assert db.get_listing_by_url(url)["role"] == "candidate"


def test_repair_roles_frees_a_stuck_comp_whose_stored_page_is_live():
    url = "https://www.facebook.com/marketplace/item/72/"
    ingest_items("facebook", [dict(_fb(url, FB_LIVE), sold=True)], run_ai=False)
    lid = db.get_listing_by_url(url)["id"]
    db.update_listing(lid, {"availability": "active"})    # the state #71 was left in
    assert [r["id"] for r in repair_roles()] == [lid]
    assert db.get_listing(lid)["role"] == "candidate"


def test_repair_roles_leaves_a_comp_you_set_and_one_without_a_live_page():
    a, b = "https://www.facebook.com/marketplace/item/73/", "https://www.facebook.com/marketplace/item/74/"
    ingest_items("facebook", [dict(_fb(a, FB_LIVE), sold=True), dict(_fb(b, "no status here " * 50), sold=True)], run_ai=False)
    for u in (a, b):
        db.update_listing(db.get_listing_by_url(u)["id"], {"availability": "active"})
    db.update_listing(db.get_listing_by_url(a)["id"], {"role_user_set": 1})
    assert repair_roles() == []


# --- 2. identity conflicts without a VIN ---------------------------------------

def _ev(facts=(), contradictions=(), **kw):
    r = lambda v: CategoryRating(rating=v, rationale="test")
    return EvidenceInterpretation(
        ratings=Ratings(documentation=r(6), condition=r(7), price_value=r(8), mission_fit=r(8), logistics=r(7), emotional_spec_fit=r(7)),
        evidence_quality=6, immediate_service_estimate=MoneyRange(low=500, high=1500),
        critical_evidence=[], facts=list(facts), contradictions=list(contradictions), **kw)


def _listing(**kw):
    base = {"id": 1, "site": "facebook", "url": "u", "year": 2000, "make": "BMW", "model": "Z3 3.0i", "trim": "",
            "transmission": "Manual", "mileage": 54000, "price": 15500, "price_kind": "asking", "location": "Seattle, WA",
            "engine_liters": 3.0, "photos": ["p"], "raw_text": "x" * 1000}
    base.update(kw)
    return base


TRIM_CONFLICT = Contradiction(topic="model/trim identity", severity="identity",
                              detail="Header says 2000 Z3 3.0i; the description says Z3M Roadster and the photos show M parts.")


def test_a_vinless_which_car_conflict_is_a_vin_question_not_a_reject():
    a = assess(_listing(), db.get_profile("z3_30i"), _ev(contradictions=[TRIM_CONFLICT]), STATE, mission="enthusiast_bridge")
    assert not any(g.kind == "hard" for g in a.gates)
    assert any(it["key"] == "identity_unverified" for it in a.open_questions["document"])
    assert a.verdict != "Reject" and a.next_step["action"] != "Skip"


def test_the_same_conflict_with_a_vin_in_hand_stays_hard():
    a = assess(_listing(vin="WBSCK9340YLC00000"), db.get_profile("z3_30i"), _ev(contradictions=[TRIM_CONFLICT]), STATE,
               mission="enthusiast_bridge")
    assert any(g.key == "identity_contradiction" and g.kind == "hard" for g in a.gates)


def test_a_mileage_conflict_is_never_softened():
    c = Contradiction(topic="mileage", severity="identity", detail="58,000 in the title, 85,000 in the description.")
    a = assess(_listing(), db.get_profile("z3_30i"), _ev(contradictions=[c]), STATE, mission="enthusiast_bridge")
    assert any(g.key == "identity_contradiction" and g.kind == "hard" for g in a.gates)


# --- 2b. re-profile from the assessment's evidence -----------------------------

def test_photo_evidence_of_an_m_roadster_points_to_the_m_profile():
    facts = [Fact(key="model", value="Z3 M Roadster (E36/7 M), not a Z3 3.0i", status="inferred", source="photo")]
    assert profile_from_evidence(facts, _listing(), db.list_profiles())["key"] == "z3_m"


def test_m_sport_packages_and_ad_text_do_not_re_profile():
    profiles = db.list_profiles()
    sport = [Fact(key="trim", value="3.0i M Sport package", status="verified", source="photo")]
    assert (profile_from_evidence(sport, _listing(year=2001), profiles) or {}).get("key") in {None, "z3_30i"}
    claimed = [Fact(key="trim", value="M Roadster", status="claimed", source="listing_text")]
    assert profile_from_evidence(claimed, _listing(), profiles) is None


def test_a_generic_body_reading_never_moves_a_car_to_a_generated_catch_all():
    db.upsert_profile({"key": "bmw_z3", "label": "BMW Z3 Roadster & Coupe", "make": "BMW", "source": "ai", "verified": False,
                       "models": ["Z3", "Z3 Roadster", "Z3 Coupe", "Z3 3.0i", "M Roadster"], "years": [1996, 2002],
                       "weights": {"value": 1.0}})
    facts = [Fact(key="model", value="Z3 roadster (E36/7)", status="verified", source="photo")]
    assert profile_from_evidence(facts, _listing(year=2001), db.list_profiles()) is None
    m = [Fact(key="model", value="Z3 M Roadster", status="inferred", source="photo")]
    assert profile_from_evidence(m, _listing(), db.list_profiles())["key"] == "z3_m"


def test_assessment_re_profiles_the_car_and_a_later_sync_keeps_it(monkeypatch):
    from scout.config import CONFIG
    import scout.ai.assess as assess_mod
    _normalizer_says(monkeypatch, year=2000, model="Z3 3.0i")
    url = "https://www.facebook.com/marketplace/item/109/"
    ingest_items("facebook", [_fb(url, FB_LIVE)], run_ai=True)
    lid = db.get_listing_by_url(url)["id"]
    assert db.get_listing(lid)["profile_key"] == "z3_30i"
    ev = _ev(facts=[Fact(key="model", value="Z3 M Roadster (E36/7 M)", status="inferred", source="photo")],
             contradictions=[TRIM_CONFLICT])
    monkeypatch.setattr(assess_mod, "interpret_listing", lambda *a, **k: ev)
    monkeypatch.setattr("scout.server.decode_vin", lambda *a, **k: None, raising=False)
    from scout.server import app
    with TestClient(app) as c:
        r = c.post(f"/api/listings/{lid}/assess")
    assert r.status_code == 200, r.text
    row = db.get_listing(lid)
    assert row["profile_key"] == "z3_m" and row["profile_source"] == "assessment" and row["mission"] == "future_keeper"
    assert r.json()["assessment"]["mission"] == "future_keeper"
    # The next sync's fast read still says "3.0i": the evidence-based profile stays.
    ingest_items("facebook", [_fb(url, FB_LIVE + "edited " * 80)], run_ai=True)
    assert db.get_listing(lid)["profile_key"] == "z3_m"


def test_a_profile_you_pick_survives_a_re_read(monkeypatch):
    _normalizer_says(monkeypatch)
    url = "https://www.facebook.com/marketplace/item/110/"
    ingest_items("facebook", [_fb(url, FB_LIVE)], run_ai=True)
    lid = db.get_listing_by_url(url)["id"]
    from scout.server import app
    with TestClient(app) as c:
        assert c.patch(f"/api/listings/{lid}", json={"profile_key": "z3_m"}).status_code == 200
    ingest_items("facebook", [_fb(url, FB_LIVE + "edited " * 80)], run_ai=True)
    row = db.get_listing(lid)
    assert row["profile_key"] == "z3_m" and row["profile_source"] == "user"


# --- 3. comps with no transmission read; thin sets cannot Skip -----------------

def _sale(price, trans="Manual", site="carsandbids", miles=60000, year=2001, **kw):
    return {"id": None, "site": site, "year": year, "model": "Z3 3.0i roadster", "trim": "", "body_style": "Roadster",
            "transmission": trans, "mileage": miles, "price": price, "price_kind": "sold", "availability": "sold",
            "sold_price": price, **kw}


def test_comps_whose_transmission_was_never_read_still_count():
    listing = {"id": 1, "year": 2001, "model": "Z3 3.0i roadster", "body_style": "Roadster", "transmission": "Manual", "mileage": 60000}
    rows = [_sale(14000), _sale(15000), _sale(16000), _sale(17000, trans="Unknown"), _sale(18000, trans="")]
    fv = fair_value(listing, rows)
    assert fv["n"] == 5 and fv["relaxed"] == []


def test_classified_last_asks_count_at_a_discount_and_hammers_with_the_buyer_fee():
    listing = {"id": 1, "year": 2001, "model": "Z3 3.0i roadster", "body_style": "Roadster", "transmission": "Manual", "mileage": 60000}
    hammers = fair_value(listing, [_sale(20000) for _ in range(4)])
    asks = fair_value(listing, [_sale(20000, site="cargurus") for _ in range(4)])
    assert hammers["mid"] == 21000 and asks["mid"] == round(20000 * 0.93)   # 1.9.3: hammer + 5% fee


def _costs_case(n_comps: int):
    """A car asking well over a value walk-away read off n comps."""
    fair = {"mid": 12000, "low": 11000, "high": 12500, "n": n_comps, "basis": "sold", "relaxed": [], "note": ""}
    return assess(_listing(year=2001, price=16000, location="Carmel, CA"), db.get_profile("z3_30i"), _ev(), STATE,
                  fair=fair, mission="enthusiast_bridge")


def test_a_thin_comp_set_says_watch_not_skip():
    a = _costs_case(5)
    assert a.costs.max_price_basis == "value" and a.costs.price > 1.15 * a.costs.max_price
    assert a.next_step["action"] == "Watch" and "only 5 sales" in a.next_step["reason"]


def test_a_solid_comp_set_can_skip():
    assert _costs_case(8).next_step["action"] == "Skip"


# --- 5. mileage fit for keepers -------------------------------------------------

def test_keeper_mileage_caps_mission_fit():
    prof = db.get_profile("z3_m")
    base = dict(model="Z3 M roadster", year=1998, price=13000)
    low = assess(_listing(mileage=90000, **base), prof, _ev(), STATE, mission="future_keeper")
    mid = assess(_listing(mileage=130000, **base), prof, _ev(), STATE, mission="future_keeper")
    high = assess(_listing(mileage=152000, **base), prof, _ev(), STATE, mission="future_keeper")
    assert low.score.mission_fit == 12 and mid.score.mission_fit == 9 and high.score.mission_fit == 6
    assert high.merit < low.merit
    bridge = assess(_listing(mileage=152000, year=2001, price=13000), db.get_profile("z3_30i"), _ev(), STATE,
                    mission="enthusiast_bridge")
    assert bridge.score.mission_fit == 12          # bridges unchanged
