#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import asyncio
import logging
import threading
from aiohttp import web
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
    BOT_TOKEN   = os.environ.get("BOT_TOKEN", "")
    API_ID      = int(os.environ.get("API_ID", "0"))
    API_HASH    = os.environ.get("API_HASH", "")

    # Support both OWNER_ID and ADMIN_IDS
    _owner_raw  = os.environ.get("OWNER_ID", "") or os.environ.get("ADMIN_IDS", "")
    OWNER_ID    = int(_owner_raw.split(",")[0].strip()) if _owner_raw.strip() else 0

    AUTH_USERS  = list(map(int, filter(None, os.environ.get("AUTH_USERS", "").split(","))))
    LOG_CH      = os.environ.get("LOG_CH", "")

    MONGO_URI   = os.environ.get("MONGO_URI", "")

    DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "./DOWNLOADS")
    SESSIONS_DIR = "./SESSIONS"

    PW_TOKEN    = os.environ.get("PW_TOKEN", "")
    PW_MOBILE   = os.environ.get("PW_MOBILE", "")
    THUMB_URL   = os.environ.get("THUMB_URL", "")

    MP4DECRYPT  = os.environ.get("MP4DECRYPT_PATH", "mp4decrypt")
    FFMPEG      = os.environ.get("FFMPEG_PATH", "ffmpeg")
    N_M3U8DL   = os.environ.get("N_M3U8DL_RE_PATH", "")

    MAX_PARALLEL_DL = int(os.environ.get("MAX_PARALLEL_DL", "2"))
    DEFAULT_QUALITY = os.environ.get("DEFAULT_QUALITY", "best")

    TG_MAX_SIZE = 2 * 1024 * 1024 * 1024

    # Web server port for Render Web Service health checks
    PORT        = int(os.environ.get("PORT", "8080"))

    # Your Render service URL for self-ping (set RENDER_URL env var)
    RENDER_URL  = os.environ.get("RENDER_URL", "")

    # Self-ping interval in seconds (default 10 minutes)
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
    CMD_UPLOAD  = "<b>📤 Uploading:</b> <code>{name}</code>"
    CMD_DL      = "<b>📥 Downloading:</b>\n<code>{name}</code>"
    CMD_KEY     = "<b>🔑 Fetching DRM key...</b>\n<code>{name}</code>"


prefixes = ["/", "!", ".", "~"]
plugins  = dict(root="plugins")

DB = None


async def init_db():
    global DB
    if not Config.MONGO_URI:
        LOGGER.warning("MONGO_URI not set — sessions won't persist across restarts")
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


# ── Web server for Render health check ───────────────────────────────────────

async def health_handler(request):
    return web.Response(text="✅ PW DRM Bot is alive!", status=200)


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_handler)
    app.router.add_get("/health", health_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", Config.PORT)
    await site.start()
    LOGGER.info(f"Web server started on port {Config.PORT}")


# ── Self-ping to prevent Render free-tier sleep ───────────────────────────────

async def self_ping():
    if not Config.RENDER_URL:
        LOGGER.info("RENDER_URL not set — self-ping disabled")
        return
    import aiohttp
    url = Config.RENDER_URL.rstrip("/") + "/health"
    LOGGER.info(f"Self-ping started → {url} every {Config.PING_INTERVAL}s")
    while True:
        await asyncio.sleep(Config.PING_INTERVAL)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
                    LOGGER.info(f"Self-ping OK ({r.status})")
        except Exception as e:
            LOGGER.warning(f"Self-ping failed: {e}")


# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    for folder in [Config.DOWNLOAD_DIR, Config.SESSIONS_DIR]:
        os.makedirs(folder, exist_ok=True)

    if not Config.BOT_TOKEN or not Config.API_ID or not Config.API_HASH:
        LOGGER.error("BOT_TOKEN / API_ID / API_HASH not set!")
        exit(1)

    if Config.OWNER_ID == 0:
        LOGGER.warning("OWNER_ID / ADMIN_IDS not set! Bot will reject all users.")

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
        await start_web_server()
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
