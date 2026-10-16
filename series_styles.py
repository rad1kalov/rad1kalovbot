# series_styles.py
"""Визуальные стили, темы оформления и карточки серий общения в зависимости от дней."""

from typing import Dict, Any, Tuple
from stages import stage_name, get_progress, render_progress_bar, STAGE_CONFIG
from series import plural_days
from timezone import today_str

STYLES = [
    {
        "min_days": 365,
        "name": "👑 АБСОЛЮТНАЯ ЛЕГЕНДА",
        "header": "╭─── 👑 АБСОЛЮТНАЯ ЛЕГЕНДА ───╮",
        "footer": "╰──────────────────────────────╯",
        "badge": "👑 Королевский статус",
        "accent": "🌟",
    },
    {
        "min_days": 200,
        "name": "⚡️ ГРОЗОВОЙ АЛМАЗ",
        "header": "╭─── ⚡️ ГРОЗОВОЙ АЛМАЗ ───╮",
        "footer": "╰─────────────────────────╯",
        "badge": "⚡️ Несокрушимая связь",
        "accent": "⚡️",
    },
    {
        "min_days": 100,
        "name": "🔥 ПЛАМЕННЫЙ РУБИН",
        "header": "╭─── 🔥 ПЛАМЕННЫЙ РУБИН ───╮",
        "footer": "╰──────────────────────────╯",
        "badge": "🔥 Сотня дней огня",
        "accent": "🔥",
    },
    {
        "min_days": 60,
        "name": "🔮 НЕОНОВЫЙ АМЕТИСТ",
        "header": "╭─── 🔮 НЕОНОВЫЙ АМЕТИСТ ───╮",
        "footer": "╰───────────────────────────╯",
        "badge": "🔮 Космическая глубина",
        "accent": "🔮",
    },
    {
        "min_days": 30,
        "name": "💎 ПЛАТИНОВЫЙ СТРИК",
        "header": "╭─── 💎 ПЛАТИНОВЫЙ СТРИК ───╮",
        "footer": "╰───────────────────────────╯",
        "badge": "💎 Месяц непрерывного контакта",
        "accent": "💎",
    },
    {
        "min_days": 14,
        "name": "🥇 ЗОЛОТОЙ СТРИК",
        "header": "╭─── 🥇 ЗОЛОТОЙ СТРИК ───╮",
        "footer": "╰────────────────────────╯",
        "badge": "🥇 Две недели золота",
        "accent": "🥇",
    },
    {
        "min_days": 7,
        "name": "🥈 СЕРЕБРЯНЫЙ СТРИК",
        "header": "╭─── 🥈 СЕРЕБРЯНЫЙ СТРИК ───╮",
        "footer": "╰───────────────────────────╯",
        "badge": "🥈 Неделя неразрывного диалога",
        "accent": "🥈",
    },
    {
        "min_days": 3,
        "name": "🥉 БРОНЗОВЫЙ СТРИК",
        "header": "╭─── 🥉 БРОНЗОВЫЙ СТРИК ───╮",
        "footer": "╰──────────────────────────╯",
        "badge": "🥉 Привычка сформирована",
        "accent": "🥉",
    },
    {
        "min_days": 0,
        "name": "🌱 ВЕСЕННИЙ РОСТОК",
        "header": "╭─── 🌱 ВЕСЕННИЙ РОСТОК ───╮",
        "footer": "╰──────────────────────────╯",
        "badge": "🌱 Первые шаги в диалоге",
        "accent": "🌱",
    },
]


def get_streak_theme(days: int) -> dict:
    for style in STYLES:
        if days >= style["min_days"]:
            return style
    return STYLES[-1]


def format_styled_streak_card(s: dict) -> str:
    """Генерирует стилизованную карточку серии с учетом цвета, дней, кастомных стадий и скрытия."""
    streak = s.get("current_streak", 0)
    best = s.get("best_streak", 0)
    theme = get_streak_theme(streak)

    # Стадия: проверяем скрытие (Premium) и кастомную стадию
    hide_stage = bool(s.get("hide_stage", 0))
    custom_stage = s.get("custom_stage")
    base_stage = s.get("stage", "spark")

    if hide_stage:
        stage_display = "🔒 _[Скрыта настройки]_"
    elif custom_stage:
        stage_display = f"✨ *{custom_stage}* _(кастомная)_"
    else:
        stage_display = f"*{stage_name(base_stage)}*"

    cur_span, req_span, pct, nxt_key = get_progress(streak, base_stage)
    bar = render_progress_bar(pct, length=8)

    # Статус дня
    is_enabled = bool(s.get("enabled", 0))
    if not is_enabled:
        status_line = "🔴 Серия выключена"
    elif s.get("frozen"):
        status_line = "❄️ Защищена заморозкой"
    elif s.get("last_date") == today_str():
        status_line = "✅ Продлена сегодня"
    else:
        status_line = "⏳ Ожидает продления сегодня"

    lines = [
        theme["header"],
        f"  {theme['accent']} *СЕРИЯ ОБЩЕНИЯ:* *{streak}* {plural_days(streak)}",
        f"  🏆 *Личный рекорд:* {best} {plural_days(best)}",
    ]

    if not hide_stage:
        lines.append(f"  🎭 *Стадия:* {stage_display}")

    lines.append(f"  📊 *Прогресс:* `{bar}` {int(pct)}%")

    if nxt_key and not hide_stage:
        cfg = STAGE_CONFIG[nxt_key]
        remaining = req_span - cur_span
        lines.append(f"  🎯 До «{cfg['title']} {cfg['icon']}»: ещё {remaining} {plural_days(remaining)}")

    lines.append(f"  📌 *Статус:* {status_line}")
    lines.append(f"  🎖 _{theme['badge']}_")
    lines.append(theme["footer"])

    return "\n".join(lines)
