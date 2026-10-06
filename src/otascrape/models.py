from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta


@dataclass(frozen=True)
class Stay:
    """Aranacak konaklama şekli. `adults`/`children` tüm odalardaki toplam kişi sayısıdır."""

    name: str
    nights: int
    adults: int
    children: int = 0
    child_ages: tuple[int, ...] = ()
    rooms: int = 1


@dataclass(frozen=True)
class Search:
    """Bir adaptörün tek seferde çalıştıracağı arama: otel x kanal x tarih x konaklama."""

    hotel_id: str
    hotel_name: str
    channel: str
    url: str
    check_in: date
    stay: Stay
    currency: str
    options: dict = field(default_factory=dict, compare=False, hash=False)

    @property
    def check_out(self) -> date:
        return self.check_in + timedelta(days=self.stay.nights)


@dataclass
class Offer:
    """Bir kanalda görülen tek bir fiyat satırı. `seller`, metasearch'te acenta/reklamveren,
    OTA'da ise OTA'nın kendisidir."""

    hotel_id: str
    channel: str
    seller: str
    stay_name: str
    check_in: date
    nights: int
    adults: int
    children: int
    room_name: str
    board: str
    total_price: float
    currency: str
    free_cancellation: bool | None
    taxes_included: bool | None
    source_url: str
    scraped_at: datetime
