# premium_commands.py
"""Интерфейс витрины Premium-подписки, оплата через Telegram Stars и заглушка для ЮKassa."""

import logging
import sqlite3
import time
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Update
from telegram.ext import ContextTypes

from premium_db import is_premium, get_premium_info, activate_premium, OWNER_ID
from bio_worker import apply_bio_for_connection
import tn_db

log = logging.getLogger(__name__)

# Тарифы подписки: (название, звезды XTR, рубли для ЮKassa, дней)
TARIFFS = {
    "1m": {
        "title": "1 месяц Premium",
        "stars": 150,
        "rub": "299 ₽",
        "days": 30,
        "desc": "Премиум-доступ на 30 дней: без водяного знака в био, авто-заморозка стриков и расширенные лимиты.",
    },
    "3m": {
        "title": "3 месяца Premium",
        "stars": 390,
        "rub": "799 ₽",
        "days": 90,
        "desc": "Премиум-доступ на 90 дней со скидкой 15%: всё включено.",
    },
    "lifetime": {
        "title": "Premium Навсегда",
        "stars": 990,
        "rub": "1990 ₽",
        "days": None,
        "desc": "Пожизненный доступ к премиум-функциям и обновлениям.",
    },
}


def build_premium_main_screen(conn: sqlite3.Connection, user_id: int):
    info = get_premium_info(conn, user_id)
    is_prem = info["is_premium"]

    if is_prem:
        status_line = f"АКТИВЕН ✅ ({info['plan']})\nСрок действия: *{info['expires_at']}*"
        footer = "🎉 *Все премиум-привилегии включены!* Водяной знак из вашего описания удалён."
    else:
        status_line = "НЕ АКТИВЕН ❌ (Бесплатный тариф)"
        footer = "Выберите подходящий тариф ниже, чтобы снять ограничения 👇"

    text = (
        "💎 *Telegram Business Premium*\n\n"
        f"Ваш статус подписки: *{status_line}*\n\n"
        "🚀 *Что даёт Премиум-подписка:*\n"
        "• 🚫 *Без водяного знака в описании:* убирает метку `| user @rad1kal_bot` из вашего профиля\n"
        "• 🛡 *Авто-заморозка серий:* в 23:30 бот сам спасёт ваш стрик общения, если вы забыли написать\n"
        "• 💫 *Расширенные лимиты:* 10 заморозок и 10 воскрешений в месяц без ограничения в 72 часа\n"
        "• ✏️ *Анти-редактирование:* сохранение оригиналов изменённых сообщений собеседника\n"
        "• 🕒 *Приоритетный воркер:* стабильное минутное время в имени\n\n"
        f"{footer}"
    )

    keyboard = [
        [
            InlineKeyboardButton("⭐️ 1 месяц — 150 Stars (299 ₽)", callback_data="prem:tier:1m"),
        ],
        [
            InlineKeyboardButton("⭐️ 3 месяца — 390 Stars (799 ₽)", callback_data="prem:tier:3m"),
        ],
        [
            InlineKeyboardButton("⭐️ Навсегда — 990 Stars (1990 ₽)", callback_data="prem:tier:lifetime"),
        ],
        [
            InlineKeyboardButton("⬅️ Назад в главное меню", callback_data="prem:back"),
        ],
    ]

    return text, InlineKeyboardMarkup(keyboard)


def build_payment_method_screen(tier_key: str):
    tier = TARIFFS.get(tier_key, TARIFFS["1m"])

    text = (
        f"💎 *Оплата подписки: {tier['title']}*\n\n"
        f"_{tier['desc']}_\n\n"
        "Выберите способ оплаты:\n"
        f"• **Telegram Stars:** {tier['stars']} ⭐ (моментальная оплата прямо в приложении)\n"
        f"• **ЮKassa:** {tier['rub']} (Банковские карты, СБП, Mir Pay)"
    )

    keyboard = [
        [
            InlineKeyboardButton(f"⭐ Оплатить Telegram Stars ({tier['stars']} ⭐)", callback_data=f"prem:pay:stars:{tier_key}"),
        ],
        [
            InlineKeyboardButton(f"💳 ЮKassa ({tier['rub']})", callback_data=f"prem:pay:yookassa:{tier_key}"),
        ],
        [
            InlineKeyboardButton("⬅️ Назад к тарифам", callback_data="prem:main"),
        ],
    ]

    return text, InlineKeyboardMarkup(keyboard)


