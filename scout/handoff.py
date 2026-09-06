"""Full-fidelity handoff bundle for an outside analyst (e.g. ChatGPT): the
methodology, the policy state, the guide, and everything the tracker holds on
the top-N active candidates. Writes a versioned Markdown file plus a JSON twin.

    .venv/bin/python -m scout.handoff            # top 10 -> data/handoffs/
    .venv/bin/python -m scout.handoff 15
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from scout import db
from scout.config import DATA_DIR, ROOT, SITES
from scout.policy import GUIDE_FILENAME, POLICY_VERSION
from scout.policy.preferences import CATEGORY_LABELS, CATEGORY_POINTS, SCORE_BANDS, VERDICTS
from scout.policy.state import load_state
from scout.scoring import listing_age_days, market_stats

HANDOFF_DIR = DATA_DIR / "handoffs"
RAW_TEXT_CAP = 6000
PHOTO_CAP = 12


def _money(v) -> str:
    return f"${int(v):,}" if isinstance(v, (int, float)) and v is not None else "—"


def _glance(l: dict[str, Any], a: dict[str, Any] | None, offset: int | None) -> float | None:
    if a and (a.get("score") or {}).get("total") is not None:
        return float(a["score"]["total"])
    p = l.get("prelim_score")
    if p is None:
        return None
    return max(0.0, min(100.0, float(p) + (offset or 0)))


def _calibration(listings: list[dict[str, Any]], A: dict[int, dict[str, Any]]) -> int | None:
    gaps = sorted(A[l["id"]]["score"]["total"] - l["prelim_score"] for l in listings
                  if l["id"] in A and l.get("prelim_score") is not None)
    return int(gaps[len(gaps) // 2]) if len(gaps) >= 3 else None


def top_candidates(n: int | None = 10, site: str | None = None, require_profile: bool = True
                   ) -> tuple[list[tuple[dict[str, Any], dict[str, Any] | None, float | None]], int | None]:
    """Live candidates (active or pending), ranked by assessed score where present,
    else the calibrated preliminary score. `site` limits to one source; n=None keeps all."""
    A = db.latest_assessments_by_vehicle()
    rows = [r for r in db.list_listings(role="candidate")
            if r["availability"] in ("active", "pending")
            and (r.get("profile_key") or not require_profile)
            and (site is None or r["site"] == site)]
    off = _calibration([r for r in db.list_listings(role="candidate")], A)
    ranked = sorted(((r, A.get(r["id"]), _glance(r, A.get(r["id"]), off)) for r in rows),
                    key=lambda t: -(t[2] if t[2] is not None else -1))
    return (ranked if n is None else ranked[:n]), off


def _md_list(items, empty="(none)") -> str:
    items = [str(x) for x in (items or []) if str(x).strip()]
    return "\n".join(f"- {x}" for x in items) if items else f"- {empty}"


def _facts_table(facts: list[dict[str, Any]]) -> str:
    if not facts:
        return "(no fact table: not assessed)"
    out = ["| fact | value | status | source | note |", "|---|---|---|---|---|"]
    for f in facts:
        out.append(f"| {f.get('key','')} | {str(f.get('value') or '—').replace('|','/')} | {f.get('status','')} | {f.get('source','')} | {str(f.get('note') or '').replace('|','/')[:160]} |")
    return "\n".join(out)


def car_section(rank: int, l: dict[str, Any], a: dict[str, Any] | None, glance: float | None, profile: dict[str, Any],
                state: dict[str, Any], comps: list[dict[str, Any]], peers: list[dict[str, Any]]) -> str:
    N = l.get("normalized") or {}
    title = l.get("title") or f"{l.get('year')} {l.get('make')} {l.get('model')}"
    age = listing_age_days(l)
    lines = [f"## {rank}. {title}", ""]
    lines.append(f"**Tracker id** #{l['id']} · **Source** {SITES.get(l['site'], l['site'])} · **URL** {l['url']}")
    gtxt = f"{glance:.0f}/100" if glance is not None else "—"
    lines.append(f"**Glance score** {gtxt} ({'assessed' if a else 'preliminary, calibrated'}) · **Status (Jason)** {l.get('status') or 'New'} · **Mission** {l.get('mission')} · **Profile** {profile.get('label') or 'none assigned'}")
    lines.append("")
    lines.append("### Identity and listing facts (tracker read)")
    facts = [("Year", l.get("year")), ("Make", l.get("make")), ("Model", l.get("model")), ("Generation", l.get("generation")), ("Trim", l.get("trim")),
             ("Engine", l.get("engine") or (f"{l.get('engine_liters')}L" if l.get("engine_liters") else None)), ("Transmission", l.get("transmission")),
             ("Drivetrain", l.get("drivetrain")), ("Body", l.get("body_style")), ("Exterior", l.get("exterior_color")), ("Interior", l.get("interior_color")),
             ("Mileage", f"{l['mileage']:,} mi" if l.get("mileage") else None), ("VIN", l.get("vin")), ("Price", f"{_money(l.get('price'))} ({l.get('price_kind') or 'asking'})"),
             ("Sold price", _money(l.get("sold_price")) if l.get("sold_price") else None), ("Location", l.get("location")), ("Seller", " · ".join(x for x in (l.get("seller_type"), l.get("seller_name")) if x)),
             ("Title status", l.get("title_status")), ("Accidents (listing)", l.get("accidents")), ("Owners", l.get("num_owners")),
             ("Listed", l.get("listing_date")), ("Listing age", f"{age} days" if age is not None else "n/a (live auction)"),
             ("Auction end", l.get("auction_end") or (l.get("raw") or {}).get("time_left")), ("Availability", l.get("availability")),
             ("First seen / last seen", f"{(l.get('first_seen') or '')[:10]} / {(l.get('last_seen') or '')[:10]}")]
    lines.append("\n".join(f"- **{k}:** {v}" for k, v in facts if v not in (None, "", "—")))
    if l.get("options"):
        lines.append(f"- **Options / equipment:** {', '.join(l['options'])}")
    if N.get("vin_decode"):
        d = N["vin_decode"]
        lines.append(f"- **NHTSA VIN decode:** {' · '.join(str(d[k]) for k in ('year','make','model','series','trim','engine_liters','body_class') if d.get(k))}")
    if N.get("vin_contradictions"):
        lines.append(f"- **Decode vs listing:** {'; '.join(c['detail'] for c in N['vin_contradictions'])}")
    lines.append("")
    lines.append("### Sync-time read (fast model)")
    lines.append(f"- **Summary:** {N.get('prelim_summary') or '—'}")
    lines.append(f"- **Highlights:** {'; '.join(N.get('highlights') or []) or '—'}")
    lines.append(f"- **Red flags (car-specific):** {'; '.join(N.get('red_flags') or []) or 'none'}")
    if N.get("ratings"):
        lines.append("- **Ratings (0-10):** " + " · ".join(f"{k} {v['score']} ({v.get('why','')[:120]})" for k, v in N["ratings"].items()))
    if N.get("price_drops"):
        lines.append("- **Site-reported price drops:** " + "; ".join(f"from {_money(d['prior_price'])} by {_money(d['amount'])}" for d in N["price_drops"]))
    if N.get("quick_gates"):
        lines.append(f"- **Sync-time flags:** {', '.join(N['quick_gates'])}")
    if N.get("prelim_breakdown"):
        lines.append("- **Preliminary score breakdown:** " + " · ".join(f"{k} {v['points']}/{v['max']}" for k, v in N["prelim_breakdown"].items()))
    lines.append("")
    if a:
        S, E, C = a["score"], a["evidence"], a["costs"]
        lines.append(f"### Assessment ({a.get('model')} · policy {a.get('policy_version')}{' · re-derived from ' + a['rescored_from'] if a.get('rescored_from') else ''} · {a.get('assessed_at','')[:16]})")
        lines.append(f"- **Verdict:** {a['verdict']} — {a.get('verdict_reason','')}")
        lines.append(f"- **Score:** {S['total']}/100 = " + " + ".join(f"{k} {S[k]}/{CATEGORY_POINTS[k]}" for k in CATEGORY_POINTS) + (f" · caps: {'; '.join(S.get('caps_applied') or [])}" if S.get("caps_applied") else ""))
        lines.append(f"- **Assessment confidence:** {a['confidence']}/100 · **evidence quality (model):** {E.get('evidence_quality')}/10")
        lines.append(f"- **Mission as judged:** {a['mission']} · **urgency:** {a['urgency_mode']}" + (f" · **context changed since:** {', '.join(a['context_changed'])}" if a.get("context_changed") else ""))
        if a.get("gates"):
            lines.append("- **Gates:** " + "; ".join(f"[{g['kind']}] {g['reason']}" for g in a["gates"]))
        lines.append("")
        lines.append("**Category ratings (model, 0-10) with rationale**")
        for k in CATEGORY_POINTS:
            r = (E.get("ratings") or {}).get(k) or {}
            lines.append(f"- {CATEGORY_LABELS[k]}: {r.get('rating','—')} — {r.get('rationale','')}")
        lines.append("")
        lines.append("**Model-critical evidence**")
        lines.append(_md_list([f"{c['key']}: {c['status']}" + (f" — {c['evidence']}" if c.get("evidence") else "") + f" (source: {c.get('source','')})" for c in E.get("critical_evidence") or []]))
        lines.append("")
        lines.append("**Flags (yes/no/unknown)**: " + ", ".join(f"{k}={v}" for k, v in (E.get("flags") or {}).items()))
        if E.get("contradictions"):
            lines.append("\n**Contradictions**\n" + _md_list([f"[{c['severity']}] {c['topic']}: {c['detail']}" for c in E["contradictions"]]))
        lines.append("\n**Positives**\n" + _md_list(E.get("positives")))
        lines.append("\n**Concerns**\n" + _md_list(E.get("concerns")))
        lines.append("\n**Dealbreakers**\n" + _md_list(E.get("dealbreakers"), "none identified"))
        lines.append("\n**Unknowns / missing evidence**\n" + _md_list(E.get("unknowns")))
        lines.append("\n**Questions for the seller**\n" + "\n".join(f"{i+1}. {q}" for i, q in enumerate(E.get("seller_questions") or [])))
        lines.append("\n**PPI focus**\n" + _md_list(E.get("ppi_focus")))
        lines.append("\n**What would change the verdict**\n" + _md_list(E.get("what_would_change_verdict")))
        lines.append(f"\n**Mission note:** {E.get('mission_note') or '—'}")
        lines.append(f"\n**Rationale:** {E.get('rationale') or '—'}")
        lines.append(f"\n**Next action:** {E.get('next_action') or '—'}")
        lines.append("")
        lines.append("**Price discipline (deterministic)**")
        lines.append(f"- Basis: {C['price_basis']} {_money(C['price'])} · buyer fee {_money(C['buyer_fee'])} · transport {_money(C['transport'])} · CA tax+reg {_money(C['tax_and_registration'])}")
        lines.append(f"- Known repairs (this car, counted): {_money(C.get('known_work_low',0))}–{_money(C.get('known_work_high',0))} {', '.join(C.get('known_work_items') or [])}")
        lines.append(f"- **All-in:** {_money(C['all_in_low'])}–{_money(C['all_in_high'])}")
        lines.append(f"- Not counted: likely catch-up {_money(C['immediate_service_low'])}–{_money(C['immediate_service_high'])} · overdue allowance {_money(C['overdue_allowance'])} · model risk reserve {_money(C['risk_reserve'])} → if all of that lands {_money(C.get('with_catchup_low'))}–{_money(C.get('with_catchup_high'))}")
        lines.append(f"- **Maximum price / hammer:** {_money(C['max_price'])} · **offer range:** {_money(C['offer_low'])}–{_money(C['offer_high'])}")
        if C.get("notes"):
            lines.append("- Notes: " + " ".join(C["notes"]))
        if E.get("expected_hammer"):
            lines.append(f"- Expected hammer (model): {_money(E['expected_hammer']['low'])}–{_money(E['expected_hammer']['high'])}")
        lines.append("")
        lines.append("**Facts with provenance**\n" + _facts_table(E.get("facts") or []))
        vh = a.get("vin_history") or {}
        if vh.get("prior_listings") or vh.get("recalls"):
            lines.append("\n**VIN history in the tracker**")
            for p in vh.get("prior_listings") or []:
                lines.append(f"- {p.get('first_seen','')[:10]} {SITES.get(p.get('site'), p.get('site'))} {_money(p.get('sold_price') or p.get('price'))} {p.get('availability')} {p.get('url')}")
            if vh.get("markup_vs_last_sale") is not None:
                lines.append(f"- Markup vs last sale: {vh['markup_vs_last_sale']:.1%}")
            if vh.get("recalls"):
                lines.append(f"- NHTSA campaigns for make/model/year: {len(vh['recalls'])} (completion unknown)")
        lines.append("")
    else:
        lines.append("### Assessment\n(not yet assessed by the deep model; preliminary only)\n")
    hist = db.list_snapshots(l["id"])
    if hist:
        lines.append("### Price / availability history (tracker snapshots)")
        lines.append("\n".join(f"- {s['seen_at'][:10]}: {_money(s.get('price'))} {s.get('price_kind') or ''} {s.get('availability') or ''}" + (f" · {s['bid_count']} bids" if s.get("bid_count") is not None else "") for s in hist))
        lines.append("")
    if l.get("vehicle_id"):
        ev = db.vehicle_events(l["vehicle_id"])
        if len(ev) > 1:
            lines.append("### Vehicle timeline (same VIN / record)")
            lines.append("\n".join(f"- {e.get('event_date') or 'undated'} · {e.get('venue')} · {e.get('status')} · {_money(e.get('price'))} {e.get('price_type') or ''} · {e.get('evidence') or ''} [{e.get('identity_confidence')}]" for e in ev))
            lines.append("")
    if l.get("provenance"):
        P = l["provenance"]
        lines.append("### Provenance investigation")
        lines.append(f"- Confidence: {P.get('confidence_label')} · flags: {', '.join(P.get('flags') or []) or 'none'}")
        if P.get("summary"):
            lines.append(f"- {P['summary']}")
        for e in P.get("same_car_history") or []:
            lines.append(f"- {e.get('date')} · {e.get('venue')} · {e.get('status')} · {e.get('description')} {e.get('url') or ''}")
        for s_ in P.get("cross_post_findings") or []:
            lines.append(f"- Seller statement ({s_.get('kind')}, {s_.get('date')}): \"{s_.get('quote')}\" {s_.get('url') or ''}")
        for e in P.get("effect") or []:
            lines.append(f"- Effect: {e}")
        lines.append("")
    ms = market_stats(comps, peers)
    lines.append("### Market context for this profile")
    mm = f"{ms['mileage_median']:,}" if ms.get("mileage_median") else "—"
    lines.append(f"- Active peers {ms.get('active_count')} (asking median {_money(ms.get('asking_median'))}) · comps {ms.get('comp_count')} (sold {ms.get('sold_count')}, sold median {_money(ms.get('sold_median'))}, range {_money(ms.get('sold_low'))}–{_money(ms.get('sold_high'))}) · mileage median {mm}")
    for c in sorted(comps, key=lambda c: c.get("listing_date") or c.get("last_seen") or "", reverse=True)[:12]:
        cm = f"{c['mileage']:,} mi" if c.get("mileage") else "? mi"
        lines.append(f"  - comp: {c.get('year')} {c.get('model')} {c.get('trim') or ''} · {cm} · {_money(c.get('sold_price') or c.get('price'))} {c.get('price_kind') or ''} {c.get('availability')} · {SITES.get(c['site'], c['site'])} · {(c.get('listing_date') or c.get('last_seen') or '')[:10]}")
    lines.append("")
    photos = (l.get("photos") or [])[:PHOTO_CAP]
    if photos:
        lines.append(f"### Photos captured ({len(l.get('photos') or [])}; first {len(photos)} listed)")
        lines.append("\n".join(f"- {p}" for p in photos))
        lines.append("")
    raw = (l.get("raw_text") or "").strip()
    lines.append(f"### Listing text as captured{' (truncated to ' + str(RAW_TEXT_CAP) + ' chars)' if len(raw) > RAW_TEXT_CAP else ''}")
    lines.append("```\n" + raw[:RAW_TEXT_CAP] + "\n```")
    if l.get("notes"):
        lines.append(f"\n**Jason's notes:** {l['notes']}")
    lines.append("\n---\n")
    return "\n".join(lines)


def profile_section(p: dict[str, Any]) -> str:
    out = [f"### {p['label']} (`{p['key']}`, {p['source']}{', unverified' if not p.get('verified') else ''})"]
    for k in ("framing", "weak_points", "immediate_repairs", "repairs_12mo", "market_notes", "catchup_notes"):
        if p.get(k):
            out.append(f"- **{k.replace('_', ' ')}:** {p[k]}")
    out.append(f"- **default mission:** {p.get('mission_default')} · **risk reserve:** {_money(p.get('risk_reserve'))} · **automatic acceptable:** {bool(p.get('automatic_ok'))}")
    out.append("- **model-critical evidence:** " + "; ".join(f"{c['label']} [{c['severity']}]" for c in p.get("critical_evidence") or []))
    out.append("- **dealbreaker rules:** " + "; ".join(p.get("dealbreakers") or []))
    out.append("- **PPI checklist:** " + "; ".join(c["label"] for c in p.get("checks") or []))
    return "\n".join(out) + "\n"


def build(n: int | None = 10, site: str | None = None, brief: str = "decision") -> tuple[str, dict[str, Any]]:
    state = load_state()
    ranked, offset = top_candidates(n, site=site, require_profile=False)
    today = date.today().isoformat()
    guide = (ROOT / "scout" / "policy" / GUIDE_FILENAME).read_text()
    changes = (ROOT / "scout" / "policy" / "POLICY_CHANGES.md").read_text()
    profiles_used = {}
    for l, _, _ in ranked:
        if l.get("profile_key"):
            profiles_used[l["profile_key"]] = db.get_profile(l["profile_key"])
    fresh = sum(1 for _, a, _ in ranked if a and not a.get("rescored_from"))
    rederived = sum(1 for _, a, _ in ranked if a and a.get("rescored_from"))
    prelim_only = sum(1 for _, a, _ in ranked if not a)

    scope = f"all {len(ranked)} live {SITES.get(site, site)} listings" if site else f"top {len(ranked)} active candidates"
    md = [f"# Hoopty Scout handoff — {scope}", f"Generated {today} · policy {POLICY_VERSION} · " +
          ("for independent research on each vehicle" if brief == "research" else "for an independent second analysis"), ""]
    md.append("## 0. What I want from you")
    if brief == "research":
        md.append("""You are receiving every vehicle listing one buyer, Jason (Carmel, California), currently has SAVED and still
