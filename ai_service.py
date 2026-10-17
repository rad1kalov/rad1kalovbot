# ai_service.py
"""
Модуль интеграции с OpenAI API (модель gpt-6-astra):
- Команда: .ai <запрос> (также /ai)
- Лимиты:
  • Бесплатно: 2 ответа в календарный месяц
  • С Премиумом: безлимитные ответы
- Поддержка бизнес-чатов (business_connection_id) и личных сообщений
- Поддержка работы через ответ (reply) на сообщение
"""

import asyncio
import html
import logging
import os
import re
import sqlite3
from datetime import datetime
from typing import Optional, Tuple, Any, List

from telegram import Update
from telegram.ext import ContextTypes

from premium_db import is_premium
from timezone import now, now_iso

log = logging.getLogger(__name__)

MAX_FREE_MONTHLY_AI_REQUESTS = 2
DEFAULT_AI_MODEL = os.getenv("OPENAI_MODEL", "gpt-6-astra")


# ─────────────────────────── БАЗА ДАННЫХ ЛИМИТОВ ───────────────────────────

def init_ai_db(conn: sqlite3.Connection) -> None:
    """Инициализация таблицы истории запросов к ИИ."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ai_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            prompt TEXT,
            response TEXT,
            created_at TEXT,
            month_key TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_usage_user_month ON ai_usage(user_id, month_key)")
    conn.commit()


def get_month_key() -> str:
    """Возвращает ключ текущего месяца, например '2026-10'."""
    return datetime.now().strftime("%Y-%m")


def get_user_monthly_ai_count(conn: sqlite3.Connection, user_id: int) -> int:
    """Возвращает количество использованных запросов за текущий календарный месяц."""
    m_key = get_month_key()
    row = conn.execute(
        "SELECT COUNT(*) FROM ai_usage WHERE user_id = ? AND month_key = ?",
        (user_id, m_key),
    ).fetchone()
    return int(row[0]) if row else 0


def check_ai_limit(conn: sqlite3.Connection, user_id: int) -> Tuple[bool, int, int]:
    """
    Проверяет лимит запросов к ИИ:
    Возвращает (can_request, current_count, max_limit).
    Для премиум-пользователей: (True, current_count, -1).
    """
    cnt = get_user_monthly_ai_count(conn, user_id)
    if is_premium(conn, user_id):
        return True, cnt, -1
    return (cnt < MAX_FREE_MONTHLY_AI_REQUESTS), cnt, MAX_FREE_MONTHLY_AI_REQUESTS


def record_ai_usage(
    conn: sqlite3.Connection,
    user_id: int,
    prompt: str,
    response: str,
) -> None:
    """Сохраняет запись об успешном обращении к ИИ."""
    m_key = get_month_key()
    conn.execute("""
        INSERT INTO ai_usage (user_id, prompt, response, created_at, month_key)
        VALUES (?, ?, ?, ?, ?)
    """, (user_id, prompt[:1000], response[:2000], now_iso(), m_key))
    conn.commit()


# ─────────────────────────── ВЗАИМОДЕЙСТВИЕ С OPENAI ────────────────────────

def query_openai_sync(prompt: str, model: str = DEFAULT_AI_MODEL) -> str:
    """
    Синхронный вызов OpenAI API с использованием Responses API:
    client = OpenAI()
    response = client.responses.create(model="gpt-6-astra", input=prompt)
    print(response.output_text)
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError(
            "API-ключ OpenAI не настроен!\n"
            "Добавьте строку OPENAI_API_KEY=ваш_ключ в файл .env на сервере."
        )

    base_url = os.getenv("OPENAI_BASE_URL") or os.getenv("OPENAI_API_BASE")

    from openai import OpenAI
    client_kwargs = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url

    client = OpenAI(**client_kwargs)

    # 1. Пробуем современный Responses API (как в ТЗ)
    try:
        res = client.responses.create(model=model, input=prompt)
        if hasattr(res, "output_text") and res.output_text:
            return res.output_text
        # Дополнительная проверка на случай списочной структуры output
        if hasattr(res, "output") and res.output:
            texts = []
            for item in res.output:
                if hasattr(item, "content"):
                    texts.append(str(item.content))
                elif isinstance(item, dict) and "content" in item:
                    texts.append(str(item["content"]))
            if texts:
                return "\n".join(texts)
    except Exception as e_resp:
        log.warning("client.responses.create error (%s), fallback to chat.completions.create", e_resp)

    # 2. Резервный fallback через chat.completions.create
    try:
        res_chat = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )
        ans = res_chat.choices[0].message.content
        if ans:
            return ans
    except Exception as e_chat:
        # Если модель gpt-6-astra неизвестна обычному эндпоинту completions, пробуем gpt-4o-mini
        err_str = str(e_chat).lower()
        if "model" in err_str or "not found" in err_str:
            try:
                res_fb = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                )
                if res_fb.choices[0].message.content:
                    return res_fb.choices[0].message.content
            except Exception:
                pass
        raise RuntimeError(f"{e_chat}")

    return "Пустой ответ от нейросети."


