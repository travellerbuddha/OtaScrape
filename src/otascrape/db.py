from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

from .models import Offer

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running'
);
CREATE TABLE IF NOT EXISTS offers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    hotel_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    seller TEXT NOT NULL,
    stay_name TEXT NOT NULL,
    check_in TEXT NOT NULL,
    nights INTEGER NOT NULL,
    adults INTEGER NOT NULL,
    children INTEGER NOT NULL,
    room_name TEXT NOT NULL,
    board TEXT NOT NULL,
    total_price REAL NOT NULL,
    currency TEXT NOT NULL,
    free_cancellation INTEGER,
    taxes_included INTEGER,
    source_url TEXT NOT NULL,
    scraped_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_offers_run ON offers(run_id);
CREATE TABLE IF NOT EXISTS errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    hotel_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    check_in TEXT NOT NULL,
    stay_name TEXT NOT NULL,
    message TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _bool_to_int(v: bool | None) -> int | None:
    return None if v is None else int(v)


def _int_to_bool(v: int | None) -> bool | None:
    return None if v is None else bool(v)


def start_run(conn: sqlite3.Connection, now: datetime) -> int:
    cur = conn.execute("INSERT INTO runs (started_at) VALUES (?)", (now.isoformat(),))
    conn.commit()
    return int(cur.lastrowid)


def finish_run(conn: sqlite3.Connection, run_id: int, status: str, now: datetime) -> None:
    conn.execute("UPDATE runs SET finished_at = ?, status = ? WHERE id = ?", (now.isoformat(), status, run_id))
    conn.commit()


def save_offers(conn: sqlite3.Connection, run_id: int, offers: list[Offer]) -> None:
    conn.executemany(
        """INSERT INTO offers (run_id, hotel_id, channel, seller, stay_name, check_in, nights, adults,
           children, room_name, board, total_price, currency, free_cancellation, taxes_included,
           source_url, scraped_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [
            (
                run_id, o.hotel_id, o.channel, o.seller, o.stay_name, o.check_in.isoformat(), o.nights,
                o.adults, o.children, o.room_name, o.board, o.total_price, o.currency,
                _bool_to_int(o.free_cancellation), _bool_to_int(o.taxes_included), o.source_url,
                o.scraped_at.isoformat(),
            )
            for o in offers
        ],
    )
    conn.commit()


def save_error(conn: sqlite3.Connection, run_id: int, search, message: str, now: datetime) -> None:
    conn.execute(
        """INSERT INTO errors (run_id, hotel_id, channel, check_in, stay_name, message, occurred_at)
           VALUES (?,?,?,?,?,?,?)""",
        (run_id, search.hotel_id, search.channel, search.check_in.isoformat(), search.stay.name, message, now.isoformat()),
    )
    conn.commit()


def load_offers(conn: sqlite3.Connection, run_id: int) -> list[Offer]:
    rows = conn.execute("SELECT * FROM offers WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
    return [
        Offer(
            hotel_id=r["hotel_id"], channel=r["channel"], seller=r["seller"], stay_name=r["stay_name"],
            check_in=date.fromisoformat(r["check_in"]), nights=r["nights"], adults=r["adults"],
            children=r["children"], room_name=r["room_name"], board=r["board"],
            total_price=r["total_price"], currency=r["currency"],
            free_cancellation=_int_to_bool(r["free_cancellation"]),
            taxes_included=_int_to_bool(r["taxes_included"]), source_url=r["source_url"],
            scraped_at=datetime.fromisoformat(r["scraped_at"]),
        )
        for r in rows
    ]


def load_errors(conn: sqlite3.Connection, run_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM errors WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()


def latest_run_id(conn: sqlite3.Connection, before: int | None = None) -> int | None:
    """Bitmiş (ok/partial) son çalıştırma; `before` verilirse o id'den önceki."""
    sql = "SELECT id FROM runs WHERE status IN ('ok','partial')"
    params: tuple = ()
    if before is not None:
        sql += " AND id < ?"
        params = (before,)
    row = conn.execute(sql + " ORDER BY id DESC LIMIT 1", params).fetchone()
    return None if row is None else int(row["id"])
