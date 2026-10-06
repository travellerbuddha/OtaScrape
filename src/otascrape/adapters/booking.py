"""Booking.com adaptörü (pilot kanal).

Parça parça test edilebilsin diye üç aşamaya ayrıldı: `build_url` -> `fetch_html` (Playwright)
-> `parse_offers` (saf HTML ayrıştırma). Booking'in HTML yapısı sık değişir; seçiciler
`ROW_SELECTORS` ve `PRICE_SELECTORS` içinde toplandı, bozulduğunda yalnızca orayı güncelleyin.
Hata anında HTML `scraper.debug_dir` altına kaydedilir.
"""
from __future__ import annotations

import os
import random
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag

from ..config import ScraperSettings
from ..models import Offer, Search
from ..normalize import detect_free_cancellation, detect_taxes_included, normalize_board, parse_price
from .base import Adapter, BlockedError, ScrapeError

ROW_SELECTORS = ["table.hprt-table tr[data-block-id]", "#hprt-table tr[data-block-id]"]
ROOM_NAME_SELECTORS = [".hprt-roomtype-link", "[data-testid='room-name']", ".hprt-roomtype-icon-link"]
PRICE_SELECTORS = [
    "[data-testid='price-and-discounted-price']",
    ".bui-price-display__value",
    ".prco-valign-middle-helper",
    ".prco-text-nowrap-helper",
]
_NO_AVAILABILITY = re.compile(r"no availability|not available|sold out|müsait değil|uygun oda yok", re.I)
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
        text = soup.get_text(" ", strip=True)
        if _BLOCKED.search(html):
            raise BlockedError("Booking botu engelledi (captcha/WAF)")
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


class BookingAdapter(Adapter):
    channel = "booking"

    def __init__(self, settings: ScraperSettings) -> None:
        self._s = settings
        self._pw = None
        self._browser = None
        self._context = None

    # -- tarayıcı -----------------------------------------------------------------
    def _ensure_browser(self) -> None:
        if self._context is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover
            raise ScrapeError("Playwright kurulu değil: pip install '.[browser]' && playwright install chromium") from exc
        self._pw = sync_playwright().start()
        proxy = None
        if self._s.proxy_server:
            proxy = {"server": self._s.proxy_server}
            if self._s.proxy_username_env:
                proxy["username"] = os.environ.get(self._s.proxy_username_env, "")
            if self._s.proxy_password_env:
                proxy["password"] = os.environ.get(self._s.proxy_password_env, "")
        self._browser = self._pw.chromium.launch(headless=self._s.headless, proxy=proxy)
        self._context = self._browser.new_context(locale="en-US", timezone_id="Europe/Istanbul", viewport={"width": 1366, "height": 900})

    def fetch_html(self, url: str) -> str:
        self._ensure_browser()
        page = self._context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=self._s.timeout_seconds * 1000)
            try:
                page.wait_for_selector(", ".join(ROW_SELECTORS), timeout=15_000)
            except Exception:  # tablo yoksa parse_offers neden yok olduğunu ayırt eder
                pass
            return page.content()
        finally:
            page.close()

    # -- arama --------------------------------------------------------------------
    def fetch(self, search: Search) -> list[Offer]:
        url = build_url(search)
        last_error: Exception | None = None
        for attempt in range(self._s.retries + 1):
            html = ""
            try:
                html = self.fetch_html(url)
                return parse_offers(html, search)
            except BlockedError:
                self._dump(html, search)
                raise  # engellenmişken tekrar denemek durumu kötüleştirir
            except Exception as exc:
                last_error = exc
                self._dump(html, search)
                time.sleep(random.uniform(*self._s.delay_seconds))
        raise ScrapeError(f"{self._s.retries + 1} denemede başarısız: {last_error}") from last_error

    def _dump(self, html: str, search: Search) -> None:
        if not html:
            return
        path = Path(self._s.debug_dir)
        path.mkdir(parents=True, exist_ok=True)
        name = f"booking_{search.hotel_id}_{search.check_in}_{search.stay.name}.html"
        (path / name).write_text(html, encoding="utf-8")

    def close(self) -> None:
        if self._context is not None:
            self._context.close()
        if self._browser is not None:
            self._browser.close()
        if self._pw is not None:
            self._pw.stop()
        self._context = self._browser = self._pw = None
