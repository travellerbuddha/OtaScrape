"""Kanallar/acentalar arası fark hesapları ve iki çalıştırma arası fiyat değişimi."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from .config import Config
from .models import Offer
from .pricing import convert, per_night, per_person_night

log = logging.getLogger(__name__)


@dataclass
class Row:
    seller: str
    channel: str
    room_name: str
    free_cancellation: bool | None
    total: float
    per_night: float
    per_person_night: float
    diff_abs: float
    diff_pct: float
    is_reference: bool
    is_cheapest: bool
    parity_breach: bool
    source_url: str


@dataclass
class Comparison:
    hotel_id: str
    check_in: date
    stay_name: str
    nights: int
    adults: int
    children: int
    board: str
    reference_seller: str
    reference_total: float
    rows: list[Row]

    @property
    def spread_pct(self) -> float:
        """En pahalı ile en ucuz arasındaki fark (% olarak, en ucuza göre)."""
        lo, hi = self.rows[0].total, self.rows[-1].total
        return (hi - lo) / lo * 100 if lo else 0.0


@dataclass
class PriceChange:
    hotel_id: str
    seller: str
    check_in: date
    stay_name: str
    board: str
    previous_total: float
    current_total: float
    diff_abs: float
    diff_pct: float


def is_direct(seller: str, direct_sellers: list[str]) -> bool:
    s = seller.lower()
    return any(token in s for token in direct_sellers)


def _converted(offers: list[Offer], cfg: Config) -> list[tuple[Offer, float]]:
    out: list[tuple[Offer, float]] = []
    for o in offers:
        if cfg.only_free_cancellation and o.free_cancellation is not True:
            continue
        total = convert(o.total_price, o.currency, cfg.base_currency, cfg.fx)
        if total is None:
            log.warning("Kur yok, teklif atlandı: %s -> %s (%s)", o.currency, cfg.base_currency, o.seller)
            continue
        out.append((o, total))
    return out


def build_comparisons(offers: list[Offer], cfg: Config) -> list[Comparison]:
    groups: dict[tuple, dict[str, tuple[Offer, float]]] = {}
    for offer, total in _converted(offers, cfg):
        key = (offer.hotel_id, offer.check_in, offer.stay_name, offer.board)
        best = groups.setdefault(key, {})
        seller_key = offer.seller.lower()
        if seller_key not in best or total < best[seller_key][1]:
            best[seller_key] = (offer, total)

    comparisons: list[Comparison] = []
    for (hotel_id, check_in, stay_name, board), by_seller in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2], kv[0][3])):
        entries = sorted(by_seller.values(), key=lambda e: e[1])
        direct = [e for e in entries if is_direct(e[0].seller, cfg.direct_sellers)]
        ref_offer, ref_total = direct[0] if direct else entries[0]
        has_direct_ref = bool(direct)

        rows = []
        for offer, total in entries:
            diff = total - ref_total
            is_ref = offer is ref_offer
            rows.append(
                Row(
                    seller=offer.seller,
                    channel=offer.channel,
                    room_name=offer.room_name,
                    free_cancellation=offer.free_cancellation,
                    total=total,
                    per_night=per_night(total, offer.nights),
                    per_person_night=per_person_night(total, offer.nights, offer.adults, offer.children, cfg.pax_basis),
                    diff_abs=diff,
                    diff_pct=diff / ref_total * 100 if ref_total else 0.0,
                    is_reference=is_ref,
                    is_cheapest=total == entries[0][1],
                    parity_breach=(
                        has_direct_ref
                        and not is_direct(offer.seller, cfg.direct_sellers)
                        and ref_total > 0
                        and diff / ref_total * 100 < -cfg.parity_tolerance_pct
                    ),
                    source_url=offer.source_url,
                )
            )
        first = entries[0][0]
        comparisons.append(
            Comparison(
                hotel_id=hotel_id, check_in=check_in, stay_name=stay_name, nights=first.nights,
                adults=first.adults, children=first.children, board=board,
                reference_seller=ref_offer.seller, reference_total=ref_total, rows=rows,
            )
        )
    return comparisons


def _cheapest_by_key(offers: list[Offer], cfg: Config) -> dict[tuple, float]:
    best: dict[tuple, float] = {}
    for offer, total in _converted(offers, cfg):
        key = (offer.hotel_id, offer.seller, offer.check_in, offer.stay_name, offer.board)
        if key not in best or total < best[key]:
            best[key] = total
    return best


def price_changes(previous: list[Offer], current: list[Offer], cfg: Config) -> list[PriceChange]:
    prev = _cheapest_by_key(previous, cfg)
    curr = _cheapest_by_key(current, cfg)
    changes = []
    for key, now_total in curr.items():
        before = prev.get(key)
        if before is None or before == now_total:
            continue
        hotel_id, seller, check_in, stay_name, board = key
        diff = now_total - before
        changes.append(
            PriceChange(hotel_id, seller, check_in, stay_name, board, before, now_total, diff, diff / before * 100 if before else 0.0)
        )
    return sorted(changes, key=lambda c: abs(c.diff_pct), reverse=True)
