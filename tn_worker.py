# tn_worker.py
"""Воркер динамического времени, погоды и ночного статуса в профиле."""

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


async def name_worker(app, conn: sqlite3.Connection, interval_seconds: int = 60):
    """
    Каждую минуту обновляет last_name бизнес-аккаунта:
    - При активном ночном статусе с 23:00 до 08:00: [🌙 Сплю]
    - С погодой (Premium): [HH:MM +18° ⛅️]
    - Стандартно: [HH:MM]
    """
    log.info("Воркер динамического времени, погоды и ночного статуса запущен")

    while True:
        try:
            connections = tn_db.get_all_connections(conn)
            for c in connections:
                # Обновляем профиль ТОЛЬКО если пользователь сам включил время и подключение активно
                if not c.get("time_enabled") or c.get("is_active") == 0:
                    continue

                bc_id = c["business_connection_id"]
                offset = c["tz_offset"] if c.get("tz_offset") is not None else 3
                first_name = c.get("first_name")

                if not first_name or not bc_id:
                    continue

                tz = timezone(timedelta(hours=offset))
                now_local = datetime.now(tz)
                hour = now_local.hour
                time_str = now_local.strftime("%H:%M")

                # 1. Проверяем ночной статус (23:00 - 08:00)
                if c.get("night_status_enabled") and (hour >= 23 or hour < 8):
                    target_last_name = "[🌙 Сплю]"
                else:
                    # 2. Проверяем погоду в имени
                    weather_text = ""
                    if c.get("weather_enabled") and c.get("weather_city"):
                        w_res = await get_city_weather(c["weather_city"])
                        if w_res:
                            weather_text = f" {w_res}"

                    target_last_name = f"[{time_str}{weather_text}]"

                await set_business_account_name(
                    app.bot,
                    business_connection_id=bc_id,
                    first_name=first_name,
                    last_name=target_last_name,
                )

        except Exception as e:
            log.error("name_worker ошибка: %s", e)

        # Выравниваем сон до начала следующей минуты
        now_sec = datetime.now().second
        sleep_time = max(1, interval_seconds - now_sec)
        await asyncio.sleep(sleep_time)