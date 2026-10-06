"""Trivago, Check24, TripAdvisor, Expedia ve Trip.com adaptörleri.

DENEYSEL: Bu kanallar geliştirme ortamından bot engeli (HTTP 403) nedeniyle gerçek sayfada
denenemedi. Veri, sayfanın XHR/gömülü JSON'larından `jsonscan` ile sezgisel olarak çıkarılır.
İlk kullanımdan önce `otascrape probe` ile yakalama alıp sonucu kontrol edin.

URL: Yerleşik oluşturucu yalnızca kaynaklarla doğrulanan Trivago ve Trip.com için vardır.
Diğerleri için config'teki URL'de yer tutucu kullanın, ör.
  https://.../hotel?...&checkin={check_in}&checkout={check_out:%d.%m.%Y}&adults={adults}
"""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from ..models import Offer, Search
from ..normalize import detect_free_cancellation, detect_taxes_included, normalize_board
from .base import FatalScrapeError, ScrapeError
from .browser import BrowserAdapter, PageCapture, render_template
from .jsonscan import scan_deals

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
    channel = "trivago"

    def build_builtin_url(self, search: Search) -> str:
        if search.stay.children or search.stay.rooms != 1:
            raise FatalScrapeError("trivago: çocuklu/çok odalı arama için URL şablonu kullanın (rc- biçimi doğrulanmadı)")
        parts = urlsplit(search.url)
        match = re.search(r"(?:^|&)search=([^&]+)", parts.query)
        token = unquote(match.group(1)).split(";")[0] if match else ""
        if not re.fullmatch(r"\d+-\d+", token):
            raise FatalScrapeError("trivago: URL'de 'search=<tip>-<id>' bulunamadı; Trivago'da otel sayfasını açıp tam URL'yi kopyalayın")
        s = search
        value = f"{token};dr-{s.check_in:%Y%m%d}-{s.check_out:%Y%m%d};rc-1-{s.stay.adults}"
        return urlunsplit((parts.scheme, parts.netloc, parts.path, f"search={value}", parts.fragment))  # fragment (#::hasInteracted=true gibi) korunur


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
