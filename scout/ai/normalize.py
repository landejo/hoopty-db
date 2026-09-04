"""Sync-time normalization (fast model). Runs on every new/changed listing,
candidates AND comps. Cheap: structured facts, profile pick, quick read."""
from __future__ import annotations

from datetime import date
from typing import Any

from scout import coerce
from scout.ai import call_text
from scout.config import AXES, CONFIG, SITES

SYSTEM = """You normalize used-vehicle listings for a private buyer in Carmel, CA.
You receive the raw text of one listing (plus any structured hints the scraper
found). Return ONE JSON object and nothing else.

KNOWN VEHICLE PROFILES (pick the best profile_key; use "new" if none fits the
make/model/generation, and "skip" if the listing is not a vehicle):
{registry}

Fields (omit anything you cannot determine; never guess a VIN):
- year (int), make, model (short, e.g. "Z3 M coupe", "GX470", "911 Carrera"),
  generation (chassis/gen code if known, e.g. "E36/8", "J120", "997.2"),
  trim, engine (short label, e.g. "S54", "4.7L V8"), engine_liters (number),
  transmission (Manual / Automatic / Unknown), drivetrain, body_style,
  exterior_color, interior_color
- mileage (int miles), price (int USD; for an auction use the current bid or
  the final sale price), price_kind: one of asking, current_bid, sold,
  reserve_not_met, no_reserve; sold_price (int, only when actually sold)
- availability: active | sold | ended | removed. "sold" when the text says
  Sold / Sold for $X. "ended" for an auction that closed without a sale
  (reserve not met, bid to $X). Otherwise "active".
- location "City, ST", vin (17 chars only), seller_type (Private / Dealer /
  Auction / Unknown), seller_name, title_status, accidents (yes/no/unknown),
  num_owners (int)
- listing_date: ISO date. TODAY IS {today}. Convert "Listed 3 weeks ago" to
  today minus 21 days. For auctions use the auction END date if shown.
- price_drops: array of {{prior_price (int), amount (int), note}} for price
  reductions the SITE or SELLER states (e.g. "Price drop -$5,000",
  "$300 price drop", "was $19,995 now $18,995", "reduced from"). Derive
  prior_price = current price + amount when only the amount is given. Ignore
  currency conversions such as "CA$24,400". Empty array if none.
- days_listed: integer days on market if the site states it (omit otherwise)
- options: array of factory options / packages / notable equipment
- highlights: 2-6 short concrete positives (documented work, rare spec, records)
- red_flags: 0-6 short concrete concerns (salvage, rust, mods, gaps, vague
  seller, mileage/price mismatch, missing key info for this model)
- summary: 2-3 plain sentences, buyer-oriented
- scores: quick 1-5 integers on any of these axes you can reason about:
{axes}
- profile_key (from the list above, or "new"/"skip"), profile_confidence 1-5

Be literal. Do not invent facts the text does not support."""


def _registry_text(profiles: list[dict[str, Any]]) -> str:
    from scout.profiles import registry_summary
    return registry_summary(profiles) or "- (none yet)"


def normalize_listing(raw_text: str, hints: dict[str, Any], site: str,
                      profiles: list[dict[str, Any]]) -> dict[str, Any]:
    axes = "\n".join(f"  * {k}: {v}" for k, v in AXES.items())
    system = SYSTEM.format(registry=_registry_text(profiles), today=date.today().isoformat(), axes=axes)
    hint_lines = [f"SITE: {SITES.get(site, site)}"]
    for k, v in hints.items():
        if v not in (None, "", [], {}):
            hint_lines.append(f"{k.upper()}: {v}")
    user = "\n".join(hint_lines) + "\n\nLISTING TEXT:\n" + (raw_text or "")[:40_000]
    text = call_text(CONFIG.model_fast, system, user, max_tokens=4000, log_name="last_normalize")
    return coerce.normalized_listing(coerce.parse_json(text))
