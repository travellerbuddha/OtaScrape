from __future__ import annotations

import logging
from collections.abc import Callable

from ..config import ScraperSettings
from .base import Adapter, BlockedError, ScrapeError
from .mock import MockAdapter

log = logging.getLogger(__name__)

__all__ = ["Adapter", "AdapterRegistry", "BlockedError", "ScrapeError", "KNOWN_CHANNELS"]

# Planlanan ama henüz yazılmamış kanallar: config'te geçerler, çalıştırmada net bir hata verirler.
KNOWN_CHANNELS = ("booking", "expedia", "tripcom", "trivago", "check24", "tripadvisor")


def _booking(settings: ScraperSettings) -> Adapter:
    from .booking import BookingAdapter

    return BookingAdapter(settings)


def _scan(name: str) -> Callable[[ScraperSettings], Adapter]:
    def factory(settings: ScraperSettings) -> Adapter:
        from . import scan_channels

        return getattr(scan_channels, name)(settings)

    return factory


# Yeni kanal: bir Adapter alt sınıfı yazıp buraya `kanal_adı: fabrika` olarak ekleyin.
_FACTORIES: dict[str, Callable[[ScraperSettings], Adapter]] = {
    "booking": _booking,
    "trivago": _scan("TrivagoAdapter"),
    "check24": _scan("Check24Adapter"),
    "tripadvisor": _scan("TripAdvisorAdapter"),
    "expedia": _scan("ExpediaAdapter"),
    "tripcom": _scan("TripComAdapter"),
}


class AdapterRegistry:
    """Kanal adına göre adaptörü tembel oluşturur ve çalışma bitince kapatır."""

    def __init__(self, settings: ScraperSettings, mock: bool = False) -> None:
        self._settings = settings
        self._mock = mock
        self._adapters: dict[str, Adapter] = {}

    def get(self, channel: str) -> Adapter:
        if channel not in self._adapters:
            if self._mock:
                self._adapters[channel] = MockAdapter(channel)
            elif channel in _FACTORIES:
                self._adapters[channel] = _FACTORIES[channel](self._settings)
                if getattr(self._adapters[channel], "experimental", False):
                    log.warning("'%s' adaptörü DENEYSEL: gerçek sayfada doğrulanmadı. Sonuçları 'otascrape probe' çıktısıyla karşılaştırın.", channel)
            elif channel in KNOWN_CHANNELS:
                raise ScrapeError(f"'{channel}' adaptörü henüz yazılmadı")
            else:
                raise ScrapeError(f"Bilinmeyen kanal: '{channel}'")
        return self._adapters[channel]

    def close(self) -> None:
        for adapter in self._adapters.values():
            adapter.close()
        self._adapters.clear()
