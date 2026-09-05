"""Evidence interpretation for the assessment engine (deep model). The model
reads the listing and returns facts with provenance, contradictions, the status
of each model-critical evidence item, gate flags, category ratings, and the
qualitative lists. It never computes the score, cost, or verdict; the policy
engine does. Output is validated against EvidenceInterpretation before use."""
from __future__ import annotations

import json
from datetime import date
from typing import Any

from pydantic import ValidationError

from scout import coerce
from scout.ai import call_json_text
from scout.config import CONFIG, SITES
from scout.policy.preferences import CATEGORY_LABELS, CATEGORY_POINTS, COMPACT_CONTEXT
from scout.policy.schema import EvidenceInterpretation, Flags

SYSTEM = """You are a veteran independent mechanic and buyer's advocate helping Jason
evaluate ONE saved used-car listing. You interpret evidence; deterministic code
applies the gates, arithmetic, score, costs, and verdict afterwards. Be literal,
asymmetric about risk, and never turn missing evidence into a positive.

BUYER CONTEXT:
{context}

CURRENT STATE (editable; authoritative over anything older):
{state}

MISSION FOR THIS LISTING: {mission}
{mission_guidance}

BUYER PROFILE FOR THIS MODEL:
{profile}

MODEL-CRITICAL EVIDENCE to report on (use these exact keys; status is one of
satisfied / claimed_only / missing / failed; "satisfied" needs a receipt, photo,
report, or specialist inspection, never a seller sentence):
{critical}

EVIDENCE SOURCE vocabulary: receipt, history_report, photo, external_vin,
listing_text, seller_comment, seller_claim, ai_inference.
FACT STATUS vocabulary: verified (established by strong evidence), claimed
(seller assertion), inferred (your reasoning, label it so), unknown.

Return ONE JSON object with exactly these keys:
- facts: array of {{key, value, status, source, note}} for the important facts:
  vin, year, make, model, trim, engine, transmission, mileage, exterior_color,
  interior_color, title_status, owners, ownership_duration, accident_history,
  modifications, records_available, warning_lights, leaks_cooling, tires,
  suspension, structure, smog_status, seller_cooperation, ppi_access,
  auction_reserve, auction_close_pacific. Include an entry with status
  "unknown" for anything the listing does not establish.
- contradictions: array of {{topic, detail, severity: minor|material|identity}}
  (year/engine/trim mismatch, mileage inconsistencies, title, ownership, dates).
- critical_evidence: array of {{key, status, evidence, source}} for EVERY key
  listed above.
- flags: object with these keys, each "yes" / "no" / "unknown":
  {flag_keys}
  Use "yes" only on evidence; "unknown" when the listing is silent.
- ratings: object with {category_keys}; each {{rating: 0-10, rationale}}.
  Meanings (points in parentheses are applied by code, not you):
{category_help}
  Rate documentation on what is VERIFIABLE, not on how much the seller wrote.
  Rate condition on evidence; unknown areas pull the rating down.
  Rate price_value against the comps/peers given and the buyer's budget.
  Rate mission_fit for the stated mission and urgency mode.
  Rate logistics for distance from Carmel, CA, PPI access, transport, smog.
  Rate emotional_spec_fit for color, spec, body style, character.
- evidence_quality: 0-10, how much of the KEY evidence is verifiable from
  receipts, photos, reports, or inspection (not seller prose).
- immediate_service_estimate: {{low, high}} USD for known/likely first-30-day work.
- expected_hammer: {{low, high}} USD for an AUCTION only, or null.
- positives: 3-6 strings, most important first.
- concerns: 3-6 strings, most important first (model weak points the listing
  is silent on count as concerns).
- unknowns: strings, the missing evidence that matters most.
- seller_questions: 6-12 falsifiable questions that do NOT ask for facts the
  listing already states; prefer receipts, dates, mileages, photos of the
  specific area, scan data, cold-start video.
- ppi_focus: 4-10 items specific to THIS car.
- what_would_change_verdict: 2-5 strings, how it could move up (or down).
- mission_note: 1-2 sentences on why it fits or conflicts with the mission and
  urgency mode. If it ranks well only as a pragmatic bridge, say so.
- rationale: 4-8 sentences leading with the result. Cite evidence.
- next_action: ONE concrete next action.

TODAY IS {today}. No prose outside the JSON."""

MISSION_GUIDANCE = {
    "enthusiast_bridge": "Bridge car that must still have a point of view: manual required, ideally $10-13k and under $15k all-in-aware, reliable, inspectable, easy to resell in 6-24 months.",
    "pragmatic_bridge": "Low-cost, reliable, immediately available bridge that solves the 335i problem. An automatic is not disqualifying here, but it must win decisively on reliability, condition, price, convenience, and resale, and you must say it does not fulfill the enthusiast brief.",
    "future_keeper": "A selective longer-term enthusiast purchase. Higher price can be justified only by genuine superiority and documentation; say plainly if it is attractive only as a keeper and conflicts with the current cash-preservation strategy.",
    "utility_capability": "Capability-oriented SUV branch. Automatic is fine. Must justify itself by capability or character the RX 350 does not already supply.",
}