async def premium_command(update: Update, context: ContextTypes.DEFAULT_TYPE, conn: sqlite3.Connection):
    """Открывает витрину подписки."""
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    text, keyboard = build_premium_main_screen(conn, user.id)
    await msg.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def handle_premium_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, conn: sqlite3.Connection) -> bool:
    """Обрабатывает выбор тарифов и способов оплаты."""
    query = update.callback_query
    if not query or not query.data or not query.data.startswith("prem:"):
        return False

    parts = query.data.split(":")
    action = parts[1]
    user_id = query.from_user.id if query.from_user else 0

    if action == "main":
        await query.answer()
        text, keyboard = build_premium_main_screen(conn, user_id)
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
        return True

    elif action == "tier":
        tier_key = parts[2] if len(parts) > 2 else "1m"
        await query.answer()
        text, keyboard = build_payment_method_screen(tier_key)
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
        return True

    elif action == "pay":
        method = parts[2] if len(parts) > 2 else "stars"
        tier_key = parts[3] if len(parts) > 3 else "1m"
        tier = TARIFFS.get(tier_key, TARIFFS["1m"])

        if method == "yookassa":
            # Заглушка по требованию пользователя
            await query.answer(
                "⚠️ Платежная система ЮKassa находится в процессе подключения.\nПожалуйста, воспользуйтесь оплатой через Telegram Stars ⭐",
                show_alert=True,
            )
            return True

        elif method == "stars":
            await query.answer("Создаём счёт на оплату звездами ⭐...")
            chat_id = update.effective_chat.id
            payload = f"prem_stars_{tier_key}_{user_id}_{int(time.time())}"

            try:
                await context.bot.send_invoice(
                    chat_id=chat_id,
                    title=f"💎 {tier['title']}",
                    description=tier["desc"],
                    payload=payload,
                    currency="XTR",
                    prices=[LabeledPrice(tier["title"], tier["stars"])],
                    provider_token="",  # Для Stars обязана быть пустой строкой
                )
            except Exception as e:
                log.error(f"send_invoice failed: {e}")
                await query.message.reply_text(f"⚠️ Ошибка создания счёта Telegram Stars: {e}")
            return True

    elif action == "back":
        from start_commands import START_TEXT, get_start_keyboard
        await query.answer()
        await query.edit_message_text(START_TEXT, reply_markup=get_start_keyboard(), parse_mode="Markdown")
        return True

    return False


async def pre_checkout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обязательный ответ на запрос подтверждения перед оплатой Telegram Stars."""
    query = update.pre_checkout_query
    if not query:
        return
    # Всегда подтверждаем готовность провести платеж
    await query.answer(ok=True)


async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, conn: sqlite3.Connection):
    """Срабатывает при успешном завершении платежа Telegram Stars."""
    msg = update.message
    if not msg or not msg.successful_payment:
        return

    pmt = msg.successful_payment
    payload = pmt.invoice_payload or ""
    charge_id = pmt.telegram_payment_charge_id
    user_id = msg.from_user.id if msg.from_user else 0

    # Определяем тариф из payload, например: prem_stars_1m_...
    plan = "1m"
    days = 30
    for key, t in TARIFFS.items():
        if f"_{key}_" in payload:
            plan = key
            days = t["days"]
            break

    # Активируем подписку в БД
    activate_premium(conn, user_id, plan=plan, days=days, charge_id=charge_id)

    # Немедленно снимаем водяной знак со всех подключений этого пользователя
    conns = tn_db.get_all_connections(conn)
    for c in conns:
        bc_id = c["business_connection_id"]
        await apply_bio_for_connection(context.bot, conn, bc_id, user_id)

    tier_info = TARIFFS.get(plan, TARIFFS["1m"])
    await msg.reply_text(
        f"🎉 *Оплата успешно принята!*\n\n"
        f"💎 Премиум-подписка: *{tier_info['title']}* активирована!\n"
        "• Водяной знак `| user @rad1kal_bot` удалён из вашего описания.\n"
        "• Включены все премиум-возможности (авто-заморозка, расширенные лимиты).\n\n"
        "Спасибо за поддержку проекта! 🚀",
        parse_mode="Markdown",
    )


async def give_premium_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE, conn: sqlite3.Connection):
    """Админ-команда для владельца: /give_premium <user_id> [days]"""
    user = update.effective_user
    if not user or user.id != OWNER_ID:
        return

    args = context.args or []
    if not args:
        await update.effective_message.reply_text("Использование: `/give_premium <user_id> [дней/lifetime]`", parse_mode="Markdown")
        return

    try:
        target_uid = int(args[0])
    except ValueError:
        await update.effective_message.reply_text("Неверный user_id")
        return

    days_arg = args[1].lower() if len(args) > 1 else "30"
    if days_arg in ("lifetime", "вечно", "0"):
        plan = "lifetime"
        days = None
    else:
        try:
            days = int(days_arg)
            plan = "admin_grant"
        except ValueError:
            days = 30
            plan = "admin_grant"

    activate_premium(conn, target_uid, plan=plan, days=days)

    # Снимаем водяной знак
    conns = tn_db.get_all_connections(conn)
    for c in conns:
        await apply_bio_for_connection(context.bot, conn, c["business_connection_id"], target_uid)

    await update.effective_message.reply_text(
        f"✅ Пользователю `{target_uid}` успешно выдан Premium ({days or 'навсегда'} дн.)!",
        parse_mode="Markdown",
    )
