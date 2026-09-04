import pytest

from scout import db
from scout.config import CONFIG


@pytest.fixture(autouse=True)
def no_paid_ai(monkeypatch):
    """Tests never call the API, whatever .env says."""
    monkeypatch.setattr(CONFIG, "anthropic_api_key", None)
    import scout.ai as ai
    monkeypatch.setattr(ai, "_client", None)
    yield


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    from scout.profiles import sync_seed_profiles
    sync_seed_profiles()
    yield path
