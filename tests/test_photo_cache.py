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


def test_server_starts_with_the_photo_worker_on(monkeypatch):
    """2026-10-04: a local `import threading` later in _startup made the worker start raise
    UnboundLocalError; the tests had the worker off and missed it."""
    from scout import server
    started = []
    monkeypatch.setenv("SCOUT_PHOTO_CACHE", "1")
    monkeypatch.setattr(server, "_photo_worker", lambda: started.append(1))
    with TestClient(server.app) as c:
        assert c.get("/api/health").status_code == 200
    assert started == [1]


# ---------- CARFAX padding (2026-10-23): its CDN pads small photos onto a white 1024x768 ----------

CFX = "https://carfax-img.vast.com/carfax/v2/8479222255141981448/1/1024x768"


def _padded(w=1024, h=768, inner=(640, 360)):
    img = Image.new("RGB", (w, h), (255, 255, 255))
    img.paste(Image.new("RGB", inner, (40, 70, 140)), ((w - inner[0]) // 2, (h - inner[1]) // 2))
    buf = io.BytesIO(); img.save(buf, format="JPEG", quality=95); return buf.getvalue()


class _Resp(io.BytesIO):
    headers = {"Content-Type": "image/jpeg"}
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _size(data):
    return Image.open(io.BytesIO(data)).size


def test_a_carfax_photo_is_saved_without_its_white_padding(cache, monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Resp(_padded()))
    w, h = _size(ph.fetch_cached(CFX))
    assert abs(w - 640) <= 2 and abs(h - 360) <= 2          # JPEG edges blur a pixel or two
    assert _size(ph.cached_path(CFX).read_bytes()) == (w, h)


def test_other_sites_photos_keep_their_borders(cache, monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Resp(_padded()))
    assert _size(ph.fetch_cached("https://images.autotrader.com/x/1.jpg")) == (1024, 768)


def test_trim_saved_crops_padded_carfax_copies_only(cache):
    cache.mkdir()
    other = "https://images.autotrader.com/x/2.jpg"
    clean = "https://carfax-img.vast.com/carfax/v2/1/2/1024x768"
    for u, data in ((CFX, _padded()), (other, _padded()), (clean, _jpeg(1024, 768))):
        (cache / f"{ph.cache_key(u)}.jpg").write_bytes(data)
    assert ph.trim_saved([CFX, other, clean, "https://carfax-img.vast.com/never/saved"]) == 1
    assert _size(ph.cached_path(CFX).read_bytes())[0] <= 642
    assert _size(ph.cached_path(other).read_bytes()) == (1024, 768)
    assert _size(ph.cached_path(clean).read_bytes()) == (1024, 768)
    assert ph.trim_saved([CFX]) == 0                           # already cropped: left alone


def test_a_mostly_white_photo_is_not_cropped_to_a_sliver(cache, monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Resp(_padded(inner=(200, 150))))
    assert _size(ph.fetch_cached(CFX)) == (1024, 768)
