from scout import db
from scout.ingest import detect_availability, ingest_items, parse_price


def _item(url, title="2001 BMW Z3 M Coupe", price="$42,000", sold=False, text="lots of text " * 20):
    return {"url": url, "title": title, "price_text": price, "sold": sold,
            "card_text": f"{title}\n{price}", "detail": {"text": text, "photos": ["https://x/1.jpg"]}}


def test_detect_availability():
    assert detect_availability({"sold": True}, "facebook") == "sold"
    assert detect_availability({"card_text": "x", "price_text": "Sold $1,000"}, "facebook") == "sold"
    assert detect_availability({"price_text": "Bid to $30,000"}, "bat") == "ended"
    assert detect_availability({"price_text": "Bid to $30,000"}, "facebook") == "active"
    assert detect_availability({"price_text": "$30,000"}, "carscom") == "active"
    assert detect_availability({"pending": True, "price_text": "$30,000"}, "facebook") == "pending"
    assert detect_availability({"badge": "Sale pending"}, "carscom") == "pending"


def test_parse_price():
    assert parse_price("$24,500") == 24500
    assert parse_price("Sold for $31,000") == 31000
    assert parse_price("$12") is None
    assert parse_price(None) is None


def test_sold_becomes_comp_and_active_becomes_candidate():
    stats = ingest_items("facebook", [
        _item("https://www.facebook.com/marketplace/item/1/"),
        _item("https://www.facebook.com/marketplace/item/2/", sold=True),
    ], include_sold=True, run_ai=False)
    assert stats["created"] == 2 and stats["candidates"] == 1 and stats["comps"] == 1
    rows = {r["url"]: r for r in db.list_listings()}
    assert rows["https://www.facebook.com/marketplace/item/1/"]["role"] == "candidate"
    assert rows["https://www.facebook.com/marketplace/item/2/"]["role"] == "comp"
    assert rows["https://www.facebook.com/marketplace/item/2/"]["availability"] == "sold"


def test_skip_sold_toggle():
    stats = ingest_items("bat", [_item("https://bringatrailer.com/listing/a/", sold=True)], include_sold=False, run_ai=False)
    assert stats["skipped_sold"] == 1 and stats["created"] == 0


def test_resync_records_price_change_and_marks_removed():
    ingest_items("carscom", [_item("https://www.cars.com/vehicledetail/a/", price="$20,000"),
                             _item("https://www.cars.com/vehicledetail/b/", price="$21,000")], run_ai=False)
    ingest_items("carscom", [_item("https://www.cars.com/vehicledetail/a/", price="$19,000")], run_ai=False, full_sync=True)
    a = db.get_listing_by_url("https://www.cars.com/vehicledetail/a/")
    b = db.get_listing_by_url("https://www.cars.com/vehicledetail/b/")
    assert a["price"] == 19000
    assert [s["price"] for s in db.list_snapshots(a["id"])] == [20000, 19000]
    assert b["availability"] == "removed"
    # Snapshot dedupe: an unchanged resync adds no row.
    ingest_items("carscom", [_item("https://www.cars.com/vehicledetail/a/", price="$19,000")], run_ai=False)
    assert len(db.list_snapshots(a["id"])) == 2


def test_candidate_that_sells_becomes_comp_and_never_reverts():
    url = "https://carsandbids.com/auctions/abc/2001-bmw-z3-m"
    ingest_items("carsandbids", [_item(url)], run_ai=False)
    ingest_items("carsandbids", [_item(url, sold=True)], run_ai=False)
    assert db.get_listing_by_url(url)["role"] == "comp"
    ingest_items("carsandbids", [_item(url)], run_ai=False)  # stale scrape says active again
    assert db.get_listing_by_url(url)["role"] == "comp"


def test_single_add_never_marks_others_removed():
    ingest_items("facebook", [_item("https://www.facebook.com/marketplace/item/1/"),
                              _item("https://www.facebook.com/marketplace/item/2/")], run_ai=False, full_sync=True)
    ingest_items("facebook", [_item("https://www.facebook.com/marketplace/item/3/")], run_ai=False)
    assert all(r["availability"] == "active" for r in db.list_listings())
    # Touch-only full sync (URL list without details) marks the missing one removed and keeps the rest.
    stats = ingest_items("facebook", [{"url": "https://www.facebook.com/marketplace/item/1/", "_touch": True},
                                      {"url": "https://www.facebook.com/marketplace/item/3/", "_touch": True}], run_ai=False, full_sync=True)
    assert stats["marked_removed"] == 1
    rows = {r["url"][-2]: r["availability"] for r in db.list_listings()}
    assert rows == {"1": "active", "2": "removed", "3": "active"}


