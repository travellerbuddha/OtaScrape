"""Metin normalizasyonu: pansiyon tipi, fiyat metni ve iptal koşulu."""
from __future__ import annotations

import re

BOARD_CODES = ("RO", "BB", "HB", "FB", "AI", "UAI", "UNKNOWN")

# Sıra önemli: daha spesifik kalıplar önce gelir (ultra, all inclusive'den önce).
_BOARD_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("UAI", re.compile(r"ultra\s*(all|her\s*şey|her\s*sey)|\buai\b", re.I)),
    ("AI", re.compile(r"all[\s-]*inclusive|her\s*şey\s*dahil|her\s*sey\s*dahil|alles\s*inklusive|\bai\b", re.I)),
    ("FB", re.compile(r"full[\s-]*board|tam\s*pansiyon|vollpension", re.I)),
    ("HB", re.compile(r"half[\s-]*board|yarım\s*pansiyon|yarim\s*pansiyon|halbpension", re.I)),
    ("BB", re.compile(r"breakfast|kahvaltı|kahvalti|frühstück|fruhstuck|\bbb\b", re.I)),
    ("RO", re.compile(r"room[\s-]*only|sadece\s*oda|oda\s*kahvaltısız|ohne\s*verpflegung|no\s*meals?", re.I)),
]

_FREE_CANCEL = re.compile(r"free\s*cancell?ation|ücretsiz\s*iptal|kostenlose\s*stornierung", re.I)
_NON_REFUND = re.compile(r"non[\s-]*refundable|iptal\s*edilemez|iade\s*edilemez|nicht\s*erstattbar", re.I)
_TAX_EXCLUDED = re.compile(r"\+\s*[^\n]{0,20}(tax|vergi)|excl(uding|\.)?\s*(tax|vat)|taxes?\s*and\s*charges?\s*(not|extra)", re.I)
_TAX_INCLUDED = re.compile(r"(incl(uding|udes|\.)?|dahil)\s*[^\n]{0,15}(tax|vergi)|taxes?\s*included", re.I)

_CURRENCY_SYMBOLS = {"€": "EUR", "us$": "USD", "$": "USD", "£": "GBP", "₺": "TRY", "tl": "TRY"}
_CURRENCY_CODE = re.compile(r"\b(EUR|USD|GBP|TRY|CHF|RUB|PLN|THB|VND|AED)\b")
_NUMBER = re.compile(r"\d[\d.,\s ]*\d|\d")


def normalize_board(text: str) -> str:
    for code, pattern in _BOARD_PATTERNS:
        if pattern.search(text):
            return code
    return "UNKNOWN"


def detect_free_cancellation(text: str) -> bool | None:
    if _NON_REFUND.search(text):
        return False
    if _FREE_CANCEL.search(text):
        return True
    return None


def detect_taxes_included(text: str) -> bool | None:
    if _TAX_EXCLUDED.search(text):
        return False
    if _TAX_INCLUDED.search(text):
        return True
    return None


def parse_number(raw: str) -> float:
    """'2,345.50', '2.345,50', '12 345', '2.345' (binlik) gibi biçimleri float'a çevirir."""
    s = re.sub(r"[\s ]", "", raw)
    if "," in s and "." in s:
        decimal = "," if s.rfind(",") > s.rfind(".") else "."
        thousands = "." if decimal == "," else ","
        s = s.replace(thousands, "").replace(decimal, ".")
    elif "," in s or "." in s:
        sep = "," if "," in s else "."
        parts = s.split(sep)
        if len(parts) > 2 or len(parts[-1]) == 3:
            s = "".join(parts)  # binlik ayraç
        else:
            s = ".".join(parts)  # ondalık ayraç
    return float(s)


def parse_price(text: str) -> tuple[float, str | None]:
    """Fiyat metninden (tutar, para birimi) çıkarır. Para birimi bulunamazsa None döner."""
    match = _NUMBER.search(text)
    if not match:
        raise ValueError(f"Fiyat bulunamadı: {text!r}")
    amount = parse_number(match.group(0))
    currency: str | None = None
    code = _CURRENCY_CODE.search(text)
    if code:
        currency = code.group(1)
    else:
        lowered = text.lower()
        for symbol, iso in _CURRENCY_SYMBOLS.items():
            if symbol in lowered:
                currency = iso
                break
    return amount, currency
