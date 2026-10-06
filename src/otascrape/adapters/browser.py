"""Playwright tabanlı adaptörler için ortak katman: tarayıcı yaşam döngüsü, engel tespiti,
retry, hata anında HTML dökümü ve `probe` (yakalama) desteği."""
from __future__ import annotations

import json
import os
import random
import re
import time
from abc import abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import ScraperSettings
from ..models import Offer, Search
from .base import Adapter, BlockedError, FatalScrapeError, ScrapeError
from .jsonscan import scripts_json

_BLOCK_RE = re.compile(
    r"captcha|awswaf|aws-waf|challenge-container|access denied|datadome|px-captcha|"
    r"are you a robot|unusual traffic|pardon our interruption|robot olmadığını", re.I)
_MAX_RESPONSES = 80


@dataclass
class PageCapture:
    url: str
    status: int | None
    html: str
    blobs: list[Any] = field(default_factory=list)


def render_template(url: str, search: Search) -> str:
    """`{check_in}`, `{check_out:%Y%m%d}`, `{nights}`, `{adults}`, `{children}`, `{child_ages}`,
    `{rooms}`, `{currency}` yer tutucularını doldurur."""
    stay = search.stay
    if stay.children and "{children}" not in url:
        raise FatalScrapeError(f"{search.channel}: çocuklu konaklama aranıyor ama URL şablonunda {{children}} yok")
    context = {
        "check_in": search.check_in, "check_out": search.check_out, "nights": stay.nights,
        "adults": stay.adults, "children": stay.children, "rooms": stay.rooms,
        "child_ages": ",".join(str(a) for a in stay.child_ages), "currency": search.currency,
    }
    try:
        return url.format(**context)
    except (KeyError, IndexError, ValueError) as exc:
        raise FatalScrapeError(f"{search.channel}: URL şablonu geçersiz ({exc}); kullanılabilir yer tutucular: {', '.join(context)}") from exc


class BrowserAdapter(Adapter):
    experimental = False
    wait_selector: str | None = None

    def __init__(self, settings: ScraperSettings) -> None:
        self._s = settings
        self._pw = None
        self._browser = None
        self._context = None

    # -- alt sınıfların yazacağı kısım --------------------------------------------
    @abstractmethod
    def build_url(self, search: Search) -> str: ...

    @abstractmethod
    def parse(self, capture: PageCapture, search: Search) -> list[Offer]: ...

    # -- tarayıcı -----------------------------------------------------------------
    def _ensure_browser(self) -> None:
        if self._context is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover
            raise FatalScrapeError("Playwright kurulu değil: pip install '.[browser]' && playwright install chromium") from exc
        self._pw = sync_playwright().start()
        proxy = None
        if self._s.proxy_server:
            proxy = {"server": self._s.proxy_server}
            if self._s.proxy_username_env:
                proxy["username"] = os.environ.get(self._s.proxy_username_env, "")
            if self._s.proxy_password_env:
                proxy["password"] = os.environ.get(self._s.proxy_password_env, "")
        self._browser = self._pw.chromium.launch(
            headless=self._s.headless, proxy=proxy,
            executable_path=os.environ.get("OTASCRAPE_CHROMIUM") or None)
        self._context = self._browser.new_context(locale="en-US", timezone_id="Europe/Istanbul", viewport={"width": 1366, "height": 900})

    def fetch_page(self, url: str) -> PageCapture:
        self._ensure_browser()
        page = self._context.new_page()
        responses: list[Any] = []
        page.on("response", lambda r: responses.append(r) if len(responses) < _MAX_RESPONSES else None)
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=self._s.timeout_seconds * 1000)
            status = resp.status if resp else None
            if self.wait_selector:
                try:
                    page.wait_for_selector(self.wait_selector, timeout=15_000)
                except Exception:  # yoksa parse() nedenini ayırt eder
                    pass
            else:
                try:
                    page.wait_for_load_state("networkidle", timeout=15_000)
                except Exception:
                    pass
            blobs: list[Any] = []
            for r in responses:
                try:
                    if r.status == 200 and "json" in r.headers.get("content-type", ""):
                        blobs.append(r.json())
                except Exception:  # gövde artık okunamıyor olabilir
                    continue
            html = page.content()
            blobs.extend(scripts_json(html))
            return PageCapture(url=url, status=status, html=html, blobs=blobs)
        finally:
            page.close()

    @staticmethod
    def check_blocked(capture: PageCapture) -> None:
        if capture.status in (401, 403, 429):
            raise BlockedError(f"HTTP {capture.status} (bot engeli)")
        if len(capture.html) < 20_000 and _BLOCK_RE.search(capture.html):
            raise BlockedError("Bot doğrulama sayfası döndü (captcha/WAF)")

    # -- Adapter arayüzü ----------------------------------------------------------
    def fetch(self, search: Search) -> list[Offer]:
        url = self.build_url(search)  # yapılandırma hataları burada, retry olmadan yükselir
        last_error: Exception | None = None
        for _ in range(self._s.retries + 1):
            capture: PageCapture | None = None
            try:
                capture = self.fetch_page(url)
                self.check_blocked(capture)
                return self.parse(capture, search)
            except (BlockedError, FatalScrapeError):
                self._dump(capture, search)
                raise  # engelde/yapılandırma hatasında tekrar denemek işe yaramaz
            except Exception as exc:
                last_error = exc
                self._dump(capture, search)
                time.sleep(random.uniform(*self._s.delay_seconds))
        raise ScrapeError(f"{self._s.retries + 1} denemede başarısız: {last_error}") from last_error

    def probe(self, search: Search, out_dir: Path) -> dict[str, Any]:
        """Sayfayı bir kez açar, HTML + JSON yanıtlarını diske yazar ve ayrıştırmayı dener."""
        url = self.build_url(search)
        capture = self.fetch_page(url)
        stem = f"probe_{self.channel}_{search.hotel_id}_{search.check_in}_{search.stay.name}"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{stem}.html").write_text(capture.html, encoding="utf-8")
        (out_dir / f"{stem}.json").write_text(json.dumps(capture.blobs, ensure_ascii=False, default=str)[:20_000_000], encoding="utf-8")
        summary: dict[str, Any] = {"url": url, "status": capture.status, "html_bytes": len(capture.html),
                                   "json_blobs": len(capture.blobs), "files": str(out_dir / stem) + ".{html,json}"}
        try:
            self.check_blocked(capture)
            summary["offers"] = len(self.parse(capture, search))
        except Exception as exc:
            summary["error"] = f"{type(exc).__name__}: {exc}"
        return summary

    def _dump(self, capture: PageCapture | None, search: Search) -> None:
        if capture is None or not capture.html:
            return
        path = Path(self._s.debug_dir)
        path.mkdir(parents=True, exist_ok=True)
        (path / f"{self.channel}_{search.hotel_id}_{search.check_in}_{search.stay.name}.html").write_text(capture.html, encoding="utf-8")

    def close(self) -> None:
        if self._context is not None:
            self._context.close()
        if self._browser is not None:
            self._browser.close()
        if self._pw is not None:
            self._pw.stop()
        self._context = self._browser = self._pw = None
