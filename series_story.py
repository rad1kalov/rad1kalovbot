# series_story.py
"""
Генерация юбилейных карточек серий для выкладки в Telegram Stories (1080x1920)
через HTML-рендер в строгом соответствии с утверждённым дизайн-макетом:
- Тёмный фон с неоновыми фиолетовыми градиентными свечениями
- Заголовок: СЕРИЯ ОБЩЕНИЯ / МЫ УЛУЧШИЛИ СВОЙ ЗНАЧОК
- Диагональное расположение двух участников с аватарками и никнеймами
- Элегантная неоновая фиолетовая волнистая линия между ними
- Аккуратный бейдж стадии (СТАДИЯ)
- Крупный неоновый счетчик дней общения (100) и подпись (дней)
- Подпись внизу: серии в тг: @rad1kal_bot
"""

import asyncio
import base64
import html
import io
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
from typing import Optional, Tuple

from PIL import Image

from series import plural_days
from stages import get_stage_by_days, normalize_stage, stage_name

log = logging.getLogger(__name__)

OWNER_ID = int(os.getenv("OWNER_ID", "8470104943"))
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_font_b64(filename: str) -> str:
    """Загружает TTF шрифт из папки fonts/ и кодирует в base64 для чистого офлайн-рендера в HTML."""
    path = os.path.join(_BASE_DIR, "fonts", filename)
    if os.path.exists(path):
        try:
            with open(path, "rb") as f:
                return base64.b64encode(f.read()).decode("ascii")
        except Exception as e:
            log.warning("Ошибка чтения шрифта %s: %s", path, e)
    return ""


# Предзагрузка шрифтов Montserrat для мгновенного встраивания в HTML
_MONT_REGULAR_B64 = _load_font_b64("Montserrat-Regular.ttf")
_MONT_BOLD_B64 = _load_font_b64("Montserrat-Bold.ttf")
_MONT_EXTRABOLD_B64 = _load_font_b64("Montserrat-ExtraBold.ttf")


def get_browser_executable() -> Optional[str]:
    """
    Находит доступный исполняемый файл Chrome / Chromium / Edge для рендера HTML:
    1. Переменные окружения (CHROME_BIN, CHROMIUM_BIN, BROWSER_BIN)
    2. Системный PATH
    3. Стандартные пути Linux / Docker
    4. Стандартные пути Windows
    """
    for env_var in ("CHROME_BIN", "CHROMIUM_BIN", "BROWSER_BIN"):
        val = os.getenv(env_var)
        if val and os.path.exists(val):
            return val

    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "chrome", "msedge"):
        p = shutil.which(name)
        if p and os.path.exists(p):
            return p

    linux_paths = [
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/snap/bin/chromium",
    ]
    for p in linux_paths:
        if os.path.exists(p):
            return p

    win_paths = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for p in win_paths:
        if os.path.exists(p):
            return p

    return None


def render_html_to_png(html_content: str) -> bytes:
    """
    Рендерит HTML-код в кристальное PNG изображение 1080x1920 через headless-браузер.
    """
    browser_exe = get_browser_executable()
    if not browser_exe:
        raise RuntimeError("Браузер для рендера HTML не найден (установите Google Chrome или Chromium).")

    with tempfile.TemporaryDirectory(prefix="story_render_") as tmp_dir:
        html_file = os.path.join(tmp_dir, "card.html")
        png_file = os.path.join(tmp_dir, "card.png")

        with open(html_file, "w", encoding="utf-8") as f:
            f.write(html_content)

        norm_path = os.path.abspath(html_file).replace("\\", "/")
        file_url = f"file:///{norm_path}"

        cmd = [
            browser_exe,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--window-size=1080,1920",
            "--hide-scrollbars",
            "--default-background-color=00000000",
            f"--screenshot={png_file}",
            file_url,
        ]

        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=25)
        if res.returncode != 0 and not os.path.exists(png_file):
            # Фоллбэк на классический ключ --headless для старых версий Chromium
            cmd[1] = "--headless"
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=25)

        if not os.path.exists(png_file) or os.path.getsize(png_file) == 0:
            err = res.stderr.decode("utf-8", errors="ignore")
            raise RuntimeError(f"Не удалось получить скриншот HTML-карточки: {err}")

        with open(png_file, "rb") as f:
            return f.read()


