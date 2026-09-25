"""Availability check (sold / delisted / ended detection), the 1.7.0 gate,
re-assess-top, auto-publish sittings, and the ingest bug fixes of 2026-09-24.
No paid AI: the one model path is stubbed."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from scout import autopublish, availability, db
from scout.ingest import detect_availability, ingest_items


def _add(url, site="facebook", price="$15,000", text="2001 BMW Z3 3.0i roadster for sale " * 30, **extra):
    ingest_items(site, [{"url": url, "title": "2001 BMW Z3 3.0i", "price_text": price, "card_text": "2001 BMW Z3\n" + price,
                         "detail": {"text": text, "photos": []}}], run_ai=False)
    row = db.get_listing_by_url(url)
    if extra:
        db.update_listing(row["id"], extra)
    return db.get_listing(row["id"])


# ---------- classification ----------

def test_deterministic_signals():
    c = availability.classify_deterministic
    assert c("bat", {"text": "Sold for USD $31,500 on 9/3/26"})["availability"] == "sold"
    assert c("bat", {"text": "Sold for USD $31,500 on 9/3/26"})["price"] == 31500
    ended = c("carsandbids", {"text": "Reserve not met, bid to $22,000"})
    assert ended["availability"] == "ended" and ended["price"] == 22000
    assert c("facebook", {"text": "2001 BMW Z3\nSold\n$15,000\nListed 3 weeks ago"})["availability"] == "sold"
    assert c("cargurus", {"text": "This listing is no longer available.\nSimilar cars"})["availability"] == "unavailable"
    assert c("facebook", {"text": "This listing isn't available anymore"})["availability"] == "unavailable"
    # CarGurus' real sold page, captured in the 2026-09-24 E2E run
    assert c("cargurus", {"text": "All results\nLooks like that one got away\nSimilar cars to consider\n2023 MINI Cooper"})["availability"] == "unavailable"
    assert c("bat", {"text": "Current Bid: $20,000\nTime Left 2 days"})["availability"] == "active"


def test_boilerplate_sold_is_not_a_sale():
    c = availability.classify_deterministic
    assert c("cargurus", {"text": "2001 BMW Z3 $15,000\nSimilar cars sold nearby\nSold by Carmel Motors"}) is None
    assert c("facebook", {"text": "Mark as sold\nMessage seller"}) is None


def test_model_answer_needs_a_verbatim_quote(monkeypatch):
    from scout.config import CONFIG
    monkeypatch.setattr(CONFIG, "anthropic_api_key", "test")
    import scout.ai as ai
    listing = {"id": 1, "site": "facebook", "url": "u", "title": "t"}
    page = {"text": "2001 BMW Z3 3.0i roadster, 88k miles, Carmel CA. " * 3 + "Seller says: car found a new home last weekend, thanks all", "is_detail_page": True}
    monkeypatch.setattr(ai, "call_text", lambda *a, **k: '{"availability": "sold", "evidence": "car found a new home", "sold_price": null}')
    assert availability.classify(listing, page)["availability"] == "sold"
    monkeypatch.setattr(ai, "call_text", lambda *a, **k: '{"availability": "sold", "evidence": "This car was sold", "sold_price": null}')
    assert availability.classify(listing, page)["availability"] == "unclear"


def test_unloaded_or_blocked_page_changes_nothing():
    row = _add("https://www.facebook.com/marketplace/item/1/")
    assert availability.apply(row["id"], {"error": "no response"})["changed"] is False
    assert availability.apply(row["id"], {"blocked": True})["changed"] is False
    after = db.get_listing(row["id"])
    assert after["availability"] == "active" and after["role"] == "candidate"
    assert after["raw"]["availability_check"]["result"] == "unclear"


# ---------- apply ----------

def test_sold_listing_becomes_a_comp_with_status_sold():
    row = _add("https://www.facebook.com/marketplace/item/2/", status="Verify")
    r = availability.apply(row["id"], {"text": "2001 BMW Z3\nSold\n$15,000"})
    assert r["changed"]
    after = db.get_listing(row["id"])
    assert (after["availability"], after["role"], after["status"]) == ("sold", "comp", "Sold")
    assert after["raw"]["availability_check"]["sale_evidence"] == "explicit"
    from scout.market import is_sale
    assert is_sale(after)          # counts in fair value from now on


def test_auction_result_price_and_ended_status():
    row = _add("https://bringatrailer.com/listing/a/", site="bat", price="$20,000")
    availability.apply(row["id"], {"text": "Bid to USD $24,000 on 9/20/26"})
    after = db.get_listing(row["id"])
    assert (after["availability"], after["status"], after["price"], after["price_kind"]) == ("ended", "Ended", 24000, "reserve_not_met")
    row = _add("https://bringatrailer.com/listing/b/", site="bat", price="$20,000")
    availability.apply(row["id"], {"text": "Sold for USD $26,500 on 9/20/26"})
    assert db.get_listing(row["id"])["sold_price"] == 26500


def test_purchased_status_is_never_overwritten():
    row = _add("https://www.facebook.com/marketplace/item/3/", status="Purchased")
    availability.apply(row["id"], {"text": "This listing is no longer available"})
    after = db.get_listing(row["id"])
    assert after["status"] == "Purchased" and after["role"] == "comp"


def test_empty_or_blocked_page_is_unclear_not_active():
    listing = {"id": 1, "site": "cargurus", "url": "u", "title": "t"}
    assert availability.classify(listing, {"text": "", "is_detail_page": True})["availability"] == "unclear"
    blocked = {"text": "Sorry, you have been blocked You are unable to access cf-platform-prod.cars.com"}
    assert availability.classify({**listing, "site": "carscom"}, blocked)["availability"] == "unclear"


def test_block_pages_from_the_e2e_runs_are_unclear():
    L = lambda site: {"id": 1, "site": site, "url": "u", "title": "t"}
    akamai = {"text": "We're sorry for any inconvenience, but the site is currently unavailable. Please contact our support team for help. Incident Number: 18.4d94d817"}
    datadome = {"text": "var dd={'rt':'c','cid':'AHrlqAAAAAMANflG6vtORAwAR8yTyg==','hsh':'C3D682D3F2321D709B3DA56E04E573','t':'fe','qp':'','s':42811}" * 2}
    assert availability.classify(L("autotrader"), akamai)["availability"] == "unclear"
    assert availability.classify(L("cargurus"), datadome)["availability"] == "unclear"
    # a readable page with no marker either way, on a site whose live marker we know
    assert availability.classify(L("cargurus"), {"text": "2018 MINI Cooper S $17,836 Price includes fees " * 5})["availability"] == "unclear"


def test_live_listing_markers_from_real_pages():
    c = availability.classify_deterministic
    assert c("autotrader", {"text": "Used 1999 BMW Z3 M Roadster\nListing Price\n$24,990\nMake Offer"})["availability"] == "active"
    assert c("cargurus", {"text": "2007 Porsche Cayman\n$35,075\nCheck availability\nAll results"})["availability"] == "active"
    assert c("facebook", {"text": "2008 Lexus gx470\n$15,000\nListed 3 weeks ago\nMessage\nAbout this vehicle"})["availability"] == "active"
    # a sold Facebook page (real, #67) has no Message button and is caught first anyway
    assert c("facebook", {"text": "Sold\n · 2000 BMW z3 Coupe 2D\n$16,000\nListed 15 weeks ago\nSave\nShare"})["availability"] == "sold"


def test_a_page_with_no_notice_never_rescues_a_comp():
    row = _add("https://www.cargurus.com/details/1", site="cargurus", role="comp")
    availability.apply(row["id"], {"text": "2018 MINI Cooper S\n$17,836\nPrice includes fees " * 5})
    assert db.get_listing(row["id"])["role"] == "comp"


def test_live_page_rescues_a_misread_comp_but_not_a_user_set_one():
    row = _add("https://bringatrailer.com/listing/c/", site="bat", role="comp")
    availability.apply(row["id"], {"text": "Current Bid: $30,000\nTime Left 4 days"})
    assert db.get_listing(row["id"])["role"] == "candidate"
    row = _add("https://bringatrailer.com/listing/d/", site="bat", role="comp", role_user_set=1)
    availability.apply(row["id"], {"text": "Current Bid: $30,000\nTime Left 4 days"})
    assert db.get_listing(row["id"])["role"] == "comp"


def test_targets_live_and_vanished_least_recently_checked_first():
    a = _add("https://www.facebook.com/marketplace/item/10/")
    b = _add("https://www.facebook.com/marketplace/item/11/", availability="removed")
    _add("https://www.facebook.com/marketplace/item/12/", availability="sold", role="comp")
    availability.apply(a["id"], {"text": "2001 BMW Z3 for sale, message seller " * 5})
    ids = [t["id"] for t in availability.targets()]
    assert ids == [b["id"], a["id"]]


def test_sold_gate_turns_the_verdict_to_do_not_pursue():
    from scout.policy.gates import evaluate_gates
    from tests.test_policy import _ev
    ev = _ev()
    gates = evaluate_gates({"availability": "sold", "raw": {}}, {}, ev, "enthusiast_bridge", {})
    assert any(g.key == "no_longer_available" and g.kind == "strategy" for g in gates)
    assert not any(g.key == "no_longer_available" for g in evaluate_gates({"availability": "active"}, {}, ev, "enthusiast_bridge", {}))


# ---------- endpoints ----------

def test_availability_endpoints_round_trip():
    from scout.server import app
    row = _add("https://www.facebook.com/marketplace/item/20/")
    with TestClient(app) as c:
        t = c.post("/api/availability/start").json()["targets"]
        assert [x["id"] for x in t] == [row["id"]]
        assert c.get("/api/task").json()["active"] is True
        r = c.post("/api/availability/results", json={"results": [{"id": row["id"], "detail": {"text": "Sold\n$15,000"}}]}).json()
        assert r["results"][0]["changed"]
        fin = c.post("/api/availability/finish").json()
        assert "1 changed" in fin["summary"]
        assert c.get("/api/task").json()["active"] is False
    assert db.get_listing(row["id"])["status"] == "Sold"


def test_reassess_requires_a_key_and_skips_non_candidates(monkeypatch):
    from scout import server
    from scout.config import CONFIG
    with TestClient(server.app) as c:
        assert c.post("/api/reassess", json={"ids": [1]}).status_code == 400
    monkeypatch.setattr(CONFIG, "anthropic_api_key", "test")
    live = _add("https://www.facebook.com/marketplace/item/30/", profile_key="z3_30i")
    comp = _add("https://www.facebook.com/marketplace/item/31/", role="comp", availability="sold", profile_key="z3_30i")
    seen = []

    async def fake_assess(lid, tier="full"):
        seen.append((lid, tier))
        return {"ok": True}
    monkeypatch.setattr(server, "assess_listing", fake_assess)
    with TestClient(server.app) as c:
        r = c.post("/api/reassess", json={"ids": [live["id"], comp["id"]]}).json()
    assert seen == [(live["id"], "top")] and r["skipped"] == [comp["id"]] and r["model"] == CONFIG.model_top


def test_manual_role_survives_a_sync():
    from scout.server import app
    url = "https://www.cargurus.com/details/99"
    row = _add(url, site="cargurus")
    with TestClient(app) as c:
        c.patch(f"/api/listings/{row['id']}", json={"role": "comp"})
    ingest_items("cargurus", [{"url": url, "price_text": "$15,000", "card_text": "x", "detail": {"text": "2001 BMW Z3 " * 60}}], run_ai=False)
    assert db.get_listing(row["id"])["role"] == "comp"


# ---------- ingest bug fixes ----------

def test_renormalize_keeps_availability_and_raw():
    from scout.server import app
    row = _add("https://bringatrailer.com/listing/e/", site="bat", availability="ended", role="comp")
    db.update_listing(row["id"], {"raw": {"time_left": "ended", "availability_check": {"result": "ended"}}})
    with TestClient(app) as c:
        assert c.post(f"/api/listings/{row['id']}/renormalize").status_code == 200
    after = db.get_listing(row["id"])
    assert after["availability"] == "ended" and after["raw"]["availability_check"]["result"] == "ended"
    removed = _add("https://www.facebook.com/marketplace/item/40/", availability="removed")
    with TestClient(app) as c:
        c.post(f"/api/listings/{removed['id']}/renormalize")
    assert db.get_listing(removed["id"])["availability"] == "removed"


def test_card_only_sync_keeps_the_full_page_text():
    url = "https://www.facebook.com/marketplace/item/50/"
    full = "Full description of the car. " * 100
    _add(url, text=full)
    ingest_items("facebook", [{"url": url, "price_text": "$15,000", "card_text": "2001 BMW Z3\n$15,000"}], run_ai=False)
    assert db.get_listing_by_url(url)["raw_text"] == full[:120_000]


def test_bare_sold_in_page_text_is_not_a_sale():
    page = {"status_text": "2000 BMW M Coupe\nCurrent Bid $30,000\nRecently sold: 1999 M Coupe"}
    assert detect_availability({"price_text": "$30,000", "detail": page}, "bat") == "active"
    assert detect_availability({"detail": {"status_text": "Sold\nrest of page"}}, "facebook") == "sold"
    assert detect_availability({"detail": {"status_text": "This vehicle has been sold"}}, "carscom") == "sold"


# ---------- auto-publish sittings ----------

def test_sitting_publishes_after_idle_only_when_something_changed(monkeypatch):
    monkeypatch.setenv("SCOUT_AUTOPUBLISH_IDLE_MIN", "15")
    monkeypatch.setenv("SCOUT_AUTOPUBLISH_CHECKPOINT_MIN", "45")
    t0 = datetime(2026, 9, 24, 18, 0, tzinfo=timezone.utc)
    autopublish.note_activity(changed=False, now=t0)
    assert autopublish.due(t0 + timedelta(minutes=30)) is None           # browsing only
    autopublish.note_activity(changed=True, now=t0 + timedelta(minutes=5))
    assert autopublish.due(t0 + timedelta(minutes=10)) is None           # still in the sitting
    assert autopublish.due(t0 + timedelta(minutes=21)) == "sitting ended"
    autopublish.mark_published({"ok": True, "changed": True}, "sitting ended")
    assert autopublish.status()["pending_changes"] == 0


def test_long_sitting_gets_a_checkpoint(monkeypatch):
    monkeypatch.setenv("SCOUT_AUTOPUBLISH_IDLE_MIN", "15")
    monkeypatch.setenv("SCOUT_AUTOPUBLISH_CHECKPOINT_MIN", "45")
    t0 = datetime(2026, 9, 24, 18, 0, tzinfo=timezone.utc)
    for m in range(0, 50, 5):
        autopublish.note_activity(changed=True, now=t0 + timedelta(minutes=m))
    assert autopublish.due(t0 + timedelta(minutes=46)) == "checkpoint during a long sitting"


def test_pending_changes_survive_a_restart():
    autopublish.note_activity(changed=True)
    autopublish.reset_for_tests()
    autopublish.restore()
    assert autopublish.status()["pending_changes"] == 1


def test_api_calls_count_as_activity():
    from scout.server import app
    row = _add("https://www.facebook.com/marketplace/item/60/")
    autopublish.reset_for_tests()
    with TestClient(app) as c:
        c.post("/api/activity")
        assert autopublish.status()["pending_changes"] == 0
        c.patch(f"/api/listings/{row['id']}", json={"notes": "call Tuesday"})
        assert c.get("/api/health").json()["autopublish"]["pending_changes"] == 1


def test_reassess_cycles_through_tiers_and_restarts_after_three_days(monkeypatch):
    from scout import server
    from scout.config import CONFIG
    monkeypatch.setattr(CONFIG, "anthropic_api_key", "test")
    ids = [_add(f"https://www.facebook.com/marketplace/item/7{i:02d}/", profile_key="z3_30i")["id"] for i in range(35)]
    seen = []

    async def fake_assess(lid, tier="full"):
        seen.append(lid)
        return {"ok": True}
    monkeypatch.setattr(server, "assess_listing", fake_assess)
    with TestClient(server.app) as c:
        r1 = c.post("/api/reassess", json={"ids": ids}).json()
        r2 = c.post("/api/reassess", json={"ids": ids}).json()
        r3 = c.post("/api/reassess", json={"ids": ids}).json()
        assert (r1["tier"], r2["tier"], r3["tier"]) == (1, 2, 3)
        assert r1["ids"] == ids[:15] and r2["ids"] == ids[15:30] and r3["ids"] == ids[30:]
        r4 = c.post("/api/reassess", json={"ids": ids}).json()      # everything done: new cycle
        assert r4["tier"] == 1 and r4["ids"] == ids[:15]
        # Three days after tier 1, the cycle restarts even mid-way.
        c.post("/api/reassess", json={"ids": ids})
        st = db.get_setting("reassess_cycle")
        st["started_at"] = (datetime.now(timezone.utc) - timedelta(days=3, minutes=1)).isoformat()
        db.set_setting("reassess_cycle", st)
        r6 = c.post("/api/reassess", json={"ids": ids}).json()
        assert r6["tier"] == 1 and r6["ids"] == ids[:15] and r6["cycle_restarted"]
        assert c.get("/api/reassess/cycle").json()["next_tier"] == 2


# ---------- market / publish fixes (2026-09-24, second pass) ----------

def test_percentile_and_market_stats_count_only_real_sales():
    from scout.scoring import market_stats
    comps = [{"availability": "sold", "price": 20000, "role": "comp"},
             {"availability": "ended", "price_kind": "reserve_not_met", "price": 9000, "role": "comp"},   # a high bid, not a sale
             {"availability": "active", "price": 40000, "role": "comp"}]                                  # a live comp's ask
    assert market_stats(comps, [])["sold_count"] == 1 and market_stats(comps, [])["sold_median"] == 20000


def test_recency_ignores_last_seen():
    from datetime import date
    from scout.market import _recency_filter
    old = [{"auction_end": "2019-05-01", "last_seen": "2026-09-24T00:00:00+00:00"} for _ in range(3)]
    recent = [{"listing_date": "2026-08-01"} for _ in range(4)]
    kept = _recency_filter(old + recent, date(2026, 9, 24))
    assert kept == recent


def test_published_index_carries_the_early_bid_flag():
    from scout.publish import _index_listing
    live = {"id": 1, "assessment": {"costs": {"price_basis": "expected_hammer"}, "score": {"total": 70}}}
    priced = {"id": 2, "assessment": {"costs": {"price_basis": "asking"}, "score": {"total": 70}}}
    assert _index_listing(live)["assessment"]["early_bid"] is True
    assert _index_listing(priced)["assessment"]["early_bid"] is False


# ---------- second bug pass ----------

def test_new_task_is_not_stalled_by_an_old_heartbeat():
    from scout import server
    server._task["heartbeat"] = "2020-01-01T00:00:00+00:00"
    tok = server._task_start("Re-assessing tier 1", 15)
    assert server.task_status()["active"] is True
    server._task_end("done", tok)


def test_new_sitting_is_not_a_long_sitting(monkeypatch):
    monkeypatch.setenv("SCOUT_AUTOPUBLISH_IDLE_MIN", "15")
    monkeypatch.setenv("SCOUT_AUTOPUBLISH_CHECKPOINT_MIN", "45")
    t0 = datetime(2026, 9, 23, 18, 0, tzinfo=timezone.utc)
    autopublish.note_activity(changed=True, now=t0)
    autopublish.mark_published({"ok": True, "changed": True}, "sitting ended")
    autopublish._state["last_attempt"] = t0                 # yesterday's publish
    t1 = t0 + timedelta(days=1)
    autopublish.note_activity(changed=True, now=t1)          # first edit of today's sitting
    assert autopublish.due(t1 + timedelta(seconds=30)) is None


def test_edits_during_a_publish_stay_pending():
    autopublish.note_activity(changed=True)
    covered = autopublish.set_publishing(True)
    autopublish.note_activity(changed=True)                  # edited while git ran
    autopublish.set_publishing(False)
    autopublish.mark_published({"ok": True, "changed": True}, "manual", covered=covered)
    assert autopublish.status()["pending_changes"] == 1


def test_auction_end_uses_pacific_standard_time_in_winter():
    from scout.scoring import auction_hours_left
    l = {"site": "bat", "availability": "active", "auction_end": "2026-12-15T12:00:00"}
    now = datetime(2026, 12, 15, 19, 0, tzinfo=timezone.utc)          # 11:00 PST
    assert round(auction_hours_left(l, now), 2) == 1.0


def test_hand_set_roles_are_backfilled_from_the_event_log():
    row = _add("https://www.cargurus.com/details/77", site="cargurus")
    db.update_listing(row["id"], {"role": "comp", "role_user_set": 0})
    db.log_event("edit", row["id"], "{'role': 'comp'}")
    db.init_db()
    assert db.get_listing(row["id"])["role_user_set"] == 1


# ---------- policy 1.8.0: walk-away, budgets per mission, merit, next step ----------

def _costs18(price, max_price, basis="asking"):
    from scout.policy.schema import CostBreakdown
    return CostBreakdown(price_basis=basis, price=price, buyer_fee=0, transport=0, immediate_service_low=0,
                         immediate_service_high=0, overdue_allowance=0, risk_reserve=0, tax_and_registration=0,
                         all_in_low=price, all_in_high=price, max_price=max_price, offer_low=0, offer_high=0)


def test_walkaway_is_the_lower_of_budget_and_value():
    from scout.policy.costs import compute_costs
    from scout.policy.state import DEFAULT_STATE
    from tests.test_policy import _ev
    listing = {"site": "facebook", "price": 16995, "location": "Carmel, CA", "year": 2008, "mileage": 221000}
    fair = {"mid": 11890, "low": 10668, "high": 12646, "n": 5, "basis": "sold", "relaxed": []}
    c = compute_costs(listing, {"risk_reserve": 1500}, _ev(), [], DEFAULT_STATE, fair, "utility_capability")
    assert c.max_price_basis == "value" and c.max_price == c.max_price_value == 12646
    assert c.max_price_budget and c.max_price_budget > c.max_price
    thin = {**fair, "n": 2}
    c2 = compute_costs(listing, {"risk_reserve": 1500}, _ev(), [], DEFAULT_STATE, thin, "utility_capability")
    assert c2.max_price_basis == "budget" and c2.max_price_value is None


def test_budget_for_mission_overrides_only_what_it_sets():
    from scout.policy.state import DEFAULT_STATE, budget_for
    st = {**DEFAULT_STATE, "budgets_by_mission": {"future_keeper": {"max_price": 35000}}}
    b = budget_for(st, "future_keeper")
    assert b["max_price"] == 35000 and b["acceptable_all_in"] == DEFAULT_STATE["budget"]["acceptable_all_in"]
    assert budget_for(st, "enthusiast_bridge") == DEFAULT_STATE["budget"]


def test_merit_leaves_out_documentation_only():
    from scout.policy.engine import compute_merit
    from scout.policy.schema import Score
    s = Score(documentation=5, condition=15, price_value=9, mission_fit=10, logistics=8, emotional_spec_fit=7, total=54)
    assert compute_merit(s) == round(100 * 49 / 75)


def test_next_step_contact_watch_skip():
    from scout.policy.engine import compute_next_step
    from scout.policy.schema import Gate
    none = {"document": [{"key": "k", "label": "Timing belt receipts (date)", "status": "missing"}], "inspection": [], "observed": []}
    assert compute_next_step({}, "listing", [], none, _costs18(20000, 21000), 55)["action"] == "Contact now"
    assert compute_next_step({}, "listing", [], none, _costs18(20000, 21000), 45)["action"] == "Watch"
    over = compute_next_step({}, "listing", [], none, _costs18(26000, 21000), 60)
    assert over["action"] == "Skip" and "24% over" in over["reason"]
    hard = [Gate(kind="hard", key="x", reason="Heavy structural rust")]
    assert compute_next_step({}, "listing", hard, none, _costs18(20000, 21000), 0)["action"] == "Skip"
    assert compute_next_step({}, "questions", [], none, _costs18(20000, 21000), 55)["action"] == "Follow up"
    assert compute_next_step({}, "docs", [], none, _costs18(20000, 21000), 55) is None
