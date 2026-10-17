# webapp_server.py
"""
Асинхронный сервер для Telegram Mini App (на aiohttp).
Работает в общем event loop с ботом, обслуживает фронтенд и REST API:
- GET /                    -> webapp/index.html
- GET /api/me              -> данные профиля, статистика, настройки времени, авто-описания, парного био и админ-ранг
- POST /api/settings/time  -> сохранение настроек времени и погоды
- POST /api/action/refresh_name -> немедленное обновление фамилии в Telegram
- POST /api/settings/bio   -> сохранение строк и интервала одиночного авто-описания
- POST /api/action/reset_bio -> отключение и сброс одиночного авто-описания
- POST /api/settings/pair_bio -> отправка предложения парного описания партнеру
- POST /api/action/cancel_pair_bio -> отмена / отключение парного описания
- GET /api/admin/overview  -> сводка для админ-панели (список админов, статистика)
- POST /api/admin/add_admin -> добавление админа
- POST /api/admin/del_admin -> удаление админа
- POST /api/admin/grant_feature -> выдача Premium или отдельных функций
- POST /api/admin/broadcast -> массовая рассылка по пользователям бота
- POST /api/admin/tag_mode -> переключение тега в описании для админов
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, Tuple, List
from urllib.parse import parse_qsl

from aiohttp import web
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from tn_db import (
    get_webapp_stats,
    get_user_setting,
    get_user_bc_id,
    set_time_enabled,
    set_night_status,
    set_weather_settings,
    set_tz_offset,
)
from tn_worker import update_single_user_name
from premium_db import is_premium, activate_premium
from bio_worker import set_business_account_bio, apply_bio_for_connection
from description_commands import format_bio_with_watermark
from admin_db import (
    is_admin,
    add_admin,
    del_admin,
    get_all_admins,
    resolve_user_id,
    grant_feature,
    get_all_broadcast_users,
    get_admin_tag_mode,
    set_admin_tag_mode,
)
from timezone import now_iso

log = logging.getLogger(__name__)


def validate_telegram_init_data(init_data: str, bot_token: str) -> Optional[Dict[str, Any]]:
    """
    Проверяет валидность строки initData по протоколу Telegram WebApp.
    Возвращает словарь данных (включая 'user') при успехе, иначе None.
    """
    if not init_data or not bot_token:
        return None
    try:
        parsed = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = parsed.pop("hash", None)
        if not received_hash:
            return None

        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(calculated_hash, received_hash):
            return None

        if "user" in parsed:
            parsed["user"] = json.loads(parsed["user"])
        return parsed
    except Exception as e:
        log.warning("Ошибка проверки Telegram initData: %s", e)
        return None


def get_auth_user(request: web.Request, bot_token: str, default_owner_id: int) -> Optional[Dict[str, Any]]:
    """Извлекает и валидирует пользователя из заголовка Authorization или query-параметра."""
    auth_header = request.headers.get("Authorization", "")
    init_data = ""
    if auth_header.startswith("tma "):
        init_data = auth_header[4:].strip()
    elif "tgWebAppData" in request.query:
        init_data = request.query.get("tgWebAppData", "").strip()

    if init_data:
        validated = validate_telegram_init_data(init_data, bot_token)
        if validated and "user" in validated:
            return validated["user"]

    # Режим отладки для локального тестирования
    if os.getenv("DEV_MODE") == "1" or request.query.get("dev") == "1":
        dev_id = int(request.query.get("user_id") or default_owner_id)
        return {
            "id": dev_id,
            "first_name": "Developer",
            "username": "developer",
            "is_premium": True,
        }

    return None


@web.middleware
async def cors_middleware(request: web.Request, handler):
    if request.method == "OPTIONS":
        resp = web.Response(status=204)
    else:
        try:
            resp = await handler(request)
        except web.HTTPException as ex:
            resp = ex

    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
    return resp


class WebAppHandler:
    def __init__(self, ptb_app, conn: sqlite3.Connection, bot_token: str, owner_id: int, webapp_dir: str):
        self.ptb_app = ptb_app
        self.bot = ptb_app.bot
        self.conn = conn
        self.bot_token = bot_token
        self.owner_id = owner_id
        self.webapp_dir = webapp_dir

    def _get_user_or_401(self, request: web.Request) -> Tuple[Optional[Dict[str, Any]], Optional[web.Response]]:
        user = get_auth_user(request, self.bot_token, self.owner_id)
        if not user:
            return None, web.json_response({"ok": False, "error": "Unauthorized: invalid Telegram initData"}, status=401)
        return user, None

    def _require_admin(self, request: web.Request) -> Tuple[Optional[Dict[str, Any]], Optional[web.Response]]:
        user, err_resp = self._get_user_or_401(request)
        if err_resp:
            return None, err_resp
        user_id = int(user["id"])
        if not is_admin(self.conn, user_id):
            return None, web.json_response({"ok": False, "error": "Доступ запрещен: требуется ранг администратора"}, status=403)
        return user, None

    async def handle_index(self, request: web.Request) -> web.Response:
        """Отдает статическую страницу Mini App."""
        index_path = os.path.join(self.webapp_dir, "index.html")
        if not os.path.exists(index_path):
            return web.Response(text="webapp/index.html not found", status=404)
        with open(index_path, "r", encoding="utf-8") as f:
            content = f.read()
        return web.Response(text=content, content_type="text/html")

    async def handle_get_me(self, request: web.Request) -> web.Response:
        """Сводная информация для экрана Mini App."""
        user, err_resp = self._get_user_or_401(request)
        if err_resp:
            return err_resp

        user_id = int(user["id"])
        user_prem = is_premium(self.conn, user_id)
        bc_id = get_user_bc_id(self.conn, user_id)

        # Проверка прав администратора
        is_adm = is_admin(self.conn, user_id)
        admin_rank = "Владелец" if user_id == self.owner_id else ("Администратор" if is_adm else None)
        tag_mode = get_admin_tag_mode(self.conn, user_id) if is_adm else "off"

        # 1. Статистика
        stats = get_webapp_stats(self.conn, user_id)

        # 2. Настройки времени
        setting = get_user_setting(self.conn, user_id) or {}
        offset = setting.get("tz_offset", 3)
        if offset is None:
            offset = 3

        tz = timezone(timedelta(hours=offset))
        now_local = datetime.now(tz)
        current_time_str = now_local.strftime("%H:%M")

        time_data = {
            "time_enabled": bool(setting.get("time_enabled", 0)),
            "night_status_enabled": bool(setting.get("night_status_enabled", 0)),
            "weather_enabled": bool(setting.get("weather_enabled", 0)),
            "weather_city": setting.get("weather_city") or "",
            "tz_offset": offset,
            "current_time": current_time_str,
        }

        # 3. Настройки одиночного описания
        self.conn.row_factory = sqlite3.Row
        auto_desc = self.conn.execute("SELECT * FROM auto_descriptions WHERE user_id = ?", (user_id,)).fetchone()
        bio_lines = []
        bio_active = False
        bio_interval = 5
        if auto_desc:
            bio_active = bool(auto_desc["is_active"])
            bio_interval = auto_desc["interval_minutes"] or 5
            try:
                bio_lines = json.loads(auto_desc["lines_json"])
            except Exception:
                bio_lines = []

        bio_data = {
            "is_active": bio_active,
            "interval_minutes": bio_interval,
            "lines": bio_lines,
        }

        # 4. Настройки парного описания
        pair_row = self.conn.execute("""
            SELECT * FROM pair_bios
            WHERE (user_1 = ? OR user_2 = ?) AND status IN ('active', 'pending')
            ORDER BY id DESC LIMIT 1
        """, (user_id, user_id)).fetchone()

        pair_bio_data = None
        if pair_row:
            is_u1 = (user_id == pair_row["user_1"])
            partner_id = pair_row["user_2"] if is_u1 else pair_row["user_1"]
            partner_username = pair_row["username_2"] if is_u1 else pair_row["username_1"]
            p_lines = []
            try:
                p_lines = json.loads(pair_row["lines_json"])
            except Exception:
                pass
            pair_bio_data = {
                "id": pair_row["id"],
                "status": pair_row["status"],
                "is_initiator": is_u1,
                "partner_id": partner_id,
                "partner_username": partner_username or f"ID: {partner_id}",
                "interval_minutes": pair_row["interval_minutes"],
                "lines": p_lines,
                "current_step": pair_row["current_step"],
            }

        return web.json_response({
            "ok": True,
            "user": {
                "id": user_id,
                "first_name": user.get("first_name", "Пользователь"),
                "username": user.get("username", ""),
                "is_premium": user_prem,
                "is_admin": is_adm,
                "admin_rank": admin_rank,
                "tag_mode": tag_mode,
            },
            "business": {
                "is_connected": bool(bc_id),
                "connection_id": bc_id,
            },
            "stats": stats,
            "time": time_data,
            "bio": bio_data,
            "pair_bio": pair_bio_data,
        })

    # ─── Управление временем и профилем ───

    async def handle_save_time_settings(self, request: web.Request) -> web.Response:
        user, err_resp = self._get_user_or_401(request)
        if err_resp:
            return err_resp

        user_id = int(user["id"])
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)

        if "time_enabled" in body:
            set_time_enabled(self.conn, user_id, bool(body["time_enabled"]))

        if "night_status_enabled" in body:
            set_night_status(self.conn, user_id, bool(body["night_status_enabled"]))

        if "tz_offset" in body:
            try:
                set_tz_offset(self.conn, user_id, int(body["tz_offset"]))
            except (ValueError, TypeError):
                pass

        if "weather_enabled" in body or "weather_city" in body:
            set_weather_settings(
                self.conn,
                user_id,
                city=body.get("weather_city") or None,
                enabled=bool(body.get("weather_enabled", False)),
            )

        return web.json_response({"ok": True, "message": "Настройки времени сохранены"})

    async def handle_refresh_name(self, request: web.Request) -> web.Response:
        user, err_resp = self._get_user_or_401(request)
        if err_resp:
            return err_resp

        user_id = int(user["id"])
        setting = get_user_setting(self.conn, user_id)
        if not setting or not setting.get("business_connection_id"):
            return web.json_response({"ok": False, "error": "Бизнес-аккаунт не подключен"}, status=400)

        try:
            await update_single_user_name(self.bot, self.conn, setting)
            return web.json_response({"ok": True, "message": "Имя обновлено"})
        except Exception as e:
            log.error("refresh_name error: %s", e)
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    # ─── Управление одиночным био ───

    async def handle_save_bio_settings(self, request: web.Request) -> web.Response:
        user, err_resp = self._get_user_or_401(request)
        if err_resp:
            return err_resp

        user_id = int(user["id"])
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)

        lines = [line.strip() for line in body.get("lines", []) if line.strip()]
        if not lines:
            return web.json_response({"ok": False, "error": "Необходимо указать хотя бы одну строку"}, status=400)

        interval = int(body.get("interval_minutes", 5))
        is_active = 1 if body.get("is_active", True) else 0

        bc_id = get_user_bc_id(self.conn, user_id)

        self.conn.execute("""
            INSERT INTO auto_descriptions (user_id, business_conn_id, lines_json, interval_minutes, current_index, is_active, last_change)
            VALUES (?, ?, ?, ?, 0, ?, datetime('now'))
            ON CONFLICT(user_id) DO UPDATE SET
                business_conn_id = COALESCE(excluded.business_conn_id, auto_descriptions.business_conn_id),
                lines_json = excluded.lines_json,
                interval_minutes = excluded.interval_minutes,
                current_index = 0,
                is_active = excluded.is_active,
                last_change = datetime('now')
        """, (user_id, bc_id, json.dumps(lines, ensure_ascii=False), interval, is_active))
        self.conn.commit()

        if is_active and lines and bc_id:
            prem = is_premium(self.conn, user_id)
            target_bio = format_bio_with_watermark(lines[0], prem, conn=self.conn, user_id=user_id)
            try:
                await set_business_account_bio(self.bot, bc_id, target_bio)
            except Exception as e:
                log.warning("set_business_account_bio failed: %s", e)

        return web.json_response({"ok": True, "message": "Авто-описание сохранено"})

    async def handle_reset_bio(self, request: web.Request) -> web.Response:
        user, err_resp = self._get_user_or_401(request)
        if err_resp:
            return err_resp

        user_id = int(user["id"])
        self.conn.execute("UPDATE auto_descriptions SET is_active = 0 WHERE user_id = ?", (user_id,))
        self.conn.commit()

        bc_id = get_user_bc_id(self.conn, user_id)
        if bc_id:
            try:
                await apply_bio_for_connection(self.bot, self.conn, bc_id, user_id)
            except Exception as e:
                log.warning("apply_bio_for_connection failed: %s", e)

        return web.json_response({"ok": True, "message": "Авто-описание отключено"})

    # ─── Управление парным описанием (Pair Bio) ───

    async def handle_create_pair_bio(self, request: web.Request) -> web.Response:
        """Отправляет предложение установить синхронное парное био."""
        user, err_resp = self._get_user_or_401(request)
        if err_resp:
            return err_resp

        user_id = int(user["id"])
        user_name = user.get("username") or user.get("first_name", "Собеседник")

        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)

        partner_input = str(body.get("partner_username", "")).strip().lstrip("@")
        if not partner_input:
            return web.json_response({"ok": False, "error": "Укажите @username или ID собеседника"}, status=400)

        friend_id = resolve_user_id(self.conn, partner_input)
        if not friend_id:
            return web.json_response({
                "ok": False,
                "error": f"Пользователь @{partner_input} не найден в базе. Друг должен хотя бы раз запустить бота!"
            }, status=404)

        if friend_id == user_id:
            return web.json_response({"ok": False, "error": "Нельзя установить парное био с самим собой!"}, status=400)

        lines = [l.strip() for l in body.get("lines", []) if l.strip()]
        if len(lines) < 2:
            return web.json_response({"ok": False, "error": "Добавьте минимум 2 строки (для вас и собеседника)"}, status=400)

        interval = int(body.get("interval_minutes", 5))
        if interval < 1:
            interval = 5

        my_bc_id = get_user_bc_id(self.conn, user_id)
        friend_bc_id = get_user_bc_id(self.conn, friend_id)

        # Отменяем старые незаконченные приглашения
        self.conn.execute("""
            UPDATE pair_bios SET status = 'cancelled'
            WHERE (user_1 = ? OR user_2 = ?) AND status IN ('pending', 'active')
        """, (user_id, user_id))

        cur = self.conn.execute("""
            INSERT INTO pair_bios (
                user_1, user_2, username_1, username_2,
                bc_id_1, bc_id_2, lines_json, interval_minutes,
                current_step, status, last_update
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 'pending', ?)
        """, (user_id, friend_id, user_name, partner_input, my_bc_id, friend_bc_id, json.dumps(lines, ensure_ascii=False), interval, now_iso()))
        self.conn.commit()
        pair_id = cur.lastrowid

        # Формируем и отправляем приглашение другу в Telegram
        odd_preview = "\n".join([f"• {lines[i]}" for i in range(0, len(lines), 2)])
        even_preview = "\n".join([f"• {lines[i]}" for i in range(1, len(lines), 2)])

        invitation_text = (
            "💌 *Предложение парного описания!*\n\n"
            f"Пользователь @{user_name} предлагает вам установить синхронные парные описания.\n\n"
            f"📜 *Ваши строки:*\n{even_preview}\n\n"
            f"📜 *Строки @{user_name}:*\n{odd_preview}\n\n"
            f"⏱ Интервал смены: каждые *{interval} мин.*\n\n"
            "Принять предложение?"
        )
        invitation_kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Принять", callback_data=f"pbio:accept:{pair_id}"),
                InlineKeyboardButton("❌ Отклонить", callback_data=f"pbio:reject:{pair_id}"),
            ]
        ])

        try:
            await self.bot.send_message(
                chat_id=friend_id,
                text=invitation_text,
                reply_markup=invitation_kb,
                parse_mode="Markdown",
            )
        except Exception as e:
            log.warning("Не удалось отправить сообщение другу %s: %s", friend_id, e)
            return web.json_response({
                "ok": False,
                "error": f"Не удалось отправить уведомление в ЛС другу: {e}. Убедитесь, что друг запустил бота!"
            }, status=400)

        return web.json_response({
            "ok": True,
            "message": f"Предложение отправлено @{partner_input}! Ожидаем подтверждения в боте."
        })

    async def handle_cancel_pair_bio(self, request: web.Request) -> web.Response:
        """Отмена или отключение парного описания."""
        user, err_resp = self._get_user_or_401(request)
        if err_resp:
            return err_resp

        user_id = int(user["id"])

        self.conn.row_factory = sqlite3.Row
        pairs = self.conn.execute("""
            SELECT * FROM pair_bios
            WHERE (user_1 = ? OR user_2 = ?) AND status IN ('active', 'pending')
        """, (user_id, user_id)).fetchall()

        self.conn.execute("""
            UPDATE pair_bios SET status = 'cancelled'
            WHERE (user_1 = ? OR user_2 = ?) AND status IN ('active', 'pending')
        """, (user_id, user_id))
        self.conn.commit()

        # Восстанавливаем оригинальные био для обоих участников
        for p in pairs:
            for uid, bc in [(p["user_1"], p["bc_id_1"]), (p["user_2"], p["bc_id_2"])]:
                if bc:
                    try:
                        await apply_bio_for_connection(self.bot, self.conn, bc, uid)
                    except Exception as e:
                        log.debug("apply_bio_for_connection failed: %s", e)

        return web.json_response({"ok": True, "message": "Парное описание отключено"})

    # ─── Административная панель (Admin Panel) ───

    async def handle_admin_overview(self, request: web.Request) -> web.Response:
        """Сводка данных для вкладки админа в WebApp."""
        user, err_resp = self._require_admin(request)
        if err_resp:
            return err_resp

        user_id = int(user["id"])
        admins_list = get_all_admins(self.conn)
        stats = get_webapp_stats(self.conn)
        tag_mode = get_admin_tag_mode(self.conn, user_id)

        # Список последних пользователей бота
        self.conn.row_factory = sqlite3.Row
        users_rows = self.conn.execute("""
            SELECT user_id, username, first_name, joined_at
            FROM bot_users ORDER BY joined_at DESC LIMIT 20
        """).fetchall()

        return web.json_response({
            "ok": True,
            "owner_id": self.owner_id,
            "is_owner": (user_id == self.owner_id),
            "current_tag_mode": tag_mode,
            "admins": admins_list,
            "global_stats": stats.get("global", {}),
            "recent_users": [dict(r) for r in users_rows],
            "available_features": [
                {"id": "premium", "title": "💎 Полный Премиум"},
                {"id": "custom_stage", "title": "🎭 Кастомные стадии"},
                {"id": "hide_stage", "title": "🔒 Скрытие стадий"},
                {"id": "weather", "title": "⛅️ Погода в имени"},
                {"id": "pair_bio", "title": "👥 Парные описания"},
                {"id": "all", "title": "🌟 Все функции"},
            ],
        })

    async def handle_admin_add_admin(self, request: web.Request) -> web.Response:
        user, err_resp = self._require_admin(request)
        if err_resp:
            return err_resp

        admin_id = int(user["id"])
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)

        target = str(body.get("username_or_id", "")).strip().lstrip("@")
        target_id = resolve_user_id(self.conn, target)
        if not target_id:
            return web.json_response({"ok": False, "error": f"Пользователь @{target} не найден"}, status=404)

        add_admin(self.conn, target_id, target, added_by=admin_id)
        return web.json_response({"ok": True, "message": f"Администратор @{target} успешно добавлен!"})

    async def handle_admin_del_admin(self, request: web.Request) -> web.Response:
        user, err_resp = self._require_admin(request)
        if err_resp:
            return err_resp

        try:
            body = await request.json()
            target_id = int(body.get("user_id", 0))
        except Exception:
            return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)

        if target_id == self.owner_id:
            return web.json_response({"ok": False, "error": "Нельзя удалить владельца бота!"}, status=400)

        del_admin(self.conn, target_id)
        return web.json_response({"ok": True, "message": "Администратор удален"})

    async def handle_admin_grant_feature(self, request: web.Request) -> web.Response:
        user, err_resp = self._require_admin(request)
        if err_resp:
            return err_resp

        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)

        target = str(body.get("username_or_id", "")).strip().lstrip("@")
        target_id = resolve_user_id(self.conn, target)
        if not target_id:
            return web.json_response({"ok": False, "error": f"Пользователь @{target} не найден"}, status=404)

        feature = body.get("feature", "premium")
        days = body.get("days")
        days_int = int(days) if days and int(days) > 0 else None

        grant_feature(self.conn, target_id, feature, days_int)
        if feature in ("premium", "prem", "all"):
            activate_premium(self.conn, target_id, days=days_int, plan=f"{days_int}d" if days_int else "lifetime")

        dur_text = f"на {days_int} дн." if days_int else "навсегда"
        return web.json_response({
            "ok": True,
            "message": f"Функция {feature} успешно выдана @{target} ({dur_text})!"
        })

    async def handle_admin_broadcast(self, request: web.Request) -> web.Response:
        user, err_resp = self._require_admin(request)
        if err_resp:
            return err_resp

        try:
            body = await request.json()
            text = str(body.get("text", "")).strip()
        except Exception:
            return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)

        if not text:
            return web.json_response({"ok": False, "error": "Текст рассылки не может быть пустым"}, status=400)

        recipients = get_all_broadcast_users(self.conn)
        sent = 0
        failed = 0

        for uid in recipients:
            try:
                await self.bot.send_message(chat_id=uid, text=text, parse_mode="Markdown")
                sent += 1
                await asyncio.sleep(0.05)  # Telegram rate limit safety
            except Exception:
                failed += 1

        return web.json_response({
            "ok": True,
            "message": f"Рассылка завершена: доставлено {sent}, ошибок {failed}"
        })

    async def handle_admin_set_tag_mode(self, request: web.Request) -> web.Response:
        user, err_resp = self._require_admin(request)
        if err_resp:
            return err_resp

        user_id = int(user["id"])
        try:
            body = await request.json()
            tag_mode = str(body.get("tag_mode", "off")).strip().lower()
        except Exception:
            return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)

        if tag_mode not in ("off", "admin", "dev", "user"):
            return web.json_response({"ok": False, "error": "Неверный режим тега"}, status=400)

        set_admin_tag_mode(self.conn, user_id, tag_mode)
        return web.json_response({"ok": True, "message": f"Режим тега изменен на {tag_mode}"})


async def create_webapp_app(ptb_app, conn: sqlite3.Connection, bot_token: str, owner_id: int) -> web.Application:
    """Создает и настраивает web.Application для Mini App со всеми маршрутами."""
    webapp_dir = os.path.join(os.path.dirname(__file__), "webapp")
    handler = WebAppHandler(ptb_app, conn, bot_token, owner_id, webapp_dir)

    app = web.Application(middlewares=[cors_middleware])

    # Статика фронтенда
    app.router.add_get("/", handler.handle_index)
    app.router.add_get("/index.html", handler.handle_index)

    # API пользователя
    app.router.add_get("/api/me", handler.handle_get_me)
    app.router.add_post("/api/settings/time", handler.handle_save_time_settings)
    app.router.add_post("/api/action/refresh_name", handler.handle_refresh_name)
    app.router.add_post("/api/settings/bio", handler.handle_save_bio_settings)
    app.router.add_post("/api/action/reset_bio", handler.handle_reset_bio)

    # API парного био
    app.router.add_post("/api/settings/pair_bio", handler.handle_create_pair_bio)
    app.router.add_post("/api/action/cancel_pair_bio", handler.handle_cancel_pair_bio)

    # API админки
    app.router.add_get("/api/admin/overview", handler.handle_admin_overview)
    app.router.add_post("/api/admin/add_admin", handler.handle_admin_add_admin)
    app.router.add_post("/api/admin/del_admin", handler.handle_admin_del_admin)
    app.router.add_post("/api/admin/grant_feature", handler.handle_admin_grant_feature)
    app.router.add_post("/api/admin/broadcast", handler.handle_admin_broadcast)
    app.router.add_post("/api/admin/tag_mode", handler.handle_admin_set_tag_mode)

    return app


async def start_webapp_server(ptb_app, conn: sqlite3.Connection, bot_token: str, owner_id: int, host: str = "0.0.0.0", port: int = 3000):
    """Фоновая корутина для запуска HTTP-сервера Mini App внутри asyncio event loop."""
    app = await create_webapp_app(ptb_app, conn, bot_token, owner_id)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)

    try:
        await site.start()
        log.info("🚀 Mini App сервер запущен на http://%s:%d", host, port)
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        log.info("Остановка Mini App сервера…")
    finally:
        await runner.cleanup()
