import json

from fastapi.testclient import TestClient

from scout import db
from scout.ingest import ingest_items
from scout.publish import build_export, find_leaks, redact_public, scrub_listing, write_export


def test_scrub_strips_private_seller_and_contact():
    row = {"id": 1, "url": "u", "seller_type": "Private", "seller_name": "Jane Doe",
           "seller_contact": "555-1212", "vin": "WBS", "raw_text": "secret",
           "raw": {"seller_phone": "555", "time_left": "2 days"}}
    out = scrub_listing(row)
    assert "seller_name" not in out and "seller_contact" not in out
    assert "vin" not in out and "raw_text" not in out
    assert out["raw"] == {"time_left": "2 days"}
    row["seller_type"] = "Dealer"
    assert scrub_listing(row)["seller_name"] == "Jane Doe"


def test_scrub_drops_free_text_raw_fields():
    row = {"id": 1, "url": "u", "raw": {"status_text": "Call John at 831-555-1234", "essentials": "VIN 5TDZA23A15S123456",
                                         "time_left": "2 days", "bid_count": 4, "auction_end_text": "closes soon"}}
    out = scrub_listing(row)
    assert out["raw"] == {"time_left": "2 days"}


def test_redact_public_strips_vin_phone_email():
    export = {
        "listings": [{
            "url": "https://www.autotrader.com/marketplace/buy/5TDZA23A15S123456",
            "raw": {"time_left": "call (831) 555-1234 or 831.555.9999"},
            "notes": "seller email is jane.doe@example.com, VIN 5TDZA23A15S123456",
            "timeline": [{"url": "https://www.autotrader.com/marketplace/buy/5TDZA23A15S123456"}],
            "assessment": {
                "evidence": {
                    "rationale": "VIN 5TDZA23A15S123456 confirms it, call +1 831 555 1234",
                    "critical_evidence": [{"evidence": "seller phone 831-555-1234... contact them"}],
                    "facts": [{"key": "vin", "value": "5TDZA23A15S123456"}, {"key": "mileage", "value": "88000"}],
                },
            },
            "provenance": {"note": "matched via VIN 5TDZA23A15S123456"},
            "seller_questions": ["ask about vin 5TDZA23A15S123456"],
        }],
    }
    out = redact_public(export)
    l = out["listings"][0]
    assert "5TDZA23A15S123456" not in json.dumps(out)
    assert "[VIN]" in l["url"] and "[VIN]" in l["timeline"][0]["url"]
    assert "[phone]" in l["raw"]["time_left"]
    assert "[email]" in l["notes"] and "[VIN]" in l["notes"]
    assert "[VIN]" in l["assessment"]["evidence"]["rationale"] and "[phone]" in l["assessment"]["evidence"]["rationale"]
    assert "[phone]" in l["assessment"]["evidence"]["critical_evidence"][0]["evidence"]
    assert "[VIN]" in l["provenance"]["note"]
    facts = {f["key"]: f.get("value") for f in l["assessment"]["evidence"]["facts"]}
    assert facts["vin"] is None and facts["mileage"] == "88000"
    assert find_leaks(out) == []


def test_redact_public_leaves_non_leaks_alone():
    safe = {
        "price": "$12,345", "mileage": "123,456 mi", "year": "2019", "listed": "2026-09-22",
        "generated_at": "2026-09-22T10:00:00+00:00", "no_digit_17": "ABCDEFGHJKLMNPRST",
        "thumb_hash": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",
    }
    out = redact_public(dict(safe))
    assert out == safe
    assert find_leaks(out) == []


def test_find_leaks_empty_on_seeded_export():
    ingest_items("carscom", [{"url": "https://www.cars.com/vehicledetail/vin-5TDZA23A15S123456/", "title": "2015 Lexus GX 460",
                               "price_text": "$18,000", "detail": {"text": "x" * 300,
                               "status_text": "seller phone is 831-555-1234, ask for Jane at jane@example.com"}}],
                 run_ai=False)
    lid = db.list_listings()[0]["id"]
    db.update_listing(lid, {"notes": "VIN is 5TDZA23A15S123456, call 831-555-1234", "vin": "5TDZA23A15S123456"})
    export = build_export()
    assert find_leaks(export) == []



# Orphan gh-pages publish flow (git plumbing against a temp index + work-tree,
# force-pushed) is covered in tests/test_publish_ghpages.py, which uses a real
# tmp git repo + bare "origin" rather than mocking subprocess.run.


def test_publish_endpoint_returns_502_on_failure(monkeypatch):
    import scout.server as server
    monkeypatch.setattr(server, "git_publish", lambda: {"ok": False, "changed": False, "detail": "aborted: possible leak at $.notes"})
    with TestClient(server.app) as c:
        r = c.post("/api/publish")
        assert r.status_code == 502 and "leak" in r.json()["detail"]


def test_export_shape_and_market_percentile(tmp_path):
    ingest_items("bat", [
        {"url": "https://bringatrailer.com/listing/a/", "title": "2002 BMW Z3 M Coupe", "price_text": "Sold for $50,000", "detail": {"text": "x" * 300}},
        {"url": "https://bringatrailer.com/listing/b/", "title": "2001 BMW Z3 M Coupe", "price_text": "$35,000", "detail": {"text": "y" * 300}},
    ], include_sold=True, run_ai=False)
    for r in db.list_listings():
        db.update_listing(r["id"], {"profile_key": "z3_m", "sold_price": 50000 if r["role"] == "comp" else None})
    data = build_export()
    assert set(data) == {"generated_at", "policy_version", "calibration", "sites", "profiles", "markets", "listings"}
    assert data["calibration"]["samples"] == 0 and data["calibration"]["offset"] is None
    assert all("assessment" in l for l in data["listings"])
    assert data["markets"]["z3_m"]["sold_count"] == 1
    active = next(l for l in data["listings"] if l["role"] == "candidate")
    assert active["price_pct_vs_sold"] == 0
    assert active["history"][0]["price"] == 35000
    path = write_export(data, tmp_path)
    assert json.loads(path.read_text())["markets"]["z3_m"]["sold_median"] == 50000