async def _get_user_avatar_base64(bot, user_id: int, size: int = 320) -> Optional[str]:
    """Скачивает аватар пользователя через Telegram Bot API и возвращает data URI в JPEG."""
    if not user_id:
        return None
    try:
        photos = await bot.get_user_profile_photos(user_id=user_id, limit=1)
        if photos and photos.total_count > 0 and photos.photos:
            photo = photos.photos[0][-1]
            file_obj = await bot.get_file(photo.file_id)
            buf = io.BytesIO()
            await file_obj.download_to_memory(buf)
            buf.seek(0)

            img = Image.open(buf).convert("RGB")
            w, h = img.size
            min_side = min(w, h)
            left = (w - min_side) // 2
            top = (h - min_side) // 2
            img = img.crop((left, top, left + min_side, top + min_side))
            img = img.resize((size, size), Image.Resampling.LANCZOS)

            out_buf = io.BytesIO()
            img.save(out_buf, format="JPEG", quality=92)
            out_buf.seek(0)
            b64_str = base64.b64encode(out_buf.read()).decode("ascii")
            return f"data:image/jpeg;base64,{b64_str}"
    except Exception as e:
        log.debug("Не удалось скачать аватар для user_id %s: %s", user_id, e)
    return None


async def resolve_story_participants(
    bot,
    conn: sqlite3.Connection,
    chat_id: int,
    s: Optional[dict] = None,
    current_user_id: Optional[int] = None,
    current_user_name: Optional[str] = None,
) -> Tuple[int, str, int, str]:
    """
    Надёжно определяет двух участников для юбилейной карточки:
    1. Владелец бизнес-аккаунта / серии (user1_id, user1_name)
    2. Собеседник в чате (user2_id, user2_name)
    """
    owner_id = 0
    bc_id = s.get("business_connection_id") if s else None

    # 1. Определяем ID владельца аккаунта
    if bc_id:
        try:
            row = conn.execute("SELECT user_id FROM settings WHERE business_connection_id = ?", (bc_id,)).fetchone()
            if row and row[0]:
                owner_id = int(row[0])
            if not owner_id:
                row2 = conn.execute("SELECT user_id FROM bio_settings WHERE business_connection_id = ?", (bc_id,)).fetchone()
                if row2 and row2[0]:
                    owner_id = int(row2[0])
        except Exception as e:
            log.debug("Ошибка поиска владельца по bc_id %s: %s", bc_id, e)

    if not owner_id:
        owner_id = current_user_id or OWNER_ID

    # 2. Определяем имя владельца
    owner_name = ""
    if owner_id:
        try:
            owner_chat = await bot.get_chat(owner_id)
            owner_name = owner_chat.full_name or owner_chat.first_name or owner_chat.username or ""
        except Exception:
            pass

    if not owner_name:
        try:
            row = conn.execute("SELECT first_name FROM settings WHERE user_id = ?", (owner_id,)).fetchone()
            if row and row[0]:
                owner_name = str(row[0])
        except Exception:
            pass

    if not owner_name:
        if current_user_id == owner_id and current_user_name:
            owner_name = current_user_name
        else:
            owner_name = "Вы"

    # 3. Определяем ID собеседника (user2)
    partner_id = 0
    if chat_id > 0 and chat_id != owner_id:
        partner_id = chat_id
    elif chat_id < 0:
        try:
            row = conn.execute("""
                SELECT from_user_id FROM messages 
                WHERE chat_id = ? AND from_user_id != ? AND from_user_id != 0 
                ORDER BY message_id DESC LIMIT 1
            """, (chat_id, owner_id)).fetchone()
            if row and row[0]:
                partner_id = int(row[0])
        except Exception:
            pass

    # 4. Определяем имя собеседника
    partner_name = ""
    if partner_id and partner_id != owner_id:
        try:
            p_chat = await bot.get_chat(partner_id)
            partner_name = p_chat.full_name or p_chat.first_name or p_chat.username or ""
        except Exception:
            pass

    if not partner_name:
        try:
            row = conn.execute("""
                SELECT from_user_name FROM messages 
                WHERE chat_id = ? AND from_user_id != ? AND from_user_name IS NOT NULL AND from_user_name != ''
                ORDER BY message_id DESC LIMIT 1
            """, (chat_id, owner_id)).fetchone()
            if row and row[0]:
                partner_name = str(row[0])
        except Exception:
            pass

    if not partner_name:
        candidate = (s.get("last_user_name") or "").strip() if s else ""
        if candidate and candidate.lower() != owner_name.lower():
            partner_name = candidate

    # 5. Защита от дублирования
    if not partner_name or partner_name.strip().lower() == owner_name.strip().lower():
        partner_name = "Собеседник"

    return owner_id, owner_name, partner_id, partner_name


