# series_commands.py
"""Интерактивное управление серией общения через единую команду .streak с карточками и темами оформления."""

import logging
import os
import sqlite3
from datetime import datetime, timezone as _tz
from typing import Tuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from series import plural_days
from series_db import (
    ensure_series,
    get_series,
    log_event,
    set_series_enabled,
    set_notify_mode,
    set_stage,
    get_all_active_series,
)
from series_styles import format_styled_streak_card
from stages import (
    stage_name,
    stage_info,
    next_stage,
    prev_stage,
    get_stage_by_days,
    get_progress,
    render_progress_bar,
    STAGES,
    STAGE_CONFIG,
)
from timezone import today_str, now
from admin_db import has_feature_access
from permissions_check import is_own_business_message

log = logging.getLogger(__name__)

OWNER_ID = int(os.getenv("OWNER_ID", "8470104943"))


def get_series_owner_id(conn: sqlite3.Connection, s: dict) -> int:
    bc_id = s.get("business_connection_id")
    if bc_id:
        row = conn.execute("SELECT user_id FROM settings WHERE business_connection_id = ?", (bc_id,)).fetchone()
        if row and row[0]:
            return row[0]
        row2 = conn.execute("SELECT user_id FROM bio_settings WHERE business_connection_id = ?", (bc_id,)).fetchone()
        if row2 and row2[0]:
            return row2[0]
    return OWNER_ID


def is_series_owner(conn: sqlite3.Connection, user_id: int, s: dict) -> bool:
    owner_id = get_series_owner_id(conn, s)
    return user_id == owner_id or user_id == OWNER_ID


def _get_max_limits(conn: sqlite3.Connection, user_id: int) -> Tuple[int, int]:
    """Возвращает (max_revives, max_freezes) с учётом расширенных лимитов."""
    if has_feature_access(conn, user_id, "extended_limits"):
        return 10, 10
    return 3, 2


# ─────────────────────── Генерация экранов и клавиатур ───────────────────────

def build_main_screen(s: dict) -> Tuple[str, InlineKeyboardMarkup]:
    chat_id = s["chat_id"]
    card = format_styled_streak_card(s)
    text_content = f"{card}\n\n_Выберите категорию управления кнопками ниже:_"

    keyboard = [
        [
            InlineKeyboardButton("📊 Статистика", callback_data=f"strk:stats:{chat_id}"),
            InlineKeyboardButton("🎭 Стадии", callback_data=f"strk:stages:{chat_id}"),
        ],
        [
            InlineKeyboardButton("🛡 Защита", callback_data=f"strk:protect:{chat_id}"),
            InlineKeyboardButton("⚙️ Настройки", callback_data=f"strk:settings:{chat_id}"),
        ],
    ]

    return text_content, InlineKeyboardMarkup(keyboard)


def build_stats_screen(s: dict, conn: sqlite3.Connection) -> Tuple[str, InlineKeyboardMarkup]:
    chat_id = s["chat_id"]
    today = today_str()
    status_str = "Продлена ✅" if s.get("last_date") == today else "Не продлена ⏳"
    if s.get("frozen"):
        status_str = "Заморожена ❄️"

    owner_id = get_series_owner_id(conn, s)
    max_rev, max_frz = _get_max_limits(conn, owner_id)

    lines = [
        "📊 *Подробная статистика серии*",
        "━━━━━━━━━━━━━━━",
        f"🔥 Текущая серия: *{s.get('current_streak', 0)} {plural_days(s.get('current_streak', 0))}*",
        f"🏆 Личный рекорд: *{s.get('best_streak', 0)} {plural_days(s.get('best_streak', 0))}*",
        f"🎭 Стадия: *{stage_name(s.get('stage', 'spark'))}*",
        f"📅 Всего дней общения: *{s.get('total_days', 0)}*",
        f"📌 Статус сегодня: *{status_str}*",
        "━━━━━━━━━━━━━━━",
        f"💫 Воскрешений в запасе: *{s.get('revives_left', 0)}/{max_rev}*",
        f"❄️ Заморозок в запасе: *{s.get('freezes_left', 0)}/{max_frz}*",
        f"🔔 Напоминания в 21:00: *{'вкл ✅' if s.get('reminders') else 'выкл ❌'}*",
    ]

    keyboard = [
        [
            InlineKeyboardButton("📜 История событий", callback_data=f"strk:hist:{chat_id}"),
            InlineKeyboardButton("🏆 Топ серий", callback_data=f"strk:top:{chat_id}"),
        ],
        [
            InlineKeyboardButton("⬅️ Назад в меню", callback_data=f"strk:main:{chat_id}")
        ],
    ]
    return "\n".join(lines), InlineKeyboardMarkup(keyboard)


