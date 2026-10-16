# premium_db.py
"""Работа с подписками и настройками описания (био) в базе данных."""

import sqlite3
from datetime import datetime, timedelta, timezone as _tz
from typing import Optional, Dict, Any

from timezone import now, now_iso

# Владелец бота имеет вечный Premium по умолчанию
OWNER_ID = 8470104943


def init_premium_db(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    # Таблица подписок
    conn.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            user_id                     INTEGER PRIMARY KEY,
            plan                        TEXT DEFAULT 'free',
            status                      TEXT DEFAULT 'active',
            starts_at                   TEXT,
            expires_at                  TEXT,
            charge_id                   TEXT
        )
    """)
    # Таблица настроек био для бизнес-подключений
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bio_settings (
            business_connection_id      TEXT PRIMARY KEY,
            user_id                     INTEGER,
            original_bio                TEXT,
            current_bio                 TEXT,
            last_watermark_update       TEXT
        )
    """)
    conn.commit()


def is_premium(conn: sqlite3.Connection, user_id: int) -> bool:
    """Проверяет, активна ли премиум-подписка у пользователя."""
    if user_id == OWNER_ID:
        return True

    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM subscriptions WHERE user_id = ? AND status = 'active'",
        (user_id,),
    ).fetchone()
    if not row:
        return False

    r = dict(row)
    expires_at = r.get("expires_at")
    if not expires_at or expires_at == "lifetime":
        return True

    try:
        exp_dt = datetime.fromisoformat(expires_at)
        if exp_dt.tzinfo is None:
            exp_dt = exp_dt.replace(tzinfo=_tz.utc)
        return now() < exp_dt
    except Exception:
        return False


def get_premium_info(conn: sqlite3.Connection, user_id: int) -> Dict[str, Any]:
    """Возвращает подробную информацию о подписке."""
    if user_id == OWNER_ID:
        return {
            "is_premium": True,
            "plan": "Владелец (Lifetime)",
            "expires_at": "Навсегда",
            "is_owner": True,
        }

    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM subscriptions WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        return {
            "is_premium": False,
            "plan": "Бесплатный (Free)",
            "expires_at": None,
            "is_owner": False,
        }

    r = dict(row)
    active = is_premium(conn, user_id)
    plan_names = {
        "1m": "1 месяц (Stars)",
        "3m": "3 месяца (Stars)",
        "lifetime": "Навсегда (Lifetime)",
    }
    return {
        "is_premium": active,
        "plan": plan_names.get(r.get("plan"), r.get("plan")),
        "expires_at": r.get("expires_at") if r.get("expires_at") != "lifetime" else "Навсегда",
        "is_owner": False,
    }


def activate_premium(
    conn: sqlite3.Connection,
    user_id: int,
    plan: str = "1m",
    days: Optional[int] = 30,
    charge_id: Optional[str] = None,
) -> None:
    """Активирует или продлевает подписку пользователю."""
    cur_info = get_premium_info(conn, user_id)
    starts = now_iso()

    if plan == "lifetime" or days is None:
        expires = "lifetime"
    else:
        # Если уже была активная подписка с датой, прибавляем дни к текущему окончанию
        if cur_info.get("is_premium") and cur_info.get("expires_at") and cur_info["expires_at"] != "Навсегда":
            try:
                base_dt = datetime.fromisoformat(cur_info["expires_at"])
                if base_dt.tzinfo is None:
                    base_dt = base_dt.replace(tzinfo=_tz.utc)
                if base_dt > now():
                    exp_dt = base_dt + timedelta(days=days)
                else:
                    exp_dt = now() + timedelta(days=days)
            except Exception:
                exp_dt = now() + timedelta(days=days)
        else:
            exp_dt = now() + timedelta(days=days)
        expires = exp_dt.isoformat(timespec="seconds")

    conn.execute("""
        INSERT INTO subscriptions (user_id, plan, status, starts_at, expires_at, charge_id)
        VALUES (?, ?, 'active', ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            plan = excluded.plan,
            status = 'active',
            starts_at = excluded.starts_at,
            expires_at = excluded.expires_at,
            charge_id = COALESCE(excluded.charge_id, subscriptions.charge_id)
    """, (user_id, plan, starts, expires, charge_id))
    conn.commit()


def save_original_bio(conn: sqlite3.Connection, bc_id: str, user_id: int, original_bio: str) -> None:
    conn.execute("""
        INSERT INTO bio_settings (business_connection_id, user_id, original_bio, current_bio, last_watermark_update)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(business_connection_id) DO UPDATE SET
            user_id = excluded.user_id,
            original_bio = COALESCE(bio_settings.original_bio, excluded.original_bio),
            current_bio = excluded.current_bio,
            last_watermark_update = excluded.last_watermark_update
    """, (bc_id, user_id, original_bio, original_bio, now_iso()))
    conn.commit()


def get_bio_settings(conn: sqlite3.Connection, bc_id: str) -> Optional[Dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM bio_settings WHERE business_connection_id = ?", (bc_id,)).fetchone()
    return dict(row) if row else None


def get_all_bio_settings(conn: sqlite3.Connection):
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM bio_settings").fetchall()
    return [dict(r) for r in rows]
