# media_downloader.py
"""
Модуль распознавания и скачивания медиа из соцсетей через yt-dlp:
- YouTube, YouTube Shorts
- VK Видео, VK Клипы
- Rutube
- TikTok
- Instagram (Reels, Posts)
- Pinterest
- SoundCloud
- Яндекс Музыка

Ограничения:
- Без премиума: качество до 1080p, до 20 скачиваний в месяц.
- С премиумом: 2K/4K (если доступно), безлимитные скачивания.
- Интерактивные кнопки выбора качества и скачивания.
"""

import asyncio
import hashlib
import logging
import os
import re
import shutil
import sqlite3
import tempfile
from datetime import datetime
from typing import Dict, Optional, Tuple, Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.ext import ContextTypes

from premium_db import is_premium
from timezone import now, now_iso

log = logging.getLogger(__name__)

# Регулярные выражения поддерживаемых сервисов
PLATFORMS_CONFIG = [
    {
        "name": "YouTube Shorts",
        "icon": "🔴",
        "pattern": re.compile(r"https?://(?:www\.)?youtube\.com/shorts/([a-zA-Z0-9_-]+)", re.IGNORECASE),
    },
    {
        "name": "YouTube",
        "icon": "🔴",
        "pattern": re.compile(r"https?://(?:www\.)?(?:youtube\.com/(?:watch\?v=|embed/|v/)|youtu\.be/)([a-zA-Z0-9_-]+)", re.IGNORECASE),
    },
    {
        "name": "VK Клипы",
        "icon": "🔵",
        "pattern": re.compile(r"https?://(?:www\.)?(?:vk\.com|vk\.ru)/clip(-?\d+_\d+)", re.IGNORECASE),
    },
    {
        "name": "VK Видео",
        "icon": "🔵",
        "pattern": re.compile(r"https?://(?:www\.)?(?:vk\.com|vk\.ru|vkvideo\.ru)/(?:video|video_ext\.php\?oid=)(-?\d+_\d+)?", re.IGNORECASE),
    },
    {
        "name": "Rutube",
        "icon": "🟢",
        "pattern": re.compile(r"https?://(?:www\.)?rutube\.ru/(?:video|shorts)/([a-zA-Z0-9_-]+)", re.IGNORECASE),
    },
    {
        "name": "TikTok",
        "icon": "🖤",
        "pattern": re.compile(r"https?://(?:(?:www|vm|vt|m|v)\.)?tiktok\.com/[^\s]+", re.IGNORECASE),
    },
    {
        "name": "Instagram",
        "icon": "🟣",
        "pattern": re.compile(r"https?://(?:www\.)?(?:instagram\.com|instagr\.am)/(?:reel|p|tv)/([a-zA-Z0-9_-]+)", re.IGNORECASE),
    },
    {
        "name": "Pinterest",
        "icon": "📌",
        "pattern": re.compile(r"https?://(?:[a-zA-Z0-9-]+\.)?(?:pinterest\.com/pin/\d+|pin\.it/[a-zA-Z0-9]+)", re.IGNORECASE),
    },
    {
        "name": "SoundCloud",
        "icon": "🟠",
        "pattern": re.compile(r"https?://(?:www\.)?soundcloud\.com/[^\s]+", re.IGNORECASE),
    },
    {
        "name": "Яндекс Музыка",
        "icon": "🟡",
        "pattern": re.compile(r"https?://(?:music\.yandex\.(?:ru|com)|music\.yandex\.by|music\.yandex\.kz)/album/\d+(?:/track/\d+)?", re.IGNORECASE),
    },
]

MAX_FREE_MONTHLY_DOWNLOADS = 20

# Сессии активных карточек скачивания (token -> dict info)
_ACTIVE_SESSIONS: Dict[str, Dict[str, Any]] = {}


