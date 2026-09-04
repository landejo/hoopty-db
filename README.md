# Hoopty Scout

Point it at your **saved** vehicle listings on Facebook Marketplace, CarGurus, Cars.com,
Cars & Bids, and Bring a Trailer. It pulls every listing into a local database, normalizes
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
   - Facebook: Marketplace → You → Saved
   - CarGurus: Saved cars · Cars.com: Saved · Cars & Bids: Watch list · BaT: Account → Watch list
3. Click the Hoopty Scout toolbar icon → **Sync saved listings**. Leave *Include sold / ended* on
   so those rows become comps. The popup can be closed; progress continues.
4. Open the workbench (http://127.0.0.1:8765). Cards show a preliminary Haiku score. Open a
   card → **Analyze with Opus** for the deep read (roughly $0.50–1.50 per listing).
5. **Publish** (header button) exports `docs/data/scout.json`, commits, and pushes.

On a listing page (not the saved list) the popup offers **Add this listing** for one-offs.

## What the AI does

| Step | Model | When | Cost (approx.) |
|---|---|---|---|
| Normalize: facts, profile pick, quick read, red flags, prelim scores | `SCOUT_MODEL_FAST` (Haiku) | every new/changed listing, comps included | ~$0.01 |
| Profile generation for an unknown make/model/generation | `SCOUT_MODEL_DEEP` (Opus) | once per new model, marked *unverified* | ~$0.30 |
| Deep analysis: verdict, deal score, checks, PPI focus, questions, pricing, negotiation | `SCOUT_MODEL_DEEP` (Opus) | only when you click | ~$0.50–1.50 |

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
