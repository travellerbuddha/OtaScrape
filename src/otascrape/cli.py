from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from . import db
from .compare import build_comparisons, price_changes
from .config import ConfigError, load_config
from .report import write_excel, write_html
from .runner import run_scrape


def _make_reports(cfg, conn, run_id: int, out_dir: Path) -> tuple[Path, Path, int]:
    offers = db.load_offers(conn, run_id)
    errors = db.load_errors(conn, run_id)
    comparisons = build_comparisons(offers, cfg)
    prev_id = db.latest_run_id(conn, before=run_id)
    changes = price_changes(db.load_offers(conn, prev_id), offers, cfg) if prev_id else []
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    xlsx, html = out_dir / f"rapor_{stamp}.xlsx", out_dir / f"rapor_{stamp}.html"
    write_excel(xlsx, cfg, offers, comparisons, changes, errors)
    write_html(html, cfg, comparisons, changes, errors, datetime.now())
    breaches = sum(r.parity_breach for c in comparisons for r in c.rows)
    return xlsx, html, breaches


def _probe(cfg, args) -> int:
    from datetime import date

    from .adapters import AdapterRegistry
    from .config import build_searches
    from .models import Search

    searches = [x for x in build_searches(cfg, date.today()) if x.hotel_id == args.hotel and x.channel == args.channel]
    if args.stay:
        searches = [x for x in searches if x.stay.name == args.stay]
    if args.check_in:
        searches = [x for x in searches if x.check_in.isoformat() == args.check_in]
    if args.url:
        template = searches[0] if searches else build_searches(cfg, date.today())[0]
        options = dict(template.options) if searches else {}
        if args.price_basis:
            options["price_basis"] = args.price_basis
        searches = [Search(args.hotel, args.hotel, args.channel, args.url, template.check_in, template.stay,
                           cfg.search_currency, options)]
    if not searches:
        print("Eşleşen otel/kanal/konaklama/tarih bulunamadı (config'i kontrol edin veya --url verin).", file=sys.stderr)
        return 2
    if "ORNEK" in searches[0].url.upper():
        print("UYARI: URL hâlâ örnek değer gibi görünüyor; gerçek otel sayfası URL'sini kullanın.", file=sys.stderr)
    if args.headed:
        cfg.scraper.headless = False
    registry = AdapterRegistry(cfg.scraper)
    try:
        adapter = registry.get(args.channel)
        if not hasattr(adapter, "probe"):
            print(f"'{args.channel}' adaptörü probe desteklemiyor.", file=sys.stderr)
            return 2
        summary = adapter.probe(searches[0], Path(cfg.scraper.debug_dir))
    finally:
        registry.close()
    for key, value in summary.items():
        print(f"{key}: {value}")
    return 1 if "error" in summary else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="otascrape", description="OTA fiyat takip ve karşılaştırma")
    parser.add_argument("-c", "--config", default="config.yaml", help="YAML yapılandırma dosyası")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="tara, kaydet ve rapor üret")
    run.add_argument("--mock", action="store_true", help="siteye gitmeden sahte veriyle dene")
    run.add_argument("--no-report", action="store_true")
    rep = sub.add_parser("report", help="mevcut bir taramadan rapor üret")
    rep.add_argument("--run-id", type=int, help="varsayılan: son tamamlanan tarama")
    probe = sub.add_parser("probe", help="tek bir aramayı yakala: HTML + JSON'u kaydet, ayrıştırmayı dene")
    probe.add_argument("--hotel", required=True, help="config'teki otel id")
    probe.add_argument("--channel", required=True)
    probe.add_argument("--stay", help="konaklama adı (varsayılan: ilk)")
    probe.add_argument("--check-in", help="YYYY-MM-DD (varsayılan: ilk giriş tarihi)")
    probe.add_argument("--url", help="config yerine bu kanal URL'sini kullan (otel/kanal config'te olmasa da çalışır)")
    probe.add_argument("--price-basis", choices=["total", "per_night"], help="sayfadaki fiyat toplam mı gecelik mi")
    probe.add_argument("--headed", action="store_true", help="tarayıcı penceresini göster (bot engelini azaltabilir)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        cfg = load_config(args.config)
    except (OSError, ConfigError) as exc:
        print(f"Yapılandırma hatası: {exc}", file=sys.stderr)
        return 2

    if args.cmd == "probe":
        return _probe(cfg, args)

    conn = db.connect(cfg.database)
    out_dir = Path(cfg.output_dir)
    if args.cmd == "run":
        run_id = run_scrape(cfg, conn, mock=args.mock)
        n_err = len(db.load_errors(conn, run_id))
        print(f"Tarama #{run_id} bitti: {len(db.load_offers(conn, run_id))} teklif, {n_err} hata")
        if args.no_report:
            return 0 if n_err == 0 else 1
        xlsx, html, breaches = _make_reports(cfg, conn, run_id, out_dir)
        print(f"Parite ihlali: {breaches}\nExcel: {xlsx}\nHTML:  {html}")
        return 0 if n_err == 0 else 1

    run_id = args.run_id or db.latest_run_id(conn)
    if run_id is None:
        print("Veritabanında tamamlanmış tarama yok. Önce 'run' çalıştırın.", file=sys.stderr)
        return 2
    xlsx, html, breaches = _make_reports(cfg, conn, run_id, out_dir)
    print(f"Tarama #{run_id}\nParite ihlali: {breaches}\nExcel: {xlsx}\nHTML:  {html}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
