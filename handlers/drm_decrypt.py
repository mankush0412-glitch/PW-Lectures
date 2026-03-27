#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# DRM Decrypt Handler
# - Multi-threaded download via yt-dlp concurrent-fragments
# - Parallel video/audio track download via asyncio semaphore
# - mp4decrypt with full KID:KEY args
# - ffmpeg merge with proper video/audio detection
# - Every step has try-catch + LOGGER

import os
import re
import glob
import asyncio
import aiohttp
import aiofiles
from main import LOGGER, Config


# Limit concurrent downloads across all users
_DL_SEM = None

def _get_sem() -> asyncio.Semaphore:
    global _DL_SEM
    if _DL_SEM is None:
        _DL_SEM = asyncio.Semaphore(Config.MAX_PARALLEL_DL)
    return _DL_SEM


class DRMDecrypt:

    @staticmethod
    async def download_and_decrypt(
        name: str,
        mpd_url: str,
        key: str,
        output_dir: str,
        quality: str = "best",
        progress_cb=None,           # optional async callback(text)
    ) -> str:
        """
        Full pipeline:
          1. yt-dlp → encrypted streams
          2. mp4decrypt → decrypted tracks
          3. ffmpeg → merged .mp4
        Returns path to final .mp4 or '' on failure.
        """
        clean = _safe_name(name)
        base  = os.path.join(output_dir, clean)
        final = f"{base}.mp4"

        # Skip if already done
        if os.path.isfile(final) and os.path.getsize(final) > 102400:
            LOGGER.info(f"Already exists, skipping: {final}")
            return final

        async with _get_sem():
            try:
                if key:
                    ok = await DRMDecrypt._pipeline_drm(
                        mpd_url, key, base, final, quality, progress_cb
                    )
                else:
                    ok = await DRMDecrypt._pipeline_direct(
                        mpd_url, base, final, quality, progress_cb
                    )
            except Exception as e:
                LOGGER.error(f"Pipeline exception [{name}]: {e}")
                ok = False

        if ok and os.path.isfile(final):
            LOGGER.info(f"Done: {final} ({_human(os.path.getsize(final))})")
            return final

        LOGGER.error(f"Final file not found for: {name}")
        return ""

    @staticmethod
    async def download_pdf(url: str, name: str, output_dir: str) -> str:
        clean = _safe_name(name)
        out   = os.path.join(output_dir, f"{clean}.pdf")
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    url,
                    headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.pw.live/"},
                    timeout=aiohttp.ClientTimeout(total=120),
                ) as r:
                    if r.status == 200:
                        async with aiofiles.open(out, "wb") as f:
                            await f.write(await r.read())
                        LOGGER.info(f"PDF saved: {out}")
                        return out
                    LOGGER.error(f"PDF HTTP {r.status}: {url[:80]}")
        except Exception as e:
            LOGGER.error(f"PDF download error: {e}")
        return ""

    # ── DRM Pipeline ──────────────────────────────────────────────────────

    @staticmethod
    async def _pipeline_drm(
        mpd_url: str, key: str, base: str,
        final: str, quality: str, cb
    ) -> bool:
        out_dir = os.path.dirname(base)
        bname   = os.path.basename(base)

        # Try N_m3u8DL-RE first (faster)
        if Config.N_M3U8DL and os.path.isfile(Config.N_M3U8DL):
            if await DRMDecrypt._n_m3u8dl(mpd_url, key, out_dir, bname, final, quality, cb):
                return True

        await _cb(cb, f"⬇️ Downloading encrypted streams...")
        fmt = _quality_fmt(quality)

        cmd = [
            "yt-dlp",
            "--allow-unplayable-formats",
            "--no-check-certificate",
            "--no-warnings",
            "--concurrent-fragments", "4",
            "--retries", "5",
            "--fragment-retries", "10",
            "--extractor-retries", "3",
            "-f", fmt,
            "-o", os.path.join(out_dir, f"{bname}_enc.%(ext)s"),
            mpd_url,
        ]

        LOGGER.info(f"yt-dlp DRM: {mpd_url[:60]}...")
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                err = stderr.decode(errors="replace")
                LOGGER.error(f"yt-dlp error (rc={proc.returncode}): {err[:400]}")
                await _cb(cb, "⚠️ yt-dlp failed, trying ffmpeg fallback...")
                return await DRMDecrypt._ffmpeg_direct(mpd_url, final)
        except FileNotFoundError:
            LOGGER.error("yt-dlp not found in PATH!")
            return False
        except Exception as e:
            LOGGER.error(f"yt-dlp exception: {e}")
            return False

        # Find encrypted files
        enc_files = sorted(glob.glob(os.path.join(out_dir, f"{bname}_enc.*")))
        if not enc_files:
            LOGGER.error("No encrypted files found after yt-dlp")
            return await DRMDecrypt._ffmpeg_direct(mpd_url, final)

        LOGGER.info(f"Encrypted tracks: {[os.path.basename(f) for f in enc_files]}")

        # Build mp4decrypt --key args for all keys
        key_args = []
        for k in key.split(";"):
            k = k.strip()
            if not k:
                continue
            if ":" not in k:
                k = f"{'0'*32}:{k}"
            key_args += ["--key", k]

        if not key_args:
            LOGGER.error("No valid key args — stopping to avoid corrupted output")
            _cleanup(enc_files)
            return False

        # Decrypt each track
        await _cb(cb, "🔓 Decrypting tracks...")
        dec_files = []
        for enc in enc_files:
            dec = enc.replace("_enc.", "_dec.")
            cmd_d = ["mp4decrypt"] + key_args + [enc, dec]
            try:
                p = await asyncio.create_subprocess_exec(
                    *cmd_d,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, derr = await p.communicate()
                if os.path.isfile(dec) and os.path.getsize(dec) > 0:
                    dec_files.append(dec)
                    os.remove(enc)
                    LOGGER.info(f"Decrypted: {os.path.basename(dec)}")
                else:
                    LOGGER.error(f"mp4decrypt failed for {os.path.basename(enc)}: {derr.decode()[:200]}")
                    # Don't add corrupted file — stop this track
            except FileNotFoundError:
                LOGGER.error("mp4decrypt not found! Install Bento4.")
                _cleanup(enc_files)
                return False
            except Exception as e:
                LOGGER.error(f"mp4decrypt exception: {e}")

        if not dec_files:
            LOGGER.error("No decrypted files produced — aborting merge")
            return False

        await _cb(cb, "🔗 Merging audio + video...")
        return await DRMDecrypt._merge(dec_files, final)

    # ── Direct (non-DRM) Pipeline ─────────────────────────────────────────

    @staticmethod
    async def _pipeline_direct(
        mpd_url: str, base: str, final: str, quality: str, cb
    ) -> bool:
        out_dir = os.path.dirname(base)
        bname   = os.path.basename(base)
        fmt = _quality_fmt(quality).replace(
            "[ext=mp4]+bestaudio[ext=m4a]",
            "+bestaudio"
        )
        await _cb(cb, "⬇️ Downloading stream...")
        cmd = [
            "yt-dlp",
            "--no-check-certificate",
            "--no-warnings",
            "--concurrent-fragments", "4",
            "--retries", "5",
            "--fragment-retries", "10",
            "--remux-video", "mp4",
            "-f", fmt,
            "-o", os.path.join(out_dir, f"{bname}.%(ext)s"),
            mpd_url,
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                LOGGER.error(f"yt-dlp direct error: {stderr.decode()[:300]}")
        except Exception as e:
            LOGGER.error(f"yt-dlp direct exception: {e}")
            return False

        if os.path.isfile(final):
            return True
        for ext in [".mp4", ".mkv", ".webm", ".ts"]:
            candidate = os.path.join(out_dir, f"{bname}{ext}")
            if os.path.isfile(candidate):
                os.rename(candidate, final)
                return True

        await _cb(cb, "⚠️ yt-dlp failed, ffmpeg fallback...")
        return await DRMDecrypt._ffmpeg_direct(mpd_url, final)

    # ── N_m3u8DL-RE ───────────────────────────────────────────────────────

    @staticmethod
    async def _n_m3u8dl(
        mpd_url: str, key: str,
        out_dir: str, bname: str,
        final: str, quality: str, cb
    ) -> bool:
        await _cb(cb, "⚡ Downloading with N_m3u8DL-RE...")
        key_args = []
        for k in key.split(";"):
            k = k.strip()
            if k:
                if ":" not in k:
                    k = f"{'0'*32}:{k}"
                key_args += ["--key", k]

        cmd = [
            Config.N_M3U8DL,
            mpd_url,
            "--save-dir",  out_dir,
            "--save-name", bname,
            "--auto-select",
            "--binary-merge",
            "--del-after-done",
        ] + key_args

        if quality not in ("best", ""):
            if quality.isdigit():
                cmd += ["--video-filter", f"res={quality}"]
            elif quality == "worst":
                cmd += ["--video-max-rate", "500000"]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, err = await proc.communicate()
            if proc.returncode == 0:
                for ext in [".mp4", ".mkv", ".ts"]:
                    candidate = os.path.join(out_dir, bname + ext)
                    if os.path.isfile(candidate):
                        if candidate != final:
                            os.rename(candidate, final)
                        LOGGER.info(f"N_m3u8DL-RE success: {final}")
                        return True
            LOGGER.warning(f"N_m3u8DL-RE failed: {err.decode()[:200]}")
        except Exception as e:
            LOGGER.error(f"N_m3u8DL-RE exception: {e}")
        return False

    # ── ffmpeg direct ─────────────────────────────────────────────────────

    @staticmethod
    async def _ffmpeg_direct(mpd_url: str, final: str) -> bool:
        LOGGER.info(f"ffmpeg direct download: {mpd_url[:60]}")
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "warning",
            "-protocol_whitelist", "file,http,https,tcp,tls,crypto",
            "-i", mpd_url,
            "-c", "copy",
            "-bsf:a", "aac_adtstoasc",
            "-y", final,
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, err = await proc.communicate()
            if not os.path.isfile(final):
                LOGGER.error(f"ffmpeg direct failed: {err.decode()[:200]}")
                return False
            return True
        except Exception as e:
            LOGGER.error(f"ffmpeg direct exception: {e}")
            return False

    # ── Merge ─────────────────────────────────────────────────────────────

    @staticmethod
    async def _merge(files: list, final: str) -> bool:
        if not files:
            return False

        if len(files) == 1:
            cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-i", files[0],
                "-c", "copy", "-y", final,
            ]
        else:
            # Detect video vs audio by probing streams
            video_file = None
            audio_file = None
            for f in files:
                kind = await _probe_kind(f)
                if kind == "video" and not video_file:
                    video_file = f
                elif kind == "audio" and not audio_file:
                    audio_file = f

            # Fallback: first = video, last = audio
            if not video_file:
                video_file = files[0]
            if not audio_file:
                audio_file = files[-1] if len(files) > 1 else files[0]

            if video_file == audio_file:
                cmd = [
                    "ffmpeg", "-hide_banner", "-loglevel", "error",
                    "-i", video_file,
                    "-c", "copy", "-y", final,
                ]
            else:
                cmd = [
                    "ffmpeg", "-hide_banner", "-loglevel", "error",
                    "-i", video_file, "-i", audio_file,
                    "-c:v", "copy", "-c:a", "copy",
                    "-y", final,
                ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, err = await proc.communicate()
        except Exception as e:
            LOGGER.error(f"ffmpeg merge exception: {e}")
            _cleanup(files)
            return False

        _cleanup(files, exclude=final)

        if not os.path.isfile(final):
            LOGGER.error(f"Merge failed — output missing. ffmpeg: {err.decode()[:300]}")
            return False

        LOGGER.info(f"Merged: {os.path.basename(final)}")
        return True


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_name(name: str) -> str:
    s = re.sub(r'[\\/*?:"<>|]', "", name).strip()
    s = re.sub(r"\s+", " ", s)
    return s[:100] or "video"


def _quality_fmt(q: str) -> str:
    qmap = {
        "best":  "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best",
        "1080":  "bestvideo[height<=1080][ext=mp4]+bestaudio/best[height<=1080]",
        "720":   "bestvideo[height<=720][ext=mp4]+bestaudio/best[height<=720]",
        "480":   "bestvideo[height<=480][ext=mp4]+bestaudio/best[height<=480]",
        "360":   "bestvideo[height<=360][ext=mp4]+bestaudio/best[height<=360]",
        "worst": "worstvideo+worstaudio/worst",
    }
    return qmap.get(q.lower(), qmap["best"])


def _human(size: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


def _cleanup(files: list, exclude: str = ""):
    for f in files:
        try:
            if f != exclude and os.path.isfile(f):
                os.remove(f)
        except Exception:
            pass


async def _probe_kind(filepath: str) -> str:
    """Return 'video', 'audio', or 'unknown' by probing with ffprobe."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=codec_type",
            "-of", "default=noprint_wrappers=1:nokey=1",
            filepath,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        text = out.decode().strip().lower()
        if "video" in text:
            return "video"
        # Check for audio
        proc2 = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=codec_type",
            "-of", "default=noprint_wrappers=1:nokey=1",
            filepath,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out2, _ = await proc2.communicate()
        if "audio" in out2.decode().strip().lower():
            return "audio"
    except Exception:
        pass
    # Guess from filename
    name = filepath.lower()
    if "video" in name:
        return "video"
    if "audio" in name:
        return "audio"
    return "unknown"


async def _cb(cb, text: str):
    if cb:
        try:
            await cb(text)
        except Exception:
            pass
