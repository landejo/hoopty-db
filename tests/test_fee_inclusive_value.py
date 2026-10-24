"""Policy 1.9.3: auction comps count at hammer + buyer's fee (what the winner paid), and
an auction car's own fair value and walk-away are hammers, with its fee costed once."""
from scout import db
from scout.market import auction_fee, fair_value, realized_price
from scout.policy.costs import _hammer_for
from scout.policy.engine import assess
from scout.policy.schema import CategoryRating, EvidenceInterpretation, MoneyRange, Ratings
from scout.policy.state import DEFAULT_STATE

STATE = dict(DEFAULT_STATE)


def _sale(price, site):
    return {"id": None, "site": site, "year": 2001, "model": "Z3 3.0i roadster", "trim": "", "body_style": "Roadster",
            "transmission": "Manual", "mileage": 60000, "price": price, "price_kind": "sold", "availability": "sold",
            "sold_price": price}


def _ev():
    r = lambda v: CategoryRating(rating=v, rationale="test")
    return EvidenceInterpretation(
        ratings=Ratings(documentation=r(7), condition=r(7), price_value=r(7), mission_fit=r(8), logistics=r(7), emotional_spec_fit=r(8)),
        evidence_quality=7, immediate_service_estimate=MoneyRange(low=0, high=0))


def _listing(site, price_kind="asking"):
    return {"id": 1, "site": site, "url": "u", "year": 2001, "make": "BMW", "model": "Z3 3.0i", "trim": "",
            "transmission": "Manual", "mileage": 60000, "price": 15000, "price_kind": price_kind, "location": "Carmel, CA",
            "engine_liters": 3.0, "photos": ["p"], "raw_text": "x" * 1000}


def test_auction_fee_has_a_floor_and_a_cap_and_classifieds_pay_none():
    assert auction_fee("bat", 3000) == 250          # 5% would be $150
    assert auction_fee("carsandbids", 20000) == 1000
    assert auction_fee("bat", 200000) == 7500       # 5% would be $10,000
    assert auction_fee("facebook", 20000) == 0 and auction_fee(None, 20000) == 0


def test_realized_price_adds_the_fee_to_hammers_and_discounts_last_asks():
    assert realized_price({"site": "bat"}, 20000) == 21000
    assert realized_price({"site": "cargurus"}, 20000) == 18600


def test_fair_value_from_auction_sales_includes_the_fee():
    listing = {"id": 1, "year": 2001, "model": "Z3 3.0i roadster", "body_style": "Roadster", "transmission": "Manual", "mileage": 60000}
    assert fair_value(listing, [_sale(20000, "bat") for _ in range(4)])["mid"] == 21000


def test_hammer_for_inverts_hammer_plus_fee():
    for paid in (2000, 21000, 120000, 300000):
        h = _hammer_for(paid, "bat", STATE)
        assert abs(h + auction_fee("bat", h) - paid) <= 1


def test_an_auction_car_is_valued_as_a_hammer_and_a_dealer_car_as_a_price():
    fair = {"mid": 21000, "low": 19950, "high": 22050, "n": 8, "basis": "sold", "relaxed": [], "note": "8 sales"}
    prof = db.get_profile("z3_30i")
    dealer = assess(_listing("autotrader"), prof, _ev(), STATE, fair=fair, mission="enthusiast_bridge").costs
    auction = assess(_listing("bat", "current_bid"), prof, _ev(), STATE, fair=fair, mission="enthusiast_bridge").costs
    assert dealer.fair_mid == 21000 and dealer.buyer_fee == 0
    assert auction.fair_mid == 20000 and "buyer's fee taken out" in auction.fair_note
    # The walk-away from value is the top of the range: paid for the dealer car, a hammer at auction.
    assert dealer.max_price_value is not None and auction.max_price_value is not None
    assert dealer.max_price_value - auction.max_price_value == 22050 - _hammer_for(22050, "bat", STATE)


# ---------- Carfax Value on the export (reference only) ----------

def test_carfax_value_reads_listing_text_and_report_wording():
    from scout.publish import carfax_value
    assert carfax_value(["GREAT VALUE\n$15,000\n$1,830\nbelow\n$16,830\nCARFAX Value"]) == 16830
    assert carfax_value(["Price $21,837 · $673 below $22,510 CARFAX Value"]) == 22510
    assert carfax_value([None, "No accidents. CARFAX value $16,830 (asking $15,000)"]) == 16830
    assert carfax_value(["History-Based Value $15,000 Total Price", None]) is None


def test_export_carries_carfax_value_from_the_listing_or_an_attached_report():
    from scout.ingest import ingest_items
    from scout.publish import build_export
    ingest_items("carfax", [{"url": "https://www.carfax.com/vehicle/JM1NDAM79H0104490", "title": "2017 Mazda Miata",
                             "price_text": "$21,837", "detail": {"text": "Used 2017 Mazda Miata $21,837 $673 below $22,510 CARFAX Value " + "x" * 600}}],
                 run_ai=False)
    ingest_items("autotrader", [{"url": "https://www.autotrader.com/cars-for-sale/vehicle/1", "title": "2001 BMW Z3 3.0i",
                                 "price_text": "$16,068", "detail": {"text": "t" * 900}}], run_ai=False)
    z3 = db.get_listing_by_url("https://www.autotrader.com/cars-for-sale/vehicle/1")["id"]
    db.add_document(z3, "carfax", "CARFAX Vehicle History Report ... $15,900\nCARFAX Value ...")
    by_url = {l["url"]: l for l in build_export()["listings"]}
    assert by_url["https://www.carfax.com/vehicle/JM1NDAM79H0104490"]["carfax_value"] == 22510
    assert by_url["https://www.autotrader.com/cars-for-sale/vehicle/1"]["carfax_value"] == 15900
