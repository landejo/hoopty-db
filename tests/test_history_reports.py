"""Free history reports fetched by the extension (/api/history-reports*), and the
stage rule that a document only counts once an assessment has read it."""
from fastapi.testclient import TestClient

from scout import db, server
from scout.config import CONFIG
from scout.server import app
from scout.stage import documents_read_by

URL = "https://www.autotrader.com/cars-for-sale/vehicle/793102614"
VIN = "WBACN53401LJ58815"
REPORT = f"Experian AutoCheck Report\nVIN:\n{VIN}\nNo Accidents or Damage Reported\n" + "history line\n" * 80


def _listing(c, vin=VIN, assessed=True):
    c.post("/api/ingest", json={"site": "autotrader", "items": [
        {"url": URL, "title": "2001 BMW Z3 3.0i", "price_text": "$20,389", "detail": {"text": "t" * 900}}]})
    lid = db.get_listing_by_url(URL)["id"]
    db.update_listing(lid, {"profile_key": "z3_30i", "vin": vin, "role": "candidate", "availability": "active"})
    if assessed:
        db.add_assessment(lid, {"policy_version": "1.3.0", "mission": "enthusiast_bridge", "model": "test",
                                "assessed_at": "2026-09-08T00:00:00+00:00", "stage": "listing", "verdict": "Maybe / verify",
                                "evidence": {"ratings": {k: {"rating": 6, "rationale": ""} for k in
                                             ("documentation", "condition", "price_value", "mission_fit", "logistics", "emotional_spec_fit")},
                                             "evidence_quality": 5, "immediate_service_estimate": {"low": 0, "high": 0},
                                             "critical_evidence": [{"key": "cooling_history", "status": "missing", "evidence": "", "source": "ai_inference"}],
                                             "seller_questions": []}})
    return lid


# ---------- documents_read_by ----------

def test_documents_read_by_keeps_only_documents_attached_before_the_assessment():
    docs = [{"kind": "carfax", "created_at": "2026-10-01T00:00:00+00:00"},
            {"kind": "autocheck", "created_at": "2026-10-23T17:00:00+00:00"}]
    read = documents_read_by({"assessed_at": "2026-10-23T16:00:00+00:00"}, docs)
    assert [d["kind"] for d in read] == ["carfax"]


def test_documents_read_by_counts_a_document_attached_the_same_second():
    docs = [{"kind": "carfax", "created_at": "2026-10-23T16:00:00+00:00"}]
    assert documents_read_by({"assessed_at": "2026-10-23T16:00:00+00:00"}, docs) == docs


def test_documents_read_by_without_a_timestamp_keeps_everything():
    docs = [{"kind": "carfax", "created_at": "2026-10-23T17:00:00+00:00"}]
    assert documents_read_by({}, docs) == docs


def test_a_report_attached_after_the_assessment_does_not_flip_the_verdict():
    """The #637 regression: a clean Carfax moved the stage to "docs", which turned the
    old assessment's "missing" cooling item into an observed gap and a Reject."""
    with TestClient(app) as c:
        lid = _listing(c)
        c.post(f"/api/listings/{lid}/documents", json={"kind": "carfax", "text": "clean history"})
        after = db.latest_assessment(lid)
        assert after["stage"] == "listing"
        oq = after["open_questions"]
        assert "cooling_history" in {i["key"] for i in oq["document"]}   # still an open question to ask,
        assert not any("ooling" in o for o in oq["observed"])             # not an observed gap


# ---------- /api/history-reports/wanted ----------

def test_wanted_lists_reports_the_listing_lacks():
    with TestClient(app) as c:
        _listing(c)
        r = c.post("/api/history-reports/wanted", json={"items": [
            {"listing_url": URL, "kind": "autocheck"},
            {"listing_url": URL, "kind": "invoice"},                                   # not a history report
            {"listing_url": "https://www.autotrader.com/cars-for-sale/vehicle/1", "kind": "carfax"}]})   # unknown car
        assert r.json() == {"wanted": [{"listing_url": URL, "kind": "autocheck"}]}


def test_wanted_skips_a_kind_already_attached():
    with TestClient(app) as c:
        lid = _listing(c)
        db.add_document(lid, "autocheck", REPORT)
        r = c.post("/api/history-reports/wanted", json={"items": [
            {"listing_url": URL, "kind": "autocheck"}, {"listing_url": URL, "kind": "carfax"}]})
        assert r.json() == {"wanted": [{"listing_url": URL, "kind": "carfax"}]}


# ---------- /api/history-reports ----------

def test_attach_saves_the_report_without_ai():
    with TestClient(app) as c:
        lid = _listing(c)
        r = c.post("/api/history-reports", json={"listing_url": URL, "kind": "autocheck", "text": REPORT,
                                                 "url": URL + "/experian?x=1"})
        body = r.json()
        assert r.status_code == 200 and body["reassess_queued"] is False   # no API key in tests
        docs = db.list_documents(lid)
        assert [(d["kind"], d["source"]) for d in docs] == [("autocheck", "auto-fetched free report")]
        assert docs[0]["vin"] == VIN


def test_attach_never_replaces_an_existing_report():
    with TestClient(app) as c:
        lid = _listing(c)
        db.add_document(lid, "autocheck", REPORT + "the one Jason attached")
        r = c.post("/api/history-reports", json={"listing_url": URL, "kind": "autocheck", "text": REPORT})
        assert "skipped" in r.json()
        assert db.list_documents(lid)[0]["text"].endswith("the one Jason attached")


def test_attach_rejects_bad_input():
    with TestClient(app) as c:
        _listing(c)
        post = lambda **kw: c.post("/api/history-reports", json={"listing_url": URL, "kind": "autocheck", "text": REPORT} | kw)
        assert post(listing_url="https://www.autotrader.com/cars-for-sale/vehicle/1").status_code == 404
        assert post(kind="invoice").status_code == 400
        assert post(text="Loading…").status_code == 422                                   # the report never rendered
        assert post(text=REPORT.replace(VIN, "WBACN53491LJ59574")).status_code == 422     # another car's report


def test_attach_accepts_a_report_when_the_listing_has_no_vin():
    with TestClient(app) as c:
        _listing(c, vin=None)
        r = c.post("/api/history-reports", json={"listing_url": URL, "kind": "autocheck", "text": REPORT})
        assert r.status_code == 200 and r.json()["document_id"]


def test_attach_queues_a_reassessment_for_an_assessed_candidate(monkeypatch):
    calls = []

    async def fake_assess(listing_id, tier="full"):
        calls.append(listing_id)

    monkeypatch.setattr(server, "assess_listing", fake_assess)
    with TestClient(app) as c:
        lid = _listing(c)   # before the key: with one, ingest would queue an AI read
        monkeypatch.setattr(CONFIG, "anthropic_api_key", "test-key")
        r = c.post("/api/history-reports", json={"listing_url": URL, "kind": "autocheck", "text": REPORT})
        assert r.json()["reassess_queued"] is True
    assert calls == [lid]


def test_attach_does_not_reassess_a_car_never_assessed(monkeypatch):
    calls = []

    async def fake_assess(listing_id, tier="full"):
        calls.append(listing_id)

    monkeypatch.setattr(server, "assess_listing", fake_assess)
    with TestClient(app) as c:
        _listing(c, assessed=False)
        monkeypatch.setattr(CONFIG, "anthropic_api_key", "test-key")
        r = c.post("/api/history-reports", json={"listing_url": URL, "kind": "autocheck", "text": REPORT})
        assert r.json()["reassess_queued"] is False
    assert calls == []
