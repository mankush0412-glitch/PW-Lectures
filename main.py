#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# PW DRM Bot — python-telegram-bot 20.x (Bot API polling, no MTProto)

import os
import asyncio
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram.ext import Application, ContextTypes
from telegram import Update

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s - %(levelname)s] - %(name)s - %(message)s",
    datefmt="%d-%b-%y %H:%M:%S",
    handlers=[logging.StreamHandler()],
)
LOGGER = logging.getLogger("PW-DRM")


class Config:
    BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
    API_ID    = int(os.environ.get("API_ID", "0"))
    API_HASH  = os.environ.get("API_HASH", "").strip()

    _owner_raw = (os.environ.get("OWNER_ID") or os.environ.get("ADMIN_IDS") or "").strip()
    OWNER_ID   = int(_owner_raw.split(",")[0].strip()) if _owner_raw else 0

    AUTH_USERS = list(map(int, filter(None, os.environ.get("AUTH_USERS", "").split(","))))
    LOG_CH     = os.environ.get("LOG_CH", "").strip()
    MONGO_URI  = os.environ.get("MONGO_URI", "").strip().strip('"').strip("'")

    DOWNLOAD_DIR    = os.environ.get("DOWNLOAD_DIR", "./DOWNLOADS")
    PW_TOKEN        = os.environ.get("PW_TOKEN", "").strip()
    PW_MOBILE       = os.environ.get("PW_MOBILE", "").strip()
    THUMB_URL       = os.environ.get("THUMB_URL", "").strip()
    MP4DECRYPT      = os.environ.get("MP4DECRYPT_PATH", "mp4decrypt")
    FFMPEG          = os.environ.get("FFMPEG_PATH", "ffmpeg")
    N_M3U8DL        = os.environ.get("N_M3U8DL_RE_PATH", "")
    MAX_PARALLEL_DL = int(os.environ.get("MAX_PARALLEL_DL", "2"))
    DEFAULT_QUALITY = os.environ.get("DEFAULT_QUALITY", "best")
    TG_MAX_SIZE     = 50 * 1024 * 1024   # 50 MB Bot API hard limit
    PORT            = int(os.environ.get("PORT", "8080"))
    RENDER_URL      = os.environ.get("RENDER_URL", "").strip()
    PING_INTERVAL   = int(os.environ.get("PING_INTERVAL", "600"))


class Msg:
    START_MSG  = (
        "<b>🤖 PW DRM Bot</b>\n\n"
        "<b>📥 Usage:</b>\n"
        "1. /pwlogin → phone + OTP\n"
        "2. Send your <code>.txt</code> file\n"
        "3. Bot downloads, decrypts, uploads!\n\n"
        "<b>Commands:</b> /pwlogin /pwstatus /dl /batch /help"
    )
    CMD_UPLOAD = "<b>📤 Uploading:</b> <code>{name}</code>"
    CMD_DL     = "<b>📥 Downloading:</b>\n<code>{name}</code>"
    CMD_KEY    = "<b>🔑 Fetching DRM key...</b>\n<code>{name}</code>"


DB = None


async def init_db():
    global DB
    if not Config.MONGO_URI:
        LOGGER.warning("MONGO_URI not set — sessions stored in memory only")
        return
    try:
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient(Config.MONGO_URI, serverSelectionTimeoutMS=5000)
        await client.admin.command("ping")
        DB = client["pw_drm_bot"]
        LOGGER.info("MongoDB connected!")
    except Exception as e:
        LOGGER.error(f"MongoDB failed: {e}")
        DB = None


# ── Health server (keeps Render Web Service alive) ────────────────────────────

class _Health(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"OK"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def _run_health():
    try:
        HTTPServer(("0.0.0.0", Config.PORT), _Health).serve_forever()
    except Exception as e:
        LOGGER.error(f"Health server error: {e}")


# ── Self-ping (prevents Render free tier spin-down) ───────────────────────────

async def _self_ping(app: Application):
    if not Config.RENDER_URL:
        return
    import aiohttp as _aio
    url = Config.RENDER_URL.rstrip("/") + "/"
    while True:
        await asyncio.sleep(Config.PING_INTERVAL)
        try:
            async with _aio.ClientSession() as s:
                async with s.get(url, timeout=_aio.ClientTimeout(total=10)) as r:
                    LOGGER.info(f"Self-ping OK {r.status}")
        except Exception as e:
            LOGGER.warning(f"Self-ping failed: {e}")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    os.makedirs(Config.DOWNLOAD_DIR, exist_ok=True)

    if not Config.BOT_TOKEN:
        LOGGER.error("BOT_TOKEN missing! Set it in Render → Environment.")
        exit(1)

    LOGGER.info(f"Starting PW-DRM-Bot | OWNER_ID={Config.OWNER_ID}")

    threading.Thread(target=_run_health, daemon=True).start()
    LOGGER.info(f"Health server on port {Config.PORT}")

    from plugins.pw import setup_handlers

    async def post_init(application: Application):
        await init_db()
        asyncio.create_task(_self_ping(application))
        me = await application.bot.get_me()
        LOGGER.info(f"Bot @{me.username} ready! OWNER_ID={Config.OWNER_ID}")
        if Config.LOG_CH:
            try:
                await application.bot.send_message(
                    int(Config.LOG_CH), "✅ <b>Bot Started!</b>", parse_mode="HTML"
                )
            except Exception as e:
                LOGGER.warning(f"LOG_CH send failed: {e}")

    app = (
        Application.builder()
        .token(Config.BOT_TOKEN)
        .post_init(post_init)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(300)
        .pool_timeout(30)
        .build()
    )

    setup_handlers(app)

    LOGGER.info("Starting polling...")
    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )
