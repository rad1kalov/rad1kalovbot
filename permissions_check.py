# permissions_check.py
"""Проверка прав доступа в Telegram Business (Правило 999).

Если нету доступа к изменению описания И имени, не работает ни .time, ни .description.
"""

import logging
from typing import Tuple, Optional
import sqlite3

log = logging.getLogger(__name__)

RULE_999_NOTICE = (
    "⚠️ *Недостаточно прав в Telegram Business!*\n\n"
    "Для работы функций изменения профиля необходимо предоставить боту права "
    "на изменение **имени** И **описания** в настройках Telegram Business.\n\n"
    "⚙️ *Как включить:*\n"
    "1. Перейдите в *Настройки Telegram* ➔ *Telegram для бизнеса* ➔ *Чат-боты*.\n"
    "2. Выберите нашего бота.\n"
    "3. Убедитесь, что боту разрешено управление бизнес-аккаунтом, "
    "включая изменение **имени** и **описания** профиля."
)


async def check_name_and_bio_permissions(
    bot,
    conn: sqlite3.Connection,
    business_connection_id: Optional[str],
) -> Tuple[bool, str]:
    """
    Проверяет, есть ли у бота права на редактирование имени И описания.
    Если хотя бы одного права нет или соединение не активно — возвращает (False, RULE_999_NOTICE).
    """
    if not business_connection_id:
        return False, (
            "⚠️ Бот не подключен к Telegram Business.\n\n"
            "Подключите бота в *Настройки* ➔ *Telegram для бизнеса* ➔ *Чат-боты*."
        )

    try:
        bc = await bot.get_business_connection(business_connection_id)
    except Exception as e:
        log.warning(f"get_business_connection({business_connection_id}) failed: {e}")
        return False, RULE_999_NOTICE

    if not bc or not getattr(bc, "is_enabled", False):
        return False, "⚠️ Подключение к Telegram Business временно отключено пользователем."

    raw = {}
    if hasattr(bc, "to_dict"):
        raw = bc.to_dict()
    elif hasattr(bc, "api_kwargs") and bc.api_kwargs:
        raw = bc.api_kwargs

    # Проверяем явные флаги запрета, если Telegram передаёт детальные права
    can_name = raw.get("can_edit_name", raw.get("can_change_name", True))
    can_bio = raw.get("can_edit_bio", raw.get("can_change_bio", True))

    if can_name is False or can_bio is False:
        return False, RULE_999_NOTICE

    # Дополнительно проверяем базовое право отвечать/управлять
    if hasattr(bc, "can_reply") and not bc.can_reply:
        return False, RULE_999_NOTICE

    return True, ""


async def get_connection_owner_id(bot, conn: sqlite3.Connection, conn_id: Optional[str]) -> Optional[int]:
    """Возвращает Telegram user_id владельца бизнес-подключения и кэширует его в БД."""
    if not conn_id:
        return None

    cur = conn.execute("SELECT user_id FROM settings WHERE business_connection_id = ?", (conn_id,))
    row = cur.fetchone()
    if row and row[0]:
        return row[0]

    cur2 = conn.execute("SELECT user_id FROM bio_settings WHERE business_connection_id = ?", (conn_id,))
    row2 = cur2.fetchone()
    if row2 and row2[0]:
        return row2[0]

    try:
        bc = await bot.get_business_connection(conn_id)
        if bc and bc.user:
            owner_id = bc.user.id
            import tn_db
            tn_db.upsert_user_connection(conn, owner_id, conn_id, bc.user.first_name or "", is_active=True)
            conn.execute("UPDATE bio_settings SET user_id = ? WHERE business_connection_id = ?", (owner_id, conn_id))
            conn.commit()
            return owner_id
    except Exception as e:
        log.warning(f"get_business_connection failed for {conn_id}: {e}")

    return None


async def is_own_business_message(bot, conn: sqlite3.Connection, msg) -> bool:
    """
    Проверяет, что сообщение отправлено владельцем бизнес-аккаунта (своё сообщение),
    а не собеседником (клиентом/другом).
    """
    if not msg:
        return False

    sender = getattr(msg, "from_user", None)
    if not sender:
        return False

    sender_id = sender.id
    conn_id = getattr(msg, "business_connection_id", None)
    # Если это сообщение в личке напрямую с ботом, считаем своим
    if not conn_id:
        return True

    # В бизнес-чатах chat_id — это ID собеседника (клиента).
    # Если sender_id равен chat_id, сообщение 100% от собеседника!
    chat_id = getattr(msg, "chat_id", None)
    if chat_id == sender_id:
        return False

    owner_id = await get_connection_owner_id(bot, conn, conn_id)
    if owner_id:
        return sender_id == owner_id

    # Fallback: если sender_id != chat_id, значит отправитель — владелец чата
    return chat_id != sender_id

