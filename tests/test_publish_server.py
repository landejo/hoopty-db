import json

from fastapi.testclient import TestClient

from scout import db
from scout.ingest import ingest_items
from scout.publish import build_export, scrub_listing, write_export


def test_scrub_strips_private_seller_and_contact():
    row = {"id": 1, "url": "u", "seller_type": "Private", "seller_name": "Jane Doe",
           "seller_contact": "555-1212", "vin": "WBS", "raw_text": "secret",
           "raw": {"seller_phone": "555", "bid_text": "$1"}}
    out = scrub_listing(row)
    assert "seller_name" not in out and "seller_contact" not in out
    assert "vin" not in out and "raw_text" not in out
    assert out["raw"] == {"bid_text": "$1"}
    row["seller_type"] = "Dealer"
    assert scrub_listing(row)["seller_name"] == "Jane Doe"


def test_export_shape_and_market_percentile(tmp_path):
    ingest_items("bat", [
        {"url": "https://bringatrailer.com/listing/a/", "title": "2002 BMW Z3 M Coupe", "price_text": "Sold for $50,000", "detail": {"text": "x" * 300}},
        {"url": "https://bringatrailer.com/listing/b/", "title": "2001 BMW Z3 M Coupe", "price_text": "$45,000", "detail": {"text": "y" * 300}},
    ], include_sold=True, run_ai=False)
    for r in db.list_listings():
        db.update_listing(r["id"], {"profile_key": "z3_m", "sold_price": 50000 if r["role"] == "comp" else None})
    data = build_export()
    assert set(data) == {"generated_at", "policy_version", "sites", "profiles", "markets", "listings"}
    assert all("assessment" in l for l in data["listings"])
    assert data["markets"]["z3_m"]["sold_count"] == 1
    active = next(l for l in data["listings"] if l["role"] == "candidate")
    assert active["price_pct_vs_sold"] == 0
    assert active["history"][0]["price"] == 45000
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
