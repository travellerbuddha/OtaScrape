from datetime import date

import pytest

from otascrape.config import parse_config


@pytest.fixture
def cfg():
    return parse_config({
        "base_currency": "EUR",
        "fx": {"EUR": 1, "USD": 0.9},
        "direct_sellers": ["official site"],
        "parity_tolerance_pct": 1.0,
        "compare_by_cancellation": False,
        "stays": [{"name": "7N-2AD", "nights": 7, "adults": 2}],
        "checkin": {"offsets_days": [10], "dates": ["2030-01-01"]},
        "hotels": [{"id": "h1", "name": "Hotel 1", "channels": {"booking": "https://example.test/h1", "trivago": {"url": "https://example.test/t1"}}}],
    })


@pytest.fixture
def today():
    return date(2026, 10, 6)
