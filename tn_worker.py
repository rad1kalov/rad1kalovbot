import asyncio
import logging
from datetime import datetime, timezone, timedelta
from aiogram import Bot
import tn_db

log = logging.getLogger(__name__)

async def update_name_loop(bot: Bot, interval_seconds:):
    while True:
        try:
            connections = await tn_db.get_all_connections()
            for conn in connections:
                bc_id = conn["business_connection_id"]
                offset = conn.get("tz_offset") or 0
                first_name = conn.get("first_name")

                if not first_name:
                    log.warning("skip %s: first_name неизвестен", bc_id)
                    continue

                tz = timezone(timedelta(hours=offset))
                time_str = datetime.now(tz).strftime("[%H:%M]")

                try:
                    await bot.set_business_account_name(
                        business_connection_id=bc_id,
                        first_name=first_name,
                        last_name=time_str,
                    )
                    log.info("обновил имя %s -> %s %s", bc_id, first_name, time_str)
                except Exception as e:
                    log.error("set_business_account_name failed для %s: %s", bc_id, e)
        except Exception as e:
            log.error("tn_worker ошибка: %s", e)

        await asyncio.sleep(interval_seconds)