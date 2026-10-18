# start_commands.py
"""Обработчики команд /start, .help, /help и меню инструкций."""

import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import ContextTypes

log = logging.getLogger(__name__)

START_TEXT = (
    "👋 *Привет! Я твой персональный Telegram Business ассистент.*\n\n"
    "Вот список моих основных возможностей:\n\n"
    "🔥 *Серии общения (стрики)*\n"
    "• Управление через единую команду `.streak` прямо в бизнес-чатах с собеседниками\n"
    "• 9 уникальных стилей карточек в зависимости от дней стрика\n"
    "• Заморозка серии на сегодня, автозаморозка в 23:30 и воскрешение сгоревших серий\n"
    "• Кастомные стадии и скрытие уровня (Премиум)\n\n"
    "🕒 *Время, погода и статус в имени*\n"
    "• Управление кнопками прямо здесь в личных сообщениях\n"
    "• Фамилия бизнес-аккаунта обновляется каждую минуту: `[ЧЧ:ММ]`\n"
    "• Погода в имени: `.weather <город>` ➔ `[ЧЧ:ММ +18° ⛅️]` (Премиум)\n"
    "• Ночной статус: `[🌙 Сплю]` с 23:00 до 08:00 (Премиум)\n\n"
    "📝 *Авто-описание профиля и парные био*\n"
    "• Управление кнопками прямо здесь в личных сообщениях\n"
    "• Циклическая смена био по таймеру и синхронные парные описания для двоих (Премиум)\n\n"
    "📥 *Скачивание видео и музыки из соцсетей*\n"
    "• Авто-распознавание ссылок: YouTube, VK, Rutube, TikTok, Instagram, Pinterest, SoundCloud, Яндекс Музыка\n"
    "• Выбор качества до 1080p (2K/4K с Премиумом), скачивание аудио в MP3\n"
    "• До 20 скачиваний в месяц бесплатно, безлимит с Премиумом\n\n"
    "🤖 *Искусственный интеллект (gpt-6-astra)*\n"
    "• Команда `.ai <запрос>` в любом чате или ответом на сообщение собеседника\n"
    "• До 2 ответов в месяц бесплатно, безлимит с Премиумом\n\n"
    "🎮 *Игры для двоих в бизнес-чатах*\n"
    "• Команды `.games`, `.rps` (КНБ), `.ttt` (крестики-нолики), `.dice` (кости), `.roulette` (рулетка), `.coin`\n"
    "• Рассчитаны на 2 игроков: собеседнику НЕ нужно подключать бота, ходы делаются кнопками!\n"
    "• Подсчёт очков, тайный выбор ходов и реванши прямо в диалоге\n\n"
    "Нажмите на кнопки ниже для настройки и чтения справки 👇"
)

HELP_SUMMARY_TEXT = (
    "⚡️ *Краткая выжимка по возможностям бота:*\n\n"
    "🎮 `.games` — игры для двоих в бизнес-чатах (`.rps` — КНБ, `.ttt` — крестики-нолики, `.dice` — кости, `.roulette` — рулетка, `.coin` — монетка). Собеседнику не нужен бот!\n"
    "🔥 `.streak` — интерактивная карточка серии общения и управление стриками (в бизнес-чатах).\n"
    "🤖 `.ai <запрос>` — ответы искусственного интеллекта (gpt-6-astra) в любом чате (2 запроса/мес бесплатно, безлимит с Премиумом).\n"
    "📥 *Скачивание видео* — отправьте ссылку на YouTube, VK, Rutube, TikTok, Instagram, Pinterest, SoundCloud, Яндекс Музыка для скачивания в нужном качестве.\n"
    "🕒 *Время в имени* — удобная настройка часового пояса, погоды и ночного статуса кнопками в ЛС бота.\n"
    "📝 *Авто-описание* — настройка авто-смены описания и парных био кнопками в ЛС бота.\n"
    "🌦 `.weather <город>` — включение текущей температуры и погоды в имени (в ЛС бота).\n"
    "💎 `/premium` — покупка и управление премиум-подпиской (2K/4K видео, безлимитные скачивания и безлимитный ИИ).\n"
    "❓ `/help` (или `.help`) — эта памятка."
)

