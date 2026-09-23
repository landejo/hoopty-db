"""Tests for Tranche 3 (stage model + pursue-next priority, policy 1.4.0):
scout/stage.py, and the new open-question/observed classification, upside,
priority and next-steps in scout/policy/{scoring,engine}.py."""
from fastapi.testclient import TestClient

from scout import db
from scout.ingest import ingest_items
from scout.policy import POLICY_VERSION
from scout.policy.engine import assess, compute_next_steps, compute_priority
from scout.policy.schema import CategoryRating, CostBreakdown, EvidenceInterpretation, Gate, MoneyRange, Ratings, Score
from scout.policy.scoring import classify_conditionals, compute_upside, verdict_from
from scout.policy.state import DEFAULT_STATE
from scout.server import app
from scout.stage import stage_for

STATE = dict(DEFAULT_STATE)


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
            "engine_liters": 3.0, "photos": ["p"], "raw_text": "x" * 1000, "vin": "WBSCK93451LC98765"}
    base.update(kw)
    return base


def _gate(kind, key, reason):
    return Gate(kind=kind, key=key, reason=reason)


def _score(total, doc=20, cond=20, val=10, fit=10, log=8, emo=8):
    return Score(documentation=doc, condition=cond, price_value=val, mission_fit=fit, logistics=log, emotional_spec_fit=emo, total=total)


def _costs(price_basis="asking"):
    return CostBreakdown(price_basis=price_basis, price=10000, buyer_fee=0, transport=0,
                         immediate_service_low=0, immediate_service_high=0, overdue_allowance=0,
                         risk_reserve=0, tax_and_registration=0, all_in_low=10000, all_in_high=10000,
                         max_price=10000, offer_low=9000, offer_high=10000)


# ---------- stage_for ----------

def test_stage_for_defaults_to_listing():
    assert stage_for({"status": "New"}, []) == "listing"


def test_stage_for_questions_on_contacted_or_ppi_scheduled():
    assert stage_for({"status": "Contacted"}, []) == "questions"
    assert stage_for({"status": "PPI Scheduled"}, []) == "questions"


def test_stage_for_docs_on_any_non_inspection_document():
    assert stage_for({"status": "New"}, [{"kind": "carfax"}]) == "docs"
    assert stage_for({"status": "New"}, [{"kind": "service_records"}]) == "docs"
    assert stage_for({"status": "New"}, [{"kind": "other"}]) == "docs"


def test_stage_for_ppi_on_inspection_document_or_terminal_status():
    assert stage_for({"status": "New"}, [{"kind": "inspection"}]) == "ppi"
    assert stage_for({"status": "Offer Made"}, []) == "ppi"
    assert stage_for({"status": "Purchased"}, []) == "ppi"


def test_stage_for_inspection_beats_other_documents_and_status():
    assert stage_for({"status": "Contacted"}, [{"kind": "carfax"}, {"kind": "inspection"}]) == "ppi"


# ---------- gate classification by stage ----------

def test_classify_conditionals_open_vs_observed():
    gates = [
        _gate("conditional", "critical_missing:cooling_history", "Cooling-system receipts: missing"),
        _gate("conditional", "critical_missing:rear_structure", "Rear structure inspection: missing"),
        _gate("conditional", "salvage_or_rebuilt_title", "Salvage / rebuilt title"),
        _gate("conditional", "critical_reservation:rear_structure", "Rear structure: strong reservations"),
        _gate("conditional", "major_service_claimed_undocumented", "Major service claimed but not documented"),
        _gate("conditional", "stale_listing", "Listed about 14 months ago; confirm it is still available"),
        _gate("hard", "seller_refuses_vin_or_ppi", "nope"),   # not conditional: ignored
    ]
    c = classify_conditionals(gates, "listing")
    assert {i["key"] for i in c["document"]} == {"cooling_history", "major_service_claimed_undocumented", "stale_listing"}
    assert {i["key"] for i in c["inspection"]} == {"rear_structure"}
    assert c["observed"] == ["Salvage / rebuilt title", "Rear structure: strong reservations"]


def test_classify_conditionals_docs_stage_moves_document_items_only():
    gates = [_gate("conditional", "critical_missing:cooling_history", "Cooling-system receipts: missing"),
             _gate("conditional", "critical_missing:rear_structure", "Rear structure inspection: missing")]
    c = classify_conditionals(gates, "docs")
    assert c["document"] == [] and "Cooling-system receipts: missing" in c["observed"]
    assert {i["key"] for i in c["inspection"]} == {"rear_structure"}   # inspection-only stays open at docs


