"""Trivago, Check24, TripAdvisor, Expedia ve Trip.com adaptörleri.

DENEYSEL: Bu kanallar geliştirme ortamından bot engeli (HTTP 403) nedeniyle gerçek sayfada
denenemedi. Veri, sayfanın XHR/gömülü JSON'larından `jsonscan` ile sezgisel olarak çıkarılır.
İlk kullanımdan önce `otascrape probe` ile yakalama alıp sonucu kontrol edin.

URL: Yerleşik oluşturucu yalnızca kaynaklarla doğrulanan Trivago ve Trip.com için vardır.
Diğerleri için config'teki URL'de yer tutucu kullanın, ör.
  https://.../hotel?...&checkin={check_in}&checkout={check_out:%d.%m.%Y}&adults={adults}
"""
from __future__ import annotations

import logging
import re
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from ..models import Offer, Search
from ..normalize import detect_free_cancellation, detect_taxes_included, normalize_board
from .base import FatalScrapeError, ScrapeError
from .browser import BrowserAdapter, PageCapture, render_template
from .jsonscan import scan_deals
from .trivago_parse import parse_trivago, slug_from_url

log = logging.getLogger(__name__)

_NO_AVAILABILITY = re.compile(r"no availability|not available|sold out|müsait değil|uygun oda yok|keine verfügbarkeit", re.I)


class ScanAdapter(BrowserAdapter):
    experimental = True
    mode = "advertiser"           # 'advertiser' (metasearch) | 'room' (tek satıcılı OTA)
    scroll = True
    settle_ms = 2500
    fixed_seller: str | None = None

    # -- URL ----------------------------------------------------------------------
    def build_url(self, search: Search) -> str:
        if "{" in search.url:
            return render_template(search.url, search)
        return self.build_builtin_url(search)

    def build_builtin_url(self, search: Search) -> str:
        raise FatalScrapeError(
            f"{self.channel}: yerleşik URL oluşturucu yok. Sitede bir arama yapıp URL'yi kopyalayın ve "
            "tarihleri/kişileri {check_in}, {check_out}, {adults}, {children}, {nights} yer tutucularıyla değiştirin")

    # -- ayrıştırma ---------------------------------------------------------------
    def parse(self, capture: PageCapture, search: Search) -> list[Offer]:
        deals = [d for blob in capture.blobs for d in scan_deals(blob, self.mode)]
        if not deals:
            if _NO_AVAILABILITY.search(capture.html) and len(capture.html) < 200_000:
                return []
            raise ScrapeError(f"{self.channel}: JSON'larda fiyat bulunamadı ({len(capture.blobs)} blok). "
                              "`otascrape probe` ile yakalama alıp inceleyin")
        opts = search.options
        offers: list[Offer] = []
        seen: set[tuple] = set()
        for deal in deals:
            basis = deal.basis if deal.basis != "unknown" else opts.get("price_basis")
            if basis is None:
                raise FatalScrapeError(
                    f"{self.channel}: fiyat alanı '{deal.price_key}' toplam mı gecelik mi belirsiz. "
                    "Kanal ayarına price_basis: total|per_night ekleyin (probe çıktısına bakarak karar verin)")
            total = deal.amount if basis == "total" else deal.amount * search.stay.nights
            seller = deal.seller or self.fixed_seller or self.channel
            board = normalize_board(deal.text)
            free = detect_free_cancellation(deal.text)
            key = (seller, round(total, 2), deal.room, board, free)
            if key in seen:
                continue
            seen.add(key)
            offers.append(Offer(
                hotel_id=search.hotel_id, channel=search.channel, seller=seller, stay_name=search.stay.name,
                check_in=search.check_in, nights=search.stay.nights, adults=search.stay.adults,
                children=search.stay.children, room_name=deal.room or "?", board=board,
                total_price=round(total, 2), currency=deal.currency or opts.get("currency") or search.currency,
                free_cancellation=free, taxes_included=detect_taxes_included(deal.text),
                source_url=search.url, scraped_at=_now(),
            ))
        return offers


def _now():
    from datetime import datetime
    return datetime.now()


