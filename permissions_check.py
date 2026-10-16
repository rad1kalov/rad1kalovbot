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
