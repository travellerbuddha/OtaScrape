from datetime import date, datetime
from pathlib import Path

import pytest
import yaml

from otascrape import db
from otascrape.adapters import AdapterRegistry
from otascrape.adapters.base import BlockedError, FatalScrapeError, ScrapeError
from otascrape.adapters.browser import PageCapture, render_template
from otascrape.adapters.jsonscan import scan_deals, scripts_json
from otascrape.adapters.scan_channels import Check24Adapter, ExpediaAdapter, TrivagoAdapter, TripComAdapter
from otascrape.cli import main
from otascrape.config import ScraperSettings
from otascrape.models import Offer, Search, Stay
from otascrape.runner import apply_default_board


def make_search(channel="trivago", url="https://www.trivago.com/en-US/oar/hotel-x?search=100-12345", stay=None, **options):
    return Search("h1", "Hotel 1", channel, url, date(2026, 11, 1), stay or Stay("7N-2AD", 7, 2), "EUR", options)


# SENTETİK örnek: gerçek Trivago yanıtı değildir; sezgisel tarayıcının mantığını sınar.
TRIVAGO_LIKE = {"data": {"deals": [
    {"advertiser": {"id": 1, "name": "Booking.com"}, "price": {"amount": 210.0, "currency": "EUR"}, "label": "Free cancellation · Breakfast included"},
    {"advertiserName": "Official Site", "pricePerNight": "€ 195", "label": "All-inclusive"},
    {"partnerName": "Expedia", "totalPrice": 1500, "currencyCode": "eur"},
    {"advertiser": {"name": "NoPrice"}, "price": None},
    {"name": "Hotel record without advertiser", "price": 99},
]}}


def test_scan_advertiser_mode():
    deals = list(scan_deals(TRIVAGO_LIKE))
    got = {d.seller: (d.amount, d.currency, d.basis) for d in deals}
    assert got == {"Booking.com": (210.0, "EUR", "unknown"), "Official Site": (195.0, "EUR", "per_night"), "Expedia": (1500.0, "EUR", "total")}
    assert "Free cancellation" in next(d for d in deals if d.seller == "Booking.com").text


def test_scan_room_mode():
    blob = [{"roomName": "Deluxe", "price": {"value": "3,450.00", "currency": "USD"}, "mealPlan": "All inclusive"}, {"title": "x", "price": 5}]
    deals = list(scan_deals(blob, "room"))
    assert len(deals) == 1 and deals[0].room == "Deluxe" and deals[0].amount == 3450 and deals[0].currency == "USD"


def test_scripts_json():
    html = '<script type="application/ld+json">{"a": 1}</script><script id="__NEXT_DATA__" type="x">{"b": 2}</script><script type="application/json">{bad</script>'
    assert scripts_json(html) == [{"a": 1}, {"b": 2}]


def adapter(cls):
    return cls(ScraperSettings(retries=0))


def test_parse_requires_price_basis_when_ambiguous():
    cap = PageCapture("u", 200, "<html></html>", [{"advertiser": "Booking.com", "price": 210}])
    s = make_search()
    with pytest.raises(FatalScrapeError, match="price_basis"):
        adapter(TrivagoAdapter).parse(cap, s)
    offers = adapter(TrivagoAdapter).parse(cap, make_search(price_basis="per_night"))
    assert offers[0].total_price == 1470 and offers[0].seller == "Booking.com"
    offers = adapter(TrivagoAdapter).parse(cap, make_search(price_basis="total"))
    assert offers[0].total_price == 210


def test_parse_full_trivago_like():
    cap = PageCapture("u", 200, "<html></html>", [TRIVAGO_LIKE])
    offers = {o.seller: o for o in adapter(TrivagoAdapter).parse(cap, make_search(price_basis="per_night"))}
    assert offers["Expedia"].total_price == 1500                 # açık toplam alanı: kanal ayarından etkilenmez
    assert offers["Official Site"].total_price == 195 * 7 and offers["Official Site"].board == "AI"
    assert offers["Booking.com"].free_cancellation is True and offers["Booking.com"].board == "BB"
    assert offers["Booking.com"].currency == "EUR"


def test_parse_empty_and_no_availability():
    with pytest.raises(ScrapeError, match="probe"):
        adapter(TrivagoAdapter).parse(PageCapture("u", 200, "<html>garip</html>", []), make_search())
    assert adapter(TrivagoAdapter).parse(PageCapture("u", 200, "<html>Sorry, sold out</html>", []), make_search()) == []


def test_room_mode_fixed_seller():
    cap = PageCapture("u", 200, "", [{"rooms": [{"roomName": "Suite", "totalPrice": 2000, "currency": "EUR"}]}])
    offers = adapter(ExpediaAdapter).parse(cap, make_search("expedia", "https://x/{check_in}"))
    assert [(o.seller, o.room_name, o.total_price) for o in offers] == [("Expedia", "Suite", 2000)]


def test_render_template():
    s = make_search(url="https://h.test/x?in={check_in}&out={check_out:%d.%m.%Y}&a={adults}&n={nights}&c={children}&cur={currency}")
    assert render_template(s.url, s) == "https://h.test/x?in=2026-11-01&out=08.11.2026&a=2&n=7&c=0&cur=EUR"
    kids = make_search(url="https://h.test/x?a={adults}", stay=Stay("k", 7, 2, 1, (7,)))
    with pytest.raises(FatalScrapeError, match="children"):
        render_template(kids.url, kids)
    with pytest.raises(FatalScrapeError, match="yer tutucu"):
        render_template("https://h.test/{bogus}", s)
    kids2 = make_search(url="https://h.test/?c={children}&ages={child_ages}", stay=Stay("k", 7, 2, 2, (7, 4)))
    assert render_template(kids2.url, kids2) == "https://h.test/?c=2&ages=7,4"


