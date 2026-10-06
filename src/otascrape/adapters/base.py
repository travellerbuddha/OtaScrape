from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Offer, Search


class ScrapeError(Exception):
    """Sayfa okunamadı / beklenen yapı bulunamadı."""


class FatalScrapeError(ScrapeError):
    """Yapılandırma/desteklenmeyen durum kaynaklı hata: tekrar denemek anlamsız (retry yapılmaz)."""


class BlockedError(ScrapeError):
    """Kanal botu engelledi (captcha, WAF, 403...). Proxy/yöntem değişikliği gerekir."""


class Adapter(ABC):
    """Bir kanal için tek bir aramayı çalıştırıp `Offer` listesi döndürür.

    Yeni kanal eklemek için bu sınıfı türetin ve `adapters/__init__.py` içindeki
    `_FACTORIES` sözlüğüne kaydedin. Boş liste = müsaitlik yok; hata = exception.
    """

    channel: str

    @abstractmethod
    def fetch(self, search: Search) -> list[Offer]: ...

    def close(self) -> None:  # noqa: B027 - opsiyonel kaynak temizliği
        pass
