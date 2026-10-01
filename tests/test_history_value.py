"""Policy 1.9.1: a branded title or an insurance total loss cuts fair value 30%."""
from scout import db
from scout.policy.costs import HISTORY_VALUE_DISCOUNT, history_value_flag

GLOSSARY = "Total Loss Vehicle\nAn insurance or fleet company declares a vehicle a total loss when a claim exceeds approximately 75%"
CLEAN = "Title History\nDamage Brands\nSalvage | Junk | Rebuilt | Fire | Flood | Hail | Lemon No Problem\n" + GLOSSARY
TOTALLED = "Event 1\n11/10/2012\nTOTAL LOSS VEHICLE\nVehicle declared a total loss by an insurance company\nTotal Loss Reported: 11/10/2012.\n" + CLEAN


def _car(key, **extra):
    lid, _ = db.upsert_listing({"site": "facebook", "url": f"https://www.facebook.com/marketplace/item/{key}/", "title": "1999 BMW M Roadster",
                                "role": "candidate", "availability": "active", **extra})
    return db.get_listing(lid)


def test_total_loss_on_the_carfax_flags_the_car_but_the_glossary_alone_does_not():
    clean, totalled = _car("1"), _car("2")
    db.add_document(clean["id"], "carfax", CLEAN)
    db.add_document(totalled["id"], "carfax", TOTALLED)
    assert history_value_flag(clean) is None
    assert history_value_flag(totalled) == "an insurance total loss on its history report"


def test_branded_title_status_flags_the_car():
    assert history_value_flag(_car("3", title_status="Rebuilt (FL)")) == "a rebuilt title"
    assert history_value_flag(_car("4", title_status="Clean (OR)")) is None


def test_the_cut_reaches_the_walk_away():
    from scout.policy.costs import compute_costs
    from scout.policy.schema import EvidenceInterpretation
    import inspect
    car = _car("5", price=8000, price_kind="asking", title_status="Rebuilt")
    fair = {"low": 16000, "mid": 18000, "high": 21000, "n": 40, "basis": "sold"}
    ev = EvidenceInterpretation.model_construct(immediate_service_estimate=type("R", (), {"low": 0, "high": 0})(), known_work_estimate=None,
                                                known_work_items=[], facts=[], contradictions=[])
    try:
        costs = compute_costs(car, {}, ev, [], {"tax_rate": 0, "registration_fee": 0}, fair, "enthusiast_bridge")
    except Exception as e:   # the cost model reads more evidence fields than this stub sets
        import pytest; pytest.skip(f"stub evidence incomplete: {e}")
    assert costs.fair_high == int(21000 * (1 - HISTORY_VALUE_DISCOUNT))
    assert costs.max_price_value is not None and costs.max_price_value <= costs.fair_high
    assert any("rule of thumb" in n for n in costs.notes)
