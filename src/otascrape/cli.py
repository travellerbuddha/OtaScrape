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
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        cfg = load_config(args.config)
    except (OSError, ConfigError) as exc:
        print(f"Yapılandırma hatası: {exc}", file=sys.stderr)
        return 2

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
