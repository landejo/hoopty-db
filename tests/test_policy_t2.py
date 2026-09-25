"""Tests for Task 2 (t2) ranking policy improvements:
- Confidence formula refinement
- compute_score with costs parameter
- State-based exclusions single-source
"""
from scout import db
from scout.policy.gates import is_excluded
from scout.policy.schema import CategoryRating, CostBreakdown, EvidenceInterpretation, Gate, MoneyRange, Ratings
from scout.policy.scoring import compute_confidence, compute_score
from scout.policy.state import DEFAULT_STATE, load_state, reset_state, save_state


def _rating(v: int) -> CategoryRating:
    return CategoryRating(rating=v, rationale="test")


def _ev(doc=8, cond=8, val=7, fit=8, log=8, emo=8, quality=7, facts=None, critical=None, flags=None, **kw):
    """Build an EvidenceInterpretation for testing."""
    from scout.policy.schema import Fact

    fact_list = []
    if facts:
        for key, status in facts.items():
            fact_list.append(Fact(key=key, status=status, source="ai_inference"))

    critical_list = []
    if critical:
        for key, status in critical.items():
            critical_list.append({"key": key, "status": status, "evidence": "", "source": "ai_inference"})

    return EvidenceInterpretation(
        ratings=Ratings(
            documentation=_rating(doc),
            condition=_rating(cond),
            price_value=_rating(val),
            mission_fit=_rating(fit),
            logistics=_rating(log),
            emotional_spec_fit=_rating(emo),
        ),
        evidence_quality=quality,
        immediate_service_estimate=MoneyRange(low=500, high=1500),
        facts=fact_list,
        critical_evidence=critical_list,
        flags=flags or {},
        **kw,
    )


def _listing(**kw):
    base = {
        "id": 1, "site": "facebook", "url": "u", "year": 2001,
        "make": "BMW", "model": "Z3 3.0i roadster", "trim": "Sport",
        "transmission": "Manual", "mileage": 80000, "price": 12000,
        "price_kind": "asking", "location": "Santa Cruz, CA",
        "engine_liters": 3.0, "photos": ["p"], "raw_text": "x" * 1000,
    }
    base.update(kw)
    return base


def _costs(price_basis="asking", price=12000, all_in_low=14000, all_in_high=16000, **kw):
    """Helper to build a minimal CostBreakdown for testing."""
    base = {
        "price_basis": price_basis,
        "price": price,
        "buyer_fee": 0,
        "transport": 900,
        "immediate_service_low": 500,
        "immediate_service_high": 1500,
        "overdue_allowance": 0,
        "risk_reserve": 1500,
        "tax_and_registration": 1200,
        "all_in_low": all_in_low,
        "all_in_high": all_in_high,
        "max_price": 16000,
        "offer_low": 14000,
        "offer_high": 16000,
    }
    base.update(kw)
    return CostBreakdown(**base)


