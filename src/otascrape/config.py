from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

from .models import Search, Stay


class ConfigError(ValueError):
    pass


@dataclass
class ChannelConfig:
    url: str
    options: dict = field(default_factory=dict)


@dataclass
class HotelConfig:
    id: str
    name: str
    own: bool
    channels: dict[str, ChannelConfig]


@dataclass
class ScraperSettings:
    headless: bool = True
    delay_seconds: tuple[float, float] = (4.0, 9.0)
    retries: int = 2
    timeout_seconds: int = 45
    proxy_server: str | None = None
    proxy_username_env: str | None = None
    proxy_password_env: str | None = None
    debug_dir: str = "data/debug"


@dataclass
class Config:
    base_currency: str
    fx: dict[str, float]
    pax_basis: str
    parity_tolerance_pct: float
    only_free_cancellation: bool
    direct_sellers: list[str]
    database: str
    output_dir: str
    scraper: ScraperSettings
    stays: list[Stay]
    checkin_offsets_days: list[int]
    checkin_dates: list[date]
    hotels: list[HotelConfig]
    search_currency: str = field(default="")

    def __post_init__(self) -> None:
        if not self.search_currency:
            self.search_currency = self.base_currency


def _require(data: dict[str, Any], key: str, where: str) -> Any:
    if key not in data or data[key] in (None, ""):
        raise ConfigError(f"{where}: '{key}' alanı zorunlu")
    return data[key]


def _parse_stay(raw: dict[str, Any]) -> Stay:
    where = f"stays[{raw.get('name', '?')}]"
    nights = int(_require(raw, "nights", where))
    adults = int(_require(raw, "adults", where))
    children = int(raw.get("children", 0))
    ages = tuple(int(a) for a in raw.get("child_ages", []))
    if nights < 1 or adults < 1:
        raise ConfigError(f"{where}: nights ve adults en az 1 olmalı")
    if len(ages) != children:
        raise ConfigError(f"{where}: children={children} ama child_ages {len(ages)} adet")
    return Stay(
        name=str(raw.get("name") or f"{nights}N-{adults}AD" + (f"-{children}CH" if children else "")),
        nights=nights,
        adults=adults,
        children=children,
        child_ages=ages,
        rooms=int(raw.get("rooms", 1)),
    )


def _parse_hotel(raw: dict[str, Any]) -> HotelConfig:
    hid = str(_require(raw, "id", "hotels[]"))
    where = f"hotels[{hid}]"
    channels: dict[str, ChannelConfig] = {}
    for channel, value in (_require(raw, "channels", where)).items():
        url = value if isinstance(value, str) else (value or {}).get("url")
        if not url:
            raise ConfigError(f"{where}.channels.{channel}: 'url' zorunlu")
        options = {} if isinstance(value, str) else {k: v for k, v in value.items() if k != "url"}
        if options.get("price_basis") not in (None, "total", "per_night"):
            raise ConfigError(f"{where}.channels.{channel}: price_basis 'total' veya 'per_night' olmalı")
        channels[str(channel).lower()] = ChannelConfig(url=url, options=options)
    return HotelConfig(id=hid, name=str(raw.get("name", hid)), own=bool(raw.get("own", False)), channels=channels)


def parse_config(data: dict[str, Any]) -> Config:
    stays = [_parse_stay(s) for s in _require(data, "stays", "config")]
    hotels = [_parse_hotel(h) for h in _require(data, "hotels", "config")]
    checkin = data.get("checkin") or {}
    offsets = [int(x) for x in checkin.get("offsets_days", [])]
    dates = [d if isinstance(d, date) else date.fromisoformat(str(d)) for d in checkin.get("dates", [])]
    if not offsets and not dates:
        raise ConfigError("checkin.offsets_days veya checkin.dates tanımlanmalı")

    s = data.get("scraper") or {}
    delay = s.get("delay_seconds", [4, 9])
    scraper = ScraperSettings(
        headless=bool(s.get("headless", True)),
        delay_seconds=(float(delay[0]), float(delay[1])),
        retries=int(s.get("retries", 2)),
        timeout_seconds=int(s.get("timeout_seconds", 45)),
        proxy_server=s.get("proxy_server"),
        proxy_username_env=s.get("proxy_username_env"),
        proxy_password_env=s.get("proxy_password_env"),
        debug_dir=str(s.get("debug_dir", "data/debug")),
    )

    base = str(data.get("base_currency", "EUR")).upper()
    pax_basis = str(data.get("pax_basis", "adults"))
    if pax_basis not in ("adults", "all_guests"):
        raise ConfigError("pax_basis 'adults' veya 'all_guests' olmalı")
    ids = [h.id for h in hotels]
    if len(ids) != len(set(ids)):
        raise ConfigError("hotels[].id değerleri benzersiz olmalı")

    return Config(
        base_currency=base,
        fx={str(k).upper(): float(v) for k, v in (data.get("fx") or {}).items()},
        pax_basis=pax_basis,
        parity_tolerance_pct=float(data.get("parity_tolerance_pct", 1.0)),
        only_free_cancellation=bool(data.get("only_free_cancellation", False)),
        direct_sellers=[str(x).lower() for x in data.get("direct_sellers", [])],
        database=str(data.get("database", "data/otascrape.db")),
        output_dir=str(data.get("output_dir", "data/reports")),
        scraper=scraper,
        stays=stays,
        checkin_offsets_days=offsets,
        checkin_dates=dates,
        hotels=hotels,
        search_currency=str(data.get("search_currency", base)).upper(),
    )


def load_config(path: str | Path) -> Config:
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: geçerli bir YAML sözlüğü değil")
    return parse_config(data)


def check_in_dates(config: Config, today: date) -> list[date]:
    dates = {today + timedelta(days=o) for o in config.checkin_offsets_days}
    dates.update(d for d in config.checkin_dates if d >= today)
    return sorted(dates)


def build_searches(config: Config, today: date) -> list[Search]:
    searches: list[Search] = []
    for hotel in config.hotels:
        for channel, ch in hotel.channels.items():
            for check_in in check_in_dates(config, today):
                for stay in config.stays:
                    searches.append(
                        Search(
                            hotel_id=hotel.id,
                            hotel_name=hotel.name,
                            channel=channel,
                            url=ch.url,
                            check_in=check_in,
                            stay=stay,
                            currency=config.search_currency,
                            options=ch.options,
                        )
                    )
    return searches
