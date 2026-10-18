# games_commands.py
"""Реализация игр для двоих в бизнес-чатах Telegram."""

import html
import logging
import random
import sqlite3
from typing import Optional, Dict, Any, Tuple
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from games_db import create_game, get_game, update_game, get_partner_name
from permissions_check import get_connection_owner_id, is_own_business_message

log = logging.getLogger(__name__)

# Эмодзи кубиков
DICE_MAP = {1: "⚀", 2: "⚁", 3: "⚂", 4: "⚃", 5: "⚄", 6: "⚅"}

# Комбинации победы в крестиках-ноликах
WIN_LINES = [
    (0, 1, 2), (3, 4, 5), (6, 7, 8),  # строки
    (0, 3, 6), (1, 4, 7), (2, 5, 8),  # столбцы
    (0, 4, 8), (2, 4, 6),             # диагонали
]


def check_ttt_winner(board: list) -> Optional[str]:
    """Проверяет победителя в крестиках-ноликах: 'X', 'O' или None."""
    for a, b, c in WIN_LINES:
        if board[a] and board[a] == board[b] == board[c]:
            return board[a]
    return None


def check_rps_winner(m1: str, m2: str) -> int:
    """0 - ничья, 1 - победил игрок 1, 2 - победил игрок 2."""
    if m1 == m2:
        return 0
    wins = {
        ("rock", "scissors"),
        ("scissors", "paper"),
        ("paper", "rock"),
    }
    return 1 if (m1, m2) in wins else 2


# ────────────────────────── РЕНДЕРИНГ ИГР ──────────────────────────

def render_menu(game: Dict[str, Any]) -> Tuple[str, InlineKeyboardMarkup]:
    """Рендерит меню выбора игр."""
    p1 = html.escape(game["player1_name"])
    p2 = html.escape(game["player2_name"])
    gid = game["game_id"]

    text = (
        "🎮 <b>Игры для двоих в бизнес-чате</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"⚔️ <b>{p1}</b> vs <b>{p2}</b>\n\n"
        "Выберите игру, чтобы бросить вызов собеседнику:\n\n"
        "• 🪨 <b>Камень, Ножницы, Бумага</b> — классика со скрытым выбором\n"
        "• ❌ <b>Крестики-Нолики</b> — тактическая дуэль 3×3\n"
        "• 🎲 <b>Дуэль на костях</b> — броски кубика на удачу\n"
        "• 🔫 <b>Русская рулетка</b> — адреналин на выживание\n"
        "• 🪙 <b>Монетка</b> — быстрое решение спора (орёл / решка)\n\n"
        "💡 <i>Собеседнику не нужно подключать бота — играть можно прямо кнопками!</i>"
    )
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🪨✂️📄 КНБ", callback_data=f"gm:{gid}:start:rps"),
            InlineKeyboardButton("❌⭕ Крестики-Нолики", callback_data=f"gm:{gid}:start:ttt"),
        ],
        [
            InlineKeyboardButton("🎲 Дуэль на костях", callback_data=f"gm:{gid}:start:dice"),
            InlineKeyboardButton("🔫 Русская рулетка", callback_data=f"gm:{gid}:start:roulette"),
        ],
        [
            InlineKeyboardButton("🪙 Монетка", callback_data=f"gm:{gid}:start:coin"),
            InlineKeyboardButton("❌ Отмена", callback_data=f"gm:{gid}:cancel"),
        ],
    ])
    return text, keyboard


def render_rps(game: Dict[str, Any]) -> Tuple[str, InlineKeyboardMarkup]:
    """Рендерит игру Камень-Ножницы-Бумага."""
    p1 = html.escape(game["player1_name"])
    p2 = html.escape(game["player2_name"])
    s1, s2 = game["p1_score"], game["p2_score"]
    state = game["state"]
    round_num = state.get("round", 1)
    gid = game["game_id"]

    p1_move = state.get("p1_move")
    p2_move = state.get("p2_move")
    last_res = state.get("last_result", "")

    p1_status = "✅ <b>Выбор сделан</b>" if p1_move else "⏳ <i>Думает...</i>"
    p2_status = "✅ <b>Выбор сделан</b>" if p2_move else "⏳ <i>Думает...</i>"

    lines = [
        "🪨✂️📄 <b>Камень, Ножницы, Бумага</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"⚔️ <b>{p1}</b> <code>[{s1}]</code> vs <b>{p2}</b> <code>[{s2}]</code>",
        f"Раунд: <b>#{round_num}</b>\n",
        f"👤 <b>{p1}</b>: {p1_status}",
        f"👤 <b>{p2}</b>: {p2_status}",
    ]

    if last_res:
        lines.append("\n━━━━━━━━━━━━━━━━━━━━")
        lines.append(last_res)

    if not p1_move or not p2_move:
        lines.append("\n<i>Нажмите кнопку ниже, чтобы сделать секретный выбор:</i>")
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🪨 Камень", callback_data=f"gm:{gid}:rps:rock"),
                InlineKeyboardButton("✂️ Ножницы", callback_data=f"gm:{gid}:rps:scissors"),
                InlineKeyboardButton("📄 Бумага", callback_data=f"gm:{gid}:rps:paper"),
            ],
            [
                InlineKeyboardButton("❌ Завершить игру", callback_data=f"gm:{gid}:cancel")
            ],
        ])
    else:
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔄 Следующий раунд", callback_data=f"gm:{gid}:rps_next"),
                InlineKeyboardButton("❌ Завершить", callback_data=f"gm:{gid}:cancel"),
            ]
        ])

    return "\n".join(lines), keyboard


