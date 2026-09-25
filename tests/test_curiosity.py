"""Curiosities: followed out of interest, not serious candidates (over the
curiosity line, default $40,000, or set by hand)."""
from fastapi.testclient import TestClient

from scout import curiosity, db
from scout.ingest import ingest_items


def _add(url, price, site="facebook", **extra):
    ingest_items(site, [{"url": url, "title": "1994 Porsche 911 Carrera 4", "price_text": price,
                         "card_text": "1994 Porsche 911\n" + price, "detail": {"text": "1994 Porsche 911 Carrera 4 " * 40}}], run_ai=False)
    row = db.get_listing_by_url(url)
    if extra:
        db.update_listing(row["id"], extra)
    return db.get_listing(row["id"])


def test_a_car_over_the_line_is_a_curiosity_and_comes_back_when_the_price_drops():
    url = "https://www.facebook.com/marketplace/item/911/"
    assert _add(url, "$131,964")["role"] == "curiosity"
    ingest_items("facebook", [{"url": url, "price_text": "$38,500", "card_text": "x"}], run_ai=False)
    assert db.get_listing_by_url(url)["role"] == "candidate"


def test_expected_hammer_counts_not_just_an_early_bid():
    row = _add("https://bringatrailer.com/listing/911/", "$25,000", site="bat")
    assert row["role"] == "candidate"
    db.add_assessment(row["id"], {"costs": {"price_basis": "expected_hammer", "price": 60000}, "verdict": "Maybe / verify",
                                  "policy_version": "x", "mission": "future_keeper", "score": {"total": 50}, "confidence": 30,
                                  "model": "t", "assessed_at": "2026-09-25T00:00:00+00:00"})
    assert curiosity.sync(row["id"]) == "curiosity"


def test_a_role_you_set_wins_both_ways():
    from scout.server import app
    over = _add("https://www.facebook.com/marketplace/item/9111/", "$55,000")
    under = _add("https://www.facebook.com/marketplace/item/9112/", "$18,000")
    with TestClient(app) as c:
        c.patch(f"/api/listings/{over['id']}", json={"role": "candidate"})     # I am serious about this one
        c.patch(f"/api/listings/{under['id']}", json={"role": "curiosity"})    # just curious
    assert curiosity.sync_all() == []
    assert db.get_listing(over["id"])["role"] == "candidate" and db.get_listing(under["id"])["role"] == "curiosity"


def test_moving_the_line_on_the_policy_page_reclassifies():
    from scout.server import app
    row = _add("https://www.facebook.com/marketplace/item/9113/", "$45,000")
    assert row["role"] == "curiosity"
    with TestClient(app) as c:
        r = c.put("/api/settings", json={"curiosity_over_price": 50000}).json()
    assert db.get_listing(row["id"])["role"] == "candidate" and r["curiosity_changes"][0]["role"] == "candidate"


def test_curiosities_stay_out_of_tier_reassess_and_become_comps_when_sold(monkeypatch):
    from scout import availability, server
    from scout.config import CONFIG
    row = _add("https://www.facebook.com/marketplace/item/9114/", "$131,000", profile_key="z3_m")
    monkeypatch.setattr(CONFIG, "anthropic_api_key", "test")
    seen = []

    async def fake(lid, tier="full"):
        seen.append(lid)
        return {"ok": True}
    monkeypatch.setattr(server, "assess_listing", fake)
    with TestClient(server.app) as c:
        r = c.post("/api/reassess", json={"ids": [row["id"]]}).json()
    assert seen == [] and r["skipped"] == [row["id"]]
    assert row["id"] in [t["id"] for t in availability.targets()]      # still watched
    availability.apply(row["id"], {"text": "1994 Porsche 911\nSold\n$131,000"})
    assert db.get_listing(row["id"])["role"] == "comp"                 # and teaches the market once sold
