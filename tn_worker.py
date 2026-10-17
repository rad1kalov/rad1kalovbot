# tn_worker.py
"""
Высокопроизводительный воркер динамического времени, погоды и ночного статуса в профиле.
Поддержка тысяч пользователей, пул конкурентности Semaphore(20),
интеллектуальное кэширование (пропуск неизменившихся имен), полная изоляция ошибок между пользователями.
"""

import asyncio
import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Optional

import tn_db
from weather_service import get_city_weather

log = logging.getLogger(__name__)


async def set_business_account_name(
    bot,
    business_connection_id: str,
    first_name: str,
    last_name: Optional[str] = None,
) -> bool:
    """Вызывает метод Telegram Bot API setBusinessAccountName через do_api_request."""
    try:
        return await bot.do_api_request(
            "setBusinessAccountName",
            api_kwargs={
                "business_connection_id": business_connection_id,
                "first_name": first_name,
                "last_name": last_name or "",
            },
        )
    except Exception as e:
        log.warning("setBusinessAccountName failed для %s: %s", business_connection_id, e)
        return False


async def update_single_user_name(bot, conn: sqlite3.Connection, c: dict) -> None:
    """Обновляет имя одного пользователя с полной изоляцией."""
    user_id = c.get("user_id")
    bc_id = c.get("business_connection_id")
    first_name = c.get("first_name")

    if not user_id or not bc_id or not first_name:
        return

    offset = c.get("tz_offset", 3)
    if offset is None:
        offset = 3

    tz = timezone(timedelta(hours=offset))
    now_local = datetime.now(tz)
    hour = now_local.hour
    time_str = now_local.strftime("%H:%M")

    # 1. Проверяем ночной статус (23:00 - 08:00)
    if c.get("night_status_enabled") and (hour >= 23 or hour < 8):
        target_last_name = "[🌙 Сплю]"
    else:
        # 2. Если время отключено пользователем, ночной статус днем не ставит время
        if not c.get("time_enabled"):
            target_last_name = ""
        else:
            weather_text = ""
            if c.get("weather_enabled") and c.get("weather_city"):
                w_res = await get_city_weather(c["weather_city"])
                if w_res:
                    weather_text = f" {w_res}"
            target_last_name = f"[{time_str}{weather_text}]"

    # Интеллектуальный пропуск: если фамилия не изменилась с прошлого раза, не спамим Telegram API
    if c.get("last_set_last_name") == target_last_name:
        return

    ok = await set_business_account_name(
        bot,
        business_connection_id=bc_id,
        first_name=first_name,
        last_name=target_last_name,
    )

    if ok:
        tn_db.update_last_set_name(conn, user_id, target_last_name)


async def name_worker(app, conn: sqlite3.Connection, interval_seconds: int = 60):
    """
    Каждую минуту обновляет фамилию бизнес-аккаунта:
    - Конкурентный пул Semaphore(20)
    - Ошибки одного пользователя никак не влияют на других
    """
    log.info("Воркер динамического времени и погоды запущен")
    sem = asyncio.Semaphore(20)

    async def _safe_update(c: dict):
        async with sem:
            try:
                await update_single_user_name(app.bot, conn, c)
            except Exception as e:
                log.debug(f"Ошибка обновления имени для user {c.get('user_id')}: {e}")

    while True:
        try:
            # Получаем только активных пользователей с включенным временем или ночным статусом
            active_users = tn_db.get_active_time_users(conn)
            if active_users:
                tasks = [_safe_update(u) for u in active_users]
                await asyncio.gather(*tasks, return_exceptions=True)

        except Exception as e:
            log.error(f"name_worker критическая ошибка: {e}")

        # Выравниваем сон ровно до начала следующей минуты
        now_sec = datetime.now().second
        sleep_time = max(1, interval_seconds - now_sec)
        await asyncio.sleep(sleep_time)