# Hoopty Scout — Project Log

Running log of high-effort work sessions. Newest entry first. Each entry ends
with a System State Summary per the Claude environment rules.

## 2026-09-25 — Curiosities

New role `curiosity` (scout/curiosity.py): followed out of interest, not a
serious candidate. Automatic over `curiosity_over_price` ($40,000, listed price
or expected hammer, whichever is higher), back to candidate under it; a role
set by hand wins. Curious tab, chip, Role-menu option, tile count. Excluded
from tier re-assess / quick-assess-all; still synced, availability-checked and
turned into a comp when sold. Applied at ingest, after an AI read, after an
assessment, on a Policy change to the line, after an availability check, and
at startup. On a copy of the real data: #272, #261, #6, #237, #238 move to
curiosities; #263 (the 911 example) is Ignored by hand, so it stays until
Jason picks "curiosity" in its Role menu.
E2E harness: the fake API replays an answer with no expected hammer; tier 2
is "up to 15 not yet done" (tier 1 can shrink the pool).
Tests: 261 unit; E2E startup 5/5, ux 32/32, replay 12/12, reassess 12/12,
published 12/12.

## 2026-09-25 (night) — Every car on Opus 5.5; effort evaluation; Opus 5.5 refactors

**Overnight run.** All 37 active/pending candidates assessed on claude-opus-5-5
(skipping ones already done). 40 paid assessment calls, $12.48 measured
(average $0.31 at high effort). #263 (1994 911 Widebody) failed twice: the
output schema capped money ranges at $100k and its expected hammer was
$140k-185k. Cap raised to $2M; re-run succeeded (Reject: $182k all-in).

**Effort evaluation** (6 cars, one per profile, on a DB copy; baseline = the
stored Opus 5.5 @ high assessment; 21 runs, ~$4.80):

| variant | verdict same | next step same | mean abs score change | mean abs rating change | critical evidence agree | cost | time |
|---|---|---|---|---|---|---|---|
| Opus 5.5 high, re-run (noise floor, n=3) | 3/3 | 2/3 | 1.7 | 0.28 | 10/10 | $0.30 | 107 s |
| Opus 5.5 medium | 6/6 | 4/6 | 1.5 | 0.33 | 22/22 | $0.29 | 93 s |
| Opus 5.5 low | 6/6 | 3/6 | 2.7 (max 8) | 0.42 | 21/22 | $0.18 | 43 s |
| Sonnet 5 high (old quick tier) | 6/6 | 4/6 | 3.2 | 0.83 | 21/22 | $0.18 | 134 s |

Medium is inside high's run-to-run noise; low matches Sonnet's cost at 3x
the speed with half the drift. Small sample (6 cars): directional, not proof.

**Changes.** Defaults: full + tier re-assess on Opus 5.5 at medium, quick
tier on Opus 5.5 at low (was Sonnet 5 at high); a tier pinned to an older
model keeps high. Effort is recorded on every AI call and stored assessment;
cost estimates are per model + effort. Opus 5.5 calls carry the server-side
refusal fallback (API accepts it; probed). UI labels show model, effort and
measured cost; assessment chips show the model version.

**Open for Jason:** `.env` pins `SCOUT_MODEL_DEEP=claude-opus-5`, so the Assess
button still runs Opus 5 (at high). Change that line to `claude-opus-5-5` (or
delete it) to move it to Opus 5.5 at medium.

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

## 2026-09-28 — Facebook saved-list collector missed most saves

**Cause:** Facebook's Marketplace › Saved grid keeps only ~26 cards mounted and unmounts
the rest as you scroll. `collectSaved` scrolled to the bottom and then read the page, so it
only ever saw the oldest ~32 of 76 saves. Result: 22 saved cars never added (incl. the
newest), and 17 still-saved cars marked `removed` by the two-miss rule.

