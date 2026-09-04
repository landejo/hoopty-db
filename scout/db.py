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

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER NOT NULL,
    policy_version TEXT NOT NULL,
    mission TEXT NOT NULL,
    verdict TEXT NOT NULL,
    score INTEGER,
    confidence INTEGER,
    model TEXT,
    assessment_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(listing_id) REFERENCES listings(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_assessments_listing ON assessments(listing_id, id);

CREATE TABLE IF NOT EXISTS vehicles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vin TEXT UNIQUE,
    fingerprint TEXT,
    year INTEGER, make TEXT, model TEXT, trim TEXT, engine TEXT, transmission TEXT,
    exterior_color TEXT, interior_color TEXT, plate TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vehicles_fingerprint ON vehicles(fingerprint);

CREATE TABLE IF NOT EXISTS vehicle_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id INTEGER NOT NULL,
    listing_id INTEGER,
    event_date TEXT,
    venue TEXT,
    url TEXT,
    mileage INTEGER,
    price INTEGER,
    price_type TEXT,
    status TEXT NOT NULL,
    evidence TEXT,
    source TEXT,
    identity_confidence TEXT NOT NULL DEFAULT 'confirmed',
    seller TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(vehicle_id, url, status, event_date),
    FOREIGN KEY(vehicle_id) REFERENCES vehicles(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_vehicle_events_vehicle ON vehicle_events(vehicle_id, event_date);

CREATE TABLE IF NOT EXISTS provenance_hits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER NOT NULL,
    engine TEXT,
    query TEXT,
    url TEXT NOT NULL,
    title TEXT,
    snippet TEXT,
    detail_text TEXT,
    fetched_at TEXT NOT NULL,
    UNIQUE(listing_id, url),
    FOREIGN KEY(listing_id) REFERENCES listings(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS provenance_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    queries_json TEXT NOT NULL,
    hits INTEGER DEFAULT 0,
    result_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(listing_id) REFERENCES listings(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS vin_decodes (
    vin TEXT PRIMARY KEY,
    decode_json TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    listing_id INTEGER,
    detail TEXT
);
"""

JSON_COLS = {"raw_json": {}, "photos_json": [], "options_json": [], "normalized_json": {}, "analysis_json": None, "provenance_json": None}
PROFILE_JSON_COLS = {"models_json": [], "years_json": [], "weights_json": {}, "checks_json": [], "dealbreakers_json": [],
                     "critical_evidence_json": []}


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


_ADDITIVE_COLUMNS = {
    "listings": [("mission", "TEXT"), ("vehicle_id", "INTEGER"), ("provenance_json", "TEXT")],
    "profiles": [("critical_evidence_json", "TEXT DEFAULT '[]'"), ("mission_default", "TEXT"),
                 ("risk_reserve", "INTEGER"), ("automatic_ok", "INTEGER DEFAULT 0"),
                 ("catchup_notes", "TEXT")],
}


def init_db(path: Path | None = None) -> None:
    with connect(path) as c:
        c.executescript(SCHEMA)
        for table, cols in _ADDITIVE_COLUMNS.items():
            have = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
            for name, decl in cols:
                if name not in have:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


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
        "critical_evidence": p.get("critical_evidence", []), "mission_default": p.get("mission_default"),
        "risk_reserve": p.get("risk_reserve"), "automatic_ok": 1 if p.get("automatic_ok") else 0,
        "catchup_notes": p.get("catchup_notes"),
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

def get_vin_decode(vin: str, path: Path | None = None) -> dict[str, Any] | None:
    with connect(path) as c:
        row = c.execute("SELECT decode_json FROM vin_decodes WHERE vin=?", (vin,)).fetchone()
    return json.loads(row["decode_json"]) if row else None


def set_vin_decode(vin: str, decoded: dict[str, Any], path: Path | None = None) -> None:
    with connect(path) as c:
        c.execute("INSERT INTO vin_decodes (vin, decode_json, fetched_at) VALUES (?, ?, ?) "
                  "ON CONFLICT(vin) DO UPDATE SET decode_json=excluded.decode_json, fetched_at=excluded.fetched_at",
                  (vin, json.dumps(decoded, ensure_ascii=False), now()))


def log_event(kind: str, listing_id: int | None = None, detail: str = "", path: Path | None = None) -> None:
    with connect(path) as c:
        c.execute("INSERT INTO events (ts, kind, listing_id, detail) VALUES (?, ?, ?, ?)",
                  (now(), kind, listing_id, detail[:2000]))


def recent_events(limit: int = 50, path: Path | None = None) -> list[dict[str, Any]]:
    with connect(path) as c:
        rows = c.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


# ---------- settings ----------

def get_setting(key: str, path: Path | None = None) -> Any:
    with connect(path) as c:
        row = c.execute("SELECT value_json FROM settings WHERE key=?", (key,)).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["value_json"])
    except json.JSONDecodeError:
        return None


def set_setting(key: str, value: Any, path: Path | None = None) -> None:
    with connect(path) as c:
        c.execute("INSERT INTO settings (key, value_json, updated_at) VALUES (?, ?, ?) "
                  "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
                  (key, json.dumps(value, ensure_ascii=False), now()))


# ---------- assessments ----------

def add_assessment(listing_id: int, assessment: dict[str, Any], path: Path | None = None) -> int:
    with connect(path) as c:
        cur = c.execute(
            "INSERT INTO assessments (listing_id, policy_version, mission, verdict, score, confidence, model, assessment_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (listing_id, assessment["policy_version"], assessment["mission"], assessment["verdict"],
             (assessment.get("score") or {}).get("total"), assessment.get("confidence"), assessment.get("model"),
             json.dumps(assessment, ensure_ascii=False), now()),
        )
        return cur.lastrowid


def latest_assessment(listing_id: int, path: Path | None = None) -> dict[str, Any] | None:
    with connect(path) as c:
        row = c.execute("SELECT assessment_json FROM assessments WHERE listing_id=? ORDER BY id DESC LIMIT 1", (listing_id,)).fetchone()
    return json.loads(row["assessment_json"]) if row else None


def latest_assessments(path: Path | None = None) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    with connect(path) as c:
        for r in c.execute("SELECT listing_id, assessment_json FROM assessments ORDER BY id"):
            out[r["listing_id"]] = json.loads(r["assessment_json"])
    return out


def list_assessments(listing_id: int, path: Path | None = None) -> list[dict[str, Any]]:
    with connect(path) as c:
        rows = c.execute("SELECT id, policy_version, mission, verdict, score, confidence, model, created_at "
                         "FROM assessments WHERE listing_id=? ORDER BY id DESC", (listing_id,)).fetchall()
    return [dict(r) for r in rows]


# ---------- VIN history ----------

def vin_history(vin: str | None, exclude_listing_id: int | None = None, path: Path | None = None) -> dict[str, Any]:
    """Every listing in this database sharing the VIN, with price/mileage/disclosure
    deltas. Internal only: no external VIN service is wired."""
    out: dict[str, Any] = {"vin": vin, "prior_listings": [], "prior_sales": [], "price_changes": [],
                           "mileage_changes": [], "disclosure_changes": [], "markup_vs_last_sale": None}
    if not vin:
        return out
    with connect(path) as c:
        rows = [_row_to_dict(r, JSON_COLS) for r in c.execute(
            "SELECT * FROM listings WHERE vin=? ORDER BY first_seen, id", (vin,)).fetchall()]
        snaps = {r["id"]: [dict(s) for s in c.execute(
            "SELECT * FROM snapshots WHERE listing_id=? ORDER BY seen_at", (r["id"],)).fetchall()] for r in rows}
    current = next((r for r in rows if r["id"] == exclude_listing_id), None)
    others = [r for r in rows if r["id"] != exclude_listing_id]
    for r in others:
        entry = {"listing_id": r["id"], "site": r["site"], "url": r["url"], "first_seen": r["first_seen"],
                 "last_seen": r["last_seen"], "availability": r["availability"], "price": r.get("price"),
                 "sold_price": r.get("sold_price"), "mileage": r.get("mileage"), "title_status": r.get("title_status"),
                 "red_flags": (r.get("normalized") or {}).get("red_flags") or []}
        out["prior_listings"].append(entry)
        if r.get("sold_price") or r.get("availability") == "sold":
            out["prior_sales"].append({"date": r.get("listing_date") or r["last_seen"][:10],
                                       "price": r.get("sold_price") or r.get("price"), "site": r["site"]})
    for r in rows:
        prices = [s for s in snaps.get(r["id"], []) if s.get("price")]
        for a, b in zip(prices, prices[1:]):
            if a["price"] != b["price"]:
                out["price_changes"].append({"listing_id": r["id"], "from": a["price"], "to": b["price"], "at": b["seen_at"][:10]})
    miles = [(r["first_seen"][:10], r.get("mileage")) for r in rows if r.get("mileage")]
    for (d1, m1), (d2, m2) in zip(miles, miles[1:]):
        if m1 != m2:
            out["mileage_changes"].append({"from": m1, "to": m2, "between": [d1, d2],
                                           "note": "mileage went DOWN" if m2 < m1 else ""})
    if current and others:
        prev = others[-1]
        pf = set((prev.get("normalized") or {}).get("red_flags") or [])
        cf = set((current.get("normalized") or {}).get("red_flags") or [])
        for gone in sorted(pf - cf):
            out["disclosure_changes"].append({"previously_disclosed": gone, "now": "not mentioned"})
        if (prev.get("title_status") or "").lower() != (current.get("title_status") or "").lower() and prev.get("title_status"):
            out["disclosure_changes"].append({"previously_disclosed": f"title: {prev['title_status']}",
                                              "now": f"title: {current.get('title_status') or 'not stated'}"})
    if current and out["prior_sales"] and current.get("price"):
        last = out["prior_sales"][-1]["price"]
        if last:
            out["markup_vs_last_sale"] = round((current["price"] - last) / last, 3)
    return out


# ---------- vehicles (one record per VIN) ----------

def get_vehicle(vehicle_id: int, path: Path | None = None) -> dict[str, Any] | None:
    with connect(path) as c:
        row = c.execute("SELECT * FROM vehicles WHERE id=?", (vehicle_id,)).fetchone()
    return dict(row) if row else None


def get_vehicle_by_vin(vin: str, path: Path | None = None) -> dict[str, Any] | None:
    with connect(path) as c:
        row = c.execute("SELECT * FROM vehicles WHERE vin=?", (vin.upper(),)).fetchone()
    return dict(row) if row else None


def upsert_vehicle(vin: str | None, fingerprint: str | None, attrs: dict[str, Any], path: Path | None = None,
                   current_vehicle_id: int | None = None) -> int:
    """VIN is the only identity. A listing without a VIN gets its OWN provisional
    record (fingerprint kept for candidate matching, never for merging). When a
    VIN later appears on a listing that already has a provisional record, that
    record either takes the VIN or is merged into the existing VIN record."""
    ts = now()
    cols = {k: attrs.get(k) for k in ("year", "make", "model", "trim", "engine", "transmission", "exterior_color", "interior_color", "plate") if attrs.get(k) not in (None, "")}
    with connect(path) as c:
        vin_row = c.execute("SELECT * FROM vehicles WHERE vin=?", (vin.upper(),)).fetchone() if vin else None
        cur_row = c.execute("SELECT * FROM vehicles WHERE id=?", (current_vehicle_id,)).fetchone() if current_vehicle_id else None
        if vin_row and cur_row and vin_row["id"] != cur_row["id"]:
            # The listing's provisional record is now known to be the VIN record: merge.
            _merge_vehicles(c, cur_row["id"], vin_row["id"])
            row = vin_row
        elif vin_row:
            row = vin_row
        elif cur_row:
            row = cur_row
        else:
            row = None
        if row:
            sets = {k: v for k, v in cols.items() if not row[k]}
            if vin and not row["vin"]:
                sets["vin"] = vin.upper()
            if fingerprint and not row["fingerprint"]:
                sets["fingerprint"] = fingerprint
            if sets:
                sets["updated_at"] = ts
                c.execute(f"UPDATE vehicles SET {', '.join(k + '=?' for k in sets)} WHERE id=?", [*sets.values(), row["id"]])
            return row["id"]
        cols.update({"vin": vin.upper() if vin else None, "fingerprint": fingerprint, "created_at": ts, "updated_at": ts})
        cur = c.execute(f"INSERT INTO vehicles ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})", list(cols.values()))
        return cur.lastrowid


def _merge_vehicles(c: sqlite3.Connection, src: int, dst: int) -> None:
    """Move listings and events from src onto dst, then drop src. Events that
    would collide on (url, status, date) are dropped as duplicates."""
    if src == dst:
        return
    c.execute("UPDATE listings SET vehicle_id=? WHERE vehicle_id=?", (dst, src))
    for r in c.execute("SELECT * FROM vehicle_events WHERE vehicle_id=?", (src,)).fetchall():
        try:
            c.execute("UPDATE vehicle_events SET vehicle_id=? WHERE id=?", (dst, r["id"]))
        except sqlite3.IntegrityError:
            c.execute("DELETE FROM vehicle_events WHERE id=?", (r["id"],))
    srow = c.execute("SELECT * FROM vehicles WHERE id=?", (src,)).fetchone()
    drow = c.execute("SELECT * FROM vehicles WHERE id=?", (dst,)).fetchone()
    sets = {k: srow[k] for k in ("year", "make", "model", "trim", "engine", "transmission", "exterior_color", "interior_color", "plate", "fingerprint") if srow[k] and not drow[k]}
    if sets:
        c.execute(f"UPDATE vehicles SET {', '.join(k + '=?' for k in sets)} WHERE id=?", [*sets.values(), dst])
    c.execute("DELETE FROM vehicles WHERE id=?", (src,))


def merge_vehicles(src: int, dst: int, path: Path | None = None) -> None:
    """Explicit merge, used when an investigation confirms two tracked listings
    are the same car (exact VIN, or a strongly-likely match the user accepts)."""
    with connect(path) as c:
        _merge_vehicles(c, src, dst)


def split_provisional_vehicles(path: Path | None = None) -> int:
    """Repair: a VIN-less record holding several listings was merged on a
    fingerprint under the old rule. Give every listing but the first its own
    record and rebuild their sync-derived events."""
    moved = 0
    with connect(path) as c:
        groups = c.execute("SELECT v.id FROM vehicles v JOIN listings l ON l.vehicle_id=v.id WHERE v.vin IS NULL "
                           "GROUP BY v.id HAVING COUNT(l.id) > 1").fetchall()
        for g in groups:
            ls = c.execute("SELECT id FROM listings WHERE vehicle_id=? ORDER BY id", (g["id"],)).fetchall()
            for l in ls[1:]:
                c.execute("UPDATE listings SET vehicle_id=NULL WHERE id=?", (l["id"],))
                c.execute("DELETE FROM vehicle_events WHERE vehicle_id=? AND listing_id=?", (g["id"], l["id"]))
                moved += 1
    return moved


def add_vehicle_event(vehicle_id: int, ev: dict[str, Any], path: Path | None = None) -> bool:
    cols = {k: ev.get(k) for k in ("listing_id", "event_date", "venue", "url", "mileage", "price", "price_type", "status",
                                   "evidence", "source", "identity_confidence", "seller")}
    cols = {k: v for k, v in cols.items() if v is not None}
    cols.setdefault("identity_confidence", "confirmed")
    cols["vehicle_id"] = vehicle_id
    cols["created_at"] = now()
    with connect(path) as c:
        try:
            c.execute(f"INSERT INTO vehicle_events ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})", list(cols.values()))
            return True
        except sqlite3.IntegrityError:
            return False


def vehicle_events(vehicle_id: int, path: Path | None = None) -> list[dict[str, Any]]:
    with connect(path) as c:
        rows = c.execute("SELECT * FROM vehicle_events WHERE vehicle_id=? ORDER BY COALESCE(event_date, created_at), id", (vehicle_id,)).fetchall()
    return [dict(r) for r in rows]


def listings_for_vehicle(vehicle_id: int, path: Path | None = None) -> list[dict[str, Any]]:
    with connect(path) as c:
        rows = c.execute("SELECT * FROM listings WHERE vehicle_id=? ORDER BY first_seen, id", (vehicle_id,)).fetchall()
    return [_row_to_dict(r, JSON_COLS) for r in rows]


# ---------- provenance hits / jobs ----------

def add_provenance_hits(listing_id: int, hits: list[dict[str, Any]], path: Path | None = None) -> int:
    n = 0
    with connect(path) as c:
        for h in hits:
            url = (h.get("url") or "").strip()
            if not url:
                continue
            try:
                c.execute("INSERT INTO provenance_hits (listing_id, engine, query, url, title, snippet, detail_text, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                          (listing_id, h.get("engine"), (h.get("query") or "")[:300], url[:1000], (h.get("title") or "")[:300],
                           (h.get("snippet") or "")[:1500], (h.get("detail_text") or "")[:40000], now()))
                n += 1
            except sqlite3.IntegrityError:
                if h.get("detail_text"):
                    c.execute("UPDATE provenance_hits SET detail_text=? WHERE listing_id=? AND url=?", ((h.get("detail_text") or "")[:40000], listing_id, url[:1000]))
    return n


def provenance_hits(listing_id: int, path: Path | None = None) -> list[dict[str, Any]]:
    with connect(path) as c:
        rows = c.execute("SELECT * FROM provenance_hits WHERE listing_id=? ORDER BY id", (listing_id,)).fetchall()
    return [dict(r) for r in rows]


def create_provenance_job(listing_id: int, queries: list[dict[str, Any]], path: Path | None = None) -> int:
    ts = now()
    with connect(path) as c:
        c.execute("UPDATE provenance_jobs SET status='superseded', updated_at=? WHERE listing_id=? AND status IN ('queued','running')", (ts, listing_id))
        cur = c.execute("INSERT INTO provenance_jobs (listing_id, status, queries_json, created_at, updated_at) VALUES (?, 'queued', ?, ?, ?)",
                        (listing_id, json.dumps(queries, ensure_ascii=False), ts, ts))
        return cur.lastrowid


def list_provenance_jobs(status: str | None = "queued", path: Path | None = None) -> list[dict[str, Any]]:
    q = "SELECT j.*, l.title AS listing_title, l.url AS listing_url, l.site FROM provenance_jobs j JOIN listings l ON l.id=j.listing_id"
    args: list[Any] = []
    if status:
        q += " WHERE j.status=?"
        args.append(status)
    q += " ORDER BY j.id DESC LIMIT 50"
    with connect(path) as c:
        rows = c.execute(q, args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["queries"] = json.loads(d.pop("queries_json") or "[]")
        d["result"] = json.loads(d.pop("result_json") or "null")
        out.append(d)
    return out


def update_provenance_job(job_id: int, path: Path | None = None, **fields: Any) -> None:
    if "result" in fields:
        fields["result_json"] = json.dumps(fields.pop("result"), ensure_ascii=False)
    fields["updated_at"] = now()
    with connect(path) as c:
        c.execute(f"UPDATE provenance_jobs SET {', '.join(k + '=?' for k in fields)} WHERE id=?", [*fields.values(), job_id])