def build_stages_screen(s: dict) -> Tuple[str, InlineKeyboardMarkup]:
    chat_id = s["chat_id"]
    current_key = s.get("stage", "spark")
    streak = s.get("current_streak", 0)
    cur_span, req_span, pct, nxt_key = get_progress(streak, current_key)
    bar = render_progress_bar(pct, length=10)

    hide_stg = bool(s.get("hide_stage", 0))
    custom_stg = s.get("custom_stage")

    custom_line = f"\n✨ Кастомная стадия: *{custom_stg}*" if custom_stg else ""
    hide_line = "\n🙈 Стадия в карточке: *СКРЫТА 🔒*" if hide_stg else ""

    lines = [
        f"🎭 *Стадии отношений: {stage_name(current_key)}*",
        f"_{stage_info(current_key)['description']}_\n",
        f"Прогресс уровня: `{bar}` {int(pct)}%",
    ]

    if nxt_key:
        cfg = STAGE_CONFIG[nxt_key]
        remaining = req_span - cur_span
        lines.append(f"Следующая: *{cfg['title']} {cfg['icon']}* (ещё {remaining} {plural_days(remaining)})\n")
    else:
        lines.append("👑 Достигнута наивысшая ступень!\n")

    lines.append("📜 *Лестница уровней:*")
    for key in STAGES:
        cfg = STAGE_CONFIG[key]
        marker = "👉 " if key == current_key else "• "
        lines.append(f"{marker}{cfg['title']} {cfg['icon']} ({cfg['min_days']}+ дн.)")

    mode_label = "автоматический (по дням)" if s.get("auto_stage", 1) else "ручной"
    lines.append(f"\nРежим стадии: *{mode_label}*{custom_line}{hide_line}")

    custom_btn_label = "🔄 Сбросить кастомную" if custom_stg else "✨ Задать кастомную"
    hide_btn_label = "🙈 Скрыть стадию: ВЫКЛ" if hide_stg else "🙈 Скрыть стадию: ВКЛ"

    keyboard = [
        [
            InlineKeyboardButton("⬆️ Повысить", callback_data=f"strk:stg_up:{chat_id}"),
            InlineKeyboardButton("⬇️ Понизить", callback_data=f"strk:stg_down:{chat_id}"),
        ],
        [
            InlineKeyboardButton("🔄 Включить авто-расчёт", callback_data=f"strk:stg_auto:{chat_id}")
        ],
        [
            InlineKeyboardButton(custom_btn_label, callback_data=f"strk:stg_custom:{chat_id}"),
            InlineKeyboardButton(hide_btn_label, callback_data=f"strk:tog_hide_stg:{chat_id}"),
        ],
        [
            InlineKeyboardButton("⬅️ Назад в меню", callback_data=f"strk:main:{chat_id}")
        ],
    ]
    return "\n".join(lines), InlineKeyboardMarkup(keyboard)