def render_ttt(game: Dict[str, Any]) -> Tuple[str, InlineKeyboardMarkup]:
    """Рендерит игру Крестики-Нолики."""
    p1 = html.escape(game["player1_name"])
    p2 = html.escape(game["player2_name"])
    s1, s2 = game["p1_score"], game["p2_score"]
    state = game["state"]
    board = state.get("board", [""] * 9)
    round_num = state.get("round", 1)
    gid = game["game_id"]
    current_turn = game["current_turn"]
    status = game["status"]
    last_res = state.get("last_result", "")

    # Определение знака и имени текущего игрока
    turn_mark = "❌" if current_turn == game["player1_id"] else "⭕"
    turn_name = p1 if current_turn == game["player1_id"] else p2

    lines = [
        "❌⭕ <b>Крестики-Нолики</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"❌ <b>{p1}</b> <code>[{s1}]</code> vs ⭕ <b>{p2}</b> <code>[{s2}]</code>",
        f"Раунд: <b>#{round_num}</b>",
    ]

    if status == "active":
        lines.append(f"\n👉 Сейчас ход: {turn_mark} <b>{turn_name}</b>")
    elif last_res:
        lines.append("\n━━━━━━━━━━━━━━━━━━━━")
        lines.append(last_res)

    # Формируем сетку 3x3
    keyboard_rows = []
    for r in range(3):
        row_btns = []
        for c in range(3):
            idx = r * 3 + c
            val = board[idx]
            disp = "❌" if val == "X" else ("⭕" if val == "O" else "⬜")
            # Если игра активна и клетка пуста, можно жать
            row_btns.append(InlineKeyboardButton(disp, callback_data=f"gm:{gid}:ttt:{idx}"))
        keyboard_rows.append(row_btns)

    if status == "active":
        keyboard_rows.append([
            InlineKeyboardButton("🏳️ Сдаться", callback_data=f"gm:{gid}:ttt_surrender"),
            InlineKeyboardButton("❌ Закончить", callback_data=f"gm:{gid}:cancel"),
        ])
    else:
        keyboard_rows.append([
            InlineKeyboardButton("🔄 Реванш", callback_data=f"gm:{gid}:ttt_next"),
            InlineKeyboardButton("❌ Выйти", callback_data=f"gm:{gid}:cancel"),
        ])

    return "\n".join(lines), keyboard_rows


def render_dice(game: Dict[str, Any]) -> Tuple[str, InlineKeyboardMarkup]:
    """Рендерит игру Дуэль на костях."""
    p1 = html.escape(game["player1_name"])
    p2 = html.escape(game["player2_name"])
    s1, s2 = game["p1_score"], game["p2_score"]
    state = game["state"]
    round_num = state.get("round", 1)
    gid = game["game_id"]
    current_turn = game["current_turn"]
    status = game["status"]

    r1 = state.get("p1_roll")
    r2 = state.get("p2_roll")
    last_res = state.get("last_result", "")

    p1_str = f"<b>{DICE_MAP.get(r1, '🎲')} {r1}</b>" if r1 else "⏳ <i>Ожидает броска...</i>"
    p2_str = f"<b>{DICE_MAP.get(r2, '🎲')} {r2}</b>" if r2 else "⏳ <i>Ожидает броска...</i>"

    turn_name = p1 if current_turn == game["player1_id"] else p2

    lines = [
        "🎲 <b>Дуэль на костях</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"⚔️ <b>{p1}</b> <code>[{s1}]</code> vs <b>{p2}</b> <code>[{s2}]</code>",
        f"Раунд: <b>#{round_num}</b>\n",
        f"👤 <b>{p1}</b>: {p1_str}",
        f"👤 <b>{p2}</b>: {p2_str}",
    ]

    if status == "active":
        lines.append(f"\n👉 Очередь бросать: <b>{turn_name}</b>")
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🎲 Бросить кубик", callback_data=f"gm:{gid}:dice_roll"),
                InlineKeyboardButton("❌ Завершить", callback_data=f"gm:{gid}:cancel"),
            ]
        ])
    else:
        if last_res:
            lines.append("\n━━━━━━━━━━━━━━━━━━━━")
            lines.append(last_res)
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🎲 Следующий бросок", callback_data=f"gm:{gid}:dice_next"),
                InlineKeyboardButton("❌ Завершить", callback_data=f"gm:{gid}:cancel"),
            ]
        ])

    return "\n".join(lines), keyboard


