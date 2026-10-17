# admin_commands.py
"""Панель администратора, рассылка сообщений, управление админами и выдача премиум-функций."""

import asyncio
import logging
import sqlite3
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from admin_db import (
    OWNER_ID,
    is_admin,
    add_admin,
    del_admin,
    get_all_admins,
    resolve_user_id,
    record_bot_user,
    grant_feature,
    revoke_feature,
    get_all_broadcast_users,
    get_admin_tag_mode,
    set_admin_tag_mode,
)
from premium_db import activate_premium, is_premium
from bio_worker import apply_bio_for_connection
import tn_db

log = logging.getLogger(__name__)

FEATURE_NAMES = {
    "premium": "👑 Полный Премиум",
    "custom_stage": "✨ Кастомная стадия общения",
    "auto_freeze": "⚡️ Автозаморозка в 23:30",
    "extended_limits": "💎 Расширенные лимиты (10 заморозок / 10 воскрешений)",
    "weather_name": "🌦 Погода в имени",
    "night_status": "🌙 Ночной статус в имени",
    "pair_bio": "👫 Парные описания",
    "hide_stage": "🙈 Скрытие стадии на карточке",
}


# ─────────────────────── Экран админ-панели ───────────────────────

def build_admin_panel(conn: sqlite3.Connection, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    conn.row_factory = sqlite3.Row
    total_users = conn.execute("SELECT COUNT(*) FROM bot_users").fetchone()[0]
    total_conns = conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0]
    total_admins = conn.execute("SELECT COUNT(*) FROM admins").fetchone()[0]
    total_prems = conn.execute("SELECT COUNT(*) FROM subscriptions WHERE status = 'active'").fetchone()[0]

    lines = [
        "👑 *Панель администратора бота*",
        "━━━━━━━━━━━━━━━",
        f"👤 Всего пользователей в боте: *{total_users}*",
        f"💼 Активных подключений Business: *{total_conns}*",
        f"💎 Активных Премиум-подписок: *{total_prems}*",
        f"🛡 Назначенных администраторов: *{total_admins}*",
        "━━━━━━━━━━━━━━━",
        "Доступные быстрые действия через кнопки 👇",
    ]

    keyboard = [
        [
            InlineKeyboardButton("📢 Сделать рассылку", callback_data="adm:broadcast_prompt"),
            InlineKeyboardButton("🎁 Выдать доступ", callback_data="adm:give_help"),
        ],
        [
            InlineKeyboardButton("👥 Список администраторов", callback_data="adm:list_admins"),
            InlineKeyboardButton("⚙️ Лимит одноразок", callback_data="adm:ephem_menu"),
        ],
        [
            InlineKeyboardButton("🏷 Тег в описании (био)", callback_data="adm:tag_menu"),
        ],
        [
            InlineKeyboardButton("❌ Закрыть панель", callback_data="adm:close"),
        ],
    ]

    return "\n".join(lines), InlineKeyboardMarkup(keyboard)


# ─────────────────────── Команда /admin ───────────────────────

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE, conn: sqlite3.Connection):
    """Открывает админ-панель."""
    user = update.effective_user
    if not user or not is_admin(conn, user.id):
        return

    text, kb = build_admin_panel(conn, user.id)
    target = update.effective_message or update.message
    if target:
        await target.reply_text(text, reply_markup=kb, parse_mode="Markdown")


# ─────────────────────── Управление админами ───────────────────────

async def add_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE, conn: sqlite3.Connection):
    """Выдача прав админа: только создатель! Сохраняет именно числовой user_id."""
    user = update.effective_user
    if not user or user.id != OWNER_ID:
        return

    args = context.args or []
    if not args:
        await update.effective_message.reply_text(
            "⚠️ Использование: `/add_admin @username` или `/add_admin <user_id>`",
            parse_mode="Markdown",
        )
        return

    target_str = args[0]
    target_id = resolve_user_id(conn, target_str)
    if not target_id:
        # Пробуем через get_chat
        try:
            chat = await context.bot.get_chat(target_str if target_str.startswith("@") else f"@{target_str}")
            target_id = chat.id
            record_bot_user(conn, chat.id, chat.username, chat.first_name)
        except Exception:
            pass

    if not target_id:
        await update.effective_message.reply_text(
            f"❌ Не удалось найти пользователя `{target_str}` в базе данных.\n"
            "Пользователь должен хотя бы раз нажать /start в боте!",
            parse_mode="Markdown",
        )
        return

    clean_name = target_str.lstrip("@")
    add_admin(conn, target_id, clean_name, added_by=user.id)
    await update.effective_message.reply_text(
        f"✅ *Администратор успешно добавлен!*\n\n"
        f"• Пользователь: @{clean_name}\n"
        f"• Числовой ID: `{target_id}` (сохранён в БД)\n"
        f"• Назначен создателем: `{user.id}`",
        parse_mode="Markdown",
    )


