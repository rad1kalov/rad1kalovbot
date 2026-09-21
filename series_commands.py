# series_commands.py
"""Обработчик команд серии (.streak, .stage, .upgrade и т.д.)."""

import logging
import sqlite3

from series import plural_days
from series_db import ensure_series, get_series, log_event
from stages import stage_name, next_stage, prev_stage
from timezone import today_str

KNOWN_COMMANDS = {
    "streak", "stage", "upgrade", "downgrade",
    "revive", "revives", "remind", "freeze",
}


async def handle_series_command(update, context,
                                conn: sqlite3.Connection) -> bool:
    """
    Возвращает True, если команда распознана и обработана.
    Иначе — False (сообщение пойдёт дальше по логике).
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

    if cmd not in KNOWN_COMMANDS:
        return False

    chat_id = msg.chat_id
    conn_id = msg.business_connection_id
    user_name = msg.from_user.full_name if msg.from_user else "Кто-то"

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
            logging.error(f"series command send failed: {e}")

    # ─────── .streak ───────
    if cmd == "streak":
        if args and args[0].lower() == "stats":
            await send(_stats_text(s))
        elif args and args[0].lower() == "history":
            await send(_history_text(conn, chat_id))
        elif args and args[0].lower() == "all":
            await send(_all_text(conn))
        else:
            await send(_streak_text(s))

    # ─────── .stage ───────
    elif cmd == "stage":
        nxt = next_stage(s["stage"])
        lines = [f"🎭 Стадия: *{stage_name(s['stage'])}*"]
        if nxt:
            lines.append(f"Следующая: {stage_name(nxt)}")
            lines.append("Используй `.upgrade` чтобы повысить.")
        else:
            lines.append("Это максимальная стадия.")
        await send("\n".join(lines))

    # ─────── .upgrade ───────
    elif cmd == "upgrade":
        nxt = next_stage(s["stage"])
        if not nxt:
            await send(f"🎭 Стадия уже максимальная: *{stage_name(s['stage'])}*")
        else:
            old = s["stage"]
            conn.execute("UPDATE series SET stage = ? WHERE chat_id = ?", (nxt, chat_id))
            conn.commit()
            log_event(conn, chat_id, "upgrade", old, nxt, user_name)
            await send(
                f"🎉 *Стадия повышена!*\n"
                f"Было: {stage_name(old)}\n"
                f"Стало: *{stage_name(nxt)}*\n"
                f"Поздравляю!"
            )

    # ─────── .downgrade ───────
    elif cmd == "downgrade":
        prev = prev_stage(s["stage"])
        if not prev:
            await send(f"🎭 Стадия уже минимальная: *{stage_name(s['stage'])}*")
        else:
            old = s["stage"]
            conn.execute("UPDATE series SET stage = ? WHERE chat_id = ?", (prev, chat_id))
            conn.commit()
            log_event(conn, chat_id, "downgrade", old, prev, user_name)
            await send(
                f"💔 *Стадия понижена.*\n"
                f"Было: {stage_name(old)}\n"
                f"Стало: *{stage_name(prev)}*"
            )

    # ─────── .revive ───────
    elif cmd == "revive":
        if s["current_streak"] > 0:
            await send("✨ Серия и так жива, воскрешать нечего.")
        elif s["pre_burn_streak"] <= 0:
            await send("💀 Нет сгоревшей серии для воскрешения.")
        elif s["revives_left"] <= 0:
            await send("💫 Воскрешения закончились. Следующий сброс — 1 числа.")
        else:
            restored = max(1, s["pre_burn_streak"] - 1)
            conn.execute(
                "UPDATE series SET current_streak = ?, "
                "revives_left = revives_left - 1, pre_burn_streak = 0 "
                "WHERE chat_id = ?",
                (restored, chat_id),
            )
            conn.commit()
            log_event(conn, chat_id, "revive", by_user=user_name)
            new_s = get_series(conn, chat_id)
            await send(
                f"✨ *Серия воскрешена!*\n"
                f"🔥 Восстановлено дней: {restored}\n"
                f"💫 Осталось воскрешений в этом месяце: {new_s['revives_left']}/3"
            )

    # ─────── .revives ───────
    elif cmd == "revives":
        await send(
            f"💫 Воскрешения в этом месяце: *{s['revives_left']}/3*\n"
            f"Следующий сброс: 1 числа."
        )

    # ─────── .remind ───────
    elif cmd == "remind":
        new_val = 0 if s["reminders"] else 1
        conn.execute("UPDATE series SET reminders = ? WHERE chat_id = ?", (new_val, chat_id))
        conn.commit()
        status = "включены ✅" if new_val else "выключены ❌"
        await send(f"🔔 Напоминания о скором сгорании серии {status}.")

    # ─────── .freeze ───────
    # ─────── .freeze ───────
    elif cmd == "freeze":
        today = today_str()
        if s["frozen"]:
            await send("❄️ Серия уже заморожена на сегодня.")
        elif s["freezes_left"] <= 0:
            await send("❄️ Заморозки закончились. Следующий сброс — 1 числа.")
        else:
            conn.execute(
                "UPDATE series SET frozen = 1, freezes_left = freezes_left - 1, "
                "last_date = ? WHERE chat_id = ?",
                (today, chat_id),
            )
            conn.commit()
            new_s = get_series(conn, chat_id)
            await send(
                f"❄️ *Серия заморожена на сегодня!*\n"
                f"Осталось заморозок в этом месяце: {new_s['freezes_left']}/1"
            )

    return True


# ─────────────────────── Форматирование ───────────────────────

def _streak_text(s: dict) -> str:
    return "\n".join([
        f"🔥 Серия: *{s['current_streak']}* {plural_days(s['current_streak'])}",
        f"🏆 Рекорд: *{s['best_streak']}* {plural_days(s['best_streak'])}",
        f"🎭 Стадия: *{stage_name(s['stage'])}*",
        f"📊 Всего дней общения: {s['total_days']}",
    ])


def _stats_text(s: dict) -> str:
    return "\n".join([
        "📊 *Статистика серии*",
        f"🔥 Текущая: {s['current_streak']} {plural_days(s['current_streak'])}",
        f"🏆 Рекорд: {s['best_streak']} {plural_days(s['best_streak'])}",
        f"🎭 Стадия: {stage_name(s['stage'])}",
        f"📅 Всего дней общения: {s['total_days']}",
        f"💫 Воскрешений: {s['revives_left']}/3",
        f"❄️ Заморозок: {s['freezes_left']}/1",
        f"🔔 Напоминания: {'вкл' if s['reminders'] else 'выкл'}",
    ])


def _history_text(conn: sqlite3.Connection, chat_id: int) -> str:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM series_log WHERE chat_id = ? ORDER BY id DESC LIMIT 10",
        (chat_id,),
    ).fetchall()
    if not rows:
        return "📜 История пуста."
    lines = ["📜 *Последние события:*"]
    for row in rows:
        r = dict(row)
        e, by, d = r["event"], r["by_user"] or "—", r["date"]
        if e == "upgrade":
            lines.append(f"⬆️ {d} — {by}: {stage_name(r['from_stage'])} → {stage_name(r['to_stage'])}")
        elif e == "downgrade":
            lines.append(f"⬇️ {d} — {by}: {stage_name(r['from_stage'])} → {stage_name(r['to_stage'])}")
        elif e == "revive":
            lines.append(f"✨ {d} — {by}: серия воскрешена")
        elif e == "burned":
            lines.append(f"💀 {d} — серия сгорела")
        else:
            lines.append(f"• {d} — {e}")
    return "\n".join(lines)


def _all_text(conn: sqlite3.Connection) -> str:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM series ORDER BY current_streak DESC LIMIT 10"
    ).fetchall()
    if not rows:
        return "Нет сохранённых серий."
    lines = ["🔥 *Топ серий:*"]
    for i, row in enumerate(rows, 1):
        r = dict(row)
        name = r["last_user_name"] or f"чат {r['chat_id']}"
        lines.append(
            f"{i}. {name} — {r['current_streak']} "
            f"{plural_days(r['current_streak'])} ({stage_name(r['stage'])})"
        )
    return "\n".join(lines)