def build_protect_screen(s: dict, conn: sqlite3.Connection) -> Tuple[str, InlineKeyboardMarkup]:
    chat_id = s["chat_id"]
    frozen_status = "Активна на сегодня ❄️ (серия в безопасности)" if s.get("frozen") else "Не активна"
    streak = s.get("current_streak", 0)
    pre_burn = s.get("pre_burn_streak", 0)
    auto_frz = bool(s.get("auto_freeze", 0))

    streak_state = f"Активен ({streak} дн.)" if streak > 0 else f"Сгорел (можно восстановить {pre_burn} дн.)" if pre_burn > 0 else "0 дней"
    owner_id = get_series_owner_id(conn, s)
    max_rev, max_frz = _get_max_limits(conn, owner_id)

    lines = [
        "🛡 *Защита и Восстановление серии*",
        "━━━━━━━━━━━━━━━",
        f"❄️ Заморозка на сегодня: *{frozen_status}*",
        f"⚡️ Автозаморозка в 23:30: *{'ВКЛЮЧЕНА ✅' if auto_frz else 'ВЫКЛЮЧЕНА ❌'}*",
        f"🔥 Состояние стрика: *{streak_state}*",
        f"💫 Доступно воскрешений: *{s.get('revives_left', 0)}/{max_rev}*",
        f"❄️ Доступно заморозок: *{s.get('freezes_left', 0)}/{max_frz}*",
        "━━━━━━━━━━━━━━━",
        "• *Заморозка* защищает серию от сгорания, если сегодня нет возможности написать.",
        "• *Автозаморозка* автоматически спасает стрик в 23:30, если общение не состоялось.",
        "• *Воскрешение* восстанавливает сгоревший стрик в течение 72 часов.",
    ]

    auto_frz_btn = "⚡️ Автозаморозка: ВЫКЛ" if auto_frz else "⚡️ Автозаморозка: ВКЛ"

    keyboard = [
        [
            InlineKeyboardButton("❄️ Заморозить на сегодня", callback_data=f"strk:act_frz:{chat_id}"),
        ],
        [
            InlineKeyboardButton(auto_frz_btn, callback_data=f"strk:tog_auto_frz:{chat_id}"),
        ],
        [
            InlineKeyboardButton("✨ Воскресить серию", callback_data=f"strk:act_rev:{chat_id}"),
        ],
        [
            InlineKeyboardButton("⬅️ Назад в меню", callback_data=f"strk:main:{chat_id}"),
        ],
    ]
    return "\n".join(lines), InlineKeyboardMarkup(keyboard)


def build_settings_screen(s: dict) -> Tuple[str, InlineKeyboardMarkup]:
    chat_id = s["chat_id"]
    enabled = bool(s.get("enabled", 0))
    mode_names = {
        "milestones": "Юбилеи и стадии 🌟",
        "all": "Каждый день 📅",
        "silent": "Тихий режим 🤫",
    }
    mode = mode_names.get(s.get("notify_mode", "milestones"), "Юбилеи и стадии 🌟")
    remind_str = "Включены ✅ (в 21:00)" if s.get("reminders", 1) else "Выключены ❌"

    lines = [
        "⚙️ *Настройки серии в этом чате*",
        "━━━━━━━━━━━━━━━",
        f"• Трекер серии: *{'Включён 🟢' if enabled else 'Выключен 🔴'}*",
        f"• Уведомления: *{mode}*",
        f"• Напоминания в 21:00: *{remind_str}*",
        "━━━━━━━━━━━━━━━",
        "Нажмите на нужную кнопку для изменения параметра:",
    ]

    toggle_btn_text = "🔴 Выключить серию" if enabled else "🟢 Включить серию"

    keyboard = [
        [
            InlineKeyboardButton(toggle_btn_text, callback_data=f"strk:tog_en:{chat_id}"),
        ],
        [
            InlineKeyboardButton(f"💬 Режим: {mode}", callback_data=f"strk:cyc_mode:{chat_id}"),
        ],
        [
            InlineKeyboardButton("🔔 Напоминания: Вкл/Выкл", callback_data=f"strk:tog_rem:{chat_id}"),
        ],
        [
            InlineKeyboardButton("⬅️ Назад в меню", callback_data=f"strk:main:{chat_id}"),
        ],
    ]
    return "\n".join(lines), InlineKeyboardMarkup(keyboard)


# ─────────────────────── Обработчик команды streak ───────────────────────

