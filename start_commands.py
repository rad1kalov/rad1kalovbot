# start_commands.py
"""Обработчик команды /start и инлайн-кнопок для вывода списка возможностей и инструкций."""

import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

log = logging.getLogger(__name__)

START_TEXT = (
    "👋 *Привет! Я твой персональный Telegram Business ассистент.*\n\n"
    "Вот список моих основных возможностей:\n\n"
    "🔥 *Стрики и серии общения 2.0*\n"
    "• Умный подсчёт дней общения без спама в чаты (включается только там, где нужно: `.streak on`)\n"
    "• Лестница из 9 стадий отношений с прогресс-баром (от _Знакомства_ до _Легендарной связи_)\n"
    "• Заморозка стрика (`.freeze`) и воскрешение сгоревших серий (`.revive`)\n"
    "• Режимы уведомлений: только юбилеи (`milestones`), каждый день или тихий режим\n\n"
    "⏰ *Автоматическое время в профиле*\n"
    "• Ежечасное обновление фамилии профиля на текущее время (например, `[16:45]`)\n"
    "• Настройка любого часового пояса (`.time +3`)\n\n"
    "🛡 *Сохранение и перехват медиа*\n"
    "• Локальное сохранение истории переписки в SQLite\n"
    "• Перехват одноразовых (исчезающих) фото и видео с отправкой владельцу\n"
    "• Защита от удаления сообщений: бот пришлёт удалённый текст или медиа\n"
    "• Перехват скрытых медиа через ответ (reply) на сообщение\n\n"
    "Нажмите на кнопку ниже, чтобы открыть подробную инструкцию 👇"
)

INSTRUCTION_TEXT = (
    "📖 *Инструкция по использованию бота*\n\n"
    "1️⃣ *Подключение к Telegram Business:*\n"
    "1. Откройте *Настройки Telegram* ➔ *Telegram для бизнеса* ➔ *Чат-боты*.\n"
    "2. Добавьте этого бота в качестве чат-бота для вашего бизнес-аккаунта.\n"
    "3. Выберите чаты, в которых бот будет активен, и разрешите доступ к управлению сообщениями.\n\n"
    "2️⃣ *Управление сериями общения (в бизнес-чатах):*\n"
    "• `.streak on` / `.streak off` — включить / выключить отслеживание серии в этом чате (по умолчанию выключено, чтобы не тревожить клиентов)\n"
    "• `.streak` — карточка серии с прогресс-баром и статусом\n"
    "• `.streak mode <milestones|all|silent>` — режим уведомлений (по умолчанию уведомляет только в юбилеи и при повышении стадии)\n"
    "• `.streak stats` — полная статистика, лимиты и параметры\n"
    "• `.streak top` — топ ваших серий среди всех чатов\n"
    "• `.streak history` — история событий серии\n\n"
    "🎭 *Стадии и уровни:*\n"
    "• `.stage` — просмотр текущей стадии, прогресса и лестницы уровней\n"
    "• `.stage auto` — вернуть авто-расчёт стадии по дням стрика\n"
    "• `.upgrade` / `.downgrade` — ручная корректировка уровня\n\n"
    "🛡 *Защита и восстановление:*\n"
    "• `.freeze` — заморозить серию на сегодня (до 2 раз в месяц)\n"
    "• `.revive` — воскресить сгоревшую серию в течение 72 часов (до 3 раз в месяц)\n"
    "• `.revives` — узнать остаток воскрешений и заморозок\n"
    "• `.remind on/off` — вечерние напоминания в 21:00\n\n"
    "3️⃣ *Динамическое время в профиле:*\n"
    "• `.time +3` (или ваше смещение) — каждый час обновляет фамилию в профиле на `[ЧЧ:ММ]`.\n\n"
    "4️⃣ *Анти-удаление и медиа:*\n"
    "• При удалении собеседником сообщения или медиа бот пришлёт копию владельцу в ЛС.\n"
    "• Одноразовые фото/видео пересылаются сразу при получении либо через ответ (reply) на них."
)


def get_start_keyboard() -> InlineKeyboardMarkup:
    """Возвращает клавиатуру для команды /start с кнопкой 'Инструкция'."""
    keyboard = [
        [InlineKeyboardButton("📖 Инструкция", callback_data="instruction")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_instruction_keyboard() -> InlineKeyboardMarkup:
    """Возвращает клавиатуру для экрана инструкции с кнопкой возврата назад."""
    keyboard = [
        [InlineKeyboardButton("⬅️ Назад", callback_data="start_back")]
    ]
    return InlineKeyboardMarkup(keyboard)


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
    """Обработчик команды /help."""
    target = update.effective_message or update.message
    if not target:
        return

    await target.reply_text(
        text=INSTRUCTION_TEXT,
        reply_markup=get_instruction_keyboard(),
        parse_mode="Markdown",
    )


async def instruction_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик инлайн-кнопок перехода к инструкции и возврата назад."""
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
