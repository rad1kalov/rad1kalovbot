# admin_db.py
"""База данных администраторов, пользователей бота, прав на функции, авто-описаний и парных био."""

import json
import sqlite3
from typing import Optional, List, Dict, Any
from timezone import now, now_iso
from datetime import datetime, timedelta, timezone as _tz

OWNER_ID = 8470104943


def init_admin_db(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row

    # Пользователи бота (для рассылки и поиска по юзернейму)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bot_users (
            user_id     INTEGER PRIMARY KEY,
            username    TEXT,
            first_name  TEXT,
            joined_at   TEXT
        )
    """)

    # Администраторы (доступ выдаётся по юзернейму, сохраняется числовой user_id!)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            user_id     INTEGER PRIMARY KEY,
            username    TEXT,
            added_by    INTEGER,
            added_at    TEXT
        )
    """)

    # Доступ к отдельным премиум-функциям
    conn.execute("""
        CREATE TABLE IF NOT EXISTS feature_access (
            user_id     INTEGER,
            feature     TEXT,
            expires_at  TEXT,
            PRIMARY KEY (user_id, feature)
        )
    """)

    # Авто-ротация описаний
    conn.execute("""
        CREATE TABLE IF NOT EXISTS auto_descriptions (
            user_id             INTEGER PRIMARY KEY,
            business_conn_id    TEXT,
            lines_json          TEXT,
            interval_minutes    INTEGER DEFAULT 5,
            current_index       INTEGER DEFAULT 0,
            is_active           INTEGER DEFAULT 1,
            last_change         TEXT
        )
    """)

    # Парные описания
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pair_bios (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            user_1              INTEGER,
            user_2              INTEGER,
            username_1          TEXT,
            username_2          TEXT,
            bc_id_1             TEXT,
            bc_id_2             TEXT,
            lines_json          TEXT,
            interval_minutes    INTEGER DEFAULT 5,
            current_step        INTEGER DEFAULT 0,
            status              TEXT DEFAULT 'pending',
            last_update         TEXT
        )
    """)

    conn.commit()


# ─── Пользователи и резолв юзернейма ───

def record_bot_user(conn: sqlite3.Connection, user_id: int, username: Optional[str], first_name: Optional[str]) -> None:
    clean_username = username.lstrip("@").lower() if username else None
    conn.execute("""
        INSERT INTO bot_users (user_id, username, first_name, joined_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username = COALESCE(excluded.username, bot_users.username),
            first_name = COALESCE(excluded.first_name, bot_users.first_name)
    """, (user_id, clean_username, first_name, now_iso()))
    conn.commit()


def resolve_user_id(conn: sqlite3.Connection, username_or_id: str) -> Optional[int]:
    """Ищет числовой user_id по юзернейму или проверяет числовой ввод."""
    clean = username_or_id.strip().lstrip("@")
    if clean.isdigit():
        return int(clean)

    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT user_id FROM bot_users WHERE LOWER(username) = LOWER(?)", (clean,)).fetchone()
    if row:
        return row["user_id"]

    # Также проверяем таблицу settings
    row2 = conn.execute("SELECT user_id FROM bio_settings WHERE LOWER(user_id) = LOWER(?)", (clean,)).fetchone()
    if row2:
        return row2["user_id"]

    return None


# ─── Администраторы ───

def is_admin(conn: sqlite3.Connection, user_id: int) -> bool:
    """Проверяет, является ли пользователь создателем или назначенным админом."""
    if user_id == OWNER_ID:
        return True
    row = conn.execute("SELECT user_id FROM admins WHERE user_id = ?", (user_id,)).fetchone()
    return bool(row)


def add_admin(conn: sqlite3.Connection, user_id: int, username: Optional[str], added_by: int) -> None:
    clean = username.lstrip("@") if username else None
    conn.execute("""
        INSERT INTO admins (user_id, username, added_by, added_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username = excluded.username,
            added_by = excluded.added_by
    """, (user_id, clean, added_by, now_iso()))
    conn.commit()


def del_admin(conn: sqlite3.Connection, user_id: int) -> bool:
    if user_id == OWNER_ID:
        return False
    cur = conn.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
    conn.commit()
    return cur.rowcount > 0


def get_all_admins(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM admins").fetchall()
    return [dict(r) for r in rows]


# ─── Доступ к отдельным фичам ───

def has_feature_access(conn: sqlite3.Connection, user_id: int, feature: str) -> bool:
    """
    Проверяет, есть ли у пользователя доступ к фиче:
    - Владелец: всегда True
    - Полная премиум-подписка: всегда True
    - Отдельная выданная фича с проверкой срока
    """
    if user_id == OWNER_ID:
        return True

    from premium_db import is_premium
    if is_premium(conn, user_id):
        return True

    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT expires_at FROM feature_access WHERE user_id = ? AND feature = ?",
        (user_id, feature),
    ).fetchone()
    if not row:
        return False

    exp = row["expires_at"]
    if not exp or exp == "lifetime":
        return True

    try:
        dt = datetime.fromisoformat(exp)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_tz.utc)
        return now() < dt
    except Exception:
        return False


def grant_feature(conn: sqlite3.Connection, user_id: int, feature: str, days: Optional[int]) -> None:
    if days is None or days <= 0:
        exp = "lifetime"
    else:
        exp = (now() + timedelta(days=days)).isoformat(timespec="seconds")

    conn.execute("""
        INSERT INTO feature_access (user_id, feature, expires_at)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id, feature) DO UPDATE SET expires_at = excluded.expires_at
    """, (user_id, feature, exp))
    conn.commit()


def revoke_feature(conn: sqlite3.Connection, user_id: int, feature: str) -> None:
    conn.execute("DELETE FROM feature_access WHERE user_id = ? AND feature = ?", (user_id, feature))
    conn.commit()


# ─── Рассылка ───

def get_all_broadcast_users(conn: sqlite3.Connection) -> List[int]:
    rows = conn.execute("SELECT user_id FROM bot_users").fetchall()
    return [r[0] for r in rows]
