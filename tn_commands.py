# tn_commands.py
"""Обработка команды .time, погоды в имени и ночного статуса для профиля."""

import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

import tn_db
from tn_worker import set_business_account_name
from permissions_check import check_name_and_bio_permissions
from admin_db import has_feature_access
from weather_service import get_city_weather

log = logging.getLogger(__name__)

TIMEZONES = [
    ("⭐️ Москва (UTC+3)", 3),
    ("Калининград (UTC+2)", 2),
    ("Самара (UTC+4)", 4),
    ("Екатеринбург (UTC+5)", 5),
    ("Омск (UTC+6)", 6),
    ("Красноярск (UTC+7)", 7),
    ("Иркутск (UTC+8)", 8),
    ("Якутск (UTC+9)", 9),
    ("Владивосток (UTC+10)", 10),
]


def get_current_offset(conn: sqlite3.Connection, bc_id: str) -> int:
    s = tn_db.get_setting(conn, bc_id)
    return s["tz_offset"] if (s and s.get("tz_offset") is not None) else 3


def build_timezone_keyboard(conn: sqlite3.Connection, bc_id: str, user_id: int) -> InlineKeyboardMarkup:
    s = tn_db.get_setting(conn, bc_id) or {}
    selected_offset = s.get("tz_offset", 3)

    keyboard = []

    # Закреплённое Московское время в самом верху
    moscow_label = "⭐️ Москва (UTC+3)" + (" ✅" if selected_offset == 3 else "")
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

    # Премиум-кнопки: Погода и Ночной статус
    w_on = bool(s.get("weather_enabled", 0))
    w_city = s.get("weather_city") or "город не задан"
    weather_btn = f"🌦 Погода: {w_city} ({'ВКЛ ✅' if w_on else 'ВЫКЛ ❌'})"

    n_on = bool(s.get("night_status_enabled", 0))
    night_btn = f"🌙 Ночной статус [🌙 Сплю] ({'ВКЛ ✅' if n_on else 'ВЫКЛ ❌'})"

    keyboard.append([
        InlineKeyboardButton(weather_btn, callback_data="tz:tog_weather")
    ])
    keyboard.append([
        InlineKeyboardButton(night_btn, callback_data="tz:tog_night")
    ])

    # Кнопка мгновенного обновления
    keyboard.append([
        InlineKeyboardButton("🔄 Обновить в профиле сейчас", callback_data="tz:refresh")
    ])

    return InlineKeyboardMarkup(keyboard)


def get_time_text(conn: sqlite3.Connection, bc_id: str) -> str:
    s = tn_db.get_setting(conn, bc_id) or {}
    offset = s.get("tz_offset", 3)
    tz = timezone(timedelta(hours=offset))
    cur_time = datetime.now(tz).strftime("%H:%M")

    w_city = s.get("weather_city")
    w_on = bool(s.get("weather_enabled", 0))
    n_on = bool(s.get("night_status_enabled", 0))

    extra_lines = []
    if w_on and w_city:
        extra_lines.append(f"• 🌦 Погода: `{w_city}` (активна в имени)")
    elif w_city:
        extra_lines.append(f"• 🌦 Погода: `{w_city}` (отключена)")

    if n_on:
        extra_lines.append("• 🌙 Ночной статус: активен (с 23:00 до 08:00 `[🌙 Сплю]`)")

    extra_info = ("\n" + "\n".join(extra_lines)) if extra_lines else ""

    return (
        "🕒 *Настройка времени и статуса в профиле*\n\n"
        f"Текущее установленное время: *[{cur_time}]* (UTC{offset:+d}){extra_info}\n\n"
        "Фамилия вашего бизнес-аккаунта обновляется каждую минуту: `[ЧЧ:ММ]`.\n"
        "💡 _Для установки города погоды отправьте: `.weather <город>`_\n\n"
        "Выберите ваш часовой пояс и настройки кнопками ниже 👇"
    )


