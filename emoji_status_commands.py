# emoji_status_commands.py
"""
Обработчики Telegram-команд, инлайн-кнопок и сообщений с custom_emoji
для привязки Premium-эмодзи к интервалам расписания.
"""

import html
import logging
import sqlite3
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from emoji_status_db import (
    get_user_emoji_settings,
    get_user_emoji_slots,
    get_slot_by_id,
    link_emoji_to_slot,
    unlink_emoji_from_slot,
    delete_slot,
    update_user_emoji_settings,
)
from emoji_status_worker import minutes_to_str

log = logging.getLogger(__name__)


def build_slots_menu(conn: sqlite3.Connection, user_id: int) -> Tuple[str, InlineKeyboardMarkup]:
    """Формирует текст списка слотов и инлайн-клавиатуру для привязки эмодзи."""
    settings = get_user_emoji_settings(conn, user_id)
    slots = get_user_emoji_slots(conn, user_id)

    status_icon = "🟢 Включено" if settings.get("is_enabled") else "🔴 Выключено"
    tz = settings.get("tz_offset", 3.0)
    tz_sign = f"+{tz:g}" if tz >= 0 else f"{tz:g}"

    text_lines = [
        "✨ <b>Авто-установка Premium-эмодзи по расписанию</b>\n",
        f"Статус: <b>{status_icon}</b> | Часовой пояс: <b>UTC{tz_sign}</b>\n",
        "Создавать и двигать временные отрезки можно в <b>Mini App</b> с drag-and-drop.",
        "А здесь привязывайте конкретные Premium-эмодзи к каждому интервалу:\n",
    ]

    keyboard = []

    if not slots:
        text_lines.append("<i>У вас пока нет настроенных интервалов. Откройте Mini App, чтобы добавить их.</i>")
    else:
        text_lines.append("📋 <b>Ваши интервалы:</b>")
        for s in slots:
            slot_id = s["id"]
            start_str = minutes_to_str(s["start_minutes"])
            end_str = minutes_to_str(s["end_minutes"])
            title = html.escape(s.get("title") or "Без названия")
            custom_id = s.get("custom_emoji_id")
            preview = s.get("emoji_preview") or ""

            if custom_id:
                emoji_badge = f"{preview} <code>(ID: {custom_id[:8]}…)</code>"
                status_mark = "✅"
            else:
                emoji_badge = "⚠️ <i>не привязан</i>"
                status_mark = "⏳"

            text_lines.append(
                f"• <b>{start_str} — {end_str}</b> | <b>{title}</b>: {emoji_badge}"
            )

            # Кнопки для слота
            row = []
            if custom_id:
                row.append(InlineKeyboardButton(f"🔁 Сменить {preview}", callback_data=f"emj:link:{slot_id}"))
                row.append(InlineKeyboardButton("❌ Снять", callback_data=f"emj:unlink:{slot_id}"))
            else:
                row.append(InlineKeyboardButton(f"🔗 Привязать к [{title}]", callback_data=f"emj:link:{slot_id}"))
            keyboard.append(row)

    # Кнопки управления профилем
    toggle_btn_text = "🔴 Выключить авто-статус" if settings.get("is_enabled") else "🟢 Включить авто-статус"
    keyboard.append([InlineKeyboardButton(toggle_btn_text, callback_data="emj:toggle")])
    keyboard.append([
        InlineKeyboardButton("🔄 Обновить список", callback_data="emj:refresh"),
        InlineKeyboardButton("📱 Открыть Mini App", callback_data="emj:open_webapp")
    ])

    return "\n".join(text_lines), InlineKeyboardMarkup(keyboard)


async def emoji_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE, conn: sqlite3.Connection):
    """Обработчик команды /emoji_status и .emoji_status."""
    user = update.effective_user
    if not user:
        return

    text, reply_markup = build_slots_menu(conn, user.id)
    msg = update.effective_message or update.message
    if msg:
        await msg.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")


