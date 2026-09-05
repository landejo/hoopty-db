"""The guide's calibration scenarios, run through the deterministic engine with
hand-built evidence interpretations (no model calls)."""
import pytest

from scout import db
from scout.policy import POLICY_VERSION
from scout.policy.engine import assess
from scout.policy.schema import CategoryRating, EvidenceInterpretation, MoneyRange, Ratings
from scout.policy.state import DEFAULT_STATE, load_state, save_state


def _profile(key):
    p = db.get_profile(key)
    assert p, key
    return p


def _ev(doc=8, cond=8, val=7, fit=8, log=8, emo=8, quality=7, critical=None, flags=None, imm=(500, 1500), **kw):
    r = lambda v: CategoryRating(rating=v, rationale="test")
    return EvidenceInterpretation(
        ratings=Ratings(documentation=r(doc), condition=r(cond), price_value=r(val), mission_fit=r(fit), logistics=r(log), emotional_spec_fit=r(emo)),
        evidence_quality=quality, immediate_service_estimate=MoneyRange(low=imm[0], high=imm[1]),
        critical_evidence=[{"key": k, "status": v, "evidence": "", "source": "ai_inference"} for k, v in (critical or {}).items()],
        flags=flags or {}, **kw,
    )


def _listing(**kw):
    base = {"id": 1, "site": "facebook", "url": "u", "year": 2001, "make": "BMW", "model": "Z3 3.0i roadster", "trim": "Sport",
            "transmission": "Manual", "mileage": 80000, "price": 12000, "price_kind": "asking", "location": "Santa Cruz, CA",
            "engine_liters": 3.0, "photos": ["p"], "raw_text": "x" * 1000}
    base.update(kw)
    return base


STATE = dict(DEFAULT_STATE)


# 1. Cayman S with an excellent spec but no borescope -> Reject, regardless of arithmetic.
def test_cayman_s_without_borescope_is_rejected():
    l = _listing(make="Porsche", model="Cayman S", trim="S", engine_liters=3.4, year=2007, price=14500, mileage=60000)
    ev = _ev(doc=9, cond=9, val=9, fit=8, log=9, emo=10, quality=8,
             critical={"borescope": "claimed_only", "dme_overrev": "missing", "cooling_aos_service": "satisfied"})
    a = assess(l, _profile("porsche_987_cayman"), ev, STATE, mission="future_keeper")
    assert a.verdict == "Reject"
    assert any(g.kind == "hard" and g.key == "critical_missing:borescope" for g in a.gates)
    assert a.score.documentation <= 10 and "documentation capped" in " ".join(a.score.caps_applied)
    assert a.policy_version == POLICY_VERSION


# 2. Stock Z3 3.0 with a clean structural inspection but no reinforcement -> not penalized for the missing kit.
def test_stock_z3_clean_structure_no_reinforcement_can_pursue():
    ev = _ev(doc=9, cond=9, val=8, fit=9, log=10, emo=8, quality=9,
             critical={"rear_structure": "satisfied", "cooling_history": "satisfied"},
             flags={"age_related_service_documented": "yes"})
    a = assess(_listing(), _profile("z3_30i"), ev, STATE)
    assert a.verdict == "Pursue" and a.score.total >= 85
    assert not [g for g in a.gates if g.kind in {"hard", "conditional"}]


# 3. Z3 with missing rear-structure evidence -> capped at Maybe / verify even with a great score.
def test_z3_missing_rear_structure_is_capped():
    ev = _ev(doc=9, cond=9, val=9, fit=9, log=10, emo=9, quality=8,
             critical={"rear_structure": "claimed_only", "cooling_history": "satisfied"})
    a = assess(_listing(), _profile("z3_30i"), ev, STATE)
    assert a.verdict == "Maybe / verify"
    assert a.score.documentation <= 15
    assert any(g.kind == "conditional" and g.key == "critical_missing:rear_structure" for g in a.gates)
    assert a.costs.risk_reserve == 1500 + 1000  # one unresolved conditional adds reserve


