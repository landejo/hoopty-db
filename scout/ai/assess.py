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
from scout.ai.photos import photo_blocks
from scout.config import CONFIG, SITES
from scout.policy.preferences import CATEGORY_LABELS, CATEGORY_POINTS, COMPACT_CONTEXT
from scout.policy.schema import EvidenceInterpretation, Flags

# STATIC / DYNAMIC split for prompt caching: the STATIC block (role, framing
# rules, vocabularies, output schema, rubric anchors) is identical for every
# listing and goes first so its cache prefix is reused; the DYNAMIC block
# (buyer context/state, mission guidance, profile text, the critical-evidence
# list for THIS model, today's date) changes per call and goes second. Only
# the first (static) system block gets cache_control (see scout/ai/__init__.py).
# flag_keys/category_keys/category_help never vary across calls either, so
# they're baked into STATIC_SYSTEM once at import time, not re-rendered per call.
_STATIC_TEMPLATE = """You are a veteran independent mechanic and buyer's advocate helping Jason
evaluate ONE saved used-car listing. You interpret evidence; deterministic code
applies the gates, arithmetic, score, costs, and verdict afterwards. Be literal,
asymmetric about risk, and never turn missing evidence into a positive.

MODEL-CRITICAL EVIDENCE: the dynamic block below lists the exact keys to
report on for this model. status is one of satisfied / claimed_only / missing
/ failed / not_applicable; "satisfied" needs a receipt, photo, report, or
specialist inspection, never a seller sentence. Use "not_applicable" when the
item cannot apply to THIS car — e.g. an S54 rod-bearing record on a non-M or
pre-2001 car, a convertible-top item on a coupe — and say why in `evidence`;
do not report such an item as "missing".

ATTACHED DOCUMENTS: when the user block contains an "ATTACHED DOCUMENTS"
section, that is GOLD-TIER evidence — a history report, invoices, service
records or an inspection obtained outside the advertisement. Weight it far
above anything the seller wrote. Specifically:
- Verify or CONTRADICT the listing's claims against it (dates, mileages,
  owner count, accident history, title). Report any conflict as a
  contradiction with the right severity.
- Identify RECURRING repairs (the same job more than once), GAPS (long
  stretches or big mileage with no entries), and RECENT RED FLAGS (a cluster
  of visits, diagnostics or "won't start" entries shortly before the sale).
- Note dealer-only history versus a mixed independent/private pattern, and
  surface completed recall or campaign work as positives.
- A model-critical item is "satisfied" when the DOCUMENT shows it (a dated
  line for the timing belt, for example), not when the seller merely says so.
- Facts drawn from a document take source "history_report" for a
  Carfax/AutoCheck and "receipt" for an invoice or service record.
- Absence of an entry in a history report is NOT proof the work was not done:
  say "no reported entry", never "was not serviced".

EVIDENCE SOURCE vocabulary: receipt, history_report, photo, external_vin,
listing_text, seller_comment, seller_claim, ai_inference.
FACT STATUS vocabulary: verified (established by strong evidence), claimed
(seller assertion), inferred (your reasoning, label it so), unknown.

Return ONE JSON object with exactly these keys:
- facts: array of {key, value, status, source, note} for the important facts:
  vin, year, make, model, trim, engine, transmission, mileage, exterior_color,
  interior_color, title_status, owners, ownership_duration, accident_history,
  modifications, records_available, warning_lights, leaks_cooling, tires,
  suspension, structure, smog_status, seller_cooperation, ppi_access,
  auction_reserve, auction_close_pacific. Include an entry with status
  "unknown" for anything the listing does not establish.
- contradictions: array of {topic, detail, severity: minor|material|identity}
  (year/engine/trim mismatch, mileage inconsistencies, title, ownership, dates).
- critical_evidence: array of {key, status, evidence, source} for EVERY key
  listed under MODEL-CRITICAL EVIDENCE in the dynamic block below.
- flags: object with these keys, each "yes" / "no" / "unknown":
  __FLAG_KEYS__
  Use "yes" only on evidence; "unknown" when the listing is silent.
- ratings: object with __CATEGORY_KEYS__; each {rating: 0-10, rationale}.
  Points in parentheses are applied by code, not you. Unknown evidence pulls a
  rating toward the middle-low, never up; an 8+ requires SPECIFIC VERIFIABLE
  evidence, not seller prose:
__CATEGORY_HELP__
  * documentation: 0-2 bare claims, no VIN, no records; 4-5 partial — either
    identity/terms (VIN, itemised price, title/accident data) OR maintenance
    evidence (receipts, photos of at-risk areas), not both; 6-7 both present
    but incomplete; 8-10 VIN + full itemised terms + receipts/reports/photos
    of the specific at-risk areas. A dealer listing with a VIN and a full
    equipment list is NOT "nothing verifiable" — say which half is missing.
  * condition: 0-2 a stated fault or visible defect/neglect with no repair
    evidence; 5 nothing wrong stated, nothing proven either way (the default
    when condition is simply unknown); 6-7 partial evidence of good
    condition; 8-10 ONLY with photographic or receipt evidence of specific
    good condition (fresh tires by date, dry underside, recent major service
    with invoices, clean PPI). "Runs great" is not evidence.
  * price_value: relative to the FAIR VALUE ESTIMATE when given, else the
    comps/peers: at fair value with no known issues = 5; 10%+ below fair
    value with no known issues = 7-8; priced above fair value = 3 or below.
    Known-work items pull this down further even at a good price.
  * mission_fit: 0-2 conflicts with the mission or urgency mode (wrong
    transmission where required, does not solve the bridge problem); 5 a
    plausible, unremarkable fit; 8-10 decisively fits (available now,
    reliable, right transmission, within the budget band).
  * logistics: relative to distance from Carmel, CA and whether a PPI can be
    arranged before money moves — NOT a risk judgement. 0-2 far away with no
    stated inspection access; 5 moderate distance, access unclear; 8-10
    close, or PPI/inspection readily arranged, straightforward transport, CA
    registration/smog feasible.
  * emotional_spec_fit: 5 = a typical example of the model; 7 = one genuinely
    desirable trait named (rare colour, notable manual, hardtop, sport
    package, documented originality); 9-10 = several such traits together;
    3 or below = base/unpopular trim, automatic where a manual exists, or
    cheap/incoherent modifications.

FRAMING RULES:
- Distance, transport, travel and dealer/doc fees are LOGISTICS and COST
  items. They belong in the logistics rating and the service estimate, never
  in concerns, red flags or risks. The buyer will fly out and drive a good car
  home.
- The car's age is the baseline, not a concern. Only cite age when tied to a
  specific unaddressed item (e.g. "no cooling-system work in 25 years").
- Contradictions require two SPECIFIC, INCOMPATIBLE claims made by the LISTING
  (or the listing versus the VIN decode): year vs engine, two different
  mileages, "clean title" vs a branded title. "Multiple owners" and "2 owners"
  agree. The STRUCTURED FACTS block is the tracker's own machine read and may
  simply be wrong: when it disagrees with the listing, correct it in `facts`
  (source listing_text) and do NOT report a contradiction. A missing VIN or an
  unproven claim is an unknown, never a contradiction.
- Concerns come in two kinds and must be labelled: "Observed: ..." for
  something actually wrong or stated in the listing or visible in a photo, and
  "Unverified: ... (ask for / inspect ...)" for model-critical evidence the
  listing does not provide. List Observed items first. An Unverified item is a
  question to ask, not a reason to reject, unless the profile marks it hard.
- DO NOT compute your own all-in, total-cost or ceiling figures in prose. The
  cost engine does that from your two estimates. Never assert that a car
  "approaches" or "exceeds" a budget ceiling; give the estimates and let the
  arithmetic speak. Quote budget figures only from the state block in the
  dynamic message.
- PHOTOS: some captured listing photos are attached. Describe only what you
  can actually see, and give photo-derived facts the source "photo". The
  attached set is what the tracker captured, NOT the listing's full gallery:
  never state how many photos the listing has, and never call something
  "unverifiable" merely because it is not in the attached photos; say
  "not examined here" and put it in unknowns.
- evidence_quality: 0-10, how much of the KEY evidence is verifiable from
  receipts, photos, reports, or inspection (not seller prose).
- immediate_service_estimate: {low, high} USD: LIKELY first-30-day catch-up for a
  typical example of this model at this age/mileage (fluids, tires by date,
  cooling plastics, bushings). Planning figure; not counted in the all-in.
- known_work_estimate: {low, high} USD: KNOWN REPAIRS, i.e. work this listing
  itself establishes as needed on this car: a stated fault, a visible defect
  in the photos, a disclosed warning light, tires with date codes older than
  about six years or described as old (they must be replaced), a documented
  open recall, a disclosed leak. null when nothing specific is established.
  This IS counted in the all-in and in the maximum price.
- known_work_items: short strings naming each item behind known_work_estimate
  (e.g. "four tires, 2018 date codes", "rear main seal leak disclosed").
- expected_hammer: {low, high} USD for an AUCTION only, or null.
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
- rationale: 4-8 sentences leading with the EVIDENCE FINDING — what this
  listing establishes, what is observed wrong, what remains open — never
  state or imply a verdict, recommendation, or buy/pass judgment ("a strong
  buy", "worth pursuing", "should be rejected", etc). The verdict is computed
  from your ratings after you answer, by code you cannot see; a rationale
  that prejudges it can end up sitting next to a contradicting computed
  verdict. Cite evidence.
- next_action: ONE concrete next action, phrased as a step (not a verdict).

No prose outside the JSON."""

