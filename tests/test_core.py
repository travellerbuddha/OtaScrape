from datetime import date, datetime

import pytest

from otascrape.compare import build_comparisons, is_direct, price_changes
from otascrape.config import ConfigError, build_searches, parse_config
from otascrape.models import Offer
from otascrape.normalize import normalize_board, parse_number, parse_price
from otascrape.pricing import convert, per_night, per_person_night


def offer(seller, total, board="AI", currency="EUR", free=True, hotel="h1", nights=7, adults=2, children=0, check_in=date(2026, 11, 1)):
    return Offer(hotel, "trivago", seller, "7N-2AD", check_in, nights, adults, children, "Room", board, total, currency,
                 free, True, "u", datetime(2026, 10, 6))


def test_per_night_and_person():
    assert per_night(1400, 7) == 200
    assert per_person_night(1400, 7, 2, 1, "adults") == 100
    assert per_person_night(1500, 5, 2, 1, "all_guests") == 100
    with pytest.raises(ValueError):
        per_person_night(1, 1, 1, 0, "x")


def test_convert():
    assert convert(100, "USD", "EUR", {"USD": 0.9}) == 90
    assert convert(100, "EUR", "EUR", {}) == 100
    assert convert(100, "TRY", "EUR", {}) is None


@pytest.mark.parametrize("text,code", [
    ("Ultra All Inclusive", "UAI"), ("All-inclusive", "AI"), ("Her şey dahil", "AI"), ("Half board", "HB"),
    ("Tam pansiyon", "FB"), ("Breakfast included", "BB"), ("Kahvaltı dahil", "BB"), ("Room only", "RO"), ("Spa access", "UNKNOWN"),
])
def test_board(text, code):
    assert normalize_board(text) == code


@pytest.mark.parametrize("raw,val", [("2,345", 2345), ("2.345", 2345), ("2,345.50", 2345.5), ("2.345,50", 2345.5), ("12 345", 12345), ("99,5", 99.5), ("350", 350)])
def test_parse_number(raw, val):
    assert parse_number(raw) == val


@pytest.mark.parametrize("text,amt,cur", [("€ 2,345", 2345, "EUR"), ("TRY 12,345.50", 12345.5, "TRY"), ("US$1,200", 1200, "USD"), ("3.980,50 ₺", 3980.5, "TRY"), ("500", 500, None)])
def test_parse_price(text, amt, cur):
    assert parse_price(text) == (amt, cur)


def test_config_searches(cfg, today):
    s = build_searches(cfg, today)
    assert {x.check_in for x in s} == {date(2026, 10, 16), date(2030, 1, 1)}
    assert {x.channel for x in s} == {"booking", "trivago"}
    assert len(s) == 4 and s[0].check_out == date(2026, 10, 23)


def test_config_errors():
    base = {"stays": [{"nights": 7, "adults": 2, "children": 1}], "checkin": {"offsets_days": [1]}, "hotels": [{"id": "a", "channels": {"booking": "u"}}]}
    with pytest.raises(ConfigError, match="child_ages"):
        parse_config(base)


def test_comparison_reference_and_breach(cfg):
    offers = [
        offer("Official Site", 1400), offer("Booking.com", 1344), offer("Expedia", 1428), offer("Booking.com", 1500),
        offer("Hotels.com", 1394, board="BB"),
        offer("Trip.com", 1000, currency="USD", free=False),  # 900 EUR
    ]
    comps = {c.board: c for c in build_comparisons(offers, cfg)}
    ai = comps["AI"]
    assert ai.reference_seller == "Official Site" and ai.reference_total == 1400
    by = {r.seller: r for r in ai.rows}
    assert [r.seller for r in ai.rows][0] == "Trip.com" and by["Trip.com"].total == 900
    assert by["Booking.com"].total == 1344  # satıcı başına en ucuz
    assert by["Booking.com"].diff_pct == pytest.approx(-4.0)
    assert by["Booking.com"].parity_breach and by["Trip.com"].parity_breach
    assert not by["Expedia"].parity_breach and not by["Official Site"].parity_breach
    assert by["Official Site"].is_reference and by["Trip.com"].is_cheapest
    assert by["Expedia"].per_person_night == pytest.approx(1428 / 7 / 2)
    assert ai.spread_pct == pytest.approx((1428 - 900) / 900 * 100)
    assert len(comps["BB"].rows) == 1  # farklı pansiyon ayrı grup
    assert not comps["BB"].rows[0].parity_breach  # direkt referans yok -> ihlal tanımsız