class TrivagoAdapter(ScanAdapter):
    """Trivago: arama `drs-40` ile kullanıcı seçimi gibi yapılır, ardından ilgili otelin 'Show all prices'
    paneli açılır ve sayfanın kendi `accommodationDealsQuery` yanıtı okunur (bkz. trivago_parse)."""

    channel = "trivago"
    experimental = False  # gerçek sayfada uçtan uca doğrulandı (Antalya, 20 teklif)

    def build_builtin_url(self, search: Search) -> str:
        if search.stay.children or search.stay.rooms != 1:
            raise FatalScrapeError("trivago: çocuklu/çok odalı arama için URL şablonu kullanın (rc- biçimi doğrulanmadı)")
        parts = urlsplit(search.url)
        match = re.search(r"(?:^|&)search=([^&]+)", parts.query)
        token = unquote(match.group(1)).split(";")[0] if match else ""
        if not re.fullmatch(r"\d+-\d+", token):
            raise FatalScrapeError("trivago: URL'de 'search=<tip>-<id>' bulunamadı; Trivago'da otel sayfasını açıp tam URL'yi kopyalayın")
        s = search
        # Biçim, tarihleri elle seçince sayfanın ürettiği URL'nin aynısıdır: dr-…;drs-40;rc-1-N
        value = f"{token};dr-{s.check_in:%Y%m%d}-{s.check_out:%Y%m%d};drs-40;rc-1-{s.stay.adults}"
        return urlunsplit((parts.scheme, parts.netloc, parts.path, f"search={value}", parts.fragment))  # fragment (#::hasInteracted=true gibi) korunur

    # -- sayfa etkileşimi ---------------------------------------------------------
    def after_load(self, page, search) -> None:
        self._dismiss_consent(page)
        try:
            page.wait_for_selector('[data-testid="accommodation-list-element"]', timeout=25_000)
        except Exception:
            log.warning("trivago: otel listesi görünmedi")
            return
        cards = page.locator('[data-testid="accommodation-list-element"]')
        card = cards.first
        slug = slug_from_url(search.url) if search else None
        if slug:
            mine = cards.filter(has=page.locator(f'a[href*="{slug}"]'))
            if mine.count():
                card = mine.first
        button = card.locator('[data-testid="additional-prices-slideout-entry-point"]').first
        if button.count() == 0:
            log.warning("trivago: 'Show all prices' düğmesi bulunamadı; yalnızca kısa teklif listesi okunabilir")
            return
        try:
            button.scroll_into_view_if_needed(timeout=5_000)
            try:
                button.click(timeout=8_000)
            except Exception:  # üstte bir katman (çerez bandı vb.) tıklamayı engelliyor olabilir
                button.dispatch_event("click")
            page.wait_for_selector('[data-testid="all-slideout-deals"]', timeout=15_000)
            page.wait_for_timeout(1_500)
        except Exception as exc:
            log.warning("trivago: fiyat paneli açılamadı: %s", exc)

    @staticmethod
    def _dismiss_consent(page) -> None:
        for selector in ('[data-testid="uc-deny-all-button"]', 'button:has-text("Deny")', 'button:has-text("Reject all")'):
            try:
                page.locator(selector).first.click(timeout=2_500)
                page.wait_for_timeout(500)
                return
            except Exception:
                continue

    def parse(self, capture: PageCapture, search: Search) -> list[Offer]:
        return parse_trivago(capture.blobs, capture.sources, capture.html, search)


class TripComAdapter(ScanAdapter):
    channel = "tripcom"
    mode = "room"
    fixed_seller = "Trip.com"

    def build_builtin_url(self, search: Search) -> str:
        if search.stay.children:
            raise FatalScrapeError("tripcom: çocuklu arama için URL şablonu kullanın (çocuk yaşı parametresi doğrulanmadı)")
        parts = urlsplit(search.url)
        query = dict(parse_qsl(parts.query))
        if "hotelId" not in query:
            raise FatalScrapeError("tripcom: URL'de hotelId yok; Trip.com otel sayfasının tam URL'sini kopyalayın")
        query.update({"checkIn": search.check_in.isoformat(), "checkOut": search.check_out.isoformat(),
                      "adult": str(search.stay.adults), "children": "0", "crn": str(search.stay.rooms),
                      "curr": search.currency})
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


class Check24Adapter(ScanAdapter):
    channel = "check24"


class TripAdvisorAdapter(ScanAdapter):
    channel = "tripadvisor"


class ExpediaAdapter(ScanAdapter):
    channel = "expedia"
    mode = "room"
    fixed_seller = "Expedia"
