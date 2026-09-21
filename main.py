import asyncio
import logging
import os
import signal
import sqlite3
from datetime import datetime
from dotenv import load_dotenv

from telegram import Update
from telegram.ext import (
    Application,
    BusinessMessagesDeletedHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from series_db import init_series_db
from series import process_message, build_summary
from series_commands import handle_series_command
from series_worker import series_worker

load_dotenv()

# ─────────────────────────── КОНФИГУРАЦИЯ ───────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN")        # токен от @BotFather
OWNER_ID = 8470104943                  # ваш user_id (узнать у @userinfobot)
DB_PATH = "messages.db"
MEDIA_DIR = "downloads"
# ────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
os.makedirs(MEDIA_DIR, exist_ok=True)


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
    conn.commit()
    return conn


DB = init_db()
init_series_db(DB)   # ← добавить


# ─────────────────────────── СКАЧИВАНИЕ МЕДИА ───────────────────────
async def download_media(message, context: ContextTypes.DEFAULT_TYPE):
    """Скачивает медиа из сообщения и возвращает (путь, тип)."""
    attachment = message.effective_attachment
    if not attachment:
        return None, None

    # Для фото Telegram присылает tuple из PhotoSize — берём самый большой
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

    # Определяем тип
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
async def send_media_to_owner(context, media_path, media_type, caption):
    """Отправляет сохранённый файл владельцу в личку."""
    if not media_path or not os.path.exists(media_path):
        return
    try:
        with open(media_path, "rb") as f:
            if media_type == "photo":
                await context.bot.send_photo(OWNER_ID, f, caption=caption, parse_mode="Markdown")
            elif media_type == "video":
                await context.bot.send_video(OWNER_ID, f, caption=caption, parse_mode="Markdown")
            elif media_type == "voice":
                await context.bot.send_voice(OWNER_ID, f, caption=caption, parse_mode="Markdown")
            elif media_type == "video_note":
                await context.bot.send_video_note(OWNER_ID, f)
                if caption:
                    await context.bot.send_message(OWNER_ID, caption, parse_mode="Markdown")
            else:
                await context.bot.send_document(OWNER_ID, f, caption=caption, parse_mode="Markdown")
    except Exception as e:
        logging.error(f"send_media_to_owner failed: {e}")


# ─────────────── ОТПРАВКА ПО FILE_ID (НОВЫЙ ПОДХОД) ─────────────────
async def resend_by_file_id(context, replied, header: str):
    """
    Пытается переслать медиа, используя file_id из reply_to_message.
    Работает даже для одноразового медиа, если сервер отдал file_id.
    Возвращает True, если что-то отправили.
    """
    sent = False

    try:
        if replied.photo:
            file_id = replied.photo[-1].file_id
            await context.bot.send_photo(
                OWNER_ID, file_id,
                caption=f"{header}\n📸 (через reply / file_id)",
                parse_mode="Markdown",
            )
            sent = True

        elif replied.video:
            await context.bot.send_video(
                OWNER_ID, replied.video.file_id,
                caption=f"{header}\n🎥 (через reply / file_id)",
                parse_mode="Markdown",
            )
            sent = True

        elif replied.video_note:
            await context.bot.send_video_note(OWNER_ID, replied.video_note.file_id)
            await context.bot.send_message(
                OWNER_ID, f"{header}\n🎥 video_note (через reply / file_id)",
                parse_mode="Markdown",
            )
            sent = True

        elif replied.voice:
            await context.bot.send_voice(
                OWNER_ID, replied.voice.file_id,
                caption=f"{header}\n🎤 (через reply / file_id)",
                parse_mode="Markdown",
            )
            sent = True

        elif replied.audio:
            await context.bot.send_audio(
                OWNER_ID, replied.audio.file_id,
                caption=f"{header}\n🎵 (через reply / file_id)",
                parse_mode="Markdown",
            )
            sent = True

        elif replied.document:
            await context.bot.send_document(
                OWNER_ID, replied.document.file_id,
                caption=f"{header}\n📄 (через reply / file_id)",
                parse_mode="Markdown",
            )
            sent = True

    except Exception as e:
        logging.error(f"resend_by_file_id failed: {e}")

    return sent


# ─────────────────────── НОВОЕ БИЗНЕС-СООБЩЕНИЕ ─────────────────────
async def on_business_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.business_message
    if not msg:
        return

    # ───── НОВОЕ: обработка команд серии ─────
    if msg.text and msg.text.startswith("."):
        handled = await handle_series_command(update, context, DB)
        if handled:
            # всё равно сохраним команду в БД для истории
            # (можно пропустить, если не нужно)
            return
    # ─────────────────────────────────────────

    # ... существующий код ниже без изменений ...

    conn_id = msg.business_connection_id

    # Признак одноразового / spoiler-медиа
    is_ephemeral = bool(getattr(msg, "ttl", None) or getattr(msg, "has_media_spoiler", False))

    # ──────── НОВАЯ ЛОГИКА: ответ на сообщение ────────
    # Обрабатываем ТОЛЬКО если в цитируемом сообщении есть медиа
    if msg.reply_to_message and msg.reply_to_message.effective_attachment:
        replied = msg.reply_to_message
        replied_from = replied.from_user
        replied_name = replied_from.full_name if replied_from else "Unknown"
        replied_id = replied_from.id if replied_from else 0

        header = (
            f"📎 *Перехват через reply*\n"
            f"👤 Автор оригинала: {replied_name} (ID: {replied_id})\n"
            f"💬 Чат ID: {msg.chat_id}\n"
            f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"━━━━━━━━━━━━━━━"
        )

        # Пытаемся переслать через file_id
        file_sent = await resend_by_file_id(context, replied, header)

        # Если в replied есть подпись — тоже перешлём (текст без медиа уже отфильтрован)
        if replied.caption:
            try:
                await context.bot.send_message(
                    OWNER_ID,
                    f"{header}\n{replied.caption}",
                    parse_mode="Markdown",
                )
            except Exception as e:
                logging.error(f"send replied caption failed: {e}")

        # Если через file_id ничего не отправили — пробуем скачать как обычно
        if not file_sent:
            media_path, media_type = await download_media(replied, context)
            if media_path:
                await send_media_to_owner(
                    context, media_path, media_type,
                    header + "\n(скачано обычным способом)",
                )

    # ──────── СУЩЕСТВУЮЩАЯ ЛОГИКА ────────
    # Скачиваем медиа СРАЗУ (критично для одноразовых)
    media_path, media_type = None, None
    if msg.effective_attachment:
        media_path, media_type = await download_media(msg, context)

    from_user = msg.from_user
    from_name = from_user.full_name if from_user else "Unknown"
    from_id = from_user.id if from_user else 0
    text = msg.text or msg.caption or ""

    # Сохраняем в БД
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
        logging.info(f"Сохранено сообщение {msg.message_id} от {from_name} (ephemeral={is_ephemeral})")
    except Exception as e:
        logging.error(f"DB insert failed: {e}")
        return

    # Одноразовое медиа — сразу шлём владельцу
    if is_ephemeral and media_path:
        header = (
            f"🔥 *Одноразовое медиа*\n"
            f"👤 Автор: {from_name} (ID: {from_id})\n"
            f"💬 Чат ID: {msg.chat_id}\n"
            f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"━━━━━━━━━━━━━━━"
        )
        await send_media_to_owner(context, media_path, media_type, header)
        
    # ───── НОВОЕ: обновление серии ─────
    if not (msg.text and msg.text.startswith(".")):
        info = process_message(DB, msg.chat_id, msg.business_connection_id, from_name)
        if info:
            summary = build_summary(DB, msg.chat_id, info)
            try:
                await context.bot.send_message(
                    chat_id=msg.chat_id,
                    text=summary,
                    business_connection_id=msg.business_connection_id,
                    parse_mode="Markdown",
                )
            except Exception as e:
                logging.error(f"send series summary failed: {e}")
    # ───────────────────────────────────
# ─────────────────────── УДАЛЕНИЕ СООБЩЕНИЙ ─────────────────────────
async def on_deleted_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    deleted = update.deleted_business_messages
    if not deleted:
        return

    conn_id = deleted.business_connection_id

    for msg_id in deleted.message_ids:
        cur = DB.execute(
            "SELECT from_user_id, from_user_name, text, media_path, media_type, date, chat_id "
            "FROM messages WHERE message_id = ? AND chat_id = ?",
            (msg_id, deleted.chat.id),
        )
        row = cur.fetchone()
        if not row:
            logging.info(f"Удалено сообщение {msg_id}, но в БД нет записи")
            continue

        user_id, user_name, text, media_path, media_type, date, chat_id = row

        header = (
            f"🗑 *Удалено сообщение*\n"
            f"👤 Автор: {user_name} (ID: {user_id})\n"
            f"💬 Чат ID: {chat_id}\n"
            f"📅 Дата: {date}\n"
            f"━━━━━━━━━━━━━━━"
        )

        # Текст
        if text:
            try:
                await context.bot.send_message(
                    OWNER_ID,
                    f"{header}\n{text}",
                    parse_mode="Markdown",
                )
            except Exception as e:
                logging.error(f"send_message failed: {e}")

        # Медиа
        if media_path:
            await send_media_to_owner(context, media_path, media_type, header)


# ─────────────────────────── ЗАПУСК ─────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Новые бизнес-сообщения (включая ответы)
    app.add_handler(
        MessageHandler(filters.UpdateType.BUSINESS_MESSAGE, on_business_message)
    )
    # Удалённые бизнес-сообщения
    app.add_handler(BusinessMessagesDeletedHandler(on_deleted_messages))

    async def run():
        await app.initialize()
        await app.start()
        await app.updater.start_polling()
        
        worker_task = asyncio.create_task(series_worker(app, DB))

        stop_event = asyncio.Event()

        def _stop(*_):
            stop_event.set()

        loop = asyncio.get_running_loop()
        try:
            loop.add_signal_handler(signal.SIGINT, _stop)
            loop.add_signal_handler(signal.SIGTERM, _stop)
        except NotImplementedError:
            # Windows
            signal.signal(signal.SIGINT, lambda *_: loop.call_soon_threadsafe(stop_event.set))

        logging.info("Бот запущен. Ждём события…")
        await stop_event.wait()
        
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass

        logging.info("Останавливаюсь…")
        await app.updater.stop()
        await app.stop()
        await app.shutdown()

    asyncio.run(run())


if __name__ == "__main__":
    main()