def test_classify_conditionals_ppi_stage_moves_everything():
    gates = [_gate("conditional", "critical_missing:cooling_history", "Cooling-system receipts: missing"),
             _gate("conditional", "critical_missing:rear_structure", "Rear structure inspection: missing")]
    c = classify_conditionals(gates, "ppi")
    assert c["document"] == [] and c["inspection"] == [] and len(c["observed"]) == 2


def test_classify_conditionals_status_claimed_only_vs_missing():
    gates = [_gate("conditional", "critical_missing:cooling_history", "Cooling-system receipts: seller assurance only"),
             _gate("conditional", "critical_missing:rear_structure", "Rear structure inspection: missing")]
    by_key = {i["key"]: i["status"] for i in classify_conditionals(gates, "listing")["document"] + classify_conditionals(gates, "listing")["inspection"]}
    assert by_key["cooling_history"] == "claimed_only" and by_key["rear_structure"] == "missing"


# ---------- upside ----------

def test_compute_upside_document_gain_capped_by_headroom():
    score = _score(total=77, doc=20, cond=21)
    classified = {"document": [{"key": "cooling_history", "label": "x", "status": "missing"}], "inspection": [], "observed": []}
    assert compute_upside(score, classified) == 77 + 5   # headroom 5, 5*1 item = 5
    score2 = _score(total=81, doc=24, cond=21)
    assert compute_upside(score2, classified) == 81 + 1   # headroom-limited to 1


def test_compute_upside_bonus_for_major_service_or_accident():
    score = _score(total=71, doc=10, cond=25)
    classified = {"document": [{"key": "major_service_claimed_undocumented", "label": "x", "status": "open"}], "inspection": [], "observed": []}
    assert compute_upside(score, classified) == 71 + 10   # raw 5*1 + 5 bonus = 10, headroom 15


def test_compute_upside_condition_gain_from_inspection_items():
    score = _score(total=71, doc=25, cond=10)
    classified = {"document": [], "inspection": [{"key": "rear_structure", "label": "x", "status": "missing"}], "observed": []}
    assert compute_upside(score, classified) == 71 + 4


def test_compute_upside_capped_at_100():
    score = _score(total=100, doc=25, cond=25, val=15, fit=15, log=10, emo=10)
    classified = {"document": [{"key": "cooling_history", "label": "x", "status": "missing"}],
                  "inspection": [{"key": "rear_structure", "label": "x", "status": "missing"}], "observed": []}
    assert compute_upside(score, classified) == 100


# ---------- verdict paths ----------

def test_verdict_open_question_high_upside_pursues_conditionally_despite_low_confidence():
    gates = [_gate("conditional", "critical_missing:cooling_history", "Cooling-system receipts: missing"),
             _gate("conditional", "critical_missing:rear_structure", "Rear structure inspection: missing")]
    score = _score(total=70, doc=15, cond=15)   # upside = 70 + min(10,5) + min(10,4) = 79
    verdict, reason = verdict_from(score, 30, gates, stage="listing")
    assert verdict == "Pursue conditionally"
    assert "confidence" not in reason.lower()   # the low-confidence cap does not apply on this path


def test_verdict_same_open_question_at_ppi_stage_is_maybe():
    gates = [_gate("conditional", "critical_missing:cooling_history", "Cooling-system receipts: missing"),
             _gate("conditional", "critical_missing:rear_structure", "Rear structure inspection: missing")]
    score = _score(total=70, doc=15, cond=15)
    verdict, _ = verdict_from(score, 30, gates, stage="ppi")
    assert verdict == "Maybe / verify"


def test_verdict_observed_conditional_always_caps_at_maybe():
    gates = [_gate("conditional", "salvage_or_rebuilt_title", "Salvage / rebuilt title")]
    score = _score(total=90, doc=22, cond=22)
    verdict, _ = verdict_from(score, 90, gates, stage="listing")
    assert verdict == "Maybe / verify"


def test_verdict_hard_gate_rejects_and_priority_is_zero():
    gates = [_gate("hard", "seller_refuses_vin_or_ppi", "Seller refuses VIN or PPI")]
    score = _score(total=90)
    verdict, reason = verdict_from(score, 90, gates, stage="listing")
    assert verdict == "Reject"
    classified = classify_conditionals(gates, "listing")
    upside = compute_upside(score, classified)
    assert compute_priority(score, upside, gates, classified, _costs()) == 0


