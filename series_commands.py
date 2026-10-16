# series_commands.py
"""Интерактивное управление серией общения через единую команду .streak с категориями инлайн-кнопок."""

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

log = logging.getLogger(__name__)

OWNER_ID = int(os.getenv("OWNER_ID", "8470104943"))


def _is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID


# ─────────────────────── Генерация экранов и клавиатур ───────────────────────

def build_main_screen(s: dict) -> Tuple[str, InlineKeyboardMarkup]:
    chat_id = s["chat_id"]
    streak = s.get("current_streak", 0)
    best = s.get("best_streak", 0)
    st = s.get("stage", "spark")
    is_enabled = bool(s.get("enabled", 0))

    if not is_enabled:
        status_line = "🔴 Выключена (серия не считается)"
    elif s.get("frozen"):
        status_line = "❄️ Защищена заморозкой"
    elif s.get("last_date") == today_str():
        status_line = "✅ Продлена сегодня"
    else:
        status_line = "⏳ Ожидает продления сегодня"

    cur_span, req_span, pct, nxt_key = get_progress(streak, st)
    bar = render_progress_bar(pct, length=8)

    lines = [
        f"🔥 *Серия общения: {streak} {plural_days(streak)}*",
        f"🏆 Личный рекорд: *{best} {plural_days(best)}*",
        f"🎭 Стадия: *{stage_name(st)}*",
        "",
        f"Прогресс: `{bar}` {int(pct)}%",
    ]

    if nxt_key:
        cfg = STAGE_CONFIG[nxt_key]
        remaining = req_span - cur_span
        lines.append(f"До «{cfg['title']} {cfg['icon']}»: ещё *{remaining} {plural_days(remaining)}*")
    else:
        lines.append("👑 Максимальный уровень отношений достигнут!")

    lines.append(f"\nСтатус: *{status_line}*")
    lines.append("\n_Выберите категорию управления кнопками ниже:_")

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

    return "\n".join(lines), InlineKeyboardMarkup(keyboard)


def build_stats_screen(s: dict) -> Tuple[str, InlineKeyboardMarkup]:
    chat_id = s["chat_id"]
    today = today_str()
    status_str = "Продлена ✅" if s.get("last_date") == today else "Не продлена ⏳"
    if s.get("frozen"):
        status_str = "Заморожена ❄️"

    lines = [
        "📊 *Подробная статистика серии*",
        "━━━━━━━━━━━━━━━",
        f"🔥 Текущая серия: *{s.get('current_streak', 0)} {plural_days(s.get('current_streak', 0))}*",
        f"🏆 Личный рекорд: *{s.get('best_streak', 0)} {plural_days(s.get('best_streak', 0))}*",
        f"🎭 Стадия: *{stage_name(s.get('stage', 'spark'))}*",
        f"📅 Всего дней общения: *{s.get('total_days', 0)}*",
        f"📌 Статус сегодня: *{status_str}*",
        "━━━━━━━━━━━━━━━",
        f"💫 Воскрешений в запасе: *{s.get('revives_left', 0)}/3*",
        f"❄️ Заморозок в запасе: *{s.get('freezes_left', 0)}/2*",
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
    lines.append(f"\nРежим стадии: *{mode_label}*")

    keyboard = [
        [
            InlineKeyboardButton("⬆️ Повысить", callback_data=f"strk:stg_up:{chat_id}"),
            InlineKeyboardButton("⬇️ Понизить", callback_data=f"strk:stg_down:{chat_id}"),
        ],
        [
            InlineKeyboardButton("🔄 Включить авто-расчёт", callback_data=f"strk:stg_auto:{chat_id}")
        ],
        [
            InlineKeyboardButton("⬅️ Назад в меню", callback_data=f"strk:main:{chat_id}")
        ],
    ]
    return "\n".join(lines), InlineKeyboardMarkup(keyboard)


def build_protect_screen(s: dict) -> Tuple[str, InlineKeyboardMarkup]:
    chat_id = s["chat_id"]
    frozen_status = "Активна на сегодня ❄️ (серия в безопасности)" if s.get("frozen") else "Не активна"
    streak = s.get("current_streak", 0)
    pre_burn = s.get("pre_burn_streak", 0)

    streak_state = f"Активен ({streak} дн.)" if streak > 0 else f"Сгорел (можно восстановить {pre_burn} дн.)" if pre_burn > 0 else "0 дней"

    lines = [
        "🛡 *Защита и Восстановление серии*",
        "━━━━━━━━━━━━━━━",
        f"❄️ Заморозка на сегодня: *{frozen_status}*",
        f"🔥 Состояние стрика: *{streak_state}*",
        f"💫 Доступно воскрешений: *{s.get('revives_left', 0)}/3*",
        f"❄️ Доступно заморозок: *{s.get('freezes_left', 0)}/2*",
        "━━━━━━━━━━━━━━━",
        "• *Заморозка* защищает серию от сгорания, если сегодня нет возможности написать.",
        "• *Воскрешение* восстанавливает сгоревший стрик в течение 72 часов.",
    ]

    keyboard = [
        [
            InlineKeyboardButton("❄️ Заморозить на сегодня", callback_data=f"strk:act_frz:{chat_id}"),
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
    Единственная входная команда: .streak (или /streak).
    Отправляет интерактивную карточку со всеми категориями управления.
    """
    msg = update.business_message or update.message
    if not msg or not msg.text:
        return False

    text = msg.text.strip().lower()
    # Принимаем только команду streak / серия
    if text not in (".streak", "/streak", ".серия", "/серия") and not text.startswith((".streak ", "/streak ")):
        return False

    chat_id = msg.chat_id
    conn_id = getattr(msg, "business_connection_id", None)
    sender = msg.from_user
    user_name = sender.full_name if sender else "Собеседник"

    s = ensure_series(conn, chat_id, conn_id, user_name)
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
    is_owner = _is_owner(user_id)

    s = get_series(conn, chat_id)
    if not s:
        s = ensure_series(conn, chat_id, getattr(query.message, "business_connection_id", None), user_name)

    # 1. Навигация по экранам
    if action == "main":
        await query.answer()
        text_content, keyboard = build_main_screen(s)
        await query.edit_message_text(text=text_content, reply_markup=keyboard, parse_mode="Markdown")
        return True

    elif action == "stats":
        await query.answer()
        text_content, keyboard = build_stats_screen(s)
        await query.edit_message_text(text=text_content, reply_markup=keyboard, parse_mode="Markdown")
        return True

    elif action == "stages":
        await query.answer()
        text_content, keyboard = build_stages_screen(s)
        await query.edit_message_text(text=text_content, reply_markup=keyboard, parse_mode="Markdown")
        return True

    elif action == "protect":
        await query.answer()
        text_content, keyboard = build_protect_screen(s)
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

    # 3. Действия защиты: Заморозка и Воскрешение
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
            await query.answer("❄️ В этом месяце заморозки закончились (лимит: 2/мес).", show_alert=True)
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
        text_content, keyboard = build_protect_screen(s)
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
            await query.answer("💫 Лимит воскрешений исчерпан (3 в месяц).", show_alert=True)
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
        text_content, keyboard = build_protect_screen(s)
        await query.edit_message_text(text=text_content, reply_markup=keyboard, parse_mode="Markdown")
        return True

    # 4. Действия со стадиями
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