def _split_text(text: str, max_length: int = 3900) -> List[str]:
    """Разбивает длинный текст на части с учётом абзацев для лимитов Telegram (4096 символов)."""
    if len(text) <= max_length:
        return [text]

    chunks = []
    current = []
    current_len = 0

    paragraphs = text.split("\n")
    for para in paragraphs:
        if current_len + len(para) + 1 > max_length:
            if current:
                chunks.append("\n".join(current))
                current = []
                current_len = 0
            if len(para) > max_length:
                # Если отдельный абзац длиннее лимита, режем по словам
                words = para.split(" ")
                w_curr = []
                w_len = 0
                for w in words:
                    if w_len + len(w) + 1 > max_length:
                        chunks.append(" ".join(w_curr))
                        w_curr = [w]
                        w_len = len(w)
                    else:
                        w_curr.append(w)
                        w_len += len(w) + 1
                if w_curr:
                    current.append(" ".join(w_curr))
                    current_len += w_len
            else:
                current.append(para)
                current_len += len(para) + 1
        else:
            current.append(para)
            current_len += len(para) + 1

    if current:
        chunks.append("\n".join(current))

    return chunks


# ─────────────────────────── ОБРАБОТЧИК КОМАНДЫ .AI ─────────────────────────

async def handle_ai_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    conn: sqlite3.Connection,
) -> bool:
    """
    Обрабатывает команды .ai / /ai в любых чатах:
    - Бизнес-чаты
    - Личные сообщения с ботом
    Возвращает True, если сообщение было распознано как обращение к ИИ.
    """
    msg = update.effective_message
    if not msg or not msg.text:
        return False

    raw_text = msg.text.strip()
    match = re.match(r"^[./]ai\b(?:\s+(.*))?", raw_text, re.IGNORECASE | re.DOTALL)
    if not match:
        return False

    prompt = (match.group(1) or "").strip()
    from_user = msg.from_user
    user_id = from_user.id if from_user else 0
    chat_id = msg.chat_id
    bc_id = getattr(msg, "business_connection_id", None)

    # В бизнес-чатах команда разрешена ТОЛЬКО от владельца бизнес-аккаунта.
    # Если бот подключен у обоих собеседников, бот второго собеседника не должен реагировать и тратить токены.
    if bc_id:
        from permissions_check import is_own_business_message
        is_own = await is_own_business_message(context.bot, conn, msg)
        if not is_own:
            log.info("handle_ai_command: команда .ai проигнорирована: отправлена собеседником (%s)", user_id)
            return False

    # Если текст запроса пуст, но сообщение отправлено в ответ на другое
    if not prompt and msg.reply_to_message:
        replied = msg.reply_to_message
        prompt = (replied.text or replied.caption or "").strip()

    # Если запрос всё ещё пуст — отправляем понятную подсказку с текущим лимитом
    if not prompt:
        can_use, cur_cnt, max_cnt = check_ai_limit(conn, user_id)
        limit_str = "Безлимит 👑 (Премиум)" if max_cnt == -1 else f"{cur_cnt}/{max_cnt} в этом месяце"
        tip = (
            "🤖 <b>Нейросеть в чате</b> <code>[gpt-6-astra]</code>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Задайте любой вопрос или дайте задачу искусственному интеллекту.\n\n"
            "💡 <b>Примеры использования:</b>\n"
            "• <code>.ai Напиши сказку на ночь про единорога</code>\n"
            "• <code>.ai Реши задачу по физике: ...</code>\n"
            "• Ответьте командой <code>.ai</code> на любое сообщение собеседника\n\n"
            f"📊 <b>Ваш лимит:</b> {limit_str}\n"
            "👑 <i>С Премиум-подпиской (/premium) доступен безлимит!</i>"
        )
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=tip,
                business_connection_id=bc_id,
                parse_mode="HTML",
                reply_to_message_id=msg.message_id,
            )
        except Exception as e:
            log.error("send ai tip failed: %s", e)
        return True

    # Проверяем лимит
    can_use, cur_cnt, max_cnt = check_ai_limit(conn, user_id)
    if not can_use:
        limit_msg = (
            "🔒 <b>Лимит бесплатных запросов к ИИ исчерпан!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"Вы использовали все <b>{max_cnt} из {max_cnt}</b> бесплатных запросов в этом месяце.\n\n"
            "👑 <b>Оформите Премиум (/premium)</b>, чтобы общаться с нейросетью без ограничений!"
        )
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=limit_msg,
                business_connection_id=bc_id,
                parse_mode="HTML",
                reply_to_message_id=msg.message_id,
            )
        except Exception as e:
            log.error("send ai limit msg failed: %s", e)
        return True

    # Отправляем статус генерации и показываем действие "печатает"
    try:
        await context.bot.send_chat_action(
            chat_id=chat_id,
            action="typing",
            business_connection_id=bc_id,
        )
    except Exception:
        pass

    status_msg = None
    try:
        status_msg = await context.bot.send_message(
            chat_id=chat_id,
            text="⏳ <i>Нейросеть генерирует ответ…</i>",
            business_connection_id=bc_id,
            parse_mode="HTML",
            reply_to_message_id=msg.message_id,
        )
    except Exception as e:
        log.debug("Could not send initial ai status message: %s", e)

    # Выполняем синхронный вызов OpenAI в отдельном пуле потоков
    loop = asyncio.get_running_loop()
    try:
        answer = await loop.run_in_executor(None, query_openai_sync, prompt)
    except Exception as err:
        log.error("OpenAI execution failed: %s", err)
        err_clean = str(err)
        err_msg = (
            "❌ <b>Не удалось получить ответ от нейросети</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ <code>{html.escape(err_clean)}</code>\n\n"
            "<i>Попробуйте повторить запрос немного позже или проверьте API-ключ.</i>"
        )
        if status_msg:
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=status_msg.message_id,
                    text=err_msg,
                    business_connection_id=bc_id,
                    parse_mode="HTML",
                )
                return True
            except Exception:
                pass
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=err_msg,
                business_connection_id=bc_id,
                parse_mode="HTML",
                reply_to_message_id=msg.message_id,
            )
        except Exception:
            pass
        return True

    # Фиксируем успешный запрос в базе данных
    record_ai_usage(conn, user_id, prompt, answer)

    # Формируем подпись с информацией о лимите
    new_cnt = cur_cnt + 1
    if max_cnt == -1:
        limit_footer = "👑 <i>Премиум: Безлимитные запросы</i>"
    else:
        limit_footer = f"📊 <i>Запросов: {new_cnt}/{max_cnt} в этом месяце</i>"

    # Делим на части, если ответ превышает лимит длины сообщения
    chunks = _split_text(answer, max_length=3700)

    for i, chunk in enumerate(chunks):
        if i == 0:
            header = f"🤖 <b>Ответ ИИ</b> <code>[{DEFAULT_AI_MODEL}]</code>\n━━━━━━━━━━━━━━━━━━━━\n"
            footer = f"\n━━━━━━━━━━━━━━━━━━━━\n{limit_footer}" if len(chunks) == 1 else ""
            part_text = f"{header}{html.escape(chunk)}{footer}"
        elif i == len(chunks) - 1:
            part_text = f"{html.escape(chunk)}\n━━━━━━━━━━━━━━━━━━━━\n{limit_footer}"
        else:
            part_text = html.escape(chunk)

        # Первую часть стараемся отредактировать в существующее сообщение статуса
        if i == 0 and status_msg:
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=status_msg.message_id,
                    text=part_text,
                    business_connection_id=bc_id,
                    parse_mode="HTML",
                )
                continue
            except Exception as e_edit:
                log.debug("edit_message_text for AI answer failed (%s), fallback to send", e_edit)

        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=part_text,
                business_connection_id=bc_id,
                parse_mode="HTML",
                reply_to_message_id=msg.message_id if i == 0 else None,
            )
        except Exception:
            # Fallback без разметки
            plain_part = re.sub(r"<[^>]+>", "", part_text)
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=plain_part,
                    business_connection_id=bc_id,
                    reply_to_message_id=msg.message_id if i == 0 else None,
                )
            except Exception as e_plain:
                log.error("send AI plain text failed: %s", e_plain)

    return True
