#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time


async def progress_for_pyrogram(current, total, text, message, start):
    now  = time.time()
    diff = max(now - start, 0.1)

    if round(diff % 8) == 0 or current == total:
        pct    = current * 100 / total if total else 0
        speed  = current / diff
        eta    = (total - current) / speed if speed else 0
        filled = int(15 * current / total) if total else 0
        bar    = "█" * filled + "░" * (15 - filled)

        try:
            await message.edit_text(
                f"{text}\n\n"
                f"[{bar}] {pct:.1f}%\n"
                f"{_fmt(current)} / {_fmt(total)}\n"
                f"Speed: {_fmt(speed)}/s  ETA: {_eta(eta)}"
            )
        except Exception:
            pass


def _fmt(size: float) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.2f}TB"


def _eta(sec: float) -> str:
    sec = int(sec)
    if sec < 60:
        return f"{sec}s"
    m, s = divmod(sec, 60)
    return f"{m}m {s}s"
