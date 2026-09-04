import pytest

from scout import db


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    from scout.profiles import sync_seed_profiles
    sync_seed_profiles()
    yield path