async def handle_series_command(update: Update, context: ContextTypes.DEFAULT_TYPE, conn: sqlite3.Connection) -> bool:
    """
    Команда .streak (или /streak) с поддержкой подкоманд:
    - .streak
    - .streak custom <текст>
    - .streak custom reset
    """
    msg = update.business_message or update.message
    if not msg or not msg.text:
        return False

    raw = msg.text.strip()
    lower = raw.lower()

    if not lower.startswith((".streak", "/streak", ".серия", "/серия")):
        return False

    # Если команда отправлена в бизнес-чате, разрешаем её ТОЛЬКО владельцу аккаунта
    if getattr(msg, "business_connection_id", None):
        is_own = await is_own_business_message(context.bot, conn, msg)
        if not is_own:
            return False

    chat_id = msg.chat_id
    conn_id = getattr(msg, "business_connection_id", None)
    sender = msg.from_user
    user_id = sender.id if sender else 0
    user_name = sender.full_name if sender else "Собеседник"

    s = ensure_series(conn, chat_id, conn_id, user_name)

    parts = raw.split()
    # Проверяем подкоманду custom
    if len(parts) >= 2 and parts[1].lower() in ("custom", "кастом"):
        if not has_feature_access(conn, user_id, "custom_stage"):
            await msg.reply_text(
                "🔒 *Кастомная стадия общения доступна по Премиум подписке!*\n\n"
                "Оформите подписку через /premium, чтобы задать собственное название стадии серии.",
                parse_mode="Markdown",
            )
            return True

        if len(parts) >= 3:
            sub = parts[2].lower()
            if sub in ("reset", "сброс", "удалить", "off"):
                conn.execute("UPDATE series SET custom_stage = NULL WHERE chat_id = ?", (chat_id,))
                conn.commit()
                await msg.reply_text("✅ Кастомная стадия сброшена! Отображается стандартная лестница стадий.")
                return True
            else:
                custom_val = " ".join(parts[2:]).strip()[:40]
                conn.execute("UPDATE series SET custom_stage = ? WHERE chat_id = ?", (custom_val, chat_id))
                conn.commit()
                log_event(conn, chat_id, "custom_stage_set", by_user=user_name)
                await msg.reply_text(f"✨ *Кастомная стадия установлена:* «*{custom_val}*»!\nОна будет отображаться в вашей карточке серии.", parse_mode="Markdown")
                return True
        else:
            await msg.reply_text(
                "ℹ️ *Установка кастомной стадии:*\n"
                "Напишите `.streak custom <название>` (например, `.streak custom 👑 Лучшие друзья`)\n"
                "Или `.streak custom reset` для сброса.",
                parse_mode="Markdown",
            )
            return True

    text_content, keyboard = build_main_screen(s)

    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text_content,
            reply_markup=keyboard,
            business_connection_id=conn_id,
            parse_mode="Markdown",
        )
    except Exception as e:
        log.error(f"failed to send streak card: {e}")

    return True


# ─────────────────────── Обработчик инлайн-кнопок серий ───────────────────────

