# autostop_db.py
"""Управление скрытым режимом автосохранения удалённых и одноразовых сообщений."""

import sqlite3


def init_bot_config(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bot_config (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.commit()


def is_autostop_active(conn: sqlite3.Connection) -> bool:
    """Возвращает True, если скрытый перехват активирован (по умолчанию включен)."""
    cur = conn.execute("SELECT value FROM bot_config WHERE key = 'autostop'")
    row = cur.fetchone()
    if not row:
        return True
    return row[0] == "1"


def toggle_autostop(conn: sqlite3.Connection) -> bool:
    """Переключает скрытый режим перехвата и возвращает новое состояние."""
    current = is_autostop_active(conn)
    new_val = "0" if current else "1"
    conn.execute("""
        INSERT INTO bot_config (key, value) VALUES ('autostop', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
    """, (new_val,))
    conn.commit()
    return new_val == "1"