# 4. GX470 with no timing-belt documentation -> conditional gate; automatic is fine in the utility mission.
def test_gx470_no_timing_belt_docs():
    l = _listing(make="Lexus", model="GX470", transmission="Automatic", year=2007, mileage=160000, price=14000, engine_liters=4.7)
    ev = _ev(doc=8, cond=8, val=8, fit=7, log=9, emo=6, quality=7,
             critical={"timing_belt_water_pump": "missing", "suspension_condition": "satisfied", "rust_evaluation": "satisfied",
                       "warning_lights": "satisfied", "matching_tires": "satisfied", "leaks_cooling_history": "satisfied"})
    a = assess(l, _profile("gx470"), ev, STATE, mission="utility_capability")
    assert a.verdict == "Maybe / verify"
    assert not any(g.kind == "configuration" for g in a.gates)
    assert any(g.key == "critical_missing:timing_belt_water_pump" for g in a.gates)


# 5. Salvage-title M Roadster with only seller assurances -> Maybe / verify at best, never Pursue.
def test_salvage_m_roadster_seller_assurances_only():
    l = _listing(model="Z3 M roadster", engine_liters=3.2, year=2000, price=8800, mileage=90000, title_status="salvage")
    ev = _ev(doc=7, cond=8, val=9, fit=8, log=10, emo=9, quality=3,
             critical={"rear_structure": "claimed_only", "cooling_history": "claimed_only", "s54_rod_bearings": "missing"},
             flags={"salvage_or_rebuilt_title": "yes", "salvage_fully_documented_with_specialist_ppi": "no",
                    "major_service_claimed_undocumented": "yes"})
    a = assess(l, _profile("z3_m"), ev, STATE, mission="enthusiast_bridge")
    assert a.verdict == "Maybe / verify"
    assert a.confidence < 50
    assert any(g.key == "salvage_or_rebuilt_title" for g in a.gates)
    # 2000 = S52: the S54 rod-bearing item must not apply.
    assert not any("s54" in g.key for g in a.gates)


# 6. Low early bid on a reserve auction is not a price; expected hammer drives cost and the max hammer.
def test_early_bid_on_reserve_auction_is_not_a_price():
    l = _listing(site="bat", price=3600, price_kind="current_bid", location="Portland, OR")
    ev = _ev(quality=7, critical={"rear_structure": "satisfied", "cooling_history": "satisfied"},
             flags={"reserve_auction": "yes"}, expected_hammer=MoneyRange(low=13000, high=17000))
    a = assess(l, _profile("z3_30i"), ev, STATE)
    assert a.costs.price_basis == "expected_hammer" and a.costs.price == 15000
    assert a.costs.buyer_fee == 750 and a.costs.transport == 900
    assert any("early bid" in n for n in a.costs.notes)
    # max hammer solved backward from acceptable all-in and everything else
    c = a.costs
    assert c.max_price + c.buyer_fee * 0 <= STATE["budget"]["acceptable_all_in"]
    fee_at_max = min(max(c.max_price * 0.05, 250), 7500)
    tax_at_max = c.max_price * STATE["tax_rate"] + STATE["registration_fee"]
    total = c.max_price + fee_at_max + c.transport + c.immediate_service_high + c.overdue_allowance + c.risk_reserve + tax_at_max
    assert abs(total - STATE["budget"]["acceptable_all_in"]) < 60


# 7. Relisted car with a substantial VIN-linked markup -> price/value capped, markup surfaced.
def test_relist_markup_caps_value():
    ev = _ev(val=10, quality=8, critical={"rear_structure": "satisfied", "cooling_history": "satisfied"})
    hist = {"prior_sales": [{"date": "2026-05-01", "price": 22337, "site": "bat"}], "markup_vs_last_sale": 0.32}
    a = assess(_listing(price=29500), _profile("z3_30i"), ev, STATE, vin_history=hist)
    assert a.score.price_value == 8
    assert any("relisted" in c for c in a.score.caps_applied)


