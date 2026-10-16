# series_commands.py
"""Обработчик команд серии общения (.streak, .stage, .freeze, .revive и др.)."""

import logging
import os
import sqlite3
from datetime import datetime, timezone as _tz

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

# ID владельца (по умолчанию из окружения или константа)
OWNER_ID = int(os.getenv("OWNER_ID", "8470104943"))

KNOWN_COMMANDS = {
    "streak", "серия",
    "stage", "стадия",
    "upgrade", "downgrade",
    "revive", "воскресить",
    "revives",
    "remind",
    "freeze", "заморозка",
}


def _is_owner(from_user_id: int) -> bool:
    return from_user_id == OWNER_ID


async def handle_series_command(update, context, conn: sqlite3.Connection) -> bool:
    """
    Обрабатывает команды серий общения, начинающиеся с точки.
    Возвращает True, если команда обработана, иначе False.
    """
    msg = update.business_message
    if not msg or not msg.text:
        return False

    text = msg.text.strip()
    if not text.startswith("."):
        return False

    parts = text[1:].split()
    if not parts:
        return False

    cmd = parts[0].lower()
    args = parts[1:]

    # Синонимы команд на русском
    alias_map = {
        "серия": "streak",
        "стадия": "stage",
        "воскресить": "revive",
        "заморозка": "freeze",
    }
    cmd = alias_map.get(cmd, cmd)

    if cmd not in KNOWN_COMMANDS:
        return False

    chat_id = msg.chat_id
    conn_id = msg.business_connection_id
    sender = msg.from_user
    user_name = sender.full_name if sender else "Собеседник"
    user_id = sender.id if sender else 0
    is_owner_user = _is_owner(user_id)

    s = ensure_series(conn, chat_id, conn_id, user_name)

    async def send(payload: str):
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=payload,
                business_connection_id=conn_id,
                parse_mode="Markdown",
            )
        except Exception as e:
            log.error(f"series command send failed: {e}")

    # ─────────────────────────── .streak ───────────────────────────
    if cmd == "streak":
        sub = args[0].lower() if args else ""

        # .streak on
        if sub in ("on", "вкл", "старт", "start"):
            if not is_owner_user:
                await send("🔒 Включать и настраивать серию может только владелец аккаунта.")
                return True
            set_series_enabled(conn, chat_id, True)
            log_event(conn, chat_id, "enabled", by_user=user_name)
            await send(
                "✨ *Серия общения в этом чате активирована!*\n\n"
                "• Теперь каждый день вашего общения будет приближать вас к новым ступеням отношений.\n"
                "• Режим уведомлений по умолчанию: *юбилейные даты* (не спамит).\n"
                "• Напишите `.streak`, чтобы проверить статус, или `.streak help` для справки."
            )
            return True

        # .streak off
        elif sub in ("off", "выкл", "стоп", "stop"):
            if not is_owner_user:
                await send("🔒 Отключать серию может только владелец аккаунта.")
                return True
            set_series_enabled(conn, chat_id, False)
            log_event(conn, chat_id, "disabled", by_user=user_name)
            await send("💤 *Отслеживание серии в этом чате приостановлено.*\nЧтобы включить снова: `.streak on`.")
            return True

        # Если серия не включена в этом чате
        if not s.get("enabled"):
            await send(
                "💬 *Серия общения в этом чате ещё не включена.*\n\n"
                "Чтобы начать считать дни непрерывного общения и открывать уровни отношений, "
                "отправьте:\n`.streak on`"
            )
            return True

        # .streak mode <milestones|all|silent>
        if sub in ("mode", "режим"):
            if not is_owner_user:
                await send("🔒 Менять режим уведомлений может только владелец аккаунта.")
                return True
            mode_arg = args[1].lower() if len(args) > 1 else ""
            modes_map = {
                "milestones": "milestones",
                "юбилеи": "milestones",
                "all": "all",
                "все": "all",
                "silent": "silent",
                "тихий": "silent",
            }
            target_mode = modes_map.get(mode_arg)
            if not target_mode:
                await send(
                    "⚙️ *Режимы уведомлений серии:*\n\n"
                    "• `.streak mode milestones` — уведомлять только в юбилейные дни и при смене стадии (рекомендуется)\n"
                    "• `.streak mode all` — отправлять сводку каждый день\n"
                    "• `.streak mode silent` — тихий режим (считать стрик, но не присылать сообщений в чат)"
                )
                return True

            set_notify_mode(conn, chat_id, target_mode)
            mode_titles = {
                "milestones": "Юбилейные даты и новые стадии 🌟",
                "all": "Ежедневно 📅",
                "silent": "Тихий режим (без спама) 🤫",
            }
            await send(f"✅ Режим уведомлений обновлён: *{mode_titles[target_mode]}*")
            return True

        # .streak stats
        elif sub in ("stats", "статистика"):
            await send(_stats_text(s))
            return True

        # .streak history
        elif sub in ("history", "история"):
            await send(_history_text(conn, chat_id))
            return True

        # .streak top / .streak all
        elif sub in ("top", "топ", "all", "все"):
            await send(_all_text(conn))
            return True

        # .streak help
        elif sub in ("help", "помощь"):
            await send(_help_text())
            return True

        # Обычный .streak (карточка серии)
        else:
            await send(_streak_card_text(s))
            return True

    # ─────────────────────────── .stage ───────────────────────────
    elif cmd == "stage":
        if not s.get("enabled"):
            await send("Серия в этом чате выключена. Включите командой `.streak on`.")
            return True

        sub = args[0].lower() if args else ""
        if sub in ("auto", "авто"):
            if not is_owner_user:
                await send("🔒 Менять режим стадий может только владелец.")
                return True
            calc = get_stage_by_days(s["current_streak"])
            set_stage(conn, chat_id, calc, auto=True)
            log_event(conn, chat_id, "stage_auto", s["stage"], calc, user_name)
            await send(f"🔄 Включён автоматический расчёт стадии по дням стрика. Текущая стадия: *{stage_name(calc)}*")
            return True

        await send(_stage_overview_text(s))
        return True

    # ─────────────────────────── .upgrade ───────────────────────────
    elif cmd == "upgrade":
        if not is_owner_user:
            await send("🔒 Повышать стадию вручную может только владелец аккаунта.")
            return True
        nxt = next_stage(s["stage"])
        if not nxt:
            await send(f"👑 Достигнута максимальная стадия: *{stage_name(s['stage'])}*!")
        else:
            old = s["stage"]
            set_stage(conn, chat_id, nxt, auto=False)
            log_event(conn, chat_id, "manual_upgrade", old, nxt, user_name)
            cfg = stage_info(nxt)
            await send(
                f"🎉 *Стадия успешно повышена!*\n\n"
                f"Было: {stage_name(old)}\n"
                f"Стало: *{cfg['title']} {cfg['icon']}*\n"
                f"_{cfg['description']}_\n\n"
                f"_(Ручной режим активен. Чтобы вернуть авто-подсчёт: `.stage auto`)_"
            )
        return True

    # ─────────────────────────── .downgrade ───────────────────────────
    elif cmd == "downgrade":
        if not is_owner_user:
            await send("🔒 Понижать стадию вручную может только владелец аккаунта.")
            return True
        prev = prev_stage(s["stage"])
        if not prev:
            await send(f"🌱 Стадия уже минимальная: *{stage_name(s['stage'])}*")
        else:
            old = s["stage"]
            set_stage(conn, chat_id, prev, auto=False)
            log_event(conn, chat_id, "manual_downgrade", old, prev, user_name)
            await send(
                f"📉 *Стадия понижена.*\n\n"
                f"Было: {stage_name(old)}\n"
                f"Стало: *{stage_name(prev)}*\n"
                f"_(Ручной режим активен. Чтобы вернуть авто-подсчёт: `.stage auto`)_"
            )
        return True

    # ─────────────────────────── .freeze ───────────────────────────
    elif cmd == "freeze":
        if not is_owner_user:
            await send("🔒 Использовать заморозку серии может только владелец аккаунта.")
            return True

        if not s.get("enabled"):
            await send("Серия в этом чате не активна. Напишите `.streak on`.")
            return True

        if s["frozen"]:
            await send("❄️ *Серия уже защищена заморозкой на сегодня!* Она не сгорит.")
            return True

        if s.get("freezes_left", 0) <= 0:
            await send("❄️ В этом месяце заморозки закончились (лимит: 2 в месяц). Сброс лимитов 1-го числа.")
            return True

        if s["current_streak"] <= 0:
            await send("Серия пока равна 0 дней — замораживать нечего.")
            return True

        conn.execute("""
            UPDATE series 
            SET frozen = 1, freezes_left = freezes_left - 1, last_date = ?
            WHERE chat_id = ?
        """, (today_str(), chat_id))
        conn.commit()
        log_event(conn, chat_id, "freeze", by_user=user_name)

        new_s = get_series(conn, chat_id)
        await send(
            "❄️ *Серия успешно заморожена!*\n\n"
            f"• Ваш стрик (*{s['current_streak']} {plural_days(s['current_streak'])}*) в безопасности и не сгорит сегодня.\n"
            f"• Осталось заморозок в этом месяце: *{new_s['freezes_left']}/2*."
        )
        return True

    # ─────────────────────────── .revive ───────────────────────────
    elif cmd == "revive":
        if not is_owner_user:
            await send("🔒 Воскрешать серию может только владелец аккаунта.")
            return True

        if s["current_streak"] > 0 and not s.get("pre_burn_streak"):
            await send("✨ Ваша серия активна и не сгорала. Воскрешать нечего!")
            return True

        pre_burn = s.get("pre_burn_streak", 0)
        if pre_burn <= 0:
            await send("💀 Нет сгоревшей серии для воскрешения.")
            return True

        if s.get("revives_left", 0) <= 0:
            await send("💫 Доступные воскрешения на этот месяц исчерпаны (лимит: 3 в месяц). Сброс 1-го числа.")
            return True

        # Проверка срока сгорания (72 часа)
        burned_date = s.get("burned_date")
        if burned_date:
            try:
                dt = datetime.fromisoformat(burned_date)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=_tz.utc)
                diff_hours = (now() - dt).total_seconds() / 3600
                if diff_hours > 72:
                    await send(f"⏳ С момента сгорания серии прошло более 72 часов ({int(diff_hours)} ч.). Срок для воскрешения истёк.")
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

        new_s = get_series(conn, chat_id)
        await send(
            "✨ *Серия успешно воскрешена!*\n\n"
            f"🔥 Восстановлено: *{restored} {plural_days(restored)}*\n"
            f"🎭 Стадия: *{stage_name(calc_stage)}*\n"
            f"💫 Доступно воскрешений в этом месяце: *{new_s['revives_left']}/3*\n\n"
            "Продолжайте общаться, чтобы серия не прерывалась!"
        )
        return True

    # ─────────────────────────── .revives ───────────────────────────
    elif cmd == "revives":
        await send(
            f"💫 Доступно воскрешений в этом месяце: *{s.get('revives_left', 0)}/3*\n"
            f"❄️ Доступно заморозок в этом месяце: *{s.get('freezes_left', 0)}/2*\n"
            f"📅 Сброс лимитов: *1-го числа следующего месяца*."
        )
        return True

    # ─────────────────────────── .remind ───────────────────────────
    elif cmd == "remind":
        if not is_owner_user:
            await send("🔒 Менять настройки напоминаний может только владелец аккаунта.")
            return True

        arg = args[0].lower() if args else ""
        if arg in ("on", "1", "вкл"):
            new_val = 1
        elif arg in ("off", "0", "выкл"):
            new_val = 0
        else:
            new_val = 0 if s.get("reminders") else 1

        conn.execute("UPDATE series SET reminders = ? WHERE chat_id = ?", (new_val, chat_id))
        conn.commit()
        status_text = "включены ✅ (в 21:00)" if new_val else "выключены ❌"
        await send(f"🔔 Вечерние напоминания о риске сгорания серии теперь *{status_text}*.")
        return True

    return False


