# tn_db.py
"""
Высокопроизводительная база данных настроек профиля (время, погода, ночной статус).
Строгая привязка к user_id как к первичному ключу.
Полная изоляция между пользователями, поддержка WAL-режима для высоких нагрузок.
"""

import sqlite3
import logging
from typing import Optional, Dict, Any, List

log = logging.getLogger(__name__)


def init_settings_table(conn: sqlite3.Connection) -> None:
    """Инициализирует и мигрирует таблицу настроек с гарантией user_id PRIMARY KEY."""
    conn.row_factory = sqlite3.Row

    # Оптимизация SQLite для высоких нагрузок и многопоточности
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA busy_timeout = 5000")
    except Exception as e:
        log.warning(f"Не удалось применить PRAGMA WAL: {e}")

    # Проверяем существующую таблицу settings
    cur = conn.execute("PRAGMA table_info(settings)")
    existing_cols = {row["name"]: dict(row) for row in cur.fetchall()}

    needs_migration = False
    if existing_cols:
        # Проверяем, является ли user_id первичным ключом
        user_id_col = existing_cols.get("user_id")
        if not user_id_col or user_id_col.get("pk") != 1:
            needs_migration = True

    if needs_migration:
        log.info("Миграция таблицы settings на новую схему с user_id PRIMARY KEY...")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings_new (
                user_id                 INTEGER PRIMARY KEY,
                business_connection_id  TEXT UNIQUE,
                first_name              TEXT NOT NULL DEFAULT '',
                tz_offset               INTEGER NOT NULL DEFAULT 3,
                weather_city            TEXT DEFAULT NULL,
                weather_enabled         INTEGER NOT NULL DEFAULT 0,
                night_status_enabled    INTEGER NOT NULL DEFAULT 0,
                ephemeral_limit         INTEGER NOT NULL DEFAULT 10,
                time_enabled            INTEGER NOT NULL DEFAULT 0,
                is_active               INTEGER NOT NULL DEFAULT 1,
                last_set_last_name      TEXT DEFAULT NULL,
                updated_at              TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Переносим существующие записи (если есть user_id)
        try:
            conn.execute("""
                INSERT OR IGNORE INTO settings_new (
                    user_id, business_connection_id, first_name, tz_offset,
                    weather_city, weather_enabled, night_status_enabled, ephemeral_limit,
                    time_enabled, is_active
                )
                SELECT 
                    user_id, business_connection_id, COALESCE(first_name, ''), COALESCE(tz_offset, 3),
                    weather_city, COALESCE(weather_enabled, 0), COALESCE(night_status_enabled, 0),
                    COALESCE(ephemeral_limit, 10), COALESCE(time_enabled, 0), COALESCE(is_active, 1)
                FROM settings
                WHERE user_id IS NOT NULL AND user_id > 0
            """)
        except Exception as e:
            log.warning(f"Ошибка при копировании старых настроек: {e}")

        conn.execute("DROP TABLE settings")
        conn.execute("ALTER TABLE settings_new RENAME TO settings")
        conn.commit()
    else:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                user_id                 INTEGER PRIMARY KEY,
                business_connection_id  TEXT UNIQUE,
                first_name              TEXT NOT NULL DEFAULT '',
                tz_offset               INTEGER NOT NULL DEFAULT 3,
                weather_city            TEXT DEFAULT NULL,
                weather_enabled         INTEGER NOT NULL DEFAULT 0,
                night_status_enabled    INTEGER NOT NULL DEFAULT 0,
                ephemeral_limit         INTEGER NOT NULL DEFAULT 10,
                time_enabled            INTEGER NOT NULL DEFAULT 0,
                is_active               INTEGER NOT NULL DEFAULT 1,
                last_set_last_name      TEXT DEFAULT NULL,
                updated_at              TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()

    # Создание индексов для молниеносных выборок при большом количестве пользователей
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_settings_user_id ON settings(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_settings_bc_id ON settings(business_connection_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_settings_active ON settings(is_active, time_enabled, night_status_enabled)")
    conn.commit()


def upsert_user_connection(
    conn: sqlite3.Connection,
    user_id: int,
    bc_id: str,
    first_name: str,
    is_active: bool = True,
) -> None:
    """
    Авторитетное сохранение подключения пользователя.
    Гарантирует, что данный bc_id закреплён только за этим user_id.
    """
    if not user_id or user_id <= 0:
        return

    # Если данный bc_id ранее числился за другим пользователем, отвязываем его
    conn.execute(
        "UPDATE settings SET business_connection_id = NULL, is_active = 0 WHERE business_connection_id = ? AND user_id != ?",
        (bc_id, user_id),
    )

    clean_first_name = (first_name or "").strip()
    conn.execute("""
        INSERT INTO settings (user_id, business_connection_id, first_name, is_active, updated_at)
        VALUES (?, ?, ?, ?, datetime('now'))
        ON CONFLICT(user_id) DO UPDATE SET
            business_connection_id = excluded.business_connection_id,
            first_name = CASE 
                WHEN excluded.first_name != '' THEN excluded.first_name 
                ELSE settings.first_name 
            END,
            is_active = excluded.is_active,
            updated_at = datetime('now')
    """, (user_id, bc_id, clean_first_name, 1 if is_active else 0))
    conn.commit()


def set_connection_inactive(conn: sqlite3.Connection, business_connection_id: str) -> None:
    """Помечает подключение как неактивное при отключении бота в Telegram."""
    conn.execute(
        "UPDATE settings SET is_active = 0, updated_at = datetime('now') WHERE business_connection_id = ?",
        (business_connection_id,),
    )
    conn.commit()


def set_time_enabled(conn: sqlite3.Connection, user_id: int, enabled: bool) -> None:
    """Включает или выключает время в фамилии строго для данного user_id."""
    conn.execute("""
        INSERT INTO settings (user_id, time_enabled, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(user_id) DO UPDATE SET
            time_enabled = excluded.time_enabled,
            updated_at = datetime('now')
    """, (user_id, 1 if enabled else 0))
    conn.commit()


def set_tz_offset(conn: sqlite3.Connection, user_id: int, offset: int) -> None:
    """Устанавливает часовой пояс строго для данного user_id."""
    conn.execute("""
        INSERT INTO settings (user_id, tz_offset, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(user_id) DO UPDATE SET
            tz_offset = excluded.tz_offset,
            updated_at = datetime('now')
    """, (user_id, offset))
    conn.commit()


def set_weather_settings(
    conn: sqlite3.Connection,
    user_id: int,
    city: Optional[str] = None,
    enabled: bool = True,
    business_connection_id: Optional[str] = None,
) -> None:
    """Устанавливает город и статус погоды строго для данного user_id."""
    conn.execute("""
        INSERT INTO settings (user_id, weather_city, weather_enabled, updated_at)
        VALUES (?, ?, ?, datetime('now'))
        ON CONFLICT(user_id) DO UPDATE SET
            weather_city = COALESCE(excluded.weather_city, settings.weather_city),
            weather_enabled = excluded.weather_enabled,
            updated_at = datetime('now')
    """, (user_id, city, 1 if enabled else 0))
    conn.commit()


def set_night_status(conn: sqlite3.Connection, user_id: int, enabled: bool) -> None:
    """Устанавливает ночной статус строго для данного user_id."""
    conn.execute("""
        INSERT INTO settings (user_id, night_status_enabled, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(user_id) DO UPDATE SET
            night_status_enabled = excluded.night_status_enabled,
            updated_at = datetime('now')
    """, (user_id, 1 if enabled else 0))
    conn.commit()


def update_last_set_name(conn: sqlite3.Connection, user_id: int, last_name: str) -> None:
    """Кэширует последнюю установленную в Telegram фамилию профиля."""
    conn.execute(
        "UPDATE settings SET last_set_last_name = ? WHERE user_id = ?",
        (last_name, user_id),
    )
    conn.commit()


def get_user_setting(conn: sqlite3.Connection, user_id: int) -> Optional[Dict[str, Any]]:
    """O(1) получение настроек пользователя по его первичному ключу user_id."""
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM settings WHERE user_id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def get_setting_by_bc_id(conn: sqlite3.Connection, bc_id: str) -> Optional[Dict[str, Any]]:
    """O(1) получение настроек по индексу business_connection_id."""
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM settings WHERE business_connection_id = ?", (bc_id,)).fetchone()
    return dict(row) if row else None


def get_user_bc_id(conn: sqlite3.Connection, user_id: int) -> Optional[str]:
    """Возвращает активный business_connection_id строго для указанного user_id."""
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT business_connection_id FROM settings WHERE user_id = ? AND is_active = 1",
        (user_id,),
    ).fetchone()
    if row and row["business_connection_id"]:
        return row["business_connection_id"]
    return None


def get_active_time_users(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """
    Высокоскоростная выборка через составной индекс:
    Возвращает только тех пользователей, у которых активно подключение
    И включено время либо ночной статус.
    """
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT * FROM settings 
        WHERE is_active = 1 
          AND business_connection_id IS NOT NULL
          AND (time_enabled = 1 OR night_status_enabled = 1)
    """).fetchall()
    return [dict(r) for r in rows]


def get_all_connections(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Возвращает все активные подключения."""
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM settings WHERE is_active = 1 AND business_connection_id IS NOT NULL"
    ).fetchall()
    return [dict(r) for r in rows]


def get_ephemeral_limit_for_user(conn: sqlite3.Connection, user_id: int) -> int:
    """Возвращает лимит сохранения одноразок."""
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT ephemeral_limit FROM settings WHERE user_id = ?", (user_id,)).fetchone()
    if row and row["ephemeral_limit"] is not None:
        return row["ephemeral_limit"]
    return 10


# ────────── Совместимость с предыдущими вызовами ──────────
def save_first_name(
    conn: sqlite3.Connection,
    business_connection_id: str,
    first_name: str,
    user_id: Optional[int] = None,
) -> None:
    if user_id:
        upsert_user_connection(conn, user_id, business_connection_id, first_name)


def get_setting(conn: sqlite3.Connection, business_connection_id: str) -> Optional[Dict[str, Any]]:
    return get_setting_by_bc_id(conn, business_connection_id)