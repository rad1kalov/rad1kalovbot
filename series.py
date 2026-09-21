# series.py
"""Логика серии: обновление, сгорание, сводка, воскрешение."""

import sqlite3
from datetime import date, timedelta

from series_db import ensure_series, get_series, log_event
from stages import stage_name


def _today() -> str:
    return date.today().isoformat()


def _yesterday() -> str:
    return (date.today() - timedelta(days=1)).isoformat()


def plural_days(n: int) -> str:
    n = abs(int(n)) % 100
    if 11 <= n <= 19:
        return "дней"
    n = n % 10
    if n == 1:
        return "день"
    if 2 <= n <= 4:
        return "дня"
    return "дней"


def process_message(conn: sqlite3.Connection, chat_id: int,
                    conn_id: str | None, user_name: str):
    """
    Вызывается на каждое не-командное бизнес-сообщение.
    Возвращает dict с данными для сводки или None, если сегодня уже обрабатывали.
    """
    s = ensure_series(conn, chat_id, conn_id, user_name)
    today = _today()
    yesterday = _yesterday()

    if s["last_date"] == today:
        return None  # уже продлевали сегодня

    was_burned = False

    if s["last_date"] is None:
        new_streak = 1
    elif s["last_date"] == yesterday:
        new_streak = s["current_streak"] + 1
    else:
        # серия была сброшена (воркером или ранее)
        if s["current_streak"] > 0:
            was_burned = True
            conn.execute(
                "UPDATE series SET pre_burn_streak = ? WHERE chat_id = ?",
                (s["current_streak"], chat_id),
            )
        elif s["pre_burn_streak"] > 0:
            was_burned = True
        new_streak = 1

    new_record = new_streak > s["best_streak"]
    new_best = max(s["best_streak"], new_streak)

    conn.execute("""
        UPDATE series
        SET current_streak = ?, best_streak = ?, last_date = ?,
            total_days = total_days + 1, announced_today = 1, last_user_name = ?
        WHERE chat_id = ?
    """, (new_streak, new_best, today, user_name, chat_id))
    conn.commit()

    return {
        "streak": new_streak,
        "best": new_best,
        "new_record": new_record,
        "was_burned": was_burned,
    }


def build_summary(conn: sqlite3.Connection, chat_id: int, info: dict) -> str:
    s = get_series(conn, chat_id)
    lines = []
    if info["was_burned"]:
        lines.append("💀 Прошлая серия сгорела. Начинаем заново!")
    if info["new_record"] and info["streak"] > 1:
        lines.append("🎉 Новый рекорд!")
    lines.append(f"🔥 Серия: {info['streak']} {plural_days(info['streak'])}")
    lines.append(f"🏆 Рекорд: {info['best']} {plural_days(info['best'])}")
    lines.append(f"🎭 Стадия: {stage_name(s['stage'])}")
    return "\n".join(lines)


def burn_stale_streaks(conn: sqlite3.Connection) -> None:
    """Вызывается воркером в полночь: сжигает непродлённые серии и сбрасывает дневные флаги."""
    conn.row_factory = sqlite3.Row
    today = _today()
    yesterday = _yesterday()
    current_month = today[:7]  # YYYY-MM

    rows = conn.execute("SELECT * FROM series").fetchall()
    for row in rows:
        r = dict(row)
        # Сжигаем, если последнее сообщение было раньше вчера и серия ещё жива
        if r["last_date"] and r["last_date"] < yesterday and r["current_streak"] > 0:
            conn.execute(
                "UPDATE series SET pre_burn_streak = ?, current_streak = 0 WHERE chat_id = ?",
                (r["current_streak"], r["chat_id"]),
            )
            log_event(conn, r["chat_id"], "burned", by_user="auto")

        # Сброс дневных флагов
        conn.execute(
            "UPDATE series SET announced_today = 0, frozen = 0 WHERE chat_id = ?",
            (r["chat_id"],),
        )

        # Сброс месячных лимитов
        if r["month_tracker"] != current_month:
            conn.execute(
                "UPDATE series SET revives_left = 3, freezes_left = 1, month_tracker = ? "
                "WHERE chat_id = ?",
                (current_month, r["chat_id"]),
            )
    conn.commit()