class TestConfidenceFormula:
    """Test the refined confidence computation formula."""

    def test_confidence_base_case_high(self):
        """Confidence with high evidence quality and no penalties."""
        ev = _ev(quality=7, facts={}, critical={})
        listing = _listing()
        gates = []

        # c = 30 + 7*6 = 30 + 42 = 72
        c = compute_confidence(ev, gates, listing)
        assert c == 72

    def test_confidence_decision_unknown_penalty(self):
        """Decision-relevant unknowns (3 each, cap 12)."""
        ev = _ev(
            quality=7,
            facts={
                "title_status": "unknown",
                "accident_history": "unknown",
                "records_available": "unknown",
                "other_unknown": "unknown",  # Not decision-relevant, no penalty
            },
        )
        listing = _listing()
        gates = []

        # c = 30 + 42 = 72
        # Minus 3 for each of 3 decision unknowns = minus 9
        # c = 72 - 9 = 63
        c = compute_confidence(ev, gates, listing)
        assert c == 63

    def test_confidence_decision_unknown_cap(self):
        """Decision unknown penalty capped at 12."""
        ev = _ev(
            quality=7,
            facts={
                "title_status": "unknown",
                "accident_history": "unknown",
                "records_available": "unknown",
                "mileage": "unknown",
                "vin": "unknown",
                "owners": "unknown",  # Six unknowns: would be 18, capped at 12
            },
        )
        listing = _listing()
        gates = []

        # c = 72 - 12 = 60
        c = compute_confidence(ev, gates, listing)
        assert c == 60

    def test_confidence_critical_missing_gates(self):
        """Critical missing gates (4 each, cap 12)."""
        ev = _ev(quality=7)
        listing = _listing()
        gates = [
            Gate(kind="conditional", key="critical_missing:timing_belt", reason="test"),
            Gate(kind="conditional", key="critical_missing:rear_structure", reason="test"),
        ]

        # c = 72 - 8 = 64
        c = compute_confidence(ev, gates, listing)
        assert c == 64

    def test_confidence_critical_missing_cap(self):
        """Critical missing gate penalty capped at 12."""
        ev = _ev(quality=7)
        listing = _listing()
        gates = [
            Gate(kind="conditional", key="critical_missing:timing_belt", reason="test"),
            Gate(kind="conditional", key="critical_missing:rear_structure", reason="test"),
            Gate(kind="conditional", key="critical_missing:suspension", reason="test"),
            Gate(kind="conditional", key="critical_missing:brakes", reason="test"),  # Four gates: would be 16, capped at 12
        ]

        # c = 72 - 12 = 60
        c = compute_confidence(ev, gates, listing)
        assert c == 60

    def test_confidence_contradictions(self):
        """Contradictions penalty (3 each, cap 9)."""
        from scout.policy.schema import Contradiction

        ev = _ev(
            quality=7,
            contradictions=[
                Contradiction(topic="mileage", detail="inconsistent", severity="material"),
                Contradiction(topic="service", detail="undocumented", severity="minor"),
            ],
        )
        listing = _listing()
        gates = []

        # c = 72 - 6 = 66
        c = compute_confidence(ev, gates, listing)
        assert c == 66

    def test_confidence_no_photos(self):
        """No photos penalty: -5."""
        ev = _ev(quality=7)
        listing = _listing(photos=[])
        gates = []

        # c = 72 - 5 = 67
        c = compute_confidence(ev, gates, listing)
        assert c == 67

    def test_confidence_short_raw_text(self):
        """Short raw_text penalty: -10."""
        ev = _ev(quality=7)
        listing = _listing(raw_text="x" * 100)
        gates = []

        # c = 72 - 10 = 62
        c = compute_confidence(ev, gates, listing)
        assert c == 62

    def test_confidence_combined_low_evidence(self):
        """Combined penalties: low quality + unknowns + critical missing + no photos + short text."""
        ev = _ev(
            quality=3,
            facts={"title_status": "unknown"},
        )
        listing = _listing(photos=[], raw_text="short")
        gates = [
            Gate(kind="conditional", key="critical_missing:timing_belt", reason="test"),
        ]

        # c = 30 + 18 = 48
        # c = 48 - 3 = 45 (one decision unknown)
        # c = 45 - 4 = 41 (one critical missing)
        # c = 41 - 5 = 36 (no photos)
        # c = 36 - 10 = 26 (short text)
        c = compute_confidence(ev, gates, listing)
        assert c == 26

    def test_confidence_clamp_min(self):
        """Confidence clamped to minimum 5."""
        ev = _ev(quality=0)  # Very low quality
        listing = _listing(photos=[], raw_text="")
        gates = [
            Gate(kind="conditional", key="critical_missing:timing_belt", reason="test"),
            Gate(kind="conditional", key="critical_missing:rear_structure", reason="test"),
        ]

        # c = 30 + 0*6 = 30
        # Two critical_missing: 30 - 8 = 22
        # No photos: 22 - 5 = 17
        # Short text: 17 - 10 = 7
        c = compute_confidence(ev, gates, listing)
        assert c == 7

    def test_confidence_clamp_max(self):
        """Confidence clamped to maximum 100."""
        ev = _ev(quality=10)  # Perfect quality
        listing = _listing()
        gates = []

        # c = 30 + 60 = 90 (already below 100)
        c = compute_confidence(ev, gates, listing)
        assert c == 90

    def test_confidence_target_case_1_high_evidence(self):
        """Target case: evidence_quality 7, one decision unknown, one critical_missing, photos, long text."""
        # Should yield 30 + 42 - 3 - 4 = 65
        ev = _ev(quality=7, facts={"title_status": "unknown"})
        listing = _listing()
        gates = [Gate(kind="conditional", key="critical_missing:timing_belt", reason="test")]

        c = compute_confidence(ev, gates, listing)
        assert c == 65

    def test_confidence_target_case_2_medium_evidence(self):
        """Target case: evidence_quality 3, three decision unknowns, two critical_missing."""
        # Should yield 30 + 18 - 9 - 8 = 31
        ev = _ev(
            quality=3,
            facts={
                "title_status": "unknown",
                "accident_history": "unknown",
                "records_available": "unknown",
            },
        )
        listing = _listing()
        gates = [
            Gate(kind="conditional", key="critical_missing:timing_belt", reason="test"),
            Gate(kind="conditional", key="critical_missing:rear_structure", reason="test"),
        ]

        c = compute_confidence(ev, gates, listing)
        assert c == 31


