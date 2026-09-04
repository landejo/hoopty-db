"""Durable preferences from the guide (§4, §5, §8, §9, §15). These change
rarely; anything that changes with circumstances lives in state.py."""
from __future__ import annotations

MISSIONS = ["enthusiast_bridge", "pragmatic_bridge", "future_keeper", "utility_capability"]
URGENCY_MODES = ["accelerated_bridge", "emergency", "casual_search"]

VERDICTS = ["Pursue", "Pursue conditionally", "Maybe / verify", "Reject", "Do not pursue"]
VERDICT_RANK = {v: i for i, v in enumerate(VERDICTS)}  # higher index = worse

# §9 scoring model (points per category, total 100).
CATEGORY_POINTS: dict[str, int] = {
    "documentation": 30,
    "condition": 25,
    "price_value": 15,
    "mission_fit": 15,
    "logistics": 10,
    "emotional_spec_fit": 5,
}
CATEGORY_LABELS: dict[str, str] = {
    "documentation": "Documentation & verifiability",
    "condition": "Mechanical / structural / cosmetic condition",
    "price_value": "Price & risk-adjusted value",
    "mission_fit": "Mission fit",
    "logistics": "Logistics & inspectability",
    "emotional_spec_fit": "Emotional / specification fit",
}
assert sum(CATEGORY_POINTS.values()) == 100

# §9 interpretation bands (score -> verdict before caps).
SCORE_BANDS = [(85, "Pursue"), (75, "Pursue conditionally"), (60, "Maybe / verify"), (0, "Reject")]

# §9 confidence: below this, verdict should rarely exceed Maybe / verify.
CONFIDENCE_PROVISIONAL = 50

# §5 explicit exclusions: substrings matched against "make model" (lowercase).
EXCLUDED_MODELS = ["sc430", "sc 430", "miata", "mx-5", "cr-z", "is350", "is 350", "gs350", "gs 350"]

# §8 manual-transmission gate applies to these missions; SUVs / utility never.
MANUAL_REQUIRED_MISSIONS = {"enthusiast_bridge", "future_keeper"}

# §7 evidence sources, in rough order of strength. Facts carry one of these.
EVIDENCE_SOURCES = [
    "receipt", "history_report", "photo", "external_vin", "listing_text",
    "seller_comment", "seller_claim", "ai_inference",
]
FACT_STATUSES = ["verified", "claimed", "inferred", "unknown"]

# Logistics cap by locality band (scoring.locality_hint: 5 = Monterey/Bay ... 1 = Northeast).
LOGISTICS_CAP_BY_BAND: dict[int | None, int] = {5: 10, 4: 9, 3: 7, 2: 5, 1: 4, None: 6}

# Relisted with a markup above this fraction of the last known sale, with no
# documented transformation, caps price_value.
RELIST_MARKUP_FLAG = 0.25
RELIST_PRICE_VALUE_CAP = 8

# Documentation caps when model-critical evidence is missing (§9 score caps).
DOC_CAP_CONDITIONAL_MISSING = 15
DOC_CAP_HARD_MISSING = 10

# Compact context block (§18) handed to the model verbatim.
COMPACT_CONTEXT = (
    "Jason lives in Carmel, California. His household recently purchased a 2018 Lexus RX 350 that "
    "is working well and covers family and utility needs. His 2011 BMW 335i has recurring coolant "
    "trouble after a radiator replacement and also needs front-suspension work. This puts him in "
    "accelerated_bridge mode: he wants to replace the BMW relatively soon, but the RX means he is not "
    "in emergency mode and should not waive inspection or price discipline. The 335i's unreliability "
    "makes a lower-cost, dependable, immediately available bridge car more attractive than it would "
    "have been while the BMW was healthy, even if that car is less special. Manual is strongly "
    "preferred and usually required for fun cars, but a saved automatic may be evaluated as a "
    "pragmatic_bridge if it wins decisively on reliability, condition, convenience, price, and resale; "
    "do not pretend it fulfills the enthusiast brief. Primary enthusiast-bridge candidates are a "
    "2017-2019 F56 MINI Cooper S manual, Mk7/Mk7.5 GTI manual, BMW 128i manual with Sport/M Sport, "
    "and 2001-2002 Z3 3.0 roadster manual. He also likes Z3/M Coupes, S52 M Roadsters, and Porsches, "
    "but those are usually future-keeper choices. He explicitly rejects the SC430, Miata, CR-Z, IS350, "
    "and GS350. Favor clean title, stock/mostly stock condition, strong records, quality matching "
    "tires, California/West Coast location, seller cooperation, PPI access, and low all-in risk. His "
    "mechanic works on VW and recommends Toyota/Lexus; he will not touch Land Rovers. Low mileage never "
    "substitutes for age-related service. Unknown is not good: missing evidence is unknown, never a "
    "positive. Seller symptom reports are not diagnostic tests. Clean Carfax means no reported event, "
    "not accident-free."
)