# ---------- priority ----------

def test_priority_base_formula():
    score = _score(total=70)
    classified = {"document": [], "inspection": [], "observed": []}
    assert compute_priority(score, 90, [], classified, _costs()) == round(0.6 * 70 + 0.4 * 90)


def test_priority_penalizes_observed_conditionals():
    score = _score(total=70)
    gates = [_gate("conditional", "salvage_or_rebuilt_title", "Salvage / rebuilt title")]
    classified = classify_conditionals(gates, "listing")
    assert compute_priority(score, 70, gates, classified, _costs()) == round(0.6 * 70 + 0.4 * 70) - 10


def test_priority_penalizes_unpriced_or_expected_hammer_price_basis():
    score = _score(total=70)
    classified = {"document": [], "inspection": [], "observed": []}
    base = round(0.6 * 70 + 0.4 * 70)
    assert compute_priority(score, 70, [], classified, _costs("unpriced")) == base - 8
    assert compute_priority(score, 70, [], classified, _costs("expected_hammer")) == base - 8
    assert compute_priority(score, 70, [], classified, _costs("asking")) == base


def test_priority_penalizes_stale_listing_gate():
    score = _score(total=70)
    gates = [_gate("conditional", "stale_listing", "stale")]
    classified = classify_conditionals(gates, "listing")
    upside = compute_upside(score, classified)
    base = round(0.6 * 70 + 0.4 * upside)
    assert compute_priority(score, upside, gates, classified, _costs()) == base - 5


def test_priority_clamped_to_0_100():
    score = _score(total=0, doc=0, cond=0, val=0, fit=0, log=0, emo=0)
    classified = {"document": [], "inspection": [], "observed": ["a"] * 11}   # far below zero before clamping
    assert compute_priority(score, 0, [], classified, _costs()) == 0


# ---------- next_steps ----------

def test_next_steps_vin_missing_comes_first():
    steps = compute_next_steps({"vin": None}, {"document": [], "inspection": [], "observed": []}, "listing", _ev())
    assert steps == ["Ask for the VIN"]


def test_next_steps_request_records_at_listing_stage():
    classified = {"document": [{"key": "cooling_history", "label": "Cooling-system receipts", "status": "missing"}],
                  "inspection": [], "observed": []}
    steps = compute_next_steps({"vin": "X"}, classified, "listing", _ev())
    assert steps == ["Request records: Cooling-system receipts"]


def test_next_steps_includes_the_first_seller_question():
    classified = {"document": [], "inspection": [], "observed": []}
    steps = compute_next_steps({"vin": "X"}, classified, "listing",
                               _ev(seller_questions=["Do you have the timing-belt invoice?", "Any accidents?"]))
    assert steps == ["Do you have the timing-belt invoice?"]


def test_next_steps_book_ppi_focused_on_open_inspection_items_at_docs_stage():
    classified = {"document": [], "inspection": [{"key": "rear_structure", "label": "Rear structure inspection", "status": "missing"}],
                  "observed": []}
    steps = compute_next_steps({"vin": "X"}, classified, "docs", _ev())
    assert steps == ["Book a PPI focused on: Rear structure inspection"]


def test_next_steps_never_exceeds_three():
    classified = {"document": [{"key": "a", "label": "A", "status": "missing"}],
                  "inspection": [{"key": "b", "label": "B", "status": "missing"}], "observed": []}
    steps = compute_next_steps({"vin": None}, classified, "docs", _ev(seller_questions=["Q1?"]))
    assert len(steps) <= 3


# ---------- assess() wiring ----------

def test_assess_sets_stage_upside_priority_open_questions_next_steps():
    ev = _ev(doc=9, cond=9, val=9, fit=9, log=10, emo=9, quality=8,
             critical={"rear_structure": "satisfied", "cooling_history": "claimed_only"})
    a = assess(_listing(), _profile("z3_30i"), ev, STATE, stage="listing")
    assert a.policy_version == POLICY_VERSION
    assert a.stage == "listing"
    assert a.upside is not None and a.upside >= a.score.total
    assert a.priority is not None and 0 <= a.priority <= 100
    assert a.open_questions["document"] and a.open_questions["document"][0]["key"] == "cooling_history"
    assert a.next_steps and "Request records" in a.next_steps[0]


# ---------- server: stage stays fresh, and the published index carries it ----------