**Fix:** `S.collectScrolling` (extension/common.js) steps down the page and collects at every
step; the Facebook adapter uses it. Extension 0.4.4. A re-sync restores the wrongly removed
cars (their pages read active) and adds the missing ones.

**Test:** `e2e/e2e_saved_list.js` (suite `saved`, in the default run): real adapter code on a
virtualized, paged grid. Old code: 15/76, newest missing. New: 76/76.

### System State Summary
- Modified: extension/common.js, extension/adapters/facebook.js, extension/manifest.json,
  e2e/run.sh, e2e/e2e_saved_list.js (new), PROJECT_LOG.md.
- Open: other adapters still use `autoScroll` then collect once; the same failure would hit
  any site that virtualizes its saved list (not observed yet).

## 2026-09-28 — Collapsible filter rail; override-aware next step; Full-assess button

- Board: the Filters rail collapses to a 44px tab (‹ / ›); the cards take the width
  (3 → 4 columns at 1440px). Remembered in localStorage (`scout.railOpen`); phones unchanged.
- Bug: a car you overrode (e.g. #285 set to Reject) still counted in the "Contact now" tile,
  the Next-step filter and the table's Next column, while its card hid the chip.
  `nextStepOf` now returns nothing when `verdict_override` is set.
- Policy page: "Full-assess unassessed active candidates" (Opus 5.5 medium) beside Quick;
  `/api/assess-all` validates the tier and its task label says Full or Quick correctly.
- E2E: `e2e_ux.js` waits for the listing that was opened to render (`openDetail`) instead of
  any heading/pager (flaky once the export grew); new rail-collapse checks. ux 40/40.

### System State Summary
- Modified: docs/app.js, docs/styles.css, scout/server.py, e2e/e2e_ux.js, PROJECT_LOG.md.
- Open: every listing navigation reloads /api/export (now 6.3 MB, ~0.9 s) — opening a
  listing feels slower as the board grows. Server label fix needs a restart after the
  running Full assessment finishes.

## 2026-09-28 — Listing pages no longer reload the whole board

Every hash navigation re-fetched /api/export (6.3 MB, ~0.8 s to build) to catch outside
changes. Now: `db.data_version()` (SQLite `PRAGMA data_version` on one idle connection;
moves on any other connection's commit), `GET /api/data-version`, the export cached per
(DB path, version) with a 10-min cap, and the viewer reloads only when the version moved.
Opening a listing: ~1 s → ~50 ms. A change made elsewhere still shows on the next click.

Tests: tests/test_export_cache.py (2); e2e_ux adds three checks (0 export fetches while
stepping; 46–56 ms per listing; outside change visible) — ux 43/43. Full default E2E run:
all green except one reassess run where tier 2's browser request died with "Failed to
fetch" after 4 cars; 4 reruns (alone and after ux/replay) passed 12/12. Cause not found.

### System State Summary
- Modified: scout/db.py, scout/server.py, docs/app.js, e2e/e2e_ux.js, tests/test_export_cache.py (new), PROJECT_LOG.md.
- Needs: server restart to serve /api/data-version (the viewer falls back to reloading on every click until then).
- Open: intermittent reassess E2E "Failed to fetch" (1 in 5 runs).

## 2026-09-29 — Status + verdict override → "Your call"; "System Assessment"

The system's opinion (verdict, score, next step) is the **System Assessment**, never edited.
**Your call** (`status` + new `status_reason`) is yours: New, Interested, Contacted,
PPI Scheduled, Offer Made, Purchased, Pass. Pass wins everywhere (card chip, Contact-now
tile, Next-step filter, table, handoff); a Pass reason like "sold"/"gone" moves the car to
the comps. "Mark sold / gone" is `POST /api/listings/{id}/off-market` and leaves your call
alone; the availability check no longer writes status. Verify/Sold/Ended are gone.

Migration (`db.migrate_your_call`, in init_db, idempotent): Pass or override Reject/Do not
pursue → Pass (override reason kept); Pursue or override Pursue(/conditionally) → Interested;
Verify/Sold/Ended and "Maybe / verify" overrides → New; override columns cleared.
46 of 301 listings change — itemized in `data/your-call-migration_v1.md`; backup
`data/backups/scout_pre-your-call_2026-09-29.db`. Runs on the live DB at the next server start.

Tests: tests/test_your_call.py (mapping + migration on an old-schema DB); availability tests
rewritten to the new model; e2e_ux +6 checks (one field, System Assessment label, Pass with
reason, one chip, Pass-because-sold → comp). replay_real_pages judges what the replay did
(#83 really sold since its page was captured; that failed on the old code too).
Full E2E: startup 3/3, startup-board 2/2, ux 48/48, replay 12/12, reassess 12/12,
published 12/12, popup 4/4, saved 4/4. Unit: 268 passed.

### System State Summary
- Modified: scout/config.py, scout/db.py, scout/availability.py, scout/server.py, scout/handoff.py,
  scout/publish.py, docs/app.js, README.md, tests/test_availability.py, tests/test_publish_server.py,
  tests/test_your_call.py (new), e2e/e2e_ux.js, e2e/replay_real_pages.py, PROJECT_LOG.md.
- Needs: server restart (applies the migration to data/scout.db, serves the new endpoints).
- Dead columns left in place on existing DBs: listings.verdict_override / verdict_override_reason (always NULL).

## 2026-09-29 — Your call from the board card

Local board cards carry a "Your call" menu (styled as the chip; dashed "Your call…" when
New). Choosing Pass asks for an optional reason (Cancel leaves the car unchanged); "sold"/
"gone" moves it to the comps. Saves in place, re-renders, keeps the board's scroll position;
clicking the menu never opens the listing. The published board keeps the read-only chip.
Also: the listing page's headline verdict is labelled "System Assessment".
E2E ux 54/54 (+6: menu click doesn't navigate, set from card, scroll kept, cancelled Pass,
Pass with reason, Pass-because-sold leaves the board). Phone: no card-footer overflow.

## 2026-09-30 — Documents of the same kind no longer overwrite each other

`db.add_document` deleted any earlier document of the same kind on the listing. Attaching the
M Roadster Buyers Guide page (kind "other") to #276 deleted both seller replies (docs 9 and 11),
and the re-assessment ran without them. Now only single-copy reports (Carfax, AutoCheck, window
sticker) replace an earlier capture; seller replies, invoices, service records and inspections
accumulate. Restored #276's replies (doc 9 from data/backups/scout_pre-your-call_2026-09-29.db,
the 2026-09-29 reply re-added as doc 13) and re-assessed: Maybe / verify 55, c47, walk-away $16,543.
Checked every listing: #276 was the only one affected (#22's documents went with the listing).
Test: test_second_seller_reply_does_not_delete_the_first. Unit 269 passed.
Needs a server restart (the running server still replaces by kind).

## 2026-09-29 — Policy 1.9.0 (Z3 review); "sold" in the seller's words

Review of the 31 unsold Facebook Z3-family listings found one bug and four scoring gaps.
Details and reasons: scout/policy/POLICY_CHANGES.md § 1.9.0.

- Bug: the fast read took "sold" from seller prose and made live cars comps, forever
  (`_ever_sold`). Off auction sites the reader's "sold" now needs the page's own status
  (availability.classify_deterministic); a comp whose page reads live returns to candidate
  (ingest and `repair_roles`). Freed #71 and #269 (live BaT Cayman S, closes 2026-10-01).
- 1.9.0: VIN-less which-car contradictions are an `identity_unverified` question;
  assessment evidence (photo / history report / VIN / verified) re-profiles a car
  (`listings.profile_source`, also set by a hand-picked profile; syncs keep both);
  unknown-transmission comps count; walk-away from < 8 sales cannot Skip; classified
  "sold" prices count at 93%; keeper mission fit capped over 120k / 150k miles.
- Live DB: backup data/backups/scout_20260929-195751_pre-policy-1.9.db; migration +
  repair + free re-derivation (114 assessments). Opus 5.5 medium on #109 (re-profiled
  z3_30i -> z3_m, Skip -> Contact now), #97 (Skip: $23,999 vs walk-away $14,780 on 8
  sales), #59 (Contact now). About $0.75.

