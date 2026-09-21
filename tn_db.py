import sqlite3


def init_settings_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            business_connection_id TEXT PRIMARY KEY,
            tz_offset INTEGER DEFAULT 0,
            first_name TEXT
        )
    """)
    conn.commit()


def set_tz_offset(conn: sqlite3.Connection, business_connection_id: str, offset: int) -> None:
    conn.execute("""
        INSERT INTO settings (business_connection_id, tz_offset)
        VALUES (?, ?)
        ON CONFLICT(business_connection_id)
        DO UPDATE SET tz_offset = excluded.tz_offset
    """, (business_connection_id, offset))
    conn.commit()


def save_first_name(conn: sqlite3.Connection, business_connection_id: str, first_name: str) -> None:
    conn.execute("""
        INSERT INTO settings (business_connection_id, first_name)
        VALUES (?, ?)
        ON CONFLICT(business_connection_id)
        DO UPDATE SET first_name = COALESCE(settings.first_name, excluded.first_name)
    """, (business_connection_id, first_name))
    conn.commit()


def get_all_connections(conn: sqlite3.Connection):
    cur = conn.execute(
        "SELECT business_connection_id, tz_offset, first_name FROM settings"
    )
    rows = cur.fetchall()
    return [
        {"business_connection_id": r[0], "tz_offset": r[1], "first_name": r[2]}
        for r in rows
    ]