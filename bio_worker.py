# bio_worker.py
"""Воркер управления описанием (био) бизнес-профиля и наложением водяного знака @rad1kal_bot."""

import asyncio
import logging
import sqlite3
from typing import Optional

import tn_db
from premium_db import is_premium, save_original_bio, get_bio_settings, get_all_bio_settings
from timezone import now_iso

log = logging.getLogger(__name__)

WATERMARK_SUFFIX = " | user @rad1kal_bot"
WATERMARK_TAG = "user @rad1kal_bot"
MAX_BIO_LENGTH = 140


async def set_business_account_bio(
    bot,
    business_connection_id: str,
    bio: str,
) -> bool:
    """Вызывает метод Telegram Bot API setBusinessAccountBio через do_api_request."""
    try:
        return await bot.do_api_request(
            "setBusinessAccountBio",
            api_kwargs={
                "business_connection_id": business_connection_id,
                "bio": bio,
            },
        )
    except Exception as e:
        log.warning("setBusinessAccountBio failed для %s: %s", business_connection_id, e)
        return False


async def apply_bio_for_connection(bot, conn: sqlite3.Connection, bc_id: str, user_id: Optional[int] = None):
    """
    Проверяет статус подписки и устанавливает био:
    - Без премиума: добавляет '<описание | user @rad1kal_bot>'
    - С премиумом: убирает водяной знак и возвращает исходное описание
    """
    settings = get_bio_settings(conn, bc_id)
    uid = user_id or (settings.get("user_id") if settings else None)

    if not uid:
        # Пробуем найти через активные подключения
        return

    has_prem = is_premium(conn, uid)
    original_bio = settings.get("original_bio", "") if settings else ""
    current_bio = settings.get("current_bio", "") if settings else ""

    if has_prem:
        # ПРЕМИУМ: убираем водяной знак, если он был
        if WATERMARK_TAG in (current_bio or ""):
            clean_bio = original_bio or current_bio.replace(WATERMARK_SUFFIX, "").replace(WATERMARK_TAG, "").strip()
            ok = await set_business_account_bio(bot, bc_id, clean_bio)
            if ok:
                conn.execute(
                    "UPDATE bio_settings SET current_bio = ?, last_watermark_update = ? WHERE business_connection_id = ?",
                    (clean_bio, now_iso(), bc_id),
                )
                conn.commit()
                log.info(f"Премиум: водяной знак снят для {bc_id}")
    else:
        # БЕСПЛАТНЫЙ: добавляем водяной знак
        if not current_bio or WATERMARK_TAG not in current_bio:
            base_text = original_bio or current_bio or ""
            max_len = MAX_BIO_LENGTH - len(WATERMARK_SUFFIX)
            trimmed_base = base_text.strip()[:max_len]
            target_bio = f"{trimmed_base}{WATERMARK_SUFFIX}" if trimmed_base else WATERMARK_TAG

            ok = await set_business_account_bio(bot, bc_id, target_bio)
            if ok:
                save_original_bio(conn, bc_id, uid, base_text)
                conn.execute(
                    "UPDATE bio_settings SET current_bio = ?, last_watermark_update = ? WHERE business_connection_id = ?",
                    (target_bio, now_iso(), bc_id),
                )
                conn.commit()
                log.info(f"Установлен водяной знак в био для {bc_id}: {target_bio}")


async def on_business_connection_update(update, context, conn: sqlite3.Connection):
    """Срабатывает мгновенно при подключении бота к Telegram Business."""
    bc = update.business_connection
    if not bc:
        return

    bc_id = bc.id
    user_id = bc.user.id
    first_name = bc.user.first_name or ""

    # Сохраняем подключение в tn_db
    tn_db.save_first_name(conn, bc_id, first_name)

    # Пробуем получить текущее описание профиля пользователя
    existing_bio = ""
    try:
        chat_info = await context.bot.get_chat(bc.user_chat_id or user_id)
        existing_bio = chat_info.bio or ""
    except Exception as e:
        log.warning(f"Не удалось получить bio пользователя через get_chat: {e}")

    # Сохраняем исходное био
    save_original_bio(conn, bc_id, user_id, existing_bio)

    # Немедленно применяем правила для био
    await apply_bio_for_connection(context.bot, conn, bc_id, user_id)


async def bio_worker(app, conn: sqlite3.Connection, interval_seconds: int = 300):
    """
    Фоновый воркер: каждые 5 минут (300 секунд) проверяет описания
    и поддерживает актуальный водяной знак у бесплатных пользователей.
    """
    log.info("Воркер описания профиля (каждые 5 минут) запущен")

    while True:
        try:
            connections = tn_db.get_all_connections(conn)
            for c in connections:
                bc_id = c["business_connection_id"]
                b_settings = get_bio_settings(conn, bc_id)
                uid = b_settings.get("user_id") if b_settings else None

                if bc_id:
                    await apply_bio_for_connection(app.bot, conn, bc_id, uid)

        except Exception as e:
            log.error(f"bio_worker ошибка: {e}")

        await asyncio.sleep(interval_seconds)