def test_tolerance(cfg):
    comps = build_comparisons([offer("Official Site", 1000), offer("Booking.com", 995)], cfg)
    assert not comps[0].rows[0].parity_breach  # %0.5 < tolerans


def test_only_free_cancellation_and_missing_fx(cfg):
    cfg.only_free_cancellation = True
    comps = build_comparisons([offer("Official Site", 1400), offer("Expedia", 1000, free=False), offer("X", 1, currency="TRY")], cfg)
    assert [r.seller for r in comps[0].rows] == ["Official Site"]


def test_is_direct():
    assert is_direct("Swandor Official", ["swandor"]) and not is_direct("Booking.com", ["swandor"])


def test_price_changes(cfg):
    prev = [offer("Booking.com", 1000), offer("Expedia", 1000)]
    curr = [offer("Booking.com", 1100), offer("Expedia", 1000), offer("Trip.com", 900)]
    ch = price_changes(prev, curr, cfg)
    assert len(ch) == 1 and ch[0].seller == "Booking.com" and ch[0].diff_pct == pytest.approx(10)


def _cfg_with(**extra):
    base = {"base_currency": "EUR", "fx": {"EUR": 1}, "direct_sellers": ["hotel site"], "parity_tolerance_pct": 1.0,
            "stays": [{"name": "6N-2AD", "nights": 6, "adults": 2}], "checkin": {"offsets_days": [10]},
            "hotels": [{"id": "h1", "name": "H1", "channels": {"trivago": "u"}, **extra.pop("hotel", {})}]}
    base.update(extra)
    return parse_config(base)


def _offer(seller, total, room="Deluxe Room", free=None, board="AI"):
    return Offer("h1", "trivago", seller, "6N-2AD", date(2026, 11, 6), 6, 2, 0, room, board, total, "EUR", free, True, "u", datetime(2026, 10, 7))


def test_groups_by_cancellation_policy_by_default():
    cfg = _cfg_with()
    offers = [_offer("Hotel Site", 1000, free=False), _offer("Hotel Site", 1200, free=True),
              _offer("Booking.com", 900, free=False), _offer("Booking.com", 1300, free=True), _offer("Expedia", 1100)]
    comps = {c.cancel_policy: c for c in build_comparisons(offers, cfg)}
    assert set(comps) == {"nofree", "free", "unknown"}
    nofree = {r.seller: r for r in comps["nofree"].rows}
    assert nofree["Booking.com"].parity_breach and nofree["Hotel Site"].is_reference     # iadesiz-iadesiz kıyas
    assert not any(r.parity_breach for r in comps["free"].rows)                        # iadeli: 1300 > 1200
    assert [r.seller for r in comps["unknown"].rows] == ["Expedia"]


def test_room_types_map_names_and_drop_unmatched():
    cfg = _cfg_with(hotel={"room_types": {"Deluxe": ["^deluxe (room|oda)\\b", "^delüks"], "Family": "family"}})
    offers = [_offer("Hotel Site", 2000, "Deluxe Room - NR"), _offer("Hotels.com", 1500, "Deluxe Oda, 1 Yatak Odası"),
              _offer("Hotel Site", 3000, "Deluxe Family Room - NR"), _offer("Hotels.com", 2900, "Family Dubleks"),
              _offer("Hotels.com", 100, "Standard Suite")]
    comps = {c.room_type: c for c in build_comparisons(offers, cfg)}
    assert set(comps) == {"Deluxe", "Family"}                       # 'Standard Suite' eşlemeye uymadı, dışarıda
    assert [r.seller for r in comps["Deluxe"].rows] == ["Hotels.com", "Hotel Site"]
    assert comps["Deluxe"].rows[0].parity_breach                     # 1500 < 2000
    assert comps["Family"].rows[0].total == 2900


def test_room_types_config_validation():
    with pytest.raises(ConfigError, match="geçersiz regex"):
        _cfg_with(hotel={"room_types": {"Deluxe": ["("]}})
