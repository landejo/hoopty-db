# Policy changes relative to the guide

The guide (`Jason_Car_Assessment_Guide.md`, v1.1) is the source. Where the code
deliberately departs from its text, the change is recorded here with the reason,
so the guide can be updated when Jason next revises it.

## 1.6.0 (2026-09-23)

**A missing hard item is required, not yet a finding, until the PPI.** A
model-critical item marked `severity: hard` (e.g. the 987 Cayman S borescope)
that is merely missing or seller-assurance-only no longer rejects the car at the
Listing, Questions or Docs stage. It becomes a conditional gate tagged
"(required before purchase)" (`scoring.defer_required`), counted as an open
question that caps the verdict at `Maybe / verify` and blocks `Pursue
conditionally`. It is also the first next step and leads the headline. Still
missing at the PPI stage, it stays a hard gate: `Reject`. A FAILED hard item
rejects at any stage, as before. The documentation cap for a missing hard item
(10) is unchanged. Before this, every Cayman S on the board read `Reject` before
the seller had been asked anything. Jason's instruction, 2026-09-23.

## 1.5.0 (2026-09-23)

**Reject needs a reason.** The anchored rubrics added in tranche 5 made ratings
more conservative (price 5 at fair value, logistics lower for distant cars), so a
fairly priced car with nothing wrong could fall under the 45 band and read as
`Reject`. Below 45, the verdict is now `Reject` only when something is observed
against the car (an observed conditional, including open questions that should
have been settled by the current stage) or the score is under 35
(`REJECT_FLOOR_NOTHING_OBSERVED`). Otherwise it is `Maybe / verify` with the
reason "low, but nothing observed wrong; low priority". The priority ranking
already orders these below stronger cars. Hard, strategy and configuration gates
are unchanged. Jason's instruction, 2026-09-23.

The deterministic headline for a score-only verdict now names the two weakest
categories (by share of their points).

## 1.4.0 (2026-09-22)

Almost every assessed car landed on `Maybe / verify` (33) or `Reject` (22), 0
`Pursue`, because almost every car has model-critical evidence not yet in the
listing (cooling records, a rear-structure inspection...) and any conditional
gate capped the verdict at `Maybe`. That answers "is it proven?", not "is it
worth proving next?" This adds a stage model (`scout/stage.py`): Listing ->
Questions sent -> Docs in -> PPI done, derived from the listing's status and
attached documents.

1. **Open question vs. observed.** A conditional gate is now either an *open
   question* — evidence not gathered yet (`critical_missing:*`, major service
   claimed but undocumented, accident without repair docs, a stale listing) —
   or an *observed* negative (everything else conditional: strong reservations
   on a critical item, salvage title, warning lights, undocumented mods, a
   remote auction with no PPI). Observed conditionals still cap the verdict at
   `Maybe / verify` exactly as before. Open questions no longer do, by
   themselves. `scout.evidence.classify()` (already used for the evidence-gaps
   report) decides whether an open critical item is document- or
   inspection-resolvable; `COND_GAIN_PER_ITEM = 4` sits next to the existing
   `GAIN_PER_ITEM = 5` in `scout/evidence.py` as the single source for both.
2. **Stage relevance.** An open question that should have been settled by now
   stops being "open" and becomes observed: a document-resolvable item still
   open at the Docs stage, or any item still open at the PPI stage, counts
   against the car instead of for it.
3. **Upside** (`Assessment.upside`) is the score the car could reach if its
   still-open questions resolve favourably: `score.total + doc_gain +
   cond_gain`, capped at 100, where `doc_gain`/`cond_gain` are the documentation
   / condition headroom, capped by 5 (4) points per still-open document
   (inspection) item, plus a one-time +5 if major service or an accident is
   claimed without documentation, and in total by 12 documentation and 8
   condition points (`DOC_GAIN_CAP` / `COND_GAIN_CAP`): records and a clean PPI
   rarely move a car further than that, and without the cap a listing that
   discloses less would out-rank one that discloses more.
4. **New verdict path.** With open questions only (no observed conditional),
   `score.total >= 45` and `upside >= 75`, at the Listing or Questions stage:
   `Pursue conditionally`, "worth pursuing if the open questions check out."
   The confidence-below-50 cap does not apply on this path — low confidence is
   expected before the seller has answered anything. Otherwise (low score, low
   upside, or Docs/PPI stage) it still caps at `Maybe / verify` as before.
