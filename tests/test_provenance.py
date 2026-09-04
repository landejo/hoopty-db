"""Provenance brief: VIN-centered records, timeline, markup arithmetic, cautious
wording, withdrawal detection. Acceptance case: WBSCK9347YLC91693."""
from datetime import date, timedelta

from scout import db
from scout.provenance import analyze, build_queries, fingerprint, link_listing_vehicle
from scout.policy.engine import assess
from scout.policy.state import DEFAULT_STATE
from tests.test_policy import _ev, _listing, _profile

VIN = "WBSCK9347YLC91693"
TODAY = date(2026, 9, 4)


def _iso(days_ago):
    return (TODAY - timedelta(days=days_ago)).isoformat()


def _current():
    return {"id": 42, "url": "https://www.facebook.com/marketplace/item/999/", "site": "facebook", "vin": VIN, "year": 2000,
            "make": "BMW", "model": "Z3 M roadster", "exterior_color": "Estoril Blue", "mileage": 78400, "price": 17500,
            "price_kind": "asking", "listing_date": _iso(4), "first_seen": _iso(4) + "T00:00:00+00:00", "availability": "active",
            "transmission": "Manual", "engine_liters": 3.2, "location": "Fresno, CA", "photos": ["p"], "raw_text": "x" * 800}


def _events():
    return [
        {"event_date": _iso(40), "venue": "Facebook Marketplace", "url": "https://www.facebook.com/marketplace/item/111/", "mileage": 78100,
         "price": 13900, "price_type": "asking", "status": "Listed", "evidence": "Advertised at $13,900", "identity_confidence": "confirmed"},
        {"event_date": _iso(33), "venue": "Facebook Marketplace", "url": "https://www.facebook.com/marketplace/item/111/", "mileage": 78100,
         "price": 13900, "price_type": "advertised_sold", "status": "Sold", "evidence": "Marked sold", "identity_confidence": "confirmed"},
        {"event_date": _iso(4), "venue": "Facebook Marketplace", "url": "https://www.facebook.com/marketplace/item/999/", "listing_id": 42,
         "mileage": 78400, "price": 17500, "price_type": "asking", "status": "Listed", "identity_confidence": "confirmed"},
        # A similar car with no unique identifier must be shown but never used.
        {"event_date": _iso(20), "venue": "Cars.com", "url": "https://www.cars.com/vehicledetail/x/", "mileage": 79000,
         "price": 9900, "price_type": "advertised_sold", "status": "Sold", "identity_confidence": "possible"},
    ]


def _statements():
    return [{"date": _iso(1), "url": "https://www.facebook.com/groups/z3/posts/777/", "venue": "Facebook group", "kind": "keep",
             "quote": "Decided not to sell, GF said I should keep", "factual": True}]


def test_acceptance_estoril_m_roadster():
    interp = {"work_before_prior_sale": ["Cooling system refresh (invoice dated before the first sale)"],
              "work_after_prior_sale": [], "cosmetic_or_preference": ["New wheels"], "repairs_correcting_faults": []}
    r = analyze(_current(), _events(), _statements(), interp, today=TODAY)
    pp = r["price_progression"]
    assert pp["reference"]["price"] == 13900 and pp["reference"]["price_type"] == "advertised_sold"
    assert pp["dollar_change"] == 3600 and abs(pp["percent_change"] - 0.259) < 0.001
    assert pp["elapsed_days"] == 29 and pp["mileage_added"] == 300
    assert "advertised at $13,900 and later marked sold; the actual transaction price is not public" in pp["reference_description"]
    assert {"major_markup", "very_recent_resale", "rapid_relisting", "not_actively_available"} <= set(r["flags"])
    assert r["current_status"]["available"] is False
    assert "Decided not to sell, GF said I should keep" in r["current_status"]["note"]
    assert r["confidence"] == "confirmed" and r["confidence_label"].startswith("Confirmed by an exact VIN")
    assert any("25.9% gross increase" in e for e in r["effect"])
    assert any("Do not pursue" in e for e in r["effect"])
    assert r["what_changed"]["work_before_prior_sale"] and not r["what_changed"]["work_after_prior_sale"]
    assert "https://www.facebook.com/groups/z3/posts/777/" in r["sources"]
    # the possible match is listed separately and did not become the reference
    assert r["possible_matches"] and all(m["price"] != 9900 for m in r["same_car_history"])