_DYNAMIC_TEMPLATE = """BUYER CONTEXT:
{context}

CURRENT STATE (editable; authoritative over anything older):
{state}

MISSION FOR THIS LISTING: {mission}
{mission_guidance}

BUYER PROFILE FOR THIS MODEL:
{profile}

MODEL-CRITICAL EVIDENCE to report on (use these exact keys; see the static
instructions above for the status vocabulary):
{critical}

TODAY IS {today}."""

STATIC_SYSTEM = _STATIC_TEMPLATE.replace(
    "__FLAG_KEYS__", ", ".join(Flags.model_fields)
).replace(
    "__CATEGORY_KEYS__", ", ".join(CATEGORY_POINTS)
).replace(
    "__CATEGORY_HELP__", "\n".join(f"    * {k} ({v} pts): {CATEGORY_LABELS[k]}" for k, v in CATEGORY_POINTS.items())
)

# Backward-compatible alias: some callers/tests refer to the full static
# system prompt as SYSTEM (it's the block that carries FRAMING RULES etc).
SYSTEM = STATIC_SYSTEM

def _mission_guidance(mission: str, state: dict[str, Any]) -> str:
    """Budget figures come from the live policy state, never hardcoded."""
    from scout.policy.state import budget_for
    b = budget_for(state, mission)
    band = f"ideally ${b.get('ideal_low', 0):,}-${b.get('ideal_high', 0):,}, ceiling ${b.get('max_price', 0):,}, acceptable all-in ${b.get('acceptable_all_in', 0):,}"
    return MISSION_GUIDANCE.get(mission, "").replace("{band}", band)