5. **Priority** (`Assessment.priority`, 0-100) ranks which car is worth
   pursuing next: `round(0.6 * score.total + 0.4 * upside)` (proven evidence outweighs hoped-for evidence), minus 10 per
   observed conditional, minus 8 for an unpriced or early-bid auction, minus 5
   for a stale listing; zero for any hard/strategy/configuration gate.
6. **Next steps** (`Assessment.next_steps`, up to 3): ask for the VIN if
   missing; request records for open document items at the Listing stage; the
   seller's own first question; book a PPI for open inspection items once
   documents are in.
7. `stage`, `upside` and `priority` are also in the published index
   (`publish.INDEX_ASSESSMENT_FIELDS`); `open_questions`/`next_steps` stay
   detail-only.

## 1.3.0 (2026-09-22)

From the 2026-09-22 audit (data/audits/Hoopty_Scout_Audit_v1_20260922.md, #6-#9, #13, #17).

1. **Market fair value** (`scout/market.py`). Comps are real sales only; ended
   auctions that did not sell are a floor, never a price. Segmented by body class
   (open/closed), transmission and ±3 model years (relaxed in steps when thin,
   and said so), mileage-adjusted about −4% per 10k, recent 3 years preferred.
   Asking prices ×0.93 only when there are fewer than two sales. It feeds the
   preliminary price/value points, the expected hammer for auctions, the model's
   prompt, and the offer range.
2. **Offers anchor on fair value**, less the midpoint of known work, never above the
   ask or the maximum price; the opening offer is at least 5% below the top.
3. **An early auction with no hammer estimate is "unpriced"**. The budget does not
   credit the current bid; mission fit is capped at 9 until it closes.
4. **The mission-fit budget cap uses the expected price**, and compares the all-in
   midpoint (not the price) with the defeats-purpose all-in line.
5. **Confidence recalibrated.** Base 30 + 6 × evidence quality. Unknown facts count
   only for title, accidents, records, mileage, VIN and owners (−3 each, cap 12);
   critical items missing −4 each (cap 12). The guide-era formula had a median of
   15 and never reached the 50 line.
6. **Exclusions come only from the editable state.** The hardcoded list is gone. A
   one-time migration added BMW Z4, Saturn and Mazda MX-5 to stored overrides.

## 1.2.1 (2026-09-05)

**All-in cost counts only what is known.** All-in = price (or expected hammer)
+ buyer fee + transport + California tax/registration + work the listing
itself establishes as needed (stated fault, visible defect, disclosed warning
light, old tires, open recall). The generic first-30-day catch-up estimate,
the age/mileage overdue allowance and the model risk reserve are still
computed and shown, labelled "not counted", with an "if all of that lands"
total, but they no longer drive the maximum price or the cost gate. Reason:
with a $27k price ceiling the guide-era arithmetic solved every car needing
work to a hammer far below its asking, which is not a usable negotiating
number. Jason's instruction, 2026-09-05.

## 1.2.0 (2026-09-05)

Measured on 32 active candidates and 15 Opus/Sonnet assessments: totals ran
23-61, every assessed car was `Reject`, and emotional/spec fit varied by less
than one point across the board. The rubric was measuring "how much is proven
yet" rather than "which car is worth proving next".

1. **Weights 25/25/15/15/10/10** (guide: 30/25/15/15/10/5). Five points move from
   documentation to emotional/specification fit. Documentation stays the
   heaviest category alongside condition.
2. **Documentation is scored on the listing as presented.** The preliminary
   score no longer pre-caps documentation at 15 merely because the profile has
   critical-evidence items. In the assessment, a missing conditional item caps
   documentation at 20 (guide-era code: 15) and still caps the verdict at
   `Maybe / verify`; a failed or hard-missing item still gives `Reject`.
3. **Price/value is market-relative only.** Budget fit is scored once, in
   mission fit: over the max price costs 3 points, over the defeats-purpose
   line costs 6, none for `future_keeper`. (Guide-era code double-counted it.)
4. **Verdict bands: Pursue 85, Pursue conditionally 75, Maybe / verify 45-74,
   Reject below 45** (guide: Maybe from 60). The honest listing-stage ceiling
   is about 65 because condition and documentation evidence mostly arrives
   after seller questions and a PPI. Hard gates still override.
5. **Anchored rubrics** for the sync-time read: spec 5 = typical example, 8+ only
   for a named rare colour / package / body style / hardtop, 3 or below for
   base spec, poor colours or cheap modifications; condition 8+ only with
   photographic or receipt evidence.

Stored assessments keep the policy version that produced them; the score is
recomputed deterministically from the stored ratings when the viewer or the
`/api/rescore` endpoint asks for it.