async def del_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE, conn: sqlite3.Connection):
    """Снятие прав админа: только создатель!"""
    user = update.effective_user
    if not user or user.id != OWNER_ID:
        return

    args = context.args or []
    if not args:
        await update.effective_message.reply_text("⚠️ Использование: `/del_admin @username` или `<user_id>`")
        return

    target_id = resolve_user_id(conn, args[0])
    if not target_id:
        await update.effective_message.reply_text(f"❌ Пользователь {args[0]} не найден.")
        return

    if del_admin(conn, target_id):
        await update.effective_message.reply_text(f"✅ Администратор `{target_id}` успешно удалён.")
    else:
        await update.effective_message.reply_text("❌ Не удалось удалить администратора.")


# ─────────────────────── Выдача доступа к фичам ───────────────────────

async def give_feature_command(update: Update, context: ContextTypes.DEFAULT_TYPE, conn: sqlite3.Connection):
    """
    Команда выдачи доступа:
    `/give_feature @username <feature> [days]`
    days: 0 или lifetime — бессрочно.
    """
    user = update.effective_user
    if not user or not is_admin(conn, user.id):
        return

    args = context.args or []
    if len(args) < 2:
        feats_list = "\n".join([f"• `{k}` — {v}" for k, v in FEATURE_NAMES.items()])
        await update.effective_message.reply_text(
            "🎁 *Использование команды /give_feature:*\n"
            "`/give_feature @username <название_фичи> [срок_в_днях]`\n\n"
            f"📋 *Доступные функции:*\n{feats_list}\n\n"
            "Примеры:\n"
            "`/give_feature @durov premium 30` (Премиум на месяц)\n"
            "`/give_feature @durov auto_freeze 0` (Автозаморозка бессрочно)\n"
            "`/give_feature @durov weather_name 14` (Погода в имени на 14 дней)",
            parse_mode="Markdown",
        )
        return

    target_str = args[0]
    feature = args[1].lower()
    days = 0
    if len(args) >= 3:
        try:
            days = int(args[2])
        except ValueError:
            days = 0

    target_id = resolve_user_id(conn, target_str)
    if not target_id:
        try:
            chat = await context.bot.get_chat(target_str if target_str.startswith("@") else f"@{target_str}")
            target_id = chat.id
            record_bot_user(conn, chat.id, chat.username, chat.first_name)
        except Exception:
            pass

    if not target_id:
        clean_un = target_str.lstrip("@").lower()
        conn.execute("""
            INSERT INTO pending_grants (username, feature, days)
            VALUES (?, ?, ?)
            ON CONFLICT(username, feature) DO UPDATE SET days = excluded.days
        """, (clean_un, feature, days))
        conn.commit()
        feature_title = FEATURE_NAMES.get(feature, f"«{feature}»")
        days_label = "Бессрочно (навсегда) ♾" if days <= 0 else f"{days} дней"
        await update.effective_message.reply_text(
            f"⏳ Пользователь `@{clean_un}` пока не запускал бота.\n\n"
            f"✅ Выдача {feature_title} ({days_label}) сохранена в очередь и активируется **автоматически**, как только пользователь перейдет в бота или напишет любое сообщение!",
            parse_mode="Markdown",
        )
        return

    feature_title = FEATURE_NAMES.get(feature, f"«{feature}»")
    days_label = "Бессрочно (навсегда) ♾" if days <= 0 else f"{days} дней"

    if feature in ("premium", "prem"):
        activate_premium(conn, target_id, days=days if days > 0 else None, plan="lifetime" if days <= 0 else f"{days}d")
    else:
        grant_feature(conn, target_id, feature, days if days > 0 else None)

    await update.effective_message.reply_text(
        f"✅ *Доступ успешно выдан!*\n\n"
        f"👤 Пользователь: `{target_str}` (ID: `{target_id}`)\n"
        f"🎁 Функция: *{feature_title}*\n"
        f"⏳ Срок: *{days_label}*",
        parse_mode="Markdown",
    )

    # Уведомляем пользователя в ЛС
    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=(
                f"🎉 *Вам открыт доступ к премиум-функции!*\n\n"
                f"🎁 Функция: *{feature_title}*\n"
                f"⏳ Срок действия: *{days_label}*\n\n"
                "Приятного использования! Все возможности уже активны в меню бота."
            ),
            parse_mode="Markdown",
        )
    except Exception:
        pass