# ─────────────────────── Форматирование сообщений ───────────────────────

def _streak_card_text(s: dict) -> str:
    today = today_str()
    streak = s.get("current_streak", 0)
    best = s.get("best_streak", 0)
    st = s.get("stage", "spark")

    cur_span, req_span, pct, nxt_key = get_progress(streak, st)
    bar = render_progress_bar(pct, length=8)

    status_line = "✅ Серия продлена сегодня" if s.get("last_date") == today else "⏳ Ожидает продления сегодня"
    if s.get("frozen"):
        status_line = "❄️ Серия защищена заморозкой"

    lines = [
        f"🔥 *Серия общения: {streak} {plural_days(streak)}*",
        f"🏆 Рекорд: *{best} {plural_days(best)}*",
        f"🎭 Стадия: *{stage_name(st)}*",
        "",
        f"Прогресс: `{bar}` {int(pct)}%",
    ]

    if nxt_key:
        nxt_cfg = STAGE_CONFIG[nxt_key]
        remaining = req_span - cur_span
        lines.append(f"До стадии «{nxt_cfg['title']} {nxt_cfg['icon']}»: *{remaining} {plural_days(remaining)}*")
    else:
        lines.append("👑 Максимальный уровень отношений достигнут!")

    lines.append("")
    lines.append(f"Статус: {status_line}")
    lines.append("_(Для подробностей: `.streak stats` | команды: `.streak help`)_")

    return "\n".join(lines)