available on """ + (SITES.get(site, site or "these sources")) + """ — nothing sold, ended or withdrawn. For each car you have the
buyer's own decision guide (Appendix A), the tracking system's methodology and settings, the vehicle profile it applies, every
fact it holds with a provenance label, its scores and cost arithmetic, the price history, market comps, photo URLs and the
captured listing text.

Your job is RESEARCH, not ranking. For each vehicle:
1. **Establish identity.** Decode the VIN where present; confirm year, engine variant, transmission and trim against the
   listing's own claims. Flag any mismatch. Where no VIN is given, say what would be needed to pin the car down.
2. **Find the car's history beyond this listing.** Search the VIN, the plate if visible, distinctive photos, the seller name,
   and the year/model/colour/mileage combination across Bring a Trailer, Cars & Bids, eBay (including completed listings),
   Classic.com, Hagerty, dealer sites, marque forums, Reddit and public Facebook posts. Report prior sales, prior asking
   prices, relists, mileage changes, disclosures that appeared in one advertisement and vanished from another, and any seller
   statements made elsewhere (including "sold", "withdrawn" or "decided to keep"). Grade every match: exact VIN = confirmed;
   identical plate or identical photographs plus coherent mileage/colour/equipment/location/chronology = strongly likely;
   similar specification only = possible. Never call two listings the same car on year, colour and approximate mileage alone.