# ─────────────────────── Рассылка сообщений ───────────────────────

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE, conn: sqlite3.Connection):
    """Рассылает сообщение всем пользователям бота из bot_users."""
    user = update.effective_user
    if not user or not is_admin(conn, user.id):
        return

    msg = update.effective_message
    if not msg:
        return

    text_to_send = ""
    if context.args:
        text_to_send = " ".join(context.args)
    elif msg.reply_to_message and msg.reply_to_message.text:
        text_to_send = msg.reply_to_message.text
    else:
        await msg.reply_text(
            "📢 *Использование команды /broadcast:*\n"
            "`/broadcast <текст рассылки>` или ответьте командой `/broadcast` на нужное сообщение.",
            parse_mode="Markdown",
        )
        return

    users = get_all_broadcast_users(conn)
    if not users:
        await msg.reply_text("База пользователей для рассылки пуста.")
        return

    status_msg = await msg.reply_text(f"⏳ Начинаем рассылку для {len(users)} пользователей...")

    success = 0
    failed = 0

    for uid in users:
        try:
            await context.bot.send_message(chat_id=uid, text=text_to_send, parse_mode="Markdown")
            success += 1
            await asyncio.sleep(0.05)  # Защита от flood limits
        except Exception:
            failed += 1

    await status_msg.edit_text(
        f"📢 *Рассылка завершена!*\n\n"
        f"✅ Успешно доставлено: *{success}*\n"
        f"❌ Не доставлено (блок бота): *{failed}*",
        parse_mode="Markdown",
    )


# ─────────────────────── Колбэки админ-панели ───────────────────────

