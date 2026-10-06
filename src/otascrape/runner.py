from __future__ import annotations

import logging
import random
import re
import time
from datetime import date, datetime

from . import db
from .adapters import AdapterRegistry, BlockedError
from .config import Config, build_searches

log = logging.getLogger(__name__)


def apply_default_board(offers, search) -> None:
    """Pansiyon tipi okunamayan teklifler için kanal ayarındaki `default_board` değerini uygular
    (ör. her şey dahil oteller için 'AI'); yoksa 'UNKNOWN' kalır ve ayrı grupta karşılaştırılır."""
    default = search.options.get("default_board")
    if default:
        for offer in offers:
            if offer.board == "UNKNOWN":
                offer.board = str(default).upper()


def filter_rooms(offers, search):
    """Kanal ayarındaki `room_include` / `room_exclude` (regex, büyük/küçük harf duyarsız) ile oda tiplerini
    süzer. Farklı oda tiplerinin fiyatlarını kıyaslamamak için kullanılır (ör. yalnız 'deluxe|delüks')."""
    include, exclude = search.options.get("room_include"), search.options.get("room_exclude")
    kept = offers
    if include:
        pattern = re.compile(str(include), re.I)
        kept = [o for o in kept if pattern.search(o.room_name)]
    if exclude:
        pattern = re.compile(str(exclude), re.I)
        kept = [o for o in kept if not pattern.search(o.room_name)]
    return kept


def run_scrape(config: Config, conn, mock: bool = False, sleep=time.sleep, today: date | None = None) -> int:
    """Tüm aramaları çalıştırır, sonuçları DB'ye yazar ve run_id döndürür.

    Bir arama hata verirse kaydedilir ve devam edilir. Bir kanal engellenirse (BlockedError)
    o kanalın kalan aramaları atlanır; böylece engel büyütülmez.
    """
    today = today or date.today()
    searches = build_searches(config, today)
    run_id = db.start_run(conn, datetime.now())
    registry = AdapterRegistry(config.scraper, mock=mock)
    blocked_channels: set[str] = set()
    failures = 0
    try:
        for i, search in enumerate(searches, 1):
            label = f"[{i}/{len(searches)}] {search.hotel_id} {search.channel} {search.check_in} {search.stay.name}"
            if search.channel in blocked_channels:
                db.save_error(conn, run_id, search, "kanal engellendiği için atlandı", datetime.now())
                failures += 1
                continue
            try:
                offers = registry.get(search.channel).fetch(search)
                apply_default_board(offers, search)
                before = len(offers)
                offers = filter_rooms(offers, search)
                if len(offers) != before:
                    log.info("%s: %d teklif oda filtresi (room_include/room_exclude) nedeniyle elendi, %d kaldı",
                             label, before - len(offers), len(offers))
                db.save_offers(conn, run_id, offers)
                log.info("%s -> %d teklif", label, len(offers))
            except Exception as exc:
                failures += 1
                if isinstance(exc, BlockedError):
                    blocked_channels.add(search.channel)
                log.error("%s -> HATA: %s", label, exc)
                db.save_error(conn, run_id, search, str(exc), datetime.now())
            if not mock:
                sleep(random.uniform(*config.scraper.delay_seconds))
    finally:
        registry.close()
    status = "ok" if failures == 0 else ("partial" if failures < len(searches) else "failed")
    db.finish_run(conn, run_id, status, datetime.now())
    return run_id
