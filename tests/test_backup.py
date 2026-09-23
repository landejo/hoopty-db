import sqlite3

from scout import db
from scout.backup import backup_db


def test_backup_copies_db_and_prunes(tmp_path):
    with db.connect() as c:
        c.execute("INSERT INTO settings (key, value_json, updated_at) VALUES ('k', '{}', 'now')")
    out = tmp_path / "bk"
    first = backup_db("test", dest_dir=out, keep=2)
    assert first and first.exists()
    with sqlite3.connect(str(first)) as c:
        assert c.execute("SELECT count(*) FROM settings WHERE key='k'").fetchone()[0] == 1
    for i in range(3):
        (out / f"scout_20000101-00000{i}_old.db").write_bytes(b"")
    backup_db("test", dest_dir=out, keep=2)
    assert len(list(out.glob("scout_*.db"))) == 2
