# webapp_server.py
"""
Асинхронный сервер для Telegram Mini App (на aiohttp).
Работает в общем event loop с ботом, обслуживает фронтенд и REST API:
- GET /                    -> webapp/index.html
- GET /api/me              -> данные профиля, статистика, настройки времени и описания
- POST /api/settings/time  -> сохранение настроек времени и погоды
- POST /api/action/refresh_name -> немедленное обновление фамилии в Telegram
- POST /api/settings/bio   -> сохранение строк и интервала авто-описания
- POST /api/action/reset_bio -> отключение и сброс авто-описания
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, Tuple
from urllib.parse import parse_qsl

from aiohttp import web

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
from premium_db import is_premium
from bio_worker import set_business_account_bio, apply_bio_for_connection
from description_commands import format_bio_with_watermark

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

        # Формируем data_check_string: отсортированные k=v, разделенные \n
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))

        # secret_key = HMAC_SHA256(bot_token, "WebAppData")
        secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
        # calculated_hash = HMAC_SHA256(data_check_string, secret_key)
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

    # Режим отладки для локального тестирования (если передан ?dev_user=ID или DEV_MODE=1)
    if os.getenv("DEV_MODE") == "1" or request.query.get("dev") == "1":
        dev_id = int(request.query.get("user_id") or default_owner_id)
        return {
            "id": dev_id,
            "first_name": "Developer",
            "username": "developer",
            "is_premium": False,
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

        # 3. Настройки описания
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

        return web.json_response({
            "ok": True,
            "user": {
                "id": user_id,
                "first_name": user.get("first_name", "Пользователь"),
                "username": user.get("username", ""),
                "is_premium": user_prem,
            },
            "business": {
                "is_connected": bool(bc_id),
                "connection_id": bc_id,
            },
            "stats": stats,
            "time": time_data,
            "bio": bio_data,
        })

    async def handle_save_time_settings(self, request: web.Request) -> web.Response:
        """Сохранение настроек времени и погоды в нике."""
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
        """Принудительное обновление фамилии в Telegram."""
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

    async def handle_save_bio_settings(self, request: web.Request) -> web.Response:
        """Сохранение настроек авто-описания."""
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

        # Если активно и есть бизнес-подключение — обновляем био сразу
        if is_active and lines and bc_id:
            prem = is_premium(self.conn, user_id)
            target_bio = format_bio_with_watermark(lines[0], prem, conn=self.conn, user_id=user_id)
            try:
                await set_business_account_bio(self.bot, bc_id, target_bio)
            except Exception as e:
                log.warning("set_business_account_bio failed: %s", e)

        return web.json_response({"ok": True, "message": "Авто-описание сохранено"})

    async def handle_reset_bio(self, request: web.Request) -> web.Response:
        """Отключение и сброс описания профиля."""
        user, err_resp = self._get_user_or_401(request)
        if err_resp:
            return err_resp

        user_id = int(user["id"])
        self.conn.execute("UPDATE auto_descriptions SET is_active = 0 WHERE user_id = ?", (user_id,))
        self.conn.execute("UPDATE pair_bios SET status = 'cancelled' WHERE (user_1 = ? OR user_2 = ?) AND status = 'active'", (user_id, user_id))
        self.conn.commit()

        bc_id = get_user_bc_id(self.conn, user_id)
        if bc_id:
            try:
                await apply_bio_for_connection(self.bot, self.conn, bc_id, user_id)
            except Exception as e:
                log.warning("apply_bio_for_connection failed: %s", e)

        return web.json_response({"ok": True, "message": "Авто-описание отключено"})


async def create_webapp_app(ptb_app, conn: sqlite3.Connection, bot_token: str, owner_id: int) -> web.Application:
    """Создает и настраивает web.Application для Mini App."""
    webapp_dir = os.path.join(os.path.dirname(__file__), "webapp")
    handler = WebAppHandler(ptb_app, conn, bot_token, owner_id, webapp_dir)

    app = web.Application(middlewares=[cors_middleware])

    # Статика фронтенда
    app.router.add_get("/", handler.handle_index)
    app.router.add_get("/index.html", handler.handle_index)

    # API маршруты
    app.router.add_get("/api/me", handler.handle_get_me)
    app.router.add_post("/api/settings/time", handler.handle_save_time_settings)
    app.router.add_post("/api/action/refresh_name", handler.handle_refresh_name)
    app.router.add_post("/api/settings/bio", handler.handle_save_bio_settings)
    app.router.add_post("/api/action/reset_bio", handler.handle_reset_bio)

    return app


async def start_webapp_server(ptb_app, conn: sqlite3.Connection, bot_token: str, owner_id: int, host: str = "0.0.0.0", port: int = 8080):
    """Фоновая корутина для запуска HTTP-сервера Mini App внутри asyncio event loop."""
    app = await create_webapp_app(ptb_app, conn, bot_token, owner_id)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)

    try:
        await site.start()
        log.info("🚀 Mini App сервер запущен на http://%s:%d", host, port)
        # Держим сервер запущенным
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        log.info("Остановка Mini App сервера…")
    finally:
        await runner.cleanup()
