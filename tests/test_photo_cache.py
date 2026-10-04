"""Saved photo copies (2026-10-04): Facebook photo links expire days after a sync,
so the board and assessments use a local copy that outlives the link."""
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from scout import db
from scout.ai import photos as ph

OLD = "https://scontent-sjc3-1.xx.fbcdn.net/v/t39.84726-6/753729805_1577879387013625_2703356425043941281_n.jpg?stp=dst-jpg&oe=6AB8A54E"
RESIGNED = "https://scontent-sea1-1.xx.fbcdn.net/v/t39.84726-6/753729805_1577879387013625_2703356425043941281_n.jpg?stp=dst-jpg&oe=6AC1F000"


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(ph, "_CACHE_DIR", tmp_path / "photo_cache")
    return tmp_path / "photo_cache"


def _jpeg(w=1200, h=800):
    buf = io.BytesIO(); Image.new("RGB", (w, h), (120, 130, 140)).save(buf, format="JPEG"); return buf.getvalue()


def test_a_resigned_facebook_link_finds_the_saved_copy(cache, monkeypatch):
    cache.mkdir()
    (cache / f"{ph.cache_key(OLD)}.jpg").write_bytes(_jpeg())
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no download")))
    assert ph.cache_key(OLD) == ph.cache_key(RESIGNED)
    assert ph.fetch_cached(RESIGNED) is not None and len(ph.photo_blocks([RESIGNED])) == 1


def test_photo_endpoint_serves_saved_copies_of_tracked_photos_only(cache, monkeypatch):
    from scout.server import app
    cache.mkdir()
    (cache / f"{ph.cache_key(OLD)}.jpg").write_bytes(_jpeg())
    db.upsert_listing({"site": "facebook", "url": "https://www.facebook.com/marketplace/item/1/", "title": "1999 BMW M Roadster",
                       "role": "candidate", "availability": "active", "photos": [OLD, "https://scontent.xx.fbcdn.net/v/t1/9_9_9_n.jpg?oe=1"]})
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("403 expired")))
    with TestClient(app) as c:
        r = c.get("/api/photo", params={"u": OLD})
        assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
        assert c.get("/api/photo", params={"u": "https://example.com/anything.jpg"}).status_code == 404   # not an open proxy
        assert c.get("/api/photo", params={"u": "https://scontent.xx.fbcdn.net/v/t1/9_9_9_n.jpg?oe=1"}).status_code == 404   # expired, no copy


def test_publish_writes_a_card_image_from_the_saved_copy(cache, tmp_path):
    from scout.publish import _stage_site
    cache.mkdir()
    (cache / f"{ph.cache_key(OLD)}.jpg").write_bytes(_jpeg(1600, 1000))
    index = {"listings": [{"id": 7, "thumb": OLD}, {"id": 8, "thumb": None}]}
    (tmp_path / "stage").mkdir()
    sizes = _stage_site(tmp_path / "stage", index, {7: {"photos": [OLD]}, 8: {}})
    assert sizes["card_images"] == 1 and index["listings"][0]["thumb_file"] == "data/t/7.jpg"
    with Image.open(tmp_path / "stage" / "data" / "t" / "7.jpg") as im:
        assert max(im.size) == 640
    assert "thumb_file" not in index["listings"][1]