3. **Test the seller's claims.** Separate what the listing establishes from what it merely asserts. "Fully serviced",
   "runs great" and "rear welds intact" are claims, not evidence.
4. **Price it against the real market.** Find recent comparable sales, not asking prices, and say what this specific car is
   worth given its mileage, condition evidence, spec and location. Note where the tracker's comps look thin or wrong.
5. **Name the model-specific risks** for this exact variant and year, and say which are resolved by the listing and which are open.
6. **State what is still unknown** and the cheapest way to resolve each item (a question, a photo, a scan, a specific PPI).

Deliver a per-vehicle research note with sources linked and dated, then a short section listing the cars where your research
found something the tracker did not know. Distinguish facts, inferences and recommendations throughout. Use the guide's verdict
vocabulary only if you choose to give a verdict (Pursue / Pursue conditionally / Maybe verify / Reject / Do not pursue), and
never use "pass" as a positive.""")
    else:
        md.append("""You are receiving the full contents of a personal used-car tracking system for one buyer, Jason, in Carmel, California.
Everything below is data: the buyer's own decision guide (Appendix A), the system's methodology, its current budget and
mission settings, the vehicle profiles it uses, and, for each of the top candidates, every fact, model reading, score, cost
figure, price-history event and the raw listing text it holds. Treat the system's scores and verdicts as one opinion, not as
ground truth, and treat every seller sentence as a claim until evidence backs it.

