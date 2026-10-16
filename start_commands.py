# start_commands.py
"""Обработчики команд /start, .help, /help и меню инструкций."""

import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

log = logging.getLogger(__name__)

START_TEXT = (
    "👋 *Привет! Я твой персональный Telegram Business ассистент.*\n\n"
    "Вот список моих основных возможностей:\n\n"
    "🔥 *Серии общения (стрики)*\n"
    "• Управление через единую команду `.streak` с категориями кнопок\n"
    "• Подробная статистика, лестница из 9 стадий отношений с прогресс-баром\n"
    "• Заморозка стрика на сегодня и воскрешение сгоревших серий\n"
    "• Умные режимы уведомлений (юбилеи / все / тихий)\n\n"
    "🕒 *Время в профиле каждую минуту*\n"
    "• Команда `.time` с удобным инлайн-выбором часового пояса (закреплена Москва)\n"
    "• Фамилия бизнес-аккаунта автоматически обновляется каждую минуту: `[ЧЧ:ММ]`\n\n"
    "Нажмите на кнопку ниже, чтобы открыть подробную справку 👇"
)

HELP_SUMMARY_TEXT = (
    "⚡️ *Краткая выжимка по командам бота:*\n\n"
    "🔥 `.streak` — управление серией общения в текущем чате.\n"
    "Все возможности (статистика, стадии отношений, заморозка, воскрешение, настройки) "
    "открываются в аккуратном меню с кнопками по категориям.\n\n"
    "🕒 `.time` — настройка времени в профиле бизнес-аккаунта.\n"
    "Открывает инлайн-кнопки с выбором часового пояса (по умолчанию закреплено Московское время). "
    "Время в профиле обновляется каждую минуту.\n\n"
    "❓ `.help` (или `/help`) — эта краткая памятка."
)

INSTRUCTION_TEXT = (
    "📖 *Инструкция по настройке и использованию бота*\n\n"
    "1️⃣ *Подключение к Telegram Business:*\n"
    "1. Перейдите в *Настройки Telegram* ➔ *Telegram для бизнеса* ➔ *Чат-боты*.\n"
    "2. Добавьте этого бота в список чат-ботов.\n"
    "3. Выберите чаты, в которых бот будет активен, и разрешите доступ к управлению сообщениями.\n\n"
    "2️⃣ *Серии общения (`.streak`):*\n"
    "• Напишите `.streak` в любом бизнес-чате.\n"
    "• Откроется интерактивная карточка с 4 категориями:\n"
    "  📊 *Статистика* — текущий стрик, рекорд, общие дни, история событий и топ чатов.\n"
    "  🎭 *Стадии* — описание текущего уровня, прогресс-бар и переключение стадий.\n"
    "  🛡 *Защита* — заморозка серии на сегодня и воскрешение при случайном сгорании.\n"
    "  ⚙️ *Настройки* — включение/выключение серии в чате и выбор частоты уведомлений.\n\n"
    "3️⃣ *Время в профиле (`.time`):*\n"
    "• Отправьте `.time` в бизнес-чате.\n"
    "• Выберите часовой пояс (по умолчанию закреплена Москва UTC+3).\n"
    "• Бот будет каждую минуту обновлять вашу фамилию на актуальное время: `[ЧЧ:ММ]`."
)


def get_start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💎 Премиум подписка", callback_data="prem:main")],
        [InlineKeyboardButton("📖 Подробная инструкция", callback_data="instruction")],
    ])


def get_instruction_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Назад", callback_data="start_back")]
    ])


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start."""
    target = update.effective_message or update.message
    if not target:
        return

    await target.reply_text(
        text=START_TEXT,
        reply_markup=get_start_keyboard(),
        parse_mode="Markdown",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды .help и /help."""
    target = update.effective_message or update.message
    if not target:
        return

    await target.reply_text(
        text=HELP_SUMMARY_TEXT,
        parse_mode="Markdown",
    )


async def instruction_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик переключения на инструкцию и обратно."""
    query = update.callback_query
    if not query:
        return

    await query.answer()

    if query.data == "instruction":
        await query.edit_message_text(
            text=INSTRUCTION_TEXT,
            reply_markup=get_instruction_keyboard(),
            parse_mode="Markdown",
        )
    elif query.data == "start_back":
        await query.edit_message_text(
            text=START_TEXT,
            reply_markup=get_start_keyboard(),
            parse_mode="Markdown",
        )
