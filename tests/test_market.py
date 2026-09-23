"""scout.market: is_sale classification, body_class, segmentation/relaxation,
mileage adjustment and the asking-price fallback. Pure functions, no AI/DB."""
from datetime import date

from scout.market import body_class, fair_value, is_sale


def _row(**kw):
    base = {"id": None, "year": 2001, "model": "Z3 3.0i roadster", "trim": "", "body_style": None,
            "transmission": "Manual", "mileage": 80000, "price": None, "price_kind": None,
            "availability": None, "sold_price": None}
    base.update(kw)
    return base


def test_is_sale_classifies_all_observed_combos():
    assert is_sale(_row(price_kind="sold", availability="sold", sold_price=15000)) is True
    assert is_sale(_row(price_kind="sold", availability="ended", sold_price=15000)) is True       # 24 rows in the data
    assert is_sale(_row(price_kind="asking", availability="sold", price=15000)) is True           # 5 rows
    assert is_sale(_row(price_kind="reserve_not_met", availability="ended", price=15000)) is False  # 13 rows: failed auction
    assert is_sale(_row(price_kind="current_bid", availability="ended", price=15000)) is False     # 10 rows: failed auction
    assert is_sale(_row(price_kind="no_reserve", availability="ended", price=15000)) is False      # 1 row: not a completed sale record
    assert is_sale(_row(price_kind="asking", availability="active", price=15000)) is False
    assert is_sale(_row(price_kind="sold", availability="sold", sold_price=0)) is False            # no positive price


def test_body_class():
    assert body_class(_row(model="Z3 M roadster")) == "open"
    assert body_class(_row(model="Z3 M coupe")) == "closed"
    assert body_class(_row(model="911", body_style="Convertible")) == "open"
    assert body_class(_row(model="GX470", body_style="SUV")) == "closed"
    assert body_class(_row(model="Widget", body_style=None, trim="")) is None


def _sale(year, price, miles, trans="Manual", model="Z3 3.0i roadster", **kw):
    return _row(year=year, price_kind="sold", availability="sold", sold_price=price, price=price,
               mileage=miles, transmission=trans, model=model, **kw)


def test_segmentation_relaxes_in_order_and_records_it():
    listing = _row(id=1, year=2001, transmission="Manual", model="Z3 3.0i roadster", mileage=80000)
    # Only 2 rows within year+/-3/body/trans -> relax down to a wider net for >=4.
    rows = [
        _sale(2000, 14000, 80000),
        _sale(2002, 15000, 75000),
        _sale(1997, 13000, 90000),                          # outside +-3 years, inside +-6
        _sale(1996, 12000, 95000, trans="Automatic"),        # needs transmission dropped too
    ]
    fv = fair_value(listing, rows)
    assert fv is not None and fv["basis"] == "sold" and fv["n"] == 4
    assert fv["relaxed"]                                     # something had to give


def test_segmentation_no_relaxation_needed():
    listing = _row(id=1, year=2001)
    rows = [_sale(2000, 14000, 80000), _sale(2002, 15000, 75000), _sale(2001, 15500, 82000), _sale(2003, 13500, 78000)]
    fv = fair_value(listing, rows)
    assert fv["relaxed"] == [] and fv["n"] == 4


def test_failed_high_bids_excluded_from_sold_pool_but_reported_as_floor():
    listing = _row(id=1, year=2001)
    rows = [
        _sale(2000, 14000, 80000), _sale(2002, 15000, 75000), _sale(2001, 15500, 82000), _sale(2003, 13500, 78000),
        _row(id=9, year=2001, price_kind="reserve_not_met", availability="ended", price=19000),
    ]
    fv = fair_value(listing, rows)
    assert fv["n"] == 4                       # the failed auction never enters the sold pool
    assert fv["failed_high_bid_max"] == 19000  # but is reported as an informational floor


def test_mileage_adjustment_is_clamped():
    listing = _row(id=1, mileage=200000)
    # A near-new comp would blow past the clamp without it.
    rows = [_sale(2001, 20000, 10000), _sale(2001, 19000, 12000), _sale(2001, 21000, 9000), _sale(2001, 20500, 11000)]
    fv = fair_value(listing, rows)
    # Unclamped, exp(-0.004*(200000-10000)/1000) ~= 0.047x; clamped floor is 0.70x.
    assert fv["mid"] >= 20000 * 0.70 - 1


def test_asking_fallback_when_fewer_than_two_sales():
    listing = _row(id=1, year=2001)
    rows = [
        _sale(2001, 14000, 80000),  # only one sale: not enough
        _row(id=2, year=2001, price_kind="asking", availability="active", price=20000, mileage=80000),
        _row(id=3, year=2001, price_kind="asking", availability="active", price=22000, mileage=80000),
    ]
    fv = fair_value(listing, rows)
    assert fv["basis"] == "asking"
    assert fv["mid"] == round(21000 * 0.93)  # median of 20000/22000, discounted 7%


def test_fair_value_none_with_insufficient_data():
    listing = _row(id=1, year=2001)
    assert fair_value(listing, []) is None
    assert fair_value(listing, [_sale(2001, 14000, 80000)]) is None  # one sale, no asking pool either


def test_listing_excludes_itself_and_same_vehicle():
    listing = _row(id=1, vehicle_id=100, year=2001)
    rows = [
        listing,                                                     # itself, by id
        _row(id=2, vehicle_id=100, year=2001, price_kind="sold", availability="sold", sold_price=14000, mileage=80000),  # same car, different listing
        _sale(2001, 14500, 80000, id=3), _sale(2001, 15000, 79000, id=4), _sale(2001, 15500, 81000, id=5),
    ]
    fv = fair_value(listing, rows)
    assert fv["n"] == 3   # the self-listing and its vehicle twin never enter the pool


def test_z3_m_coupe_and_roadster_get_different_fair_values():
    listing_coupe = _row(id=1, model="Z3 M coupe", year=2000)
    rows = [
        _sale(1999, 35000, 40000, model="Z3 M coupe"), _sale(2000, 36000, 38000, model="Z3 M coupe"),
        _sale(2001, 34000, 42000, model="Z3 M coupe"), _sale(2000, 33000, 45000, model="Z3 M coupe"),
        _sale(1999, 22000, 40000, model="Z3 M roadster"), _sale(2000, 23000, 38000, model="Z3 M roadster"),
        _sale(2001, 21000, 42000, model="Z3 M roadster"), _sale(2000, 20000, 45000, model="Z3 M roadster"),
    ]
    fv_coupe = fair_value(listing_coupe, rows)
    fv_roadster = fair_value(_row(id=2, model="Z3 M roadster", year=2000), rows)
    assert fv_coupe["mid"] > fv_roadster["mid"] * 1.3