# 8. Automatic enthusiast car that conflicts with the manual brief -> Reject: wrong configuration.
def test_automatic_in_enthusiast_search_is_wrong_configuration():
    l = _listing(make="BMW", model="128i", transmission="Automatic", year=2011, engine_liters=3.0)
    ev = _ev(doc=9, cond=9, val=9, fit=5, log=10, emo=6, quality=8,
             critical={"cooling_history": "satisfied", "oil_leaks": "satisfied"})
    a = assess(l, _profile("bmw_128i"), ev, STATE, mission="enthusiast_bridge")
    assert a.verdict == "Reject" and "wrong configuration" in a.verdict_reason
    assert a.score.total > 60  # the arithmetic is still reported, the gate decides


# 9. The same automatic intentionally evaluated as a pragmatic bridge -> no configuration gate, mission-fit capped.
def test_automatic_as_pragmatic_bridge_is_allowed_but_labeled():
    l = _listing(make="BMW", model="128i", transmission="Automatic", year=2011, engine_liters=3.0, price=9500)
    ev = _ev(doc=9, cond=9, val=9, fit=10, log=10, emo=5, quality=8,
             critical={"cooling_history": "satisfied", "oil_leaks": "satisfied"})
    a = assess(l, _profile("bmw_128i"), ev, STATE, mission="pragmatic_bridge")
    assert not any(g.kind == "configuration" for g in a.gates)
    assert a.mission == "pragmatic_bridge" and a.score.mission_fit == 11
    assert a.verdict in {"Pursue", "Pursue conditionally"}


# 10. Well-documented local car beats a more exciting but poorly documented remote car.
def test_documented_local_beats_exciting_remote():
    local = _listing(price=12500, location="San Jose, CA")
    ev_local = _ev(doc=9, cond=8, val=8, fit=9, log=10, emo=6, quality=9,
                   critical={"rear_structure": "satisfied", "cooling_history": "satisfied"})
    remote = _listing(price=11000, location="Boston, MA", model="Z3 3.0i coupe", exterior_color="Estoril Blue")
    ev_remote = _ev(doc=4, cond=6, val=8, fit=7, log=9, emo=10, quality=3,
                    critical={"rear_structure": "missing", "cooling_history": "missing"})
    a = assess(local, _profile("z3_30i"), ev_local, STATE)
    b = assess(remote, _profile("z3_30i"), ev_remote, STATE)
    assert a.score.total > b.score.total
    assert a.verdict in {"Pursue", "Pursue conditionally"}
    assert b.verdict in {"Maybe / verify", "Reject"}  # thin evidence + two unresolved items never reaches Pursue
    assert b.score.emotional_spec_fit == 5 and b.score.logistics <= 4  # rare color earns its 5 points and no more
    assert b.costs.transport == 1900 and a.costs.transport == 0


# Explicit exclusion -> Do not pursue.
def test_excluded_model_is_do_not_pursue():
    l = _listing(make="Mazda", model="MX-5 Miata", year=2008, engine_liters=2.0)
    a = assess(l, _profile("z3_30i"), _ev(critical={"rear_structure": "satisfied", "cooling_history": "satisfied"}), STATE)
    assert a.verdict == "Do not pursue"


# Cost gate: a bridge car whose risk-adjusted all-in defeats the purpose is a hard reject.
def test_all_in_cost_defeats_bridge_purpose():
    l = _listing(price=17500, location="Boston, MA")
    ev = _ev(quality=8, critical={"rear_structure": "satisfied", "cooling_history": "satisfied"}, imm=(2500, 4000))
    a = assess(l, _profile("z3_30i"), ev, STATE)
    assert a.costs.all_in_high > STATE["budget"]["defeats_purpose_all_in"]
    assert a.verdict == "Reject" and any(g.key == "cost_defeats_bridge_purpose" for g in a.gates)


