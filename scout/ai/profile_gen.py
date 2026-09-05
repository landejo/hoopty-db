"""Generate a buyer profile for a make/model/generation we have no seed for.
Deep model, once per new model. Result is marked unverified until reviewed."""
from __future__ import annotations

from typing import Any

from scout import coerce
from scout.ai import call_json_text
from scout.config import AXES, CONFIG

SYSTEM = """You are a veteran independent mechanic and used-car buyer's advocate.
Build a BUYER PROFILE for a specific make/model/generation so that later
analyses of individual listings can be model-aware. The buyer lives in
Carmel, CA, prefers documented, unmodified cars, and is evaluating this as a
real-world driver rather than an investment.

Return ONE JSON object:
- key: short snake_case id, e.g. "porsche_911_997", "toyota_4runner_5g"
- label: human label, e.g. "Porsche 911 (997.1 & 997.2)"
- make, models: array of model strings a listing might use (variants, aliases)
- years: [first_year, last_year] for the generation
- framing: 2-4 sentences on what this car is for this buyer and what matters
- weak_points: the specific, well-known failure points for THIS generation,
  with mileage/age context and what documentation to look for. Be concrete
  (part names, symptoms, rough repair cost bands). 6-14 items in prose.
- immediate_repairs: likely first-30-day items for a typical example
- repairs_12mo: likely additional 12-month items
- market_notes: what moves price for this model (trims, colors, records,
  transmission, mileage bands), 2-4 sentences
- weights: object over these axes (only the ones that matter for this car;
  numbers sum roughly to 1):
{axes}
- checks: 8-16 objects {{key: snake_case, label: short inspection item}}
  covering the weak points (these become a PPI checklist)
- dealbreaker_rules: 2-6 short rules (e.g. "Salvage title", "Automatic")
- critical_evidence: 1-5 objects {{key: snake_case, label, severity: hard|conditional}}
  for the model-specific evidence a buyer must see before purchase. "hard"
  ONLY for catastrophic engine/structure risks where no evidence means no
  purchase (e.g. a Porsche M97 borescope); otherwise "conditional".
- mission_default: one of enthusiast_bridge, pragmatic_bridge, future_keeper,
  utility_capability (SUVs/trucks -> utility_capability; expensive collector
  specs -> future_keeper; manual compact fun cars -> enthusiast_bridge)
- risk_reserve: USD reserve for this model's low-frequency high-cost failures
- automatic_ok: true only for vehicle classes where an automatic is normal (SUVs)
- catchup_notes: 1-3 sentences on age-related catch-up work for a typical example

Be specific to the generation. No prose outside the JSON."""


def generate_profile(make: str, model: str, generation: str | None, year: int | None,
                     sample_text: str = "") -> dict[str, Any] | None:
    axes = "\n".join(f"  * {k}: {v}" for k, v in AXES.items())
    system = SYSTEM.format(axes=axes)
    user = (
        f"MAKE: {make}\nMODEL: {model}\nGENERATION: {generation or 'unknown'}\n"
        f"EXAMPLE YEAR: {year or 'unknown'}\n\n"
        f"EXAMPLE LISTING (for context only):\n{sample_text[:6000]}"
    )
    text = call_json_text(CONFIG.model_deep, system, user, max_tokens=24000,
                          log_name="last_profile_gen", effort="high")
    return coerce.profile(coerce.parse_json(text))