def test_asking_price_is_never_called_a_sale():
    ev = [{"event_date": _iso(60), "venue": "CarGurus", "url": "u1", "price": 16000, "price_type": "asking", "status": "Listed",
           "identity_confidence": "confirmed"}]
    r = analyze(_current(), ev, [], today=TODAY)
    assert r["price_progression"]["reference_description"].startswith("Asking price $16,000.")
    assert "very_recent_resale" not in r["flags"] and "recent_resale" not in r["flags"]  # no sale happened
    assert "rapid_relisting" in r["flags"]


def test_withdrawal_before_listing_does_not_apply():
    st = [{"date": _iso(90), "url": "u", "kind": "keep", "quote": "keeping it", "factual": True}]
    r = analyze(_current(), _events()[:2], st, today=TODAY)
    assert r["current_status"]["available"] is True


def test_provenance_drives_assessment_gate_and_ceiling():
    r = analyze(_current(), _events(), _statements(), None, today=TODAY)
    l = _listing(vin=VIN, model="Z3 M roadster", engine_liters=3.2, year=2000, price=17500, mileage=78400)
    ev = _ev(critical={"rear_structure": "satisfied", "cooling_history": "satisfied"}, quality=8)
    a = assess(l, _profile("z3_m"), ev, DEFAULT_STATE, vin_history={"provenance": r, "markup_vs_last_sale": r["price_progression"]["percent_change"]},
               mission="future_keeper")
    assert a.verdict == "Do not pursue" and any(g.key == "not_actively_available" for g in a.gates)
    assert a.score.price_value <= 8  # major markup cap
    assert a.costs.max_price <= int(13900 * 1.10)
    assert any("anchored to the last documented price" in n for n in a.costs.notes)


def test_build_queries_follow_the_brief():
    q = build_queries({**_current(), "title": "2000 BMW M Roadster Estoril Blue", "seller_name": "z3guy", "site_id": "999000111"})
    text = [x["q"] for x in q]
    assert f'"{VIN}"' in text and f'"{VIN}" BMW Z3 M roadster' in text
    assert any("78,400" in t for t in text) and any('"z3guy" BMW Z3 M roadster' in t for t in text)
    engines = {x["engine"] for x in q}
    assert {"duckduckgo", "bing", "google", "bat", "ebay_sold", "classic", "reddit", "facebook_posts", "facebook_marketplace"} <= engines
    assert len(q) <= 28


def test_one_vehicle_per_vin_and_fingerprint_merge():
    a, _ = db.upsert_listing({"site": "carscom", "url": "https://www.cars.com/vehicledetail/a/", "year": 2000, "make": "BMW",
                              "model": "Z3 M roadster", "exterior_color": "Estoril Blue", "price": 13900, "availability": "sold", "role": "comp"})
    db.add_snapshot(a, 13900, "asking", "sold")
    va = link_listing_vehicle(a)
    b, _ = db.upsert_listing({"site": "facebook", "url": "https://www.facebook.com/marketplace/item/999/", "vin": VIN, "year": 2000, "make": "BMW",
                              "model": "Z3 M roadster", "exterior_color": "Estoril Blue", "price": 17500, "availability": "active", "role": "candidate"})
    db.add_snapshot(b, 17500, "asking", "active")
    vb = link_listing_vehicle(b)
    assert va == vb  # fingerprint record absorbed the VIN
    v = db.get_vehicle(vb)
    assert v["vin"] == VIN and fingerprint(db.get_listing(a)) == v["fingerprint"]
    ev = db.vehicle_events(vb)
    statuses = [e["status"] for e in ev]
    assert statuses.count("Listed") == 2 and "Sold" in statuses
    sold = next(e for e in ev if e["status"] == "Sold")
    assert sold["price_type"] == "advertised_sold" and "transaction price is unknown" in sold["evidence"]
    # idempotent
    link_listing_vehicle(b)
    assert len(db.vehicle_events(vb)) == len(ev)


def test_mileage_going_backwards_is_a_hard_gate():
    cur = {**_current(), "mileage": 60000}
    r = analyze(cur, _events()[:2], [], None, today=TODAY)
    assert "mileage_decreased" in r["flags"] and any("LOWER" in e for e in r["effect"])
    l = _listing(vin=VIN, model="Z3 M roadster", engine_liters=3.2, year=2000, price=17500, mileage=60000)
    a = assess(l, _profile("z3_m"), _ev(critical={"rear_structure": "satisfied", "cooling_history": "satisfied"}, quality=8),
               DEFAULT_STATE, vin_history={"provenance": r}, mission="future_keeper")
    assert a.verdict == "Reject" and any(g.key == "odometer_inconsistency" for g in a.gates)
