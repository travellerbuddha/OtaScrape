"""Gerçek siteye gitmeden uçtan uca denemek için deterministik sahte veri üreticisi."""
from __future__ import annotations

import hashlib
from datetime import datetime

from ..models import Offer, Search
from .base import Adapter

_SELLERS = {
    "booking": ["Booking.com"],
    "expedia": ["Expedia"],
    "tripcom": ["Trip.com"],
    "trivago": ["Official Site", "Booking.com", "Expedia", "Hotels.com", "Trip.com"],
    "check24": ["Official Site", "Booking.com", "Expedia", "HRS"],
    "tripadvisor": ["Official Site", "Booking.com", "Expedia", "Trip.com"],
}
_BOARDS = {"AI": ("All-inclusive", 1.0), "BB": ("Bed and breakfast", 0.78)}


def _unit(*parts: object) -> float:
    """0..1 arası deterministik sayı."""
    digest = hashlib.sha256("|".join(map(str, parts)).encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


class MockAdapter(Adapter):
    def __init__(self, channel: str) -> None:
        self.channel = channel

    def fetch(self, search: Search) -> list[Offer]:
        base_per_night = 120 + _unit(search.hotel_id) * 280
        season = 1 + 0.25 * _unit(search.check_in.isoformat())
        offers: list[Offer] = []
        for seller in _SELLERS.get(self.channel, [self.channel.title()]):
            for board, (label, board_factor) in _BOARDS.items():
                factor = 0.94 + 0.12 * _unit(search.hotel_id, search.check_in, seller, board)
                pax_factor = search.stay.adults / 2 + 0.35 * search.stay.children
                total = round(base_per_night * season * board_factor * factor * pax_factor * search.stay.nights, 2)
                offers.append(
                    Offer(
                        hotel_id=search.hotel_id, channel=self.channel, seller=seller,
                        stay_name=search.stay.name, check_in=search.check_in, nights=search.stay.nights,
                        adults=search.stay.adults, children=search.stay.children,
                        room_name=f"Standard Room ({label})", board=board, total_price=total,
                        currency=search.currency, free_cancellation=_unit(seller, board, search.hotel_id) > 0.4,
                        taxes_included=True, source_url=search.url, scraped_at=datetime.now(),
                    )
                )
        return offers
