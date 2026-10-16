# tn_commands.py
"""Обработка команды .time и инлайн-выбора часового пояса для авто-времени в профиле."""

import logging
import sqlite3
from datetime import datetime, timezone, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

import tn_db
from tn_worker import set_business_account_name

log = logging.getLogger(__name__)

TIMEZONES = [
    ("⭐️ Московское время (UTC+3)", 3),
    ("Калининград (UTC+2)", 2),
    ("Самара (UTC+4)", 4),
    ("Екатеринбург (UTC+5)", 5),
    ("Омск (UTC+6)", 6),
    ("Красноярск / Новосибирск (UTC+7)", 7),
    ("Иркутск (UTC+8)", 8),
    ("Якутск (UTC+9)", 9),
    ("Владивосток (UTC+10)", 10),
]


def get_current_offset(conn: sqlite3.Connection, bc_id: str) -> int:
    cur = conn.execute(
        "SELECT tz_offset FROM settings WHERE business_connection_id = ?",
        (bc_id,),
    )
    row = cur.fetchone()
    return row[0] if (row and row[0] is not None) else 3


def build_timezone_keyboard(selected_offset: int) -> InlineKeyboardMarkup:
    """Создаёт инлайн-клавиатуру выбора часового пояса с закреплённым Московским временем."""
    keyboard = []

    # Закреплённое Московское время в самом верху
    moscow_label = "⭐️ Московское время (UTC+3)" + (" ✅" if selected_offset == 3 else "")
    keyboard.append([
        InlineKeyboardButton(moscow_label, callback_data="tz:set:3")
    ])

    # Остальные часовые пояса парами
    other_buttons = []
    for title, offset in TIMEZONES:
        if offset == 3:
            continue
        mark = " ✅" if selected_offset == offset else ""
        other_buttons.append(
            InlineKeyboardButton(f"{title}{mark}", callback_data=f"tz:set:{offset}")
        )

    for i in range(0, len(other_buttons), 2):
        keyboard.append(other_buttons[i:i + 2])

    # Кнопка мгновенного обновления
    keyboard.append([
        InlineKeyboardButton("🔄 Обновить время в профиле сейчас", callback_data="tz:refresh")
    ])

    return InlineKeyboardMarkup(keyboard)


def get_time_text(offset: int) -> str:
    tz = timezone(timedelta(hours=offset))
    cur_time = datetime.now(tz).strftime("%H:%M")
    return (
        "🕒 *Настройка времени в профиле (обновление каждую минуту)*\n\n"
        f"Текущее установленное время: *[{cur_time}]* (UTC{offset:+d})\n\n"
        "Фамилия вашего бизнес-аккаунта каждую минуту меняется на точное время `[ЧЧ:ММ]`.\n"
        "Выберите ваш часовой пояс кнопками ниже 👇"
    )


async def handle_time_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE, conn: sqlite3.Connection
) -> bool:
    """
    Обрабатывает команду .time: отправляет сообщение с инлайн-кнопками для выбора часового пояса.
    """
    msg = update.business_message or update.message
    if not msg or not msg.text:
        return False

    text = msg.text.strip().lower()
    if text != ".time" and not text.startswith(".time "):
        return False

    bc_id = getattr(msg, "business_connection_id", None)
    if not bc_id:
        # Проверяем, есть ли сохранённые подключения
        all_conns = tn_db.get_all_connections(conn)
        if all_conns:
            bc_id = all_conns[0]["business_connection_id"]
        else:
            await msg.reply_text(
                "⚠️ Команда доступна в бизнес-чате или после подключения бота к Telegram Business."
            )
            return True

    # Сохраняем имя владельца
    if msg.from_user and isinstance(getattr(msg.from_user, "first_name", None), str):
        tn_db.save_first_name(conn, bc_id, msg.from_user.first_name)

    current_offset = get_current_offset(conn, bc_id)
    keyboard = build_timezone_keyboard(current_offset)
    text_content = get_time_text(current_offset)

    try:
        await context.bot.send_message(
            chat_id=msg.chat_id,
            text=text_content,
            reply_markup=keyboard,
            business_connection_id=bc_id,
            parse_mode="Markdown",
        )
    except Exception as e:
        log.error(f"send_message failed in .time: {e}")

    return True


async def handle_time_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE, conn: sqlite3.Connection
) -> bool:
    """Обрабатывает нажатия на инлайн-кнопки часовых поясов."""
    query = update.callback_query
    if not query or not query.data or not query.data.startswith("tz:"):
        return False

    bc_id = getattr(query.message, "business_connection_id", None)
    if not bc_id:
        all_conns = tn_db.get_all_connections(conn)
        if all_conns:
            bc_id = all_conns[0]["business_connection_id"]

    if query.data.startswith("tz:set:"):
        try:
            offset = int(query.data.split(":")[-1])
        except ValueError:
            offset = 3

        if bc_id:
            tn_db.set_tz_offset(conn, bc_id, offset)

            # Пробуем мгновенно обновить имя профиля
            cur = conn.execute(
                "SELECT first_name FROM settings WHERE business_connection_id = ?",
                (bc_id,),
            )
            row = cur.fetchone()
            first_name = row[0] if row and row[0] else None

            if first_name:
                tz = timezone(timedelta(hours=offset))
                time_str = datetime.now(tz).strftime("[%H:%M]")
                await set_business_account_name(
                    context.bot,
                    business_connection_id=bc_id,
                    first_name=first_name,
                    last_name=time_str,
                )

        await query.answer(f"✅ Часовой пояс установлен: UTC{offset:+d}")

        try:
            await query.edit_message_text(
                text=get_time_text(offset),
                reply_markup=build_timezone_keyboard(offset),
                parse_mode="Markdown",
            )
        except Exception as e:
            log.error(f"edit_message_text failed in time callback: {e}")

        return True

    elif query.data == "tz:refresh":
        cur_offset = get_current_offset(conn, bc_id) if bc_id else 3
        if bc_id:
            cur = conn.execute(
                "SELECT first_name FROM settings WHERE business_connection_id = ?",
                (bc_id,),
            )
            row = cur.fetchone()
            first_name = row[0] if row and row[0] else None
            if first_name:
                tz = timezone(timedelta(hours=cur_offset))
                time_str = datetime.now(tz).strftime("[%H:%M]")
                await set_business_account_name(
                    context.bot,
                    business_connection_id=bc_id,
                    first_name=first_name,
                    last_name=time_str,
                )

        await query.answer("🔄 Время в профиле обновлено!")
        try:
            await query.edit_message_text(
                text=get_time_text(cur_offset),
                reply_markup=build_timezone_keyboard(cur_offset),
                parse_mode="Markdown",
            )
        except Exception as e:
            pass
        return True

    return False