# series.py
"""Логика серий общения: подсчёт стриков, авто-повышение стадий, заморозка и сводки."""

import sqlite3
from typing import Optional, Dict, Any

from series_db import ensure_series, get_series, log_event
from stages import (
    stage_name,
    get_stage_by_days,
    stage_info,
    get_progress,
    render_progress_bar,
    normalize_stage,
)
from timezone import today_str as _today, yesterday_str as _yesterday, now_iso

MILESTONES = {3, 7, 10, 14, 21, 30, 50, 75, 100, 150, 200, 250, 300, 365}


def is_milestone(days: int) -> bool:
    """Проверяет, является ли количество дней юбилейной отметкой."""
    if days in MILESTONES:
        return True
    return days > 365 and (days % 100 == 0 or days % 365 == 0)


def plural_days(n: int) -> str:
    """Склонение слова 'день' для русского языка."""
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
                    conn_id: Optional[str], user_name: str) -> Optional[Dict[str, Any]]:
    """
    Вызывается при входящем сообщении в бизнес-чате.
    Возвращает dict с информацией о событии или None, если серия неактивна
    или уже продлевалась сегодня.
    """
    s = ensure_series(conn, chat_id, conn_id, user_name)

    # Не спамим в чаты, где серия не была включена пользователем
    if not s.get("enabled"):
        return None

    today = _today()
    yesterday = _yesterday()

    # Уже зачли общение сегодня
    if s["last_date"] == today:
        return None

    was_burned = False
    pre_burn = s.get("pre_burn_streak", 0)
    was_frozen = bool(s.get("frozen", 0))

    if s["last_date"] is None:
        # Первое продление после старта
        new_streak = 1
    elif was_frozen:
        # Серия была заморожена — спасена и продолжается
        new_streak = s["current_streak"] + 1 if s["current_streak"] > 0 else 1
    elif s["last_date"] == yesterday:
        # Обычный непрерывный день общения
        new_streak = s["current_streak"] + 1
    else:
        # Пропущено более одного дня без заморозки
        if s["current_streak"] > 0:
            pre_burn = s["current_streak"]
            was_burned = True
            conn.execute(
                "UPDATE series SET pre_burn_streak = ?, burned_date = ? WHERE chat_id = ?",
                (pre_burn, now_iso(), chat_id),
            )
            log_event(conn, chat_id, "burned", by_user=user_name)
        elif s.get("pre_burn_streak", 0) > 0:
            was_burned = True
        new_streak = 1

    new_record = new_streak > s.get("best_streak", 0)
    new_best = max(s.get("best_streak", 0), new_streak)

    # Автоматическое определение стадии по дням стрика
    current_stage = s.get("stage") or "spark"
    auto_stage = s.get("auto_stage", 1)
    stage_up = False
    new_stage = current_stage

    if auto_stage:
        calc_stage = get_stage_by_days(new_streak)
        if calc_stage != current_stage:
            stage_up = True
            new_stage = calc_stage
            log_event(conn, chat_id, "upgrade", current_stage, new_stage, user_name)

    conn.execute("""
        UPDATE series
        SET current_streak = ?, best_streak = ?, stage = ?, last_date = ?,
            total_days = total_days + 1, announced_today = 1, last_user_name = ?,
            frozen = 0
        WHERE chat_id = ?
    """, (new_streak, new_best, new_stage, today, user_name, chat_id))
    conn.commit()

    return {
        "streak": new_streak,
        "best": new_best,
        "stage": new_stage,
        "new_record": new_record,
        "was_burned": was_burned,
        "pre_burn": pre_burn,
        "stage_up": stage_up,
        "is_milestone": is_milestone(new_streak),
        "notify_mode": s.get("notify_mode", "milestones"),
    }


