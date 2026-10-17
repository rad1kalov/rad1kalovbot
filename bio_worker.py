# bio_worker.py
"""Воркер управления описанием (био) бизнес-профиля и наложением водяного знака @rad1kal_bot."""

import asyncio
import logging
import sqlite3
from typing import Optional

import tn_db
from admin_db import is_admin, get_admin_tag_mode
from premium_db import is_premium, save_original_bio, get_bio_settings, get_all_bio_settings
from timezone import now_iso

log = logging.getLogger(__name__)

WATERMARK_TAG = "user @rad1kal_bot"
WATERMARK_SUFFIX = " | " + WATERMARK_TAG

ADMIN_TAG = "admin @rad1kal_bot"
ADMIN_SUFFIX = " | " + ADMIN_TAG

DEV_TAG = "dev @rad1kal_bot"
DEV_SUFFIX = " | " + DEV_TAG

ALL_WATERMARK_TAGS = [WATERMARK_TAG, ADMIN_TAG, DEV_TAG]
ALL_WATERMARK_SUFFIXES = [WATERMARK_SUFFIX, ADMIN_SUFFIX, DEV_SUFFIX]

MAX_BIO_LENGTH = 140


def get_user_bio_tag(conn: sqlite3.Connection, user_id: int) -> tuple[Optional[str], Optional[str]]:
    """
    Возвращает (tag, suffix) для пользователя или (None, None), если тег отключен/снят:
    - Для админов/создателя: настраиваемый режим ('admin', 'dev', 'user', 'off')
    - Для премиум-пользователей: (None, None)
    - Для обычных пользователей: ('user @rad1kal_bot', ' | user @rad1kal_bot')
    """
    if is_admin(conn, user_id):
        mode = get_admin_tag_mode(conn, user_id)
        if mode == "admin":
            return ADMIN_TAG, ADMIN_SUFFIX
        elif mode == "dev":
            return DEV_TAG, DEV_SUFFIX
        elif mode == "user":
            return WATERMARK_TAG, WATERMARK_SUFFIX
        else:
            return None, None

    if is_premium(conn, user_id):
        return None, None

    return WATERMARK_TAG, WATERMARK_SUFFIX


def strip_all_watermarks(text: str) -> str:
    """Удаляет любые теги бота из текста описания."""
    res = text or ""
    for suffix in ALL_WATERMARK_SUFFIXES:
        res = res.replace(suffix, "")
    for tag in ALL_WATERMARK_TAGS:
        res = res.replace(tag, "")
    return res.strip()


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
    Проверяет статус подписки и роль пользователя, затем устанавливает био:
    - Без премиума: добавляет '<описание | user @rad1kal_bot>'
    - Для админов/владельца: в зависимости от настройки admin_tag_settings (admin / dev / user / off)
    - С премиумом (обычный пользователь): убирает теги и возвращает исходное описание
    """
    settings = get_bio_settings(conn, bc_id)
    uid = user_id or (settings.get("user_id") if settings else None)

    if not uid:
        # Пробуем найти через активные подключения
        return

    # Если у пользователя активно авто-описание или парное био, ротацией управляет description_worker
    has_auto = conn.execute(
        "SELECT 1 FROM auto_descriptions WHERE user_id = ? AND is_active = 1",
        (uid,)
    ).fetchone()
    has_pair = conn.execute(
        "SELECT 1 FROM pair_bios WHERE (user_1 = ? OR user_2 = ?) AND status = 'active'",
        (uid, uid)
    ).fetchone()
    if has_auto or has_pair:
        return

    original_bio = settings.get("original_bio", "") if settings else ""
    current_bio = settings.get("current_bio", "") if settings else ""

    target_tag, target_suffix = get_user_bio_tag(conn, uid)

    if not target_tag:
        # Тег не требуется (Премиум или выключен админом)
        has_any_tag = any(tag in (current_bio or "") for tag in ALL_WATERMARK_TAGS)
        if has_any_tag:
            clean_bio = original_bio or strip_all_watermarks(current_bio)
            ok = await set_business_account_bio(bot, bc_id, clean_bio)
            if ok:
                conn.execute(
                    "UPDATE bio_settings SET current_bio = ?, last_watermark_update = ? WHERE business_connection_id = ?",
                    (clean_bio, now_iso(), bc_id),
                )
                conn.commit()
                log.info(f"Тег снят из био для {bc_id}")
    else:
        # Требуется установить target_tag / target_suffix
        # Проверяем, нужно ли обновить био
        needs_update = False
        if not current_bio or target_tag not in current_bio:
            needs_update = True
        else:
            # Возможно, установлен другой тег
            for other_tag in ALL_WATERMARK_TAGS:
                if other_tag != target_tag and other_tag in current_bio:
                    needs_update = True
                    break

        if needs_update:
            base_text = original_bio or strip_all_watermarks(current_bio) or ""
            max_len = MAX_BIO_LENGTH - len(target_suffix)
            trimmed_base = base_text.strip()[:max_len]
            target_bio = f"{trimmed_base}{target_suffix}" if trimmed_base else target_tag

            ok = await set_business_account_bio(bot, bc_id, target_bio)
            if ok:
                save_original_bio(conn, bc_id, uid, base_text)
                conn.execute(
                    "UPDATE bio_settings SET current_bio = ?, last_watermark_update = ? WHERE business_connection_id = ?",
                    (target_bio, now_iso(), bc_id),
                )
                conn.commit()
                log.info(f"Установлен тег в био для {bc_id}: {target_bio}")


async def on_business_connection_update(update, context, conn: sqlite3.Connection):
    """Срабатывает мгновенно при подключении бота к Telegram Business."""
    bc = update.business_connection
    if not bc:
        return

    bc_id = bc.id
    user_id = bc.user.id
    first_name = bc.user.first_name or ""

    if not getattr(bc, "is_enabled", False):
        tn_db.set_connection_inactive(conn, bc_id)
        log.info(f"Бизнес-подключение {bc_id} отключено пользователем {user_id}")
        return

    # Сохраняем подключение строго с привязкой к user_id как к первичному ключу
    tn_db.upsert_user_connection(conn, user_id=user_id, bc_id=bc_id, first_name=first_name, is_active=True)

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
    Фоновый воркер: каждые 5 минут проверяет описания профилей
    с пулом конкурентности Semaphore для поддержки тысяч пользователей.
    """
    log.info("Воркер описания профиля запущен")
    sem = asyncio.Semaphore(15)

    async def _safe_apply(bc_id: str, uid: Optional[int]):
        async with sem:
            try:
                await apply_bio_for_connection(app.bot, conn, bc_id, uid)
            except Exception as e:
                log.debug(f"Ошибка bio для {bc_id}: {e}")

    while True:
        try:
            connections = tn_db.get_all_connections(conn)
            tasks = []
            for c in connections:
                if c.get("is_active") == 0:
                    continue
                bc_id = c.get("business_connection_id")
                if not bc_id:
                    continue
                uid = c.get("user_id")
                tasks.append(_safe_apply(bc_id, uid))

            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

        except Exception as e:
            log.error(f"bio_worker ошибка: {e}")

        await asyncio.sleep(interval_seconds)
