# tn_db.py
"""Таблица настроек профиля business_connection: часовой пояс, имя, погода, ночной статус и лимиты."""

import sqlite3
from typing import Optional, Dict, Any, List


def init_settings_table(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            business_connection_id  TEXT PRIMARY KEY,
            tz_offset               INTEGER DEFAULT 3,
            first_name              TEXT,
            user_id                 INTEGER,
            weather_city            TEXT,
            weather_enabled         INTEGER DEFAULT 0,
            night_status_enabled    INTEGER DEFAULT 0,
            ephemeral_limit         INTEGER DEFAULT 10,
            time_enabled            INTEGER DEFAULT 0,
            is_active               INTEGER DEFAULT 1
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
        ("time_enabled", "INTEGER DEFAULT 0"),
        ("is_active", "INTEGER DEFAULT 1"),
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
            INSERT INTO settings (business_connection_id, first_name, user_id, is_active)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(business_connection_id)
            DO UPDATE SET
                first_name = COALESCE(excluded.first_name, settings.first_name),
                user_id = COALESCE(excluded.user_id, settings.user_id),
                is_active = 1
        """, (business_connection_id, first_name, user_id))
    else:
        conn.execute("""
            INSERT INTO settings (business_connection_id, first_name, is_active)
            VALUES (?, ?, 1)
            ON CONFLICT(business_connection_id)
            DO UPDATE SET
                first_name = COALESCE(excluded.first_name, settings.first_name),
                is_active = 1
        """, (business_connection_id, first_name))
    conn.commit()


def set_connection_inactive(conn: sqlite3.Connection, business_connection_id: str) -> None:
    conn.execute(
        "UPDATE settings SET is_active = 0 WHERE business_connection_id = ?",
        (business_connection_id,)
    )
    conn.commit()


def set_time_enabled(conn: sqlite3.Connection, business_connection_id: str, enabled: bool) -> None:
    conn.execute(
        "UPDATE settings SET time_enabled = ? WHERE business_connection_id = ?",
        (1 if enabled else 0, business_connection_id)
    )
    conn.commit()


def get_setting(conn: sqlite3.Connection, business_connection_id: str) -> Optional[Dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM settings WHERE business_connection_id = ?",
        (business_connection_id,)
    ).fetchone()
    return dict(row) if row else None


def set_weather_settings(
    conn: sqlite3.Connection,
    business_connection_id: str,
    city: Optional[str] = None,
    enabled: bool = True,
    user_id: Optional[int] = None,
) -> None:
    if user_id:
        conn.execute("""
            UPDATE settings
            SET weather_city = COALESCE(?, weather_city),
                weather_enabled = ?,
                user_id = COALESCE(settings.user_id, ?)
            WHERE business_connection_id = ?
        """, (city, 1 if enabled else 0, user_id, business_connection_id))
    else:
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


def get_user_bc_id(conn: sqlite3.Connection, user_id: int) -> Optional[str]:
    """Возвращает business_connection_id строго для указанного пользователя."""
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT business_connection_id FROM settings WHERE user_id = ? AND is_active = 1",
        (user_id,)
    ).fetchone()
    if row and row["business_connection_id"]:
        return row["business_connection_id"]

    row2 = conn.execute(
        "SELECT business_connection_id FROM settings WHERE user_id = ?",
        (user_id,)
    ).fetchone()
    if row2 and row2["business_connection_id"]:
        return row2["business_connection_id"]

    row3 = conn.execute(
        "SELECT business_connection_id FROM bio_settings WHERE user_id = ?",
        (user_id,)
    ).fetchone()
    if row3 and row3["business_connection_id"]:
        return row3["business_connection_id"]

    return None


def get_setting_by_user(conn: sqlite3.Connection, user_id: int) -> Optional[Dict[str, Any]]:
    """Возвращает настройки профиля строго для указанного пользователя."""
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM settings WHERE user_id = ?", (user_id,)).fetchone()
    if row:
        return dict(row)

    bc_id = get_user_bc_id(conn, user_id)
    if bc_id:
        return get_setting(conn, bc_id)

    return None


def get_all_connections(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM settings WHERE is_active = 1").fetchall()
    return [dict(r) for r in rows]