def render_roulette(game: Dict[str, Any]) -> Tuple[str, InlineKeyboardMarkup]:
    """Рендерит игру Русская рулетка."""
    p1 = html.escape(game["player1_name"])
    p2 = html.escape(game["player2_name"])
    s1, s2 = game["p1_score"], game["p2_score"]
    state = game["state"]
    round_num = state.get("round", 1)
    gid = game["game_id"]
    current_turn = game["current_turn"]
    status = game["status"]
    chamber = state.get("chamber", 0)
    last_res = state.get("last_result", "")

    turn_name = p1 if current_turn == game["player1_id"] else p2

    lines = [
        "🔫 <b>Русская рулетка на двоих</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "В барабане <b>6 камор</b> и <b>1 боевой патрон</b>.",
        "Вы по очереди нажимаете на спуск!\n",
        f"⚔️ <b>{p1}</b> <code>[{s1}]</code> vs <b>{p2}</b> <code>[{s2}]</code>",
        f"Камора: <b>#{chamber + 1} / 6</b>",
    ]

    if status == "active":
        if last_res:
            lines.append(f"\n{last_res}")
        lines.append(f"\n👉 Револьвер у: <b>{turn_name}</b>")
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("💥 Нажать на спуск", callback_data=f"gm:{gid}:roulette_shoot"),
                InlineKeyboardButton("🏳️ Сдаться", callback_data=f"gm:{gid}:roulette_ff"),
            ]
        ])
    else:
        if last_res:
            lines.append("\n━━━━━━━━━━━━━━━━━━━━")
            lines.append(last_res)
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔄 Зарядить заново", callback_data=f"gm:{gid}:roulette_next"),
                InlineKeyboardButton("❌ Завершить", callback_data=f"gm:{gid}:cancel"),
            ]
        ])

    return "\n".join(lines), keyboard


def render_coin(game: Dict[str, Any]) -> Tuple[str, InlineKeyboardMarkup]:
    """Рендерит бросок монетки."""
    p1 = html.escape(game["player1_name"])
    p2 = html.escape(game["player2_name"])
    s1, s2 = game["p1_score"], game["p2_score"]
    state = game["state"]
    gid = game["game_id"]
    last_res = state.get("last_result", "")

    lines = [
        "🪙 <b>Орёл или Решка</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"⚔️ <b>{p1}</b> <code>[{s1}]</code> vs <b>{p2}</b> <code>[{s2}]</code>\n",
    ]
    if last_res:
        lines.append(last_res)
    else:
        lines.append("Бросьте монетку, чтобы решить спор или узнать судьбу!")

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🪙 Подбросить монетку", callback_data=f"gm:{gid}:coin_flip"),
            InlineKeyboardButton("❌ Завершить", callback_data=f"gm:{gid}:cancel"),
        ]
    ])
    return "\n".join(lines), keyboard


def render_game_message(game: Dict[str, Any]) -> Tuple[str, InlineKeyboardMarkup]:
    """Универсальный рендерер игры по типу."""
    gtype = game["game_type"]
    if gtype == "menu":
        return render_menu(game)
    elif gtype == "rps":
        return render_rps(game)
    elif gtype == "ttt":
        return render_ttt(game)
    elif gtype == "dice":
        return render_dice(game)
    elif gtype == "roulette":
        return render_roulette(game)
    elif gtype == "coin":
        return render_coin(game)
    return "Неизвестная игра", InlineKeyboardMarkup([])


