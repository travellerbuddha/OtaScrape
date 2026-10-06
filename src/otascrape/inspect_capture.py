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


def _chain(el, depth: int = 6) -> str:
    parts = []
    for parent in [el, *el.parents][:depth]:
        if getattr(parent, "name", None) in (None, "[document]", "html", "body"):
            break
        mark = parent.get("data-testid") or parent.get("id") or ""
        cls = (parent.get("class") or [""])[0][:18]
        parts.append(f"{parent.name}[{mark}]" if mark else f"{parent.name}.{cls}" if cls else parent.name)
    return " < ".join(parts)


def _dom_diagnostics(soup: BeautifulSoup) -> list[str]:
    out: list[str] = ["", "## Sayfa yapısı"]
    h1 = soup.find("h1")
    out.append(f"title: {soup.title.get_text(strip=True)[:100] if soup.title else None} | h1: {h1.get_text(' ', strip=True)[:100] if h1 else None}")
    date_lines = [t for t in soup.get_text("\n", strip=True).split("\n")
                  if re.search(r"\b(Oct|Nov|Dec|Eki|Kas|Ara)\b.*\d|\d+\s*(nights?|gece)", t, re.I) and len(t) < 80]
    out.append("Tarih/gece satırları: " + " || ".join(list(dict.fromkeys(date_lines))[:8]))
    counts: dict[str, int] = {}
    for el in soup.find_all(attrs={"data-testid": True}):
        counts[el["data-testid"]] = counts.get(el["data-testid"], 0) + 1
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:45]
    out.append("data-testid (adet): " + ", ".join(f"{k}x{v}" for k, v in top))
    out.append("Fiyat satırlarının DOM bağlamı (ilk 6):")
    seen = 0
    for node in soup.find_all(string=_MONEY_TEXT):
        el = node.parent
        box = el
        for _ in range(3):
            if box.parent is not None and box.parent.name not in ("body", "html", "[document]"):
                box = box.parent
        out.append(f"  '{str(node).strip()[:30]}'  {_chain(el)}")
        out.append(f"      çevre metin: {box.get_text(' | ', strip=True)[:170]}")
        seen += 1
        if seen >= 6:
            break
    return out


def _prune(node: Any, depth: int = 0) -> Any:
    """Yapıyı korurken boyutu küçültür: listelerin ilk 2 öğesi, kısaltılmış metinler."""
    if depth > 16:
        return "…"
    if isinstance(node, dict):
        return {k: _prune(v, depth + 1) for k, v in node.items()}
    if isinstance(node, list):
        out = [_prune(x, depth + 1) for x in node[:2]]
        if len(node) > 2:
            out.append(f"…(+{len(node) - 2} öğe daha)")
        return out
    if isinstance(node, str):
        return node if len(node) <= 50 else node[:50] + "…"
    return node


def block_skeleton(records: list[dict], index: int, limit: int = 7000) -> list[str]:
    if not 0 <= index < len(records):
        return [f"[{index}] böyle bir blok yok (0-{len(records) - 1})"]
    rec = records[index]
    src = rec["source"]
    head = f"=== blok [{index}] {src.get('method', '')} {src.get('url', '?')[:120]}"
    text = json.dumps(_prune(rec["body"]), ensure_ascii=False, indent=1)
    lines = [head, f"istek gövdesi: {src.get('post', '')[:300]}", "yapı (listelerin ilk 2 öğesi):"]
    lines.extend(text[:limit].split("\n"))
    if len(text) > limit:
        lines.append("…(kesildi)")
    return lines


def deal_rows(soup: BeautifulSoup, limit: int = 6) -> list[str]:
    """`advertiser-name` öğelerinin bulunduğu satırın iç yapısını (testid: metin) listeler."""
    out = ["## Acenta satırları (DOM): her satırdaki metin öğeleri"]
    shown: set[str] = set()
    for el in soup.find_all(attrs={"data-testid": "advertiser-name"}):
        row = el
        best = None
        for parent in el.parents:
            if parent.name in ("body", "html", "[document]"):
                break
            if len(parent.find_all(attrs={"data-testid": "advertiser-name"})) != 1:
                break
            best = parent
        if best is None:
            continue
        items = []
        for node in best.find_all(True):
            own = "".join(t for t in node.find_all(string=True, recursive=False)).strip()
            mark = node.get("data-testid")
            if own and len(own) < 90:
                items.append(f"{mark or node.name}: {own}")
            elif mark and not own:
                items.append(f"[{mark}]")
        signature = " | ".join(items)
        if signature in shown:
            continue
        shown.add(signature)
        slide = any("slideout" in (a.get("data-testid") or "") for a in [best, *best.parents])
        out.append(f"- satır (slideout içinde: {slide}; kök: {_chain(best, 3)}):")
        out.extend(f"    {it}" for it in items[:40])
        if len(shown) >= limit:
            break
    if len(out) == 1:
        out.append("(advertiser-name öğesi bulunamadı)")
    return out