async def handle_time_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE, conn: sqlite3.Connection
) -> bool:
    """Обрабатывает команду .time с проверкой Правила 999."""
    msg = update.business_message or update.message
    if not msg or not msg.text:
        return False

    text = msg.text.strip().lower()
    if text != ".time" and not text.startswith(".time "):
        return False

    bc_id = getattr(msg, "business_connection_id", None)
    if not bc_id:
        all_conns = tn_db.get_all_connections(conn)
        if all_conns:
            bc_id = all_conns[0]["business_connection_id"]
        else:
            await msg.reply_text(
                "⚠️ Команда доступна в бизнес-чате или после подключения бота к Telegram Business."
            )
            return True

    # ───── ПРАВИЛО 999 ─────
    ok, err_msg = await check_name_and_bio_permissions(context.bot, conn, bc_id)
    if not ok:
        try:
            await context.bot.send_message(
                chat_id=msg.chat_id,
                text=err_msg,
                business_connection_id=bc_id,
                parse_mode="Markdown",
            )
        except Exception:
            await msg.reply_text(err_msg, parse_mode="Markdown")
        return True
    # ────────────────────────

    user_id = msg.from_user.id if msg.from_user else 0
    if msg.from_user and isinstance(getattr(msg.from_user, "first_name", None), str):
        tn_db.save_first_name(conn, bc_id, msg.from_user.first_name, user_id=user_id)

    keyboard = build_timezone_keyboard(conn, bc_id, user_id)
    text_content = get_time_text(conn, bc_id)

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


async def handle_weather_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE, conn: sqlite3.Connection
) -> bool:
    """Обрабатывает команду .weather <город> или /weather <город>."""
    msg = update.business_message or update.message
    if not msg or not msg.text:
        return False

    raw = msg.text.strip()
    parts = raw.split(maxsplit=1)
    cmd = parts[0].lower()
    if cmd not in (".weather", "/weather", ".погода", "/погода"):
        return False

    user_id = msg.from_user.id if msg.from_user else 0
    if not has_feature_access(conn, user_id, "weather_name"):
        await msg.reply_text(
            "🔒 *Функция «Погода в имени» доступна по Премиум подписке!*\n\n"
            "Оформите подписку через /premium, чтобы добавить температуру и статус погоды в фамилию профиля.",
            parse_mode="Markdown",
        )
        return True

    if len(parts) < 2 or not parts[1].strip():
        await msg.reply_text(
            "📍 *Укажите ваш город!*\n\nПример использования:\n`.weather Москва`\n`.weather Санкт-Петербург`",
            parse_mode="Markdown",
        )
        return True

    city = parts[1].strip()
    bc_id = getattr(msg, "business_connection_id", None)
    if not bc_id:
        all_conns = tn_db.get_all_connections(conn)
        if all_conns:
            bc_id = all_conns[0]["business_connection_id"]

    if bc_id:
        tn_db.set_weather_settings(conn, bc_id, city=city, enabled=True)

    # Пробуем запросить погоду для проверки
    weather_sample = await get_city_weather(city)
    status_sample = f" `{weather_sample}`" if weather_sample else ""

    await msg.reply_text(
        f"✅ *Город успешно установлен:* `{city}`{status_sample}\n\n"
        f"Погода включена и теперь каждую минуту будет транслироваться в имени вашего бизнес-аккаунта!",
        parse_mode="Markdown",
    )
    return True