Do your own full analysis against the guide in Appendix A and return:
1. A ranked **top 5** of the cars Jason should consider buying now, with a detailed explanation for each: why it fits his
   current mission and urgency, what the evidence actually establishes versus what is only claimed, the model-specific risks
   and whether they are resolved, a risk-adjusted all-in cost and a maximum price you would defend, and the single next action.
2. For every car you leave out of the five, one or two sentences on why.
3. Anywhere you disagree with the system's verdict, score, gate, or cost arithmetic, say so and say why.
4. A short list of the questions Jason should ask before spending money on any of the five.
Use the guide's verdict vocabulary (Pursue / Pursue conditionally / Maybe verify / Reject / Do not pursue) and never use "pass" as a positive.""")
    md.append("")
    md.append("## 1. How the system works (methodology)")
    md.append(f"""**Pipeline.** A Chrome extension reads Jason's *saved* listings on Facebook Marketplace, CarGurus, Cars.com, Autotrader,
Cars & Bids and Bring a Trailer in his own logged-in browser, opens each listing for its full text and photos, and posts them to a
local server. Sold and ended listings are kept as market comps. Every listing gets a **sync-time read** by a fast model
({state.get('models', {}).get('fast', 'Claude Sonnet')}) that extracts structured facts, picks a vehicle profile, lists car-specific
red flags, and rates documentation / condition / spec appeal 0-10 with a one-line reason. A free NHTSA VIN decode runs on every VIN
and any decode-versus-listing mismatch is recorded. Listings sharing a VIN are one vehicle record with a timeline of
listed / price reduced / sold / withdrawn events across venues.

**Preliminary score (no deep model).** Same 100-point rubric as the assessment, cheap inputs: documentation and condition and
spec from the fast model's ratings; price/value from the car's price against the median of sold comps (or active peers) with a
mileage adjustment; mission fit from transmission versus mission, budget band, age of the car and age of the listing;
logistics from distance to Carmel. Shown dashed / "≈" and calibrated by the measured median gap between preliminary and assessed
scores ({'currently ' + str(offset) if offset is not None else 'not yet measured'}).

**Deep assessment.** A larger model (Claude Sonnet for the quick tier, Claude Opus for the full tier) receives the listing text,
up to 12 captured photos, the profile, the budget/mission state, the VIN history, active peers and sold comps. It returns
**evidence only**, validated against a schema: facts with a status (verified / claimed / inferred / unknown) and a provenance
source (receipt, history_report, photo, external_vin, listing_text, seller_comment, seller_claim, ai_inference); contradictions;
the status of each model-critical evidence item (satisfied / claimed_only / missing / failed); yes-no-unknown gate flags; 0-10
category ratings with rationale; a likely-catch-up estimate and a known-repairs estimate; positives, concerns (labelled
Observed vs Unverified), unknowns, seller questions, PPI focus, what would change the verdict, rationale, one next action.
The model never computes the score, cost or verdict.

**Deterministic policy engine (policy {POLICY_VERSION}).**
- Gates first. *Hard* (verdict Reject regardless of score): seller refuses VIN/PPI; unresolved identity or odometer fraud;
  active overheating/coolant loss; serious brake/oil-pressure issue; unsafe structure or heavy rust; California emissions or
  registration infeasible; a hard model-critical item missing or failed (today only the Cayman S borescope); risk-adjusted
  all-in above the bridge ceiling (midpoint of the range). *Strategy* (Do not pursue): explicitly excluded models; seller has
  withdrawn the car. *Configuration* (Reject): automatic in a manual-required mission. *Conditional* (cap the verdict at
  Maybe / verify until resolved): salvage/rebuilt title without full evidence; accident without repair docs; permanent warning
  lights; undocumented powertrain mods; remote auction with no PPI; major service claimed but undocumented; a conditional
  model-critical item missing or claimed-only (a "failed" conditional item reads as strong reservations); listing older than
  {(state.get('listing_age') or {}).get('stale_after_days', 120)} days.
- Score: {' / '.join(f'{CATEGORY_LABELS[k]} {v}' for k, v in CATEGORY_POINTS.items())} = 100. Category points = max × rating/10,
  then caps: documentation capped at 20 while a conditional critical item is unresolved (10 if hard); logistics capped by
  location band; mission fit capped when over budget; price/value capped when relisted ≥20% above the last documented sale
  without documented work.
- Verdict bands: {', '.join(f'{v} ≥ {s}' for s, v in SCORE_BANDS if s)}, Reject below 45; conditional gates and confidence < 50 cap at Maybe / verify.
- Confidence (0-100, separate from the score): from the model's evidence-quality rating, minus unknown facts, unresolved critical
  items, contradictions, missing photos, thin text.
- Costs: **all-in = price (or expected hammer) + buyer fee + transport + California tax/registration + known repairs this listing
  establishes.** Likely catch-up, an age/mileage overdue allowance and a model risk reserve are shown but not counted.
  Maximum hammer is solved backward from the acceptable all-in. An early bid on a live auction (more than
  {state.get('early_bid_hours_before_close', 24)}h from close) is not a price; the expected hammer or sold-comp median is used.
- Provenance: when run, a browser-driven same-car search across the web, auction sites, eBay sold, Classic.com, Reddit and
  Facebook; matches are graded confirmed / strongly likely / possible / not established; markup, elapsed time, mileage added,
  recent resale, rapid relisting and seller withdrawal statements are computed and gate the verdict.

**Missions:** enthusiast_bridge (manual required), pragmatic_bridge (automatic tolerated, must win decisively), future_keeper,
utility_capability (SUV branch, automatic fine). **Urgency mode:** {state.get('urgency_mode')}.""")
    md.append("")
    md.append("## 2. Current policy state (editable settings the judgements were made under)")
    b = state.get("budget") or {}
    md.append(f"- Budget: ideal {_money(b.get('ideal_low'))}–{_money(b.get('ideal_high'))} · price ceiling {_money(b.get('max_price'))} · acceptable all-in {_money(b.get('acceptable_all_in'))} · bridge-defeating all-in {_money(b.get('defeats_purpose_all_in'))}")
    md.append(f"- Urgency: {state.get('urgency_mode')} · home {state.get('home_location')} · travel: {state.get('travel')}")
    md.append(f"- Current vehicles: " + "; ".join(f"{v['name']} ({v['role']})" for v in state.get("current_vehicles") or []))
    md.append(f"- Active exclusions: {', '.join(state.get('active_exclusions') or [])} · deprioritized: {', '.join(state.get('deprioritized') or [])}")
    md.append(f"- Fees (verify): " + "; ".join(f"{k} {int(v['pct']*100)}% min {_money(v['min'])} max {_money(v['max'])}" for k, v in (state.get('fees') or {}).items() if v.get('pct')))
    md.append(f"- Transport by location band (5 = Monterey/Bay … 1 = Northeast): {state.get('transport_by_locality_band')} · tax {state.get('tax_rate')} + registration {_money(state.get('registration_fee'))}")
    md.append(f"- Listing age: fresh ≤ {(state.get('listing_age') or {}).get('fresh_days')} days, stale after {(state.get('listing_age') or {}).get('stale_after_days')} days, mission-fit penalty steps {(state.get('listing_age') or {}).get('steps')}")
    md.append("")
    md.append(f"## 3. Data coverage note\nOf the {len(ranked)} cars below: {fresh} carry a fresh assessment under policy {POLICY_VERSION}, {rederived} carry an assessment whose model ratings were formed under an earlier policy/budget and whose arithmetic was re-derived, {prelim_only} are preliminary only. Ranking is by assessed score where present, else the calibrated preliminary score.\n")
    md.append("## 4. Vehicle profiles used by these candidates\n")
    for p in profiles_used.values():
        if p:
            md.append(profile_section(p))
    md.append("## 5. The candidates\n")
    export_cars = []
    for i, (l, a, g) in enumerate(ranked, 1):
        prof = profiles_used.get(l.get("profile_key")) or {}
        comps = db.list_listings(role="comp", profile_key=l["profile_key"]) if l.get("profile_key") else []
        peers = [p for p in (db.list_listings(role="candidate", profile_key=l["profile_key"]) if l.get("profile_key") else [])
                 if p["availability"] in ("active", "pending") and p["id"] != l["id"]]
        md.append(car_section(i, l, a, g, prof, state, comps, peers))
        export_cars.append({"rank": i, "glance": g, "listing": {k: v for k, v in l.items() if k not in ("raw_text",)},
                            "raw_text": (l.get("raw_text") or "")[:RAW_TEXT_CAP], "assessment": a,
                            "snapshots": db.list_snapshots(l["id"]), "timeline": db.vehicle_events(l["vehicle_id"]) if l.get("vehicle_id") else [],
                            "market": market_stats(comps, peers), "comps": [{k: c.get(k) for k in ("year", "model", "trim", "mileage", "price", "sold_price", "price_kind", "availability", "site", "listing_date", "url")} for c in comps]})
    md.append("## Appendix A — Jason's assessment guide (verbatim)\n")
    md.append(guide)
    md.append("\n## Appendix B — Policy changes relative to the guide\n")
    md.append(changes)
    md.append("\n## Appendix C — Verdict vocabulary\n" + ", ".join(VERDICTS) + ". Never use \"pass\" as a positive verdict.\n")
    bundle = {"generated": datetime.now(timezone.utc).replace(microsecond=0).isoformat(), "policy_version": POLICY_VERSION,
              "scope": {"site": site, "brief": brief, "count": len(ranked)},
              "state": {k: state.get(k) for k in ("urgency_mode", "budget", "home_location", "travel", "active_exclusions", "deprioritized", "fees", "transport_by_locality_band", "tax_rate", "registration_fee", "listing_age")},
              "calibration_offset": offset, "profiles": profiles_used, "cars": export_cars}
    return "\n".join(md), bundle


