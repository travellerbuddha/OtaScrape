import logging
import os
from datetime import date, datetime

import pytest

from otascrape.adapters.base import FatalScrapeError, ScrapeError
from otascrape.adapters.browser import PageCapture
from otascrape.adapters.scan_channels import TrivagoAdapter
from otascrape.adapters.trivago_parse import parse_trivago, request_info, slug_from_url, target_id
from otascrape.config import ScraperSettings
from otascrape.models import Offer, Search, Stay
from otascrape.runner import filter_rooms

URL = "https://www.trivago.com.tr/en-US/lm/swandor-hotels-resorts-topkapi-palace-antalya?search=100-151839;dr-20261012-20261018;drs-40"
POST = ('{"variables":{"getAccommodationDealsParams":{"accommodationNsid":{"ns":100,"id":151839},'
        '"stayPeriod":{"arrival":"2026-10-12","departure":"2026-10-18"},"rooms":[{"adults":2,"children":[]}],"currency":"TRY",'
        '"channel":{"branded":{"isStandardDate":false,"stayPeriodSource":{"value":40}}}}}}')


def deal(adv_id, desc, total, night, eur_night, codes, deal_id, acc=151839, deadline=None):
    return {
        "advertiserDetails": {"nsid": {"id": adv_id, "ns": 400}},
        "accommodationDetails": {"nsid": {"id": acc, "ns": 100}},
        "enrichedPriceAttributesTranslated": [{"nsid": {"ns": ns, "id": i}} for ns, i in codes],
        "description": desc,
        "allInPricePerNight": {"amount": night, "eurocents": eur_night},
        "allInPricePerStay": {"amount": total, "eurocents": None},
        "pricePerNight": {"amount": night, "eurocents": eur_night},
        "pricePerStayObject": {"amount": total, "eurocents": None},
        "priceDetails": {"freeCancellationDeadline": deadline, "roomInfo": [], "rewardRate": None},
        "id": deal_id,
    }


# SENTETİK: gerçek yakalamada gözlenen alan yapısına göre kurulmuştur; değerler uydurmadır.
DEALS = {"data": {"getAccommodationDeals": {"deals": [
    deal(3008, "Deluxe Oda - Özel Fiyatlı Ürünler Deluxe Oda", 93293, 15549, 28198, [(411, 5), (412, 1), (413, 2)], "d1"),
    deal(395, "Delüks Oda", 82268, 13711, 24866, [(411, 5), (412, 2)], "d2"),
    deal(3570, "deluxe room - book direct", 136585, 22764, 41283, [(411, 5)], "d3"),
    deal(9999, "Standart Oda", 70000, 11667, 21000, [(411, 7)], "d4", deadline="2026-10-10T14:59:00Z"),
    deal(3008, "Deluxe Oda (kopya)", 93293, 15549, 28198, [(411, 5)], "d1"),                 # aynı teklif kimliği
    deal(3008, "Başka otel", 1000, 100, 200, [(411, 5)], "x9", acc=555),                    # başka otelin teklifi
]}}}
ADV = [{"data": {"getAdvertiserDetails": {"advertiserDetails": [{"nsid": {"ns": 400, "id": i}, "translatedName": {"value": n}}]}}}
       for i, n in ((3008, "Setur"), (395, "Agoda"))]
HTML = ('<html><body><li><div data-testid="advertiser-details-3570"></div><span data-testid="advertiser-name">Hotel Site</span></li>'
        '<li><div data-testid="advertiser-details-395"></div><span data-testid="advertiser-name">Agoda (sayfa)</span></li></body></html>')


def search(currency="EUR", stay=None, url=URL, options=None, **opts):
    return Search("topkapi", "Topkapı", "trivago", url, date(2026, 10, 12), stay or Stay("6N-2AD", 6, 2), currency, {**opts, **(options or {})})


def run(blobs=None, sources=None, **kw):
    blobs = blobs if blobs is not None else [*ADV, DEALS]
    sources = sources if sources is not None else [{}] * len(ADV) + [{"post": POST}]
    return parse_trivago(blobs, sources, HTML, search(**kw))


def test_helpers():
    assert target_id(URL) == 151839 and target_id("https://x/y") is None
    assert slug_from_url(URL) == "swandor-hotels-resorts-topkapi-palace-antalya"
    info = request_info(POST)
    assert info == {"arrival": date(2026, 10, 12), "departure": date(2026, 10, 18), "adults": 2, "children": 0, "currency": "TRY"}