def request_bodies(records: list[dict], ops=("accommodationSearchQuery", "accommodationDealsQuery", "getAdvertiserDetails"),
                   per_op: int = 2) -> list[str]:
    out = ["## İstek gövdeleri (her işlemden ilk %d)" % per_op]
    counts: dict[str, int] = {}
    for i, rec in enumerate(records):
        src = rec["source"]
        for op in ops:
            if op in src.get("url", "") and counts.get(op, 0) < per_op:
                counts[op] = counts.get(op, 0) + 1
                out.append(f"[{i}] {op}: {src.get('post', '')[:1200]}")
    return out


def summarize(stem: Path, max_lines: int = 160, blocks: list[int] | None = None,
              rows: bool = False, requests: bool = False) -> list[str]:
    json_path, html_path = stem.with_suffix(".json"), stem.with_suffix(".html")
    lines: list[str] = []

    if rows:
        soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        return deal_rows(soup)
    if requests:
        raw = json.loads(json_path.read_text(encoding="utf-8"))
        records = [r if isinstance(r, dict) and "body" in r else {"source": {}, "body": r} for r in raw]
        return request_bodies(records)
    if blocks:
        raw = json.loads(json_path.read_text(encoding="utf-8"))
        records = [r if isinstance(r, dict) and "body" in r else {"source": {}, "body": r} for r in raw]
        for index in blocks:
            lines.extend(block_skeleton(records, index))
            lines.append("")
        return lines

    if json_path.exists():
        raw = json.loads(json_path.read_text(encoding="utf-8"))
        records = [r if isinstance(r, dict) and "body" in r else {"source": {}, "body": r} for r in raw]
        lines.append(f"## JSON blokları: {len(records)} adet")
        small_inline = 0
        for i, rec in enumerate(records):
            src = rec["source"]
            if src.get("url") == "inline-script" and len(json.dumps(rec["body"], ensure_ascii=False)) < 1024:
                small_inline += 1  # kalabalığı azaltmak için tek satırda özetlenir
                continue
            op = ""
            m = re.search(r'"operationName"\s*:\s*"([^"]+)"', src.get("post", ""))
            if m:
                op = f" op={m.group(1)}"
            size = len(json.dumps(rec["body"], ensure_ascii=False))
            lines.append(f"[{i}] {src.get('method','')} {src.get('url','?')[:110]}{op} ({size // 1024} KB)")
        if small_inline:
            lines.append(f"(+ {small_inline} küçük inline-script JSON bloğu, <1 KB)")
        lines.append("")
        lines.append("## Fiyat/acenta ile ilgili JSON yolları (blok no, yol, adet, örnekler)")
        found = []
        for i, rec in enumerate(records):
            paths: dict[str, list[Any]] = {}
            _walk(rec["body"], "", paths, [_MAX_NODES])
            for path, values in paths.items():
                found.append((bool(_STRONG.search(".".join(path.split(".")[-2:]))), i, path, values))
        found.sort(key=lambda t: (not t[0], t[1]))
        for strong, i, path, values in found[:110]:
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
        lines.extend(_dom_diagnostics(soup))
        text_lines = [t for t in soup.get_text("\n", strip=True).split("\n") if _MONEY_TEXT.search(t)]
        lines.append(f"Görünür metinde para tutarı geçen satırlar: {len(text_lines)} adet (ilk 25):")
        lines.extend(f"  {t[:110]}" for t in list(dict.fromkeys(text_lines))[:25])
    else:
        lines.append(f"({html_path.name} bulunamadı)")
    return lines[:max_lines + 60]