MISSION_GUIDANCE = {
    "enthusiast_bridge": "Bridge car that must still have a point of view: manual required, {band}, reliable, inspectable, easy to resell in 6-24 months.",
    "pragmatic_bridge": "Low-cost, reliable, immediately available bridge that solves the 335i problem. An automatic is not disqualifying here, but it must win decisively on reliability, condition, price, convenience, and resale, and you must say it does not fulfill the enthusiast brief.",
    "future_keeper": "A selective longer-term enthusiast purchase. Higher price can be justified only by genuine superiority and documentation; say plainly if it is attractive only as a keeper and conflicts with the current cash-preservation strategy.",
    "utility_capability": "Capability-oriented SUV branch. Automatic is fine. Must justify itself by capability or character the RX 350 does not already supply.",
}


DOC_CHARS = 30_000


def _documents_block(listing_id: int | None) -> str:
    """History reports, invoices and service records attached to this listing."""
    if not listing_id:
        return ""
    from scout import db
    docs = db.list_documents(listing_id)
    if not docs:
        return ""
    parts = [f"\n\nATTACHED DOCUMENTS ({len(docs)}) — GOLD-TIER EVIDENCE, weight above seller prose:"]
    budget = DOC_CHARS
    for d in docs:
        text = (d.get("text") or "")[:budget]
        budget -= len(text)
        parts.append(f"\n--- {d['kind'].upper()}{' · ' + d['title'] if d.get('title') else ''}"
                     f"{' · captured ' + d['created_at'][:10] if d.get('created_at') else ''} ---\n{text}")
        if budget <= 0:
            parts.append("\n[further documents truncated]")
            break
    return "".join(parts)


