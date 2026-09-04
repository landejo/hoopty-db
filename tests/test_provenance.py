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


def test_fingerprint_never_merges_but_vin_does():
    # Two red 2000 M Roadsters with no VIN are two cars until a VIN says otherwise.
    a, _ = db.upsert_listing({"site": "facebook", "url": "https://www.facebook.com/marketplace/item/1/", "year": 2000, "make": "BMW",
                              "model": "Z3 M roadster", "exterior_color": "Imola Red", "price": 8800, "availability": "active", "role": "candidate"})
    b, _ = db.upsert_listing({"site": "facebook", "url": "https://www.facebook.com/marketplace/item/2/", "year": 2000, "make": "BMW",
                              "model": "Z3 M roadster", "exterior_color": "Imola Red", "price": 25000, "availability": "active", "role": "candidate"})
    db.add_snapshot(a, 8800, "asking", "active"); db.add_snapshot(b, 25000, "asking", "active")
    va, vb = link_listing_vehicle(a), link_listing_vehicle(b)
    assert va != vb and fingerprint(db.get_listing(a)) == fingerprint(db.get_listing(b))
    # The same VIN on two venues IS one car: a dealer cross-post.
    c, _ = db.upsert_listing({"site": "cargurus", "url": "https://www.cargurus.com/details/1/", "vin": VIN, "year": 2000, "make": "BMW",
                              "model": "Z3 M roadster", "price": 17500, "availability": "active", "role": "candidate"})
    d, _ = db.upsert_listing({"site": "carscom", "url": "https://www.cars.com/vehicledetail/d/", "vin": VIN, "year": 2000, "make": "BMW",
                              "model": "Z3 M roadster", "price": 17400, "availability": "active", "role": "candidate"})
    db.add_snapshot(c, 17500, "asking", "active"); db.add_snapshot(d, 17400, "asking", "active")
    assert link_listing_vehicle(c) == link_listing_vehicle(d)
    # A provisional record that later learns its VIN merges into the VIN record and keeps its events.
    db.update_listing(a, {"vin": VIN})
    assert link_listing_vehicle(a) == db.get_listing(c)["vehicle_id"]
    assert db.get_vehicle(va) is None  # provisional row is gone
    urls = {e["url"] for e in db.vehicle_events(db.get_listing(c)["vehicle_id"])}
    assert "https://www.facebook.com/marketplace/item/1/" in urls and "https://www.cargurus.com/details/1/" in urls
    # idempotent
    n = len(db.vehicle_events(db.get_listing(c)["vehicle_id"]))
    link_listing_vehicle(a); link_listing_vehicle(c)
    assert len(db.vehicle_events(db.get_listing(c)["vehicle_id"])) == n


def test_repair_splits_old_fingerprint_merges():
    from scout.provenance import repair_vehicle_links
    a, _ = db.upsert_listing({"site": "facebook", "url": "https://www.facebook.com/marketplace/item/1/", "year": 2000, "make": "BMW",
                              "model": "Z3 M roadster", "exterior_color": "Imola Red", "price": 8800, "availability": "active", "role": "candidate"})
    b, _ = db.upsert_listing({"site": "facebook", "url": "https://www.facebook.com/marketplace/item/2/", "year": 2000, "make": "BMW",
                              "model": "Z3 M roadster", "exterior_color": "Imola Red", "price": 25000, "availability": "active", "role": "candidate"})
    vid = db.upsert_vehicle(None, "2000|bmw|z3mroadster|imolared", db.get_listing(a))
    db.update_listing(a, {"vehicle_id": vid}); db.update_listing(b, {"vehicle_id": vid})  # the old, wrong state
    r = repair_vehicle_links()
    assert r["split"] == 1
    assert db.get_listing(a)["vehicle_id"] != db.get_listing(b)["vehicle_id"]


def test_site_reported_drop_becomes_an_event_without_a_fake_date():
    a, _ = db.upsert_listing({"site": "cargurus", "url": "https://www.cargurus.com/details/9/", "year": 1998, "make": "BMW", "model": "Z3 M roadster",
                              "price": 21353, "availability": "active", "role": "candidate", "listing_date": "2026-08-20",
                              "normalized": {"price_drops": [{"prior_price": 26353, "amount": 5000, "note": "Price drop -$5,000"}]}})
    db.add_snapshot(a, 21353, "asking", "active")
    vid = link_listing_vehicle(a)
    ev = [e for e in db.vehicle_events(vid) if e["status"] == "Price reduced"]
    assert len(ev) == 1 and ev[0]["source"] == "site_reported" and "26,353" in ev[0]["evidence"] and "date of the drop not stated" in ev[0]["evidence"]
    link_listing_vehicle(a)
    assert len([e for e in db.vehicle_events(vid) if e["status"] == "Price reduced"]) == 1
