"""SQLite store. Canonical data lives here; docs/data/*.json is an export."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from scout.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site TEXT NOT NULL,
    site_id TEXT,
    url TEXT UNIQUE NOT NULL,
    role TEXT NOT NULL DEFAULT 'candidate',
    availability TEXT NOT NULL DEFAULT 'unknown',
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    title TEXT,
    thumb TEXT,
    raw_text TEXT,
    raw_json TEXT DEFAULT '{}',
    photos_json TEXT DEFAULT '[]',
    year INTEGER, make TEXT, model TEXT, generation TEXT, trim TEXT,
    engine TEXT, engine_liters REAL, transmission TEXT, drivetrain TEXT,
    body_style TEXT, exterior_color TEXT, interior_color TEXT,
    mileage INTEGER, price INTEGER, price_kind TEXT, sold_price INTEGER,
    location TEXT, vin TEXT, seller_type TEXT, seller_name TEXT,
    seller_contact TEXT, title_status TEXT, accidents TEXT, num_owners INTEGER,
    listing_date TEXT, auction_end TEXT,
    options_json TEXT DEFAULT '[]',
    profile_key TEXT, profile_confidence INTEGER,
    normalized_at TEXT, normalized_json TEXT DEFAULT '{}',
    prelim_score REAL,
    analysis_json TEXT, analyzed_at TEXT, analysis_model TEXT,
    status TEXT NOT NULL DEFAULT 'New',
    notes TEXT DEFAULT '',
    pinned INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_listings_profile ON listings(profile_key);
CREATE INDEX IF NOT EXISTS idx_listings_role ON listings(role, availability);

CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER NOT NULL,
    seen_at TEXT NOT NULL,
    price INTEGER,
    price_kind TEXT,
    availability TEXT,
    bid_count INTEGER,
    FOREIGN KEY(listing_id) REFERENCES listings(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_snapshots_listing ON snapshots(listing_id, seen_at);

CREATE TABLE IF NOT EXISTS profiles (
    key TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    make TEXT,
    models_json TEXT DEFAULT '[]',
    years_json TEXT DEFAULT '[]',
    framing TEXT, weak_points TEXT, immediate_repairs TEXT, repairs_12mo TEXT,
    market_notes TEXT,
    weights_json TEXT NOT NULL,
    checks_json TEXT DEFAULT '[]',
    dealbreakers_json TEXT DEFAULT '[]',
    source TEXT NOT NULL DEFAULT 'ai',
    verified INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    listing_id INTEGER,
    detail TEXT
);
"""

