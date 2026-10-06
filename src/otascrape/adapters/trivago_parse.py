"""Trivago yanıtlarını `Offer`'a çeviren saf (tarayıcısız) ayrıştırıcı.

Şema, gerçek bir yakalamadan (Antalya, `accommodationDealsQuery`) gözlemlenen yapıya dayanır:
  data.getAccommodationDeals.deals[]      -> otelin tüm acenta teklifleri
  data.accommodationSearchResponse.accommodations[].deals.{best,cheapest,alternatives[]} -> kısa liste (yedek)
  data.getAdvertiserDetails.advertiserDetails[] -> acente kimliği -> ad
Teklifte acente yalnızca sayısal kimlikle gelir; ad ayrı yanıtlardan ve sayfadaki
`advertiser-details-<id>` + `advertiser-name` öğelerinden birleştirilir.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Any

from bs4 import BeautifulSoup

from ..models import Offer, Search
from .base import FatalScrapeError, ScrapeError

log = logging.getLogger(__name__)

# Etiket kodları (ns, id). YALNIZCA iki teklifin DOM etiketleriyle karşılaştırılarak çıkarıldı:
# 411:5 = Her şey dahil, 412:1 = Ücretsiz iptal. Diğer kodlar bilinmiyor ve loglanır.
BOARD_CODES: dict[tuple[int, int], str] = {(411, 5): "AI"}
FREE_CANCEL_CODES: set[tuple[int, int]] = {(412, 1)}
KNOWN_NAMESPACES = (411, 412)

_STAY = re.compile(r'"stayPeriod":\{"arrival":"(\d{4}-\d{2}-\d{2})","departure":"(\d{4}-\d{2}-\d{2})"')
_ROOMS = re.compile(r'"rooms":\[\{"adults":(\d+),"children":\[([^\]]*)\]')
_CURRENCY = re.compile(r'"currency":"([A-Z]{3})"')
_TARGET = re.compile(r"search=(\d+)-(\d+)")


def target_id(url: str) -> int | None:
    m = _TARGET.search(url)
    return int(m.group(2)) if m else None


def slug_from_url(url: str) -> str | None:
    path = url.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    slug = path.rsplit("/", 1)[-1]
    return slug or None


def request_info(post: str) -> dict[str, Any]:
    """İstek gövdesinden tarih/kişi/para birimi (bulunamayanlar eksik kalır)."""
    info: dict[str, Any] = {}
    if m := _STAY.search(post):
        info["arrival"], info["departure"] = date.fromisoformat(m.group(1)), date.fromisoformat(m.group(2))
    if m := _ROOMS.search(post):
        info["adults"] = int(m.group(1))
        info["children"] = len([x for x in m.group(2).split(",") if x.strip()])
    if m := _CURRENCY.search(post):
        info["currency"] = m.group(1)
    return info


def _dig(node: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def advertiser_names(blobs: list[Any], html: str) -> dict[int, str]:
    names: dict[int, str] = {}
    # 1) sayfadaki satırlar: [advertiser-details-<id>] ile aynı satırdaki advertiser-name
    if html:
        soup = BeautifulSoup(html, "html.parser")
        for el in soup.find_all(attrs={"data-testid": re.compile(r"^advertiser-details-\d+$")}):
            adv_id = int(el["data-testid"].rsplit("-", 1)[1])
            row = el
            for parent in el.parents:
                found = parent.find(attrs={"data-testid": "advertiser-name"})
                if found is not None:
                    text = found.get_text(" ", strip=True)
                    if text:
                        names.setdefault(adv_id, text)
                    break
                row = parent
    # 2) getAdvertiserDetails yanıtları (öncelikli)
    for blob in blobs:
        for item in _dig(blob, "data", "getAdvertiserDetails", "advertiserDetails") or []:
            adv_id, name = _dig(item, "nsid", "id"), _dig(item, "translatedName", "value")
            if isinstance(adv_id, int) and isinstance(name, str) and name.strip():
                names[adv_id] = name.strip()
    return names


def _collect(blobs: list[Any], sources: list[dict[str, str]], target: int | None) -> list[tuple[dict, dict]]:
    """(teklif, istek bilgisi) çiftleri. Tam liste (accommodationDealsQuery) varsa o; yoksa arama yanıtındaki kısa liste."""
    full: list[tuple[dict, dict]] = []
    short: list[tuple[dict, dict]] = []
    for i, blob in enumerate(blobs):
        info = request_info(sources[i].get("post", "")) if i < len(sources) else {}
        deals = _dig(blob, "data", "getAccommodationDeals", "deals")
        if isinstance(deals, list):
            full.extend((d, info) for d in deals if isinstance(d, dict) and _matches(d, target))
        for acc in _dig(blob, "data", "accommodationSearchResponse", "accommodations") or []:
            if target is not None and _dig(acc, "nsid", "id") != target:
                continue
            block = acc.get("deals") or {}
            for d in [block.get("best"), block.get("cheapest"), *(block.get("alternatives") or [])]:
                if isinstance(d, dict):
                    short.append((d, info))
    return full or short


def _matches(deal: dict, target: int | None) -> bool:
    return target is None or _dig(deal, "accommodationDetails", "nsid", "id") in (target, None)


def _codes(deal: dict) -> list[tuple[int, int]]:
    out = []
    for item in deal.get("enrichedPriceAttributesTranslated") or []:
        ns, idv = _dig(item, "nsid", "ns"), _dig(item, "nsid", "id")
        if isinstance(ns, int) and isinstance(idv, int):
            out.append((ns, idv))
    return out


def _warn_missing_advertisers(html: str, offers: list[Offer]) -> None:
    """Fiyat panelinde görünen ama okunan tekliflerde olmayan acenteleri uyarı olarak yazar
    (panel eksik yüklenmiş ya da ayrıştırma bir şeyi kaçırmış olabilir)."""
    if not html:
        return
    soup = BeautifulSoup(html, "html.parser")
    shown = {el.get_text(" ", strip=True) for el in
             soup.select('[data-testid="all-slideout-deals"] [data-testid="advertiser-name"]')}
    have = {o.seller.casefold() for o in offers}
    missing = sorted(n for n in shown if n and n.casefold() not in have)
    if missing:
        log.warning("trivago: panelde görünen %d acente okunan tekliflerde yok: %s", len(missing), ", ".join(missing))


def parse_trivago(blobs: list[Any], sources: list[dict[str, str]], html: str, search: Search) -> list[Offer]:
    target = target_id(search.url)
    pairs = _collect(blobs, sources, target)
    if not pairs:
        raise ScrapeError(
            "trivago: teklif verisi yakalanamadı (accommodationDealsQuery yok). Tarih seçilmemiş ya da "
            "'Show all prices' açılmamış olabilir; `otascrape probe --headed` ile sayfayı izleyin")

    stay = search.stay
    names = advertiser_names(blobs, html)
    offers: list[Offer] = []
    seen: set[str] = set()
    unknown: set[tuple[int, int]] = set()
    now = datetime.now()

    for deal, info in pairs:
        # Sayfa istediğimizden farklı tarih/kişiyle aradıysa fiyatlar yanlış konaklamanın fiyatıdır
        if "arrival" in info and (info["arrival"], info["departure"]) != (search.check_in, search.check_out):
            raise ScrapeError(f"trivago: sayfa farklı tarih kullandı (istenen {search.check_in}→{search.check_out}, "
                              f"sayfa {info['arrival']}→{info['departure']})")
        if "adults" in info and (info["adults"], info["children"]) != (stay.adults, stay.children):
            raise ScrapeError(f"trivago: sayfa farklı kişi sayısı kullandı (istenen {stay.adults}+{stay.children}, "
                              f"sayfa {info['adults']}+{info['children']})")

        deal_id = str(deal.get("id") or id(deal))
        if deal_id in seen:
            continue
        seen.add(deal_id)

        total_native = _dig(deal, "allInPricePerStay", "amount") or _dig(deal, "pricePerStayObject", "amount")
        eur_night = _dig(deal, "pricePerNight", "eurocents") or _dig(deal, "allInPricePerNight", "eurocents")
        if search.currency == "EUR" and eur_night:
            total, currency = round(eur_night / 100 * stay.nights, 2), "EUR"   # Trivago'nun kendi EUR çevrimi
        elif total_native and info.get("currency"):
            total, currency = float(total_native), info["currency"]
        elif total_native and search.options.get("currency"):
            total, currency = float(total_native), str(search.options["currency"]).upper()
        else:
            raise FatalScrapeError("trivago: fiyat para birimi belirlenemedi; kanal ayarına currency: TRY ekleyin")
        if not total or total <= 0:
            continue

        codes = _codes(deal)
        unknown.update(c for c in codes if c[0] in KNOWN_NAMESPACES and c not in BOARD_CODES and c not in FREE_CANCEL_CODES)
        board = next((BOARD_CODES[c] for c in codes if c in BOARD_CODES), None)
        if board is None:
            # Pansiyon kodu var ama anlamı bilinmiyor: 'UNKNOWN' yapılırsa default_board (ör. AI) yanlışlıkla
            # uygulanır. Ham kodla (T411-1) ayrı grupta kalır ve raporda görünür.
            meal = next((c for c in codes if c[0] == 411), None)
            board = f"T{meal[0]}-{meal[1]}" if meal else "UNKNOWN"
        free = True if any(c in FREE_CANCEL_CODES for c in codes) or _dig(deal, "priceDetails", "freeCancellationDeadline") else None

        adv_id = _dig(deal, "advertiserDetails", "nsid", "id")
        seller = names.get(adv_id) or f"Trivago acente #{adv_id}"
        offers.append(Offer(
            hotel_id=search.hotel_id, channel=search.channel, seller=seller, stay_name=stay.name,
            check_in=search.check_in, nights=stay.nights, adults=stay.adults, children=stay.children,
            room_name=(deal.get("description") or "?").strip(), board=board, total_price=total, currency=currency,
            free_cancellation=free, taxes_included=True, source_url=search.url, scraped_at=now,
        ))

    _warn_missing_advertisers(html, offers)
    if unknown:
        log.warning("trivago: anlamı bilinmeyen etiket kodları %s — pansiyon/iptal bunlara göre belirlenemedi; "
                    "'inspect --deal-rows' ile karşılaştırıp trivago_parse.py'ye ekleyin",
                    ", ".join(f"{ns}:{i}" for ns, i in sorted(unknown)))
    return offers