def init_media_db(conn: sqlite3.Connection) -> None:
    """Инициализация таблиц для истории скачиваний и предпочтений."""
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS media_downloads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            url TEXT NOT NULL,
            platform TEXT,
            title TEXT,
            quality TEXT,
            file_size INTEGER DEFAULT 0,
            downloaded_at TEXT,
            month_key TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_media_dl_user_month ON media_downloads(user_id, month_key)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_media_prefs (
            user_id INTEGER PRIMARY KEY,
            preferred_quality TEXT DEFAULT '1080p',
            last_url TEXT,
            updated_at TEXT
        )
    """)
    conn.commit()


def extract_supported_url(text: str) -> Optional[Tuple[str, str, str]]:
    """
    Распознаёт ссылку в тексте сообщения.
    Возвращает (url, platform_name, icon) или None, если ссылка не поддерживается.
    """
    if not text:
        return None

    # Поиск первого совпадения
    for item in PLATFORMS_CONFIG:
        m = item["pattern"].search(text)
        if m:
            raw_url = m.group(0).rstrip(".,)>]\"'")
            return raw_url, item["name"], item["icon"]

    return None


def get_month_key() -> str:
    """Возвращает ключ текущего месяца, например '2026-10'."""
    return datetime.now().strftime("%Y-%m")


def get_user_monthly_downloads(conn: sqlite3.Connection, user_id: int) -> int:
    """Возвращает число скачиваний за текущий календарный месяц."""
    m_key = get_month_key()
    row = conn.execute(
        "SELECT COUNT(*) FROM media_downloads WHERE user_id = ? AND month_key = ?",
        (user_id, m_key),
    ).fetchone()
    return int(row[0]) if row else 0


def check_download_limit(conn: sqlite3.Connection, user_id: int) -> Tuple[bool, int, int]:
    """
    Проверяет лимит скачиваний:
    Возвращает (can_download, current_count, max_limit).
    Для премиума: (True, current_count, -1).
    """
    cnt = get_user_monthly_downloads(conn, user_id)
    if is_premium(conn, user_id):
        return True, cnt, -1
    return (cnt < MAX_FREE_MONTHLY_DOWNLOADS), cnt, MAX_FREE_MONTHLY_DOWNLOADS


def record_media_download(
    conn: sqlite3.Connection,
    user_id: int,
    url: str,
    platform: str,
    title: str,
    quality: str,
    file_size: int = 0,
) -> None:
    """Сохраняет успешную запись о скачивании."""
    m_key = get_month_key()
    conn.execute("""
        INSERT INTO media_downloads (user_id, url, platform, title, quality, file_size, downloaded_at, month_key)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (user_id, url, platform, title, quality, file_size, now_iso(), m_key))
    conn.commit()


def get_user_quality(conn: sqlite3.Connection, user_id: int) -> str:
    """Получает сохранённое предпочитаемое качество пользователя."""
    row = conn.execute("SELECT preferred_quality FROM user_media_prefs WHERE user_id = ?", (user_id,)).fetchone()
    return row[0] if row and row[0] else "1080p"


def set_user_quality(conn: sqlite3.Connection, user_id: int, quality: str) -> None:
    """Сохраняет предпочитаемое качество пользователя."""
    conn.execute("""
        INSERT INTO user_media_prefs (user_id, preferred_quality, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            preferred_quality = excluded.preferred_quality,
            updated_at = excluded.updated_at
    """, (user_id, quality, now_iso()))
    conn.commit()


def _make_session_id(user_id: int, url: str) -> str:
    """Создаёт короткий уникальный хеш для сессии инлайн-кнопок."""
    h = hashlib.md5(f"{user_id}:{url}:{datetime.now().timestamp()}".encode()).hexdigest()[:8]
    return h


def get_download_proxy() -> Optional[str]:
    """Возвращает адрес прокси из переменных окружения, если задан."""
    for key in (
        "DOWNLOAD_PROXY",
        "MEDIA_PROXY",
        "PROXY",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "https_proxy",
        "http_proxy",
        "all_proxy",
    ):
        val = os.getenv(key)
        if val and val.strip():
            return val.strip()
    return None


def _fetch_tiktok_data(url: str) -> Optional[Dict[str, Any]]:
    """
    Получает информацию о видео TikTok через TikWM API (без ограничений по IP датацентров и без водяных знаков).
    Поддерживает зеркала и настроенный прокси.
    """
    import json
    import urllib.parse
    import urllib.request

    endpoints = [
        "https://www.tikwm.com/api/",
        "https://tikwm.com/api/",
    ]
    post_data = urllib.parse.urlencode({"url": url, "hd": 1}).encode("utf-8")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Origin": "https://www.tikwm.com",
        "Referer": "https://www.tikwm.com/",
    }

    proxy = get_download_proxy()
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)

    for ep in endpoints:
        try:
            req = urllib.request.Request(ep, data=post_data, headers=headers)
            with opener.open(req, timeout=12) as resp:
                raw = resp.read().decode("utf-8", errors="ignore")
                data = json.loads(raw)
                if data.get("code") == 0 and "data" in data and isinstance(data["data"], dict):
                    return data["data"]
                log.debug("TikWM %s returned: code=%s msg=%s", ep, data.get("code"), data.get("msg"))
        except Exception as e:
            log.warning("TikWM request to %s failed: %s", ep, e)
            continue

    return None


