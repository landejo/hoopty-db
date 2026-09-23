"""Online SQLite backups of data/scout.db, kept outside the repo.

scout.db is the only copy of notes, assessments and provenance (it is
gitignored, and the published JSON is scrubbed), so a copy is taken on server
start and before every publish. Location: $SCOUT_BACKUP_DIR, else
~/Documents/Hoopty Scout Backups (on iCloud Drive when Documents sync is on).
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path

from scout import db

KEEP = 14


def backup_dir() -> Path:
    return Path(os.environ.get("SCOUT_BACKUP_DIR") or (Path.home() / "Documents" / "Hoopty Scout Backups"))


def backup_db(reason: str = "manual", dest_dir: Path | None = None, keep: int = KEEP) -> Path | None:
    """Copy the live DB with sqlite's online backup API (safe while the server
    writes). Returns the new file, or None when there is no DB yet. Keeps the
    newest `keep` backups and deletes older ones."""
    src = Path(db.DB_PATH)
    if not src.exists():
        return None
    out_dir = dest_dir or backup_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = out_dir / f"scout_{stamp}_{reason}.db"
    with sqlite3.connect(str(src)) as s, sqlite3.connect(str(dest)) as d:
        s.backup(d)
    for old in sorted(out_dir.glob("scout_*.db"))[:-keep]:
        old.unlink()
    return dest
