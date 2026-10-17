# description_commands.py
"""
Команда /description, настройка авто-описаний и парных био с проверкой Правила 999.
Полная изоляция данных между пользователями, защита от действий собеседника,
привязка через первичный ключ базы данных и user_id в callback_data.
"""

import json
import logging
import sqlite3
from typing import Optional, List, Tuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import ContextTypes

import tn_db
from permissions_check import check_name_and_bio_permissions, RULE_999_NOTICE, is_own_business_message, get_connection_owner_id
from admin_db import (
    has_feature_access,
    resolve_user_id,
    record_bot_user,
)
from bio_worker import (
    set_business_account_bio,
    apply_bio_for_connection,
    WATERMARK_SUFFIX,
    MAX_BIO_LENGTH,
    get_user_bio_tag,
    strip_all_watermarks,
)
from premium_db import is_premium
from timezone import now_iso

log = logging.getLogger(__name__)


def format_bio_with_watermark(bio_line: str, is_prem: bool, conn: Optional[sqlite3.Connection] = None, user_id: Optional[int] = None) -> str:
    """Форматирует строку описания с учетом водяного знака / тега для пользователя."""
    clean = strip_all_watermarks(bio_line or "").strip()

    if conn is not None and user_id is not None:
        target_tag, target_suffix = get_user_bio_tag(conn, user_id)
        if not target_tag:
            return clean[:MAX_BIO_LENGTH]
        max_len = MAX_BIO_LENGTH - len(target_suffix)
        trimmed = clean[:max_len]
        return f"{trimmed}{target_suffix}" if trimmed else target_tag

    # Fallback если не передан conn или user_id
    if is_prem:
        return clean[:MAX_BIO_LENGTH]

    max_len = MAX_BIO_LENGTH - len(WATERMARK_SUFFIX)
    trimmed = clean[:max_len]
    return f"{trimmed}{WATERMARK_SUFFIX}" if trimmed else WATERMARK_SUFFIX.lstrip(" | ")


# ─────────────────────── Экраны авто-описания ───────────────────────

def build_description_menu(conn: sqlite3.Connection, user_id: int) -> Tuple[str, InlineKeyboardMarkup]:
    conn.row_factory = sqlite3.Row
    auto_desc = conn.execute("SELECT * FROM auto_descriptions WHERE user_id = ?", (user_id,)).fetchone()
    pair_bio = conn.execute(
        "SELECT * FROM pair_bios WHERE (user_1 = ? OR user_2 = ?) AND status = 'active'",
        (user_id, user_id),
    ).fetchone()

    lines = [
        "📝 *Авто-описание профиля*",
        "━━━━━━━━━━━━━━━",
        "Бот может автоматически циклически менять описание вашего профиля по расписанию.\n",
    ]

    has_active = False

    if pair_bio:
        has_active = True
        pb = dict(pair_bio)
        is_user1 = (user_id == pb["user_1"])
        friend_name = pb["username_2"] if is_user1 else pb["username_1"]
        raw_lines = json.loads(pb.get("lines_json") or "[]")
        my_lines = [raw_lines[i] for i in range(len(raw_lines)) if (i % 2 == (0 if is_user1 else 1))]

        lines.append("👫 *Активно парное описание:*")
        lines.append(f"• Друг: @{friend_name}")
        lines.append(f"• Интервал: каждые *{pb['interval_minutes']} мин.*")
        lines.append(f"• Ваши строки ({len(my_lines)}):")
        for idx, l in enumerate(my_lines[:4], 1):
            lines.append(f"  {idx}. {l}")
        if len(my_lines) > 4:
            lines.append(f"  ... и ещё {len(my_lines) - 4}")
        lines.append("")

    elif auto_desc and auto_desc["is_active"]:
        has_active = True
        ad = dict(auto_desc)
        raw_lines = json.loads(ad.get("lines_json") or "[]")
        lines.append("🔄 *Активно личное авто-описание:*")
        lines.append(f"• Интервал: каждые *{ad['interval_minutes']} мин.*")
        lines.append(f"• Строк всего: *{len(raw_lines)}*")
        for idx, l in enumerate(raw_lines[:4], 1):
            lines.append(f"  {idx}. {l}")
        if len(raw_lines) > 4:
            lines.append(f"  ... и ещё {len(raw_lines) - 4}")
        lines.append("")

    else:
        lines.append("Статус: *Не настроено* ⚪️")
        lines.append("Нажмите кнопку ниже, чтобы задать строки и интервал смены.")

    keyboard = [
        [
            InlineKeyboardButton("➕ Настроить строки описания", callback_data=f"desc:setup_single:{user_id}"),
        ],
        [
            InlineKeyboardButton("👫 Установить парные описания", callback_data=f"desc:setup_pair:{user_id}"),
        ],
    ]

    if has_active:
        keyboard.append([
            InlineKeyboardButton("❌ Отключить авто-описание", callback_data=f"desc:reset_all:{user_id}"),
        ])

    keyboard.append([
        InlineKeyboardButton("⬅️ В главное меню", callback_data="start_back"),
    ])

    return "\n".join(lines), InlineKeyboardMarkup(keyboard)


