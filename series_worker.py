# series_worker.py
"""Фоновый воркер: обработка смены суток, автозаморозка в 23:30 и вечерние напоминания."""

import asyncio
import logging
import sqlite3
import os

from series import burn_stale_streaks, plural_days
from stages import stage_name
from timezone import now, today_str
from series_db import log_event
from admin_db import has_feature_access

log = logging.getLogger(__name__)
OWNER_ID = int(os.getenv("OWNER_ID", "8470104943"))


async def series_worker(app, conn: sqlite3.Connection):
    """
    Фоновый процесс:
    - При смене дня сжигает пропущенные серии и обновляет лимиты.
    - В 21:00 аккуратно напоминает об активности тем, у кого включены напоминания и серия под угрозой.
    - В 23:30 автоматически замораживает стрик у тех, у кого включена автозаморозка (Premium).
    """
    last_midnight_date = today_str()
    last_reminder_date = None
    last_autofreeze_date = None

    log.info("Series worker успешно запущен")

    while True:
        try:
            current = now()
            today = today_str()

            # 1. Смена суток: гарантированная обработка при наступлении нового дня
            if last_midnight_date != today:
                log.info("Series worker: смена суток, обработка серий...")
                burn_stale_streaks(conn)
                last_midnight_date = today

            # 2. Вечерние напоминания в 21:00
            if current.hour >= 21 and last_reminder_date != today:
                log.info("Series worker: отправка вечерних напоминаний в 21:00...")
                await send_reminders(app, conn)
                last_reminder_date = today

            # 3. Автозаморозка в 23:30 (Премиум функция)
            if (current.hour == 23 and current.minute >= 30) and last_autofreeze_date != today:
                log.info("Series worker: проверка автозаморозки в 23:30...")
                await run_auto_freeze(app, conn)
                last_autofreeze_date = today

        except Exception as e:
            log.error(f"series_worker error: {e}")

        await asyncio.sleep(60)


async def run_auto_freeze(app, conn: sqlite3.Connection):
    conn.row_factory = sqlite3.Row
    today = today_str()

    rows = conn.execute("""
        SELECT * FROM series
        WHERE enabled = 1 AND auto_freeze = 1 AND current_streak > 0
          AND frozen = 0 AND (last_date IS NULL OR last_date != ?)
    """, (today,)).fetchall()

    for row in rows:
        r = dict(row)
        chat_id = r["chat_id"]
        freezes_left = r.get("freezes_left", 0)

        # Проверяем наличие доступа к автозаморозке
        if not has_feature_access(conn, OWNER_ID, "auto_freeze") and not has_feature_access(conn, chat_id, "auto_freeze"):
            continue

        if freezes_left <= 0:
            log.info(f"Автозаморозка для чата {chat_id} пропущена: закончились заморозки")
            continue

        new_freezes = freezes_left - 1
        conn.execute("""
            UPDATE series
            SET frozen = 1, freezes_left = ?, last_date = ?
            WHERE chat_id = ?
        """, (new_freezes, today, chat_id))
        conn.commit()

        log_event(conn, chat_id, "freeze_applied", by_user="auto_freeze")

        chat_name = r.get("last_user_name") or f"чат {chat_id}"
        streak = r["current_streak"]

        notify_text = (
            "⚡️ *Сработала Автозаморозка в 23:30!*\n\n"
            f"Серия общения в чате *{chat_name}* (🔥 *{streak} {plural_days(streak)}*) "
            "была автоматически заморожена, чтобы спасти её от случайного сгорания. ❄️\n\n"
            f"Осталось заморозок в этом месяце: *{new_freezes}*."
        )

        try:
            await app.bot.send_message(
                chat_id=OWNER_ID,
                text=notify_text,
                parse_mode="Markdown",
            )
            log.info(f"Автозаморозка успешно выполнена для чата {chat_id}")
        except Exception as e:
            log.error(f"Failed to notify owner about auto-freeze: {e}")


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
            "или используйте заморозку, чтобы сохранить её на сегодня ❄️"
        )

        try:
            await app.bot.send_message(
                chat_id=r["chat_id"],
                text=text,
                business_connection_id=r["business_connection_id"],
                parse_mode="Markdown",
            )
            log.info(f"Напоминание отправлено в чат {r['chat_id']}")
        except Exception as e:
            log.error(f"send_reminders failed for {r['chat_id']}: {e}")