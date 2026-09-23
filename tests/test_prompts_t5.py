"""Tranche 5: cacheable prompt split, deterministic headline, editable buyer context."""
import re
from datetime import date

import pytest

from scout.ai import assess as assess_mod
from scout.ai import normalize as norm_mod
from scout.policy.engine import compute_headline
from scout.policy.preferences import COMPACT_CONTEXT
from scout.policy.schema import Gate, Score
from scout.policy.state import DEFAULT_STATE, load_state, save_state


class _Captured(Exception):
    pass


def _capture_system(monkeypatch, module, fn_name):
    seen = {}

    def fake(model, system, user, *a, **k):
        seen["system"], seen["user"] = system, user
        raise _Captured

    monkeypatch.setattr(module, fn_name, fake)
    return seen


def _assess_system(monkeypatch, listing, profile, state):
    seen = _capture_system(monkeypatch, assess_mod, "call_json_text")
    monkeypatch.setattr(assess_mod, "photo_blocks", lambda urls, **k: [])
    with pytest.raises(_Captured):
        assess_mod.interpret_listing(listing, profile, "enthusiast_bridge", state, {}, [], [], [])
    return seen["system"]


def test_assess_static_block_identical_across_listings_and_carries_no_placeholders(monkeypatch):
    p1 = {"key": "z3_m", "label": "Z3 M", "critical_evidence": [{"key": "rear_structure", "label": "Rear", "severity": "conditional"}]}
    p2 = {"key": "gx470", "label": "GX470", "critical_evidence": [{"key": "timing_belt_water_pump", "label": "Belt", "severity": "conditional"}]}
    s1 = _assess_system(monkeypatch, {"id": 1, "site": "bat", "title": "A"}, p1, DEFAULT_STATE)
    s2 = _assess_system(monkeypatch, {"id": 2, "site": "facebook", "title": "B"}, p2, DEFAULT_STATE)
    assert isinstance(s1, list) and len(s1) == 2
    assert s1[0] == s2[0]                                     # cached prefix never varies
    assert date.today().isoformat() not in s1[0] and date.today().isoformat() in s1[1]
    assert "rear_structure" in s1[1] and "timing_belt_water_pump" in s2[1]
    for block in s1 + s2:
        assert not re.search(r"__[A-Z_]+__|\{[a-z_]+\}", block), "unrendered placeholder"
    assert len(s1[0]) / 4 > 1024                              # clears Opus 5 (512) and Sonnet 5 (1024) cache minimums


def test_assess_uses_editable_buyer_context(monkeypatch):
    state = {**DEFAULT_STATE, "buyer_context": "CUSTOM CONTEXT 123"}
    s = _assess_system(monkeypatch, {"id": 1, "site": "bat"}, {"key": "x", "label": "X"}, state)
    assert "CUSTOM CONTEXT 123" in s[1] and COMPACT_CONTEXT not in s[1]
    s = _assess_system(monkeypatch, {"id": 1, "site": "bat"}, {"key": "x", "label": "X"}, {k: v for k, v in DEFAULT_STATE.items() if k != "buyer_context"})
    assert COMPACT_CONTEXT in s[1]


def test_normalize_static_block_stable_and_no_axis_scores(monkeypatch):
    seen = _capture_system(monkeypatch, norm_mod, "call_text")
    with pytest.raises(_Captured):
        norm_mod.normalize_listing("1999 BMW M Roadster", {}, "bat", [])
    static, dynamic = seen["system"]
    assert date.today().isoformat() in dynamic and date.today().isoformat() not in static
    assert "scores:" not in static                             # legacy 9-axis request removed


def test_buyer_context_default_and_override():
    assert load_state()["buyer_context"] == COMPACT_CONTEXT
    assert save_state({"buyer_context": "new text"})["buyer_context"] == "new text"


def _score(total=60, doc=10, cond=12):
    return Score(documentation=doc, condition=cond, price_value=10, mission_fit=12, logistics=8, emotional_spec_fit=8, total=total, caps_applied=[])


def test_headline_paths():
    empty = {"document": [], "inspection": [], "observed": []}
    hard = [Gate(kind="strategy", key="explicit_exclusion", reason="Explicitly excluded model (BMW Z4)")]
    assert compute_headline("Do not pursue", "x", _score(), hard, empty, 60) == "Do not pursue — Explicitly excluded model (BMW Z4)"
    open_q = {"document": [{"key": "cooling_history", "label": "Cooling records (dated)", "status": "missing"}],
              "inspection": [{"key": "rear_structure", "label": "Rear structure; welds", "status": "missing"}], "observed": []}
    h = compute_headline("Pursue conditionally", "x", _score(), [], open_q, 78)
    assert h.startswith("Pursue conditionally — could reach 78") and "Cooling records" in h and "(" not in h
    h = compute_headline("Maybe / verify", "x", _score(), [], {**open_q, "observed": ["Rear main seal leak disclosed"]}, 70)
    assert h == "Maybe / verify — observed: Rear main seal leak disclosed; 2 questions open."
    long = compute_headline("Reject", "y" * 400, _score(), [], empty, None)
    assert len(long) <= 160
