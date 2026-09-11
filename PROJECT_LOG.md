# Hoopty Scout — Project Log

Running log of high-effort work sessions. Newest entry first. Each entry ends
with a System State Summary per the Claude environment rules.

## 2026-09-11 — Autotrader adapter: saved cards found again (0.3.2)

Autotrader's saved-listings page moved saved cards to
`/cars-for-sale/vehicledetails.xhtml?listingId=<id>` links (with
`/cars-for-sale/vehicle/<id>` left only on the excluded "Cars You May Like"
block) and put Private Seller Exchange cars on `/marketplace/buy/<VIN>` —
sync collected 0 items. The adapter now matches all three formats,
normalizes dealer cars to the canonical `/vehicle/<id>` URL (still 200),
and treats the VIN as the site id for marketplace cars. Verified by
dry-running the new collect logic in the live logged-in DOM: 9/9 saved
cars found, 0 recommendations leaked. Reload the unpacked extension to
pick it up.

## 2026-09-11 — Sync stall fix: background AI queue + re-normalize damping

**Problem.** Syncs on Cars & Bids and Bring a Trailer stalled and never
finished. Two compounding causes, confirmed from the events table: (1) auction
page text changes on every visit, so every listing re-triggered a synchronous
Sonnet normalization inside `/api/ingest` — batches of 8 took 1.5–7 minutes,
long enough for Chrome to kill the extension's idle MV3 service worker
mid-await; (2) inline Opus profile generation added ~2 minutes per new
make/model. A third stall path: `/api/ingest` shared `_ai_lock` with
assessments, so any running assessment blocked all sync batches.

**Fix.**
- `scout/ingest.py`: AI normalization + profile generation moved to a daemon
  worker thread fed by an in-process queue (`defer_ai=True` path; inline path
  kept for single adds, renormalize, and tests). Text-drift re-normalization
  damped to once per `RENORM_COOLDOWN_HOURS` (12 h); availability changes
  still re-read immediately. Live card prices update unconditionally; the
  worker snapshots after each read so `_ever_sold` history stays faithful.
- `scout/server.py`: `IngestPayload.defer_ai` (default true), deferred path
  skips `_ai_lock`, `ai_queue` depth exposed in `/api/health` and ingest
  responses.
- Extension (`0.3.1`): sync sends `defer_ai: true`; popup/log report queued AI
  reads; single "Add" keeps inline normalization.
- `tests/test_profiles_scoring.py`: un-pinned the 2026-09-04 clock in the
  early-bid test (date bomb — `preliminary_score` reads the real clock).

**Verified.** 87/87 tests pass (3 new). E2E through FastAPI TestClient with a
stubbed slow model: ingest of 3 listings answered in 9 ms, queue drained in
background, cooldown suppressed re-reads on a second sync.

**System State Summary**
- Active tools: pytest (`.venv/bin/python -m pytest tests/ -q`), FastAPI
  TestClient for E2E; server run via `.venv/bin/python run.py`; extension
  loaded unpacked (reload required after this change).
- Modified paths: `scout/ingest.py`, `scout/server.py`,
  `extension/background.js`, `extension/popup.js`, `extension/manifest.json`,
  `tests/test_ingest.py`, `tests/test_profiles_scoring.py`, `PROJECT_LOG.md`
  (new).
- Open dependencies / follow-ups: normalization queue is in-process and
  non-persistent by design (lost jobs re-queue on next sync via unset
  `normalized_at`); workbench UI does not yet surface `ai_queue` depth; MV3
  service worker still has no explicit keepalive (unneeded now that ingest
  returns in milliseconds, but worth one if any long await returns to the
  sync path).