def _fetch_tiktok_info_sync(url: str) -> Optional[Dict[str, Any]]:
    """Извлекает информацию о видео TikTok без ограничений по IP."""
    try:
        data = _fetch_tiktok_data(url)
        if not data:
            return None

        duration_sec = data.get("duration") or 0
        dur_str = ""
        if duration_sec:
            mins = int(duration_sec // 60)
            secs = int(duration_sec % 60)
            dur_str = f"{mins}:{secs:02d}"

        author = data.get("author") or {}
        uploader = author.get("nickname") or author.get("unique_id") or ""
        raw_title = data.get("title") or "TikTok видео"
        clean_title = re.sub(r"[\r\n\t]+", " ", raw_title).strip()
        if not clean_title:
            clean_title = f"TikTok от @{uploader}" if uploader else "TikTok видео"

        return {
            "title": clean_title[:80],
            "duration": dur_str,
            "uploader": uploader,
            "url": url,
        }
    except Exception as e:
        log.warning("TikTok info extract error: %s", e)
        return None


def _fetch_yandex_music_info_sync(url: str) -> Optional[Dict[str, Any]]:
    """Извлекает название и исполнителя трека Яндекс Музыки через мобильный API без авторизации."""
    m = re.search(r"/track/(\d+)", url)
    if not m:
        return None
    track_id = m.group(1)
    try:
        import urllib.request
        import json
        api_url = f"https://api.music.yandex.net/tracks/{track_id}"
        req = urllib.request.Request(
            api_url,
            headers={"User-Agent": "YandexMusic/2024.04 (Android 14; Pixel 8)"}
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
            items = data.get("result", [])
            if not items:
                return None
            res = items[0]
            title = res.get("title") or "Трек"
            artists = ", ".join([a.get("name") for a in res.get("artists", []) if a.get("name")])
            full_title = f"{artists} - {title}" if artists else title
            duration_ms = res.get("durationMs", 0)
            dur_str = ""
            if duration_ms:
                secs_total = duration_ms // 1000
                dur_str = f"{secs_total // 60}:{secs_total % 60:02d}"
            return {
                "title": full_title[:80],
                "duration": dur_str,
                "uploader": artists,
                "url": url,
            }
    except Exception as e:
        log.debug("Yandex Music API extract failed: %s", e)
        return None


def _escape_md(text: str) -> str:
    """Экранирует специальные символы Markdown v1 для безопасной отправки."""
    if not text:
        return ""
    return re.sub(r"([_*`\[\]])", r"\\\1", str(text))


async def _safe_edit_text(query, text: str, reply_markup=None, parse_mode: Optional[str] = "Markdown") -> None:
    """Безопасное редактирование сообщения с защитой от ошибок парсинга Markdown."""
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception as e:
        log.debug("safe_edit_text Markdown failed (%s), fallback plain text", e)
        plain = re.sub(r"[*_`]", "", text)
        try:
            await query.edit_message_text(plain, reply_markup=reply_markup)
        except Exception:
            pass


def fetch_media_info_sync(url: str) -> Dict[str, Any]:
    """Извлекает информацию о медиа через прямой API или yt-dlp без скачивания."""
    # Для TikTok используем быстрый прямой API TikWM
    if "tiktok.com" in url:
        tt = _fetch_tiktok_info_sync(url)
        if tt:
            return tt

    # Для Яндекс Музыки используем прямой API
    if "music.yandex." in url:
        ym = _fetch_yandex_music_info_sync(url)
        if ym:
            return ym

    import yt_dlp

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": False,
        "noplaylist": True,
        "nocheckcertificate": True,
        "socket_timeout": 15,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        },
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "web"],
            },
        },
    }
    proxy = get_download_proxy()
    if proxy:
        ydl_opts["proxy"] = proxy
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
            if not info or not isinstance(info, dict):
                return {"title": "Медиафайл", "duration": "", "uploader": "", "url": url}

            # Если это плейлист/альбом, берём первое видео
            if "entries" in info:
                entries = info.get("entries")
                if entries and isinstance(entries, list) and entries[0]:
                    info = entries[0]

            duration = info.get("duration") if isinstance(info, dict) else None
            dur_str = ""
            if duration:
                mins = int(duration // 60)
                secs = int(duration % 60)
                dur_str = f"{mins}:{secs:02d}"

            raw_title = info.get("title") if isinstance(info, dict) else None
            return {
                "title": (raw_title or "Медиафайл").strip()[:80],
                "duration": dur_str,
                "uploader": (info.get("uploader") or info.get("channel") or "") if isinstance(info, dict) else "",
                "url": url,
            }
        except Exception as e:
            log.warning("yt-dlp extract_info error for %s: %s", url, e)
            return {"title": "Медиафайл", "duration": "", "uploader": "", "url": url}


def build_download_panel(
    conn: sqlite3.Connection,
    user_id: int,
    session_id: str,
    platform: str,
    icon: str,
    url: str,
    title: str = "Медиафайл",
    duration: str = "",
    quality: Optional[str] = None,
) -> Tuple[str, InlineKeyboardMarkup]:
    """Формирует текст карточки и инлайн-кнопки для скачивания."""
    qual = quality or get_user_quality(conn, user_id)
    can_dl, cur_cnt, max_cnt = check_download_limit(conn, user_id)
    prem = is_premium(conn, user_id)

    if prem:
        limit_line = "Безлимит 👑 (Премиум)"
    else:
        limit_line = f"{cur_cnt}/{max_cnt} в этом месяце"

    dur_line = f"⏱ Длительность: *{duration}*\n" if duration else ""
    safe_title = _escape_md(title)

    text = (
        f"{icon} *{platform}* • Найдено медиа!\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📹 *{safe_title}*\n"
        f"{dur_line}"
        f"⚙️ Качество: *{qual}*\n"
        f"📊 Лимит скачиваний: *{limit_line}*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"Нажмите кнопку ниже, чтобы начать скачивание:"
    )

    kb = [
        [
            InlineKeyboardButton(f"📥 Скачать ({qual})", callback_data=f"mdl:dl:{session_id}"),
        ],
        [
            InlineKeyboardButton("⚙️ Качество", callback_data=f"mdl:qual:{session_id}"),
            InlineKeyboardButton("🎵 Аудио (MP3)", callback_data=f"mdl:aud:{session_id}"),
        ],
        [
            InlineKeyboardButton("❌ Закрыть", callback_data=f"mdl:close:{session_id}"),
        ],
    ]
    return text, InlineKeyboardMarkup(kb)


def build_quality_menu(session_id: str, current_qual: str, is_prem: bool) -> Tuple[str, InlineKeyboardMarkup]:
    """Формирует меню выбора качества видео."""
    text = (
        "⚙️ *Выберите качество видео для скачивания:*\n\n"
        "• Для обычных пользователей доступно качество *до 1080p*.\n"
        "• Качество *1440p (2K)* и *2160p (4K)* доступно с *Премиум подпиской* ⭐."
    )

    q_list = ["360p", "480p", "720p", "1080p"]
    prem_q_list = ["1440p", "2160p"]

    rows = []
    # Ряд 1: стандартное качество
    r1 = []
    for q in ["360p", "480p"]:
        mark = " ✅" if q == current_qual else ""
        r1.append(InlineKeyboardButton(f"{q}{mark}", callback_data=f"mdl:setq:{session_id}:{q}"))
    rows.append(r1)

    r2 = []
    for q in ["720p", "1080p"]:
        mark = " ✅" if q == current_qual else ""
        r2.append(InlineKeyboardButton(f"{q}{mark}", callback_data=f"mdl:setq:{session_id}:{q}"))
    rows.append(r2)

    # Ряд 2: Премиум-качество
    r3 = []
    for q in prem_q_list:
        label = f"{q} (2K) ⭐" if q == "1440p" else f"{q} (4K) ⭐"
        if q == current_qual:
            label += " ✅"
        r3.append(InlineKeyboardButton(label, callback_data=f"mdl:setq:{session_id}:{q}"))
    rows.append(r3)

    rows.append([
        InlineKeyboardButton("🎵 Скачать только аудио (MP3)", callback_data=f"mdl:aud:{session_id}"),
    ])
    rows.append([
        InlineKeyboardButton("⬅️ Назад", callback_data=f"mdl:back:{session_id}"),
    ])

    return text, InlineKeyboardMarkup(rows)


def get_ffmpeg_path() -> Optional[str]:
    """
    Находит доступный исполняемый файл ffmpeg:
    1. Системный PATH
    2. Встроенный через библиотеку imageio-ffmpeg
    3. Локальные папки проекта
    """
    p = shutil.which("ffmpeg")
    if p:
        return p

    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.exists(exe):
            return exe
    except Exception:
        pass

    base_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(base_dir, "data", "bin", "ffmpeg.exe"),
        os.path.join(base_dir, "bin", "ffmpeg.exe"),
        os.path.join(base_dir, "ffmpeg.exe"),
        os.path.join(base_dir, "data", "bin", "ffmpeg"),
        os.path.join(base_dir, "bin", "ffmpeg"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c

    return None


def _download_tiktok_sync(url: str, quality: str, is_audio: bool = False) -> Tuple[Optional[str], Optional[str], Optional[int]]:
    """
    Скачивает видео или аудио TikTok без водяных знаков и без ограничений по IP датацентров.
    Возвращает (file_path, title, file_size) или (None, error_msg, None).
    """
    import urllib.request
    try:
        data = _fetch_tiktok_data(url)
        if not data:
            return None, "Не удалось получить ссылку на медиафайл через TikTok API", None

        author = data.get("author") or {}
        uploader = author.get("nickname") or author.get("unique_id") or ""
        raw_title = data.get("title") or "TikTok видео"
        clean_title = re.sub(r"[\r\n\t]+", " ", raw_title).strip()
        if not clean_title:
            clean_title = f"TikTok от @{uploader}" if uploader else "TikTok видео"
        title = clean_title[:80]

        video_id = str(data.get("id") or int(datetime.now().timestamp()))
        temp_dir = tempfile.mkdtemp(prefix="tt_")

        proxy = get_download_proxy()
        handlers = []
        if proxy:
            handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        opener = urllib.request.build_opener(*handlers)

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Referer": "https://www.tiktok.com/",
        }

        # 1. Скачивание аудио
        if is_audio:
            audio_url = data.get("music")
            if audio_url:
                if audio_url.startswith("/"):
                    audio_url = f"https://www.tikwm.com{audio_url}"
                target_file = os.path.join(temp_dir, f"audio_{video_id}.mp3")
                req = urllib.request.Request(audio_url, headers=headers)
                with opener.open(req, timeout=30) as resp, open(target_file, "wb") as f:
                    while True:
                        chunk = resp.read(64 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                sz = os.path.getsize(target_file)
                if sz > 0:
                    music_info = data.get("music_info") or {}
                    music_title = music_info.get("title") or title
                    return target_file, music_title[:80], sz

        # 2. Скачивание видео (или аудио через видео, если прямого audio_url не было)
        hd_url = data.get("hdplay")
        play_url = data.get("play")
        hd_size = data.get("hd_size") or 0
        std_size = data.get("size") or 0
        max_tg_size = 49 * 1024 * 1024  # 49 МБ для Telegram Bot API

        # Для обычных пользователей или если HD превышает лимит Telegram — берем стандартный stream
        if quality in ("1080p", "1440p", "2160p") and hd_url:
            if hd_size > max_tg_size and std_size > 0 and std_size <= max_tg_size and play_url:
                chosen_url = play_url
            else:
                chosen_url = hd_url
        else:
            chosen_url = play_url or hd_url

        if not chosen_url:
            return None, "В ответе TikTok API отсутствует ссылка на скачивание видео", None

        if chosen_url.startswith("/"):
            chosen_url = f"https://www.tikwm.com{chosen_url}"

        target_file = os.path.join(temp_dir, f"video_{video_id}.mp4")
        req = urllib.request.Request(chosen_url, headers=headers)
        with opener.open(req, timeout=60) as resp, open(target_file, "wb") as f:
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                f.write(chunk)

        sz = os.path.getsize(target_file)
        if sz == 0:
            return None, "Скачанный файл видео оказался пустым", None

        # Если запрашивалось аудио, но прямой ссылки на трек не было — конвертируем через ffmpeg
        if is_audio:
            ffmpeg_bin = get_ffmpeg_path()
            if ffmpeg_bin:
                mp3_file = os.path.join(temp_dir, f"audio_{video_id}.mp3")
                cmd = [ffmpeg_bin, "-y", "-i", target_file, "-vn", "-c:a", "libmp3lame", "-b:a", "192k", mp3_file]
                import subprocess
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                if os.path.exists(mp3_file) and os.path.getsize(mp3_file) > 0:
                    try:
                        os.remove(target_file)
                    except Exception:
                        pass
                    return mp3_file, title, os.path.getsize(mp3_file)

        return target_file, title, sz
    except Exception as e:
        log.warning("TikTok direct download failed: %s", e)
        return None, f"Ошибка загрузки TikTok: {e}", None


def download_media_sync(url: str, quality: str, is_audio: bool = False) -> Tuple[Optional[str], Optional[str], Optional[int]]:
    """
    Синхронно скачивает файл через прямой сервис или yt-dlp во временный каталог.
    Возвращает (file_path, title, file_size) или (None, error_msg, None).
    """
    # 1. Для TikTok пробуем приоритетный надёжный загрузчик (без водяных знаков и без бана по IP)
    if "tiktok.com" in url:
        tt_res = _download_tiktok_sync(url, quality, is_audio)
        if tt_res[0] and os.path.exists(tt_res[0]):
            return tt_res
        log.warning("TikTok direct download failed, falling back to yt-dlp...")

    import yt_dlp

    temp_dir = tempfile.mkdtemp(prefix="ytdl_")
    out_tmpl = os.path.join(temp_dir, "%(title).60s_%(id)s.%(ext)s")

    # Предельное разрешение по высоте
    height_map = {
        "360p": 360,
        "480p": 480,
        "720p": 720,
        "1080p": 1080,
        "1440p": 1440,
        "2160p": 2160,
    }
    max_h = height_map.get(quality, 1080)
    ffmpeg_bin = get_ffmpeg_path()
    proxy = get_download_proxy()

    custom_title = None
    target_url = url

    # Специальная обработка Яндекс Музыки (прямой поиск аудиопотока по названию)
    if "music.yandex." in url:
        ym = _fetch_yandex_music_info_sync(url)
        if ym and ym.get("title"):
            custom_title = ym["title"]
            target_url = f"ytsearch1:{ym['title']} audio"

    common_opts = {
        "outtmpl": out_tmpl,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "nocheckcertificate": True,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        },
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "web"],
            },
        },
    }
    if proxy:
        common_opts["proxy"] = proxy

    if is_audio:
        postprocessors = []
        if ffmpeg_bin:
            postprocessors.append({
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            })
        ydl_opts = {
            **common_opts,
            "format": "bestaudio/best",
            "postprocessors": postprocessors,
        }
        if ffmpeg_bin:
            ydl_opts["ffmpeg_location"] = ffmpeg_bin
    else:
        ydl_opts = {
            **common_opts,
        }
        if ffmpeg_bin:
            ydl_opts["ffmpeg_location"] = ffmpeg_bin
            ydl_opts["merge_output_format"] = "mp4"
            ydl_opts["format"] = (
                f"bestvideo[height<={max_h}][ext=mp4]+bestaudio[ext=m4a]/"
                f"bestvideo[height<={max_h}]+bestaudio/"
                f"best[height<={max_h}][ext=mp4]/"
                f"best[height<={max_h}]/best"
            )
        else:
            # Если ffmpeg отсутствует, скачиваем готовый единый поток без слияния,
            # чтобы yt-dlp никогда не выдавал ошибку "merging of multiple formats but ffmpeg is not installed"
            ydl_opts["format"] = f"best[height<={max_h}][ext=mp4]/best[height<={max_h}]/best"

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(target_url, download=True)
            if not info or not isinstance(info, dict):
                return None, "Не удалось извлечь информацию о медиафайле", None

            if "entries" in info:
                entries = info.get("entries")
                if entries and isinstance(entries, list) and entries[0]:
                    info = entries[0]

            title = custom_title or (info.get("title") if isinstance(info, dict) else None) or "Медиафайл"

            # Находим скачанный файл в папке, исключая временные фрагменты
            files = [
                os.path.join(temp_dir, f) for f in os.listdir(temp_dir)
                if os.path.isfile(os.path.join(temp_dir, f))
                and not f.endswith((".part", ".ytdl", ".temp", ".aria2"))
            ]
            if not files:
                part_files = [f for f in os.listdir(temp_dir) if f.endswith((".part", ".ytdl"))]
                if part_files:
                    return None, "Загрузка файла была прервана сетью. Попробуйте ещё раз.", None
                return None, "Медиафайл не удалось сохранить. Возможно, видео защищено от скачивания или недоступно.", None

            # Берём самый крупный скачанный файл (на случай промежуточных)
            target_file = max(files, key=os.path.getsize)
            file_size = os.path.getsize(target_file)
            return target_file, title, file_size
    except Exception as e:
        log.error("yt-dlp download failed: %s", e)
        err_msg = str(e)
        err_lower = err_msg.lower()
        if "ip address is blocked" in err_lower or ("blocked" in err_lower and "ip" in err_lower):
            err_msg = "Сервер соцсети временно заблокировал IP адрес бота (защита от скачивания). Попробуйте позже или настройте прокси."
        elif "bot" in err_lower and ("sign in" in err_lower or "confirm" in err_lower):
            err_msg = "Сервис требует подтверждения, что вы не бот. Для сервера требуется настроить прокси."
        elif "nonetype" in err_lower:
            err_msg = "Сервис заблокировал прямой доступ к медиафайлу (требуется авторизация или VPN)."
        elif "merging of multiple formats" in err_msg or "ffprobe and ffmpeg not found" in err_msg:
            err_msg = "Для объединения видео и звука в этом качестве требуется ffmpeg (он автоматически устанавливается с imageio-ffmpeg)."
        elif "404" in err_msg or "not found" in err_lower or "is unavailable" in err_lower:
            err_msg = "Медиафайл не найден или недоступен (возможно, удалён автором)."
        elif "timed out" in err_lower or "getaddrinfo failed" in err_lower:
            err_msg = "Таймаут соединения с сервисом. Попробуйте ещё раз."
        return None, err_msg, None


