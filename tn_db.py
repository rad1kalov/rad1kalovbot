import aiosqlite

DB_PATH = "messages.db"

async def init_settings_table():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                business_connection_id TEXT PRIMARY KEY,
                tz_offset INTEGER DEFAULT 0,
                first_name TEXT
            )
        """)
        await db.commit()

async def set_tz_offset(business_connection_id: str, offset: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO settings (business_connection_id, tz_offset)
            VALUES (?, ?)
            ON CONFLICT(business_connection_id)
            DO UPDATE SET tz_offset = excluded.tz_offset
        """, (business_connection_id, offset))
        await db.commit()

async def save_first_name(business_connection_id: str, first_name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO settings (business_connection_id, first_name)
            VALUES (?, ?)
            ON CONFLICT(business_connection_id)
            DO UPDATE SET first_name = COALESCE(settings.first_name, excluded.first_name)
        """, (business_connection_id, first_name))
        await db.commit()

async def get_all_connections():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT business_connection_id, tz_offset, first_name FROM settings"
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]