def _build_html_markup(
    user1_name: str,
    user1_username: str,
    user1_avatar_uri: Optional[str],
    user2_name: str,
    user2_username: str,
    user2_avatar_uri: Optional[str],
    streak: int,
    stage_text: str,
    hide_stage: bool,
    days_word: str,
) -> str:
    """Формирует готовый HTML-документ с точной версткой по утвержденному дизайну."""
    font_faces = ""
    if _MONT_REGULAR_B64:
        font_faces += f"""
        @font-face {{
          font-family: 'StoryFont';
          src: url('data:font/truetype;base64,{_MONT_REGULAR_B64}') format('truetype');
          font-weight: 400 500;
        }}
        """
    if _MONT_BOLD_B64:
        font_faces += f"""
        @font-face {{
          font-family: 'StoryFont';
          src: url('data:font/truetype;base64,{_MONT_BOLD_B64}') format('truetype');
          font-weight: 700;
        }}
        """
    if _MONT_EXTRABOLD_B64:
        font_faces += f"""
        @font-face {{
          font-family: 'StoryFont';
          src: url('data:font/truetype;base64,{_MONT_EXTRABOLD_B64}') format('truetype');
          font-weight: 800 900;
        }}
        """

    # Аватары (картинка или стильный градиентный круг с первой буквой)
    if user1_avatar_uri:
        av1_html = f'<img class="avatar" src="{user1_avatar_uri}" alt="avatar1">'
    else:
        init1 = (user1_name.strip()[:1] if user1_name else "?").upper()
        av1_html = f'<div class="avatar fallback" style="background: linear-gradient(135deg, #8b5cf6, #ec4899);">{html.escape(init1)}</div>'

    if user2_avatar_uri:
        av2_html = f'<img class="avatar" src="{user2_avatar_uri}" alt="avatar2">'
    else:
        init2 = (user2_name.strip()[:1] if user2_name else "?").upper()
        av2_html = f'<div class="avatar fallback" style="background: linear-gradient(135deg, #0ea5e9, #6366f1);">{html.escape(init2)}</div>'

    stage_pill_html = f'<div class="stage-pill">{html.escape(stage_text)}</div>' if not hide_stage else ""

    u1_name_esc = html.escape(user1_name[:18])
    u1_user_esc = html.escape(user1_username[:22])
    u2_name_esc = html.escape(user2_name[:18])
    u2_user_esc = html.escape(user2_username[:22])

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<style>
  {font_faces}

  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    width: 1080px;
    height: 1920px;
    background-color: #06040a;
    background-image: 
      radial-gradient(circle at 0% 12%, rgba(135, 25, 220, 0.46) 0%, rgba(65, 10, 115, 0.24) 36%, transparent 66%),
      radial-gradient(circle at 100% 88%, rgba(125, 18, 205, 0.44) 0%, rgba(55, 6, 100, 0.20) 36%, transparent 66%);
    color: #ffffff;
    font-family: 'StoryFont', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'DejaVu Sans', sans-serif;
    position: relative;
    overflow: hidden;
    -webkit-font-smoothing: antialiased;
  }}

  /* Шапка */
  .header {{
    position: absolute;
    top: 90px;
    left: 65px;
  }}
  .header h1 {{
    font-size: 68px;
    font-weight: 800;
    letter-spacing: -0.5px;
    text-transform: uppercase;
    color: #ffffff;
    line-height: 1.1;
  }}
  .header h2 {{
    font-size: 32px;
    font-weight: 700;
    color: #726a80;
    letter-spacing: 0.8px;
    text-transform: uppercase;
    margin-top: 12px;
  }}

  /* Участник 1 (слева вверху) */
  .user-1 {{
    position: absolute;
    top: 260px;
    left: 55px;
    display: flex;
    align-items: center;
    gap: 32px;
  }}
  .avatar {{
    width: 320px;
    height: 320px;
    border-radius: 50%;
    object-fit: cover;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 130px;
    font-weight: 800;
    color: #ffffff;
    box-shadow: 0 15px 45px rgba(0, 0, 0, 0.6);
    flex-shrink: 0;
  }}
  .user-info {{
    display: flex;
    flex-direction: column;
  }}
  .user-info .nickname {{
    font-size: 46px;
    font-weight: 800;
    color: #ffffff;
    text-transform: uppercase;
    line-height: 1.15;
    letter-spacing: 0.2px;
    max-width: 440px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }}
  .user-info .username {{
    font-size: 28px;
    font-weight: 500;
    color: #8c839d;
    margin-top: 8px;
  }}

  /* Фиолетовая соединительная волна */
  .wave-svg {{
    position: absolute;
    top: 0;
    left: 0;
    width: 1080px;
    height: 1920px;
    pointer-events: none;
    z-index: 5;
  }}

  /* Участник 2 (справа внизу) */
  .user-2 {{
    position: absolute;
    top: 570px;
    right: 55px;
    display: flex;
    align-items: center;
    gap: 32px;
    text-align: right;
  }}
  .user-2 .user-info {{
    align-items: flex-end;
  }}

  /* Центральный блок: стадия и счетчик */
  .counter-section {{
    position: absolute;
    top: 1060px;
    left: 0;
    width: 1080px;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
  }}

  .stage-pill {{
    display: inline-flex;
    align-items: center;
    justify-content: center;
    padding: 12px 42px;
    border-radius: 9999px;
    background: rgba(35, 10, 55, 0.65);
    border: 2px solid rgba(168, 85, 247, 0.65);
    color: #ffffff;
    font-size: 26px;
    font-weight: 700;
    letter-spacing: 2px;
    text-transform: uppercase;
    box-shadow: 0 4px 25px rgba(168, 85, 247, 0.25);
  }}

  .days-number {{
    font-size: 380px;
    font-weight: 900;
    color: #c544ff;
    line-height: 0.85;
    letter-spacing: -8px;
    margin-top: 35px;
    text-shadow: 0 10px 60px rgba(197, 68, 255, 0.35);
  }}

  .days-text {{
    font-size: 115px;
    font-weight: 800;
    color: #ffffff;
    line-height: 1.0;
    letter-spacing: -2px;
    margin-top: 12px;
  }}

  /* Футер */
  .footer {{
    position: absolute;
    bottom: 65px;
    left: 0;
    width: 1080px;
    text-align: center;
    font-size: 28px;
    font-weight: 600;
    color: #ffffff;
    letter-spacing: 0.5px;
    opacity: 0.95;
  }}
