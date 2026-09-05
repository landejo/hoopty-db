# Policy changes relative to the guide

The guide (`Jason_Car_Assessment_Guide.md`, v1.1) is the source. Where the code
deliberately departs from its text, the change is recorded here with the reason,
so the guide can be updated when Jason next revises it.

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
