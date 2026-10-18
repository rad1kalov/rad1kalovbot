# emoji_status_db.py
"""
Модуль работы с базой данных для управления расписанием Premium-эмодзи-статусов.
Каждый пользователь строго изолирован по user_id.
"""

import sqlite3
import logging
from typing import List, Dict, Any, Optional, Tuple

log = logging.getLogger(__name__)


def init_emoji_status_db(conn: sqlite3.Connection):
    """Создает таблицы для хранения профиля и слотов расписания эмодзи-статусов."""
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS emoji_status_settings (
                user_id INTEGER PRIMARY KEY,
                is_enabled INTEGER DEFAULT 1,
                tz_offset REAL DEFAULT 3.0,
                default_emoji_id TEXT DEFAULT NULL,
                current_active_slot_id INTEGER DEFAULT NULL,
                last_applied_emoji_id TEXT DEFAULT NULL,
                last_applied_at TEXT DEFAULT NULL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS emoji_status_slots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT DEFAULT '',
                start_minutes INTEGER NOT NULL,
                end_minutes INTEGER NOT NULL,
                custom_emoji_id TEXT DEFAULT NULL,
                emoji_preview TEXT DEFAULT '',
                is_active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES emoji_status_settings(user_id) ON DELETE CASCADE
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_emoji_slots_user ON emoji_status_slots(user_id, is_active)")
    log.info("Таблицы emoji_status_settings и emoji_status_slots инициализированы.")


def get_user_emoji_settings(conn: sqlite3.Connection, user_id: int) -> Dict[str, Any]:
    """Возвращает настройки эмодзи-статуса пользователя. Если нет — создает дефолтную запись."""
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM emoji_status_settings WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        tz = 3.0
        try:
            tz_row = conn.execute("SELECT tz_offset FROM user_settings WHERE user_id = ?", (user_id,)).fetchone()
            if tz_row and tz_row[0] is not None:
                tz = float(tz_row[0])
        except Exception:
            pass

        with conn:
            conn.execute("""
                INSERT OR IGNORE INTO emoji_status_settings (user_id, is_enabled, tz_offset)
                VALUES (?, 1, ?)
            """, (user_id, tz))
        row = conn.execute("SELECT * FROM emoji_status_settings WHERE user_id = ?", (user_id,)).fetchone()
    return dict(row) if row else {"user_id": user_id, "is_enabled": 1, "tz_offset": 3.0}


def update_user_emoji_settings(
    conn: sqlite3.Connection,
    user_id: int,
    is_enabled: Optional[bool] = None,
    tz_offset: Optional[float] = None,
    default_emoji_id: Optional[str] = None
) -> None:
    """Обновляет глобальные настройки эмодзи-статуса для пользователя."""
    fields = []
    params = []
    if is_enabled is not None:
        fields.append("is_enabled = ?")
        params.append(1 if is_enabled else 0)
    if tz_offset is not None:
        fields.append("tz_offset = ?")
        params.append(float(tz_offset))
    if default_emoji_id is not None:
        fields.append("default_emoji_id = ?")
        params.append(default_emoji_id if default_emoji_id != "" else None)

    if not fields:
        return

    fields.append("updated_at = CURRENT_TIMESTAMP")
    params.append(user_id)
    with conn:
        conn.execute(f"UPDATE emoji_status_settings SET {', '.join(fields)} WHERE user_id = ?", params)


def get_user_emoji_slots(conn: sqlite3.Connection, user_id: int) -> List[Dict[str, Any]]:
    """Возвращает список всех слотов расписания пользователя, отсортированных по времени начала."""
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT id, user_id, title, start_minutes, end_minutes, custom_emoji_id, emoji_preview, is_active, created_at
        FROM emoji_status_slots
        WHERE user_id = ?
        ORDER BY start_minutes ASC
    """, (user_id,)).fetchall()
    return [dict(r) for r in rows]


def get_slot_by_id(conn: sqlite3.Connection, slot_id: int, user_id: int) -> Optional[Dict[str, Any]]:
    """Возвращает конкретный слот пользователя с гарантией изоляции по user_id."""
    conn.row_factory = sqlite3.Row
    row = conn.execute("""
        SELECT * FROM emoji_status_slots WHERE id = ? AND user_id = ?
    """, (slot_id, user_id)).fetchone()
    return dict(row) if row else None


def check_intervals_overlap(slots: List[Dict[str, Any]], new_start: int, new_end: int, exclude_id: Optional[int] = None) -> bool:
    """
    Проверяет пересечение интервала [new_start, new_end) со списком существующих слотов.
    Корректно обрабатывает интервалы с переходом через полночь (например, 23:00 -> 07:00).
    """
    def expand_ranges(s: int, e: int):
        s = s % 1440
        e = e % 1440
        if s < e:
            return [(s, e)]
        elif s > e:
            return [(s, 1440), (0, e)]
        else:
            return [(0, 1440)]

    new_ranges = expand_ranges(new_start, new_end)

    for slot in slots:
        if exclude_id and slot.get("id") == exclude_id:
            continue
        s, e = slot["start_minutes"], slot["end_minutes"]
        slot_ranges = expand_ranges(s, e)

        for ns, ne in new_ranges:
            for es, ee in slot_ranges:
                if max(ns, es) < min(ne, ee):
                    return True
    return False


def save_user_slots_from_webapp(
    conn: sqlite3.Connection,
    user_id: int,
    slots_data: List[Dict[str, Any]]
) -> Tuple[bool, str]:
    """
    Сохраняет слоты пользователя из Mini App с валидацией пересечений и сохранением уже привязанных эмодзи.
    """
    get_user_emoji_settings(conn, user_id)  # гарантируем наличие профиля

    normalized = []
    for idx, item in enumerate(slots_data):
        try:
            s = int(item["start_minutes"]) % 1440
            e = int(item["end_minutes"]) % 1440
        except (KeyError, ValueError, TypeError):
            return False, f"Некорректный формат времени в слоте #{idx + 1}"

        if s == e:
            return False, f"Слот '{item.get('title', idx + 1)}' не может иметь нулевую длительность"

        normalized.append({
            "id": item.get("id"),
            "title": str(item.get("title", f"Интервал {idx + 1}"))[:32],
            "start_minutes": s,
            "end_minutes": e,
            "custom_emoji_id": item.get("custom_emoji_id"),
            "emoji_preview": item.get("emoji_preview", ""),
            "is_active": 1 if item.get("is_active", True) else 0,
        })

    # Проверяем взаимные пересечения между всеми предложенными слотами
    for i in range(len(normalized)):
        for j in range(i + 1, len(normalized)):
            if check_intervals_overlap([normalized[i]], normalized[j]["start_minutes"], normalized[j]["end_minutes"]):
                t1 = normalized[i]["title"] or f"#{i+1}"
                t2 = normalized[j]["title"] or f"#{j+1}"
                return False, f"Пересечение слотов '{t1}' и '{t2}'"

    # Получаем уже существующие привязанные эмодзи для слотов пользователя
    existing_slots = {
        r["id"]: dict(r) for r in conn.execute(
            "SELECT id, custom_emoji_id, emoji_preview FROM emoji_status_slots WHERE user_id = ?",
            (user_id,)
        ).fetchall()
    }

    with conn:
        # Удаляем текущие слоты пользователя
        conn.execute("DELETE FROM emoji_status_slots WHERE user_id = ?", (user_id,))
        for s in normalized:
            old_slot = existing_slots.get(s.get("id"), {})
            # Сохраняем ранее привязанный custom_emoji_id, если не передан новый
            emoji_id = s.get("custom_emoji_id") or old_slot.get("custom_emoji_id")
            preview = s.get("emoji_preview") or old_slot.get("emoji_preview", "")

            conn.execute("""
                INSERT INTO emoji_status_slots 
                (user_id, title, start_minutes, end_minutes, custom_emoji_id, emoji_preview, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (user_id, s["title"], s["start_minutes"], s["end_minutes"], emoji_id, preview, s["is_active"]))

    return True, "Слоты успешно сохранены"


def link_emoji_to_slot(
    conn: sqlite3.Connection,
    user_id: int,
    slot_id: int,
    custom_emoji_id: str,
    emoji_preview: str = ""
) -> bool:
    """Привязывает custom_emoji_id к слоту пользователя со строгой проверкой владения."""
    with conn:
        cur = conn.execute("""
            UPDATE emoji_status_slots
            SET custom_emoji_id = ?, emoji_preview = ?
            WHERE id = ? AND user_id = ?
        """, (custom_emoji_id, emoji_preview, slot_id, user_id))
        return cur.rowcount > 0


def unlink_emoji_from_slot(conn: sqlite3.Connection, user_id: int, slot_id: int) -> bool:
    """Удаляет привязку эмодзи из слота."""
    with conn:
        cur = conn.execute("""
            UPDATE emoji_status_slots
            SET custom_emoji_id = NULL, emoji_preview = ''
            WHERE id = ? AND user_id = ?
        """, (slot_id, user_id))
        return cur.rowcount > 0


def delete_slot(conn: sqlite3.Connection, user_id: int, slot_id: int) -> bool:
    """Удаляет слот пользователя."""
    with conn:
        cur = conn.execute("DELETE FROM emoji_status_slots WHERE id = ? AND user_id = ?", (slot_id, user_id))
        return cur.rowcount > 0