</style>
</head>
<body>
  <!-- Шапка -->
  <div class="header">
    <h1>СЕРИЯ ОБЩЕНИЯ</h1>
    <h2>МЫ УЛУЧШИЛИ СВОЙ ЗНАЧОК</h2>
  </div>

  <!-- Первый участник -->
  <div class="user-1">
    {av1_html}
    <div class="user-info">
      <div class="nickname">{u1_name_esc}</div>
      <div class="username">{u1_user_esc}</div>
    </div>
  </div>

  <!-- Соединительная волна -->
  <svg class="wave-svg" viewBox="0 0 1080 1920" fill="none" xmlns="http://www.w3.org/2000/svg">
    <path d="M 328 540 C 400 600, 480 575, 560 600 C 625 620, 665 645, 700 645" 
          stroke="#a855f7" stroke-width="3.5" stroke-linecap="round" />
  </svg>

  <!-- Второй участник -->
  <div class="user-2">
    <div class="user-info">
      <div class="nickname">{u2_name_esc}</div>
      <div class="username">{u2_user_esc}</div>
    </div>
    {av2_html}
  </div>

  <!-- Счетчик дней и стадия -->
  <div class="counter-section">
    {stage_pill_html}
    <div class="days-number">{streak}</div>
    <div class="days-text">{html.escape(days_word)}</div>
  </div>

  <!-- Подпись внизу -->
  <div class="footer">серии в тг: @rad1kal_bot</div>
