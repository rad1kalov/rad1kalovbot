# series_worker.py
"""Фоновый воркер: полночь + напоминания в 21:00."""

import asyncio
import logging
import sqlite3

from series import burn_stale_streaks, plural_days
from stages import stage_name
from timezone import now, today_str


async def series_worker(app, conn: sqlite3.Connection):
    last_midnight_date = None
    last_reminder_date = None

    logging.info("Series worker запущен")

    while True:
        try:
            current = now()
            today = today_str()

            # Полночные задачи: 00:00–00:04
            if current.hour == 0 and current.minute < 5 and last_midnight_date != today:
                logging.info("Series worker: полночные задачи")
                burn_stale_streaks(conn)
                last_midnight_date = today

            # Напоминания: 21:00–21:04
            if current.hour == 21 and current.minute < 5 and last_reminder_date != today:
                logging.info("Series worker: напоминания 21:00")
                await send_reminders(app, conn)
                last_reminder_date = today

        except Exception as e:
            logging.error(f"series_worker error: {e}")

        await asyncio.sleep(60)


async def send_reminders(app, conn: sqlite3.Connection):
    conn.row_factory = sqlite3.Row
    today = today_str()

    rows = conn.execute(
        "SELECT * FROM series "
        "WHERE reminders = 1 AND current_streak > 0 "
        "AND (last_date IS NULL OR last_date != ?)",
        (today,),
    ).fetchall()

    for row in rows:
        r = dict(row)
        if not r["business_connection_id"]:
            continue
        text = (
            "⚠️ *Серия скоро сгорит!*\n"
            f"🔥 Текущая серия: {r['current_streak']} {plural_days(r['current_streak'])}\n"
            f"🏆 Рекорд: {r['best_streak']} {plural_days(r['best_streak'])}\n"
            f"🎭 Стадия: {stage_name(r['stage'])}\n"
            "⏳ Напишите что-нибудь до полуночи!"
        )
        try:
            await app.bot.send_message(
                chat_id=r["chat_id"],
                text=text,
                business_connection_id=r["business_connection_id"],
                parse_mode="Markdown",
            )
        except Exception as e:
            logging.error(f"send_reminders failed for {r['chat_id']}: {e}")