def _stage_overview_text(s: dict) -> str:
    current_key = s.get("stage", "spark")
    streak = s.get("current_streak", 0)
    cur_span, req_span, pct, nxt_key = get_progress(streak, current_key)
    bar = render_progress_bar(pct, length=10)

    lines = [
        f"🎭 *Текущая стадия: {stage_name(current_key)}*",
        f"_{stage_info(current_key)['description']}_",
        "",
        f"Прогресс уровня: `{bar}` {int(pct)}%",
    ]

    if nxt_key:
        cfg = STAGE_CONFIG[nxt_key]
        remaining = req_span - cur_span
        lines.append(f"Следующая: *{cfg['title']} {cfg['icon']}* (ещё {remaining} {plural_days(remaining)})")
    else:
        lines.append("👑 Максимальная вершина достигнута!")

    lines.append("\n📜 *Лестница стадий:*")
    for key in STAGES:
        cfg = STAGE_CONFIG[key]
        marker = "👉 " if key == current_key else "• "
        days_label = f"({cfg['min_days']}+ дн.)"
        lines.append(f"{marker}{cfg['title']} {cfg['icon']} {days_label}")

    mode_label = "автоматический (по дням)" if s.get("auto_stage", 1) else "ручной"
    lines.append(f"\nРежим стадии: *{mode_label}* (сбросить на авто: `.stage auto`)")

    return "\n".join(lines)