# ─────────────────────── Команда /description ───────────────────────

async def description_command(update: Update, context: ContextTypes.DEFAULT_TYPE, conn: sqlite3.Connection) -> bool:
    """Обработчик команды /description и .description (ТОЛЬКО в ЛС с ботом)."""
    msg = update.message or update.effective_message
    if not msg:
        return False

    chat = update.effective_chat
    # Проверка: автоописание настраивается ТОЛЬКО в личке с ботом
    if getattr(msg, "business_connection_id", None) or (chat and chat.type != "private"):
        log.info("Команда .description проигнорирована: вызвана вне личных сообщений с ботом")
        return False

    sender = update.effective_user or (msg.from_user if msg else None)
    user_id = sender.id if sender else 0
    if not user_id:
        return False

    username = sender.username if sender else None
    first_name = sender.first_name if sender else None
    record_bot_user(conn, user_id, username, first_name)

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
            [InlineKeyboardButton("🔄 Проверить снова", callback_data="desc:open_start")],
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

    # Проверка подключения и прав (Правило 999)
    ok, err_msg = await check_name_and_bio_permissions(context.bot, conn, bc_id)
    if not ok:
        retry_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Повторить проверку", callback_data="desc:open_start")],
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

    text_content, keyboard = build_description_menu(conn, user_id)

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
        log.error(f"failed to send description menu: {e}")

    return True


# ─────────────────────── Колбэки меню описания ───────────────────────

