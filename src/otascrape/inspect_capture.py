"""`otascrape inspect`: bir `probe` yakalamasını kısa, yapıştırılabilir bir özete çevirir.

Amaç, ayrıştırıcıyı yazmak için gereken bilgiyi (hangi istek, hangi JSON yolu, hangi örnek değerler)
büyük dosyaları paylaşmadan çıkarmaktır.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

_KEY_HINT = re.compile(r"price|rate|amount|cost|total|deal|advertiser|partner|provider|offer|currency|cancel|breakfast|board", re.I)
_STRONG = re.compile(r"price|amount|advertiser|deal|partner", re.I)
_MONEY_TEXT = re.compile(r"(?:₺|\bTL\b|\bTRY\b|\$|€|\bEUR\b|\bUSD\b)\s?\d|\d[\d.,]*\s?(?:₺|\bTL\b|\bTRY\b|€|\bEUR\b|\$)")
_MAX_NODES = 300_000


def _walk(node: Any, path: str, out: dict[str, list[Any]], budget: list[int]) -> None:
    budget[0] -= 1
    if budget[0] < 0:
        return
    if isinstance(node, dict):
        for k, v in node.items():
            _walk(v, f"{path}.{k}" if path else str(k), out, budget)
    elif isinstance(node, list):
        for v in node:
            _walk(v, f"{path}[]", out, budget)
    else:
        key = ".".join(path.split(".")[-2:])  # 'advertiser.name' gibi üst anahtarı da dikkate al
        if _KEY_HINT.search(key) and node not in (None, "", [], {}):
            out.setdefault(path, []).append(node)


def _short(v: Any) -> str:
    return str(v).replace("\n", " ")[:40]


def summarize(stem: Path, max_lines: int = 160) -> list[str]:
    json_path, html_path = stem.with_suffix(".json"), stem.with_suffix(".html")
    lines: list[str] = []

    if json_path.exists():
        raw = json.loads(json_path.read_text(encoding="utf-8"))
        records = [r if isinstance(r, dict) and "body" in r else {"source": {}, "body": r} for r in raw]
        lines.append(f"## JSON blokları: {len(records)} adet")
        for i, rec in enumerate(records):
            src = rec["source"]
            op = ""
            m = re.search(r'"operationName"\s*:\s*"([^"]+)"', src.get("post", ""))
            if m:
                op = f" op={m.group(1)}"
            size = len(json.dumps(rec["body"], ensure_ascii=False))
            lines.append(f"[{i}] {src.get('method','')} {src.get('url','?')[:110]}{op} ({size // 1024} KB)")
        lines.append("")
        lines.append("## Fiyat/acenta ile ilgili JSON yolları (blok no, yol, adet, örnekler)")
        found = []
        for i, rec in enumerate(records):
            paths: dict[str, list[Any]] = {}
            _walk(rec["body"], "", paths, [_MAX_NODES])
            for path, values in paths.items():
                found.append((bool(_STRONG.search(".".join(path.split(".")[-2:]))), i, path, values))
        found.sort(key=lambda t: (not t[0], t[1]))
        for strong, i, path, values in found[:70]:
            samples = " | ".join(dict.fromkeys(_short(v) for v in values[:6]))
            lines.append(f"[{i}] {path}  x{len(values)}  örn: {samples}")
        if not found:
            lines.append("(hiçbir blokta fiyat/acenta benzeri anahtar yok)")
    else:
        lines.append(f"({json_path.name} bulunamadı)")

    lines.append("")
    if html_path.exists():
        html = html_path.read_text(encoding="utf-8")
        soup = BeautifulSoup(html, "html.parser")
        lines.append(f"## HTML: {len(html) // 1024} KB; '\"price\"' {html.count(chr(34) + 'price')} kez, "
                     f"'advertiser' {html.lower().count('advertiser')} kez")
        scripts = []
        for tag in soup.find_all("script"):
            body = tag.string or ""
            if re.search(r"advertiser|\"price", body):
                scripts.append(f"  <script id={tag.get('id')} type={tag.get('type')}> {len(body) // 1024} KB: "
                               f"{body[:90].strip()!r}")
        lines.append("Fiyat/acenta içeren script etiketleri:" + ("" if scripts else " yok"))
        lines.extend(scripts[:8])
        for tag in soup(["script", "style"]):
            tag.decompose()
        text_lines = [t for t in soup.get_text("\n", strip=True).split("\n") if _MONEY_TEXT.search(t)]
        lines.append(f"Görünür metinde para tutarı geçen satırlar: {len(text_lines)} adet (ilk 25):")
        lines.extend(f"  {t[:110]}" for t in list(dict.fromkeys(text_lines))[:25])
    else:
        lines.append(f"({html_path.name} bulunamadı)")
    return lines[:max_lines + 60]
