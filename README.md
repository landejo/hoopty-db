# Hoopty-Matic

Point it at your **saved** vehicle listings on Facebook Marketplace, CarGurus, Cars.com,
Autotrader, Cars & Bids, and Bring a Trailer. It pulls every listing into a local database, normalizes
it with a fast model, scores it against a per-model buyer profile, and runs a deep Opus
analysis on demand. Sold listings and ended auctions become market comps. A static viewer
publishes to GitHub Pages so the board is readable anywhere.

```
extension/   Chrome extension (MV3). One adapter per site. Posts to the local server.
scout/       Python: SQLite store, ingest pipeline, AI calls, FastAPI server, publisher.
scout/profiles/   Seed buyer profiles (YAML): z3_30i, z3_m, gx470, gx460.
docs/        Static viewer. Served locally by the server; published to GitHub Pages.
tests/       pytest. No paid AI calls anywhere in the suite.
```

## Setup (once)

```bash
python3.11 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cp .env.example .env      # add ANTHROPIC_API_KEY
```

Load the extension: `chrome://extensions` → Developer mode → **Load unpacked** → pick `extension/`.

## Daily use

1. Start the server: `.venv/bin/python run.py` (http://127.0.0.1:8765).
2. In Chrome, open a saved-listings page:
   - Facebook: https://www.facebook.com/marketplace/you/saved
   - CarGurus: https://www.cargurus.com/Cars/myAccount/saved-listings (the Sold cars tab is read too)
   - Cars.com: https://www.cars.com/profile/saved-cars/
   - Autotrader: https://www.autotrader.com/account/cars
   - Cars & Bids: https://carsandbids.com/watch-list/ (live auctions; ended ones are re-checked on the next sync)
   - Bring a Trailer: https://bringatrailer.com/watchlist/ (live auctions; ended ones re-checked on the next sync)
3. Click the Hoopty-Matic toolbar icon → **Sync saved listings**. Leave *Include sold / ended* on
   so those rows become comps. The popup can be closed; progress continues.
4. Open the workbench (http://127.0.0.1:8765). Cards show a preliminary Haiku score. Open a
   card → **Analyze with Opus** for the deep read (about $0.35 per listing, measured 2026-09-23).
5. **Publish** (header button) exports the data and force-pushes it to `gh-pages` (see
   "GitHub Pages" below) — `main` is untouched.

On a listing page (not the saved list) the popup offers **Add this listing** for one-offs.

### Board tools (local workbench)

- **Source link**: every card (top-right of the photo) and table row links to the
  original listing; it opens in a new tab.
- **Check availability**: re-opens every live listing, plus ones that vanished
  from a saved list, in background tabs of your own browser (extension 0.4+;
  the popup has the same button). The page decides: "Sold for $X" / a Sold
  status line → sold; "no longer available" / removed → sold (delisted); "Bid
  to" / reserve not met → ended. Pages with no clear signal go to the fast model,
  and its answer counts only if it quotes the page word for word. A page that
  did not load, a bot wall or a login page changes nothing. A sold/ended car
  becomes a market comp (it feeds fair value from then on), its status becomes
  **Sold** / **Ended** (never over **Purchased**), and policy 1.7.0 gates its
  verdict to **Do not pursue**. Every stored assessment in an affected model is
  then re-derived for free so offers and fair values use the new comp.
- **Re-assess tier N**: re-assesses on `SCOUT_MODEL_TOP` (Opus 5.5 by default)
  in tiers of 15, down the whole board in Pursue-next order (your filters are
  ignored). Each press takes the next 15 cars this cycle has not re-assessed yet;
  a car whose assessment failed stays pending and leads the next tier. The cycle
  restarts at tier 1 once 3 days have passed since tier 1 ran, or once every car
  has been done. Nothing runs unless you press the button. The confirm dialog
  lists the cars with their board rank and a cost estimate from measured calls.
- On a listing page, **‹ ›** (or the ← / → keys) step through the board's order;
  going back to the board returns to where you were.

### Auto-publish

A *sitting* is a run of workbench activity with no gap longer than 15 minutes.
Activity is the viewer's heartbeat (sent once a minute only while you are
actually using it: input in the last 2 minutes, tab visible) or any
data-changing API call (edits, assessments, syncs, availability checks).
When a sitting that changed data ends, the server publishes; a long sitting
also publishes a checkpoint every 45 minutes. Publish skips the push when the
site is unchanged (a new timestamp alone is not a change). Pending changes
survive a server restart. The header pill shows what is pending; set
`SCOUT_AUTOPUBLISH=0` to turn it off, or tune `SCOUT_AUTOPUBLISH_IDLE_MIN` /
`SCOUT_AUTOPUBLISH_CHECKPOINT_MIN`.

## Assessment policy

**What the board tells you (policy 1.8.0).** Before you have contacted a
seller, each car gets a **next step**: *Contact now*, *Watch* or *Skip*, with
the reason. It ranks on **known merit** (everything except the records you
have not asked for yet; those are the open-questions to-do list) and on the
**walk-away** price: the lower of your budget for that car's mission and what
this car is worth (top of its fair range, less known work and open questions).
Set a budget per mission on the Policy page with `budgets_by_mission`.
**Curiosities.** A car over `curiosity_over_price` ($40,000; the higher of the
listed price and the expected auction hammer) is followed as a *curiosity*:
it has its own **Curious** tab, stays synced and availability-checked, becomes
a comp when it sells, but is off the candidates board and out of every bulk
assessment. It returns to the candidates if the price drops under the line.
Pick "curiosity" in a listing's Role menu to set one by hand; a role you set
wins over the price rule both ways. After
questions are sent it says *Follow up*; from documents on, the verdict governs.

`scout/policy/Jason_Car_Assessment_Guide.md` is the authoritative, human-readable
description of how listings are judged. The code encodes it:

| Piece | Where | Editable |
|---|---|---|
| Durable preferences, category weights (30/25/15/15/10/5), verdict bands, exclusions | `scout/policy/preferences.py` | in code, bump `POLICY_VERSION` |
| Temporary state: urgency mode, budget, current vehicles, active exclusions, fees, transport, tax | `scout/policy/state.py` defaults; overrides in the `settings` table | **Policy** page on the local workbench |
| Model-specific critical evidence (borescope, rear structure, timing belt, …), default mission, risk reserve | profile YAML in `scout/profiles/` (AI-generated profiles get theirs from the model) | YAML |
| Gates, score, confidence, costs, verdict | `scout/policy/{gates,scoring,costs,engine}.py` | deterministic code |
| What the model may return | `scout/policy/schema.py` (pydantic; invalid output is rejected, never stored) | code |

The model interprets evidence only: facts with provenance (`receipt`, `photo`,
`history_report`, `external_vin`, `listing_text`, `seller_comment`,
`seller_claim`, `ai_inference`), contradictions, the status of each critical
evidence item, gate flags (yes / no / unknown), 0-10 category ratings, and the
qualitative lists. Deterministic code then applies hard, conditional,
configuration, and strategy gates, computes the 100-point score with caps, a
separate 0-100 confidence, the risk-adjusted all-in cost and backward-solved
maximum price, and the verdict (`Pursue`, `Pursue conditionally`,
`Maybe / verify`, `Reject`, `Do not pursue`). Every stored assessment carries
the policy version that produced it; the raw listing is stored separately.

**Missions** per listing: `enthusiast_bridge` (default for fun cars, manual
required), `pragmatic_bridge` (an automatic is allowed but must win decisively
and is labeled as solving the immediate problem), `future_keeper`,
`utility_capability` (SUVs; automatic fine). Change it on the listing page.

**VIN services (free):** NHTSA vPIC decodes every VIN at sync time (year, make,
model, series, displacement, body, plant) and NHTSA recall campaigns are
listed; decode-versus-listing mismatches become contradictions. VIN history
across listings in this database (prior sales, relist markup, mileage and
disclosure changes) feeds the assessment. No paid history service is wired.

## Provenance: the same car, not the model

Every listing attaches to one **vehicle record per VIN** (`vehicles`), with a
`vehicle_events` timeline (Listed, Price reduced, Sold, Bid to / reserve not
met, Withdrawn, Relisted, Seller decided to keep, Dealer acquisition). Listings
without a VIN get a provisional record on a year/make/model/color fingerprint
that merges into the VIN record when the VIN appears. Sync data alone builds
the timeline; an **investigation** extends it across the web.

1. On a listing page click **Investigate provenance**. The server writes the
   guide's query set (quoted VIN, VIN + model, title + mileage, listing id,
   seller + model, distinctive combinations) for DuckDuckGo, Bing, Google,
   BaT, eBay sold, Classic.com, Reddit, Facebook posts and Marketplace.