async def handle_series_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, conn: sqlite3.Connection) -> bool:
    query = update.callback_query
    if not query or not query.data or not query.data.startswith("strk:"):
        return False

    parts = query.data.split(":")
    if len(parts) < 3:
        return False

    action = parts[1]
    try:
        chat_id = int(parts[2])
    except ValueError:
        return False

    user_id = query.from_user.id if query.from_user else 0
    user_name = query.from_user.full_name if query.from_user else "Пользователь"

    s = get_series(conn, chat_id)
    if not s:
        s = ensure_series(conn, chat_id, getattr(query.message, "business_connection_id", None), user_name)

    is_owner = is_series_owner(conn, user_id, s)

    # 1. Навигация по экранам
    if action == "main":
        await query.answer()
        text_content, keyboard = build_main_screen(s)
        await query.edit_message_text(text=text_content, reply_markup=keyboard, parse_mode="Markdown")
        return True

    elif action == "stats":
        await query.answer()
        text_content, keyboard = build_stats_screen(s, conn)
        await query.edit_message_text(text=text_content, reply_markup=keyboard, parse_mode="Markdown")
        return True

    elif action == "stages":
        await query.answer()
        text_content, keyboard = build_stages_screen(s)
        await query.edit_message_text(text=text_content, reply_markup=keyboard, parse_mode="Markdown")
        return True

    elif action == "protect":
        await query.answer()
        text_content, keyboard = build_protect_screen(s, conn)
        await query.edit_message_text(text=text_content, reply_markup=keyboard, parse_mode="Markdown")
        return True

    elif action == "settings":
        await query.answer()
        text_content, keyboard = build_settings_screen(s)
        await query.edit_message_text(text=text_content, reply_markup=keyboard, parse_mode="Markdown")
        return True

    # 2. История и Топ
    elif action == "hist":
        await query.answer()
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM series_log WHERE chat_id = ? ORDER BY id DESC LIMIT 10", (chat_id,)).fetchall()
        lines = ["📜 *История событий серии:*", "━━━━━━━━━━━━━━━"]
        if not rows:
            lines.append("История пока пуста.")
        else:
            event_titles = {
                "upgrade": "⬆️ Повышение стадии",
                "manual_upgrade": "⬆️ Ручное повышение",
                "manual_downgrade": "⬇️ Ручное понижение",
                "stage_auto": "🔄 Возврат в авто-режим",
                "freeze": "❄️ Активирована заморозка",
                "freeze_applied": "❄️ Заморозка спасла серию",
                "revive": "✨ Серия воскрешена",
                "burned": "💀 Серия сгорела",
                "enabled": "🟢 Серия включена",
                "disabled": "🔴 Серия отключена",
                "custom_stage_set": "✨ Задана кастомная стадия",
            }
            for row in rows:
                r = dict(row)
                ev = event_titles.get(r["event"], f"• {r['event']}")
                dt = r["date"][:16].replace("T", " ")
                if r.get("from_stage") and r.get("to_stage"):
                    lines.append(f"{dt} | {ev}: {stage_name(r['from_stage'])} → {stage_name(r['to_stage'])}")
                else:
                    lines.append(f"{dt} | {ev} ({r.get('by_user') or 'бот'})")

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Назад в статистику", callback_data=f"strk:stats:{chat_id}")]
        ])
        await query.edit_message_text(text="\n".join(lines), reply_markup=keyboard, parse_mode="Markdown")
        return True

    elif action == "top":
        await query.answer()
        rows = get_all_active_series(conn)
        lines = ["🏆 *Топ активных серий общения:*", "━━━━━━━━━━━━━━━"]
        if not rows:
            lines.append("Нет активных серий.")
        else:
            for i, r in enumerate(rows[:10], 1):
                name = r.get("last_user_name") or f"Чат {r['chat_id']}"
                lines.append(f"{i}. *{name}* — 🔥 {r['current_streak']} {plural_days(r['current_streak'])} ({stage_name(r['stage'])})")

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Назад в статистику", callback_data=f"strk:stats:{chat_id}")]
        ])
        await query.edit_message_text(text="\n".join(lines), reply_markup=keyboard, parse_mode="Markdown")
        return True

    # 3. Действия защиты: Заморозка, Автозаморозка, Воскрешение
    elif action == "act_frz":
        if not is_owner:
            await query.answer("🔒 Заморозку серии может использовать только владелец аккаунта.", show_alert=True)
            return True

        if not s.get("enabled"):
            await query.answer("Серия в этом чате выключена.", show_alert=True)
            return True

        if s.get("frozen"):
            await query.answer("❄️ Серия уже защищена заморозкой на сегодня!", show_alert=True)
            return True

        if s.get("freezes_left", 0) <= 0:
            await query.answer("❄️ В этом месяце заморозки закончились.", show_alert=True)
            return True

        if s.get("current_streak", 0) <= 0:
            await query.answer("Стрик равен 0 — замораживать нечего.", show_alert=True)
            return True

        conn.execute("""
            UPDATE series SET frozen = 1, freezes_left = freezes_left - 1, last_date = ?
            WHERE chat_id = ?
        """, (today_str(), chat_id))
        conn.commit()
        log_event(conn, chat_id, "freeze", by_user=user_name)

        await query.answer("❄️ Серия успешно заморожена на сегодня!", show_alert=True)
        s = get_series(conn, chat_id)
        text_content, keyboard = build_protect_screen(s, conn)
        await query.edit_message_text(text=text_content, reply_markup=keyboard, parse_mode="Markdown")
        return True

    elif action == "tog_auto_frz":
        if not has_feature_access(conn, user_id, "auto_freeze"):
            await query.answer("🔒 Автозаморозка доступна по Премиум подписке!", show_alert=True)
            return True

        new_auto = 0 if s.get("auto_freeze", 0) else 1
        conn.execute("UPDATE series SET auto_freeze = ? WHERE chat_id = ?", (new_auto, chat_id))
        conn.commit()
        await query.answer(f"Автозаморозка в 23:30 {'включена ✅' if new_auto else 'выключена ❌'}")
        s = get_series(conn, chat_id)
        text_content, keyboard = build_protect_screen(s, conn)
        await query.edit_message_text(text=text_content, reply_markup=keyboard, parse_mode="Markdown")
        return True

    elif action == "act_rev":
        if not is_owner:
            await query.answer("🔒 Воскрешать серию может только владелец аккаунта.", show_alert=True)
            return True

        pre_burn = s.get("pre_burn_streak", 0)
        if s.get("current_streak", 0) > 0 and not pre_burn:
            await query.answer("✨ Серия активна и не сгорала.", show_alert=True)
            return True

        if pre_burn <= 0:
            await query.answer("💀 Нет сгоревшей серии для воскрешения.", show_alert=True)
            return True

        if s.get("revives_left", 0) <= 0:
            await query.answer("💫 Лимит воскрешений исчерпан.", show_alert=True)
            return True

        burned_date = s.get("burned_date")
        if burned_date:
            try:
                dt = datetime.fromisoformat(burned_date)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=_tz.utc)
                if (now() - dt).total_seconds() > 72 * 3600:
                    await query.answer("⏳ Прошло более 72 часов с момента сгорания. Срок истёк.", show_alert=True)
                    return True
            except Exception:
                pass

        restored = max(1, pre_burn)
        today = today_str()
        calc_stage = get_stage_by_days(restored) if s.get("auto_stage", 1) else s["stage"]

        conn.execute("""
            UPDATE series
            SET current_streak = ?, pre_burn_streak = 0, burned_date = NULL,
                revives_left = revives_left - 1, stage = ?, last_date = ?
            WHERE chat_id = ?
        """, (restored, calc_stage, today, chat_id))
        conn.commit()
        log_event(conn, chat_id, "revive", by_user=user_name)

        await query.answer(f"✨ Серия успешно воскрешена ({restored} дн.)!", show_alert=True)
        s = get_series(conn, chat_id)
        text_content, keyboard = build_protect_screen(s, conn)
        await query.edit_message_text(text=text_content, reply_markup=keyboard, parse_mode="Markdown")
        return True

    # 4. Действия со стадиями
    elif action == "stg_custom":
        if not has_feature_access(conn, user_id, "custom_stage"):
            await query.answer("🔒 Кастомная стадия доступна по Премиум подписке!", show_alert=True)
            return True

        if s.get("custom_stage"):
            conn.execute("UPDATE series SET custom_stage = NULL WHERE chat_id = ?", (chat_id,))
            conn.commit()
            await query.answer("🔄 Кастомная стадия сброшена к стандартной.")
        else:
            await query.answer(
                "✨ Чтобы задать кастомную стадию, отправьте в чат:\n.streak custom <название>\n(например, .streak custom 👑 Лучшие друзья)",
                show_alert=True,
            )
        s = get_series(conn, chat_id)
        text_content, keyboard = build_stages_screen(s)
        await query.edit_message_text(text=text_content, reply_markup=keyboard, parse_mode="Markdown")
        return True

    elif action == "tog_hide_stg":
        if not has_feature_access(conn, user_id, "hide_stage"):
            await query.answer("🔒 Скрытие стадии доступно по Премиум подписке!", show_alert=True)
            return True

        new_hide = 0 if s.get("hide_stage", 0) else 1
        conn.execute("UPDATE series SET hide_stage = ? WHERE chat_id = ?", (new_hide, chat_id))
        conn.commit()
        await query.answer(f"Скрытие стадии: {'ВКЛЮЧЕНО 🔒' if new_hide else 'ВЫКЛЮЧЕНО'}")
        s = get_series(conn, chat_id)
        text_content, keyboard = build_stages_screen(s)
        await query.edit_message_text(text=text_content, reply_markup=keyboard, parse_mode="Markdown")
        return True

    elif action == "stg_up":
        if not is_owner:
            await query.answer("🔒 Менять стадию вручную может только владелец.", show_alert=True)
            return True
        nxt = next_stage(s["stage"])
        if not nxt:
            await query.answer("👑 Это уже максимальная стадия!", show_alert=True)
            return True
        old = s["stage"]
        set_stage(conn, chat_id, nxt, auto=False)
        log_event(conn, chat_id, "manual_upgrade", old, nxt, user_name)
        await query.answer(f"🎉 Стадия повышена: {stage_name(nxt)}")
        s = get_series(conn, chat_id)
        text_content, keyboard = build_stages_screen(s)
        await query.edit_message_text(text=text_content, reply_markup=keyboard, parse_mode="Markdown")
        return True

    elif action == "stg_down":
        if not is_owner:
            await query.answer("🔒 Менять стадию вручную может только владелец.", show_alert=True)
            return True
        prev = prev_stage(s["stage"])
        if not prev:
            await query.answer("🌱 Это уже минимальная стадия!", show_alert=True)
            return True
        old = s["stage"]
        set_stage(conn, chat_id, prev, auto=False)
        log_event(conn, chat_id, "manual_downgrade", old, prev, user_name)
        await query.answer(f"📉 Стадия понижена: {stage_name(prev)}")
        s = get_series(conn, chat_id)
        text_content, keyboard = build_stages_screen(s)
        await query.edit_message_text(text=text_content, reply_markup=keyboard, parse_mode="Markdown")
        return True

    elif action == "stg_auto":
        if not is_owner:
            await query.answer("🔒 Менять режим стадии может только владелец.", show_alert=True)
            return True
        calc = get_stage_by_days(s.get("current_streak", 0))
        set_stage(conn, chat_id, calc, auto=True)
        log_event(conn, chat_id, "stage_auto", s["stage"], calc, user_name)
        await query.answer(f"🔄 Включён авто-расчёт: {stage_name(calc)}")
        s = get_series(conn, chat_id)
        text_content, keyboard = build_stages_screen(s)
        await query.edit_message_text(text=text_content, reply_markup=keyboard, parse_mode="Markdown")
        return True

    # 5. Действия настроек
    elif action == "tog_en":
        if not is_owner:
            await query.answer("🔒 Включать/выключать серию может только владелец.", show_alert=True)
            return True
        new_enabled = not bool(s.get("enabled", 0))
        set_series_enabled(conn, chat_id, new_enabled)
        log_event(conn, chat_id, "enabled" if new_enabled else "disabled", by_user=user_name)
        await query.answer(f"{'🟢 Серия включена' if new_enabled else '🔴 Серия выключена'}")
        s = get_series(conn, chat_id)
        text_content, keyboard = build_settings_screen(s)
        await query.edit_message_text(text=text_content, reply_markup=keyboard, parse_mode="Markdown")
        return True

    elif action == "cyc_mode":
        if not is_owner:
            await query.answer("🔒 Менять режим может только владелец.", show_alert=True)
            return True
        cur_mode = s.get("notify_mode", "milestones")
        modes = ["milestones", "all", "silent"]
        next_mode = modes[(modes.index(cur_mode) + 1) % len(modes)] if cur_mode in modes else "milestones"
        set_notify_mode(conn, chat_id, next_mode)
        await query.answer(f"Режим обновлён: {next_mode}")
        s = get_series(conn, chat_id)
        text_content, keyboard = build_settings_screen(s)
        await query.edit_message_text(text=text_content, reply_markup=keyboard, parse_mode="Markdown")
        return True

    elif action == "tog_rem":
        if not is_owner:
            await query.answer("🔒 Менять напоминания может только владелец.", show_alert=True)
            return True
        new_rem = 0 if s.get("reminders", 1) else 1
        conn.execute("UPDATE series SET reminders = ? WHERE chat_id = ?", (new_rem, chat_id))
        conn.commit()
        await query.answer(f"Напоминания {'включены ✅' if new_rem else 'выключены ❌'}")
        s = get_series(conn, chat_id)
        text_content, keyboard = build_settings_screen(s)
        await query.edit_message_text(text=text_content, reply_markup=keyboard, parse_mode="Markdown")
        return True

    return False