def build_summary(conn: sqlite3.Connection, chat_id: int, info: Dict[str, Any]) -> Optional[str]:
    """
    Формирует приятный, лаконичный текст сводки в зависимости от настроек уведомлений.
    Возвращает None, если сообщение слать не нужно.
    """
    mode = info.get("notify_mode", "milestones")
    if mode == "silent":
        return None

    streak = info["streak"]
    best = info["best"]
    st_name = stage_name(info["stage"])

    # Если режим "milestones": отправляем только важные события
    if mode == "milestones":
        if not (info["stage_up"] or info["is_milestone"] or (info["new_record"] and streak >= 7) or info["was_burned"] or streak == 1):
            return None

    lines = []

    if info["stage_up"]:
        lines.append("🎊 *Новый уровень отношений!*")
        lines.append(f"🎭 Стадия повышена: *{st_name}*")
        lines.append(f"🔥 Серия: *{streak} {plural_days(streak)}* подряд!")
        if info["new_record"]:
            lines.append("🏆 *Это ваш новый абсолютный рекорд!*")
    elif info["is_milestone"]:
        lines.append("🌟 *Юбилейная серия общения!*")
        lines.append(f"🔥 Мы неразрывно общаемся уже *{streak} {plural_days(streak)}*!")
        lines.append(f"🎭 Стадия: *{st_name}*")
    elif info["was_burned"]:
        lines.append("💔 *Серия была прервана*")
        if info.get("pre_burn"):
            lines.append(f"Предыдущий стрик ({info['pre_burn']} {plural_days(info['pre_burn'])}) сгорел из-за паузы.")
        lines.append("✨ Вы можете воскресить её командой `.revive`!")
        lines.append(f"🔥 Начинаем новый отсчёт: *1 день* ({st_name})")
    elif info["new_record"] and streak >= 7:
        lines.append("🏆 *Новый рекорд общения!*")
        lines.append(f"🔥 Серия: *{streak} {plural_days(streak)}*")
        lines.append(f"🎭 Стадия: *{st_name}*")
    else:
        # Для первого дня или режима "all"
        lines.append(f"🔥 Серия продлена: *{streak} {plural_days(streak)}* подряд!")
        lines.append(f"🎭 {st_name} • 🏆 Рекорд: {best}")

    return "\n".join(lines)


def burn_stale_streaks(conn: sqlite3.Connection) -> None:
    """
    Вызывается воркером при смене суток:
    - Защищает замороженные серии (frozen == 1)
    - Сжигает непродлённые серии, у которых last_date < yesterday
    - Сбрасывает суточные флаги
    - Обновляет лимиты 1-го числа месяца
    """
    conn.row_factory = sqlite3.Row
    today = _today()
    yesterday = _yesterday()
    current_month = today[:7]

    rows = conn.execute("SELECT * FROM series WHERE enabled = 1").fetchall()
    for row in rows:
        r = dict(row)
        chat_id = r["chat_id"]

        # Если была активна заморозка на вчера
        if r["frozen"]:
            # Заморозка спасает стрик: считаем, что вчера было защищено
            conn.execute(
                "UPDATE series SET frozen = 0, last_date = ? WHERE chat_id = ?",
                (yesterday, chat_id),
            )
            log_event(conn, chat_id, "freeze_applied", by_user="system")
            continue

        # Сжигаем, если последнее сообщение было раньше вчера и серия была активна
        if r["last_date"] and r["last_date"] < yesterday and r["current_streak"] > 0:
            conn.execute("""
                UPDATE series
                SET pre_burn_streak = ?, current_streak = 0, burned_date = ?
                WHERE chat_id = ?
            """, (r["current_streak"], now_iso(), chat_id))
            log_event(conn, chat_id, "burned", by_user="system")

        # Сброс суточного флага
        conn.execute("UPDATE series SET announced_today = 0 WHERE chat_id = ?", (chat_id,))

        # Сброс лимитов 1-го числа месяца
        if r.get("month_tracker") != current_month:
            from admin_db import has_feature_access, OWNER_ID
            rev_limit, frz_limit = (10, 10) if has_feature_access(conn, OWNER_ID, "extended_limits") else (3, 2)
            conn.execute("""
                UPDATE series
                SET revives_left = ?, freezes_left = ?, month_tracker = ?
                WHERE chat_id = ?
            """, (rev_limit, frz_limit, current_month, chat_id))

    conn.commit()