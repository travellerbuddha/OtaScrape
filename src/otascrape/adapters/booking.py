"""Booking.com adaptörü (pilot kanal).

Parça parça test edilebilsin diye üç aşamaya ayrıldı: `build_url` -> `BrowserAdapter.fetch_page` (Playwright)
-> `parse_offers` (saf HTML ayrıştırma). Booking'in HTML yapısı sık değişir; seçiciler
`ROW_SELECTORS` ve `PRICE_SELECTORS` içinde toplandı, bozulduğunda yalnızca orayı güncelleyin.
Hata anında HTML `scraper.debug_dir` altına kaydedilir.
"""
from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag

from ..models import Offer, Search
from ..normalize import detect_free_cancellation, detect_taxes_included, normalize_board, parse_price
from .base import BlockedError, ScrapeError
from .browser import BrowserAdapter, PageCapture

ROW_SELECTORS = ["table.hprt-table tr[data-block-id]", "#hprt-table tr[data-block-id]"]
ROOM_NAME_SELECTORS = [".hprt-roomtype-link", "[data-testid='room-name']", ".hprt-roomtype-icon-link"]
PRICE_SELECTORS = [
    "[data-testid='price-and-discounted-price']",
    ".bui-price-display__value",
    ".prco-valign-middle-helper",
    ".prco-text-nowrap-helper",
]
_NO_AVAILABILITY = re.compile(r"no availability|sold out for your dates|müsait değil|uygun oda yok", re.I)
_DATES_NOT_APPLIED = re.compile(r"select dates to see|please enter your dates|tarih seçin", re.I)
_BLOCKED = re.compile(r"captcha|awswaf|aws-waf|challenge-container|are you a robot|robot olmadığını", re.I)

SELLER = "Booking.com"


def build_url(search: Search) -> str:
    parts = urlsplit(search.url)
    params = {
        "checkin": search.check_in.isoformat(),
        "checkout": search.check_out.isoformat(),
        "group_adults": search.stay.adults,
        "group_children": search.stay.children,
        "no_rooms": search.stay.rooms,
        "selected_currency": search.currency,
        "lang": "en-us",
    }
    query = urlencode(params)
    for age in search.stay.child_ages:
        query += f"&age={age}"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def _first_text(node: Tag, selectors: list[str]) -> str | None:
    for sel in selectors:
        found = node.select_one(sel)
        if found and found.get_text(strip=True):
            return found.get_text(" ", strip=True)
    return None


def parse_offers(html: str, search: Search, scraped_at: datetime | None = None) -> list[Offer]:
    """Booking otel sayfasındaki oda/fiyat tablosunu `Offer` listesine çevirir."""
    scraped_at = scraped_at or datetime.now()
    soup = BeautifulSoup(html, "html.parser")
    rows: list[Tag] = []
    for sel in ROW_SELECTORS:
        rows = soup.select(sel)
        if rows:
            break

    if not rows:
        # 'awswaf' gibi ifadeler gerçek sayfalarda da geçer: engel yalnızca kısa (doğrulama) sayfalarda sayılır
        if len(html) < 20_000 and _BLOCKED.search(html):
            raise BlockedError("Booking botu engelledi (captcha/WAF)")
        for tag in soup(["script", "style"]):  # JS içindeki sabit metinler görünür içerik değildir
            tag.decompose()
        text = soup.get_text(" ", strip=True)
        if _DATES_NOT_APPLIED.search(text):
            raise ScrapeError("Booking tarih/kişi parametrelerini uygulamadı (sayfa 'Select dates' diyor); "
                              "URL parametreleri yönlendirmede düşmüş olabilir")
        if _NO_AVAILABILITY.search(text):
            return []
        raise ScrapeError("Booking fiyat tablosu bulunamadı (seçiciler güncel olmayabilir)")

    offers: list[Offer] = []
    room_name = ""
    for row in rows:
        name = _first_text(row, ROOM_NAME_SELECTORS)
        if name:  # aynı oda tipinin sonraki satırlarında ad tekrarlanmaz (rowspan)
            room_name = name
        price_text = _first_text(row, PRICE_SELECTORS)
        if not price_text:
            continue  # bu satırda fiyat yok (ör. "müsait değil" satırı)
        amount, currency = parse_price(price_text)
        row_text = row.get_text(" ", strip=True)
        offers.append(
            Offer(
                hotel_id=search.hotel_id, channel=search.channel, seller=SELLER,
                stay_name=search.stay.name, check_in=search.check_in, nights=search.stay.nights,
                adults=search.stay.adults, children=search.stay.children, room_name=room_name or "?",
                board=normalize_board(row_text), total_price=amount,
                currency=currency or search.currency,
                free_cancellation=detect_free_cancellation(row_text),
                taxes_included=detect_taxes_included(row_text),
                source_url=search.url, scraped_at=scraped_at,
            )
        )
    if not offers:
        raise ScrapeError("Booking tablosu bulundu ama hiçbir satırda fiyat okunamadı")
    return offers


class BookingAdapter(BrowserAdapter):
    channel = "booking"
    wait_selector = ", ".join(ROW_SELECTORS)

    def build_url(self, search: Search) -> str:
        return build_url(search)

    def parse(self, capture: PageCapture, search: Search) -> list[Offer]:
        return parse_offers(capture.html, search)
