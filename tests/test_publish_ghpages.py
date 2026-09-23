"""Publish now pushes a single orphan commit to `gh-pages`, built from a
temporary git index + work-tree so `main`'s checkout is never touched. These
tests use a throwaway git repo (with a bare repo standing in for `origin`) —
no network, no real refs."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scout import db, publish
from scout.ingest import ingest_items


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def _git_bare(bare: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", f"--git-dir={bare}", *args], capture_output=True, text=True, check=True)


@pytest.fixture
def git_repo(tmp_path, monkeypatch):
    """A tmp_path git repo (standing in for the real hoopty-scout checkout) with
    a bare repo as its 'origin', on branch main. publish.py's module-level
    paths are monkeypatched to point at it."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test User")

    docs = root / "docs"
    docs.mkdir()
    (docs / "index.html").write_text("<html>viewer</html>")
    (docs / "app.js").write_text("// app")
    (docs / "styles.css").write_text("body{}")
    (root / "README.md").write_text("readme")
    _git(root, "add", "README.md", "docs/index.html", "docs/app.js", "docs/styles.css")
    _git(root, "commit", "-q", "-m", "init")

    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    _git(root, "remote", "add", "origin", str(bare))
    _git(root, "push", "-q", "-u", "origin", "main")

    monkeypatch.setattr(publish, "ROOT", root)
    monkeypatch.setattr(publish, "DOCS_DIR", docs)
    monkeypatch.setattr(publish, "SITE_DATA_DIR", docs / "data")
    return root, bare


def _seed_two_listings():
    ingest_items("carscom", [
        {"url": "https://www.cars.com/vehicledetail/a/", "title": "2015 Lexus GX 460",
         "price_text": "$22,000", "detail": {"text": "x" * 300}},
        {"url": "https://www.cars.com/vehicledetail/b/", "title": "2016 Lexus GX 460",
         "price_text": "$24,000", "detail": {"text": "y" * 300}},
    ], run_ai=False)
    rows = db.list_listings()
    db.update_listing(rows[0]["id"], {
        "profile_key": "gx460",
        "photos": ["https://example.com/1.jpg", "https://example.com/2.jpg"],
    })
    db.add_assessment(rows[0]["id"], {
        "policy_version": "1.2.1", "mission": "utility_capability", "verdict": "Pursue",
        "score": {"total": 78}, "confidence": 60, "model": "test",
        "assessed_at": "2026-09-08T00:00:00+00:00",
        "costs": {"max_price": 25000, "all_in_low": 24000, "all_in_high": 26000},
        "evidence": {"positives": ["clean"], "concerns": ["tires"], "facts": [{"key": "mileage", "value": "80000"}]},
    })
    return [r["id"] for r in rows]


def test_publish_creates_orphan_gh_pages_commit(git_repo):
    root, bare = git_repo
    ids = _seed_two_listings()
    before_head = _git(root, "rev-parse", "HEAD").stdout.strip()
    before_status = _git(root, "status", "--porcelain").stdout

    result = publish.git_publish()
    assert result["ok"] is True and result["changed"] is True

    # main's HEAD and working tree are untouched
    assert _git(root, "rev-parse", "HEAD").stdout.strip() == before_head
    assert _git(root, "status", "--porcelain").stdout == before_status
    assert not (root / "docs" / "data").exists()

    # gh-pages exists on origin as a single parentless commit
    sha = _git_bare(bare, "rev-parse", "gh-pages").stdout.strip()
    parents = _git_bare(bare, "log", "-n1", "--format=%P", sha).stdout.strip()
    assert parents == ""

    tree_files = set(_git_bare(bare, "ls-tree", "-r", "--name-only", sha).stdout.split())
    assert {"index.html", "app.js", "styles.css", ".nojekyll", "data/index.json"} <= tree_files
    assert "data/scout.json" not in tree_files
    for lid in ids:
        assert f"data/l/{lid}.json" in tree_files

    index = json.loads(_git_bare(bare, "show", f"{sha}:data/index.json").stdout)
    assert set(publish.INDEX_TOP_LEVEL_KEYS) <= set(index)
    lst = {l["id"]: l for l in index["listings"]}
    assert "photos" not in lst[ids[0]]
    assert "evidence" not in lst[ids[0]]["assessment"]
    assert "costs" not in lst[ids[0]]["assessment"]
    assert lst[ids[0]]["assessment"]["verdict"] == "Pursue"

    detail = json.loads(_git_bare(bare, "show", f"{sha}:data/l/{ids[0]}.json").stdout)
    assert detail["photos"] == ["https://example.com/1.jpg", "https://example.com/2.jpg"]
    assert detail["assessment"]["evidence"]["concerns"] == ["tires"]


