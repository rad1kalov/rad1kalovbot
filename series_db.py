# series_db.py
"""Работа с таблицами series и series_log с поддержкой безопасных миграций."""

import sqlite3
from typing import Optional, Dict, Any, List

from timezone import now_iso


def init_series_db(conn: sqlite3.Connection) -> None:
    """Инициализирует таблицы и накатывает безопасные миграции."""
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS series (
            chat_id                 INTEGER PRIMARY KEY,
            business_connection_id  TEXT,
            current_streak          INTEGER DEFAULT 0,
            best_streak             INTEGER DEFAULT 0,
            pre_burn_streak         INTEGER DEFAULT 0,
            stage                   TEXT    DEFAULT 'spark',
            last_date               TEXT,
            total_days              INTEGER DEFAULT 0,
            revives_left            INTEGER DEFAULT 3,
            freezes_left            INTEGER DEFAULT 2,
            month_tracker           TEXT,
            frozen                  INTEGER DEFAULT 0,
            reminders               INTEGER DEFAULT 1,
            announced_today         INTEGER DEFAULT 0,
            last_user_name          TEXT,
            enabled                 INTEGER DEFAULT 0,
            notify_mode             TEXT    DEFAULT 'milestones',
            auto_stage              INTEGER DEFAULT 1,
            burned_date             TEXT
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

    # Проверяем и накатываем недостающие колонки при обновлении существующей БД
    cur = conn.execute("PRAGMA table_info(series)")
    existing_cols = {row["name"] for row in cur.fetchall()}

    migrations = [
        ("enabled", "INTEGER DEFAULT 0"),
        ("notify_mode", "TEXT DEFAULT 'milestones'"),
        ("auto_stage", "INTEGER DEFAULT 1"),
        ("burned_date", "TEXT"),
        ("hide_stage", "INTEGER DEFAULT 0"),
        ("custom_stage", "TEXT"),
        ("auto_freeze", "INTEGER DEFAULT 0"),
    ]

    for col_name, col_def in migrations:
        if col_name not in existing_cols:
            try:
                conn.execute(f"ALTER TABLE series ADD COLUMN {col_name} {col_def}")
                conn.commit()
            except sqlite3.OperationalError:
                pass


def get_series(conn: sqlite3.Connection, chat_id: int) -> Optional[Dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM series WHERE chat_id = ?", (chat_id,)).fetchone()
    return dict(row) if row else None


def ensure_series(conn: sqlite3.Connection, chat_id: int,
                  conn_id: Optional[str] = None,
                  user_name: Optional[str] = None) -> Dict[str, Any]:
    s = get_series(conn, chat_id)
    if s is None:
        conn.execute("""
            INSERT INTO series (chat_id, business_connection_id, last_user_name, stage, enabled)
            VALUES (?, ?, ?, 'spark', 0)
        """, (chat_id, conn_id, user_name))
        conn.commit()
        s = get_series(conn, chat_id)
    else:
        # Обновляем имя и connection_id, если поменялись
        updates = []
        params = []
        if conn_id and s["business_connection_id"] != conn_id:
            updates.append("business_connection_id = ?")
            params.append(conn_id)
        if user_name and s["last_user_name"] != user_name:
            updates.append("last_user_name = ?")
            params.append(user_name)
        if updates:
            params.append(chat_id)
            conn.execute(f"UPDATE series SET {', '.join(updates)} WHERE chat_id = ?", tuple(params))
            conn.commit()
            s = get_series(conn, chat_id)
    return s


def set_series_enabled(conn: sqlite3.Connection, chat_id: int, enabled: bool) -> None:
    conn.execute("UPDATE series SET enabled = ? WHERE chat_id = ?", (1 if enabled else 0, chat_id))
    conn.commit()


def set_notify_mode(conn: sqlite3.Connection, chat_id: int, mode: str) -> None:
    conn.execute("UPDATE series SET notify_mode = ? WHERE chat_id = ?", (mode, chat_id))
    conn.commit()


def set_stage(conn: sqlite3.Connection, chat_id: int, stage: str, auto: bool = False) -> None:
    conn.execute(
        "UPDATE series SET stage = ?, auto_stage = ? WHERE chat_id = ?",
        (stage, 1 if auto else 0, chat_id)
    )
    conn.commit()


def log_event(conn: sqlite3.Connection, chat_id: int, event: str,
              from_stage: Optional[str] = None,
              to_stage: Optional[str] = None,
              by_user: Optional[str] = None) -> None:
    conn.execute(
        "INSERT INTO series_log (chat_id, event, from_stage, to_stage, by_user, date) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (chat_id, event, from_stage, to_stage, by_user, now_iso()),
    )
    conn.commit()


def get_all_active_series(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM series WHERE enabled = 1 ORDER BY current_streak DESC LIMIT 20"
    ).fetchall()
    return [dict(r) for r in rows]