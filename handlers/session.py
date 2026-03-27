#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# MongoDB Session Manager — per-user PW token storage

import time
from main import LOGGER, Config


class SessionManager:
    """
    Stores PW session per Telegram user ID.
    Falls back to in-memory if MongoDB not available.
    """

    _memory: dict = {}   # uid → {token, refresh_token, mobile, expires_at}

    # ── Public API ────────────────────────────────────────────────────────

    @classmethod
    async def save(cls, uid: int, token: str, refresh_token: str, mobile: str):
        data = {
            "uid":           uid,
            "token":         token,
            "refresh_token": refresh_token,
            "mobile":        mobile,
            "updated_at":    int(time.time()),
        }
        cls._memory[uid] = data
        await cls._db_save(uid, data)
        LOGGER.info(f"Session saved for uid={uid}")

    @classmethod
    async def get(cls, uid: int) -> dict | None:
        # Check memory first
        if uid in cls._memory:
            return cls._memory[uid]
        # Try MongoDB
        doc = await cls._db_get(uid)
        if doc:
            cls._memory[uid] = doc
            return doc
        return None

    @classmethod
    async def delete(cls, uid: int):
        cls._memory.pop(uid, None)
        await cls._db_delete(uid)
        LOGGER.info(f"Session deleted for uid={uid}")

    @classmethod
    async def exists(cls, uid: int) -> bool:
        return (await cls.get(uid)) is not None

    # ── MongoDB helpers ───────────────────────────────────────────────────

    @classmethod
    async def _db_save(cls, uid: int, data: dict):
        try:
            from main import DB
            if DB is None:
                return
            await DB["sessions"].update_one(
                {"uid": uid}, {"$set": data}, upsert=True
            )
        except Exception as e:
            LOGGER.debug(f"DB save error: {e}")

    @classmethod
    async def _db_get(cls, uid: int) -> dict | None:
        try:
            from main import DB
            if DB is None:
                return None
            doc = await DB["sessions"].find_one({"uid": uid})
            if doc:
                doc.pop("_id", None)
                return doc
        except Exception as e:
            LOGGER.debug(f"DB get error: {e}")
        return None

    @classmethod
    async def _db_delete(cls, uid: int):
        try:
            from main import DB
            if DB is None:
                return
            await DB["sessions"].delete_one({"uid": uid})
        except Exception as e:
            LOGGER.debug(f"DB delete error: {e}")