Tests: tests/test_policy_1_9.py (17; 12 fail on the old code). Unit 286 passed.
E2E: startup 3/3, startup-board 2/2, ux 54/54, replay 12/12, reassess 12/12,
published 12/12, popup 4/4, saved 4/4.

### System State Summary
- Modified: scout/{db,ingest,market,profiles,server}.py, scout/policy/{__init__,costs,engine,gates,
  preferences,schema,scoring}.py, scout/policy/POLICY_CHANGES.md, tests/test_policy_1_9.py (new), PROJECT_LOG.md.
- Needs: server restart (PID 73417 still runs 1.8.0 code; until then a sync could re-mark
  a seller's "sold" or reset #109's profile). Not committed.
- Open: board priority is not comparable across stages (a worked car at Docs, e.g. #276 at 17,
  vs an uncontacted one on merit, e.g. #59 at 52).

## 2026-09-30 — Policy 1.9.1: total-loss / branded-title value cut

#59's Carfax shows a 2012 insurance total loss under a clean Oregon title; its walk-away
($17,917) came from clean-history comps. Now a salvage/rebuilt/reconstructed title status, or
the event lines of an attached Carfax/AutoCheck ("TOTAL LOSS VEHICLE", "Total Loss Reported:" —
not the glossary every report carries), cut fair low/mid/high by 30% (rule of thumb, noted on
the car), so walk-away and offers follow. Score, gates and verdict unchanged. Affects #59 and
the 5 Rebuilt-title cars. tests/test_history_value.py; unit 290 passed.
Note: another session committed policy 1.9.0 (c16504e) and had uncommitted edits in
scout/config.py, scout/market.py, scout/profiles.py, tests/test_policy_1_9.py — left untouched.
Needs a server restart (startup re-derives stored assessments with the new costs).

## 2026-10-01 — Policy 1.9.2: Porsche borescope is a bonus, not a requirement

Jason: borescope evidence is almost never available and the risk at this age is low; add a
bonus when a car has one. A Porsche profile's critical-evidence item with "borescope" in its
key no longer raises a gate when missing or claimed only (was hard on the 987 Cayman S,
conditional elsewhere); a clean one adds 3 documentation points; a failed one still counts.
Seed 987 Cayman YAML wording softened. Re-derived the 23 assessed Porsches (no AI): every
borescope gate gone; scores up 0-8 where the documentation cap had applied (#131 59->67,
#82 54->59, #246 71->74 with the bonus); verdicts unchanged — they rest on other gates and on
the model's own documentation ratings, which a fresh assessment would redo.
Tests: guide scenario 1 rewritten to the new rule; Porsche-only check. Unit 292 passed.
- 2026-10-01 follow-up: a failed borescope is a hard reject on every Porsche profile (generated
  profiles had it "conditional", which only capped the verdict). Generated Porsche profiles'
  borescope labels now say "bonus when present" (DB edit); their dealbreakers are unchanged
  (a first pass wrongly dropped the "scoring found" ones; restored from backups and checked).

## 2026-10-01 — Facebook "Today's picks" thumbnails were stored as car photos

Every Marketplace listing (85/85) carried ~20 feed thumbnails from under the listing (#109's
included a different black Z3), and assessments saw them. They are square-cropped
(stp=c<x>.<y>.<w>.<h>a_); a listing's own photos never are (checked on all 85; group posts
unaffected). Filter in extension/adapters/facebook.js (0.4.5) and scout/ingest.py
(is_junk_photo; clean_all_photos also runs at server start). Cleaned the DB: 1,647 photos
removed from 85 listings (#45, a sold listing showing only the feed, now has none).
Then re-assessed the 39 affected active candidates on Opus 5.5 medium. Unit 294 passed; E2E saved 4/4.

## 2026-10-04 — Saved photo copies; no more broken tiles

Facebook photo links carry a signed expiry (oe=…) a few days after the sync that captured them;
on 10-04 nearly every stored Facebook link had expired (21 tiles broken, the rest only cached by
the browser), and assessments would have run without photos. Now:
- scout/ai/photos.py: one saved-copy cache (data/photo_cache) keyed by the Facebook file name, so a
  re-signed link finds its copy (old full-URL keys still read); fetch_cached / cached_path / thumbnail.
- server: a photo worker saves photos at startup and right after every /api/ingest (candidates and
  curiosities up to 12, comps their card photo); GET /api/photo serves the saved copy (tracked URLs
  only, not an open proxy). SCOUT_PHOTO_CACHE=0 turns the worker off (tests, E2E).
- board: every photo through /api/photo; on failure it tries the original link once, then a
  "Photo expired. Re-sync to refresh" placeholder instead of a broken image.
- publish: a 640px card image per car (data/t/<id>.jpg, thumb_file) from the saved copy.
Tests: tests/test_photo_cache.py (3); e2e_ux +2 photo checks (56/56). Unit 297 passed.
Open: no pruning of orphaned copies yet (~186 MB → ~300 MB expected).

## 2026-10-04 — Facebook "Today's picks" made live cars read as pending

#109 vanished from the board (default filter: active) because today's sync marked it pending:
the "Pending" line belonged to a $50 drum box in the "Today's picks" feed under the listing.
The extension and the server both read status from the first ~1,500–3,000 characters of the
page, which include that feed. Now ingest.listing_own_text() cuts a Facebook page at
"Today's picks" for detect_availability and availability.classify_deterministic, and the
extension (0.4.6) reads Sold/Pending only from the listing's own text. Restored 23 falsely
pending listings to active (#109 among them); #293 and #283 stay pending (their own text says so).
Checked the "sold" Facebook comps whose evidence looked weak: #67, #94, #103, #104 really are sold
(their own header reads "Sold · <title>"); #52 redirected to unavailable_product; #294 marked
Purchased by hand. Test added; unit 299, E2E replay 12/12.

## 2026-10-10 — Walk-away labels: expected auction price; budget-only when comps are thin

- Auction cards compared the walk-away with the expected hammer while showing the early bid
  (#338 "$5,000 · 3% over"; #336). Card now: "expected ~$X · N% over expected"; listing header:
  "early bid; expected to sell ~$X" instead of a percentile of the early bid.
- With fewer than 3 comparable sales the walk-away is only what the mission budget allows
  (#253: "$32,904" next to a $23,000 car the comps call overpriced). Card, next-step panel and
  cost table now say "budget limit … too few sales to value" instead of "walk-away".
- Open: e2e_ux "board keeps your place" fails 7317 -> 6877 (440px) on the current data with
  the previous commit too (not caused by these changes); to investigate.

## 2026-10-12 — Cars & Bids sync pulled in 71 auctions you were not watching

The Oct 11 watch-list sync collected every li.auction-item on the page: 71 (Ford GT, SLR, Singer…)
against 2 watched live auctions + 20 watched results. The adapter now takes only cards marked
"Watched" (extension 0.4.7; checked in the signed-in watch list: 22/22 cards carry it). Cleanup:
69 listings created by that sync and not watched set to ignored (role_user_set, reversible);
the 2 watched kept (#356 Clio V6, #422). The six 987 Boxster S comps added 2026-10-10 untouched.

## 2026-10-14 — BRZ / 86 / FR-S share one comp pool; full re-assessment of the board

- `market.COMP_TWINS` / `comp_keys()`: a BRZ, 86 or FR-S values against all three (fair value,
  offers, walk-away, comps given to the assessment). `db.list_listings(profile_key=[...])` takes a
  list. Commit b0d1ba6; tests/test_comp_twins.py; unit 302.
- Re-assessed all 34 active, non-Pass candidates on Full (Opus 5.5 medium), including the 5
  never-assessed BaT cars (#427, #430, #431, #435, #436); 34/34, run from a separate process.
- Side effect to watch: the shared pool has 3 sales (two ~90k-mile FR-S at $10–11k, one BRZ
  $16,400), so the 86/BRZ value switched from asking prices to these sales and fell (#445
  $18.5–21k → $11.7–15.8k). Thin; more 86/BRZ sold comps needed.
- Not changed: the detail page's "Market context" box still counts one profile's comps.

### System State Summary
- Server needs a restart to pick up the comp-pool change (the batch ran outside it).
- Uncommitted from another session: scout/config.py, scout/market.py (bmwcca), scout/profiles.py,
  tests/test_policy_1_9.py.
- Open: task_15cf6b69 (board scroll jump); #253 waiting on seller; G37 #443 auction ends 2026-10-15.

## 2026-10-14 (later) — 86/BRZ/FR-S sold comps; ignored listings no longer price cars

- Added 13 stock, manual 2013–2020 results (#446–458): Cars & Bids 6 (one reserve-not-met), BaT 7.
  Skipped supercharged/turbo/track/modified cars. #256 corrected to reserve not met at $14,500.
  #257 and #260 (modified) set to ignored. Backup: data/backups/scout_pre-twin-comps_2026-10-14.db.
- `market.fair_value` now skips role=ignored (commit a06c2e8; unit 303). Also moved: #436 128i,
  the GX470s and the Caymans slightly (each had one ignored sale in its pool).
- 86/BRZ fair now ~$15–21k from 13 sales (was $11.7–15.8k from 3); walk-aways $18.0–19.4k.

## 2026-10-18 — Fit score; CARFAX sync; Cars & Bids ended fix; VIN-bearing listing links

- **Fit score** (scout/policy/fit.py, POLICY_CHANGES.md): a 0–100 rank beside the assessment
  score, which bunched at 41–64. Board sorts by Fit by default; detail page shows the parts.
  No verdict or gate changed. tests/test_fit.py.
- **CARFAX** followed cars + listing pages (extension 0.5.1; negative photo ids). 8 cars synced.
- **Cars & Bids**: a live auction is no longer filed as ended on the AI's word (#514 twice);
  watch-list titles read from .auction-title (extension 0.4.9).
- **Export**: CARFAX and Autotrader private-seller links keep their VIN (it is the address);
  every other VIN still stripped. #463's stored link was saved as "[VIN]" and can't be rebuilt.
- Data: #482/#499 Pass (sold to someone else); NC Miata comps #523–526; #408 restored after an
  accidental overwrite (backup scout_pre-nc-comps_2026-10-18.db).

### System State Summary
- Server needs a restart for the Fit score to appear on the board.
- Uncommitted from another session: scout/config.py, scout/market.py (bmwcca), scout/profiles.py,
  tests/test_policy_1_9.py.
- Dead code noted, not removed: preferences.EXCLUDED_MODELS (unused; still lists miata/is350).

## 2026-10-19 — Roof-aware Miata fair value (a7db284)
- `scout/market.py`: `roof_type()` (rf / hardtop / soft / None). A car is valued against same-roof comps when there are >= 3; otherwise other-roof comps are scaled by a roof premium (learned when >= 3 soft and >= 3 fixed-roof sales, else +12% default, bounds 1.0-1.35). Non-RF NDs count as soft tops.
- Effect: #515 (2017 RF) fair $18.4-20.7k -> $20.6-23.2k, walk-away $18,683 -> $21,165 (default premium, no RF sales in the pool yet). ND soft tops unchanged. #537 (Mesa NC, insurance total loss) set to Pass.

### System State Summary
- Tests: 316 passed. Uncommitted, other session: scout/config.py, scout/market.py (bmwcca line), scout/profiles.py, tests/test_policy_1_9.py.
- Open: no RF sales comps (premium is a default); NC soft tops without "soft top" wording read as roof unknown and are valued against the hardtop-heavy NC pool; #109 status vs reason mismatch unconfirmed.

## 2026-10-19 — RF sales comps (79a4ef7)
- Added 26 sold ND RF manual comps (12 BaT, 14 Cars & Bids; sold Mar 2024 to Sep 2026). Excluded: 30th Anniversary, turbo/supercharged, heavily modified, automatics, bid-to/no-sale, a 166k replacement-engine car, and a duplicate relist. Backup: data/backups/scout_pre-rf-comps_2026-10-19.db.
- Fix: same-roof sales are now picked from the whole generation before the year window (the RF sales had crowded soft tops out of soft-top cars' windows). Roof premium is learned from the whole pool.
- Result: #515 fair $18,424-20,739 on 15 RF sales, walk-away $18,739 (was $21,165 on the +12% default). Implied RF premium vs ND soft top, year- and mileage-adjusted: +1.9% (26 RF vs 8 soft sales).

### System State Summary
- Tests: 317 passed. Other session's uncommitted edits untouched (config.py, market.py bmwcca line, profiles.py, test_policy_1_9.py).
- Open: the +12% default premium is now known to be high for ND RF; it is used only when a roof has < 3 sales (NC soft tops). Only 8 ND soft-top sales; more would firm up the +1.9%.

## 2026-10-20 to 2026-10-22 — Sweep, Z4 reopen, Fit fixes, shortlist
- Commits: 62b53d1 / 24e8062 (Fit accident parsing: hyphenated "minor-damage"; a "no" heading a list), 70f0830 / f7e4ad1 (BMW Z4 reopened; tests updated). Stored policy_state exclusions updated to match.
- Tracker adds: #569 (total loss, Pass), #570 (MD NC, Pass), #577 2008 Miata GT (BaT, Carfax doc), #579 BaT Z4 3.0i (sold $18,750), #596 128i conv, #597 Torrance Z4 3.0si (Pass), #607 Z4 3.0si Coupe, #630 / #631 gray RF Clubs. Comps added: 16 Z4 3.0i, 9 Z4 3.0si roadster, 4 Z4 coupe 3.0si manual (BaT).
- Status changes per Jason: Sport-trim NDs, Z3s over 60k (except #307 restored), #109, #519, #461 context, #572 passed.

### System State Summary
- Tests: 317 passed. Other session's uncommitted edits untouched (config.py, market.py bmwcca line, profiles.py, test_policy_1_9.py).
- Server restart pending so the board picks up the roof valuation, Fit fixes and Z4 reopen.
- Open: nc-miata-hardtop-watch scheduled task has never run; no Wrangler/E93/981 comps in tracker.
- Shortlist and offer numbers are in memory (second-car-status-2026-10-22).

## 2026-10-23 — Free history-report auto-fetch; stale-Reject fix
- Fix (`scout/stage.py` `documents_read_by`, used by `_rederive_assessment` and `/api/rescore`): a free re-derivation stages only on documents attached at or before the stored assessment ran. Before, attaching any report moved the car to "docs" and turned the old assessment's "missing" items into observed gaps: a clean Carfax flipped #637 from Maybe to Reject. `tests/test_stage.py::test_document_attach_and_delete_rederive_stage` updated to the new rule.
- Server: `POST /api/history-reports/wanted` (which listing/kind pairs lack a report) and `POST /api/history-reports` (attach a fetched free Carfax/AutoCheck; never replaces one; rejects < 800 chars or a VIN mismatch; queues a full re-assessment, ~$0.25, only for an already-assessed active candidate).
- Extension 0.5.3: autotrader.js and carfax.js report free report links (`detail.history_report`; Autotrader `/experian?` = free AutoCheck, autocheck.com "Buy" links skipped; CARFAX listing "View FREE CARFAX Report"). background.js fetches wanted reports after each ingest batch and on "Add this listing". document.js reads same-origin iframe[srcdoc] text (Autotrader renders its report there), waits for it, takes a kind hint; now also loaded on autotrader.com.
- Data today: AutoCheck #648/#646, Carfax #637 (free), Carfax #645/#647 (Jason bought) attached; the five fully re-assessed (~$1.25). #639/#638/#642/#649/#644 Pass per Jason.

### System State Summary
- Tests: 329 passed (13 new in tests/test_history_reports.py). E2E: new `reports` suite 8/8; popup 4/4, reassess 12/12, startup 5/5. `ux` fails at the scroll-memory check ("reading 'dataset'") on the original code too: pre-existing, not from this change.
- Modified: scout/stage.py, scout/server.py, tests/test_stage.py, tests/test_history_reports.py (new), extension/{manifest.json, background.js, adapters/autotrader.js, adapters/carfax.js, adapters/document.js}, e2e/e2e_history_reports.js (new), e2e/run.sh. Not committed.
- Needs: restart the tracker server and reload the extension (0.5.3) in Chrome. Free-report links on Cars.com / CarGurus are not detected (unverified markup).
- Other session's uncommitted edits untouched (config.py, market.py, profiles.py, test_policy_1_9.py).

## 2026-10-23 — Board: History report filter
- `docs/app.js`: rail section "History report" (Any / Has one / Missing). Counts a Carfax or AutoCheck on the card's own listing or a folded VIN twin; applied after folding, like Next step; the Re-assess tier ranking ignores it. No card changes.
- `e2e/e2e_ux.js`: two checks (Has + Missing = Any; Missing shows no report chip). Live board: 13 has / 61 missing of 74.

### System State Summary
- Not committed. The ux suite still stops at the old scroll-memory check here; the fix is ad1537a on claude/competent-maxwell-d4d8b1 (not merged).

## 2026-10-23 — Policy 1.9.3: fee-inclusive auction comps; Carfax Value reference
- `scout/market.py`: `realized_price` replaces `realized_factor`. An auction sale counts at hammer + buyer's fee (`auction_fee`, from the policy state's fee table: 5%, $250 min, $7,500 cap; both FAQs checked 2026-10-23, BaT uses 10% / $4,000 cap in some non-car categories). Classified last asks keep x0.93.
- `scout/policy/costs.py`: an auction car's fair range is converted back to a hammer (`_hammer_for`) before walk-away and offers, since its own fee is a cost line. `POLICY_VERSION` 1.9.3; POLICY_CHANGES.md entry.
- `scout/publish.py` + `docs/app.js`: `carfax_value` read from carfax.com listing text or an attached Carfax report; car page shows "Carfax Value · reference" under Market fair value. Nothing scores on it.
- Data: #467 MR2 sold_price $16,000 (was its last-seen bid $9,300); #577 marked sold $20,942 and moved to comps.
- Effect (DB copy, after the startup rescore): #515 walk-away $18,648 -> $19,681, #630 $19,846 -> $20,938, #637 $9,162 -> $10,629, Z3s +$700-900. No verdict changed.

### System State Summary
- Tests: 336 passed (7 new in tests/test_fee_inclusive_value.py; one 1.9.0 test in tests/test_policy_1_9.py updated to the new rule). E2E: startup 5/5, reassess 12/12, reports 8/8. `published` 11/12: its VIN check flags Autotrader /marketplace/buy/<VIN> links that publish.py has allowed since 6ef0e02 (2026-10-18); pre-existing test mismatch.
- E2E note: the fake Anthropic server uses port 8799; don't run a side tracker on 8799 during e2e.
- Needs: restart the tracker server so the 1.9.3 rescore runs on the real DB.