async def handle_emoji_status_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, conn: sqlite3.Connection):
    """Обработка инлайн-кнопок раздела эмодзи-статуса."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    user_id = query.from_user.id
    data = query.data or ""

    if data == "emj:refresh":
        text, reply_markup = build_slots_menu(conn, user_id)
        try:
            await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="HTML")
        except Exception:
            pass

    elif data == "emj:toggle":
        settings = get_user_emoji_settings(conn, user_id)
        new_state = not bool(settings.get("is_enabled"))
        update_user_emoji_settings(conn, user_id, is_enabled=new_state)
        text, reply_markup = build_slots_menu(conn, user_id)
        try:
            await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="HTML")
        except Exception:
            pass

    elif data.startswith("emj:unlink:"):
        slot_id = int(data.split(":")[2])
        unlink_emoji_from_slot(conn, user_id, slot_id)
        text, reply_markup = build_slots_menu(conn, user_id)
        try:
            await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="HTML")
        except Exception:
            pass

    elif data.startswith("emj:link:"):
        slot_id = int(data.split(":")[2])
        slot = get_slot_by_id(conn, slot_id, user_id)
        if not slot:
            await query.edit_message_text("❌ Интервал не найден или был удален.", parse_mode="HTML")
            return

        # Запоминаем слот, для которого ожидаем эмодзи
        context.user_data["awaiting_emoji_slot_id"] = slot_id

        title = slot.get("title") or "Интервал"
        start_str = minutes_to_str(slot["start_minutes"])
        end_str = minutes_to_str(slot["end_minutes"])

        prompt_text = (
            f"🎯 <b>Привязка Premium-эмодзи к слоту:</b>\n"
            f"<b>{title}</b> ({start_str} — {end_str})\n\n"
            f"Отправьте мне <b>сообщение с Premium-эмодзи</b> (можно отправить один кастомный эмодзи или текст с ним).\n\n"
            f"<i>Бот автоматически считает custom_emoji_id и сохранит его для этого интервала.</i>"
        )
        cancel_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Отмена", callback_data="emj:cancel_link")]
        ])
        await query.edit_message_text(prompt_text, reply_markup=cancel_kb, parse_mode="HTML")

    elif data == "emj:cancel_link":
        context.user_data.pop("awaiting_emoji_slot_id", None)
        text, reply_markup = build_slots_menu(conn, user_id)
        try:
            await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="HTML")
        except Exception:
            pass

    elif data == "emj:open_webapp":
        from start_commands import webapp_command
        await webapp_command(update, context)


async def handle_emoji_status_text_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    conn: sqlite3.Connection
) -> bool:
    """
    Перехватывает входящее текстовое сообщение, если пользователь находится в режиме
    привязки эмодзи к слоту (awaiting_emoji_slot_id).
    Достает custom_emoji_id из message.entities и сохраняет в БД.
    Возвращает True, если сообщение было обработано.
    """
    slot_id = context.user_data.get("awaiting_emoji_slot_id")
    if not slot_id:
        return False

    msg = update.message
    if not msg:
        return False

    user_id = msg.from_user.id if msg.from_user else 0
    if not user_id:
        return False

    # Проверяем, существует ли слот и принадлежит ли пользователю
    slot = get_slot_by_id(conn, slot_id, user_id)
    if not slot:
        context.user_data.pop("awaiting_emoji_slot_id", None)
        await msg.reply_text("❌ Интервал не найден. Возможно, он был изменен в Mini App.")
        return True

    # 1. Извлекаем custom_emoji_id из entities
    custom_emoji_id: Optional[str] = None
    preview_char = "✨"

    if msg.entities:
        for ent in msg.entities:
            if ent.type == "custom_emoji" and getattr(ent, "custom_emoji_id", None):
                custom_emoji_id = str(ent.custom_emoji_id)
                # Вырезаем символ из текста для превью
                if msg.text:
                    preview_char = msg.text[ent.offset:ent.offset + ent.length]
                break

    if not custom_emoji_id:
        await msg.reply_text(
            "⚠️ <b>В сообщении не найден Premium-эмодзи.</b>\n"
            "Пожалуйста, отправьте сообщение с кастомным эмодзи из набора Telegram Premium (или нажмите кнопку отмены).",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Отмена", callback_data="emj:cancel_link")]
            ]),
            parse_mode="HTML"
        )
        return True

    # 2. Сохраняем в базе
    ok = link_emoji_to_slot(conn, user_id, slot_id, custom_emoji_id, preview_char)
    context.user_data.pop("awaiting_emoji_slot_id", None)

    title = slot.get("title") or "Интервал"
    start_str = minutes_to_str(slot["start_minutes"])
    end_str = minutes_to_str(slot["end_minutes"])

    if ok:
        await msg.reply_text(
            f"✅ <b>Эмодзи успешно привязан!</b>\n\n"
            f"• Слот: <b>{title}</b> ({start_str} — {end_str})\n"
            f"• Эмодзи: {preview_char}\n"
            f"• Custom ID: <code>{custom_emoji_id}</code>\n\n"
            f"Воркер автоматически применит этот статус при наступлении указанного времени.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 Вернуться к списку слотов", callback_data="emj:refresh")]
            ]),
            parse_mode="HTML"
        )
    else:
        await msg.reply_text("❌ Ошибка привязки эмодзи к базе данных. Попробуйте еще раз.")

    return True
