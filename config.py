#!/usr/bin/env python3
import os

class Config:
    BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

    API_ID   = int(os.environ.get("API_ID", "0") or "0")
    API_HASH = os.environ.get("API_HASH", "").strip()

    # Accept both OWNER_ID and ADMIN_IDS (user's env uses ADMIN_IDS)
    _owner_raw = (
        os.environ.get("OWNER_ID") or
        os.environ.get("ADMIN_IDS") or ""
    ).strip()
    OWNER_ID = int(_owner_raw.split(",")[0].strip()) if _owner_raw else 0

    AUTH_USERS = list(map(int, filter(None,
        os.environ.get("AUTH_USERS", "").split(","))))

    # Accept both MONGO_URI and MONGODB_URL (user's env uses MONGODB_URL)
    MONGO_URI = (
        os.environ.get("MONGO_URI") or
        os.environ.get("MONGODB_URL") or ""
    ).strip().strip('"').strip("'")

    LOG_CH     = os.environ.get("LOG_CH", "").strip()
    THUMB_URL  = os.environ.get("THUMB_URL", "").strip()

    DOWNLOAD_DIR    = os.environ.get("DOWNLOAD_DIR", "./DOWNLOADS")
    MP4DECRYPT      = os.environ.get("MP4DECRYPT_PATH", "mp4decrypt")
    FFMPEG          = os.environ.get("FFMPEG_PATH", "ffmpeg")
    N_M3U8DL        = os.environ.get("N_M3U8DL_RE_PATH", "")
    MAX_PARALLEL_DL = int(os.environ.get("MAX_PARALLEL_DL", "2"))
    DEFAULT_QUALITY = os.environ.get("DEFAULT_QUALITY", "best")

    TG_MAX_SIZE   = 50 * 1024 * 1024
    PORT          = int(os.environ.get("PORT", "8080"))
    RENDER_URL    = os.environ.get("RENDER_URL", "").strip()
    PING_INTERVAL = int(os.environ.get("PING_INTERVAL", "300"))

    @classmethod
    def is_auth(cls, uid: int) -> bool:
        return uid == cls.OWNER_ID or uid in cls.AUTH_USERS