def _profile_text(profile: dict[str, Any]) -> str:
    keys = ("label", "framing", "weak_points", "immediate_repairs", "repairs_12mo", "market_notes", "catchup_notes")
    return "\n".join(f"{k}: {profile.get(k)}" for k in keys if profile.get(k))


def _fmt_row(r: dict[str, Any]) -> str:
    bits = [f"{r.get('year') or '?'} {r.get('make') or ''} {r.get('model') or ''}".strip(), r.get("trim") or "",
            f"{r.get('mileage'):,} mi" if r.get("mileage") else "? mi",
            f"${(r.get('sold_price') or r.get('price')):,}" if (r.get("sold_price") or r.get("price")) else "$?",
            r.get("price_kind") or "", r.get("availability") or "", r.get("location") or "",
            SITES.get(r.get("site", ""), r.get("site", "")), (r.get("listing_date") or "")[:10]]
    return " · ".join(b for b in bits if b)


def interpret_listing(listing: dict[str, Any], profile: dict[str, Any], mission: str, state: dict[str, Any],
                      vin_history: dict[str, Any], snapshots: list[dict[str, Any]],
                      peers: list[dict[str, Any]], comps: list[dict[str, Any]]) -> EvidenceInterpretation:
    critical = "\n".join(f"  - {c['key']}: {c.get('label', c['key'])} [{c.get('severity', 'conditional')}]"
                         for c in profile.get("critical_evidence") or []) or "  (none defined for this model)"
    state_view = {k: state.get(k) for k in ("urgency_mode", "budget", "current_vehicles", "active_exclusions", "deprioritized", "home_location")}
    system = SYSTEM.format(
        context=COMPACT_CONTEXT, state=json.dumps(state_view, indent=1), mission=mission,
        mission_guidance=MISSION_GUIDANCE.get(mission, ""), profile=_profile_text(profile), critical=critical,
        flag_keys=", ".join(Flags.model_fields), category_keys=", ".join(CATEGORY_POINTS),
        category_help="\n".join(f"    * {k} ({v} pts): {CATEGORY_LABELS[k]}" for k, v in CATEGORY_POINTS.items()),
        today=date.today().isoformat(),
    )
    facts = {k: listing.get(k) for k in (
        "site", "url", "title", "year", "make", "model", "generation", "trim", "engine", "engine_liters",
        "transmission", "mileage", "price", "price_kind", "sold_price", "location", "vin", "seller_type",
        "seller_name", "title_status", "accidents", "num_owners", "listing_date", "auction_end", "options",
    ) if listing.get(k) not in (None, "", [])}
    raw = listing.get("raw") or {}
    for k in ("bid_label", "bid_text", "time_left", "bid_count", "auction_end_text", "essentials", "listed_text"):
        if raw.get(k) not in (None, ""):
            facts[k] = raw[k]
    hist = "\n".join(f"  {s['seen_at'][:10]}: {'$' + format(s['price'], ',') if s.get('price') else '-'} "
                     f"{s.get('price_kind') or ''} {s.get('availability') or ''}" for s in snapshots) or "  (first sighting)"
    user = (
        f"STRUCTURED FACTS (from the scraper + normalizer; verify against the text):\n{json.dumps(facts, indent=1)}\n\n"
        f"PHOTO COUNT: {len(listing.get('photos') or [])} (you cannot see them; do not infer condition from them)\n\n"
        f"THIS LISTING'S PRICE / AVAILABILITY HISTORY:\n{hist}\n\n"
        f"VIN HISTORY IN THE TRACKER (same VIN, other listings):\n{json.dumps(vin_history, indent=1)[:6000]}\n\n"
        f"ACTIVE PEERS (same profile):\n" + ("\n".join(f"  - {_fmt_row(p)}" for p in peers[:20]) or "  (none)") + "\n\n"
        f"SOLD / ENDED COMPS:\n" + ("\n".join(f"  - {_fmt_row(c)}" for c in comps[:30]) or "  (none)") + "\n\n"
        f"FULL LISTING TEXT:\n{(listing.get('raw_text') or '')[:60_000]}"
    )
    text = call_json_text(CONFIG.model_deep, system, user, max_tokens=32000, log_name="last_assess", effort="high")
    data = coerce.parse_json(text)
    try:
        return EvidenceInterpretation.model_validate(data)
    except ValidationError as e:
        raise RuntimeError(f"model output failed schema validation: {e.errors()[:3]}")