2. Open the extension popup and click **Run queued investigations**. Searches
   run in background tabs in your own browser (so Facebook posts and groups
   are visible), known listing pages among the hits are opened and read, and
   everything posts to the server. No paid search service is used.
3. The deep model classifies each hit: `confirmed` (exact VIN), `strongly_likely`
   (plate, or identical photos plus coherent mileage/color/equipment/location/
   chronology), `possible`, or `not_established`; extracts events with a
   price type (`verified_sale`, `winning_bid`, `high_bid_reserve_not_met`,
   `advertised_sold`, `asking`, `estimated`); pulls seller statements (sold,
   withdrawn, decided to keep, reasons, problems, PPI, track use, earlier
   prices); and splits work before versus after the prior sale.
4. Code computes the price progression from the last documented price
   (transaction-grade first), dollar and percent change, elapsed time, mileage
   added, and flags: very recent resale (≤6 months), recent resale (≤24
   months), rapid relisting (≤90 days), material markup (>10%), major markup
   (>20%), and not actively available when a withdrawal post-dates the listing.
   Only confirmed and strongly-likely events count; possible matches are shown
   separately and never used.

Findings sit at the top of the listing page and feed the assessment: a
withdrawal is a `Do not pursue`, a markup caps price/value and anchors the
price ceiling to the last documented price plus documented post-sale work.
Asking prices are never described as sale prices.

