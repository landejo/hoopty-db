"""The board's export is built once per database change, and the cheap
/api/data-version check tells the viewer when to fetch it again."""
from fastapi.testclient import TestClient

from scout import db, server


def _add(url: str, title: str) -> int:
    lid, _ = db.upsert_listing({"site": "bat", "url": url, "title": title, "role": "candidate", "availability": "active"})
    return lid


def test_export_is_cached_until_the_database_changes(monkeypatch):
    _add("https://bringatrailer.com/listing/a/", "1999 BMW M Roadster")
    builds = []
    real = server.build_export
    monkeypatch.setattr(server, "build_export", lambda: builds.append(1) or real())
    with TestClient(server.app) as c:
        v1 = c.get("/api/data-version").json()["version"]
        r1 = c.get("/api/export")
        assert r1.headers["X-Data-Version"] == str(v1)
        r2 = c.get("/api/export")
        assert len(builds) == 1 and r2.content == r1.content          # unchanged: served from cache
        assert c.get("/api/data-version").json()["version"] == v1

        _add("https://bringatrailer.com/listing/b/", "2001 BMW Z3 3.0i Coupe")   # a sync, another connection
        v2 = c.get("/api/data-version").json()["version"]
        assert v2 != v1                                                  # the viewer knows to reload
        titles = [l["title"] for l in c.get("/api/export").json()["listings"]]
        assert len(builds) == 2 and "2001 BMW Z3 3.0i Coupe" in titles


def test_edits_through_the_api_move_the_version():
    lid = _add("https://bringatrailer.com/listing/c/", "2000 BMW Z3 M Coupe")
    with TestClient(server.app) as c:
        c.get("/api/export")
        v1 = c.get("/api/data-version").json()["version"]
        assert c.patch(f"/api/listings/{lid}", json={"status": "Pass"}, headers={"Origin": "http://127.0.0.1:8765"}).status_code == 200
        assert c.get("/api/data-version").json()["version"] != v1
        assert next(l for l in c.get("/api/export").json()["listings"] if l["id"] == lid)["status"] == "Pass"