async def safe_edit_game_message(bot, query, game: Dict[str, Any], text: str, reply_markup: InlineKeyboardMarkup):
    """Надёжно редактирует сообщение игры с поддержкой business_connection_id."""
    try:
        await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode="HTML")
    except Exception as e:
        log.warning(f"query.edit_message_text error: {e}, falling back to bot.edit_message_text")
        try:
            await bot.edit_message_text(
                chat_id=game["chat_id"],
                message_id=game.get("message_id") or query.message.message_id,
                business_connection_id=game.get("business_connection_id"),
                text=text,
                reply_markup=reply_markup,
                parse_mode="HTML",
            )
        except Exception as e2:
            log.error(f"bot.edit_message_text error: {e2}")


# ─────────────────────── ОБРАБОТЧИК КОМАНД ──────────────────────────

async def handle_game_command(update: Update, context: ContextTypes.DEFAULT_TYPE, conn: sqlite3.Connection) -> bool:
    """
    Обрабатывает команды вызова игр в бизнес-чатах:
    .game, .games, .игра, .игры, .rps, .кнб, .ttt, .xo, .кн, .dice, .кости, .roulette, .рулетка, .coin, .монетка
    """
    msg = update.business_message or update.message or update.effective_message
    if not msg or not msg.text:
        return False

    text_clean = msg.text.strip().lower()
    cmd = text_clean.split()[0]

    # Список поддерживаемых команд и их маппинг на тип игры
    cmd_map = {
        ".game": "menu", "/game": "menu",
        ".games": "menu", "/games": "menu",
        ".игра": "menu", ".игры": "menu",
        ".rps": "rps", "/rps": "rps",
        ".кнб": "rps",
        ".ttt": "ttt", "/ttt": "ttt",
        ".xo": "ttt", "/xo": "ttt",
        ".кн": "ttt", ".крестики": "ttt",
        ".dice": "dice", "/dice": "dice",
        ".кости": "dice", ".кубики": "dice",
        ".roulette": "roulette", "/roulette": "roulette",
        ".рулетка": "roulette",
        ".coin": "coin", "/coin": "coin",
        ".монетка": "coin",
    }

    if cmd not in cmd_map:
        return False

    game_type = cmd_map[cmd]
    business_connection_id = getattr(msg, "business_connection_id", None)
    chat_id = msg.chat_id
    from_user = msg.from_user
    from_id = from_user.id if from_user else 0
    from_name = from_user.full_name if from_user else "Игрок"

    # Если вызов сделан в личных сообщениях с ботом (не в бизнес-чате)
    if not business_connection_id:
        await msg.reply_text(
            "🎮 <b>Игры для двоих в Telegram Business</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Эти игры предназначены для ваших <b>бизнес-диалогов</b> с друзьями и клиентами!\n\n"
            "💬 <b>Как играть:</b>\n"
            "1. Откройте любой диалог с собеседником.\n"
            "2. Напишите команду <code>.games</code> (или <code>.rps</code>, <code>.ttt</code>, <code>.dice</code>, <code>.roulette</code>).\n"
            "3. Бот мгновенно отправит интерактивную игру прямо в чат!\n\n"
            "✨ <i>Собеседнику не нужно подключать бота — он сможет нажимать на кнопки и делать ходы прямо из чата!</i>",
            parse_mode="HTML",
        )
        return True

    # Определяем двух участников игры
    # Игрок 1: отправитель команды
    p1_id = from_id
    p1_name = from_name

    # Игрок 2: второй участник чата
    is_own = await is_own_business_message(context.bot, conn, msg)
    if is_own:
        # Отправил владелец бизнеса -> второй участник - chat_id (клиент/друг)
        p2_id = chat_id
        p2_name = await get_partner_name(context.bot, conn, p2_id)
    else:
        # Отправил собеседник -> второй участник - владелец бизнес-аккаунта
        p2_id = await get_connection_owner_id(context.bot, conn, business_connection_id) or chat_id
        p2_name = await get_partner_name(context.bot, conn, p2_id)

    # Инициализация начального состояния в зависимости от игры
    if game_type == "menu":
        state = {}
        current_turn = None
    elif game_type == "rps":
        state = {"round": 1, "p1_move": None, "p2_move": None, "last_result": ""}
        current_turn = None
    elif game_type == "ttt":
        state = {"round": 1, "board": [""] * 9, "starter_id": p1_id, "last_result": ""}
        current_turn = p1_id
    elif game_type == "dice":
        state = {"round": 1, "p1_roll": None, "p2_roll": None, "last_result": ""}
        current_turn = p1_id
    elif game_type == "roulette":
        state = {"round": 1, "bullet": random.randint(0, 5), "chamber": 0, "last_result": ""}
        current_turn = p1_id
    elif game_type == "coin":
        state = {"round": 1, "last_result": ""}
        current_turn = p1_id
    else:
        state = {}
        current_turn = None

    game_id = create_game(
        conn=conn,
        chat_id=chat_id,
        business_connection_id=business_connection_id,
        game_type=game_type,
        player1_id=p1_id,
        player1_name=p1_name,
        player2_id=p2_id,
        player2_name=p2_name,
        current_turn=current_turn,
        state=state,
    )

    game_data = get_game(conn, game_id)
    if not game_data:
        return True

    text, keyboard = render_game_message(game_data)

    try:
        sent = await context.bot.send_message(
            chat_id=chat_id,
            business_connection_id=business_connection_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
        update_game(conn, game_id, message_id=sent.message_id)
    except Exception as e:
        log.error(f"Failed to send game message: {e}")

    return True


# ─────────────────────── ОБРАБОТЧИК КНОПОК ──────────────────────────

async def handle_game_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, conn: sqlite3.Connection) -> None:
    """Обрабатывает нажатия на кнопки всех игр (gm:...)."""
    query = update.callback_query
    if not query or not query.data or not query.data.startswith("gm:"):
        return

    parts = query.data.split(":")
    if len(parts) < 3:
        await query.answer()
        return

    try:
        game_id = int(parts[1])
    except ValueError:
        await query.answer("Некорректная игра.", show_alert=True)
        return

    action = parts[2]
    extra = parts[3] if len(parts) > 3 else None

    game = get_game(conn, game_id)
    if not game:
        await query.answer("⚠️ Игра устарела или не найдена.", show_alert=True)
        return

    user_id = query.from_user.id
    p1_id = game["player1_id"]
    p2_id = game["player2_id"]
    p1_name = game["player1_name"]
    p2_name = game["player2_name"]

    # Проверка, что нажимает один из двух участников
    if user_id != p1_id and user_id != p2_id:
        await query.answer("⚠️ Вы не являетесь участником этой игры!", show_alert=True)
        return

    state = game["state"]

    # ────── ОТМЕНА / ЗАВЕРШЕНИЕ ИГРЫ ──────
    if action == "cancel":
        update_game(conn, game_id, status="finished")
        game["status"] = "finished"
        s1, s2 = game["p1_score"], game["p2_score"]
        fin_text = (
            "🏁 <b>Игра завершена</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>{html.escape(p1_name)}</b> <code>[{s1}]</code> vs 👤 <b>{html.escape(p2_name)}</b> <code>[{s2}]</code>\n\n"
            "Спасибо за игру! Сыграем ещё?"
        )
        fin_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎮 Выбрать другую игру", callback_data=f"gm:{game_id}:to_menu")]
        ])
        await safe_edit_game_message(context.bot, query, game, fin_text, fin_kb)
        await query.answer("Игра завершена.")
        return

    # ────── ПЕРЕХОД В МЕНЮ ──────
    if action == "to_menu":
        update_game(conn, game_id, game_type="menu", status="active")
        game["game_type"] = "menu"
        game["status"] = "active"
        text, kb = render_menu(game)
        await safe_edit_game_message(context.bot, query, game, text, kb)
        await query.answer()
        return

    # ────── СТАРТ ИГРЫ ИЗ МЕНЮ ──────
    if action == "start":
        chosen_type = extra or "rps"
        if chosen_type == "rps":
            new_state = {"round": 1, "p1_move": None, "p2_move": None, "last_result": ""}
            turn = None
        elif chosen_type == "ttt":
            new_state = {"round": 1, "board": [""] * 9, "starter_id": user_id, "last_result": ""}
            turn = user_id
        elif chosen_type == "dice":
            new_state = {"round": 1, "p1_roll": None, "p2_roll": None, "last_result": ""}
            turn = user_id
        elif chosen_type == "roulette":
            new_state = {"round": 1, "bullet": random.randint(0, 5), "chamber": 0, "last_result": ""}
            turn = user_id
        elif chosen_type == "coin":
            new_state = {"round": 1, "last_result": ""}
            turn = user_id
        else:
            new_state = {}
            turn = None

        update_game(
            conn,
            game_id,
            game_type=chosen_type,
            current_turn=turn,
            p1_score=0,
            p2_score=0,
            state=new_state,
            status="active",
        )
        game = get_game(conn, game_id)
        text, kb = render_game_message(game)
        await safe_edit_game_message(context.bot, query, game, text, kb)
        await query.answer(f"Игра начата!")
        return

    # ────── КАМЕНЬ, НОЖНИЦЫ, БУМАГА ──────
    if action == "rps":
        move = extra
        move_names = {"rock": "🪨 Камень", "scissors": "✂️ Ножницы", "paper": "📄 Бумага"}
        move_name = move_names.get(move, move)

        p1_move = state.get("p1_move")
        p2_move = state.get("p2_move")

        if user_id == p1_id:
            if p1_move:
                await query.answer("Вы уже сделали выбор в этом раунде! Ждём соперника.", show_alert=False)
                return
            state["p1_move"] = move
            p1_move = move
        else:
            if p2_move:
                await query.answer("Вы уже сделали выбор в этом раунде! Ждём соперника.", show_alert=False)
                return
            state["p2_move"] = move
            p2_move = move

        await query.answer(f"Вы выбрали: {move_name}! 🤫 Ваш выбор скрыт до конца раунда.", show_alert=False)

        # Если оба сделали выбор — подводим итоги раунда!
        if p1_move and p2_move:
            res = check_rps_winner(p1_move, p2_move)
            round_num = state.get("round", 1)
            p1_label = move_names[p1_move]
            p2_label = move_names[p2_move]
            s1 = game["p1_score"]
            s2 = game["p2_score"]

            if res == 0:
                state["last_result"] = (
                    f"🤝 <b>Ничья в раунде #{round_num}!</b>\n"
                    f"👤 {html.escape(p1_name)}: {p1_label}\n"
                    f"👤 {html.escape(p2_name)}: {p2_label}"
                )
            elif res == 1:
                s1 += 1
                state["last_result"] = (
                    f"🏆 <b>Раунд #{round_num} выиграл {html.escape(p1_name)}!</b> (+1 очко)\n"
                    f"👤 {html.escape(p1_name)}: {p1_label} 💥 👤 {html.escape(p2_name)}: {p2_label}"
                )
            else:
                s2 += 1
                state["last_result"] = (
                    f"🏆 <b>Раунд #{round_num} выиграл {html.escape(p2_name)}!</b> (+1 очко)\n"
                    f"👤 {html.escape(p2_name)}: {p2_label} 💥 👤 {html.escape(p1_name)}: {p1_label}"
                )

            update_game(conn, game_id, p1_score=s1, p2_score=s2, state=state)
        else:
            update_game(conn, game_id, state=state)

        game = get_game(conn, game_id)
        text, kb = render_rps(game)
        await safe_edit_game_message(context.bot, query, game, text, kb)
        return

    # КНБ: Следующий раунд
    if action == "rps_next":
        state["round"] = state.get("round", 1) + 1
        state["p1_move"] = None
        state["p2_move"] = None
        # Сохраняем прошлый результат на пару секунд или очищаем
        state["last_result"] = ""
        update_game(conn, game_id, state=state)
        game = get_game(conn, game_id)
        text, kb = render_rps(game)
        await safe_edit_game_message(context.bot, query, game, text, kb)
        await query.answer("Новый раунд!")
        return

    # ────── КРЕСТИКИ-НОЛИКИ ──────
    if action == "ttt":
        if game["status"] != "active":
            await query.answer("Игра завершена. Нажмите «Реванш», чтобы сыграть снова!", show_alert=True)
            return

        current_turn = game["current_turn"]
        if user_id != current_turn:
            turn_sym = "❌" if current_turn == p1_id else "⭕"
            await query.answer(f"Сейчас не ваш ход! Ходит {turn_sym}", show_alert=True)
            return

        try:
            cell_idx = int(extra)
        except (ValueError, TypeError):
            await query.answer()
            return

        board = state.get("board", [""] * 9)
        if board[cell_idx] != "":
            await query.answer("Эта клетка уже занята!", show_alert=False)
            return

        # Ставим знак
        mark = "X" if user_id == p1_id else "O"
        board[cell_idx] = mark
        state["board"] = board

        winner_mark = check_ttt_winner(board)
        round_num = state.get("round", 1)
        s1, s2 = game["p1_score"], game["p2_score"]

        if winner_mark == "X":
            s1 += 1
            state["last_result"] = f"🏆 <b>Победа ❌ {html.escape(p1_name)} в раунде #{round_num}!</b> (+1 очко)"
            update_game(conn, game_id, p1_score=s1, state=state, status="finished")
            await query.answer("Победа крестиков!")
        elif winner_mark == "O":
            s2 += 1
            state["last_result"] = f"🏆 <b>Победа ⭕ {html.escape(p2_name)} в раунде #{round_num}!</b> (+1 очко)"
            update_game(conn, game_id, p2_score=s2, state=state, status="finished")
            await query.answer("Победа ноликов!")
        elif "" not in board:
            state["last_result"] = f"🤝 <b>Боевая ничья в раунде #{round_num}!</b> Все клетки заполнены."
            update_game(conn, game_id, state=state, status="finished")
            await query.answer("Ничья!")
        else:
            # Передаём ход
            next_turn = p2_id if user_id == p1_id else p1_id
            update_game(conn, game_id, current_turn=next_turn, state=state)
            await query.answer()

        game = get_game(conn, game_id)
        text, kb = render_ttt(game)
        await safe_edit_game_message(context.bot, query, game, text, kb)
        return

    # Крестики-нолики: Сдаться
    if action == "ttt_surrender":
        if game["status"] != "active":
            await query.answer("Игра уже окончена.", show_alert=True)
            return
        s1, s2 = game["p1_score"], game["p2_score"]
        if user_id == p1_id:
            s2 += 1
            state["last_result"] = f"🏳️ <b>{html.escape(p1_name)} сдался!</b> Победил ⭕ <b>{html.escape(p2_name)}</b>!"
        else:
            s1 += 1
            state["last_result"] = f"🏳️ <b>{html.escape(p2_name)} сдался!</b> Победил ❌ <b>{html.escape(p1_name)}</b>!"
        update_game(conn, game_id, p1_score=s1, p2_score=s2, state=state, status="finished")
        game = get_game(conn, game_id)
        text, kb = render_ttt(game)
        await safe_edit_game_message(context.bot, query, game, text, kb)
        await query.answer("Сдача зафиксирована.")
        return

    # Крестики-нолики: Реванш
    if action == "ttt_next":
        prev_starter = state.get("starter_id", p1_id)
        new_starter = p2_id if prev_starter == p1_id else p1_id
        state["round"] = state.get("round", 1) + 1
        state["board"] = [""] * 9
        state["starter_id"] = new_starter
        state["last_result"] = ""
        update_game(conn, game_id, current_turn=new_starter, state=state, status="active")
        game = get_game(conn, game_id)
        text, kb = render_ttt(game)
        await safe_edit_game_message(context.bot, query, game, text, kb)
        await query.answer("Реванш начат!")
        return

    # ────── ДУЭЛЬ НА КОСТЯХ ──────
    if action == "dice_roll":
        if game["status"] != "active":
            await query.answer("Раунд завершён. Нажмите «Следующий бросок».", show_alert=True)
            return

        current_turn = game["current_turn"]
        if user_id != current_turn:
            await query.answer("Сейчас не ваша очередь бросать кубик!", show_alert=True)
            return

        roll = random.randint(1, 6)
        r1 = state.get("p1_roll")
        r2 = state.get("p2_roll")

        if user_id == p1_id:
            state["p1_roll"] = roll
            r1 = roll
        else:
            state["p2_roll"] = roll
            r2 = roll

        # Если оба бросили — подводим итоги
        if r1 is not None and r2 is not None:
            round_num = state.get("round", 1)
            s1, s2 = game["p1_score"], game["p2_score"]
            if r1 > r2:
                s1 += 1
                state["last_result"] = (
                    f"🏆 <b>Раунд #{round_num} выиграл {html.escape(p1_name)}!</b> (+1 очко)\n"
                    f"({r1} против {r2})"
                )
            elif r2 > r1:
                s2 += 1
                state["last_result"] = (
                    f"🏆 <b>Раунд #{round_num} выиграл {html.escape(p2_name)}!</b> (+1 очко)\n"
                    f"({r2} против {r1})"
                )
            else:
                state["last_result"] = f"🤝 <b>Ничья в раунде #{round_num}!</b> ({r1} = {r2})"

            update_game(conn, game_id, p1_score=s1, p2_score=s2, state=state, status="finished")
            await query.answer(f"Вам выпало: {DICE_MAP[roll]} {roll}! Раунд завершён.")
        else:
            # Ход переходит второму игроку
            next_turn = p2_id if user_id == p1_id else p1_id
            update_game(conn, game_id, current_turn=next_turn, state=state)
            await query.answer(f"Вам выпало: {DICE_MAP[roll]} {roll}! Очередь соперника.")

        game = get_game(conn, game_id)
        text, kb = render_dice(game)
        await safe_edit_game_message(context.bot, query, game, text, kb)
        return

    # Дуэль на костях: Следующий раунд
    if action == "dice_next":
        state["round"] = state.get("round", 1) + 1
        state["p1_roll"] = None
        state["p2_roll"] = None
        state["last_result"] = ""
        # Чередуем, кто бросает первым
        next_turn = p2_id if game["current_turn"] == p1_id else p1_id
        update_game(conn, game_id, current_turn=next_turn, state=state, status="active")
        game = get_game(conn, game_id)
        text, kb = render_dice(game)
        await safe_edit_game_message(context.bot, query, game, text, kb)
        await query.answer("Новый раунд!")
        return

    # ────── РУССКАЯ РУЛЕТКА ──────
    if action == "roulette_shoot":
        if game["status"] != "active":
            await query.answer("Игра завершена.", show_alert=True)
            return

        current_turn = game["current_turn"]
        if user_id != current_turn:
            await query.answer("Сейчас револьвер не у вас!", show_alert=True)
            return

        bullet = state.get("bullet", 0)
        chamber = state.get("chamber", 0)
        s1, s2 = game["p1_score"], game["p2_score"]
        shooter_name = p1_name if user_id == p1_id else p2_name
        other_name = p2_name if user_id == p1_id else p1_name

        if chamber == bullet:
            # ВЫСТРЕЛ!
            if user_id == p1_id:
                s2 += 1
            else:
                s1 += 1
            state["last_result"] = (
                f"💥 <b>БАМ! ВЫСТРЕЛ!</b>\n"
                f"Камора #{chamber + 1} была заряжена! 💀\n"
                f"<b>{html.escape(shooter_name)}</b> застрелен!\n\n"
                f"🏆 Победитель: <b>{html.escape(other_name)}</b>! 🎉"
            )
            update_game(conn, game_id, p1_score=s1, p2_score=s2, state=state, status="finished")
            await query.answer("💥 БАМ! Выстрел!", show_alert=True)
        else:
            # Осечка / пусто
            chamber += 1
            state["chamber"] = chamber
            next_turn = p2_id if user_id == p1_id else p1_id
            state["last_result"] = (
                f"<i>*Щёлк!* Пусто! 😅 Камора #{chamber} оказалась пустой.</i>\n"
                f"Револьвер переходит к <b>{html.escape(other_name)}</b>..."
            )
            update_game(conn, game_id, current_turn=next_turn, state=state)
            await query.answer("*Щёлк!* Пусто! Вы выжили! 😅", show_alert=False)

        game = get_game(conn, game_id)
        text, kb = render_roulette(game)
        await safe_edit_game_message(context.bot, query, game, text, kb)
        return

    # Русская рулетка: Сдаться
    if action == "roulette_ff":
        if game["status"] != "active":
            await query.answer("Игра уже окончена.", show_alert=True)
            return
        s1, s2 = game["p1_score"], game["p2_score"]
        if user_id == p1_id:
            s2 += 1
            winner_name = p2_name
            loser_name = p1_name
        else:
            s1 += 1
            winner_name = p1_name
            loser_name = p2_name
        state["last_result"] = f"🏳️ <b>{html.escape(loser_name)} не выдержал напряжения и сдался!</b>\nПобедитель: <b>{html.escape(winner_name)}</b>! 🏆"
        update_game(conn, game_id, p1_score=s1, p2_score=s2, state=state, status="finished")
        game = get_game(conn, game_id)
        text, kb = render_roulette(game)
        await safe_edit_game_message(context.bot, query, game, text, kb)
        await query.answer("Сдача зафиксирована.")
        return

    # Русская рулетка: Зарядить заново
    if action == "roulette_next":
        state["round"] = state.get("round", 1) + 1
        state["bullet"] = random.randint(0, 5)
        state["chamber"] = 0
        state["last_result"] = ""
        # Передаём первый ход тому, кто выжил (или чередуем)
        next_turn = p2_id if game["current_turn"] == p1_id else p1_id
        update_game(conn, game_id, current_turn=next_turn, state=state, status="active")
        game = get_game(conn, game_id)
        text, kb = render_roulette(game)
        await safe_edit_game_message(context.bot, query, game, text, kb)
        await query.answer("Револьвер заряжен заново!")
        return

    # ────── МОНЕТКА ──────
    if action == "coin_flip":
        side = random.choice(["eagle", "tails"])
        s1, s2 = game["p1_score"], game["p2_score"]
        flipper_name = p1_name if user_id == p1_id else p2_name

        if side == "eagle":
            res_str = "🦅 <b>Выпал ОРЁЛ!</b>"
        else:
            res_str = "🪙 <b>Выпала РЕШКА!</b>"

        state["last_result"] = (
            f"👤 <b>{html.escape(flipper_name)}</b> подбросил монетку...\n\n"
            f"✨ {res_str}"
        )
        update_game(conn, game_id, state=state)
        game = get_game(conn, game_id)
        text, kb = render_coin(game)
        await safe_edit_game_message(context.bot, query, game, text, kb)
        await query.answer(f"Монетка упала: {res_str.replace('<b>', '').replace('</b>', '')}")
        return

    await query.answer()