def test_parse_offers_in_eur_via_eurocents():
    offers = {o.seller: o for o in run()}
    assert set(offers) == {"Setur", "Agoda", "Hotel Site", "Trivago acente #9999"}      # kopya ve başka otel elendi
    setur = offers["Setur"]
    assert setur.total_price == round(28198 / 100 * 6, 2) and setur.currency == "EUR"
    assert setur.board == "AI" and setur.free_cancellation is True and setur.taxes_included is True
    assert setur.room_name.startswith("Deluxe Oda") and setur.nights == 6 and setur.check_in == date(2026, 10, 12)
    assert offers["Agoda"].free_cancellation is None                                    # 412:2 anlamı bilinmiyor -> None
    assert offers["Agoda"].seller == "Agoda"                                            # getAdvertiserDetails, sayfa adından öncelikli
    assert offers["Hotel Site"].total_price == round(41283 / 100 * 6, 2)               # ad yalnızca DOM'da
    assert offers["Trivago acente #9999"].board == "T411-7" and offers["Trivago acente #9999"].free_cancellation is True   # bilinmeyen kod AI'ya karışmaz


def test_native_currency_when_search_currency_is_not_eur():
    offers = {o.seller: o for o in run(currency="TRY")}
    assert offers["Setur"].total_price == 93293.0 and offers["Setur"].currency == "TRY"


def test_unknown_codes_are_logged(caplog):
    with caplog.at_level(logging.WARNING):
        run()
    assert "411:7" in caplog.text and "413" not in caplog.text


def test_date_or_party_mismatch_is_an_error():
    wrong_dates = POST.replace("2026-10-12", "2026-10-21").replace("2026-10-18", "2026-10-27")
    with pytest.raises(ScrapeError, match="farklı tarih"):
        run(sources=[{}] * len(ADV) + [{"post": wrong_dates}])
    with pytest.raises(ScrapeError, match="kişi sayısı"):
        run(sources=[{}] * len(ADV) + [{"post": POST.replace('"adults":2', '"adults":3')}])


def test_no_deals_data_is_an_error():
    with pytest.raises(ScrapeError, match="accommodationDealsQuery"):
        run(blobs=[*ADV], sources=[{}] * len(ADV))


def test_falls_back_to_search_response_for_target_hotel_only():
    search_blob = {"data": {"accommodationSearchResponse": {"accommodations": [
        {"nsid": {"id": 111, "ns": 100}, "deals": {"best": deal(1, "Başka", 5, 1, 1, [], "o1", acc=111), "alternatives": []}},
        {"nsid": {"id": 151839, "ns": 100}, "deals": {
            "best": deal(3008, "Deluxe Oda", 93293, 15549, 28198, [(411, 5)], "s1"),
            "cheapest": deal(395, "Delüks Oda", 82268, 13711, 24866, [(411, 5)], "s2"),
            "alternatives": [deal(395, "Delüks Oda", 82268, 13711, 24866, [(411, 5)], "s2")]}}]}}}
    offers = run(blobs=[*ADV, search_blob], sources=[{}] * len(ADV) + [{"post": POST.replace("getAccommodationDealsParams", "params")}])
    assert sorted(o.seller for o in offers) == ["Agoda", "Setur"]


def test_missing_currency_is_fatal_when_not_eur():
    no_currency = [{}] * len(ADV) + [{"post": '{"variables":{}}'}]
    with pytest.raises(FatalScrapeError, match="currency"):
        run(currency="USD", sources=no_currency)
    offers = {o.seller: o for o in run(currency="USD", sources=no_currency, options={"currency": "try"})}
    assert offers["Setur"].total_price == 93293.0 and offers["Setur"].currency == "TRY"


def test_adapter_parse_uses_capture_sources():
    adapter = TrivagoAdapter(ScraperSettings())
    capture = PageCapture(URL, 200, HTML, [*ADV, DEALS], [{}] * len(ADV) + [{"post": POST}])
    assert len(adapter.parse(capture, search())) == 4


