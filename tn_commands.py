# tn_commands.py
"""
Обработка команды .time, погоды и ночного статуса профиля.
Полная изоляция между пользователями, защита от нажатия кнопок собеседником,
привязка данных строго к user_id, поддержка высоких нагрузок.
"""

import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import ContextTypes

import tn_db
from tn_worker import set_business_account_name
from permissions_check import check_name_and_bio_permissions, is_own_business_message, get_connection_owner_id
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


def build_timezone_keyboard(conn: sqlite3.Connection, user_id: int) -> InlineKeyboardMarkup:
    """Генерирует клавиатуру настроек, привязанную строго к user_id."""
    s = tn_db.get_user_setting(conn, user_id) or {}
    selected_offset = s.get("tz_offset", 3)
    if selected_offset is None:
        selected_offset = 3
    time_on = bool(s.get("time_enabled", 0))

    keyboard = []

    # Кнопка включения/выключения времени в профиле с user_id в callback_data
    time_btn = f"⏱ Время в имени: {'ВКЛ ✅' if time_on else 'ВЫКЛ ❌'}"
    keyboard.append([
        InlineKeyboardButton(time_btn, callback_data=f"tz:tog_time:{user_id}")
    ])

    # Москва в самом верху
    moscow_label = "⭐️ Москва (UTC+3)" + (" ✅" if selected_offset == 3 else "")
    keyboard.append([
        InlineKeyboardButton(moscow_label, callback_data=f"tz:set:3:{user_id}")
    ])

    # Остальные часовые пояса парами
    other_buttons = []
    for title, offset in TIMEZONES:
        if offset == 3:
            continue
        mark = " ✅" if selected_offset == offset else ""
        other_buttons.append(
            InlineKeyboardButton(f"{title}{mark}", callback_data=f"tz:set:{offset}:{user_id}")
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
        InlineKeyboardButton(weather_btn, callback_data=f"tz:tog_weather:{user_id}")
    ])
    keyboard.append([
        InlineKeyboardButton(night_btn, callback_data=f"tz:tog_night:{user_id}")
    ])

    # Кнопка мгновенного обновления
    keyboard.append([
        InlineKeyboardButton("🔄 Обновить в профиле сейчас", callback_data=f"tz:refresh:{user_id}")
    ])
    keyboard.append([
        InlineKeyboardButton("⬅️ В главное меню", callback_data="start_back")
    ])

    return InlineKeyboardMarkup(keyboard)


def get_time_text(conn: sqlite3.Connection, user_id: int) -> str:
    """Генерирует текст интерфейса настроек времени."""
    s = tn_db.get_user_setting(conn, user_id) or {}
    offset = s.get("tz_offset", 3)
    if offset is None:
        offset = 3

    tz = timezone(timedelta(hours=offset))
    cur_time = datetime.now(tz).strftime("%H:%M")

    time_on = bool(s.get("time_enabled", 0))
    w_city = s.get("weather_city")
    w_on = bool(s.get("weather_enabled", 0))
    n_on = bool(s.get("night_status_enabled", 0))

    extra_lines = [
        f"• ⏱ Время в имени: {'активно [ЧЧ:ММ] ✅' if time_on else 'отключено ❌'}"
    ]
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
        "Фамилия вашего бизнес-аккаунта обновляется каждую минуту при включенном режиме.\n"
        "💡 _Для установки города погоды отправьте: `.weather <город>`_\n\n"
        "Выберите ваш часовой пояс и настройки кнопками ниже 👇"
    )


async def handle_time_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE, conn: sqlite3.Connection
) -> bool:
    """Обрабатывает команду .time и /time (ТОЛЬКО в ЛС с ботом)."""
    msg = update.message or update.effective_message
    if not msg or not msg.text:
        return False

    chat = update.effective_chat
    # Проверка: время в нике настраивается ТОЛЬКО в личке с ботом
    if getattr(msg, "business_connection_id", None) or (chat and chat.type != "private"):
        log.info("Команда .time проигнорирована: вызвана вне личных сообщений с ботом")
        return False

    text = msg.text.strip().lower()
    parts = text.split()
    if not parts or parts[0] not in (".time", "/time"):
        return False

    return await send_timezone_menu(update, context, conn)