def test_vanished_listing_takes_result_from_its_page_or_becomes_removed():
    ingest_items("bat", [_item("https://bringatrailer.com/listing/x/", price="$30,000")], run_ai=False, full_sync=True)
    ingest_items("bat", [_item("https://bringatrailer.com/listing/y/", price="$10,000")], run_ai=False)
    # Sync: x is gone from the watchlist but its page says Sold for; y's page shows nothing useful.
    sold = _item("https://bringatrailer.com/listing/x/", price="")
    sold["_vanished"] = True
    sold["detail"]["status_text"] = "Sold for USD $31,500 on 9/3/2026"
    gone = _item("https://bringatrailer.com/listing/y/", price="")
    gone["_vanished"] = True
    ingest_items("bat", [sold, gone], run_ai=False)
    x = db.get_listing_by_url("https://bringatrailer.com/listing/x/")
    y = db.get_listing_by_url("https://bringatrailer.com/listing/y/")
    assert x["availability"] == "sold" and x["role"] == "comp"
    assert y["availability"] == "removed" and y["role"] == "candidate"


def test_bot_wall_page_is_never_stored_as_listing_text():
    from scout.ingest import is_blocked
    wall = {"text": "www.cars.com\nVerify you are human by completing the action below.\nJust a moment..."}
    assert is_blocked(wall) and is_blocked({"blocked": True}) and not is_blocked({"text": "2000 BMW M Roadster " * 50})
    item = _item("https://www.cars.com/vehicledetail/w/", title="2000 BMW M Roadster", text=wall["text"])
    stats = ingest_items("carscom", [item], run_ai=False)
    row = db.get_listing_by_url("https://www.cars.com/vehicledetail/w/")
    assert stats["blocked"] == 1
    assert "Verify you are human" not in (row["raw_text"] or "")
    assert row["raw_text"].startswith("2000 BMW M Roadster")  # card text kept as the fallback
    assert row["raw"].get("blocked") is True


def test_ignored_listing_never_comes_back_as_candidate():
    url = "https://www.facebook.com/marketplace/item/kayak/"
    ingest_items("facebook", [_item(url, title="Two kayaks with paddles", price="$450")], run_ai=False)
    lid = db.get_listing_by_url(url)["id"]
    db.update_listing(lid, {"role": "ignored"})
    ingest_items("facebook", [_item(url, title="Two kayaks with paddles", price="$400")], run_ai=False)
    assert db.get_listing_by_url(url)["role"] == "ignored"
    from scout.ingest import rescore_listing
    assert rescore_listing(lid) is None


def test_normalizer_skip_marks_not_a_vehicle(monkeypatch):
    from scout import ingest as ing
    from scout.config import CONFIG
    monkeypatch.setattr(CONFIG, "anthropic_api_key", "test-only-never-called")
    import scout.ai.normalize as nz
    monkeypatch.setattr(nz, "normalize_listing", lambda *a, **k: {"is_vehicle": False, "profile_key": "skip", "prelim_summary": "kayaks"})
    url = "https://www.facebook.com/marketplace/item/kayak2/"
    ing.ingest_items("facebook", [_item(url, title="Two kayaks", price="$450")], run_ai=True)
    row = db.get_listing_by_url(url)
    assert row["role"] == "ignored" and row["normalized"]["ignored_reason"].startswith("not a vehicle")


def test_pending_stays_a_candidate_until_sold():
    url = "https://www.facebook.com/marketplace/item/p/"
    ingest_items("facebook", [dict(_item(url), pending=True)], run_ai=False)
    row = db.get_listing_by_url(url)
    assert row["availability"] == "pending" and row["role"] == "candidate"
    ingest_items("facebook", [_item(url, sold=True)], run_ai=False)
    assert db.get_listing_by_url(url)["role"] == "comp"


def test_auto_mission_follows_profile_until_user_sets_it(monkeypatch):
    from scout import ingest as ing
    from scout.config import CONFIG
    monkeypatch.setattr(CONFIG, "anthropic_api_key", "test-only-never-called")
    import scout.ai.normalize as nz
    monkeypatch.setattr(nz, "normalize_listing", lambda *a, **k: {"is_vehicle": True, "year": 2000, "make": "BMW", "model": "M roadster", "profile_key": "z3_m", "prelim_summary": "x"})
    monkeypatch.setattr("scout.vin.decode_vin", lambda *a, **k: None)
    url = "https://www.cars.com/vehicledetail/m/"
    ing.ingest_items("carscom", [_item(url)], run_ai=True)
    row = db.get_listing_by_url(url)
    assert row["mission"] == "future_keeper" and not row["mission_user_set"]
    db.update_listing(row["id"], {"mission": "pragmatic_bridge", "mission_user_set": 1})
    ing.ingest_items("carscom", [_item(url, text="changed " * 200)], run_ai=True)
    assert db.get_listing_by_url(url)["mission"] == "pragmatic_bridge"