INSTRUCTION_TEXT = (
    "📖 *Инструкция по настройке и использованию бота*\n\n"
    "1️⃣ *Подключение к Telegram Business:*\n"
    "1. Перейдите в *Настройки Telegram* ➔ *Telegram для бизнеса* ➔ *Чат-боты*.\n"
    "2. Добавьте этого бота в список чат-ботов.\n"
    "3. Разрешите боту доступ к сообщениям, а также права на изменение имени и описания.\n\n"
    "2️⃣ *Игры для двоих в чатах (`.games`, `.rps`, `.ttt` и др.):*\n"
    "• Напишите `.games` (или прямые команды: `.rps`, `.ttt`, `.dice`, `.roulette`, `.coin`) в бизнес-чате.\n"
    "• Игра рассчитана строго на двоих: вас и вашего собеседника.\n"
    "• Собеседнику **не нужно подключать бота** — он может делать ходы и нажимать кнопки прямо в чате!\n"
    "• Поддерживаются:\n"
    "  🪨✂️📄 *Камень, Ножницы, Бумага* — скрытый выбор каждого игрока до вскрытия\n"
    "  ❌⭕ *Крестики-Нолики* — тактическая сетка 3×3 с поочерёдными ходами\n"
    "  🎲 *Дуэль на костях* — броски кубика на очки\n"
    "  🔫 *Русская рулетка* — на выживание с 1 патроном на 6 камор\n"
    "  🪙 *Монетка* — орёл или решка для споров\n\n"
    "3️⃣ *Серии общения (`.streak`):*\n"
    "• Напишите `.streak` в любом бизнес-чате.\n"
    "• Карточка меняет свой цвет и тему по мере роста стрика (от Ростка до Легенды 365+ дн.).\n"
    "• Категории управления:\n"
    "  📊 *Статистика* — стрик, рекорд, история событий и топ серий.\n"
    "  🎭 *Стадии* — прогресс-бар, ручная и кастомная стадия, скрытие стадии.\n"
    "  🛡 *Защита* — заморозка на сегодня, автозаморозка в 23:30 и воскрешение.\n"
    "  ⚙️ *Настройки* — включение трекера и выбор режима оповещений.\n\n"
    "4️⃣ *Время, погода и ночной статус:*\n"
    "• Нажмите кнопку «🕒 Время в имени» в личных сообщениях с ботом.\n"
    "• Выберите часовой пояс и нужные опции.\n"
    "• Напишите в ЛС боту `.weather Москва`, чтобы добавить актуальную погоду.\n"
    "• Включите ночной статус `[🌙 Сплю]` для сна с 23:00 до 08:00.\n\n"
    "5️⃣ *Авто-описание профиля:*\n"
    "• Нажмите кнопку «📝 Авто-описание» в личных сообщениях с ботом.\n"
    "• Задайте строки через точку с запятой `;` и интервал смены (от 5 минут).\n"
    "• Установите парное описание с другом — нечётные строки будут у вас, чётные у друга.\n\n"
    "6️⃣ *Скачивание медиа из соцсетей (yt-dlp):*\n"
    "• Отправьте ссылку на видео или трек в ЛС боту или в бизнес-чате.\n"
    "• Поддерживаются: YouTube, YouTube Shorts, VK Видео/Клипы, Rutube, TikTok, Instagram Reels, Pinterest, SoundCloud, Яндекс Музыка.\n"
    "• Выберите качество (360p, 480p, 720p, 1080p, 2K/4K для Премиум) или «🎵 Аудио (MP3)».\n"
    "• Бесплатно: до 20 скачиваний в месяц и качество до 1080p; с Премиумом — безлимит и 2K/4K.\n\n"
    "7️⃣ *Нейросеть (`.ai`):*\n"
    "• Напишите `.ai <вопрос>` в любом чате или ответьте командой `.ai` на сообщение.\n"
    "• Модель gpt-6-astra сгенерирует развёрнутый ответ прямо в чате.\n"
    "• До 2 запросов в месяц бесплатно, безлимит с Премиумом."
)


import os

def get_start_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    webapp_url = os.getenv("WEBAPP_URL", "https://bot.rad1kal.fun")
    if webapp_url:
        buttons.append([InlineKeyboardButton("📱 Открыть Mini App", web_app=WebAppInfo(url=webapp_url))])

    buttons.extend([
        [InlineKeyboardButton("🕒 Время в имени", callback_data="time:open_start")],
        [InlineKeyboardButton("📝 Авто-описание", callback_data="desc:open_start")],
        [InlineKeyboardButton("✨ Эмодзи-статус", callback_data="emj:open_start")],
        [InlineKeyboardButton("💎 Премиум подписка", callback_data="prem:main")],
        [InlineKeyboardButton("📖 Подробная инструкция", callback_data="instruction")],
    ])
    return InlineKeyboardMarkup(buttons)


async def webapp_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команд /app и /webapp."""
    target = update.effective_message or update.message
    if not target:
        return
    webapp_url = os.getenv("WEBAPP_URL", "https://bot.rad1kal.fun")
    if webapp_url:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📱 Открыть Mini App", web_app=WebAppInfo(url=webapp_url))]
        ])
        await target.reply_text(
            "📱 *Telegram Mini App*\n\nНажмите кнопку ниже для открытия панели управления:",
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
    else:
        await target.reply_text(
            "📱 *Telegram Mini App*\n\n"
            "Сервер Mini App активен!\n"
            "Чтобы кнопка открывалась прямо в приложении Telegram, добавьте публичный HTTPS адрес в `.env`:\n"
            "`WEBAPP_URL=https://ваш-домен.com`\n\n"
            "_Telegram требует HTTPS для отображения WebApp внутри приложения._",
            parse_mode="Markdown",
        )


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
