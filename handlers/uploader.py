#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Upload Handler — python-telegram-bot 20.x version

import os
import time
import asyncio
import subprocess
import aiohttp
import aiofiles
from main import Msg, LOGGER, Config

BOT_API_LIMIT = 50 * 1024 * 1024   # 50 MB


class Upload_to_Tg:
    def __init__(
        self,
        bot,
        chat_id: int,
        reply_to_id: int,
        file_path: str,
        name: str,
        thumb_url: str,
        path: str,
        show_msg,
        caption: str = "",
    ):
        self.bot          = bot
        self.chat_id      = chat_id
        self.reply_to_id  = reply_to_id
        self.file_path    = file_path
        self.name         = name
        self.thumb_url    = thumb_url
        self.path         = path
        self.show_msg     = show_msg
        self.caption      = caption or name
        self.start_time   = time.time()

    async def upload_video(self):
        size = os.path.getsize(self.file_path) if os.path.isfile(self.file_path) else 0

        if size > BOT_API_LIMIT:
            await self._edit(
                f"⚠️ <b>File too large:</b> {_human(size)}\n"
                f"Telegram Bot API limit is 50 MB.\n"
                f"File: <code>{self.name}</code>"
            )
            return

        await self._edit(Msg.CMD_UPLOAD.format(name=self.name))
        thumb    = await self._get_thumb()
        duration = self._get_duration()
        w, h     = self._get_wh()

        try:
            with open(self.file_path, "rb") as vf:
                thumb_fh = open(thumb, "rb") if thumb else None
                try:
                    await self.bot.send_video(
                        chat_id=self.chat_id,
                        video=vf,
                        caption=f"<b>{self.caption}</b>",
                        duration=duration,
                        width=w,
                        height=h,
                        supports_streaming=True,
                        reply_to_message_id=self.reply_to_id,
                        parse_mode="HTML",
                        thumbnail=thumb_fh,
                        write_timeout=600,
                        read_timeout=60,
                    )
                    LOGGER.info(f"Uploaded video: {self.name}")
                finally:
                    if thumb_fh:
                        thumb_fh.close()
        except Exception as e:
            LOGGER.warning(f"Video upload failed ({e}), trying document...")
            await self.upload_doc()
            return

        self._cleanup(thumb)

    async def upload_doc(self):
        size = os.path.getsize(self.file_path) if os.path.isfile(self.file_path) else 0

        if size > BOT_API_LIMIT:
            await self._edit(
                f"⚠️ <b>File too large:</b> {_human(size)}\n"
                f"Telegram Bot API limit is 50 MB.\n"
                f"File: <code>{self.name}</code>"
            )
            return

        await self._edit(Msg.CMD_UPLOAD.format(name=self.name))
        thumb = await self._get_thumb()

        try:
            with open(self.file_path, "rb") as df:
                thumb_fh = open(thumb, "rb") if thumb else None
                try:
                    await self.bot.send_document(
                        chat_id=self.chat_id,
                        document=df,
                        caption=f"<b>{self.caption}</b>",
                        reply_to_message_id=self.reply_to_id,
                        parse_mode="HTML",
                        thumbnail=thumb_fh,
                        write_timeout=600,
                        read_timeout=60,
                    )
                    LOGGER.info(f"Uploaded document: {self.name}")
                finally:
                    if thumb_fh:
                        thumb_fh.close()
        except Exception as e:
            LOGGER.error(f"Document upload error: {e}")

        self._cleanup(thumb)

    async def _edit(self, text: str):
        try:
            await self.show_msg.edit_text(text, parse_mode="HTML")
        except Exception:
            pass

    async def _get_thumb(self) -> str | None:
        if not self.thumb_url:
            return None
        try:
            thumb_path = os.path.join(self.path, ".thumb_tmp.jpg")
            async with aiohttp.ClientSession() as s:
                async with s.get(self.thumb_url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    if r.status == 200:
                        async with aiofiles.open(thumb_path, "wb") as f:
                            await f.write(await r.read())
                        return thumb_path
        except Exception:
            pass
        return None

    def _get_duration(self) -> int:
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", self.file_path],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10,
            )
            return int(float(r.stdout.decode().strip()))
        except Exception:
            return 0

    def _get_wh(self) -> tuple[int, int]:
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height", "-of", "csv=p=0",
                 self.file_path],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10,
            )
            parts = r.stdout.decode().strip().split(",")
            if len(parts) >= 2:
                return int(parts[0]), int(parts[1])
        except Exception:
            pass
        return 1280, 720

    def _cleanup(self, thumb=None):
        for f in [self.file_path, thumb]:
            try:
                if f and os.path.isfile(f):
                    os.remove(f)
            except Exception:
                pass
        try:
            asyncio.create_task(self.show_msg.delete())
        except Exception:
            pass


def _human(size: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"
