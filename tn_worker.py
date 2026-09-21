import asyncio
import logging
import sqlite3
from datetime import datetime, timezone, timedelta

import tn_db

log = logging.getLogger(__name__)


async def name_worker(app, conn: sqlite3.Connection, interval_seconds: int = 3600):
    """
    Раз в interval_seconds обновляет last_name бизнес-аккаунта
    на текущее время в формате [HH:MM].
    """
    while True:
        try:
            connections = tn_db.get_all_connections(conn)
            for c in connections:
                bc_id = c["business_connection_id"]
                offset = c["tz_offset"] or 0
                first_name = c["first_name"]

                if not first_name:
                    log.warning("skip %s: first_name неизвестен", bc_id)
                    continue

                tz = timezone(timedelta(hours=offset))
                time_str = datetime.now(tz).strftime("[%H:%M]")

                try:
                    await app.bot.set_business_account_name(
                        business_connection_id=bc_id,
                        first_name=first_name,
                        last_name=time_str,
                    )
                    log.info("обновил имя %s -> %s %s", bc_id, first_name, time_str)
                except Exception as e:
                    log.error("set_business_account_name failed для %s: %s", bc_id, e)
        except Exception as e:
            log.error("name_worker ошибка: %s", e)

        await asyncio.sleep(interval_seconds)