def test_patch_status_rederives_assessment_with_new_stage():
    with TestClient(app) as c:
        c.post("/api/ingest", json={"site": "facebook", "items": [
            {"url": "https://www.facebook.com/marketplace/item/stage/", "title": "2001 BMW Z3 3.0i",
             "price_text": "$12,000", "detail": {"text": "t" * 900}}]})
        lid = db.get_listing_by_url("https://www.facebook.com/marketplace/item/stage/")["id"]
        db.update_listing(lid, {"profile_key": "z3_30i"})
        db.add_assessment(lid, {"policy_version": "1.3.0", "mission": "enthusiast_bridge", "model": "test",
                                "assessed_at": "2026-09-08T00:00:00+00:00", "stage": "listing", "verdict": "Maybe / verify",
                                "evidence": {"ratings": {k: {"rating": 8, "rationale": ""} for k in
                                             ("documentation", "condition", "price_value", "mission_fit", "logistics", "emotional_spec_fit")},
                                             "evidence_quality": 7, "immediate_service_estimate": {"low": 0, "high": 0},
                                             "critical_evidence": [{"key": "cooling_history", "status": "missing", "evidence": "", "source": "ai_inference"}],
                                             "seller_questions": []}})
        assert db.latest_assessment(lid)["stage"] == "listing"
        r = c.patch(f"/api/listings/{lid}", json={"status": "Contacted"})
        assert r.status_code == 200
        after = db.latest_assessment(lid)
        assert after["stage"] == "questions" and after["policy_version"] == POLICY_VERSION


def test_document_attach_and_delete_rederive_stage():
    with TestClient(app) as c:
        c.post("/api/ingest", json={"site": "facebook", "items": [
            {"url": "https://www.facebook.com/marketplace/item/stagedoc/", "title": "2001 BMW Z3 3.0i",
             "price_text": "$12,000", "detail": {"text": "t" * 900}}]})
        lid = db.get_listing_by_url("https://www.facebook.com/marketplace/item/stagedoc/")["id"]
        db.update_listing(lid, {"profile_key": "z3_30i"})
        db.add_assessment(lid, {"policy_version": "1.3.0", "mission": "enthusiast_bridge", "model": "test",
                                "assessed_at": "2026-09-08T00:00:00+00:00", "stage": "listing", "verdict": "Maybe / verify",
                                "evidence": {"ratings": {k: {"rating": 8, "rationale": ""} for k in
                                             ("documentation", "condition", "price_value", "mission_fit", "logistics", "emotional_spec_fit")},
                                             "evidence_quality": 7, "immediate_service_estimate": {"low": 0, "high": 0},
                                             "critical_evidence": [], "seller_questions": []}})
        r = c.post(f"/api/listings/{lid}/documents", json={"kind": "carfax", "text": "clean history"})
        doc_id = r.json()["document_id"]
        assert db.latest_assessment(lid)["stage"] == "docs"
        c.delete(f"/api/documents/{doc_id}")
        assert db.latest_assessment(lid)["stage"] == "listing"


def test_export_index_includes_stage_upside_priority_detail_has_open_questions():
    from scout.publish import build_export, split_export
    ingest_items("facebook", [{"url": "https://www.facebook.com/marketplace/item/idx/", "title": "2001 BMW Z3",
                               "price_text": "$12,000", "detail": {"text": "t" * 900}}], run_ai=False)
    lid = db.get_listing_by_url("https://www.facebook.com/marketplace/item/idx/")["id"]
    db.update_listing(lid, {"profile_key": "z3_30i", "role": "candidate"})
    db.add_assessment(lid, {"policy_version": POLICY_VERSION, "mission": "enthusiast_bridge", "model": "test",
                            "assessed_at": "2026-09-08T00:00:00+00:00", "verdict": "Pursue conditionally",
                            "stage": "listing", "upside": 88, "priority": 72,
                            "open_questions": {"document": [], "inspection": [], "observed": []},
                            "next_steps": ["Ask for the VIN"], "score": {"total": 70}, "evidence": {}})
    index, details = split_export(build_export())
    row = next(l for l in index["listings"] if l["id"] == lid)
    assert row["assessment"]["stage"] == "listing" and row["assessment"]["upside"] == 88 and row["assessment"]["priority"] == 72
    assert "open_questions" not in row["assessment"] and "next_steps" not in row["assessment"]
    assert details[lid]["assessment"]["open_questions"] == {"document": [], "inspection": [], "observed": []}
    assert details[lid]["assessment"]["next_steps"] == ["Ask for the VIN"]