def test_filter_rooms():
    def o(room):
        return Offer("h", "trivago", "s", "st", date(2026, 1, 1), 7, 2, 0, room, "AI", 1, "EUR", None, None, "u", datetime.now())
    offers = [o("Deluxe Oda"), o("Delüks Oda"), o("Standart Oda"), o("Deluxe Room - Sea View")]
    assert [x.room_name for x in filter_rooms(offers, search(room_include="deluxe|delüks"))] == ["Deluxe Oda", "Delüks Oda", "Deluxe Room - Sea View"]
    assert [x.room_name for x in filter_rooms(offers, search(room_include="deluxe|delüks", room_exclude="sea"))] == ["Deluxe Oda", "Delüks Oda"]
    assert len(filter_rooms(offers, search())) == 4


FAKE_PAGE = """
<button data-testid="uc-deny-all-button" onclick="this.remove()">Deny</button>
<div data-testid="accommodation-list-element"><a href="/en-US/oar/other-hotel">Other</a>
  <button data-testid="additional-prices-slideout-entry-point" onclick="document.title='other'">Show all prices</button></div>
<div data-testid="accommodation-list-element"><a href="/en-US/oar/swandor-hotels-resorts-topkapi-palace-antalya">Mine</a>
  <button data-testid="additional-prices-slideout-entry-point"
   onclick="document.title='mine';setTimeout(()=>document.body.insertAdjacentHTML('beforeend','<ul data-testid=&quot;all-slideout-deals&quot;></ul>'),300)">Show all prices</button></div>
"""


def test_after_load_clicks_the_right_card_in_a_real_browser():
    playwright = pytest.importorskip("playwright.sync_api")
    pw = playwright.sync_playwright().start()
    try:
        try:
            browser = pw.chromium.launch(headless=True, executable_path=os.environ.get("OTASCRAPE_CHROMIUM") or None)
        except Exception as exc:  # tarayıcı kurulu değil
            pytest.skip(f"Chromium yok: {exc}")
        page = browser.new_page()
        page.set_content(FAKE_PAGE)
        TrivagoAdapter(ScraperSettings()).after_load(page, search())
        assert page.title() == "mine"
        assert page.locator('[data-testid="all-slideout-deals"]').count() == 1
        assert page.locator('[data-testid="uc-deny-all-button"]').count() == 0
        browser.close()
    finally:
        pw.stop()


def test_deal_without_meal_code_stays_unknown():
    blob = {"data": {"getAccommodationDeals": {"deals": [deal(3008, "Oda", 93293, 15549, 28198, [(412, 1)], "n1")]}}}
    offers = parse_trivago([*ADV, blob], [{}] * len(ADV) + [{"post": POST}], HTML, search())
    assert [o.board for o in offers] == ["UNKNOWN"]       # default_board yalnız bu durumda devreye girer


def test_warns_about_advertisers_visible_on_page_but_missing_from_json(caplog):
    html = ('<ul data-testid="all-slideout-deals">'
            '<li data-testid="deal-list-item"><span data-testid="advertiser-name">Setur</span></li>'
            '<li data-testid="deal-list-item"><span data-testid="advertiser-name">Hotels.com</span></li></ul>')
    with caplog.at_level(logging.WARNING):
        parse_trivago([*ADV, DEALS], [{}] * len(ADV) + [{"post": POST}], html, search())
    assert "Hotels.com" in caplog.text and "Setur" not in caplog.text.split("okunan tekliflerde yok:")[1]


def test_wait_until_list_settles_waits_for_slow_items():
    from otascrape.adapters.scan_channels import wait_until_list_settles
    playwright = pytest.importorskip("playwright.sync_api")
    pw = playwright.sync_playwright().start()
    try:
        try:
            browser = pw.chromium.launch(headless=True, executable_path=os.environ.get("OTASCRAPE_CHROMIUM") or None)
        except Exception as exc:
            pytest.skip(f"Chromium yok: {exc}")
        page = browser.new_page()
        page.set_content("""<ul id="l"></ul><script>let i=0;const t=setInterval(()=>{
            document.getElementById('l').insertAdjacentHTML('beforeend','<li>x</li>'); if(++i>=5) clearInterval(t);}, 900);</script>""")
        # Liste 4.5 sn boyunca büyüyor; 2 sn sabit kalınca bitmiş sayılır -> 5 öğe görülmeli
        assert wait_until_list_settles(page, "#l li", quiet_s=2, max_s=15) == 5
        browser.close()
    finally:
        pw.stop()
