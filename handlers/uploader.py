#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Upload Handler — sends files to Telegram with progress bar
# Large file (>2GB) detection + document fallback

import os
import time
import asyncio
import subprocess
import aiohttp
import aiofiles
from pyrogram import Client as AFK
from pyrogram.types import Message
from main import Msg, LOGGER, Config
from helpers.prog_bar import progress_for_pyrogram


class Upload_to_Tg:
    def __init__(
        self,
        bot: AFK,
        m: Message,
        file_path: str,
        name: str,
        thumb_url: str,
        path: str,
        show_msg: Message,
        caption: str = "",
    ):
        self.bot        = bot
        self.m          = m
        self.file_path  = file_path
        self.name       = name
        self.thumb_url  = thumb_url
        self.path       = path
        self.show_msg   = show_msg
        self.caption    = caption or name
        self.start_time = time.time()

    async def upload_video(self):
        file_size = os.path.getsize(self.file_path) if os.path.isfile(self.file_path) else 0

        if file_size > Config.TG_MAX_SIZE:
            LOGGER.warning(f"File too large ({_human(file_size)}) — cannot upload to Telegram")
            try:
                await self.show_msg.edit_text(
                    f"⚠️ <b>File too large:</b> {_human(file_size)}\n"
                    f"Telegram limit is 2GB. File saved at server."
                )
            except Exception:
                pass
            return

        try:
            await self.show_msg.edit_text(
                Msg.CMD_UPLOAD.format(name=self.name)
            )
        except Exception:
            pass

        thumb    = await self._get_thumb()
        duration = self._get_duration()
        w, h     = self._get_wh()

        try:
            await self.bot.send_video(
                chat_id=self.m.chat.id,
                video=self.file_path,
                caption=f"<b>{self.caption}</b>",
                thumb=thumb,
                duration=duration,
                width=w,
                height=h,
                supports_streaming=True,
                progress=progress_for_pyrogram,
                progress_args=(
                    Msg.CMD_UPLOAD.format(name=self.name),
                    self.show_msg,
                    self.start_time,
                ),
            )
            LOGGER.info(f"Uploaded video: {self.name}")
        except Exception as e:
            LOGGER.warning(f"Video upload failed → document fallback: {e}")
            await self.upload_doc()
            return

        self._cleanup(thumb)

    async def upload_doc(self):
        try:
            await self.show_msg.edit_text(
                Msg.CMD_UPLOAD.format(name=self.name)
            )
        except Exception:
            pass

        thumb = await self._get_thumb()

        try:
            await self.bot.send_document(
                chat_id=self.m.chat.id,
                document=self.file_path,
                caption=f"<b>{self.caption}</b>",
                thumb=thumb,
                progress=progress_for_pyrogram,
                progress_args=(
                    Msg.CMD_UPLOAD.format(name=self.name),
                    self.show_msg,
                    self.start_time,
                ),
            )
            LOGGER.info(f"Uploaded document: {self.name}")
        except Exception as e:
            LOGGER.error(f"Document upload error: {e}")

        self._cleanup(thumb)

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
                [
                    "ffprobe", "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    self.file_path,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=10,
            )
            return int(float(r.stdout.decode().strip()))
        except Exception:
            return 0

    def _get_wh(self) -> tuple[int, int]:
        try:
            r = subprocess.run(
                [
                    "ffprobe", "-v", "error",
                    "-select_streams", "v:0",
                    "-show_entries", "stream=width,height",
                    "-of", "csv=p=0",
                    self.file_path,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=10,
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
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"
