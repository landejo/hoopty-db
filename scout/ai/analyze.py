"""On-demand deep analysis (deep model). One listing, with its profile, its
price history, the active peers, and the sold comps as market context."""
from __future__ import annotations

import json
from datetime import date
from typing import Any

from scout import coerce
from scout.ai import call_text
from scout.config import AXES, CONFIG, SITES

SYSTEM = """You are a veteran independent mechanic and buyer's advocate advising a
private buyer in Carmel, CA who is shopping for a real-world driver, not an
investment. You are given ONE listing, the buyer profile for that model, the
listing's own price/availability history, the other ACTIVE listings of the
same profile the buyer is tracking (peers), and SOLD / ended comps.

BUYER PROFILE:
{profile}

Do all of the following, model-aware, in one pass. Cite evidence from the
listing text. Where the listing is silent on a known weak point, say so as a
concern rather than assuming the worst. Return ONE JSON object:

- verdict: one of Pursue, Verify, Maybe, Pass
- verdict_reasoning: 2-4 sentences, the decisive factors
- deal_score: 0-100 (100 = exceptional car at an exceptional price for THIS
  buyer). Combine condition evidence, documentation, spec desirability, price
  vs. the comps and peers, and the buyer's priorities.
- confidence: 1-5 in your read of this listing
- summary: 4-7 sentences. What this car is, how it sits against the market,
  what stands out, what to verify.
- market_position: 2-4 sentences comparing THIS asking price / mileage / spec
  to the sold comps and active peers provided (name them by year/mileage/price).
- scores: 1-5 integers on these axes (only the ones the profile weights):
{axes}
- positives: 3-8 concrete strings
- concerns: 3-8 concrete strings (include weak points the listing ignores)
- dealbreakers: 0-4 strings (only true dealbreakers visible in the listing)
- checks: array of {{key, status, notes}} using ONLY these check keys, and ONLY
  where the listing gives evidence (pass needs explicit evidence):
{checks}
- inspection_focus: 4-10 things the PPI should specifically cover for this car
- seller_questions: 6-12 falsifiable questions that do NOT ask for facts the
  listing already states; prefer receipts, dates, mileages, and gaps
- pricing: {{fair_value, target_offer, walk_away, immediate_repairs,
  twelve_month_repairs}} in whole USD, grounded in the comps
- negotiation: 3-6 short plays, each tied to a specific finding

TODAY IS {today}. No prose outside the JSON."""


def _fmt_row(r: dict[str, Any]) -> str:
    bits = [
        f"{r.get('year') or '?'} {r.get('make') or ''} {r.get('model') or ''}".strip(),
        f"{r.get('trim') or ''}".strip(),
        f"{r.get('mileage'):,} mi" if r.get("mileage") else "? mi",
        f"${(r.get('sold_price') or r.get('price')):,}" if (r.get("sold_price") or r.get("price")) else "$?",
        r.get("price_kind") or "",
        r.get("availability") or "",
        r.get("location") or "",
        SITES.get(r.get("site", ""), r.get("site", "")),
        (r.get("listing_date") or "")[:10],
    ]
    return " · ".join(b for b in bits if b)


def analyze_listing(listing: dict[str, Any], profile: dict[str, Any],
                    snapshots: list[dict[str, Any]], peers: list[dict[str, Any]],
                    comps: list[dict[str, Any]]) -> dict[str, Any]:
    prof_text = "\n".join(
        f"{k}: {profile.get(k)}" for k in
        ("label", "framing", "weak_points", "immediate_repairs", "repairs_12mo", "market_notes")
        if profile.get(k)
    )
    prof_text += "\nweights: " + json.dumps(profile.get("weights", {}))
    prof_text += "\ndealbreaker_rules: " + json.dumps(profile.get("dealbreakers", []))
    weighted = [a for a in AXES if a in (profile.get("weights") or {})] or list(AXES)
    axes = "\n".join(f"  * {a}: {AXES[a]}" for a in weighted)
    checks = "\n".join(f"  - {c['key']}: {c['label']}" for c in profile.get("checks", [])) or "  (none)"
    system = SYSTEM.format(profile=prof_text, axes=axes, checks=checks, today=date.today().isoformat())

    facts = {k: listing.get(k) for k in (
        "site", "url", "title", "year", "make", "model", "generation", "trim", "engine",
        "transmission", "mileage", "price", "price_kind", "location", "vin", "seller_type",
        "seller_name", "title_status", "accidents", "num_owners", "listing_date", "options",
    ) if listing.get(k) not in (None, "", [])}
    hist = "\n".join(
        f"  {s['seen_at'][:10]}: {'$' + format(s['price'], ',') if s.get('price') else '-'} "
        f"{s.get('price_kind') or ''} {s.get('availability') or ''}" for s in snapshots
    ) or "  (first sighting)"
    peers_txt = "\n".join(f"  - {_fmt_row(p)}" for p in peers[:25]) or "  (none)"
    comps_txt = "\n".join(f"  - {_fmt_row(c)}" for c in comps[:40]) or "  (none)"
    user = (
        f"STRUCTURED FACTS:\n{json.dumps(facts, indent=1)}\n\n"
        f"PRICE / AVAILABILITY HISTORY:\n{hist}\n\n"
        f"ACTIVE PEERS (same profile, being tracked):\n{peers_txt}\n\n"
        f"SOLD / ENDED COMPS:\n{comps_txt}\n\n"
        f"FULL LISTING TEXT:\n{(listing.get('raw_text') or '')[:60_000]}"
    )
    text = call_text(CONFIG.model_deep, system, user, max_tokens=12000,
                     log_name="last_analyze", effort="high")
    valid = {c["key"] for c in profile.get("checks", [])}
    return coerce.analysis(coerce.parse_json(text), valid)
