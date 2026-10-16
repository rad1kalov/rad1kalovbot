# series_worker.py
"""Фоновый воркер: обработка смены суток и вечерние напоминания."""

import asyncio
import logging
import sqlite3

from series import burn_stale_streaks, plural_days
from stages import stage_name
from timezone import now, today_str


async def series_worker(app, conn: sqlite3.Connection):
    """
    Фоновый процесс:
    - При смене дня (в полночь или после рестарта) сжигает пропущенные серии и обновляет лимиты.
    - В 21:00 аккуратно напоминает об активности тем, у кого включены напоминания и серия под угрозой.
    """
    last_midnight_date = today_str()
    last_reminder_date = None

    logging.info("Series worker успешно запущен")

    while True:
        try:
            current = now()
            today = today_str()

            # Смена суток: срабатывает гарантированно при наступлении нового дня
            if last_midnight_date != today:
                logging.info("Series worker: смена суток, обработка серий...")
                burn_stale_streaks(conn)
                last_midnight_date = today

            # Вечерние напоминания: срабатывают после 21:00 один раз в день
            if current.hour >= 21 and last_reminder_date != today:
                logging.info("Series worker: отправка вечерних напоминаний в 21:00...")
                await send_reminders(app, conn)
                last_reminder_date = today

        except Exception as e:
            logging.error(f"series_worker error: {e}")

        await asyncio.sleep(60)


async def send_reminders(app, conn: sqlite3.Connection):
    conn.row_factory = sqlite3.Row
    today = today_str()

    rows = conn.execute("""
        SELECT * FROM series 
        WHERE enabled = 1 AND reminders = 1 AND current_streak > 0 
          AND frozen = 0 AND (last_date IS NULL OR last_date != ?)
    """, (today,)).fetchall()

    for row in rows:
        r = dict(row)
        if not r.get("business_connection_id"):
            continue

        streak = r["current_streak"]
        st_name = stage_name(r["stage"])

        text = (
            "⏳ *Напоминание о серии общения*\n\n"
            f"🔥 Текущая серия: *{streak} {plural_days(streak)}*\n"
            f"🎭 Стадия: *{st_name}*\n\n"
            "До полуночи остаётся немного времени. Отправьте сообщение, чтобы продлить серию, "
            "или напишите `.freeze`, чтобы сохранить её на сегодня ❄️"
        )

        try:
            await app.bot.send_message(
                chat_id=r["chat_id"],
                text=text,
                business_connection_id=r["business_connection_id"],
                parse_mode="Markdown",
            )
            logging.info(f"Напоминание отправлено в чат {r['chat_id']}")
        except Exception as e:
            logging.error(f"send_reminders failed for {r['chat_id']}: {e}")