def _stats_text(s: dict) -> str:
    mode_names = {
        "milestones": "Юбилеи и стадии (без спама)",
        "all": "Каждый день",
        "silent": "Тихий",
    }
    mode = mode_names.get(s.get("notify_mode", "milestones"), s.get("notify_mode", "milestones"))
    today = today_str()
    status_str = "Продлена ✅" if s.get("last_date") == today else "Не продлена ⏳"
    if s.get("frozen"):
        status_str = "Заморожена ❄️"

    lines = [
        "📊 *Подробная статистика серии*",
        "━━━━━━━━━━━━━━━",
        f"🔥 Текущая серия: *{s['current_streak']} {plural_days(s['current_streak'])}*",
        f"🏆 Личный рекорд: *{s['best_streak']} {plural_days(s['best_streak'])}*",
        f"🎭 Стадия: *{stage_name(s['stage'])}*",
        f"📅 Всего дней общения: *{s.get('total_days', 0)}*",
        f"📌 Статус сегодня: *{status_str}*",
        "━━━━━━━━━━━━━━━",
        f"💫 Воскрешений в запасе: *{s.get('revives_left', 0)}/3*",
        f"❄️ Заморозок в запасе: *{s.get('freezes_left', 0)}/2*",
        f"🔔 Напоминания в 21:00: *{'вкл ✅' if s.get('reminders') else 'выкл ❌'}*",
        f"💬 Режим уведомлений: *{mode}*",
    ]
    return "\n".join(lines)


