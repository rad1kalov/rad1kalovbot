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
    "• 9 уникальных стилей карточек в зависимости от дней стрика\n"
    "• Заморозка серии на сегодня, автозаморозка в 23:30 и воскрешение сгоревших серий\n"
    "• Кастомные стадии и скрытие уровня (Премиум)\n\n"
    "🕒 *Время, погода и статус в имени*\n"
    "• Команда `.time` для выбора часового пояса и настроек\n"
    "• Фамилия бизнес-аккаунта обновляется каждую минуту: `[ЧЧ:ММ]`\n"
    "• Погода в имени: `.weather <город>` ➔ `[ЧЧ:ММ +18° ⛅️]` (Премиум)\n"
    "• Ночной статус: `[🌙 Сплю]` с 23:00 до 08:00 (Премиум)\n\n"
    "📝 *Авто-описание профиля и парные био*\n"
    "• Команда `/description` для циклической смены био по таймеру\n"
    "• Синхронные парные описания для двоих пользователей (Премиум)\n\n"
    "Нажмите на кнопки ниже для настройки и чтения справки 👇"
)

HELP_SUMMARY_TEXT = (
    "⚡️ *Краткая выжимка по командам бота:*\n\n"
    "🔥 `.streak` — интерактивная карточка серии общения и все категории управления.\n"
    "🕒 `.time` — настройка времени, погоды и ночного статуса в профиле бизнес-аккаунта.\n"
    "🌦 `.weather <город>` — включение текущей температуры и погоды в имени.\n"
    "📝 `/description` — настройка авто-смены описания и парных описаний.\n"
    "💎 `/premium` — покупка и управление премиум-подпиской.\n"
    "❓ `.help` (или `/help`) — эта памятка."
)

INSTRUCTION_TEXT = (
    "📖 *Инструкция по настройке и использованию бота*\n\n"
    "1️⃣ *Подключение к Telegram Business:*\n"
    "1. Перейдите в *Настройки Telegram* ➔ *Telegram для бизнеса* ➔ *Чат-боты*.\n"
    "2. Добавьте этого бота в список чат-ботов.\n"
    "3. Разрешите боту доступ к сообщениям, а также права на изменение имени и описания.\n\n"
    "2️⃣ *Серии общения (`.streak`):*\n"
    "• Напишите `.streak` в любом бизнес-чате.\n"
    "• Карточка меняет свой цвет и тему по мере роста стрика (от Ростка до Легенды 365+ дн.).\n"
    "• Категории управления:\n"
    "  📊 *Статистика* — стрик, рекорд, история событий и топ серий.\n"
    "  🎭 *Стадии* — прогресс-бар, ручная и кастомная стадия, скрытие стадии.\n"
    "  🛡 *Защита* — заморозка на сегодня, автозаморозка в 23:30 и воскрешение.\n"
    "  ⚙️ *Настройки* — включение трекера и выбор режима оповещений.\n\n"
    "3️⃣ *Время, погода и ночной статус (`.time`):*\n"
    "• Отправьте `.time` в чате и выберите часовой пояс.\n"
    "• Напишите `.weather Москва`, чтобы добавить актуальную погоду.\n"
    "• Включите ночной статус `[🌙 Сплю]` для сна с 23:00 до 08:00.\n\n"
    "4️⃣ *Авто-описание (`/description`):*\n"
    "• Задайте строки через точку с запятой `;` и интервал смены (от 5 минут).\n"
    "• Установите парное описание с другом — нечётные строки будут у вас, чётные у друга."
)


def get_start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Авто-описание", callback_data="desc:open_start")],
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