async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, conn: sqlite3.Connection) -> bool:
    query = update.callback_query
    if not query or not query.data or not query.data.startswith("adm:"):
        return False

    user = query.from_user
    if not user or not is_admin(conn, user.id):
        await query.answer("🔒 Доступ только для администраторов.", show_alert=True)
        return True

    data = query.data

    if data == "adm:close":
        await query.answer()
        await query.message.delete()
        return True

    elif data == "adm:broadcast_prompt":
        await query.answer()
        context.user_data["admin_state"] = "waiting_broadcast"
        await query.message.reply_text(
            "📢 *Режим рассылки сообщений*\n\n"
            "Отправьте текст сообщения для рассылки всем пользователям бота (или напишите `отмена`):",
            parse_mode="Markdown",
        )
        return True

    elif data == "adm:give_help":
        await query.answer()
        feats_list = "\n".join([f"• `{k}` — {v}" for k, v in FEATURE_NAMES.items()])
        await query.message.reply_text(
            "🎁 *Выдача подписки или отдельных функций:*\n\n"
            "Отправьте команду в формате:\n"
            "`/give_feature @username <функция> [дней]`\n\n"
            f"Список функций:\n{feats_list}\n\n"
            "_Укажите 0 дней для бессрочного доступа._",
            parse_mode="Markdown",
        )
        return True

    elif data == "adm:list_admins":
        await query.answer()
        admins = get_all_admins(conn)
        lines = ["👥 *Список действующих администраторов:*", "━━━━━━━━━━━━━━━", f"👑 Владелец: `{OWNER_ID}`"]
        for a in admins:
            un = f"@{a['username']}" if a.get("username") else "без юзернейма"
            lines.append(f"• {un} (ID: `{a['user_id']}`)")
        await query.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return True

    elif data == "adm:ephem_menu":
        await query.answer()
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("5 в день", callback_data="adm:set_eph:5"), InlineKeyboardButton("10 в день", callback_data="adm:set_eph:10")],
            [InlineKeyboardButton("25 в день", callback_data="adm:set_eph:25"), InlineKeyboardButton("Безлимит", callback_data="adm:set_eph:999999")],
        ])
        await query.message.reply_text(
            "⚙️ *Настройка лимита сохранения одноразовых медиа в день:*\n"
            "Выберите желаемый суточный лимит:",
            reply_markup=kb,
            parse_mode="Markdown",
        )
        return True

    elif data.startswith("adm:set_eph:"):
        val = int(data.split(":")[-1])
        conn.execute("INSERT OR REPLACE INTO bot_config (key, value) VALUES ('ephemeral_limit', ?)", (str(val),))
        conn.commit()
        lim_str = "Безлимитно ♾" if val >= 999999 else f"{val} шт./день"
        await query.answer(f"Лимит установлен: {lim_str}", show_alert=True)
        await query.message.reply_text(f"✅ Базовый лимит сохранения одноразок обновлён: *{lim_str}*", parse_mode="Markdown")
        return True

    elif data == "adm:tag_menu":
        await query.answer()
        cur_mode = get_admin_tag_mode(conn, user.id)
        mode_titles = {
            "admin": "| admin @rad1kal_bot",
            "dev": "| dev @rad1kal_bot",
            "user": "| user @rad1kal_bot",
            "off": "Выключен ❌",
        }
        status_label = mode_titles.get(cur_mode, "Выключен ❌")

        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(f"{'✅ ' if cur_mode == 'admin' else ''}| admin @rad1kal_bot", callback_data="adm:set_tag:admin"),
            ],
            [
                InlineKeyboardButton(f"{'✅ ' if cur_mode == 'dev' else ''}| dev @rad1kal_bot", callback_data="adm:set_tag:dev"),
            ],
            [
                InlineKeyboardButton(f"{'✅ ' if cur_mode == 'user' else ''}| user @rad1kal_bot", callback_data="adm:set_tag:user"),
            ],
            [
                InlineKeyboardButton(f"{'✅ ' if cur_mode == 'off' else ''}❌ Выключить тег", callback_data="adm:set_tag:off"),
            ],
            [
                InlineKeyboardButton("⬅️ В панель админа", callback_data="adm:back_panel"),
            ],
        ])

        msg_text = (
            "🏷 *Настройка персонального тега в описании:*\n\n"
            "Вы можете выбрать выключаемый тег, который будет отображаться в конце вашего био профиля.\n\n"
            f"Текущий режим: *{status_label}*\n\n"
            "Выберите желаемый вариант ниже 👇"
        )
        try:
            await query.edit_message_text(msg_text, reply_markup=kb, parse_mode="Markdown")
        except Exception:
            await query.message.reply_text(msg_text, reply_markup=kb, parse_mode="Markdown")
        return True

    elif data.startswith("adm:set_tag:"):
        new_mode = data.split(":")[-1]
        set_admin_tag_mode(conn, user.id, new_mode)

        # Немедленно обновляем био в Telegram Business аккаунте
        bc_id = tn_db.get_user_bc_id(conn, user.id)
        if bc_id:
            await apply_bio_for_connection(context.bot, conn, bc_id, user.id)

        mode_titles = {
            "admin": "| admin @rad1kal_bot",
            "dev": "| dev @rad1kal_bot",
            "user": "| user @rad1kal_bot",
            "off": "Выключен",
        }
        res_label = mode_titles.get(new_mode, new_mode)
        await query.answer(f"Тег обновлен: {res_label}", show_alert=True)

        # Перерисовываем меню тегов
        cur_mode = new_mode
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(f"{'✅ ' if cur_mode == 'admin' else ''}| admin @rad1kal_bot", callback_data="adm:set_tag:admin"),
            ],
            [
                InlineKeyboardButton(f"{'✅ ' if cur_mode == 'dev' else ''}| dev @rad1kal_bot", callback_data="adm:set_tag:dev"),
            ],
            [
                InlineKeyboardButton(f"{'✅ ' if cur_mode == 'user' else ''}| user @rad1kal_bot", callback_data="adm:set_tag:user"),
            ],
            [
                InlineKeyboardButton(f"{'✅ ' if cur_mode == 'off' else ''}❌ Выключить тег", callback_data="adm:set_tag:off"),
            ],
            [
                InlineKeyboardButton("⬅️ В панель админа", callback_data="adm:back_panel"),
            ],
        ])
        msg_text = (
            "🏷 *Настройка персонального тега в описании:*\n\n"
            "Вы можете выбрать выключаемый тег, который будет отображаться в конце вашего био профиля.\n\n"
            f"Текущий режим: *{res_label}*\n\n"
            "Выберите желаемый вариант ниже 👇"
        )
        try:
            await query.edit_message_text(msg_text, reply_markup=kb, parse_mode="Markdown")
        except Exception:
            pass
        return True

    elif data == "adm:back_panel":
        await query.answer()
        text, kb = build_admin_panel(conn, user.id)
        try:
            await query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
        except Exception:
            await query.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")
        return True

    return False


async def handle_admin_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE, conn: sqlite3.Connection) -> bool:
    """Перехват ввода текста рассылки в админке."""
    msg = update.message
    if not msg or not msg.text:
        return False

    state = context.user_data.get("admin_state")
    if state != "waiting_broadcast":
        return False

    user = msg.from_user
    if not user or not is_admin(conn, user.id):
        return False

    text = msg.text.strip()
    if text.lower() == "отмена":
        context.user_data.pop("admin_state", None)
        await msg.reply_text("Рассылка отменена.")
        return True

    context.user_data.pop("admin_state", None)
    users = get_all_broadcast_users(conn)
    status_msg = await msg.reply_text(f"⏳ Рассылка для {len(users)} пользователей запущена...")

    success, failed = 0, 0
    for uid in users:
        try:
            await context.bot.send_message(chat_id=uid, text=text, parse_mode="Markdown")
            success += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1

    await status_msg.edit_text(
        f"📢 *Рассылка успешно завершена!*\n\n"
        f"✅ Доставлено: *{success}*\n"
        f"❌ Ошибок: *{failed}*",
        parse_mode="Markdown",
    )
    return True
