"""Status + verdict override became one field, "Your call" (2026-09-29). The migration
maps what you had set; the System Assessment is never stored in it."""
import sqlite3

from scout import db
from scout.db import your_call_for


def test_mapping():
    assert your_call_for("Pass", None, None, None) == ("Pass", None)
    assert your_call_for("New", "Reject", "mileage", None) == ("Pass", "mileage")
    assert your_call_for("Sold", "Do not pursue", "no longer available", None) == ("Pass", "no longer available")
    assert your_call_for("Pursue", None, None, None) == ("Interested", None)
    assert your_call_for("New", "Pursue conditionally", "if records check out", None) == ("Interested", "if records check out")
    assert your_call_for("Verify", "Maybe / verify", "waiting", None) == ("New", None)   # the system's word, not yours
    assert your_call_for("Sold", None, None, None) == ("New", None)
    assert your_call_for("Ended", None, None, None) == ("New", None)
    assert your_call_for("Contacted", None, None, None) == ("Contacted", None)
    assert your_call_for("Purchased", None, None, None) == ("Purchased", None)


def test_migration_on_an_old_database_is_complete_and_idempotent(temp_db):
    with sqlite3.connect(temp_db) as c:   # an older database still has the override columns
        cols = {r[1] for r in c.execute("PRAGMA table_info(listings)")}
        for col in ("verdict_override", "verdict_override_reason"):
            if col not in cols:
                c.execute(f"ALTER TABLE listings ADD COLUMN {col} TEXT")
    ids = {}
    for key, status, ov, why in [("a", "Pass", "Reject", "mileage"), ("b", "Pursue", None, None), ("c", "Sold", "Do not pursue", "gone"),
                                 ("d", "Verify", "Maybe / verify", None), ("e", "Contacted", None, None)]:
        lid, _ = db.upsert_listing({"site": "bat", "url": f"https://bringatrailer.com/listing/{key}/", "title": key, "role": "candidate",
                                    "availability": "active", "status": status})
        with sqlite3.connect(temp_db) as c:
            c.execute("UPDATE listings SET verdict_override=?, verdict_override_reason=? WHERE id=?", (ov, why, lid))
        ids[key] = lid
    with db.connect() as c:
        changes = db.migrate_your_call(c)
    got = {k: (db.get_listing(i)["status"], db.get_listing(i)["status_reason"]) for k, i in ids.items()}
    assert got == {"a": ("Pass", "mileage"), "b": ("Interested", None), "c": ("Pass", "gone"), "d": ("New", None), "e": ("Contacted", None)}
    assert {c["id"] for c in changes} == {ids["a"], ids["b"], ids["c"], ids["d"]}
    with sqlite3.connect(temp_db) as c:
        assert c.execute("SELECT COUNT(*) FROM listings WHERE verdict_override IS NOT NULL OR verdict_override_reason IS NOT NULL").fetchone()[0] == 0
    with db.connect() as c:
        assert db.migrate_your_call(c) == []   # second run: nothing to do