</body>
</html>"""


async def create_story_card(
    bot,
    s: dict,
    user1_id: int,
    user1_name: str,
    user2_id: int,
    user2_name: str,
    user1_username: Optional[str] = None,
    user2_username: Optional[str] = None,
) -> io.BytesIO:
    """
    Генерирует готовую графическую открытку 1080x1920 для Telegram Stories
    через чистый HTML-рендер в формате PNG.
    """
    user1_name = (user1_name or "Пользователь").strip()
    user2_name = (user2_name or "Собеседник").strip()
    if user1_name and user2_name and user1_name.lower() == user2_name.lower():
        user2_name = "Собеседник"
    if user2_id == user1_id:
        user2_id = 0

    streak = s.get("current_streak", 0)
    raw_stage = s.get("stage")
    stage_key = normalize_stage(raw_stage)
    if (not raw_stage or stage_key == "spark") and streak >= 3 and s.get("auto_stage", 1):
        stage_key = get_stage_by_days(streak)

    custom_stage = s.get("custom_stage")
    hide_stage = bool(s.get("hide_stage", 0))

    if custom_stage:
        stage_text = str(custom_stage).upper()
    elif stage_key and stage_key != "spark":
        stage_text = stage_name(stage_key).upper()
    else:
        stage_text = "СТАДИЯ"

    days_word = plural_days(streak)

    # 1. Разрешение username для участников
    if not user1_username and user1_id:
        try:
            chat = await bot.get_chat(user1_id)
            if chat and chat.username:
                user1_username = f"@{chat.username}"
        except Exception:
            pass
    if not user1_username:
        clean1 = re.sub(r"[^a-zA-Z0-9_]", "", user1_name).lower()
        user1_username = f"@{clean1}" if clean1 else "@username"

    if not user2_username and user2_id:
        try:
            chat = await bot.get_chat(user2_id)
            if chat and chat.username:
                user2_username = f"@{chat.username}"
        except Exception:
            pass
    if not user2_username:
        clean2 = re.sub(r"[^a-zA-Z0-9_]", "", user2_name).lower()
        user2_username = f"@{clean2}" if clean2 else "@username"

    # 2. Скачивание аватарок параллельно
    av1_res, av2_res = await asyncio.gather(
        _get_user_avatar_base64(bot, user1_id, size=320),
        _get_user_avatar_base64(bot, user2_id, size=320),
        return_exceptions=True,
    )
    av1_b64 = av1_res if not isinstance(av1_res, Exception) else None
    av2_b64 = av2_res if not isinstance(av2_res, Exception) else None

    # 3. Формирование HTML
    html_markup = _build_html_markup(
        user1_name=user1_name,
        user1_username=user1_username,
        user1_avatar_uri=av1_b64,
        user2_name=user2_name,
        user2_username=user2_username,
        user2_avatar_uri=av2_b64,
        streak=streak,
        stage_text=stage_text,
        hide_stage=hide_stage,
        days_word=days_word,
    )

    # 4. Рендер HTML -> PNG в отдельном потоке
    loop = asyncio.get_running_loop()
    png_bytes = await loop.run_in_executor(None, render_html_to_png, html_markup)

    buf = io.BytesIO(png_bytes)
    buf.seek(0)
    return buf