def test_trivago_builtin_url():
    url = adapter(TrivagoAdapter).build_url(make_search(url="https://www.trivago.com/en-US/oar/hotel-x?search=100-12345;dr-20260101-20260102;rc-1-1&foo=1"))
    assert url == "https://www.trivago.com/en-US/oar/hotel-x?search=100-12345;dr-20261101-20261108;rc-1-2"
    with pytest.raises(FatalScrapeError, match="search="):
        adapter(TrivagoAdapter).build_url(make_search(url="https://www.trivago.com/en-US/oar/hotel-x"))
    with pytest.raises(FatalScrapeError, match="çocuklu"):
        adapter(TrivagoAdapter).build_url(make_search(stay=Stay("k", 7, 2, 1, (7,))))


def test_tripcom_builtin_url():
    s = make_search("tripcom", "https://www.trip.com/hotels/detail/?cityId=286&hotelId=2843746&checkIn=2026-01-01&checkOut=2026-01-02")
    url = adapter(TripComAdapter).build_url(s)
    for part in ("cityId=286", "hotelId=2843746", "checkIn=2026-11-01", "checkOut=2026-11-08", "adult=2", "crn=1", "curr=EUR"):
        assert part in url
    with pytest.raises(FatalScrapeError, match="hotelId"):
        adapter(TripComAdapter).build_url(make_search("tripcom", "https://www.trip.com/hotels/detail/"))


def test_channels_without_builtin_builder_need_template():
    with pytest.raises(FatalScrapeError, match="yer tutucu"):
        adapter(Check24Adapter).build_url(make_search("check24", "https://hotel.check24.de/hotel/x"))
    assert adapter(Check24Adapter).build_url(make_search("check24", "https://hotel.check24.de/x?d={check_in}")).endswith("d=2026-11-01")


@pytest.mark.parametrize("status,html,blocked", [
    (403, "<html>Access Denied</html>", True), (429, "", True), (202, "<script>awswaf</script>", True),
    (200, "<html>captcha</html>", True), (200, "<html>normal</html>", False), (200, "captcha " + "x" * 30_000, False),
])
def test_check_blocked(status, html, blocked):
    cap = PageCapture("u", status, html, [])
    if blocked:
        with pytest.raises(BlockedError):
            TrivagoAdapter.check_blocked(cap)
    else:
        TrivagoAdapter.check_blocked(cap)


def test_default_board():
    def o(board):
        return Offer("h", "trivago", "s", "st", date(2026, 1, 1), 7, 2, 0, "r", board, 1, "EUR", None, None, "u", datetime.now())
    offers = [o("UNKNOWN"), o("BB")]
    apply_default_board(offers, make_search(default_board="ai"))
    assert [x.board for x in offers] == ["AI", "BB"]
    offers = [o("UNKNOWN")]
    apply_default_board(offers, make_search())
    assert offers[0].board == "UNKNOWN"


def test_registry_warns_experimental(caplog):
    reg = AdapterRegistry(ScraperSettings())
    with caplog.at_level("WARNING"):
        a = reg.get("trivago")
    assert a.experimental and "DENEYSEL" in caplog.text
    reg.close()


def test_blocked_channel_is_skipped_after_first_block(tmp_path, monkeypatch):
    calls = []

    def fake_fetch(self, search):
        calls.append(search.check_in)
        raise BlockedError("HTTP 403")

    monkeypatch.setattr(TrivagoAdapter, "fetch", fake_fetch)
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump({
        "database": str(tmp_path / "t.db"), "output_dir": str(tmp_path / "out"),
        "stays": [{"nights": 7, "adults": 2}], "checkin": {"offsets_days": [10, 20, 30]},
        "scraper": {"delay_seconds": [0, 0]},
        "hotels": [{"id": "a", "channels": {"trivago": "https://t/x?search=100-1"}}],
    }))
    assert main(["-c", str(p), "run", "--no-report"]) == 1
    assert len(calls) == 1  # ilk engelden sonra kalan 2 arama atlandı
    errors = db.load_errors(db.connect(tmp_path / "t.db"), 1)
    assert [("atlandı" in e["message"]) for e in errors] == [False, True, True]


def test_probe_command(tmp_path, monkeypatch, capsys):
    html = "<html>" + "x" * 100 + "</html>"
    monkeypatch.setattr(TrivagoAdapter, "fetch_page", lambda self, url: PageCapture(url, 200, html, [TRIVAGO_LIKE]))
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump({
        "database": str(tmp_path / "t.db"), "output_dir": str(tmp_path / "out"), "scraper": {"debug_dir": str(tmp_path / "dbg")},
        "stays": [{"name": "7N-2AD", "nights": 7, "adults": 2}], "checkin": {"offsets_days": [10]},
        "hotels": [{"id": "a", "channels": {"trivago": {"url": "https://t/x?search=100-1", "price_basis": "per_night"}}}],
    }))
    assert main(["-c", str(p), "probe", "--hotel", "a", "--channel", "trivago"]) == 0
    out = capsys.readouterr().out
    assert "offers: 3" in out and "json_blobs: 1" in out
    files = sorted(x.suffix for x in (tmp_path / "dbg").glob("probe_trivago_*"))
    assert files == [".html", ".json"]
    assert main(["-c", str(p), "probe", "--hotel", "zzz", "--channel", "trivago"]) == 2
