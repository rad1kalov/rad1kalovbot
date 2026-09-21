# series_db.py
"""Работа с таблицами series и series_log."""

import sqlite3
from datetime import datetime


def init_series_db(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS series (
            chat_id                 INTEGER PRIMARY KEY,
            business_connection_id  TEXT,
            current_streak          INTEGER DEFAULT 0,
            best_streak             INTEGER DEFAULT 0,
            pre_burn_streak         INTEGER DEFAULT 0,
            stage                   TEXT    DEFAULT 'friendship',
            last_date               TEXT,
            total_days              INTEGER DEFAULT 0,
            revives_left            INTEGER DEFAULT 3,
            freezes_left            INTEGER DEFAULT 1,
            month_tracker           TEXT,
            frozen                  INTEGER DEFAULT 0,
            reminders               INTEGER DEFAULT 1,
            announced_today         INTEGER DEFAULT 0,
            last_user_name          TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS series_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id     INTEGER,
            event       TEXT,
            from_stage  TEXT,
            to_stage    TEXT,
            by_user     TEXT,
            date        TEXT
        )
    """)
    conn.commit()


def get_series(conn: sqlite3.Connection, chat_id: int):
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM series WHERE chat_id = ?", (chat_id,)).fetchone()
    return dict(row) if row else None


def ensure_series(conn: sqlite3.Connection, chat_id: int,
                  conn_id: str | None = None,
                  user_name: str | None = None):
    s = get_series(conn, chat_id)
    if s is None:
        conn.execute(
            "INSERT INTO series (chat_id, business_connection_id, last_user_name) "
            "VALUES (?, ?, ?)",
            (chat_id, conn_id, user_name),
        )
        conn.commit()
        s = get_series(conn, chat_id)
    elif conn_id and s["business_connection_id"] != conn_id:
        conn.execute(
            "UPDATE series SET business_connection_id = ? WHERE chat_id = ?",
            (conn_id, chat_id),
        )
        conn.commit()
        s["business_connection_id"] = conn_id
    return s


def log_event(conn: sqlite3.Connection, chat_id: int, event: str,
              from_stage: str | None = None,
              to_stage: str | None = None,
              by_user: str | None = None) -> None:
    conn.execute(
        "INSERT INTO series_log (chat_id, event, from_stage, to_stage, by_user, date) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (chat_id, event, from_stage, to_stage, by_user,
         datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()