async def handle_description_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, conn: sqlite3.Connection) -> bool:
    query = update.callback_query
    if not query or not query.data or not (query.data.startswith("desc:") or query.data.startswith("pbio:")):
        return False

    user = query.from_user
    caller_id = user.id if user else 0
    username = user.username if user else None
    first_name = user.first_name if user else None
    record_bot_user(conn, caller_id, username, first_name)

    data = query.data
    parts = data.split(":")

    # 1. Принятие / отклонение парного описания
    if data.startswith("pbio:accept:"):
        pair_id = int(parts[2])
        conn.row_factory = sqlite3.Row
        pb = conn.execute("SELECT * FROM pair_bios WHERE id = ?", (pair_id,)).fetchone()
        if not pb or pb["status"] != "pending":
            await query.answer("Заявка не найдена или уже обработана.", show_alert=True)
            return True

        if pb["user_2"] != caller_id:
            await query.answer("🔒 Это приглашение предназначено не вам.", show_alert=True)
            return True

        conn.execute("UPDATE pair_bios SET status = 'active', last_update = ? WHERE id = ?", (now_iso(), pair_id))
        conn.commit()

        lines = json.loads(pb["lines_json"] or "[]")
        line1 = lines[0] if len(lines) > 0 else ""
        line2 = lines[1] if len(lines) > 1 else ""

        prem1 = is_premium(conn, pb["user_1"])
        prem2 = is_premium(conn, pb["user_2"])

        bc_1 = tn_db.get_user_bc_id(conn, pb["user_1"]) or pb["bc_id_1"]
        bc_2 = tn_db.get_user_bc_id(conn, pb["user_2"]) or pb["bc_id_2"]

        if bc_1 and line1:
            await set_business_account_bio(context.bot, bc_1, format_bio_with_watermark(line1, prem1, conn=conn, user_id=pb["user_1"]))
        if bc_2 and line2:
            await set_business_account_bio(context.bot, bc_2, format_bio_with_watermark(line2, prem2, conn=conn, user_id=pb["user_2"]))

        await query.answer("🎉 Парное описание активировано!", show_alert=True)
        await query.edit_message_text(
            f"🎉 *Парное описание с @{pb['username_1']} успешно активировано!*\n\n"
            f"Ваша текущая строка: `{line2}`\n"
            f"Синхронная ротация запущена (каждые {pb['interval_minutes']} мин.).",
            parse_mode="Markdown",
        )

        try:
            await context.bot.send_message(
                chat_id=pb["user_1"],
                text=(
                    f"🎉 *@ {pb['username_2']} принял предложение парного описания!*\n\n"
                    f"Ваша строка: `{line1}`\n"
                    f"Строка друга: `{line2}`\n"
                    f"Синхронное обновление запущено!"
                ),
                parse_mode="Markdown",
            )
        except Exception:
            pass
        return True

    elif data.startswith("pbio:reject:"):
        pair_id = int(parts[2])
        conn.row_factory = sqlite3.Row
        pb = conn.execute("SELECT * FROM pair_bios WHERE id = ?", (pair_id,)).fetchone()
        if pb and pb["status"] == "pending":
            if pb["user_2"] != caller_id:
                await query.answer("🔒 Это приглашение предназначено не вам.", show_alert=True)
                return True

            conn.execute("UPDATE pair_bios SET status = 'rejected' WHERE id = ?", (pair_id,))
            conn.commit()
            await query.answer("Предложение отклонено.")
            await query.edit_message_text("❌ Вы отклонили предложение парного описания.")
            try:
                await context.bot.send_message(
                    chat_id=pb["user_1"],
                    text=f"❌ Пользователь @{pb['username_2']} отклонил предложение парного описания.",
                )
            except Exception:
                pass
        return True

    # 2. Проверка владельца для меню desc:
    target_user_id = caller_id
    if len(parts) >= 3 and parts[-1].isdigit():
        target_user_id = int(parts[-1])
        if caller_id != target_user_id:
            await query.answer("🔒 Вы не можете изменять настройки чужого описания!", show_alert=True)
            return True

    action = parts[1]
    bc_id = tn_db.get_user_bc_id(conn, target_user_id)

    if action == "open_start":
        await query.answer()
        return await description_command(update, context, conn)

    # Настройка одиночного авто-описания
    if action == "setup_single":
        ok, err_msg = await check_name_and_bio_permissions(context.bot, conn, bc_id)
        if not ok:
            await query.answer(RULE_999_NOTICE, show_alert=True)
            return True

        await query.answer()
        context.user_data["desc_state"] = "waiting_single_lines"
        context.user_data["desc_user_id"] = target_user_id
        context.user_data["desc_bc_id"] = bc_id

        prompt = (
            "✍️ *Введите строки для авто-описания через точку с запятой `;`*\n\n"
            "Пример сообщения:\n"
            "```\n"
            "Строка1;\n"
            "Строка2;\n"
            "Строка3\n"
            "```\n"
            "Отправьте сообщение в ответ сюда прямо сейчас 👇"
        )
        cancel_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Отмена", callback_data=f"desc:cancel:{target_user_id}")]
        ])
        await query.message.reply_text(prompt, reply_markup=cancel_kb, parse_mode="Markdown")
        return True

    # Настройка парного описания
    elif action == "setup_pair":
        if not has_feature_access(conn, target_user_id, "pair_bio"):
            await query.answer("🔒 «Парные описания» доступны по Премиум подписке!", show_alert=True)
            return True

        ok, err_msg = await check_name_and_bio_permissions(context.bot, conn, bc_id)
        if not ok:
            await query.answer(RULE_999_NOTICE, show_alert=True)
            return True

        await query.answer()
        context.user_data["desc_state"] = "waiting_pair_username"
        context.user_data["desc_user_id"] = target_user_id
        context.user_data["desc_bc_id"] = bc_id

        prompt = (
            "👫 *Установка парных описаний*\n\n"
            "1. Ваш друг также должен подключить бота к Telegram Business.\n"
            "2. Введите *@username друга* (например, `@alex_dev`):"
        )
        cancel_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Отмена", callback_data=f"desc:cancel:{target_user_id}")]
        ])
        await query.message.reply_text(prompt, reply_markup=cancel_kb, parse_mode="Markdown")
        return True

    # Выбор интервала одиночного описания
    elif action == "int":
        try:
            interval = int(parts[2])
        except (ValueError, IndexError):
            interval = 5

        lines = context.user_data.get("pending_single_lines", [])
        if not lines:
            await query.answer("Ошибка: строки не найдены. Попробуйте снова.", show_alert=True)
            return True

        if not bc_id:
            bc_id = tn_db.get_user_bc_id(conn, target_user_id)

        conn.execute("""
            INSERT INTO auto_descriptions (user_id, business_conn_id, lines_json, interval_minutes, current_index, is_active, last_change)
            VALUES (?, ?, ?, ?, 0, 1, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                business_conn_id = COALESCE(excluded.business_conn_id, auto_descriptions.business_conn_id),
                lines_json = excluded.lines_json,
                interval_minutes = excluded.interval_minutes,
                current_index = 0,
                is_active = 1,
                last_change = excluded.last_change
        """, (target_user_id, bc_id, json.dumps(lines, ensure_ascii=False), interval, now_iso()))
        conn.commit()

        # Немедленно ставим первую строку
        prem = is_premium(conn, target_user_id)
        target_bio = format_bio_with_watermark(lines[0], prem, conn=conn, user_id=target_user_id)
        if bc_id:
            ok = await set_business_account_bio(context.bot, bc_id, target_bio)
            if ok:
                try:
                    conn.execute("""
                        INSERT INTO bio_settings (business_connection_id, user_id, current_bio)
                        VALUES (?, ?, ?)
                        ON CONFLICT(business_connection_id) DO UPDATE SET current_bio = excluded.current_bio, user_id = excluded.user_id
                    """, (bc_id, target_user_id, target_bio))
                    conn.commit()
                except Exception:
                    pass

        context.user_data.pop("pending_single_lines", None)
        context.user_data.pop("desc_state", None)
        context.user_data.pop("desc_user_id", None)

        await query.answer("✅ Авто-описание активировано!", show_alert=True)
        text_content, keyboard = build_description_menu(conn, target_user_id)
        try:
            await query.edit_message_text(text_content, reply_markup=keyboard, parse_mode="Markdown")
        except Exception:
            pass
        return True

    # Выбор интервала парного описания
    elif action == "pair_int":
        try:
            interval = int(parts[2])
        except (ValueError, IndexError):
            interval = 5

        lines = context.user_data.get("pending_pair_lines", [])
        friend_id = context.user_data.get("pair_friend_id")
        friend_username = context.user_data.get("pair_friend_username")
        friend_bc_id = context.user_data.get("pair_friend_bc_id")

        if not lines or not friend_id:
            await query.answer("Ошибка данных. Начните настройку заново.", show_alert=True)
            return True

        cur = conn.execute("""
            INSERT INTO pair_bios (user_1, user_2, username_1, username_2, bc_id_1, bc_id_2, lines_json, interval_minutes, current_step, status, last_update)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 'pending', ?)
        """, (target_user_id, friend_id, username or str(target_user_id), friend_username, bc_id, friend_bc_id, json.dumps(lines, ensure_ascii=False), interval, now_iso()))
        conn.commit()
        pair_id = cur.lastrowid

        odd_preview = "\n".join([f"• {lines[i]}" for i in range(0, len(lines), 2)])
        even_preview = "\n".join([f"• {lines[i]}" for i in range(1, len(lines), 2)])

        invitation_text = (
            "💌 *Предложение парного описания!*\n\n"
            f"Пользователь @{username or 'собеседник'} предлагает вам установить синхронные парные описания.\n\n"
            f"📜 *Ваши строки (чётные):*\n{even_preview}\n\n"
            f"📜 *Строки @{username} (нечётные):*\n{odd_preview}\n\n"
            f"⏱ Интервал смены: каждые *{interval} мин.*\n\n"
            "Принять предложение?"
        )
        invitation_kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Принять", callback_data=f"pbio:accept:{pair_id}"),
                InlineKeyboardButton("❌ Отклонить", callback_data=f"pbio:reject:{pair_id}"),
            ]
        ])

        try:
            await context.bot.send_message(
                chat_id=friend_id,
                text=invitation_text,
                reply_markup=invitation_kb,
                parse_mode="Markdown",
            )
            await query.answer("📨 Предложение отправлено другу в ЛС бота!", show_alert=True)
            await query.message.reply_text(
                f"📨 *Предложение отправлено @{friend_username}!* Ожидаем, пока друг нажмёт кнопку в личке бота.",
                parse_mode="Markdown",
            )
        except Exception as e:
            log.error(f"failed to send pair invitation to {friend_id}: {e}")
            await query.answer("Не удалось отправить сообщение другу в ЛС. Убедитесь, что друг запустил бота!", show_alert=True)

        context.user_data.pop("pending_pair_lines", None)
        context.user_data.pop("pair_friend_id", None)
        context.user_data.pop("desc_state", None)
        context.user_data.pop("desc_user_id", None)
        return True

    # Сброс описания строго для данного пользователя
    elif action == "reset_all":
        conn.execute("DELETE FROM auto_descriptions WHERE user_id = ?", (target_user_id,))
        conn.execute("UPDATE pair_bios SET status = 'cancelled' WHERE (user_1 = ? OR user_2 = ?) AND status = 'active'", (target_user_id, target_user_id))
        conn.commit()

        # Восстанавливаем стандартное био
        if bc_id:
            await apply_bio_for_connection(context.bot, conn, bc_id, target_user_id)

        await query.answer("❌ Авто-описание отключено!", show_alert=True)
        text_content, keyboard = build_description_menu(conn, target_user_id)
        try:
            await query.edit_message_text(text_content, reply_markup=keyboard, parse_mode="Markdown")
        except Exception:
            pass
        return True

    elif action == "cancel":
        context.user_data.pop("desc_state", None)
        context.user_data.pop("desc_user_id", None)
        await query.answer("Отменено.")
        try:
            await query.message.delete()
        except Exception:
            pass
        return True

    return False


