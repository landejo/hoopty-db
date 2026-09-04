"""Classify search hits into same-car events and seller statements (deep
model). Identity confidence follows the brief: confirmed = exact VIN;
strongly_likely = plate or identical photos plus coherent mileage/color/
equipment/location/chronology; possible = similar spec only; else not
established. Output validated against ProvenanceInterpretation."""
from __future__ import annotations

import json
from datetime import date
from typing import Any

from pydantic import ValidationError

from scout import coerce
from scout.ai import call_text
from scout.config import CONFIG
from scout.policy.schema import ProvenanceInterpretation
from scout.provenance import IDENTITY, PRICE_TYPES, STATUSES

SYSTEM = """You investigate the provenance of ONE specific used car for a buyer.
You are given the car's identity (VIN when known, year/model/trim/engine/
transmission, mileage, colors, location, options, seller), the tracker's own
timeline for that VIN, and raw search hits (title, snippet, sometimes the
scraped page text) from web, auction, marketplace, forum and social searches.

Rules:
- The VIN is the canonical identifier. Never state two listings are the same
  car based only on year, color and approximate mileage. Do not assume similar
  usernames are the same person without corroboration.
- identity_confidence: confirmed (exact VIN match) / strongly_likely (identical
  plate, or identical photographs plus a coherent match on mileage, color,
  equipment, location and chronology) / possible (similar spec or mileage,
  no unique identifier) / not_established.
- price_type: {price_types}. Never call an asking price a sale price. A
  listing marked sold with only an advertised price is advertised_sold.
- status: one of {statuses}.
- Seller statements: quote them, date them, link them. Separate factual
  admissions from opinions. Surface "sold", "withdrawn", "decided to keep",
  reasons for selling, recent-purchase admissions, problems discussed
  elsewhere, PPI results, track use, failed sales, earlier lower prices,
  contradictions between listings. Review edits and comments, not only the
  original post.
- Do not stop at the current advertisement or a generic history report.
- Unknown is unknown. Do not invent dates or prices; use null.

Return ONE JSON object:
- events: array of {{date (YYYY-MM-DD or null), venue, url, mileage (int|null),
  price (int|null), price_type, status, evidence (short quote or description),
  identity_confidence, identity_basis (why you believe it is / is not the same
  car), seller (username/dealer or null)}}. Include every confirmed, strongly
  likely AND possible match; the code decides what counts.
- seller_statements: array of {{date, url, venue, kind, quote, factual (bool)}}
  where kind is one of withdrawn, keep, sold, reason_for_selling,
  recent_purchase, problem, ppi, track_use, failed_sale, earlier_price,
  condition_opinion, contradiction, other.
- work_before_prior_sale: strings (already reflected in the earlier price)
- work_after_prior_sale: strings (done by the current owner since)
- cosmetic_or_preference: strings
- repairs_correcting_faults: strings
- identity_notes: 1-3 sentences on how identity was established.
- summary: 3-6 sentences, leading with the most consequential finding.

TODAY IS {today}. No prose outside the JSON."""


def interpret_hits(listing: dict[str, Any], events: list[dict[str, Any]], hits: list[dict[str, Any]]) -> ProvenanceInterpretation:
    system = SYSTEM.format(price_types=", ".join(PRICE_TYPES), statuses=", ".join(STATUSES), today=date.today().isoformat())
    identity = {k: listing.get(k) for k in ("vin", "year", "make", "model", "trim", "engine", "transmission", "mileage",
                                           "exterior_color", "interior_color", "location", "options", "seller_name",
                                           "seller_type", "url", "site_id", "price", "price_kind", "listing_date") if listing.get(k) not in (None, "", [])}
    hits_txt = []
    for h in hits[:80]:
        block = f"[{h.get('engine')}] query: {h.get('query')}\nURL: {h.get('url')}\nTITLE: {h.get('title')}\nSNIPPET: {h.get('snippet')}"
        if h.get("detail_text"):
            block += f"\nPAGE TEXT: {h['detail_text'][:6000]}"
        hits_txt.append(block)
    user = (
        f"VEHICLE IDENTITY:\n{json.dumps(identity, indent=1)}\n\n"
        f"TRACKER TIMELINE (already recorded, tied to this VIN or fingerprint):\n{json.dumps(events, indent=1)[:8000]}\n\n"
        f"CURRENT LISTING TEXT (excerpt):\n{(listing.get('raw_text') or '')[:8000]}\n\n"
        f"SEARCH HITS ({len(hits)}):\n\n" + "\n\n".join(hits_txt)
    )
    text = call_text(CONFIG.model_deep, system, user, max_tokens=12000, log_name="last_provenance", effort="high")
    try:
        return ProvenanceInterpretation.model_validate(coerce.parse_json(text))
    except ValidationError as e:
        raise RuntimeError(f"provenance output failed schema validation: {e.errors()[:3]}")