def _history_text(conn: sqlite3.Connection, chat_id: int) -> str:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM series_log WHERE chat_id = ? ORDER BY id DESC LIMIT 10",
        (chat_id,),
    ).fetchall()
    if not rows:
        return "📜 История событий этой серии пока пуста."

    event_titles = {
        "upgrade": "⬆️ Повышение стадии",
        "manual_upgrade": "⬆️ Ручное повышение",
        "manual_downgrade": "⬇️ Ручное понижение",
        "stage_auto": "🔄 Возврат в авто-режим",
        "freeze": "❄️ Активирована заморозка",
        "freeze_applied": "❄️ Заморозка защитила серию",
        "revive": "✨ Серия воскрешена",
        "burned": "💀 Серия сгорела",
        "enabled": "🟢 Серия включена",
        "disabled": "🔴 Серия отключена",
    }

    lines = ["📜 *История событий серии:*", "━━━━━━━━━━━━━━━"]
    for row in rows:
        r = dict(row)
        ev = event_titles.get(r["event"], f"• {r['event']}")
        dt = r["date"][:16].replace("T", " ")
        if r.get("from_stage") and r.get("to_stage"):
            lines.append(f"{dt} | {ev}: {stage_name(r['from_stage'])} → {stage_name(r['to_stage'])}")
        else:
            lines.append(f"{dt} | {ev} (пользователь: {r.get('by_user') or 'бот'})")

    return "\n".join(lines)


def _all_text(conn: sqlite3.Connection) -> str:
    rows = get_all_active_series(conn)
    if not rows:
        return "🔥 Нет активных серий общения."

    lines = ["🏆 *Топ активных серий общения:*", "━━━━━━━━━━━━━━━"]
    for i, r in enumerate(rows, 1):
        name = r.get("last_user_name") or f"Чат {r['chat_id']}"
        lines.append(
            f"{i}. *{name}* — 🔥 {r['current_streak']} {plural_days(r['current_streak'])} "
            f"({stage_name(r['stage'])})"
        )
    return "\n".join(lines)


def _help_text() -> str:
    return (
        "📖 *Команды управления сериями общения:*\n\n"
        "• `.streak` — карточка серии и прогресс уровня\n"
        "• `.streak on` / `.streak off` — включить / выключить серию в этом чате\n"
        "• `.streak mode <milestones|all|silent>` — настроить частоту уведомлений\n"
        "• `.streak stats` — полная статистика, лимиты и параметры\n"
        "• `.streak top` — таблица лидеров по сериям\n"
        "• `.streak history` — история событий серии\n\n"
        "🎭 *Стадии:*\n"
        "• `.stage` — инфо о текущей стадии и лестница уровней\n"
        "• `.stage auto` — включить автоматический расчёт по дням\n"
        "• `.upgrade` / `.downgrade` — ручное изменение уровня\n\n"
        "🛡 *Защита и восстановление:*\n"
        "• `.freeze` — заморозить серию на сегодня (до 2 раз в месяц)\n"
        "• `.revive` — воскресить сгоревшую серию (до 3 раз в месяц, в течение 72 ч.)\n"
        "• `.revives` — проверить доступные воскрешения и заморозки\n"
        "• `.remind on/off` — вечерние напоминания в 21:00"
    )