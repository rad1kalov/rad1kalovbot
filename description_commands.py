# description_commands.py
"""Команда /description, настройка авто-описаний и парных био с проверкой Правила 999."""

import json
import logging
import sqlite3
from typing import Optional, List, Tuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

import tn_db
from permissions_check import check_name_and_bio_permissions, RULE_999_NOTICE, is_own_business_message, get_connection_owner_id
from admin_db import (
    has_feature_access,
    resolve_user_id,
    record_bot_user,
    OWNER_ID,
)
from bio_worker import set_business_account_bio, WATERMARK_SUFFIX, MAX_BIO_LENGTH
from premium_db import is_premium
from timezone import now_iso

log = logging.getLogger(__name__)


def _get_user_bc_id(conn: sqlite3.Connection, user_id: int) -> Optional[str]:
    return tn_db.get_user_bc_id(conn, user_id)


def format_bio_with_watermark(bio_line: str, is_prem: bool) -> str:
    """Форматирует строку описания с учетом водяного знака для бесплатных пользователей."""
    clean = bio_line.strip()
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
            InlineKeyboardButton("➕ Настроить строки описания", callback_data="desc:setup_single"),
        ],
        [
            InlineKeyboardButton("👫 Установить парные описания", callback_data="desc:setup_pair"),
        ],
    ]

    if has_active:
        keyboard.append([
            InlineKeyboardButton("❌ Отключить авто-описание", callback_data="desc:reset_all"),
        ])

    keyboard.append([
        InlineKeyboardButton("⬅️ В главное меню", callback_data="start_back"),
    ])

    return "\n".join(lines), InlineKeyboardMarkup(keyboard)


# ─────────────────────── Команда /description ───────────────────────

