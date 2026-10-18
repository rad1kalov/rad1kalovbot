# games_db.py
"""База данных и вспомогательные функции для игр в бизнес-чатах."""

import json
import logging
import sqlite3
from datetime import datetime
from typing import Optional, Dict, Any

log = logging.getLogger(__name__)


def init_games_db(conn: sqlite3.Connection) -> None:
    """Инициализирует таблицу для игр в бизнес-чатах."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS business_games (
            game_id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id                 INTEGER NOT NULL,
            business_connection_id  TEXT,
            game_type               TEXT NOT NULL,
            player1_id              INTEGER NOT NULL,
            player1_name            TEXT NOT NULL,
            player2_id              INTEGER NOT NULL,
            player2_name            TEXT NOT NULL,
            current_turn            INTEGER,
            p1_score                INTEGER DEFAULT 0,
            p2_score                INTEGER DEFAULT 0,
            state_json              TEXT NOT NULL,
            status                  TEXT DEFAULT 'active',
            message_id              INTEGER,
            created_at              TEXT,
            updated_at              TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_games_chat ON business_games(chat_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_games_status ON business_games(status)")
    conn.commit()


def create_game(
    conn: sqlite3.Connection,
    chat_id: int,
    business_connection_id: Optional[str],
    game_type: str,
    player1_id: int,
    player1_name: str,
    player2_id: int,
    player2_name: str,
    current_turn: Optional[int],
    state: Dict[str, Any],
    message_id: Optional[int] = None,
) -> int:
    """Создаёт новую запись об игре и возвращает её ID."""
    now_iso = datetime.now().isoformat(timespec="seconds")
    state_str = json.dumps(state, ensure_ascii=False)
    cur = conn.execute("""
        INSERT INTO business_games (
            chat_id, business_connection_id, game_type,
            player1_id, player1_name, player2_id, player2_name,
            current_turn, p1_score, p2_score, state_json,
            status, message_id, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, 'active', ?, ?, ?)
    """, (
        chat_id,
        business_connection_id,
        game_type,
        player1_id,
        player1_name,
        player2_id,
        player2_name,
        current_turn,
        state_str,
        message_id,
        now_iso,
        now_iso,
    ))
    conn.commit()
    return cur.lastrowid


def get_game(conn: sqlite3.Connection, game_id: int) -> Optional[Dict[str, Any]]:
    """Получает данные игры по её ID."""
    cur = conn.execute("""
        SELECT game_id, chat_id, business_connection_id, game_type,
               player1_id, player1_name, player2_id, player2_name,
               current_turn, p1_score, p2_score, state_json,
               status, message_id, created_at, updated_at
        FROM business_games
        WHERE game_id = ?
    """, (game_id,))
    row = cur.fetchone()
    if not row:
        return None

    try:
        state = json.loads(row[11]) if row[11] else {}
    except Exception:
        state = {}

    return {
        "game_id": row[0],
        "chat_id": row[1],
        "business_connection_id": row[2],
        "game_type": row[3],
        "player1_id": row[4],
        "player1_name": row[5],
        "player2_id": row[6],
        "player2_name": row[7],
        "current_turn": row[8],
        "p1_score": row[9],
        "p2_score": row[10],
        "state": state,
        "status": row[12],
        "message_id": row[13],
        "created_at": row[14],
        "updated_at": row[15],
    }


def update_game(
    conn: sqlite3.Connection,
    game_id: int,
    game_type: Optional[str] = None,
    current_turn: Optional[int] = None,
    p1_score: Optional[int] = None,
    p2_score: Optional[int] = None,
    state: Optional[Dict[str, Any]] = None,
    status: Optional[str] = None,
    message_id: Optional[int] = None,
) -> None:
    """Обновляет параметры игры."""
    updates = ["updated_at = ?"]
    params = [datetime.now().isoformat(timespec="seconds")]

    if game_type is not None:
        updates.append("game_type = ?")
        params.append(game_type)
    if current_turn is not None:
        updates.append("current_turn = ?")
        params.append(current_turn)
    if p1_score is not None:
        updates.append("p1_score = ?")
        params.append(p1_score)
    if p2_score is not None:
        updates.append("p2_score = ?")
        params.append(p2_score)
    if state is not None:
        updates.append("state_json = ?")
        params.append(json.dumps(state, ensure_ascii=False))
    if status is not None:
        updates.append("status = ?")
        params.append(status)
    if message_id is not None:
        updates.append("message_id = ?")
        params.append(message_id)

    params.append(game_id)
    sql = f"UPDATE business_games SET {', '.join(updates)} WHERE game_id = ?"
    conn.execute(sql, params)
    conn.commit()


async def get_partner_name(bot, conn: sqlite3.Connection, chat_id: int) -> str:
    """Находит красивое имя собеседника из bot_users, messages или через get_chat."""
    if not chat_id:
        return "Собеседник"

    # 1. Проверяем bot_users
    try:
        row = conn.execute("SELECT first_name FROM bot_users WHERE user_id = ?", (chat_id,)).fetchone()
        if row and row[0]:
            return row[0].strip()
    except Exception:
        pass

    # 2. Проверяем последние сообщения
    try:
        row = conn.execute(
            "SELECT from_user_name FROM messages WHERE from_user_id = ? ORDER BY date DESC LIMIT 1",
            (chat_id,),
        ).fetchone()
        if row and row[0] and row[0].strip() != "Unknown":
            return row[0].strip()
    except Exception:
        pass

    # 3. get_chat у Telegram
    try:
        chat = await bot.get_chat(chat_id)
        if chat:
            if chat.first_name:
                return chat.first_name.strip()
            if chat.title:
                return chat.title.strip()
    except Exception:
        pass

    return "Собеседник"
