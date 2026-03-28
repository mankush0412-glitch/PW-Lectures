#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import asyncio
import logging
from pyrogram import Client as AFK, idle

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s - %(levelname)s] - %(name)s - %(message)s",
    datefmt="%d-%b-%y %H:%M:%S",
    handlers=[logging.StreamHandler()]
)
LOGGER = logging.getLogger("PW-DRM")


class Config:
    BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
    API_ID    = int(os.environ.get("API_ID", "0") or "0")
    API_HASH  = os.environ.get("API_HASH", "").strip()

    # Accept OWNER_ID or ADMIN_IDS (user's env uses ADMIN_IDS)
    _own = (os.environ.get("OWNER_ID") or os.environ.get("ADMIN_IDS") or "").strip()
    OWNER_ID = int(_own.split(",")[0].strip()) if _own else 0

    AUTH_USERS = list(map(int, filter(None,
        os.environ.get("AUTH_USERS", "").split(","))))

    # Accept MONGO_URI or MONGODB_URL
    MONGO_URI = (
        os.environ.get("MONGO_URI") or
        os.environ.get("MONGODB_URL") or ""
    ).strip().strip('"').strip("'")

    LOG_CH    = os.environ.get("LOG_CH", "").strip()
    THUMB_URL = os.environ.get("THUMB_URL", "").strip()

    DOWNLOAD_DIR    = os.environ.get("DOWNLOAD_DIR", "./DOWNLOADS")
    SESSIONS_DIR    = "./SESSIONS"
    MP4DECRYPT      = os.environ.get("MP4DECRYPT_PATH", "mp4decrypt")
    FFMPEG          = os.environ.get("FFMPEG_PATH", "ffmpeg")
    N_M3U8DL        = os.environ.get("N_M3U8DL_RE_PATH", "")
    MAX_PARALLEL_DL = int(os.environ.get("MAX_PARALLEL_DL", "2"))
    DEFAULT_QUALITY = os.environ.get("DEFAULT_QUALITY", "best")

    TG_MAX_SIZE = 2 * 1024 * 1024 * 1024   # 2 GB (Pyrogram MTProto limit)


class Msg:
    START_MSG = (
        "<b>🤖 PW DRM Bot</b>\n\n"
        "<b>📥 Steps:</b>\n"
        "1️⃣ /pwlogin → phone + OTP\n"
        "2️⃣ .txt file bhejo\n"
        "3️⃣ Confirm → Select → Quality\n"
        "4️⃣ Bot downloads, decrypts, uploads! 🎬\n\n"
        "<b>Commands:</b> /pwlogin /pwstatus /dl /batch /help"
    )
    CMD_UPLOAD = "<b>📤 Uploading:</b> <code>{name}</code>"
    CMD_DL     = "<b>📥 Downloading:</b>\n<code>{name}</code>"
    CMD_KEY    = "<b>🔑 DRM key fetch ho rahi hai...</b>\n<code>{name}</code>"


prefixes = ["/", "!", ".", "~"]
plugins  = dict(root="plugins")

DB = None


async def init_db():
    global DB
    if not Config.MONGO_URI:
        LOGGER.warning("MONGO_URI/MONGODB_URL not set — sessions only in memory")
        return
    try:
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient(Config.MONGO_URI, serverSelectionTimeoutMS=8000)
        await client.admin.command("ping")
        DB = client["pw_drm_bot"]
        LOGGER.info("MongoDB connected!")
    except Exception as e:
        LOGGER.error(f"MongoDB failed: {e}")
        DB = None


if __name__ == "__main__":
    for folder in [Config.DOWNLOAD_DIR, Config.SESSIONS_DIR]:
        os.makedirs(folder, exist_ok=True)

    if not Config.BOT_TOKEN:
        LOGGER.error("BOT_TOKEN not set!")
        exit(1)
    if not Config.API_ID or not Config.API_HASH:
        LOGGER.error("API_ID / API_HASH not set!")
        exit(1)

    LOGGER.info(f"Starting PW-DRM-Bot | OWNER_ID={Config.OWNER_ID}")

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
        await PRO.start()
        me = await PRO.get_me()
        LOGGER.info(f"Bot @{me.username} ready! OWNER_ID={Config.OWNER_ID}")
        if Config.LOG_CH:
            try:
                await PRO.send_message(int(Config.LOG_CH), "✅ <b>PW DRM Bot Started!</b>")
            except Exception as e:
                LOGGER.warning(f"Log channel error: {e}")
        await idle()
        await PRO.stop()

    asyncio.run(main())