class TestComputeScoreWithCosts:
    """Test compute_score with costs parameter."""

    def test_compute_score_backward_compatible_no_costs(self):
        """When costs is None, behavior is unchanged from before."""
        ev = _ev()
        listing = _listing(price=12000)
        gates = []
        state = DEFAULT_STATE
        vin_history = {}

        # Should use listing price
        score = compute_score(ev, gates, listing, "enthusiast_bridge", state, vin_history, costs=None)
        # Base score with default _ev values: doc=20, cond=20, val=10, fit=12, log=8, emo=8 = 78
        assert score.total == 78

    def test_compute_score_with_costs_uses_cost_price(self):
        """When costs is given, use costs.price for mission-fit cap logic."""
        ev = _ev(fit=10)  # High mission fit
        listing = _listing(price=3000)  # Low bid price
        gates = []
        state = DEFAULT_STATE
        vin_history = {}

        # Early auction with low current bid, but expected hammer is high
        costs = _costs(
            price_basis="expected_hammer",
            price=16000,  # Expected hammer > max_price (15000)
            all_in_low=18000,
            all_in_high=19000,  # Midpoint = 18500 < defeats_purpose (21000)
        )

        score = compute_score(ev, gates, listing, "enthusiast_bridge", state, vin_history, costs=costs)
        # Mission fit should be based on costs.price (16000), not listing price (3000)
        # 16000 > max_price (15000), so cap applies
        # all_in_mid (18500) < defeats_purpose_all_in (21000), so cap is 9
        # rating is 10, capped to 9
        assert score.mission_fit == 9
        assert any("price above the" in cap and "budget" in cap for cap in score.caps_applied)

    def test_compute_score_with_costs_all_in_midpoint_vs_defeats_purpose(self):
        """When all-in midpoint exceeds defeats_purpose_all_in, cap mission_fit at 6."""
        ev = _ev(fit=10)  # High mission fit
        listing = _listing(price=17500)
        gates = []
        state = DEFAULT_STATE
        vin_history = {}

        # All-in midpoint exceeds defeats_purpose_all_in
        costs = _costs(
            price_basis="asking",
            price=17500,
            all_in_low=23000,
            all_in_high=26000,  # Midpoint = 24500 > defeats_purpose_all_in (21000)
        )

        score = compute_score(ev, gates, listing, "pragmatic_bridge", state, vin_history, costs=costs)
        # 17500 > max_price (15000) AND midpoint (24500) > defeats_purpose (21000)
        # So cap is 6, not 9
        assert score.mission_fit <= 6
        assert any("price above the" in cap and "budget" in cap for cap in score.caps_applied)

    def test_compute_score_with_unpriced_auction(self):
        """When costs.price_basis is "unpriced", cap mission_fit at 9."""
        ev = _ev(fit=10)  # High mission fit
        listing = _listing(price=0)  # No price yet
        gates = []
        state = DEFAULT_STATE
        vin_history = {}

        costs = _costs(price_basis="unpriced", price=0, all_in_low=1000, all_in_high=2000)

        score = compute_score(ev, gates, listing, "enthusiast_bridge", state, vin_history, costs=costs)
        # Mission fit should be capped at 9 with note about unpriced
        assert score.mission_fit == 9
        assert any("auction price unknown" in cap for cap in score.caps_applied)

    def test_compute_score_with_costs_keeper_uses_its_own_budget(self):
        """1.8.0: every mission is held to its budget; a keeper with its own higher budget is not capped."""
        ev = _ev(fit=8)
        listing = _listing(price=50000)  # Very high price
        costs = _costs(price=50000, all_in_low=55000, all_in_high=60000)
        # General budget only: a $50k keeper is over it, so mission fit is capped.
        score = compute_score(ev, [], listing, "future_keeper", DEFAULT_STATE, {}, costs=costs)
        assert score.mission_fit <= 9
        assert any("future keeper budget" in c for c in score.caps_applied)
        # A keeper budget that covers it: 15 * 8 / 10 = 12, uncapped.
        state = {**DEFAULT_STATE, "budgets_by_mission": {"future_keeper": {"max_price": 60000, "defeats_purpose_all_in": 70000}}}
        score = compute_score(ev, [], listing, "future_keeper", state, {}, costs=costs)
        assert score.mission_fit == 12