async def send_timezone_menu(
    update: Update, context: ContextTypes.DEFAULT_TYPE, conn: sqlite3.Connection
) -> bool:
    """Отправляет меню настроек времени и активирует время в имени (ТОЛЬКО в ЛС)."""
    user = update.effective_user
    msg = update.effective_message or update.message
    if not user or not msg:
        return False

    user_id = user.id

    # Находим business_connection_id строго для этого пользователя
    bc_id = tn_db.get_user_bc_id(conn, user_id)
    if not bc_id:
        not_conn_text = (
            "⚠️ *Telegram Business не обнаружен!*\n\n"
            "Если бот уже добавлен в чат-боты вашего аккаунта:\n"
            "1. Перейдите в *Настройки* ➔ *Telegram для бизнеса* ➔ *Чат-боты*.\n"
            "2. Выключите тумблер бота и включите обратно (чтобы Telegram обновил статус подключения).\n"
            "3. Или просто отправьте любое сообщение в любой ваш бизнес-чат.\n\n"
            "После этого нажмите кнопку «Проверить снова» 👇"
        )
        retry_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Проверить снова", callback_data="time:open_start")],
            [InlineKeyboardButton("⬅️ В главное меню", callback_data="start_back")],
        ])
        if update.callback_query:
            await update.callback_query.edit_message_text(
                text=not_conn_text,
                reply_markup=retry_kb,
                parse_mode="Markdown",
            )
        else:
            await msg.reply_text(
                not_conn_text,
                reply_markup=retry_kb,
                parse_mode="Markdown",
            )
        return True

    # Проверка прав доступа в Telegram Business (Правило 999)
    ok, err_msg = await check_name_and_bio_permissions(context.bot, conn, bc_id)
    if not ok:
        retry_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Повторить проверку", callback_data="time:open_start")],
            [InlineKeyboardButton("⬅️ В главное меню", callback_data="start_back")],
        ])
        if update.callback_query:
            await update.callback_query.edit_message_text(
                text=err_msg,
                reply_markup=retry_kb,
                parse_mode="Markdown",
            )
        else:
            try:
                await context.bot.send_message(
                    chat_id=msg.chat_id,
                    text=err_msg,
                    reply_markup=retry_kb,
                    business_connection_id=getattr(msg, "business_connection_id", None),
                    parse_mode="Markdown",
                )
            except Exception:
                await msg.reply_text(err_msg, reply_markup=retry_kb, parse_mode="Markdown")
        return True

    # Сохраняем имя владельца, если его ещё нет
    user_setting = tn_db.get_user_setting(conn, user_id)
    first_name = (user_setting.get("first_name") if user_setting else "") or (
        user.first_name if user else ""
    )
    tn_db.upsert_user_connection(conn, user_id, bc_id, first_name=first_name, is_active=True)

    # Включаем время для этого пользователя
    tn_db.set_time_enabled(conn, user_id, True)

    # Мгновенно обновляем профиль
    offset = (user_setting.get("tz_offset") if user_setting else 3) or 3
    tz = timezone(timedelta(hours=offset))
    now_dt = datetime.now(tz)
    time_str = now_dt.strftime("%H:%M")

    weather_text = ""
    if user_setting and user_setting.get("weather_enabled") and user_setting.get("weather_city"):
        w_res = await get_city_weather(user_setting["weather_city"])
        if w_res:
            weather_text = f" {w_res}"

    target_last_name = f"[{time_str}{weather_text}]"
    if first_name:
        await set_business_account_name(
            context.bot,
            business_connection_id=bc_id,
            first_name=first_name,
            last_name=target_last_name,
        )
        tn_db.update_last_set_name(conn, user_id, target_last_name)

    keyboard = build_timezone_keyboard(conn, user_id)
    text_content = get_time_text(conn, user_id)

    try:
        if update.callback_query:
            await update.callback_query.edit_message_text(
                text=text_content,
                reply_markup=keyboard,
                parse_mode="Markdown",
            )
        else:
            await context.bot.send_message(
                chat_id=msg.chat_id,
                text=text_content,
                reply_markup=keyboard,
                business_connection_id=getattr(msg, "business_connection_id", None),
                parse_mode="Markdown",
            )
    except Exception as e:
        log.error(f"send_message failed in send_timezone_menu: {e}")

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

    chat = update.effective_chat
    # Погода в профиле настраивается ТОЛЬКО в личке с ботом
    if getattr(msg, "business_connection_id", None) or (chat and chat.type != "private"):
        log.info("Команда .weather проигнорирована: вызвана вне личных сообщений с ботом")
        return False

    user_id = msg.from_user.id if msg.from_user else 0
    if not user_id:
        return False

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
    bc_id = getattr(msg, "business_connection_id", None) or tn_db.get_user_bc_id(conn, user_id)
    if not bc_id:
        await msg.reply_text(
            "⚠️ *У вас не подключен Telegram Business!*\n\n"
            "Подключите бота в *Настройки* ➔ *Telegram для бизнеса* ➔ *Чат-боты*.",
            parse_mode="Markdown",
        )
        return True

    # Сохраняем настройки погоды строго для этого пользователя
    tn_db.set_weather_settings(conn, user_id, city=city, enabled=True, business_connection_id=bc_id)

    # Пробуем запросить погоду для проверки
    weather_sample = await get_city_weather(city)
    status_sample = f" `{weather_sample}`" if weather_sample else ""

    # Если время активно, сразу обновляем имя в Telegram
    s = tn_db.get_user_setting(conn, user_id) or {}
    if s.get("time_enabled") and s.get("first_name"):
        offset = s.get("tz_offset", 3) or 3
        tz = timezone(timedelta(hours=offset))
        time_str = datetime.now(tz).strftime("%H:%M")
        w_part = f" {weather_sample}" if weather_sample else ""
        target_name = f"[{time_str}{w_part}]"
        await set_business_account_name(
            context.bot,
            business_connection_id=bc_id,
            first_name=s["first_name"],
            last_name=target_name,
        )
        tn_db.update_last_set_name(conn, user_id, target_name)

    await msg.reply_text(
        f"✅ *Город успешно установлен:* `{city}`{status_sample}\n\n"
        f"Погода включена и теперь каждую минуту будет транслироваться в имени вашего бизнес-аккаунта!",
        parse_mode="Markdown",
    )
    return True


