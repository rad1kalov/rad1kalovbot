# tz.py
"""Единый источник локального времени с учётом часового пояса."""

from datetime import datetime, date, timedelta, timezone

# ─── НАСТРОЙКА ─────────────────────────────────────────
# Смещение твоего часового пояса относительно UTC.
# Примеры:
#   Москва (UTC+3)        → 3
#   Киев (UTC+2/3)        → 2 или 3 (по сезону)
#   Калининград (UTC+2)   → 2
#   Екатеринбург (UTC+5)  → 5
#   Владивосток (UTC+10)  → 10
TZ_OFFSET_HOURS = 3
# ────────────────────────────────────────────────────────

TZ = timezone(timedelta(hours=TZ_OFFSET_HOURS))


def now() -> datetime:
    """Текущее время в твоём часовом поясе (aware)."""
    return datetime.now(timezone.utc).astimezone(TZ)


def today_str() -> str:
    """Сегодняшняя дата в формате YYYY-MM-DD."""
    return now().date().isoformat()


def yesterday_str() -> str:
    """Вчерашняя дата в формате YYYY-MM-DD."""
    return (now().date() - timedelta(days=1)).isoformat()


def today() -> date:
    """Сегодняшняя дата как объект date."""
    return now().date()


def now_iso() -> str:
    """Текущее время в ISO-формате (для логов и БД)."""
    return now().isoformat(timespec="seconds")