# Hoopty Scout — Project Log

Running log of high-effort work sessions. Newest entry first. Each entry ends
with a System State Summary per the Claude environment rules.

## 2026-09-25 — Sold/gone by hand, Best score order, Autotrader sold wording

- Status Sold/Ended, or your own "Do not pursue" whose reason says the car is
  gone ("no longer available", "sold", ...), now makes it a sold/ended comp off
  the candidates board (role counts as yours); reconciled at startup. New
  "Mark sold / gone" button on the listing page. Fixed #220 and #4.
- "Best score" sorts by the badge number only (the early-bid -10 stays in
  Pursue next); a divider introduces the not-yet-assessed (≈) cards.
- Autotrader's sold page ("this car has already found a new home") is a
  deterministic signal.
- Harness: waits for this server's own startup update (the DB copy carries old
  events); the startup suite forces a stale copy so the self-refresh path runs.
- Added #272 (2006 Cayman S 3.8, Built for Backroads, $57,500) by hand.
- Tests: 252 unit; E2E startup 5/5, ux 28/28, replay 12/12, reassess 12/12,
  published 12/12.

## 2026-09-25 — Policy 1.8.0: walk-away, budgets per mission, merit, next step

- Built items 1-4 of the scoring review (see POLICY_CHANGES 1.8.0). Viewer:
  next-step chip on cards (your own verdict wins where set), walk-away under the
  price (amber near, red over), Next-step filter, "Contact now" tile, next-step
  block and walk-away derivation on the listing page.
- Startup re-derive is now a visible task; a board opened mid-update refreshes
  itself when it ends.
- Statuses of #35, #128, #129, #130, #220 changed Purchased -> Sold on the live
  server (Jason). #220 is still an active candidate until the availability check
  reads its page.
- On a copy of the real data under 1.8.0: 5 Contact now, 9 Watch, 19 Skip.
- Tests: 249 unit; E2E startup 5/5, ux 24/24, replay 12/12, reassess 12/12,
  published 12/12.

## 2026-09-25 — Bug pass 3 (E2E-led)

Fixed, each with a unit test and covered by an E2E suite:
- Market: "pricier than X% of sold comps" and the market sold median/count
  counted reserve-not-met bids and live comps as sales (now `market.is_sale`);
  the 3-year comp recency filter read a nonexistent `sold_at` and fell back to
  `last_seen`, which every sync refreshes; the Market view plotted live comps as sold.
- Published board: the index dropped the early-bid sort signal (now
  `assessment.early_bid`); "published 1h ago" was the minimum ("just now", "9m ago").
- Tasks: a new task inherited an availability run's old heartbeat and was ended
  as "stalled" on the first poll (then 409 no longer guarded, auto-publish could
  push mid-run); provenance could end another task's banner or leave its own
  stuck forever; stall window 180 → 300 s and the extension reports every page.