JSON_COLS = {"raw_json": {}, "photos_json": [], "options_json": [], "normalized_json": {}, "analysis_json": None}
PROFILE_JSON_COLS = {"models_json": [], "years_json": [], "weights_json": {}, "checks_json": [], "dealbreakers_json": []}


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@contextmanager
def connect(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(path or DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(path: Path | None = None) -> None:
    with connect(path) as c:
        c.executescript(SCHEMA)


def _row_to_dict(row: sqlite3.Row, json_cols: dict) -> dict[str, Any]:
    d = dict(row)
    for col, default in json_cols.items():
        raw = d.pop(col, None)
        key = col[:-5]  # strip _json
        if raw in (None, ""):
            d[key] = default
        else:
            try:
                d[key] = json.loads(raw)
            except json.JSONDecodeError:
                d[key] = default
    return d


def _prep(values: dict[str, Any], json_cols: dict) -> dict[str, Any]:
    out = {}
    for k, v in values.items():
        if k + "_json" in json_cols:
            out[k + "_json"] = json.dumps(v, ensure_ascii=False)
        else:
            out[k] = v
    return out


# ---------- listings ----------

def get_listing(listing_id: int, path: Path | None = None) -> dict[str, Any] | None:
    with connect(path) as c:
        row = c.execute("SELECT * FROM listings WHERE id=?", (listing_id,)).fetchone()
    return _row_to_dict(row, JSON_COLS) if row else None


def get_listing_by_url(url: str, path: Path | None = None) -> dict[str, Any] | None:
    with connect(path) as c:
        row = c.execute("SELECT * FROM listings WHERE url=?", (url,)).fetchone()
    return _row_to_dict(row, JSON_COLS) if row else None


def list_listings(role: str | None = None, profile_key: str | None = None,
                  path: Path | None = None) -> list[dict[str, Any]]:
    q = "SELECT * FROM listings WHERE 1=1"
    args: list[Any] = []
    if role:
        q += " AND role=?"
        args.append(role)
    if profile_key:
        q += " AND profile_key=?"
        args.append(profile_key)
    q += " ORDER BY last_seen DESC, id DESC"
    with connect(path) as c:
        rows = c.execute(q, args).fetchall()
    return [_row_to_dict(r, JSON_COLS) for r in rows]


def upsert_listing(values: dict[str, Any], path: Path | None = None) -> tuple[int, bool]:
    """Insert or update by URL. Returns (id, created)."""
    ts = now()
    existing = get_listing_by_url(values["url"], path)
    prepared = _prep(values, JSON_COLS)
    prepared["updated_at"] = ts
    prepared["last_seen"] = ts
    with connect(path) as c:
        if existing:
            sets = ", ".join(f"{k}=?" for k in prepared)
            c.execute(f"UPDATE listings SET {sets} WHERE id=?", [*prepared.values(), existing["id"]])
            return existing["id"], False
        prepared.setdefault("first_seen", ts)
        cols = ", ".join(prepared)
        marks = ", ".join("?" for _ in prepared)
        cur = c.execute(f"INSERT INTO listings ({cols}) VALUES ({marks})", list(prepared.values()))
        return cur.lastrowid, True


def update_listing(listing_id: int, values: dict[str, Any], path: Path | None = None) -> None:
    if not values:
        return
    prepared = _prep(values, JSON_COLS)
    prepared["updated_at"] = now()
    sets = ", ".join(f"{k}=?" for k in prepared)
    with connect(path) as c:
        c.execute(f"UPDATE listings SET {sets} WHERE id=?", [*prepared.values(), listing_id])


def add_snapshot(listing_id: int, price: int | None, price_kind: str | None,
                 availability: str | None, bid_count: int | None = None,
                 path: Path | None = None) -> bool:
    """Record a snapshot only when something changed since the last one."""
    with connect(path) as c:
        last = c.execute(
            "SELECT price, price_kind, availability, bid_count FROM snapshots "
            "WHERE listing_id=? ORDER BY seen_at DESC, id DESC LIMIT 1", (listing_id,)
        ).fetchone()
        if last and (last["price"], last["price_kind"], last["availability"], last["bid_count"]) == \
                (price, price_kind, availability, bid_count):
            return False
        c.execute(
            "INSERT INTO snapshots (listing_id, seen_at, price, price_kind, availability, bid_count) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (listing_id, now(), price, price_kind, availability, bid_count),
        )
    return True


def list_snapshots(listing_id: int, path: Path | None = None) -> list[dict[str, Any]]:
    with connect(path) as c:
        rows = c.execute("SELECT * FROM snapshots WHERE listing_id=? ORDER BY seen_at", (listing_id,)).fetchall()
    return [dict(r) for r in rows]


def all_snapshots(path: Path | None = None) -> dict[int, list[dict[str, Any]]]:
    out: dict[int, list[dict[str, Any]]] = {}
    with connect(path) as c:
        for r in c.execute("SELECT * FROM snapshots ORDER BY listing_id, seen_at"):
            out.setdefault(r["listing_id"], []).append(dict(r))
    return out


def mark_unseen_removed(site: str, seen_urls: set[str], path: Path | None = None) -> int:
    """Active candidates on `site` that were not in this sync are marked removed."""
    with connect(path) as c:
        rows = c.execute(
            "SELECT id, url FROM listings WHERE site=? AND availability='active'", (site,)
        ).fetchall()
        ids = [r["id"] for r in rows if r["url"] not in seen_urls]
        for lid in ids:
            c.execute("UPDATE listings SET availability='removed', updated_at=? WHERE id=?", (now(), lid))
            c.execute(
                "INSERT INTO snapshots (listing_id, seen_at, availability) VALUES (?, ?, 'removed')",
                (lid, now()),
            )
    return len(ids)


# ---------- profiles ----------

def get_profile(key: str, path: Path | None = None) -> dict[str, Any] | None:
    with connect(path) as c:
        row = c.execute("SELECT * FROM profiles WHERE key=?", (key,)).fetchone()
    return _row_to_dict(row, PROFILE_JSON_COLS) if row else None


def list_profiles(path: Path | None = None) -> list[dict[str, Any]]:
    with connect(path) as c:
        rows = c.execute("SELECT * FROM profiles ORDER BY verified DESC, label").fetchall()
    return [_row_to_dict(r, PROFILE_JSON_COLS) for r in rows]


def upsert_profile(p: dict[str, Any], path: Path | None = None) -> None:
    ts = now()
    values = {
        "key": p["key"], "label": p["label"], "make": p.get("make"),
        "models": p.get("models", []), "years": p.get("years", []),
        "framing": p.get("framing"), "weak_points": p.get("weak_points"),
        "immediate_repairs": p.get("immediate_repairs"), "repairs_12mo": p.get("repairs_12mo"),
        "market_notes": p.get("market_notes"), "weights": p["weights"],
        "checks": p.get("checks", []), "dealbreakers": p.get("dealbreaker_rules", p.get("dealbreakers", [])),
        "source": p.get("source", "ai"), "verified": 1 if p.get("verified") else 0,
        "updated_at": ts,
    }
    prepared = _prep(values, PROFILE_JSON_COLS)
    with connect(path) as c:
        exists = c.execute("SELECT 1 FROM profiles WHERE key=?", (p["key"],)).fetchone()
        if exists:
            sets = ", ".join(f"{k}=?" for k in prepared if k != "key")
            c.execute(f"UPDATE profiles SET {sets} WHERE key=?",
                      [v for k, v in prepared.items() if k != "key"] + [p["key"]])
        else:
            prepared["created_at"] = ts
            cols = ", ".join(prepared)
            marks = ", ".join("?" for _ in prepared)
            c.execute(f"INSERT INTO profiles ({cols}) VALUES ({marks})", list(prepared.values()))


def set_profile_verified(key: str, verified: bool, path: Path | None = None) -> None:
    with connect(path) as c:
        c.execute("UPDATE profiles SET verified=?, updated_at=? WHERE key=?", (1 if verified else 0, now(), key))


# ---------- events ----------

def log_event(kind: str, listing_id: int | None = None, detail: str = "", path: Path | None = None) -> None:
    with connect(path) as c:
        c.execute("INSERT INTO events (ts, kind, listing_id, detail) VALUES (?, ?, ?, ?)",
                  (now(), kind, listing_id, detail[:2000]))


def recent_events(limit: int = 50, path: Path | None = None) -> list[dict[str, Any]]:
    with connect(path) as c:
        rows = c.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]