async def description_command(update: Update, context: ContextTypes.DEFAULT_TYPE, conn: sqlite3.Connection) -> bool:
    """Обработчик команды /description и .description."""
    msg = update.business_message or update.message
    if not msg:
        return False

    # Если команда отправлена в бизнес-чате, разрешаем её ТОЛЬКО владельцу аккаунта
    if getattr(msg, "business_connection_id", None):
        is_own = await is_own_business_message(context.bot, conn, msg)
        if not is_own:
            return False

    sender = msg.from_user
    user_id = sender.id if sender else 0
    username = sender.username if sender else None
    first_name = sender.first_name if sender else None

    record_bot_user(conn, user_id, username, first_name)

    conn_id = getattr(msg, "business_connection_id", None)
    bc_id = conn_id or _get_user_bc_id(conn, user_id)

    # ───── ПРАВИЛО 999 ─────
    ok, err_msg = await check_name_and_bio_permissions(context.bot, conn, bc_id)
    if not ok:
        try:
            await context.bot.send_message(
                chat_id=msg.chat_id,
                text=err_msg,
                business_connection_id=getattr(msg, "business_connection_id", None),
                parse_mode="Markdown",
            )
        except Exception:
            await msg.reply_text(err_msg, parse_mode="Markdown")
        return True
    # ────────────────────────

    owner_id = (await get_connection_owner_id(context.bot, conn, bc_id)) or user_id
    text_content, keyboard = build_description_menu(conn, owner_id)

    try:
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
    user_id = user.id if user else 0
    username = user.username if user else None
    first_name = user.first_name if user else None
    record_bot_user(conn, user_id, username, first_name)

    bc_id = getattr(query.message, "business_connection_id", None) or _get_user_bc_id(conn, user_id)

    if bc_id:
        owner_id = await get_connection_owner_id(context.bot, conn, bc_id)
        if owner_id and user_id != owner_id:
            await query.answer("🔒 Настройки описания может изменять только владелец аккаунта!", show_alert=True)
            return True

    data = query.data

    # 1. Настройка одиночного авто-описания
    if data == "desc:setup_single":
        # Проверяем правило 999
        ok, err_msg = await check_name_and_bio_permissions(context.bot, conn, bc_id)
        if not ok:
            await query.answer(RULE_999_NOTICE, show_alert=True)
            return True

        await query.answer()
        context.user_data["desc_state"] = "waiting_single_lines"
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
            [InlineKeyboardButton("❌ Отмена", callback_data="desc:cancel")]
        ])
        await query.message.reply_text(prompt, reply_markup=cancel_kb, parse_mode="Markdown")
        return True

    # 2. Настройка парного описания
    elif data == "desc:setup_pair":
        if not has_feature_access(conn, user_id, "pair_bio"):
            await query.answer("🔒 «Парные описания» доступны по Премиум подписке!", show_alert=True)
            return True

        ok, err_msg = await check_name_and_bio_permissions(context.bot, conn, bc_id)
        if not ok:
            await query.answer(RULE_999_NOTICE, show_alert=True)
            return True

        await query.answer()
        context.user_data["desc_state"] = "waiting_pair_username"
        context.user_data["desc_bc_id"] = bc_id

        prompt = (
            "👫 *Установка парных описаний*\n\n"
            "1. Ваш друг также должен подключить бота к Telegram Business.\n"
            "2. Введите *@username друга* (например, `@alex_dev`):"
        )
        cancel_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Отмена", callback_data="desc:cancel")]
        ])
        await query.message.reply_text(prompt, reply_markup=cancel_kb, parse_mode="Markdown")
        return True

    # 3. Выбор интервала одиночного описания
    elif data.startswith("desc:int:"):
        try:
            interval = int(data.split(":")[-1])
        except ValueError:
            interval = 5

        lines = context.user_data.get("pending_single_lines", [])
        if not lines:
            await query.answer("Ошибка: строки не найдены. Попробуйте снова.", show_alert=True)
            return True

        # Сохраняем в БД
        conn.execute("""
            INSERT INTO auto_descriptions (user_id, business_conn_id, lines_json, interval_minutes, current_index, is_active, last_change)
            VALUES (?, ?, ?, ?, 0, 1, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                business_conn_id = excluded.business_conn_id,
                lines_json = excluded.lines_json,
                interval_minutes = excluded.interval_minutes,
                current_index = 0,
                is_active = 1,
                last_change = excluded.last_change
        """, (user_id, bc_id, json.dumps(lines, ensure_ascii=False), interval, now_iso()))
        conn.commit()

        # Немедленно ставим первую строку
        prem = is_premium(conn, user_id)
        target_bio = format_bio_with_watermark(lines[0], prem)
        if bc_id:
            await set_business_account_bio(context.bot, bc_id, target_bio)

        context.user_data.pop("pending_single_lines", None)
        context.user_data.pop("desc_state", None)

        await query.answer("✅ Авто-описание активировано!", show_alert=True)
        text_content, keyboard = build_description_menu(conn, user_id)
        try:
            await query.edit_message_text(text_content, reply_markup=keyboard, parse_mode="Markdown")
        except Exception:
            pass
        return True

    # 4. Выбор интервала парного описания
    elif data.startswith("desc:pair_int:"):
        try:
            interval = int(data.split(":")[-1])
        except ValueError:
            interval = 5

        lines = context.user_data.get("pending_pair_lines", [])
        friend_id = context.user_data.get("pair_friend_id")
        friend_username = context.user_data.get("pair_friend_username")
        friend_bc_id = context.user_data.get("pair_friend_bc_id")

        if not lines or not friend_id:
            await query.answer("Ошибка данных. Начните настройку заново.", show_alert=True)
            return True

        # Создаём заявку в pair_bios со статусом pending
        cur = conn.execute("""
            INSERT INTO pair_bios (user_1, user_2, username_1, username_2, bc_id_1, bc_id_2, lines_json, interval_minutes, current_step, status, last_update)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 'pending', ?)
        """, (user_id, friend_id, username or str(user_id), friend_username, bc_id, friend_bc_id, json.dumps(lines, ensure_ascii=False), interval, now_iso()))
        conn.commit()
        pair_id = cur.lastrowid

        # Отправляем приглашение другу в ЛС бота
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
        return True

    # 5. Принятие / Отклонение парного описания
    elif data.startswith("pbio:accept:"):
        pair_id = int(data.split(":")[-1])
        conn.row_factory = sqlite3.Row
        pb = conn.execute("SELECT * FROM pair_bios WHERE id = ?", (pair_id,)).fetchone()
        if not pb or pb["status"] != "pending":
            await query.answer("Заявка не найдена или уже обработана.", show_alert=True)
            return True

        if pb["user_2"] != user_id:
            await query.answer("Это приглашение предназначено не вам.", show_alert=True)
            return True

        conn.execute("UPDATE pair_bios SET status = 'active', last_update = ? WHERE id = ?", (now_iso(), pair_id))
        conn.commit()

        lines = json.loads(pb["lines_json"] or "[]")
        # Синхронно устанавливаем: строка 1 (индекс 0) у User 1, строка 2 (индекс 1) у User 2
        line1 = lines[0] if len(lines) > 0 else ""
        line2 = lines[1] if len(lines) > 1 else ""

        prem1 = is_premium(conn, pb["user_1"])
        prem2 = is_premium(conn, pb["user_2"])

        if pb["bc_id_1"] and line1:
            await set_business_account_bio(context.bot, pb["bc_id_1"], format_bio_with_watermark(line1, prem1))
        if pb["bc_id_2"] and line2:
            await set_business_account_bio(context.bot, pb["bc_id_2"], format_bio_with_watermark(line2, prem2))

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
        pair_id = int(data.split(":")[-1])
        conn.row_factory = sqlite3.Row
        pb = conn.execute("SELECT * FROM pair_bios WHERE id = ?", (pair_id,)).fetchone()
        if pb and pb["status"] == "pending":
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

    # 6. Сброс описания
    elif data == "desc:reset_all":
        conn.execute("DELETE FROM auto_descriptions WHERE user_id = ?", (user_id,))
        conn.execute("UPDATE pair_bios SET status = 'cancelled' WHERE (user_1 = ? OR user_2 = ?) AND status = 'active'", (user_id, user_id))
        conn.commit()
        await query.answer("❌ Авто-описание отключено!", show_alert=True)
        text_content, keyboard = build_description_menu(conn, user_id)
        try:
            await query.edit_message_text(text_content, reply_markup=keyboard, parse_mode="Markdown")
        except Exception:
            pass
        return True

    elif data == "desc:cancel":
        context.user_data.pop("desc_state", None)
        await query.answer("Отменено.")
        try:
            await query.message.delete()
        except Exception:
            pass
        return True

    return False