def test_second_publish_with_identical_data_is_a_noop(git_repo, monkeypatch):
    root, bare = git_repo
    export = {
        "generated_at": "2026-09-22T00:00:00+00:00", "policy_version": "1.2.1",
        "calibration": {"samples": 0, "offset": None}, "sites": {}, "profiles": [], "markets": {},
        "listings": [{"id": 1, "title": "car"}],
    }
    monkeypatch.setattr(publish, "build_export", lambda: dict(export))

    r1 = publish.git_publish()
    assert r1["ok"] is True and r1["changed"] is True
    sha1 = _git_bare(bare, "rev-parse", "gh-pages").stdout.strip()

    r2 = publish.git_publish()
    assert r2["ok"] is True and r2["changed"] is False
    sha2 = _git_bare(bare, "rev-parse", "gh-pages").stdout.strip()
    assert sha1 == sha2  # no new push happened


def test_changed_listing_publishes_new_single_commit(git_repo, monkeypatch):
    root, bare = git_repo
    export = {
        "generated_at": "2026-09-22T00:00:00+00:00", "policy_version": "1.2.1",
        "calibration": {"samples": 0, "offset": None}, "sites": {}, "profiles": [], "markets": {},
        "listings": [{"id": 1, "title": "car", "price": 20000}],
    }
    monkeypatch.setattr(publish, "build_export", lambda: dict(export))
    r1 = publish.git_publish()
    sha1 = _git_bare(bare, "rev-parse", "gh-pages").stdout.strip()

    export["listings"][0]["price"] = 19000
    monkeypatch.setattr(publish, "build_export", lambda: dict(export))
    r2 = publish.git_publish()
    assert r2["ok"] is True and r2["changed"] is True
    sha2 = _git_bare(bare, "rev-parse", "gh-pages").stdout.strip()
    assert sha1 != sha2
    parents = _git_bare(bare, "log", "-n1", "--format=%P", sha2).stdout.strip()
    assert parents == ""  # still orphan, no history accumulation


def test_leak_aborts_before_any_git_call(git_repo, monkeypatch):
    root, bare = git_repo

    def boom(*a, **k):
        raise AssertionError("git must not run when a leak is found")
    monkeypatch.setattr(publish, "build_export", lambda: {"notes": "VIN 5TDZA23A15S123456", "listings": []})
    monkeypatch.setattr(publish.subprocess, "run", boom)

    result = publish.git_publish()
    assert result["ok"] is False and result["changed"] is False
    assert "notes" in result["detail"]
    # nothing was pushed: origin has no gh-pages branch at all
    monkeypatch.undo()
    with pytest.raises(subprocess.CalledProcessError):
        _git_bare(bare, "rev-parse", "gh-pages")


def test_publish_endpoint_still_returns_502_on_failure(monkeypatch):
    from fastapi.testclient import TestClient
    import scout.server as server
    monkeypatch.setattr(server, "git_publish", lambda: {"ok": False, "changed": False, "detail": "aborted: possible leak at $.notes"})
    with TestClient(server.app) as c:
        r = c.post("/api/publish")
        assert r.status_code == 502 and "leak" in r.json()["detail"]
