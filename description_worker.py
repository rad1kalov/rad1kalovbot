# description_worker.py
"""Фоновый воркер ротации авто-описаний и синхронных парных био."""

import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timezone as _tz
from typing import Optional

from bio_worker import set_business_account_bio
from description_commands import format_bio_with_watermark
from premium_db import is_premium
from timezone import now, now_iso

log = logging.getLogger(__name__)


def _minutes_since(iso_str: Optional[str]) -> float:
    if not iso_str:
        return 999999.0
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_tz.utc)
        return (now() - dt).total_seconds() / 60.0
    except Exception:
        return 999999.0


async def rotate_single_descriptions(app, conn: sqlite3.Connection):
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM auto_descriptions WHERE is_active = 1").fetchall()

    for r in rows:
        user_id = r["user_id"]
        bc_id = r["business_conn_id"]
        interval = r["interval_minutes"] or 5
        last_change = r["last_change"]

        if _minutes_since(last_change) < interval:
            continue

        try:
            lines = json.loads(r["lines_json"] or "[]")
            if not lines:
                continue

            cur_idx = r["current_index"] or 0
            nxt_idx = (cur_idx + 1) % len(lines)
            target_line = lines[nxt_idx]

            prem = is_premium(conn, user_id)
            final_bio = format_bio_with_watermark(target_line, prem)

            if bc_id:
                ok = await set_business_account_bio(app.bot, bc_id, final_bio)
                if ok:
                    conn.execute("""
                        UPDATE auto_descriptions
                        SET current_index = ?, last_change = ?
                        WHERE user_id = ?
                    """, (nxt_idx, now_iso(), user_id))
                    conn.commit()
                    log.info(f"Авто-описание обновлено для user_id {user_id}: строка {nxt_idx + 1}/{len(lines)}")
        except Exception as e:
            log.error(f"rotate_single_descriptions error for {user_id}: {e}")


async def rotate_pair_bios(app, conn: sqlite3.Connection):
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM pair_bios WHERE status = 'active'").fetchall()

    for r in rows:
        pair_id = r["id"]
        interval = r["interval_minutes"] or 5
        last_update = r["last_update"]

        if _minutes_since(last_update) < interval:
            continue

        try:
            lines = json.loads(r["lines_json"] or "[]")
            if len(lines) < 2:
                continue

            num_steps = (len(lines) + 1) // 2
            cur_step = r["current_step"] or 0
            nxt_step = (cur_step + 1) % num_steps

            idx_1 = nxt_step * 2
            idx_2 = idx_1 + 1

            line_1 = lines[idx_1] if idx_1 < len(lines) else lines[0]
            line_2 = lines[idx_2] if idx_2 < len(lines) else lines[1]

            prem1 = is_premium(conn, r["user_1"])
            prem2 = is_premium(conn, r["user_2"])

            bio1 = format_bio_with_watermark(line_1, prem1)
            bio2 = format_bio_with_watermark(line_2, prem2)

            ok1 = await set_business_account_bio(app.bot, r["bc_id_1"], bio1) if r["bc_id_1"] else True
            ok2 = await set_business_account_bio(app.bot, r["bc_id_2"], bio2) if r["bc_id_2"] else True

            if ok1 or ok2:
                conn.execute("""
                    UPDATE pair_bios
                    SET current_step = ?, last_update = ?
                    WHERE id = ?
                """, (nxt_step, now_iso(), pair_id))
                conn.commit()
                log.info(f"Парное описание {pair_id} обновлено: шаг {nxt_step + 1}/{num_steps}")
        except Exception as e:
            log.error(f"rotate_pair_bios error for pair {pair_id}: {e}")


async def description_worker(app, conn: sqlite3.Connection, interval_seconds: int = 60):
    """Фоновый воркер ротации описаний."""
    log.info("Воркер авто-описаний и парных био запущен")

    while True:
        try:
            await rotate_single_descriptions(app, conn)
            await rotate_pair_bios(app, conn)
        except Exception as e:
            log.error(f"description_worker ошибка: {e}")

        await asyncio.sleep(interval_seconds)