async def handle_time_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE, conn: sqlite3.Connection
) -> bool:
    """Обрабатывает нажатия на инлайн-кнопки часовых поясов, погоды и ночного статуса."""
    query = update.callback_query
    if not query or not query.data or not query.data.startswith("tz:"):
        return False

    bc_id = getattr(query.message, "business_connection_id", None)
    if not bc_id:
        all_conns = tn_db.get_all_connections(conn)
        if all_conns:
            bc_id = all_conns[0]["business_connection_id"]

    user_id = query.from_user.id if query.from_user else 0

    if query.data.startswith("tz:set:"):
        try:
            offset = int(query.data.split(":")[-1])
        except ValueError:
            offset = 3

        if bc_id:
            tn_db.set_tz_offset(conn, bc_id, offset)

        await query.answer(f"✅ Часовой пояс установлен: UTC{offset:+d}")
        if bc_id:
            try:
                await query.edit_message_text(
                    text=get_time_text(conn, bc_id),
                    reply_markup=build_timezone_keyboard(conn, bc_id, user_id),
                    parse_mode="Markdown",
                )
            except Exception:
                pass
        return True

    elif query.data == "tz:tog_weather":
        if not has_feature_access(conn, user_id, "weather_name"):
            await query.answer("🔒 Погода в имени доступна по Премиум подписке!", show_alert=True)
            return True

        if not bc_id:
            await query.answer("Подключение не найдено.")
            return True

        s = tn_db.get_setting(conn, bc_id) or {}
        new_state = not bool(s.get("weather_enabled", 0))
        if new_state and not s.get("weather_city"):
            await query.answer("📍 Сначала укажите город командой .weather <город>!", show_alert=True)
            return True

        tn_db.set_weather_settings(conn, bc_id, city=None, enabled=new_state)
        await query.answer(f"Погода в имени: {'ВКЛ ✅' if new_state else 'ВЫКЛ ❌'}")
        try:
            await query.edit_message_text(
                text=get_time_text(conn, bc_id),
                reply_markup=build_timezone_keyboard(conn, bc_id, user_id),
                parse_mode="Markdown",
            )
        except Exception:
            pass
        return True

    elif query.data == "tz:tog_night":
        if not has_feature_access(conn, user_id, "night_status"):
            await query.answer("🔒 Ночной статус доступен по Премиум подписке!", show_alert=True)
            return True

        if not bc_id:
            await query.answer("Подключение не найдено.")
            return True

        s = tn_db.get_setting(conn, bc_id) or {}
        new_state = not bool(s.get("night_status_enabled", 0))
        tn_db.set_night_status(conn, bc_id, enabled=new_state)
        await query.answer(f"Ночной статус (с 23:00 до 08:00): {'ВКЛ ✅' if new_state else 'ВЫКЛ ❌'}")
        try:
            await query.edit_message_text(
                text=get_time_text(conn, bc_id),
                reply_markup=build_timezone_keyboard(conn, bc_id, user_id),
                parse_mode="Markdown",
            )
        except Exception:
            pass
        return True

    elif query.data == "tz:refresh":
        if bc_id:
            s = tn_db.get_setting(conn, bc_id) or {}
            first_name = s.get("first_name")
            offset = s.get("tz_offset", 3)
            if first_name:
                tz = timezone(timedelta(hours=offset))
                now_dt = datetime.now(tz)
                time_str = now_dt.strftime("%H:%M")
                if s.get("night_status_enabled") and (now_dt.hour >= 23 or now_dt.hour < 8):
                    last_name = "[🌙 Сплю]"
                else:
                    weather_text = ""
                    if s.get("weather_enabled") and s.get("weather_city"):
                        w_res = await get_city_weather(s["weather_city"])
                        if w_res:
                            weather_text = f" {w_res}"
                    last_name = f"[{time_str}{weather_text}]"

                await set_business_account_name(
                    context.bot,
                    business_connection_id=bc_id,
                    first_name=first_name,
                    last_name=last_name,
                )

        await query.answer("🔄 Имя в профиле обновлено!")
        if bc_id:
            try:
                await query.edit_message_text(
                    text=get_time_text(conn, bc_id),
                    reply_markup=build_timezone_keyboard(conn, bc_id, user_id),
                    parse_mode="Markdown",
                )
            except Exception:
                pass
        return True

    return False