- Auto-publish: the first edit of a new sitting published ~30 s later as a
  "checkpoint" (used the previous sitting's publish time); edits made while a
  publish ran were dropped from the pending count.
- Extension (0.4.1): a second run could "recover" a live run's lock after 10 min
  and close its tab; now refused while running, and staleness is measured from
  the last page opened.
- Viewer: a finished run re-rendered listing/Settings pages (scroll to top,
  unsaved Settings edits lost); now only the board re-renders.
- Roles set by hand before `role_user_set` existed are backfilled from the
  event log on every start (#4 and #216 would have flipped back).
- Stored assessments now move to the current policy at server start (72 were on
  1.5/1.6, so 1.7.0's sold gate never reached them); `last_seen` is no longer
  bumped by a check that could not read the page; auction end times use
  America/Los_Angeles (was fixed UTC-7).

E2E: startup 3/3, ux 18/18, replay 12/12, reassess 12/12, published 12/12.
Not fixed: `db.merge_listings` does not carry role/role_user_set (rare).

## 2026-09-24 (later) — End-to-end verification; tiered re-assess

Weighted toward E2E per Jason. Harness in `e2e/` (`run.sh`), sandboxed: clone
with a local bare origin, DB copy, server on :8766, real Chromium + unpacked
extension, fake Anthropic API for paid paths.

- Real-browser availability runs (71 listings) + independent checks of every
  change against the live pages found and fixed four accuracy bugs:
  CarGurus' sold page ("Looks like that one got away") was not recognised;
  a comp was pulled back to candidate on a page with no notice either way;
  empty and bot-block pages (CarGurus DataDome, Autotrader Akamai, Cars.com
  Cloudflare) were recorded as "active"; Facebook's
  `?unavailable_product=1` redirect is now a signal. Live-listing markers
  (Facebook Message, CarGurus Check availability, Autotrader Listing Price) are
  positive evidence; without one the result is "unclear". Each check stores the
  page URL and first 300 characters for audit.
- Verified correct on live pages: #247/#252 ended (bid to $32,500, same car
  saved twice), #224 sold $30,500, #261 and #216 live, #4 and #8 sold (CarGurus),
  #67 sold and #52 delisted (Facebook).
- UI bugs found by E2E: ← / → used the stale pager during navigation (now
  from the URL + board order); a 520px profile <select> made every listing page
  1,560px wide on a phone.
- Tiered re-assess replaces "top 15": next 15 not yet done this cycle, cycle
  restarts after 3 days (`/api/reassess`, `/api/reassess/cycle`).
- Auto-publish verified: pushed once after each sitting that changed data,
  skipped an edit that changed nothing.
- Not verifiable by automation: CarGurus/Cars.com/Autotrader through the
  extension (sites block automated browsers) and signed-in Facebook sync; a real
  Opus 5.5 response (paid).

**System State Summary**
- Active tools: `e2e/run.sh` (Playwright 1.58, cached Chromium 1208), pytest
  (237 pass).
- Modified paths: `scout/availability.py`, `scout/server.py`,
  `extension/adapters/cargurus.js`, `docs/app.js`, `docs/styles.css`,
  `tests/test_availability.py`, new `e2e/`, `README.md`, `.gitignore`.
- Open dependencies: nothing committed; restart :8765 and reload the extension.

## 2026-09-24 — Hoopty-Matic: availability check, top-15 re-assess, auto-publish, redesign

- Renamed to **Hoopty-Matic** everywhere user-visible (viewer, extension, README,
  handoff filenames). Kept: the `scout` package, `SCOUT_*` env vars, localStorage
  keys, the `~/Documents/Hoopty Scout Backups` folder, the repo name.
- Board source links; availability check (`scout/availability.py`, extension
  `runAvailabilityCheck`, workbench bridge `extension/adapters/workbench.js`);
  statuses `Sold` / `Ended`; policy 1.7.0 `no_longer_available` gate.
- `POST /api/reassess` + board button (top 15 in board order, `SCOUT_MODEL_TOP`
  = claude-opus-5-5), `GET /api/assess-cost`.
- Auto-publish after a sitting (`scout/autopublish.py`, `/api/activity` heartbeat).
- Bugs fixed: publish never detected "nothing changed" (generated_at);
  re-normalize flipped ended/removed/pending listings to active and wiped `raw`;
  a single miss from a lazy saved list marked a car removed; card-only syncs
  overwrote full page text; a bare "sold" anywhere in page text made a live car a
  comp (tightened, plus CarGurus adapter); a hand-set role was flipped back by
  the next sync (`role_user_set`).
- UX: scroll/focus kept when returning to the board; ‹ › and ←/→ between
  listings; phone layout (no horizontal scroll at 375 px).
- Redesign after Dribbble "Auto.Hunt" (Aksantara) and Behance dashboards: dark
  app bar, filter rail + results head, bento stats, cobalt accent, Plus Jakarta
  Sans; new gauge icon (`docs/icon.svg`, extension PNGs). Mobbin returned 403.
- Found, not fixed: "pricier than X% of sold comps" pool includes non-sales
  (`publish.py`); comp recency filter reads a nonexistent `sold_at` and falls
  back to `last_seen` (`market.py`); the published index drops the early-bid
  sort signal.

**System State Summary**
- Active tools: pytest (232 pass), sandbox dev server `scout-dev` on :8766 with a
  DB copy in the session scratchpad, AI and auto-publish off.
- Modified paths: `scout/{availability,autopublish}.py` (new), `scout/{server,
  ingest,publish,db,config,handoff,__init__}.py`, `scout/policy/{gates,__init__}.py`,
  `scout/policy/POLICY_CHANGES.md`, `docs/{app.js,styles.css,index.html,icon.svg}`,
  `extension/*` (0.4.0), `tests/test_availability.py` (new), `tests/{conftest,
  test_ingest,test_publish_ghpages}.py`, `README.md`, `.env.example`, `.claude/launch.json`.
- Open dependencies: restart the server on :8765 and reload the unpacked
  extension; nothing committed yet.

## 2026-09-22 — Full audit (read-only, no code changes)

Opus-led audit with Sonnet sub-agents across backend, extension, viewer,
policy/AI, pipeline and live data. Report:
`data/audits/Hoopty_Scout_Audit_v1_20260922.md` (untracked). Headline: the
public export leaks VINs/phone numbers via `raw` free text and Autotrader
URLs; CORS `*` with no auth; publish reports success on failure; no DB
backup; verdict/confidence cannot discriminate at listing stage (0 Pursue,
confidence median 15).

**System State Summary**
- Active tools: pytest (87/87 pass), read-only sqlite, browser pane on the
  local workbench.
- Modified paths: `PROJECT_LOG.md` (this entry), new untracked
  `data/audits/Hoopty_Scout_Audit_v1_20260922.md`.
- Open dependencies: the user decides whether to rewrite public history for the
  leaked `docs/data/scout.json`; the P0 fixes in the report are not started.

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
