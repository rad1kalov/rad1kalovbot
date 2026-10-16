# tn_db.py
"""Таблица настроек профиля business_connection: часовой пояс, имя, погода, ночной статус и лимиты."""

import sqlite3
from typing import Optional, Dict, Any, List


def init_settings_table(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            business_connection_id  TEXT PRIMARY KEY,
            tz_offset               INTEGER DEFAULT 0,
            first_name              TEXT,
            user_id                 INTEGER,
            weather_city            TEXT,
            weather_enabled         INTEGER DEFAULT 0,
            night_status_enabled    INTEGER DEFAULT 0,
            ephemeral_limit         INTEGER DEFAULT 10
        )
    """)
    conn.commit()

    # Миграция колонок для существующей БД
    cur = conn.execute("PRAGMA table_info(settings)")
    existing_cols = {row["name"] for row in cur.fetchall()}

    migrations = [
        ("user_id", "INTEGER"),
        ("weather_city", "TEXT"),
        ("weather_enabled", "INTEGER DEFAULT 0"),
        ("night_status_enabled", "INTEGER DEFAULT 0"),
        ("ephemeral_limit", "INTEGER DEFAULT 10"),
    ]

    for col_name, col_def in migrations:
        if col_name not in existing_cols:
            try:
                conn.execute(f"ALTER TABLE settings ADD COLUMN {col_name} {col_def}")
                conn.commit()
            except sqlite3.OperationalError:
                pass


def set_tz_offset(conn: sqlite3.Connection, business_connection_id: str, offset: int) -> None:
    conn.execute("""
        INSERT INTO settings (business_connection_id, tz_offset)
        VALUES (?, ?)
        ON CONFLICT(business_connection_id)
        DO UPDATE SET tz_offset = excluded.tz_offset
    """, (business_connection_id, offset))
    conn.commit()


def save_first_name(conn: sqlite3.Connection, business_connection_id: str, first_name: str, user_id: Optional[int] = None) -> None:
    if user_id:
        conn.execute("""
            INSERT INTO settings (business_connection_id, first_name, user_id)
            VALUES (?, ?, ?)
            ON CONFLICT(business_connection_id)
            DO UPDATE SET
                first_name = COALESCE(settings.first_name, excluded.first_name),
                user_id = COALESCE(settings.user_id, excluded.user_id)
        """, (business_connection_id, first_name, user_id))
    else:
        conn.execute("""
            INSERT INTO settings (business_connection_id, first_name)
            VALUES (?, ?)
            ON CONFLICT(business_connection_id)
            DO UPDATE SET first_name = COALESCE(settings.first_name, excluded.first_name)
        """, (business_connection_id, first_name))
    conn.commit()


def get_setting(conn: sqlite3.Connection, business_connection_id: str) -> Optional[Dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM settings WHERE business_connection_id = ?",
        (business_connection_id,)
    ).fetchone()
    return dict(row) if row else None


def set_weather_settings(conn: sqlite3.Connection, business_connection_id: str, city: Optional[str], enabled: bool) -> None:
    conn.execute("""
        UPDATE settings
        SET weather_city = COALESCE(?, weather_city),
            weather_enabled = ?
        WHERE business_connection_id = ?
    """, (city, 1 if enabled else 0, business_connection_id))
    conn.commit()


def set_night_status(conn: sqlite3.Connection, business_connection_id: str, enabled: bool) -> None:
    conn.execute(
        "UPDATE settings SET night_status_enabled = ? WHERE business_connection_id = ?",
        (1 if enabled else 0, business_connection_id)
    )
    conn.commit()


def set_ephemeral_limit(conn: sqlite3.Connection, business_connection_id: str, limit: int) -> None:
    conn.execute(
        "UPDATE settings SET ephemeral_limit = ? WHERE business_connection_id = ?",
        (limit, business_connection_id)
    )
    conn.commit()


def get_ephemeral_limit_for_user(conn: sqlite3.Connection, user_id: int) -> int:
    """Возвращает лимит сохранения одноразок (по умолчанию 10, для премиум может быть выше)."""
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT ephemeral_limit FROM settings WHERE user_id = ?", (user_id,)).fetchone()
    if row and row["ephemeral_limit"] is not None:
        return row["ephemeral_limit"]
    return 10


def get_all_connections(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM settings").fetchall()
    return [dict(r) for r in rows]