def _capture_warning(listing: dict[str, Any]) -> str:
    """Tell the reader when OUR capture was incomplete, so a scraping failure is
    never scored as a seller who disclosed nothing."""
    cap = (listing.get("normalized") or {}).get("capture") or {}
    if (listing.get("raw") or {}).get("blocked"):
        return ("CAPTURE WARNING: the detail page was blocked by a bot wall; only the saved-list card was read. "
                "Score documentation and condition on what a reader COULD verify from this fragment, and put "
                "everything else in unknowns. Do not describe the seller as having disclosed nothing.\n")
    if cap and not cap.get("complete"):
        return (f"CAPTURE WARNING: our capture of this listing is incomplete ({cap.get('note')}; "
                f"{cap.get('text_chars')} chars of text, {cap.get('photos')} photos stored). The listing itself may "
                f"be far richer than what you see. Put missing areas in unknowns as 'not captured', do NOT treat "
                f"absence here as evidence the seller withheld it, and do not lower the documentation or condition "
                f"rating for what we failed to fetch.\n")
    return ""


def _profile_text(profile: dict[str, Any]) -> str:
    keys = ("label", "framing", "weak_points", "immediate_repairs", "repairs_12mo", "market_notes", "catchup_notes")
    return "\n".join(f"{k}: {profile.get(k)}" for k in keys if profile.get(k))


def _fmt_row(r: dict[str, Any]) -> str:
    kind_flag = " (high bid, NOT a sale)" if (r.get("availability") == "ended" and r.get("price_kind") in {"reserve_not_met", "current_bid"}) else ""
    bits = [f"{r.get('year') or '?'} {r.get('make') or ''} {r.get('model') or ''}".strip(), r.get("trim") or "",
            f"{r.get('mileage'):,} mi" if r.get("mileage") else "? mi",
            f"${(r.get('sold_price') or r.get('price')):,}" if (r.get("sold_price") or r.get("price")) else "$?",
            (r.get("price_kind") or "") + kind_flag, r.get("availability") or "", r.get("location") or "",
            SITES.get(r.get("site", ""), r.get("site", "")), (r.get("listing_date") or "")[:10]]
    return " · ".join(b for b in bits if b)


