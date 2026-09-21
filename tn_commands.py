import logging
import sqlite3
from telegram import Update
from telegram.ext import ContextTypes

import tn_db

log = logging.getLogger(__name__)


async def handle_time_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE, conn: sqlite3.Connection
) -> bool:
    """
    Обрабатывает .time <utc>.
    Возвращает True, если команда была обработана (и вызывающий код
    должен прекратить дальнейшую обработку сообщения).
    """
    msg = update.business_message
    if not msg or not msg.text:
        return False
    if not msg.text.startswith(".time"):
        return False

    if not msg.business_connection_id:
        await context.bot.send_message(
            chat_id=msg.chat_id,
            text="Команда работает только в бизнес-чате.",
            business_connection_id=msg.business_connection_id,
        )
        return True

    # сохраняем first_name владельца
    if msg.from_user and msg.from_user.first_name:
        tn_db.save_first_name(conn, msg.business_connection_id, msg.from_user.first_name)

    args = msg.text[len(".time"):].strip()

    if not args:
        await context.bot.send_message(
            chat_id=msg.chat_id,
            text="Укажи оффсет: .time +3 или .time -5",
            business_connection_id=msg.business_connection_id,
        )
        return True

    raw = args.replace("+", "")
    try:
        offset = int(raw)
    except ValueError:
        await context.bot.send_message(
            chat_id=msg.chat_id,
            text="Оффсет должен быть числом, например: .time +3",
            business_connection_id=msg.business_connection_id,
        )
        return True

    if offset < -12 or offset > 14:
        await context.bot.send_message(
            chat_id=msg.chat_id,
            text="Оффсет вне допустимого диапазона (-12..+14).",
            business_connection_id=msg.business_connection_id,
        )
        return True

    tn_db.set_tz_offset(conn, msg.business_connection_id, offset)
    await context.bot.send_message(
        chat_id=msg.chat_id,
        text=f"Оффсет установлен: UTC{offset:+d}",
        business_connection_id=msg.business_connection_id,
    )
    return True