## What the AI does

| Step | Model | When | Cost (approx.) |
|---|---|---|---|
| Normalize: facts, profile pick, quick read, red flags, prelim ratings | `SCOUT_MODEL_FAST` (Sonnet 5 by default; Haiku 4.5 is the cheaper option) | every new/changed listing, comps included | ~$0.02 |
| Profile generation for an unknown make/model/generation | `SCOUT_MODEL_DEEP` (Opus) | once per new model, marked *unverified* | ~$0.30 |
| Full assessment: evidence interpretation for the policy engine (facts, provenance, critical evidence, flags, ratings, questions, PPI focus), with up to 12 photos (downscaled to 1568 px, cached in data/photo_cache) | `SCOUT_MODEL_DEEP` (Opus 5.5, `medium` effort) | only when you click, or "Re-assess next tier" | ~$0.29 |
| Quick assessment: identical prompt and photos at `low` effort, for triage across the board | `SCOUT_MODEL_MID` (Opus 5.5, `low` effort) | only when you click, or "quick-assess all" | ~$0.18 |

Every call is logged with tokens, effort and estimated cost in the `ai_calls` table (`GET /api/ai-spend`); the static part of each system prompt is cached. Opus 5.5 calls carry the server-side refusal fallback. Effort per tier is set with `SCOUT_EFFORT_DEEP` / `_MID` / `_TOP`; the 2026-09-25 evaluation behind the defaults is in PROJECT_LOG.md. Every model response passes through `scout/coerce.py` before it is stored. Raw responses are
written to `data/last_*.log` for debugging.

## GitHub Pages

Settings → Pages → *Deploy from a branch* → `gh-pages` / `/` (root). `main` carries no
data — the site lives entirely on the `gh-pages` branch, which Publish rewrites from
scratch on every run: one orphan commit (no parent, no accumulated history), force-pushed.
The published JSON omits seller contact details, private-seller names, VINs, and the raw
listing text, but everything else (your notes, statuses, scores) is public to anyone with
the URL. The Pages copy is read-only; edits and analyses happen on the local server, then
Publish. Data is split into `data/index.json` (everything the board/market/profile views
need) and `data/l/<id>.json` (one file per listing, fetched on demand when you open its
detail page).

Publish refuses to write or push if any VIN, phone number or email survives the scrub
(`publish.find_leaks`), reports git failures instead of claiming success, and builds the
`gh-pages` commit with git plumbing (a temporary index + work-tree) so the local `main`
checkout is never touched. The local API only answers the workbench and the extension
(Host + Origin check), not other web pages.

**One-time setup (lead only, at switchover):** GitHub → repo Settings → Pages → change
*Branch* from `main /docs` to `gh-pages` / `(root)`. Until that's done, Pages keeps
serving the old `main`-branch copy even after this branch's changes land.

**Backups:** `data/scout.db` is copied on every server start and before every publish to
`~/Documents/Hoopty Scout Backups` (override with `SCOUT_BACKUP_DIR`; the newest 14 are kept).

## Adapters are best-effort

The five site adapters read the DOM of pages you are logged in to. They rely on URL patterns
plus visible text, not on fragile class names, but a site redesign can still break one.
Symptoms: "found 0 saved listing(s)" or empty descriptions. Fix in `extension/adapters/<site>.js`;
`common.js` has the shared collectors.

## Tests

End-to-end first: `e2e/run.sh` builds a throwaway sandbox (a clone whose git
origin is a local bare repo, a copy of `data/scout.db`, the server on :8766)
and drives the real viewer in Chromium with Playwright. Your data, your server
on :8765 and GitHub are never touched, and no paid AI call is made.

```bash
e2e/run.sh                   # startup, ux, replay, reassess, published (about 3 minutes)
```

```bash
e2e/run.sh availability      # the real extension against the live sites (~10 minutes)
```

- `startup`: a fresh server re-derives every assessment made under an older
  policy (free, in the background); sold/ended cars then read Do not pursue.
- `published`: publishes to the sandbox origin, serves exactly what was pushed,
  and checks it against the local board: same cars in the same order, listing
  pages load, no local-only controls, no VIN/phone/email in any file.
- `ux`: board source links, scroll/focus on return, ‹ › and ← / →, re-normalize,
  hand-set roles, the re-assess confirm and its no-key error, phone layout.
- `replay`: real listing pages captured from a normal browser (signed-in
  Facebook, CarGurus, Autotrader; sold, delisted and live), sent through the
  server's availability endpoints and scored against what the page showed.
- `reassess`: the tier button through the real Anthropic SDK against
  `e2e/fake_anthropic.py`: tier 1, tier 2, the 3-day restart, what was stored,
  and the auto-publish push to the sandbox origin.
- `availability`: the extension opens every tracked listing. Sites that block
  automated browsers (Cars.com, CarGurus, Autotrader, signed-out Facebook) must
  come back "unclear" and change nothing.

Unit tests cover the rules underneath:

```bash
.venv/bin/python -m pytest -q
```