# ─────────────────────── Ввод текста для описания ───────────────────────

async def handle_description_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE, conn: sqlite3.Connection) -> bool:
    """Обрабатывает текстовые ответы пользователя при настройке описаний."""
    msg = update.message
    if not msg or not msg.text:
        return False

    state = context.user_data.get("desc_state")
    if not state:
        return False

    user = msg.from_user
    user_id = user.id if user else 0
    if not user_id:
        return False

    expected_uid = context.user_data.get("desc_user_id", user_id)
    if user_id != expected_uid:
        return False

    text = msg.text.strip()

    # 1. Ожидание строк одиночного авто-описания
    if state == "waiting_single_lines":
        raw_parts = [p.strip() for p in text.replace("\n", ";").split(";") if p.strip()]
        if not raw_parts:
            await msg.reply_text(
                "⚠️ Не найдено строк! Разделяйте строки точкой с запятой `;` или переносом строки.\nПопробуйте ещё раз:"
            )
            return True

        context.user_data["pending_single_lines"] = raw_parts
        context.user_data["desc_state"] = None

        preview = "\n".join([f"{i}. {line}" for i, line in enumerate(raw_parts, 1)])
        interval_kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("⏱ Каждые 1 мин.", callback_data=f"desc:int:1:{user_id}"),
                InlineKeyboardButton("⏱ Каждые 5 мин.", callback_data=f"desc:int:5:{user_id}"),
            ],
            [
                InlineKeyboardButton("⏱ Каждые 15 мин.", callback_data=f"desc:int:15:{user_id}"),
                InlineKeyboardButton("⏱ Каждые 30 мин.", callback_data=f"desc:int:30:{user_id}"),
            ],
            [
                InlineKeyboardButton("⏱ Раз в 1 час", callback_data=f"desc:int:60:{user_id}"),
                InlineKeyboardButton("❌ Отмена", callback_data=f"desc:cancel:{user_id}"),
            ]
        ])

        desc_type_note = "Выберите интервал смены описания 👇" if len(raw_parts) > 1 else "Указана 1 строка (будет установлена в профиль). Выберите подтверждение 👇"
        await msg.reply_text(
            f"✅ *Строки успешно приняты ({len(raw_parts)} шт.):*\n\n{preview}\n\n{desc_type_note}",
            reply_markup=interval_kb,
            parse_mode="Markdown",
        )
        return True

    # 2. Ожидание юзернейма друга для парного био
    elif state == "waiting_pair_username":
        clean_user = text.lstrip("@").strip()
        friend_id = resolve_user_id(conn, clean_user)
        if not friend_id:
            await msg.reply_text(
                f"❌ Пользователь `@{clean_user}` не найден в базе бота.\n\n"
                f"Друг должен сначала написать боту команду /start и подключить Telegram Business!",
                parse_mode="Markdown",
            )
            return True

        if friend_id == user_id:
            await msg.reply_text("⚠️ Нельзя установить парное описание с самим собой!")
            return True

        friend_bc_id = tn_db.get_user_bc_id(conn, friend_id)
        if not friend_bc_id:
            await msg.reply_text(
                f"⚠️ У пользователя `@{clean_user}` не подключен Telegram Business!\n"
                f"Попросите друга подключить бота в *Настройки* ➔ *Telegram для бизнеса* ➔ *Чат-боты*.",
                parse_mode="Markdown",
            )
            return True

        context.user_data["pair_friend_id"] = friend_id
        context.user_data["pair_friend_username"] = clean_user
        context.user_data["pair_friend_bc_id"] = friend_bc_id
        context.user_data["desc_state"] = "waiting_pair_lines"

        await msg.reply_text(
            f"✅ Друг `@{clean_user}` найден!\n\n"
            f"Теперь отправьте строки через точку с запятой `;`.\n"
            f"❗️ *Важно:* нечётные строки (1, 3, 5...) будут у вас, а чётные (2, 4, 6...) — у друга!\n\n"
            f"Пример:\n"
            f"```\n"
            f"я люблю её; я люблю его;\n"
            f"мой зайчик; моя зайка\n"
            f"```",
            parse_mode="Markdown",
        )
        return True

    # 3. Ожидание строк для парного био
    elif state == "waiting_pair_lines":
        raw_parts = [p.strip() for p in text.split(";") if p.strip()]
        if len(raw_parts) < 2:
            await msg.reply_text(
                "⚠️ Укажите минимум 2 парные строки через `;`!\nПопробуйте ещё раз:",
                parse_mode="Markdown",
            )
            return True

        if len(raw_parts) % 2 != 0:
            await msg.reply_text(
                f"⚠️ Количество строк должно быть чётным (для двоих)! Вы ввели: {len(raw_parts)}.\n"
                f"Добавьте ещё одну строку через `;`:",
                parse_mode="Markdown",
            )
            return True

        context.user_data["pending_pair_lines"] = raw_parts
        context.user_data["desc_state"] = None

        user1_lines = [raw_parts[i] for i in range(0, len(raw_parts), 2)]
        user2_lines = [raw_parts[i] for i in range(1, len(raw_parts), 2)]

        preview1 = "\n".join([f"• {l}" for l in user1_lines])
        preview2 = "\n".join([f"• {l}" for l in user2_lines])

        interval_kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("⏱ Каждые 1 мин.", callback_data=f"desc:pair_int:1:{user_id}"),
                InlineKeyboardButton("⏱ Каждые 5 мин.", callback_data=f"desc:pair_int:5:{user_id}"),
            ],
            [
                InlineKeyboardButton("⏱ Каждые 15 мин.", callback_data=f"desc:pair_int:15:{user_id}"),
                InlineKeyboardButton("⏱ Каждые 30 мин.", callback_data=f"desc:pair_int:30:{user_id}"),
            ],
            [
                InlineKeyboardButton("⏱ Раз в 1 час", callback_data=f"desc:pair_int:60:{user_id}"),
                InlineKeyboardButton("❌ Отмена", callback_data=f"desc:cancel:{user_id}"),
            ]
        ])

        await msg.reply_text(
            f"👫 *Парные строки приняты!*\n\n"
            f"👤 *Ваши строки:*\n{preview1}\n\n"
            f"👥 *Строки @{context.user_data.get('pair_friend_username')}:*\n{preview2}\n\n"
            f"Выберите интервал синхронной смены описания 👇",
            reply_markup=interval_kb,
            parse_mode="Markdown",
        )
        return True

    return False
