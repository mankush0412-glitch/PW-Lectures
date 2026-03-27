#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import asyncio
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pyrogram import Client as AFK, idle
import tgcrypto
from pyromod import listen

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s - %(levelname)s] - %(name)s - %(message)s",
    datefmt="%d-%b-%y %H:%M:%S",
    handlers=[logging.StreamHandler()]
)
LOGGER = logging.getLogger("PW-DRM")


class Config:
    BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
    API_ID    = int(os.environ.get("API_ID", "0"))
    API_HASH  = os.environ.get("API_HASH", "").strip()

    # Support both OWNER_ID and ADMIN_IDS
    _owner_raw = os.environ.get("OWNER_ID", "").strip() or os.environ.get("ADMIN_IDS", "").strip()
    OWNER_ID   = int(_owner_raw.split(",")[0].strip()) if _owner_raw else 0

    AUTH_USERS = list(map(int, filter(None, os.environ.get("AUTH_USERS", "").split(","))))
    LOG_CH     = os.environ.get("LOG_CH", "").strip()

    # Strip quotes from MONGO_URI in case Render passes them
    MONGO_URI  = os.environ.get("MONGO_URI", "").strip().strip('"').strip("'")

    DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "./DOWNLOADS")
    SESSIONS_DIR = "./SESSIONS"

    PW_TOKEN  = os.environ.get("PW_TOKEN", "").strip()
    PW_MOBILE = os.environ.get("PW_MOBILE", "").strip()
    THUMB_URL = os.environ.get("THUMB_URL", "").strip()

    MP4DECRYPT = os.environ.get("MP4DECRYPT_PATH", "mp4decrypt")
    FFMPEG     = os.environ.get("FFMPEG_PATH", "ffmpeg")
    N_M3U8DL  = os.environ.get("N_M3U8DL_RE_PATH", "")

    MAX_PARALLEL_DL = int(os.environ.get("MAX_PARALLEL_DL", "2"))
    DEFAULT_QUALITY = os.environ.get("DEFAULT_QUALITY", "best")

    TG_MAX_SIZE = 2 * 1024 * 1024 * 1024

    PORT       = int(os.environ.get("PORT", "8080"))
    RENDER_URL = os.environ.get("RENDER_URL", "").strip()
    PING_INTERVAL = int(os.environ.get("PING_INTERVAL", "600"))


class Msg:
    START_MSG = (
        "<b>🤖 PW DRM Bot</b>\n\n"
        "<b>📥 Usage:</b>\n"
        "1. /pwlogin → phone + OTP\n"
        "2. Send your <code>.txt</code> file\n"
        "3. Bot downloads, decrypts, uploads!\n\n"
        "<b>Commands:</b>\n"
        "/pwlogin /pwstatus /dl /batch /help"
    )
    CMD_UPLOAD = "<b>📤 Uploading:</b> <code>{name}</code>"
    CMD_DL     = "<b>📥 Downloading:</b>\n<code>{name}</code>"
    CMD_KEY    = "<b>🔑 Fetching DRM key...</b>\n<code>{name}</code>"


prefixes = ["/", "!", ".", "~"]
plugins  = dict(root="plugins")

DB = None


async def init_db():
    global DB
    if not Config.MONGO_URI:
        LOGGER.warning("MONGO_URI not set — sessions won't persist")
        return
    try:
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient(Config.MONGO_URI, serverSelectionTimeoutMS=5000)
        await client.admin.command("ping")
        DB = client["pw_drm_bot"]
        LOGGER.info("MongoDB connected!")
    except Exception as e:
        LOGGER.error(f"MongoDB connection failed: {e}")
        DB = None


# ── Simple HTTP server in daemon thread (Render Web Service health check) ──────

class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"PW DRM Bot is alive!"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # Suppress HTTP access logs


def _start_health_server():
    try:
        server = HTTPServer(("0.0.0.0", Config.PORT), _HealthHandler)
        LOGGER.info(f"Health server on port {Config.PORT}")
        server.serve_forever()
    except Exception as e:
        LOGGER.error(f"Health server error: {e}")


# ── Self-ping to keep Render free tier alive ──────────────────────────────────

async def self_ping():
    if not Config.RENDER_URL:
        return
    import aiohttp
    url = Config.RENDER_URL.rstrip("/") + "/"
    LOGGER.info(f"Self-ping → {url} every {Config.PING_INTERVAL}s")
    while True:
        await asyncio.sleep(Config.PING_INTERVAL)
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    LOGGER.info(f"Self-ping OK ({r.status})")
        except Exception as e:
            LOGGER.warning(f"Self-ping failed: {e}")


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    for folder in [Config.DOWNLOAD_DIR, Config.SESSIONS_DIR]:
        os.makedirs(folder, exist_ok=True)

    if not Config.BOT_TOKEN or not Config.API_ID or not Config.API_HASH:
        LOGGER.error("BOT_TOKEN / API_ID / API_HASH not set!")
        exit(1)

    LOGGER.info(f"Starting with OWNER_ID={Config.OWNER_ID}")

    # Start health server in background thread (doesn't touch asyncio)
    t = threading.Thread(target=_start_health_server, daemon=True)
    t.start()

    PRO = AFK(
        "PW-DRM-Bot",
        bot_token=Config.BOT_TOKEN,
        api_id=Config.API_ID,
        api_hash=Config.API_HASH,
        sleep_threshold=120,
        plugins=plugins,
        workdir=Config.SESSIONS_DIR,
        workers=8,
    )

    async def main():
        await init_db()
        asyncio.create_task(self_ping())
        await PRO.start()
        me = await PRO.get_me()
        LOGGER.info(f"Bot @{me.username} started! OWNER_ID={Config.OWNER_ID}")
        if Config.LOG_CH:
            try:
                await PRO.send_message(int(Config.LOG_CH), "✅ <b>PW DRM Bot Started!</b>")
            except Exception as e:
                LOGGER.warning(f"Log channel error: {e}")
        await idle()
        await PRO.stop()

    asyncio.run(main())
