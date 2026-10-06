"""Sayfa içi/XHR JSON'larından acenta/oda fiyatı çıkaran, anahtar adlarına dayalı sezgisel tarayıcı.

Hiçbir sitenin JSON şemasına bağlı değildir; bu yüzden gerçek sayfada doğrulanması gerekir
(`otascrape probe`). Yanlış pozitifleri azaltmak için bir kayıt ancak hem satıcı/oda hem de
pozitif bir fiyat içerirse üretilir.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterator

from ..normalize import parse_price

_SELLER_STR = {"advertisername", "partnername", "providername", "sellername", "vendorname",
               "bookingsitename", "merchantname", "sitename", "dealname"}
_SELLER_OBJ = {"advertiser", "partner", "provider", "seller", "vendor", "merchant", "bookingsite"}
_NAME_KEYS = ("name", "title", "displayname", "label")
_ROOM_KEYS = {"roomname", "roomtype", "roomtitle", "rateplanname", "roomtypename", "roomdescription"}
_TOTAL_KEYS = ["totalprice", "pricetotal", "totalamount", "stayprice", "grandtotal", "priceperstay",
               "totalstayprice", "totalpriceamount"]
_NIGHT_KEYS = ["pricepernight", "nightlyprice", "nightlyrate", "pernightprice", "avgnightlyrate"]
_GENERIC_KEYS = ["price", "amount", "rawprice", "pricevalue", "displayprice", "formattedprice",
                 "lowestprice", "priceamount", "rate"]
_MONEY_SUBKEYS = ("amount", "value", "raw", "total", "formatted", "display", "price")
_CURRENCY_KEYS = ("currency", "currencycode", "currencyiso", "curr")
_MAX_DEPTH = 30


@dataclass
class Deal:
    seller: str | None
    room: str | None
    amount: float
    currency: str | None
    basis: str  # 'total' | 'per_night' | 'unknown'
    price_key: str
    text: str


def _norm(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())


def _money(value: Any) -> tuple[float, str | None] | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return (float(value), None) if value > 0 else None
    if isinstance(value, str):
        try:
            amount, cur = parse_price(value)
        except ValueError:
            return None
        return (amount, cur) if amount > 0 else None
    if isinstance(value, dict):
        norm = {_norm(k): v for k, v in value.items()}
        inner_cur = next((norm[k] for k in _CURRENCY_KEYS if isinstance(norm.get(k), str) and len(norm[k]) == 3), None)
        for sub in _MONEY_SUBKEYS:
            if sub in norm:
                got = _money(norm[sub])
                if got:
                    return got[0], got[1] or (inner_cur.upper() if inner_cur else None)
    return None


def _find_price(d: dict[str, Any]) -> tuple[float, str | None, str, str] | None:
    norm = {_norm(k): (k, v) for k, v in d.items()}
    for keys, basis in ((_TOTAL_KEYS, "total"), (_NIGHT_KEYS, "per_night"), (_GENERIC_KEYS, "unknown")):
        for key in keys:
            if key in norm:
                got = _money(norm[key][1])
                if got:
                    currency = got[1]
                    if currency is None:
                        currency = next((str(norm[c][1]).upper() for c in _CURRENCY_KEYS
                                         if c in norm and isinstance(norm[c][1], str) and len(norm[c][1]) == 3), None)
                    return got[0], currency, basis, norm[key][0]
    return None


def _find_seller(d: dict[str, Any]) -> str | None:
    for key, value in d.items():
        n = _norm(key)
        name = None
        if n in _SELLER_STR and isinstance(value, str):
            name = value
        elif n in _SELLER_OBJ:
            if isinstance(value, str):
                name = value
            elif isinstance(value, dict):
                inner = {_norm(k): v for k, v in value.items()}
                name = next((inner[k] for k in _NAME_KEYS if isinstance(inner.get(k), str)), None)
        if name and len(name.strip()) >= 2 and not name.strip().isdigit():
            return name.strip()
    return None


def _find_room(d: dict[str, Any]) -> str | None:
    for key, value in d.items():
        if _norm(key) in _ROOM_KEYS and isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _text(d: dict[str, Any], depth: int = 2) -> str:
    parts: list[str] = []

    def collect(node: Any, level: int) -> None:
        if isinstance(node, str):
            parts.append(node)
        elif isinstance(node, dict) and level < depth:
            for v in node.values():
                collect(v, level + 1)
        elif isinstance(node, list) and level < depth:
            for v in node:
                collect(v, level + 1)

    collect(d, 0)
    return " ".join(parts)[:600]


def scan_deals(node: Any, mode: str = "advertiser", _depth: int = 0) -> Iterator[Deal]:
    """`mode='advertiser'`: acenta adı + fiyat olan nesneler (metasearch).
    `mode='room'`: oda/tarife adı + fiyat olan nesneler (tek satıcılı OTA)."""
    if _depth > _MAX_DEPTH:
        return
    if isinstance(node, dict):
        seller = _find_seller(node) if mode == "advertiser" else None
        room = _find_room(node)
        if (mode == "advertiser" and seller) or (mode == "room" and room):
            price = _find_price(node)
            if price:
                amount, currency, basis, key = price
                yield Deal(seller, room, amount, currency, basis, key, _text(node))
                return
        for value in node.values():
            yield from scan_deals(value, mode, _depth + 1)
    elif isinstance(node, list):
        for value in node:
            yield from scan_deals(value, mode, _depth + 1)


_SCRIPT_JSON = re.compile(r"<script[^>]*type=[\"']application/(?:ld\+)?json[\"'][^>]*>(.*?)</script>", re.S | re.I)
_NEXT_DATA = re.compile(r"<script[^>]*id=[\"']__NEXT_DATA__[\"'][^>]*>(.*?)</script>", re.S | re.I)


def scripts_json(html: str) -> list[Any]:
    """HTML içine gömülü JSON bloklarını (application/json, ld+json, __NEXT_DATA__) döndürür."""
    out: list[Any] = []
    for pattern in (_SCRIPT_JSON, _NEXT_DATA):
        for match in pattern.finditer(html):
            try:
                out.append(json.loads(match.group(1)))
            except ValueError:
                continue
    return out