# Schema: the model cannot smuggle in scores, verdicts, or bad enums.
def test_schema_rejects_bad_model_output():
    with pytest.raises(Exception):
        EvidenceInterpretation.model_validate({"ratings": {"documentation": {"rating": 11, "rationale": ""}}})
    ev = EvidenceInterpretation.model_validate({
        "ratings": {k: {"rating": 5, "rationale": "r"} for k in ("documentation", "condition", "price_value", "mission_fit", "logistics", "emotional_spec_fit")},
        "evidence_quality": 5, "immediate_service_estimate": {"low": 800, "high": 400},
        "flags": {"salvage_or_rebuilt_title": "yes"}, "facts": [{"key": "vin", "status": "unknown", "source": "listing_text"}],
    })
    assert ev.immediate_service_estimate.high == 800  # ordered
    ev2 = EvidenceInterpretation.model_validate({
        "ratings": {k: {"rating": 5, "rationale": "r"} for k in ("documentation", "condition", "price_value", "mission_fit", "logistics", "emotional_spec_fit")},
        "evidence_quality": 5, "immediate_service_estimate": {"low": 1, "high": 2},
        "facts": [{"key": "year", "value": 2007, "status": "verified", "source": "listing_text"}]})
    assert ev2.facts[0].value == "2007"  # the model returns numbers; keep them as text
    assert ev.flags.permanent_warning_lights == "unknown"  # unknown by default, never "no"


# State is editable and persisted; urgency mode validated.
def test_state_edit_and_reset():
    assert load_state()["urgency_mode"] == "accelerated_bridge"
    st = save_state({"budget": {"max_price": 16000}, "urgency_mode": "casual_search"})
    assert st["budget"]["max_price"] == 16000 and st["budget"]["ideal_low"] == 10000 and st["urgency_mode"] == "casual_search"
    with pytest.raises(ValueError):
        save_state({"urgency_mode": "panic"})


# VIN history from our own rows: prior sale, markup, disclosure change.
def test_vin_history_markup_and_disclosure():
    vin = "WBSCK93451LC98765"
    a, _ = db.upsert_listing({"site": "bat", "url": "https://bringatrailer.com/listing/a/", "vin": vin, "price": 22337,
                              "sold_price": 22337, "availability": "sold", "role": "comp", "mileage": 64000,
                              "normalized": {"red_flags": ["rear-structure photos not provided"]}, "first_seen": "2026-05-01T00:00:00+00:00"})
    b, _ = db.upsert_listing({"site": "carscom", "url": "https://www.cars.com/vehicledetail/b/", "vin": vin, "price": 29500,
                              "availability": "active", "role": "candidate", "mileage": 63500, "normalized": {"red_flags": []}})
    h = db.vin_history(vin, exclude_listing_id=b)
    assert h["prior_sales"][0]["price"] == 22337
    assert abs(h["markup_vs_last_sale"] - 0.321) < 0.01
    assert h["mileage_changes"] and "DOWN" in h["mileage_changes"][0]["note"]
    assert h["disclosure_changes"][0]["previously_disclosed"] == "rear-structure photos not provided"


def test_year_old_listing_is_capped_until_availability_confirmed():
    from datetime import date, timedelta
    l = _listing(listing_date=(date.today() - timedelta(days=400)).isoformat())
    ev = _ev(doc=9, cond=9, val=9, fit=9, log=10, emo=8, quality=9, critical={"rear_structure": "satisfied", "cooling_history": "satisfied"})
    a = assess(l, _profile("z3_30i"), ev, STATE)
    assert a.verdict == "Maybe / verify" and any(g.key == "stale_listing" for g in a.gates)


