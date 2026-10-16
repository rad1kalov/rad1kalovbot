# tn_worker.py
"""Воркер динамического времени в профиле: обновляет last_name каждую минуту."""

import asyncio
import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Optional

import tn_db

log = logging.getLogger(__name__)


async def set_business_account_name(
    bot,
    business_connection_id: str,
    first_name: str,
    last_name: Optional[str] = None,
) -> bool:
    """
    Вызывает метод Telegram Bot API setBusinessAccountName через do_api_request.
    """
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
    Каждую минуту обновляет last_name бизнес-аккаунта
    на текущее локальное время в формате [HH:MM].
    """
    log.info("Воркер динамического времени (каждую минуту) запущен")

    while True:
        try:
            connections = tn_db.get_all_connections(conn)
            for c in connections:
                bc_id = c["business_connection_id"]
                offset = c["tz_offset"] if c["tz_offset"] is not None else 3
                first_name = c["first_name"]

                if not first_name:
                    continue

                tz = timezone(timedelta(hours=offset))
                time_str = datetime.now(tz).strftime("[%H:%M]")

                await set_business_account_name(
                    app.bot,
                    business_connection_id=bc_id,
                    first_name=first_name,
                    last_name=time_str,
                )

        except Exception as e:
            log.error("name_worker ошибка: %s", e)

        # Выравниваем сон ровно до начала следующей минуты
        now_sec = datetime.now().second
        sleep_time = max(1, interval_seconds - now_sec)
        await asyncio.sleep(sleep_time)