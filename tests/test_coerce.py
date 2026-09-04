from scout import coerce


def test_parse_json_strips_fences_and_prose():
    assert coerce.parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert coerce.parse_json('Here you go: {"a": 1} thanks') == {"a": 1}
    assert coerce.parse_json("nope") == {}


def test_normalized_listing_bounds():
    out = coerce.normalized_listing({
        "year": "2001", "mileage": "88,000", "price": "$24,500", "vin": "wbsck9345 1LC98765",
        "transmission": "5-speed manual", "availability": "SOLD", "price_kind": "sold",
        "sold_price": 25000, "scores": {"reliability": 4, "bogus": 5, "value": 9},
        "red_flags": ["one", "", "two"], "profile_key": "z3_m", "listing_date": "2026-08-01",
        "engine_liters": "3.2L",
    })
    assert out["year"] == 2001 and out["mileage"] == 88000 and out["price"] == 24500
    assert out["vin"] == "WBSCK93451LC98765"
    assert out["transmission"] == "Manual"
    assert out["availability"] == "sold" and out["sold_price"] == 25000
    assert out["prelim_scores"] == {"reliability": 4}
    assert out["red_flags"] == ["one", "two"]
    assert out["engine_liters"] == 3.2
    assert out["listing_date"] == "2026-08-01"


def test_normalized_listing_drops_junk():
    out = coerce.normalized_listing({"year": -56, "mileage": 9_999_999, "vin": "STOCK123",
                                     "listing_date": "last week", "availability": "maybe"})
    assert "year" not in out and "mileage" not in out and "vin" not in out
    assert "listing_date" not in out and "availability" not in out


def test_analysis_checks_filtered_to_profile_keys():
    out = coerce.analysis({
        "verdict": "Pursue", "deal_score": 150, "confidence": 4,
        "checks": [{"key": "vanos", "status": "pass", "notes": "done"},
                   {"key": "made_up", "status": "pass", "notes": ""},
                   {"key": "vanos", "status": "nope", "notes": ""}],
        "pricing": {"fair_value": "$21,000", "target_offer": 19500, "nonsense": 1},
        "scores": {"condition": 5},
    }, {"vanos", "cooling_overhaul"})
    assert out["deal_score"] is None  # out of range dropped
    assert out["checks"] == [{"key": "vanos", "status": "pass", "notes": "done"}]
    assert out["pricing"] == {"fair_value": 21000, "target_offer": 19500}
    assert out["scores"] == {"condition": 5}


def test_profile_coerce_requires_weights_over_known_axes():
    assert coerce.profile({"key": "x", "label": "X", "weights": {"foo": 1}}) is None
    p = coerce.profile({"key": "Porsche 911-997", "label": "911", "weights": {"reliability": 2, "value": 1, "engagement": 1},
                        "checks": [{"key": "IMS Bearing", "label": "IMS"}, {"key": "IMS Bearing", "label": "dup"}],
                        "years": [2005, 2012]})
    assert p["key"] == "porsche_911_997"
    assert abs(sum(p["weights"].values()) - 1) < 1e-6
    assert p["checks"] == [{"key": "ims_bearing", "label": "IMS"}]
    assert p["years"] == [2005, 2012]