def write(n: int | None = 10, site: str | None = None, brief: str = "decision") -> tuple[Path, Path]:
    HANDOFF_DIR.mkdir(parents=True, exist_ok=True)
    stamp = date.today().strftime("%Y%m%d")
    label = f"{site.capitalize()}_" if site else ""
    existing = sorted(HANDOFF_DIR.glob(f"Hoopty_Scout_{label}Handoff_v*_{stamp}.md"))
    version = 1 + max((int(re.search(r"_v(\d+)_", p.name).group(1)) for p in existing), default=0)
    md, bundle = build(n, site=site, brief=brief)
    md_path = HANDOFF_DIR / f"Hoopty_Scout_{label}Handoff_v{version}_{stamp}.md"
    json_path = HANDOFF_DIR / f"Hoopty_Scout_{label}Handoff_v{version}_{stamp}.json"
    md_path.write_text(md)
    json_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=1, default=str))
    return md_path, json_path


if __name__ == "__main__":
    args = sys.argv[1:]
    site = None
    brief = "decision"
    n: int | None = 10
    for a_ in args:
        if a_.startswith("--site="):
            site = a_.split("=", 1)[1]
        elif a_.startswith("--brief="):
            brief = a_.split("=", 1)[1]
        elif a_ in ("--all", "all"):
            n = None
        elif a_.isdigit():
            n = int(a_)
    db.init_db()
    m, j = write(n, site=site, brief=brief)
    print(m)
    print(j)