def test_server_roundtrip():
    from scout.server import app
    with TestClient(app) as c:
        assert c.get("/api/health").json()["ok"] is True
        r = c.post("/api/ingest", json={"site": "carscom", "items": [
            {"url": "https://www.cars.com/vehicledetail/z/", "title": "2007 Lexus GX 470", "price_text": "$14,900", "detail": {"text": "t" * 300}}]})
        assert r.status_code == 200 and r.json()["created"] == 1
        lid = db.list_listings()[0]["id"]
        r = c.patch(f"/api/listings/{lid}", json={"status": "Pursue", "notes": "call Tuesday", "profile_key": "gx470"})
        assert r.status_code == 200
        row = c.get(f"/api/listings/{lid}").json()
        assert row["status"] == "Pursue" and row["profile_key"] == "gx470" and row["history"]
        assert c.patch(f"/api/listings/{lid}", json={"status": "Bogus"}).status_code == 400
        assert c.post("/api/ingest", json={"site": "ebay", "items": []}).status_code == 400
        # Analyze without an API key is a clean 400, never a paid call.
        assert c.post(f"/api/listings/{lid}/analyze").status_code == 400
        assert len(c.get("/api/profiles").json()) >= 4
        assert c.delete(f"/api/listings/{lid}").status_code == 200
        assert c.get(f"/api/listings/{lid}").status_code == 404
        assert c.delete(f"/api/listings/{lid}").status_code == 404


def test_documents_attach_and_reach_the_assessment_prompt():
    from scout.server import app
    from scout.ai.assess import _documents_block
    with TestClient(app) as c:
        c.post("/api/ingest", json={"site": "facebook", "items": [
            {"url": "https://www.facebook.com/marketplace/item/doc/", "title": "2008 Lexus GX470",
             "price_text": "$15,000", "detail": {"text": "t" * 900}}]})
        lid = db.get_listing_by_url("https://www.facebook.com/marketplace/item/doc/")["id"]
        r = c.post(f"/api/listings/{lid}/documents", json={"kind": "carfax", "text": "Timing belt replaced 04/2019 at 135,102 mi", "title": "CARFAX"})
        assert r.status_code == 200 and r.json()["chars"] == 42
        assert c.post(f"/api/listings/{lid}/documents", json={"kind": "bogus", "text": "x"}).status_code == 400
        assert c.post(f"/api/listings/{lid}/documents", json={"kind": "carfax", "text": "  "}).status_code == 400
        block = _documents_block(lid)
        assert "GOLD-TIER" in block and "Timing belt replaced 04/2019" in block
        # re-attaching the same kind replaces rather than duplicates
        c.post(f"/api/listings/{lid}/documents", json={"kind": "carfax", "text": "updated report"})
        assert len(c.get(f"/api/listings/{lid}/documents").json()) == 1
        assert "updated report" in _documents_block(lid)
        doc_id = c.get(f"/api/listings/{lid}/documents").json()[0]["id"]
        assert c.delete(f"/api/documents/{doc_id}").status_code == 200
        assert _documents_block(lid) == ""


def test_evidence_gaps_split_documents_from_inspection():
    from scout.evidence import classify, gaps, request_message
    assert classify("timing_belt_water_pump", "Documented timing-belt service") == "document"
    assert classify("borescope", "Specialist borescope photos") == "inspection"
    assert classify("rust_evaluation", "Frame rust evaluated") == "inspection"
    assert classify("cooling_history", "Cooling-system receipts") == "document"
    from scout.ingest import ingest_items
    ingest_items("facebook", [{"url": "https://www.facebook.com/marketplace/item/gap/", "title": "2007 Lexus GX470",
                               "price_text": "$14,000", "detail": {"text": "x" * 900}}], run_ai=False)
    lid = db.get_listing_by_url("https://www.facebook.com/marketplace/item/gap/")["id"]
    db.update_listing(lid, {"profile_key": "gx470"})
    db.add_assessment(lid, {"policy_version": "1.2.1", "mission": "utility_capability", "verdict": "Maybe / verify",
                            "score": {"total": 45, "documentation": 5}, "confidence": 10, "model": "test",
                            "assessed_at": "2026-09-08T00:00:00+00:00",
                            "evidence": {"critical_evidence": [
                                {"key": "timing_belt_water_pump", "status": "missing", "evidence": ""},
                                {"key": "rust_evaluation", "status": "claimed_only", "evidence": ""}],
                                "seller_questions": []}})
    g = gaps(lid)
    assert [x["key"] for x in g["resolvable_by_document"]] == ["timing_belt_water_pump"]
    assert [x["key"] for x in g["inspection_only"]] == ["rust_evaluation"]
    assert g["blocked_on_vin"] is True and g["estimated_score_gain"] == 5 and g["potential_score"] == 50
    msg = request_message(lid)
    assert "VIN" in msg and "Carfax" in msg and "pre-purchase inspection" in msg
