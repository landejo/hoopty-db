# Hoopty Scout

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
3. Click the Hoopty Scout toolbar icon → **Sync saved listings**. Leave *Include sold / ended* on
   so those rows become comps. The popup can be closed; progress continues.
4. Open the workbench (http://127.0.0.1:8765). Cards show a preliminary Haiku score. Open a
   card → **Analyze with Opus** for the deep read (roughly $0.50–1.50 per listing).
5. **Publish** (header button) exports `docs/data/scout.json`, commits, and pushes.

On a listing page (not the saved list) the popup offers **Add this listing** for one-offs.

## Assessment policy

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
| Normalize: facts, profile pick, quick read, red flags, prelim scores | `SCOUT_MODEL_FAST` (Haiku) | every new/changed listing, comps included | ~$0.01 |
| Profile generation for an unknown make/model/generation | `SCOUT_MODEL_DEEP` (Opus) | once per new model, marked *unverified* | ~$0.30 |
| Full assessment: evidence interpretation for the policy engine (facts, provenance, critical evidence, flags, ratings, questions, PPI focus), with up to 12 photos | `SCOUT_MODEL_DEEP` (Opus) | only when you click | ~$1 |
| Quick assessment: identical prompt and photos on the mid tier, for triage across the board | `SCOUT_MODEL_MID` (Sonnet) | only when you click, or "quick-assess all" | ~$0.30 |

Every model response passes through `scout/coerce.py` before it is stored. Raw responses are
written to `data/last_*.log` for debugging.

## GitHub Pages

Settings → Pages → *Deploy from a branch* → `main` / `/docs`. The published JSON omits seller
contact details, private-seller names, VINs, and the raw listing text, but everything else
(your notes, statuses, scores) is public to anyone with the URL. The Pages copy is read-only;
edits and analyses happen on the local server, then Publish.

## Adapters are best-effort

The five site adapters read the DOM of pages you are logged in to. They rely on URL patterns
plus visible text, not on fragile class names, but a site redesign can still break one.
Symptoms: "found 0 saved listing(s)" or empty descriptions. Fix in `extension/adapters/<site>.js`;
`common.js` has the shared collectors.

## Tests

```bash
.venv/bin/python -m pytest -q
```