def interpret_listing(listing: dict[str, Any], profile: dict[str, Any], mission: str, state: dict[str, Any],
                      vin_history: dict[str, Any], snapshots: list[dict[str, Any]],
                      peers: list[dict[str, Any]], comps: list[dict[str, Any]], model: str | None = None,
                      fair: dict[str, Any] | None = None, effort: str = "high") -> EvidenceInterpretation:
    critical = "\n".join(f"  - {c['key']}: {c.get('label', c['key'])} [{c.get('severity', 'conditional')}]"
                         for c in profile.get("critical_evidence") or []) or "  (none defined for this model)"
    from scout.policy.state import budget_for
    state_view = {k: state.get(k) for k in ("urgency_mode", "current_vehicles", "active_exclusions", "deprioritized", "home_location", "travel", "capability_intent", "high_mileage_rule")}
    state_view["budget"] = budget_for(state, mission)   # this mission's budget (1.8.0)
    context = state.get("buyer_context") or COMPACT_CONTEXT
    dynamic = _DYNAMIC_TEMPLATE.format(
        context=context, state=json.dumps(state_view, indent=1), mission=mission,
        mission_guidance=_mission_guidance(mission, state), profile=_profile_text(profile), critical=critical,
        today=date.today().isoformat(),
    )
    system = [STATIC_SYSTEM, dynamic]
    listing_id = listing.get("id")
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
    photos = photo_blocks(listing.get("photos") or [])
    user_text = (
        f"STRUCTURED FACTS (the tracker's machine read: hints only, may be wrong; the listing text and VIN decode are authoritative):\n{json.dumps(facts, indent=1)}\n\n"
        f"PHOTOS ATTACHED: {len(photos)} of {len(listing.get('photos') or [])} captured (the listing may have more; do not count them)\n"
        f"{_capture_warning(listing)}\n"
        f"THIS LISTING'S PRICE / AVAILABILITY HISTORY:\n{hist}\n\n"
        f"VIN HISTORY IN THE TRACKER (same VIN, other listings):\n{json.dumps(vin_history, indent=1)[:6000]}\n\n"
        f"ACTIVE PEERS (same profile):\n" + ("\n".join(f"  - {_fmt_row(p)}" for p in peers[:20]) or "  (none)") + "\n\n"
        f"SOLD / ENDED COMPS:\n" + ("\n".join(f"  - {_fmt_row(c)}" for c in comps[:30]) or "  (none)") + "\n\n"
        + (f"FAIR VALUE ESTIMATE (deterministic, mileage-adjusted sold comps): ${fair['low']:,}–${fair['high']:,}, mid ${fair['mid']:,} ({fair.get('note', '')})\n\n" if fair else "")
        + f"FULL LISTING TEXT:\n{(listing.get('raw_text') or '')[:60_000]}"
        + _documents_block(listing.get("id"))
    )
    user = photos + [{"type": "text", "text": user_text}] if photos else user_text
    text = call_json_text(model or CONFIG.model_deep, system, user, max_tokens=32000, log_name="last_assess",
                          effort=effort, listing_id=listing_id)
    data = coerce.parse_json(text)
    try:
        return EvidenceInterpretation.model_validate(data)
    except ValidationError as e:
        missing = [".".join(str(x) for x in err["loc"]) for err in e.errors() if err["type"] == "missing"]
        if not missing:
            raise RuntimeError(f"model output failed schema validation: {e.errors()[:3]}")
    # One retry: the answer came back without required keys. Ask again, naming
    # them. The reminder is appended to the DYNAMIC block only, so the static
    # (cached) block's prefix is unchanged and the cache still hits.
    reminder_dynamic = dynamic + ("\n\nYOUR PREVIOUS ANSWER OMITTED REQUIRED KEYS: " + ", ".join(missing) +
                                  ". Return the complete JSON object again with every key listed above, including `ratings` "
                                  "(all six categories, each {rating, rationale}), `evidence_quality` and `immediate_service_estimate`.")
    text = call_json_text(model or CONFIG.model_deep, [STATIC_SYSTEM, reminder_dynamic], user, max_tokens=32000,
                          log_name="last_assess", effort=effort, listing_id=listing_id)
    try:
        return EvidenceInterpretation.model_validate(coerce.parse_json(text))
    except ValidationError as e:
        raise RuntimeError(f"model output failed schema validation after retry: {e.errors()[:3]}")