# ─────────────────────── ОБРАБОТЧИК ИНЛАЙН-КНОПОК ───────────────────────

async def handle_media_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, conn: sqlite3.Connection) -> bool:
    """Обрабатывает нажатия кнопок карточки скачивания (mdl:*)."""
    query = update.callback_query
    if not query or not query.data or not query.data.startswith("mdl:"):
        return False

    parts = query.data.split(":")
    action = parts[1]
    session_id = parts[2] if len(parts) > 2 else ""

    sess = _ACTIVE_SESSIONS.get(session_id)
    if not sess:
        await query.answer("Сессия скачивания устарела. Отправьте ссылку заново.", show_alert=True)
        return True

    user_id = query.from_user.id
    # Проверяем, что кнопку нажимает тот, кто запросил или владелец
    if user_id != sess.get("user_id"):
        # Если чат личный или разрешено всем
        pass

    url = sess["url"]
    platform = sess["platform"]
    icon = sess["icon"]
    title = sess.get("title", "Медиафайл")
    duration = sess.get("duration", "")
    quality = sess.get("quality") or get_user_quality(conn, user_id)

    # 1. Закрыть меню
    if action == "close":
        await query.answer()
        try:
            await query.message.delete()
        except Exception:
            await query.edit_message_text("❌ Меню скачивания закрыто.")
        _ACTIVE_SESSIONS.pop(session_id, None)
        return True

    # 2. Открыть меню выбора качества
    elif action == "qual":
        await query.answer()
        prem = is_premium(conn, user_id)
        text, kb = build_quality_menu(session_id, quality, prem)
        await query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
        return True

    # 3. Возврат в главное меню
    elif action == "back":
        await query.answer()
        text, kb = build_download_panel(
            conn, user_id, session_id, platform, icon, url, title, duration, quality
        )
        await query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
        return True

    # 4. Установка качества
    elif action == "setq":
        target_q = parts[3] if len(parts) > 3 else "1080p"
        # Проверка прав на 1440p и 2160p (Премиум)
        if target_q in ("1440p", "2160p") and not is_premium(conn, user_id):
            await query.answer(
                "🔒 Качество выше 1080p (2K / 4K) доступно только по Премиум подписке!\nОформите через команду /premium",
                show_alert=True,
            )
            return True

        set_user_quality(conn, user_id, target_q)
        sess["quality"] = target_q
        await query.answer(f"Выбрано качество: {target_q} ✅")
        text, kb = build_download_panel(
            conn, user_id, session_id, platform, icon, url, title, duration, target_q
        )
        await query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
        return True

    # 5. Скачать только аудио (MP3)
    elif action == "aud":
        await query.answer()
        can_dl, cur_cnt, max_cnt = check_download_limit(conn, user_id)
        if not can_dl:
            await query.answer(
                f"🔒 Месячный лимит бесплатных скачиваний ({max_cnt}/{max_cnt}) исчерпан!\n"
                f"Оформите Премиум (/premium) для безлимитного скачивания.",
                show_alert=True,
            )
            return True

        safe_title = _escape_md(title)
        await _safe_edit_text(
            query,
            f"⏳ *Скачиваю аудиодорожку (MP3)…*\n"
            f"📹 {safe_title}\n\n"
            f"Пожалуйста, подождите немного…",
            parse_mode="Markdown",
        )

        loop = asyncio.get_running_loop()
        file_path, err_or_title, file_size = await loop.run_in_executor(
            None, download_media_sync, url, quality, True
        )

        if not file_path or not os.path.exists(file_path):
            await _safe_edit_text(query, f"❌ Ошибка скачивания аудио: {err_or_title}", parse_mode=None)
            return True

        # Проверка лимита Telegram Bot API (50 МБ)
        max_bytes = 50 * 1024 * 1024
        if file_size and file_size > max_bytes:
            size_mb = file_size / (1024 * 1024)
            await _safe_edit_text(
                query,
                f"⚠️ Размер аудио ({size_mb:.1f} МБ) превышает лимит отправки Telegram Bot API (50 МБ).",
                parse_mode=None,
            )
            try:
                os.remove(file_path)
            except Exception:
                pass
            return True

        await _safe_edit_text(query, "📤 *Отправляю аудио в Telegram…*", parse_mode="Markdown")

        try:
            bc_id = getattr(query.message, "business_connection_id", None)
            with open(file_path, "rb") as f:
                base_ext = os.path.splitext(file_path)[1] or ".mp3"
                clean_filename = f"audio_{int(datetime.now().timestamp())}{base_ext}"
                try:
                    await context.bot.send_audio(
                        chat_id=query.message.chat_id,
                        audio=InputFile(f, filename=clean_filename),
                        title=title,
                        caption=f"{icon} *{safe_title}*\n🎵 Аудио | Скачано через @rad1kal_bot",
                        parse_mode="Markdown",
                        business_connection_id=bc_id,
                    )
                except Exception as e_caption:
                    log.warning("send_audio markdown caption failed (%s), fallback plain text", e_caption)
                    f.seek(0)
                    await context.bot.send_audio(
                        chat_id=query.message.chat_id,
                        audio=InputFile(f, filename=clean_filename),
                        title=title,
                        caption=f"{icon} {clean_title}\n🎵 Аудио | Скачано через @rad1kal_bot",
                        business_connection_id=bc_id,
                    )
            record_media_download(conn, user_id, url, platform, title, "audio_mp3", file_size or 0)
            await _safe_edit_text(query, f"✅ *Аудио успешно отправлено!*\n📹 {safe_title}", parse_mode="Markdown")
        except Exception as e:
            log.error("send_audio error: %s", e)
            await _safe_edit_text(query, f"❌ Не удалось отправить аудио: {e}", parse_mode=None)
        finally:
            try:
                os.remove(file_path)
                os.rmdir(os.path.dirname(file_path))
            except Exception:
                pass
        return True

    # 6. Скачать видео
    elif action == "dl":
        await query.answer()
        can_dl, cur_cnt, max_cnt = check_download_limit(conn, user_id)
        if not can_dl:
            await query.answer(
                f"🔒 Месячный лимит бесплатных скачиваний ({max_cnt}/{max_cnt}) исчерпан!\n"
                f"Оформите Премиум (/premium) для безлимитного скачивания.",
                show_alert=True,
            )
            return True

        safe_title = _escape_md(title)
        await _safe_edit_text(
            query,
            f"⏳ *Скачиваю видео ({quality})…*\n"
            f"📹 {safe_title}\n\n"
            f"Пожалуйста, подождите, это может занять от нескольких секунд до минуты…",
            parse_mode="Markdown",
        )

        loop = asyncio.get_running_loop()
        file_path, err_or_title, file_size = await loop.run_in_executor(
            None, download_media_sync, url, quality, False
        )

        if not file_path or not os.path.exists(file_path):
            await _safe_edit_text(query, f"❌ Ошибка скачивания видео: {err_or_title}", parse_mode=None)
            return True

        max_bytes = 50 * 1024 * 1024
        if file_size and file_size > max_bytes:
            size_mb = file_size / (1024 * 1024)
            await _safe_edit_text(
                query,
                f"⚠️ Размер видео ({size_mb:.1f} МБ) превышает лимит отправки Telegram Bot API (50 МБ).\n"
                f"Попробуйте выбрать меньшее качество (например, 720p или 480p).",
                parse_mode=None,
            )
            try:
                os.remove(file_path)
            except Exception:
                pass
            return True

        await _safe_edit_text(query, "📤 *Отправляю видео в Telegram…*", parse_mode="Markdown")

        try:
            bc_id = getattr(query.message, "business_connection_id", None)
            with open(file_path, "rb") as f:
                base_ext = os.path.splitext(file_path)[1] or ".mp4"
                clean_filename = f"video_{int(datetime.now().timestamp())}{base_ext}"
                try:
                    await context.bot.send_video(
                        chat_id=query.message.chat_id,
                        video=InputFile(f, filename=clean_filename),
                        caption=f"{icon} *{safe_title}*\nКачество: {quality} | @rad1kal_bot",
                        parse_mode="Markdown",
                        supports_streaming=True,
                        business_connection_id=bc_id,
                    )
                except Exception as e_caption:
                    log.warning("send_video markdown caption failed (%s), fallback plain text", e_caption)
                    f.seek(0)
                    await context.bot.send_video(
                        chat_id=query.message.chat_id,
                        video=InputFile(f, filename=clean_filename),
                        caption=f"{icon} {clean_title}\nКачество: {quality} | @rad1kal_bot",
                        supports_streaming=True,
                        business_connection_id=bc_id,
                    )
            record_media_download(conn, user_id, url, platform, title, quality, file_size or 0)
            await _safe_edit_text(query, f"✅ *Видео успешно отправлено!*\n📹 {safe_title}", parse_mode="Markdown")
        except Exception as e:
            log.error("send_video error: %s", e)
            await _safe_edit_text(query, f"❌ Не удалось отправить видео: {e}", parse_mode=None)
        finally:
            try:
                os.remove(file_path)
                os.rmdir(os.path.dirname(file_path))
            except Exception:
                pass
        return True

    return False