# ─────────────────────── Обработка текстовых ответов в ЛС ───────────────────────

async def handle_description_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE, conn: sqlite3.Connection) -> bool:
    """Перехватывает шаги ввода строк описания или юзернейма друга."""
    msg = update.message
    if not msg or not msg.text:
        return False

    state = context.user_data.get("desc_state")
    if not state:
        return False

    user = msg.from_user
    user_id = user.id if user else 0
    text = msg.text.strip()

    # Шаг 1: ввод строк одиночного описания
    if state == "waiting_single_lines":
        lines = [l.strip() for l in text.split(";") if l.strip()]
        if not lines:
            await msg.reply_text("⚠️ Введите хотя бы одну строку! Пример: `Строка1;\nСтрока2`", parse_mode="Markdown")
            return True

        context.user_data["pending_single_lines"] = lines
        context.user_data["desc_state"] = None

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⏱ 5 минут", callback_data="desc:int:5"), InlineKeyboardButton("⏱ 15 минут", callback_data="desc:int:15")],
            [InlineKeyboardButton("⏱ 30 минут", callback_data="desc:int:30"), InlineKeyboardButton("⏱ 1 час", callback_data="desc:int:60")],
            [InlineKeyboardButton("⏱ 3 часа", callback_data="desc:int:180"), InlineKeyboardButton("⏱ 6 часов", callback_data="desc:int:360")],
        ])

        preview = "\n".join([f"{i}. {l}" for i, l in enumerate(lines, 1)])
        await msg.reply_text(
            f"✅ *Строки получены ({len(lines)} шт.):*\n\n{preview}\n\n"
            "⏱ *Выберите интервал смены описания кнопками ниже:*",
            reply_markup=kb,
            parse_mode="Markdown",
        )
        return True

    # Шаг 2: ввод юзернейма друга для парного био
    elif state == "waiting_pair_username":
        clean_user = text.lstrip("@").strip().lower()
        friend_id = resolve_user_id(conn, clean_user)
        if not friend_id:
            await msg.reply_text(
                f"⚠️ Пользователь @{clean_user} не найден в базе бота.\n\n"
                "Друг должен сначала написать в бота /start и подключить Telegram Business!",
                parse_mode="Markdown",
            )
            return True

        friend_bc_id = _get_user_bc_id(conn, friend_id)
        if not friend_bc_id:
            await msg.reply_text(
                f"⚠️ Пользователь @{clean_user} ещё не подключил бота к Telegram Business.\n"
                "Попросите друга подключить бота в настройках Telegram для бизнеса!",
                parse_mode="Markdown",
            )
            return True

        context.user_data["pair_friend_id"] = friend_id
        context.user_data["pair_friend_username"] = clean_user
        context.user_data["pair_friend_bc_id"] = friend_bc_id
        context.user_data["desc_state"] = "waiting_pair_lines"

        await msg.reply_text(
            f"✅ Друг *@ {clean_user}* найден!\n\n"
            "✍️ *Отправьте строки для парного описания через `;` (точку с запятой):*\n"
            "Нечётные строки (1, 3, 5...) будут у вас, а чётные (2, 4, 6...) — у друга!\n\n"
            "Пример:\n"
            "```\n"
            "Моё сердце занято;\n"
            "Её сердце занято;\n"
            "Люблю её;\n"
            "Люблю его\n"
            "```",
            parse_mode="Markdown",
        )
        return True

    # Шаг 3: ввод строк парного био
    elif state == "waiting_pair_lines":
        lines = [l.strip() for l in text.split(";") if l.strip()]
        if len(lines) < 2:
            await msg.reply_text("⚠️ Для парного описания нужно минимум 2 строки (для вас и для друга) через `;`!", parse_mode="Markdown")
            return True

        context.user_data["pending_pair_lines"] = lines
        context.user_data["desc_state"] = None

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⏱ 5 минут", callback_data="desc:pair_int:5"), InlineKeyboardButton("⏱ 15 минут", callback_data="desc:pair_int:15")],
            [InlineKeyboardButton("⏱ 30 минут", callback_data="desc:pair_int:30"), InlineKeyboardButton("⏱ 1 час", callback_data="desc:pair_int:60")],
            [InlineKeyboardButton("⏱ 3 часа", callback_data="desc:pair_int:180")],
        ])

        friend_name = context.user_data.get("pair_friend_username", "друга")
        odd_preview = "\n".join([f"• {lines[i]}" for i in range(0, len(lines), 2)])
        even_preview = "\n".join([f"• {lines[i]}" for i in range(1, len(lines), 2)])

        await msg.reply_text(
            f"✅ *Строки для парного описания приняты!*\n\n"
            f"👤 *У вас (нечётные):*\n{odd_preview}\n\n"
            f"👥 *У @{friend_name} (чётные):*\n{even_preview}\n\n"
            "⏱ *Выберите интервал смены описания:*",
            reply_markup=kb,
            parse_mode="Markdown",
        )
        return True

    return False