async def handle_time_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE, conn: sqlite3.Connection
) -> bool:
    """
    Обрабатывает нажатия на инлайн-кнопки часовых поясов, погоды и времени.
    Строгая валидация прав: только владелец меню может нажимать на кнопки!
    """
    query = update.callback_query
    if not query or not query.data or not query.data.startswith("tz:"):
        return False

    parts = query.data.split(":")
    # Формат callback_data: tz:<action>[:<extra>]:<owner_user_id>
    if len(parts) < 3:
        # Устаревший формат без user_id
        await query.answer("⚠️ Пожалуйста, вызовите меню заново командой .time", show_alert=True)
        return True

    try:
        owner_user_id = int(parts[-1])
    except ValueError:
        await query.answer("⚠️ Неверный идентификатор запроса.", show_alert=True)
        return True

    caller_id = query.from_user.id if query.from_user else 0
    if caller_id != owner_user_id:
        await query.answer("🔒 Это меню настроек принадлежит другому пользователю!", show_alert=True)
        return True

    action = parts[1]
    bc_id = tn_db.get_user_bc_id(conn, owner_user_id)
    if not bc_id:
        await query.answer("⚠️ Ваше подключение Telegram Business не найдено.", show_alert=True)
        return True

    s = tn_db.get_user_setting(conn, owner_user_id) or {}
    first_name = s.get("first_name") or ""

    if action == "tog_time":
        new_state = not bool(s.get("time_enabled", 0))
        tn_db.set_time_enabled(conn, owner_user_id, new_state)

        if not new_state:
            # Отключили время: очищаем фамилию в профиле
            if first_name:
                await set_business_account_name(
                    context.bot,
                    business_connection_id=bc_id,
                    first_name=first_name,
                    last_name="",
                )
            tn_db.update_last_set_name(conn, owner_user_id, "")
            await query.answer("⏱ Время в имени: ВЫКЛЮЧЕНО ❌")
        else:
            # Включили время: мгновенно ставим текущее время
            offset = s.get("tz_offset", 3) or 3
            tz = timezone(timedelta(hours=offset))
            now_dt = datetime.now(tz)
            time_str = now_dt.strftime("%H:%M")
            weather_text = ""
            if s.get("weather_enabled") and s.get("weather_city"):
                w_res = await get_city_weather(s["weather_city"])
                if w_res:
                    weather_text = f" {w_res}"
            last_name = f"[{time_str}{weather_text}]"
            if first_name:
                await set_business_account_name(
                    context.bot,
                    business_connection_id=bc_id,
                    first_name=first_name,
                    last_name=last_name,
                )
            tn_db.update_last_set_name(conn, owner_user_id, last_name)
            await query.answer("⏱ Время в имени: ВКЛЮЧЕНО ✅")

        try:
            await query.edit_message_text(
                text=get_time_text(conn, owner_user_id),
                reply_markup=build_timezone_keyboard(conn, owner_user_id),
                parse_mode="Markdown",
            )
        except Exception:
            pass
        return True

    elif action == "set":
        try:
            offset = int(parts[2])
        except (ValueError, IndexError):
            offset = 3

        tn_db.set_tz_offset(conn, owner_user_id, offset)
        await query.answer(f"✅ Часовой пояс установлен: UTC{offset:+d}")

        # Если время включено, сразу обновляем в профиле
        if s.get("time_enabled") and first_name:
            tz = timezone(timedelta(hours=offset))
            time_str = datetime.now(tz).strftime("%H:%M")
            weather_text = ""
            if s.get("weather_enabled") and s.get("weather_city"):
                w_res = await get_city_weather(s["weather_city"])
                if w_res:
                    weather_text = f" {w_res}"
            target_name = f"[{time_str}{weather_text}]"
            await set_business_account_name(
                context.bot,
                business_connection_id=bc_id,
                first_name=first_name,
                last_name=target_name,
            )
            tn_db.update_last_set_name(conn, owner_user_id, target_name)

        try:
            await query.edit_message_text(
                text=get_time_text(conn, owner_user_id),
                reply_markup=build_timezone_keyboard(conn, owner_user_id),
                parse_mode="Markdown",
            )
        except Exception:
            pass
        return True

    elif action == "tog_weather":
        if not has_feature_access(conn, owner_user_id, "weather_name"):
            await query.answer("🔒 Погода в имени доступна по Премиум подписке!", show_alert=True)
            return True

        new_state = not bool(s.get("weather_enabled", 0))
        if new_state and not s.get("weather_city"):
            await query.answer("📍 Сначала укажите город командой .weather <город>!", show_alert=True)
            return True

        tn_db.set_weather_settings(conn, owner_user_id, city=None, enabled=new_state)
        await query.answer(f"Погода в имени: {'ВКЛ ✅' if new_state else 'ВЫКЛ ❌'}")

        # Если время включено, обновляем профиль
        if s.get("time_enabled") and first_name:
            offset = s.get("tz_offset", 3) or 3
            tz = timezone(timedelta(hours=offset))
            time_str = datetime.now(tz).strftime("%H:%M")
            weather_text = ""
            if new_state and s.get("weather_city"):
                w_res = await get_city_weather(s["weather_city"])
                if w_res:
                    weather_text = f" {w_res}"
            target_name = f"[{time_str}{weather_text}]"
            await set_business_account_name(
                context.bot,
                business_connection_id=bc_id,
                first_name=first_name,
                last_name=target_name,
            )
            tn_db.update_last_set_name(conn, owner_user_id, target_name)

        try:
            await query.edit_message_text(
                text=get_time_text(conn, owner_user_id),
                reply_markup=build_timezone_keyboard(conn, owner_user_id),
                parse_mode="Markdown",
            )
        except Exception:
            pass
        return True

    elif action == "tog_night":
        if not has_feature_access(conn, owner_user_id, "night_status"):
            await query.answer("🔒 Ночной статус доступен по Премиум подписке!", show_alert=True)
            return True

        new_state = not bool(s.get("night_status_enabled", 0))
        tn_db.set_night_status(conn, owner_user_id, enabled=new_state)
        await query.answer(f"Ночной статус (с 23:00 до 08:00): {'ВКЛ ✅' if new_state else 'ВЫКЛ ❌'}")

        try:
            await query.edit_message_text(
                text=get_time_text(conn, owner_user_id),
                reply_markup=build_timezone_keyboard(conn, owner_user_id),
                parse_mode="Markdown",
            )
        except Exception:
            pass
        return True

    elif action == "refresh":
        offset = s.get("tz_offset", 3) or 3
        tz = timezone(timedelta(hours=offset))
        time_str = datetime.now(tz).strftime("%H:%M")
        weather_text = ""
        if s.get("weather_enabled") and s.get("weather_city"):
            w_res = await get_city_weather(s["weather_city"])
            if w_res:
                weather_text = f" {w_res}"

        if s.get("time_enabled") and first_name:
            target_name = f"[{time_str}{weather_text}]"
            await set_business_account_name(
                context.bot,
                business_connection_id=bc_id,
                first_name=first_name,
                last_name=target_name,
            )
            tn_db.update_last_set_name(conn, owner_user_id, target_name)
            await query.answer("🔄 Профиль обновлен!")
        else:
            await query.answer("ℹ️ Время выключено. Включите его кнопкой выше.")

        try:
            await query.edit_message_text(
                text=get_time_text(conn, owner_user_id),
                reply_markup=build_timezone_keyboard(conn, owner_user_id),
                parse_mode="Markdown",
            )
        except Exception:
            pass
        return True

    return False