async def handle_detected_media_url(
    bot,
    conn: sqlite3.Connection,
    chat_id: int,
    user_id: int,
    raw_message_text: str,
    message_id: int,
    business_connection_id: Optional[str] = None,
) -> bool:
    """
    Вызывается при обнаружении поддерживаемой ссылки.
    Пытается отредактировать сообщение с добавлением инлайн-кнопок или отправляет меню ответом.
    """
    extracted = extract_supported_url(raw_message_text)
    if not extracted:
        return False

    url, platform, icon = extracted

    # Получаем базовую информацию о видео
    loop = asyncio.get_running_loop()
    info = await loop.run_in_executor(None, fetch_media_info_sync, url)

    title = info.get("title") or "Медиафайл"
    duration = info.get("duration") or ""

    session_id = _make_session_id(user_id, url)
    quality = get_user_quality(conn, user_id)

    _ACTIVE_SESSIONS[session_id] = {
        "user_id": user_id,
        "url": url,
        "platform": platform,
        "icon": icon,
        "title": title,
        "duration": duration,
        "quality": quality,
        "chat_id": chat_id,
        "business_connection_id": business_connection_id,
    }

    panel_text, keyboard = build_download_panel(
        conn, user_id, session_id, platform, icon, url, title, duration, quality
    )

    # 1. Если сообщение отправлено от бизнес-аккаунта
    if business_connection_id:
        try:
            # Пробуем отредактировать исходное сообщение
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=panel_text,
                reply_markup=keyboard,
                business_connection_id=business_connection_id,
                parse_mode="Markdown",
            )
            return True
        except Exception as e:
            log.debug("edit_message_text in business failed (%s), fallback to separate message", e)
            # В бизнес-сообщениях Telegram может не разрешать inline keyboard
            # В этом случае отправляем отдельное интерактивное сообщение
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=panel_text,
                    reply_markup=keyboard,
                    business_connection_id=business_connection_id,
                    parse_mode="Markdown",
                )
                return True
            except Exception as e2:
                log.warning("send_message in business with keyboard failed (%s), notifying owner in PM", e2)
                # Если и это заблокировано политикой Telegram Business, отправляем панель владельцу в ЛС
                try:
                    await bot.send_message(
                        chat_id=user_id,
                        text=f"🎬 *Обнаружена ссылка на {platform} в бизнес-чате!*\n\n{panel_text}",
                        reply_markup=keyboard,
                        parse_mode="Markdown",
                    )
                    return True
                except Exception:
                    pass
        return True

    # 2. Обычный диалог в ЛС с ботом
    try:
        # Пробуем отредактировать сообщение (если возможно)
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=panel_text,
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
    except Exception:
        # Если редактирование сообщения пользователя невозможно в Telegram, отправляем ответом
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=panel_text,
                reply_markup=keyboard,
                reply_to_message_id=message_id,
                parse_mode="Markdown",
            )
        except Exception as e:
            log.error("Failed to send download panel in private chat: %s", e)

    return True
