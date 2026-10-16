# stages.py
"""Система стадий и уровней отношений для серий общения."""

from typing import Optional, Tuple

# Список ключей стадий по порядку прогресса
STAGES = [
    "spark",        # Знакомство (0+)
    "dialogue",     # Общение (3+)
    "buddies",      # Приятели (7+)
    "friendship",   # Дружба (14+)
    "best_friends", # Лучшие друзья (30+)
    "soulmates",    # Близкие души (60+)
    "love",         # Истинная связь (100+)
    "inseparable",  # Неразлучные (200+)
    "legend",       # Легендарная связь (365+)
]

# Конфигурация каждой стадии
STAGE_CONFIG = {
    "spark": {
        "title": "Знакомство",
        "icon": "🌱",
        "min_days": 0,
        "description": "Первые искры общения",
    },
    "dialogue": {
        "title": "Общение",
        "icon": "💬",
        "min_days": 3,
        "description": "Интересные темы и взаимный интерес",
    },
    "buddies": {
        "title": "Приятели",
        "icon": "✨",
        "min_days": 7,
        "description": "Неделя ежедневных разговоров!",
    },
    "friendship": {
        "title": "Дружба",
        "icon": "🤝",
        "min_days": 14,
        "description": "Крепкая дружба, проверенная временем",
    },
    "best_friends": {
        "title": "Лучшие друзья",
        "icon": "🌟",
        "min_days": 30,
        "description": "Месяц неразрывного контакта",
    },
    "soulmates": {
        "title": "Близкие души",
        "icon": "💫",
        "min_days": 60,
        "description": "Глубокое понимание с полуслова",
    },
    "love": {
        "title": "Истинная связь",
        "icon": "❤️",
        "min_days": 100,
        "description": "Сотня дней вместе — особенная связь",
    },
    "inseparable": {
        "title": "Неразлучные",
        "icon": "💖",
        "min_days": 200,
        "description": "Вы часть жизни друг друга каждый день",
    },
    "legend": {
        "title": "Легендарная связь",
        "icon": "👑",
        "min_days": 365,
        "description": "Год непрерывного общения. Настоящая редкость!",
    },
}

# Обратная совместимость для старых записей из БД
LEGACY_MAP = {
    "marriage": "legend",
}


def normalize_stage(stage: str) -> str:
    """Приводит старый ключ стадии к актуальному."""
    stage = LEGACY_MAP.get(stage, stage)
    return stage if stage in STAGE_CONFIG else "spark"


def stage_name(stage: str) -> str:
    """Возвращает название стадии с эмодзи, например 'Лучшие друзья 🌟'."""
    key = normalize_stage(stage)
    cfg = STAGE_CONFIG[key]
    return f"{cfg['title']} {cfg['icon']}"


def stage_info(stage: str) -> dict:
    """Возвращает конфигурацию стадии."""
    key = normalize_stage(stage)
    return STAGE_CONFIG[key]


def get_stage_by_days(days: int) -> str:
    """Определяет подходящую стадию по количеству дней стрика."""
    matched = "spark"
    for key in STAGES:
        if days >= STAGE_CONFIG[key]["min_days"]:
            matched = key
        else:
            break
    return matched


def next_stage(stage: str) -> Optional[str]:
    """Возвращает ключ следующей стадии или None, если достигнут максимум."""
    key = normalize_stage(stage)
    try:
        idx = STAGES.index(key)
    except ValueError:
        return None
    return STAGES[idx + 1] if idx + 1 < len(STAGES) else None


def prev_stage(stage: str) -> Optional[str]:
    """Возвращает ключ предыдущей стадии или None, если стадия минимальная."""
    key = normalize_stage(stage)
    try:
        idx = STAGES.index(key)
    except ValueError:
        return None
    return STAGES[idx - 1] if idx > 0 else None


def get_progress(days: int, current_stage: str) -> Tuple[int, int, float, Optional[str]]:
    """
    Возвращает:
      (текущие дни на этой стадии, сколько дней нужно до следующей, процент прогресса 0..100, ключ след. стадии)
    """
    key = normalize_stage(current_stage)
    nxt_key = next_stage(key)
    cur_min = STAGE_CONFIG[key]["min_days"]

    if not nxt_key:
        return (days - cur_min, 0, 100.0, None)

    nxt_min = STAGE_CONFIG[nxt_key]["min_days"]
    required_span = nxt_min - cur_min
    current_span = max(0, min(days - cur_min, required_span))
    percent = (current_span / required_span) * 100.0 if required_span > 0 else 100.0

    return (current_span, required_span, percent, nxt_key)


def render_progress_bar(percent: float, length: int = 8) -> str:
    """Отрисовывает текстовый прогресс-бар, например: [▰▰▰▰▱▱▱▱]."""
    clamped = max(0.0, min(100.0, percent))
    filled = int(round((clamped / 100.0) * length))
    empty = length - filled
    return f"{'▰' * filled}{'▱' * empty}"