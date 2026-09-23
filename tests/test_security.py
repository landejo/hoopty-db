from fastapi.testclient import TestClient

from scout import backup
from scout.server import app


def test_evil_origin_blocked_on_all_methods(monkeypatch):
    monkeypatch.setattr("scout.server.git_publish", lambda: (_ for _ in ()).throw(AssertionError("git_publish called")))
    headers = {"Origin": "https://evil.example"}
    with TestClient(app) as c:
        assert c.post("/api/publish", headers=headers).status_code == 403
        assert c.delete("/api/listings/1", headers=headers).status_code == 403
        assert c.get("/api/export", headers=headers).status_code == 403


def test_allowed_origins_pass_and_echo_cors_header():
    with TestClient(app) as c:
        for origin in (
            "http://127.0.0.1:8765",
            "http://localhost:8765",
            "chrome-extension://abcdefghijklmnopabcdefghijklmnop",
        ):
            r = c.get("/api/health", headers={"Origin": origin})
            assert r.status_code == 200
            assert r.headers.get("access-control-allow-origin") == origin


def test_host_header_checked():
    with TestClient(app) as c:
        assert c.get("/api/health", headers={"Host": "evil.example"}).status_code == 403
        assert c.get("/api/health", headers={"Host": "127.0.0.1:8765"}).status_code == 200


def test_sec_fetch_site_cross_site_blocked_without_origin():
    with TestClient(app) as c:
        r = c.get("/api/health", headers={"Sec-Fetch-Site": "cross-site"})
        assert r.status_code == 403


def test_no_origin_plain_request_allowed():
    with TestClient(app) as c:
        assert c.get("/api/health").status_code == 200


def test_startup_backup_writes_to_tmp_dir(tmp_path, monkeypatch):
    # temp_backup_dir (conftest, autouse) already points SCOUT_BACKUP_DIR at a
    # tmp path; re-pin it here explicitly so this test doesn't depend on that.
    backup_dir = tmp_path / "backups"
    monkeypatch.setenv("SCOUT_BACKUP_DIR", str(backup_dir))
    assert backup.backup_dir() == backup_dir
    with TestClient(app):
        pass  # startup event fires here, calling backup_db("startup")
    assert list(backup_dir.glob("scout_*_startup.db"))
