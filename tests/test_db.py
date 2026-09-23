from scout import db


def test_upsert_listing_survives_concurrent_insert_race(monkeypatch):
    """The deferred-ingest AI path runs off the request thread with no lock:
    two near-simultaneous upserts for the same new URL can both see no
    existing row and both attempt an INSERT. Simulate the loser's view of the
    world (existence check says "not there yet") after a winner has already
    inserted the row, and confirm upsert_listing falls back to an update
    instead of raising sqlite3.IntegrityError."""
    url = "https://example.com/car/race"
    with db.connect() as c:
        c.execute(
            "INSERT INTO listings (site, url, first_seen, last_seen, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("facebook", url, db.now(), db.now(), db.now()),
        )
    monkeypatch.setattr(db, "get_listing_by_url", lambda u, path=None: None)

    lid, created = db.upsert_listing({"url": url, "site": "facebook", "title": "raced"})

    assert created is False
    rows = [r for r in db.list_listings() if r["url"] == url]
    assert len(rows) == 1
    assert rows[0]["id"] == lid
    assert rows[0]["title"] == "raced"
