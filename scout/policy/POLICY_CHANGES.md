# Policy changes relative to the guide

The guide (`Jason_Car_Assessment_Guide.md`, v1.1) is the source. Where the code
deliberately departs from its text, the change is recorded here with the reason,
so the guide can be updated when Jason next revises it.

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
