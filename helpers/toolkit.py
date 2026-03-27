#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import asyncio
import subprocess
import aiohttp
import aiofiles


class Tools:

    @staticmethod
    def duration(filename: str) -> float:
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "error",
                 "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", filename],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10,
            )
            return float(r.stdout.decode().strip())
        except Exception:
            return 0.0

    @staticmethod
    async def aio_download(url: str, out_path: str, headers: dict = None) -> str:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    url, headers=headers or {},
                    timeout=aiohttp.ClientTimeout(total=120),
                ) as r:
                    if r.status == 200:
                        async with aiofiles.open(out_path, "wb") as f:
                            await f.write(await r.read())
                        return out_path
        except Exception as e:
            print(f"aio_download error: {e}")
        return ""

    @staticmethod
    async def run_cmd(cmd: str) -> tuple:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return stdout.decode(), stderr.decode(), proc.returncode

    @staticmethod
    def clean_dir(path: str):
        import shutil
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
