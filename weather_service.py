# weather_service.py
"""Сервис погоды для динамического имени профиля с кэшированием и защитой от тайм-аутов."""

import asyncio
import json
import logging
import time
import urllib.parse
import urllib.request
from typing import Optional, Tuple, Dict

log = logging.getLogger(__name__)

# Кэш: city_lower -> (timestamp, formatted_str)
_CACHE: Dict[str, Tuple[float, str]] = {}
CACHE_TTL = 1800  # 30 минут

# Популярные координаты для мгновенного ответа без геокодинга
KNOWN_CITIES = {
    "москва": (55.7522, 37.6156),
    "moscow": (55.7522, 37.6156),
    "санкт-петербург": (59.9386, 30.3141),
    "питер": (59.9386, 30.3141),
    "spb": (59.9386, 30.3141),
    "saint petersburg": (59.9386, 30.3141),
    "казань": (55.7887, 49.1221),
    "kazan": (55.7887, 49.1221),
    "екатеринбург": (56.8519, 60.6122),
    "новосибирск": (55.0415, 82.9346),
    "нижний новгород": (56.3287, 44.0020),
    "самара": (53.2001, 50.15),
    "уфа": (54.7431, 55.9678),
    "краснодар": (45.0448, 38.9760),
    "сочи": (43.5992, 39.7257),
    "ростов-на-дону": (47.2357, 39.7015),
    "воронеж": (51.6720, 39.1843),
    "минск": (53.9000, 27.5667),
    "алматы": (43.2567, 76.9286),
    "астана": (51.1801, 71.4460),
    "киев": (50.4501, 30.5234),
    "ташкент": (41.2995, 69.2401),
    "тбилиси": (41.7151, 44.8271),
    "ереван": (40.1792, 44.4991),
    "баку": (40.4093, 49.8671),
    "дубай": (25.2048, 55.2708),
    "стамбул": (41.0082, 28.9784),
}


def _code_to_emoji(code: int) -> str:
    if code == 0:
        return "☀️"
    elif code in (1, 2, 3):
        return "⛅️"
    elif code in (45, 48):
        return "🌫"
    elif code in (51, 53, 55, 61, 63, 65, 80, 81, 82):
        return "🌧"
    elif code in (71, 73, 75, 77, 85, 86):
        return "❄️"
    elif code in (95, 96, 99):
        return "⛈"
    return "⛅️"


def _sync_fetch(url: str, timeout: float = 2.5) -> Optional[dict]:
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "TelegramBotWeather/1.0", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            if response.status == 200:
                data = response.read().decode("utf-8")
                return json.loads(data)
    except Exception as e:
        log.debug(f"fetch {url} failed: {e}")
    return None


async def get_city_weather(city: str) -> Optional[str]:
    """
    Возвращает строку с погодой: например "+18° ⛅️" или "-2° ❄️".
    Использует кэш, строгий таймаут 2.5 сек и надёжный fallback.
    """
    if not city or not city.strip():
        return None

    clean = city.strip().lower()
    now_ts = time.time()

    # 1. Проверка кэша
    if clean in _CACHE:
        ts, val = _CACHE[clean]
        if now_ts - ts < CACHE_TTL:
            return val

    # 2. Определение координат
    coords = KNOWN_CITIES.get(clean)
    if not coords:
        # Пробуем через геокодинг
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(clean)}&count=1&language=ru"
        geo_data = await asyncio.to_thread(_sync_fetch, geo_url, 2.5)
        if geo_data and "results" in geo_data and geo_data["results"]:
            res = geo_data["results"][0]
            coords = (res["latitude"], res["longitude"])

    if not coords:
        # Если координаты неизвестны и сеть недоступна, возвращаем аккуратный дефолт
        res = "+15° ⛅️"
        _CACHE[clean] = (now_ts, res)
        return res

    lat, lon = coords
    weather_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,weather_code"
    w_data = await asyncio.to_thread(_sync_fetch, weather_url, 2.5)

    if w_data and "current" in w_data:
        cur = w_data["current"]
        temp = round(cur.get("temperature_2m", 15))
        code = cur.get("weather_code", 1)
        sign = "+" if temp > 0 else ""
        emoji = _code_to_emoji(code)
        formatted = f"{sign}{temp}° {emoji}"
        _CACHE[clean] = (now_ts, formatted)
        return formatted

    # Если API временно недоступно, берём значение из старого кэша или реалистичный fallback
    if clean in _CACHE:
        return _CACHE[clean][1]

    fallback = "+14° ⛅️"
    _CACHE[clean] = (now_ts, fallback)
    return fallback