class TestIsExcludedStateSingleSource:
    """Test is_excluded using active_exclusions only."""

    def test_is_excluded_mazda_mx5_miata_variant(self):
        """Mazda MX-5 Miata matches 'Mazda MX-5' exclusion via full name match."""
        excluded = is_excluded("Mazda", "MX-5 Miata", ["Mazda MX-5"])
        assert excluded == "Mazda MX-5"

    def test_is_excluded_mazda_miata(self):
        """Mazda Miata matches via direct entry."""
        excluded = is_excluded("Mazda", "Miata", ["Mazda Miata"])
        assert excluded == "Mazda Miata"

    def test_is_excluded_bmw_z4_exact(self):
        """BMW Z4 matches 'BMW Z4' exclusion."""
        excluded = is_excluded("BMW", "Z4 3.0i", ["BMW Z4"])
        assert excluded == "BMW Z4"

    def test_is_excluded_saturn_partial(self):
        """Saturn matches 'Saturn' exclusion."""
        excluded = is_excluded("Saturn", "Ion", ["Saturn"])
        assert excluded == "Saturn"

    def test_not_excluded_bmw_z3(self):
        """BMW Z3 is not excluded when Z4 is excluded."""
        excluded = is_excluded("BMW", "Z3 M Coupe", ["BMW Z4"])
        assert excluded is None

    def test_not_excluded_lexus_gx(self):
        """Lexus GX470 not excluded by IS350 or GS350."""
        excluded = is_excluded("Lexus", "GX470", ["Lexus IS350", "Lexus GS350"])
        assert excluded is None

    def test_is_excluded_lexus_is350(self):
        """Lexus IS350 matches exclusion."""
        excluded = is_excluded("Lexus", "IS350", ["Lexus IS350"])
        assert excluded == "Lexus IS350"

    def test_is_excluded_lexus_sc430(self):
        """Lexus SC430 matches exclusion."""
        excluded = is_excluded("Lexus", "SC430", ["Lexus SC430"])
        assert excluded == "Lexus SC430"

    def test_is_excluded_empty_list(self):
        """Empty exclusions list returns None."""
        excluded = is_excluded("Mazda", "MX-5", [])
        assert excluded is None

    def test_is_excluded_case_insensitive(self):
        """Matching is case-insensitive."""
        excluded = is_excluded("MAZDA", "mx-5", ["mazda mx-5"])
        assert excluded == "mazda mx-5"

    def test_is_excluded_space_and_hyphen_normalization(self):
        """Spaces and hyphens are stripped for matching."""
        excluded = is_excluded("BMW", "Z-4 3.0i", ["BMW Z4"])
        assert excluded == "BMW Z4"


class TestExclusionsMigration:
    """Test the migration logic for exclusions in state.py."""

    def test_migration_adds_default_exclusions(self):
        """Stored state without _migrations marker gets Z4/Saturn/MX-5 added."""
        reset_state()
        save_state({
            "active_exclusions": ["Lexus SC430"],
        })

        state = load_state()

        # Should now have the defaults added
        exclusions = state.get("active_exclusions", [])
        assert "Lexus SC430" in exclusions
        assert "BMW Z4" in exclusions
        assert "Saturn" in exclusions
        assert "Mazda MX-5" in exclusions
        assert "_migrations" in state
        assert "exclusions_v2" in state.get("_migrations", [])

    def test_migration_preserves_user_order(self):
        """Migration preserves the user's existing exclusions first."""
        reset_state()
        save_state({
            "active_exclusions": ["Lexus SC430", "Honda CR-Z"],
        })

        state = load_state()
        exclusions = state.get("active_exclusions", [])

        # User's items should be first
        assert exclusions[0] == "Lexus SC430"
        assert exclusions[1] == "Honda CR-Z"

    def test_migration_idempotent(self):
        """Running migration twice doesn't duplicate items."""
        reset_state()
        save_state({
            "active_exclusions": ["Lexus SC430"],
        })

        state1 = load_state()
        state2 = load_state()

        assert state1.get("active_exclusions") == state2.get("active_exclusions")

    def test_user_can_remove_after_migration(self):
        """After migration, user can remove items and they stay removed."""
        reset_state()
        save_state({
            "active_exclusions": ["Lexus SC430"],
        })

        # Migrate
        state = load_state()

        # User removes Saturn
        state["active_exclusions"].remove("Saturn")
        save_state({"active_exclusions": state["active_exclusions"]})

        # Reload and verify Saturn is gone
        state = load_state()
        assert "Saturn" not in state.get("active_exclusions", [])
        assert "Lexus SC430" in state.get("active_exclusions", [])

    def test_migration_marker_prevents_duplicate_runs(self):
        """Once _migrations marker is set, migration doesn't re-run."""
        reset_state()

        # Manually set up a state with marker
        db.set_setting("policy_state", {
            "active_exclusions": ["Lexus SC430"],
            "_migrations": ["exclusions_v2"],
        })

        state = load_state()
        # Should not have duplicates
        assert state.get("active_exclusions", []).count("Lexus SC430") == 1

    def test_state_keys_iteration_works_with_migrations_key(self):
        """_migrations key doesn't break code iterating over state keys."""
        reset_state()
        save_state({
            "active_exclusions": ["Lexus SC430"],
        })

        state = load_state()

        # These should work without errors
        keys = state.keys()
        assert "active_exclusions" in keys
        assert "_migrations" in keys

        # Iteration should be fine
        for k, v in state.items():
            assert k is not None