def test_schema_trims_long_strings_instead_of_rejecting():
    ev = EvidenceInterpretation.model_validate({
        "ratings": {k: {"rating": 5, "rationale": "x" * 3000} for k in ("documentation", "condition", "price_value", "mission_fit", "logistics", "emotional_spec_fit")},
        "evidence_quality": 5, "immediate_service_estimate": {"low": 1, "high": 2},
        "mission_note": "m" * 5000, "rationale": "r" * 9000, "positives": "one string, not a list",
        "facts": [{"key": "vin", "value": "x", "status": "verified", "source": "listing_text", "note": "n" * 2000}]})
    assert len(ev.ratings.condition.rationale) == 1500 and len(ev.rationale) == 4000 and ev.positives == ["one string, not a list"]
    assert len(ev.facts[0].note) == 400


def test_photo_blocks_skip_failures_and_render_prompt():
    from scout.ai.assess import photo_blocks, SYSTEM, MISSION_GUIDANCE
    assert photo_blocks(["http://127.0.0.1:1/nope.jpg", "not a url"]) == []
    assert "FRAMING RULES" in SYSTEM and "fly out" in SYSTEM


def test_schema_maps_vocabulary_drift_and_drops_only_the_bad_entry():
    ev = EvidenceInterpretation.model_validate({
        "ratings": {k: {"rating": 5, "rationale": "r"} for k in ("documentation", "condition", "price_value", "mission_fit", "logistics", "emotional_spec_fit")},
        "evidence_quality": 5, "immediate_service_estimate": {"low": 1, "high": 2},
        "facts": [{"key": "tires", "status": "missing", "source": "listing"},            # drift -> unknown / listing_text
                  {"key": "vin", "status": "confirmed", "source": "NHTSA"},              # -> verified / external_vin
                  {"key": "junk", "status": "verified"},                                 # no source -> kept as ai_inference
                  {"status": "verified", "source": "photo"},                             # no key: dropped, not fatal
                  "not even a dict"],
        "critical_evidence": [{"key": "rear_structure", "status": "unknown"}, {"key": "cooling_history", "status": "claimed"}],
        "flags": {"salvage_or_rebuilt_title": True, "permanent_warning_lights": "maybe", "made_up_flag": "yes"},
        "contradictions": [{"topic": "t", "detail": "d", "severity": "major"}],
        "concerns": [{"text": "Observed: leak"}, "plain"],
    })
    assert [(f.status, f.source) for f in ev.facts] == [("unknown", "listing_text"), ("verified", "external_vin"), ("verified", "ai_inference")]
    assert [c.status for c in ev.critical_evidence] == ["missing", "claimed_only"]
    assert ev.flags.salvage_or_rebuilt_title == "yes" and ev.flags.permanent_warning_lights == "unknown"
    assert ev.contradictions[0].severity == "material"
    assert ev.concerns == ["Observed: leak", "plain"]


def test_renamed_critical_evidence_keys_still_match_the_profile():
    from scout.policy.gates import match_reported
    from scout.policy.schema import CriticalEvidence
    reported = [CriticalEvidence(key="subframe_inspection_photos", status="satisfied", evidence="underside photos show intact welds", source="photo"),
                CriticalEvidence(key="cooling_system_receipts", status="claimed_only"),
                CriticalEvidence(key="s54_rod_bearing_records", status="missing"),
                CriticalEvidence(key="mod_reversibility", status="missing")]
    prof = _profile("z3_m")
    by = {req["key"]: match_reported(req, reported) for req in prof["critical_evidence"]}
    assert by["rear_structure"].key == "subframe_inspection_photos" and by["rear_structure"].status == "satisfied"
    assert by["cooling_history"].status == "claimed_only"
    assert by["s54_rod_bearings"].key == "s54_rod_bearing_records"
    # And through the engine: the satisfied rear structure raises no gate, the other two do.
    ev = _ev(critical={}, quality=8)
    ev.critical_evidence = reported
    a = assess(_listing(model="Z3 M coupe", engine_liters=3.2, year=2001), prof, ev, STATE, mission="future_keeper")
    keys = [g.key for g in a.gates]
    assert "critical_missing:rear_structure" not in keys
    assert "critical_missing:cooling_history" in keys and "critical_missing:s54_rod_bearings" in keys
