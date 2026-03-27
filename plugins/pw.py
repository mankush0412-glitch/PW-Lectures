#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# PW Plugin — complete Telegram bot interface
# Features:
#   - Per-user MongoDB sessions (no repeated login)
#   - Bulk .txt processing with auto-loop
#   - Parallel downloads (asyncio semaphore)
#   - DRM key retry + expired MPD re-fetch
#   - Progress status on every step
#   - Continue on error (don't stop batch)
#   - Duplicate skip (file already exists)

import os
import re
import asyncio
from pyrogram import Client as AFK, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from main import Config, Msg, LOGGER, prefixes
from handlers.session import SessionManager
from handlers.pw_api import PWAPI
from handlers.drm_decrypt import DRMDecrypt
from handlers.uploader import Upload_to_Tg

# Per-user thumb (in-memory)
_user_thumb: dict = {}

# Per-user pending links
_user_links: dict = {}


def is_auth(m: Message) -> bool:
    uid = m.from_user.id
    return uid == Config.OWNER_ID or uid in Config.AUTH_USERS


def is_auth_uid(uid: int) -> bool:
    return uid == Config.OWNER_ID or uid in Config.AUTH_USERS


def get_thumb(uid: int) -> str:
    return _user_thumb.get(uid, Config.THUMB_URL or "")


# ── Start / Help ──────────────────────────────────────────────────────────────

@AFK.on_message(filters.command("start", prefixes))
async def start(bot: AFK, m: Message):
    if not is_auth(m):
        await m.reply_text("⛔ <b>Not authorized!</b>", quote=True)
        return
    btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔑 PW Login",    callback_data="pw_login_help"),
         InlineKeyboardButton("📖 Help",        callback_data="help")],
        [InlineKeyboardButton("✅ My Status",   callback_data="status")],
    ])
    await m.reply_text(Msg.START_MSG, reply_markup=btn, quote=True)


@AFK.on_callback_query()
async def cb_handler(bot, cq):
    uid = cq.from_user.id
    if not is_auth_uid(uid):
        return
    d   = cq.data

    if d == "help":
        await cq.message.edit_text(
            "<b>📖 Help</b>\n\n"
            "<b>Step 1:</b> /pwlogin → phone + OTP\n"
            "<b>Step 2:</b> Send .txt file\n"
            "<b>Step 3:</b> Select videos → choose quality\n"
            "<b>Bot does:</b> DRM key → Download → Decrypt → Upload\n\n"
            "<b>Txt line format:</b>\n"
            "<code>Name:https://...master.mpd&parentId=X&childId=Y</code>\n"
            "<code>Name:https://static.pw.live/.../file.pdf</code>"
        )
    elif d == "pw_login_help":
        await cq.message.edit_text(
            "<b>🔑 PW Login</b>\n\n"
            "/pwlogin → phone number → OTP\n"
            "Session is saved — no need to login again!"
        )
    elif d == "status":
        sess = await SessionManager.get(uid)
        if sess and sess.get("token"):
            p = await PWAPI.get_profile(uid)
            await cq.message.edit_text(
                f"✅ <b>Logged in</b>\n"
                f"Name: {p.get('name','?')}\n"
                f"Mobile: +91{sess.get('mobile','?')}"
            )
        else:
            await cq.message.edit_text("❌ <b>Not logged in</b>\nUse /pwlogin")
    elif d == "back":
        btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔑 PW Login",  callback_data="pw_login_help"),
             InlineKeyboardButton("📖 Help",      callback_data="help")],
            [InlineKeyboardButton("✅ My Status", callback_data="status")],
        ])
        await cq.message.edit_text(Msg.START_MSG, reply_markup=btn)


# ── PW Login ──────────────────────────────────────────────────────────────────

@AFK.on_message(filters.command("pwlogin", prefixes))
async def pw_login(bot: AFK, m: Message):
    if not is_auth(m):
        return
    uid = m.from_user.id

    # Check if already logged in
    sess = await SessionManager.get(uid)
    if sess and sess.get("token"):
        p = await PWAPI.get_profile(uid)
        if p:
            await m.reply_text(
                f"✅ <b>Already logged in!</b>\n"
                f"Name: {p.get('name','?')}\nMobile: +91{sess.get('mobile','?')}\n\n"
                f"Use /pwlogout to switch account.",
                quote=True
            )
            return

    await m.reply_text(
        "<b>📱 PW Login</b>\n\n"
        "Enter your PW phone number:\n<code>+91XXXXXXXXXX</code>\n\n"
        "/cancel to stop.",
        quote=True,
    )
    try:
        phone_msg = await bot.ask(m.chat.id, "📞 Phone:", timeout=120)
    except Exception:
        await m.reply_text("⏰ Timeout!")
        return

    phone = phone_msg.text.strip()
    if "/cancel" in phone:
        await m.reply_text("❌ Cancelled!")
        return

    mobile = re.sub(r"\D", "", phone)
    if mobile.startswith("91") and len(mobile) == 12:
        mobile = mobile[2:]
    if not mobile.isdigit() or len(mobile) != 10:
        await m.reply_text("❌ Invalid number! Send 10-digit mobile.")
        return

    wait = await m.reply_text("⏳ Sending OTP...")
    result = await PWAPI.send_otp(mobile)

    if result.get("error") or result.get("status") == "failure":
        await wait.edit_text(f"❌ OTP send failed!\n<code>{result}</code>")
        return

    await wait.edit_text(
        "✅ <b>OTP sent!</b>\nCheck PW app or SMS.\n\nEnter OTP:"
    )
    try:
        otp_msg = await bot.ask(m.chat.id, "OTP:", timeout=120)
    except Exception:
        await m.reply_text("⏰ OTP timeout!")
        return

    otp = otp_msg.text.strip()
    if "/cancel" in otp:
        await m.reply_text("❌ Cancelled!")
        return

    ver = await m.reply_text("⏳ Verifying OTP...")
    result2 = await PWAPI.verify_otp(uid, mobile, otp)

    if result2.get("error") or not (await SessionManager.get(uid)):
        msg = result2.get("message", str(result2))
        await ver.edit_text(f"❌ <b>Login failed!</b>\n<code>{msg}</code>")
        return

    p = await PWAPI.get_profile(uid)
    await ver.edit_text(
        f"✅ <b>PW Login Successful!</b>\n\n"
        f"Name: {p.get('name','?')}\n"
        f"Mobile: +91{mobile}\n\n"
        f"Session saved! Send your .txt file now."
    )
    LOGGER.info(f"Login OK uid={uid} mobile={mobile}")


@AFK.on_message(filters.command("pwstatus", prefixes))
async def pw_status(bot: AFK, m: Message):
    if not is_auth(m):
        return
    uid  = m.from_user.id
    sess = await SessionManager.get(uid)
    if not sess or not sess.get("token"):
        await m.reply_text("❌ Not logged in. Use /pwlogin", quote=True)
        return
    p = await PWAPI.get_profile(uid)
    if p:
        await m.reply_text(
            f"✅ <b>Logged in</b>\nName: {p.get('name','?')}\nMobile: +91{sess.get('mobile','?')}",
            quote=True,
        )
    else:
        await m.reply_text("⚠️ Token may be expired. Use /pwlogin.", quote=True)


@AFK.on_message(filters.command("pwlogout", prefixes))
async def pw_logout(bot: AFK, m: Message):
    if not is_auth(m):
        return
    await SessionManager.delete(m.from_user.id)
    await m.reply_text("✅ Logged out!", quote=True)


@AFK.on_message(filters.command("setpwtoken", prefixes))
async def set_token(bot: AFK, m: Message):
    if not is_auth(m):
        return
    args = m.text.split(None, 1)
    if len(args) < 2:
        await m.reply_text("Usage: <code>/setpwtoken YOUR_TOKEN</code>", quote=True)
        return
    uid = m.from_user.id
    await SessionManager.save(uid, args[1].strip(), "", "manual")
    await m.reply_text("✅ PW Token saved!", quote=True)
    try:
        await m.delete()
    except Exception:
        pass


# ── TXT File Handler ──────────────────────────────────────────────────────────

@AFK.on_message(filters.document & filters.private)
async def handle_txt(bot: AFK, m: Message):
    if not is_auth(m):
        return
    fname = m.document.file_name or ""
    if not fname.endswith(".txt"):
        return

    uid = m.from_user.id

    # Warn if not logged in
    sess = await SessionManager.get(uid)
    if not sess or not sess.get("token"):
        await m.reply_text(
            "⚠️ <b>Not logged in to PW!</b>\n"
            "DRM videos won't decrypt.\n"
            "Use /pwlogin first for DRM content.\n\n"
            "Non-DRM videos and PDFs will still work.",
            quote=True,
        )

    status = await m.reply_text("📥 Reading file...", quote=True)
    dl = await bot.download_media(
        m.document,
        file_name=os.path.join(Config.DOWNLOAD_DIR, fname),
    )
    with open(dl, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    os.remove(dl)

    links = _parse_txt(content)
    if not links:
        await status.edit_text("❌ No valid links found!")
        return

    await _show_and_download(bot, m, status, uid, links)


# ── Batch paste ───────────────────────────────────────────────────────────────

@AFK.on_message(filters.command("batch", prefixes))
async def batch_cmd(bot: AFK, m: Message):
    if not is_auth(m):
        return
    await m.reply_text("📋 Paste links (Name:URL format, one per line):", quote=True)
    try:
        txt = await bot.ask(m.chat.id, "Links:", timeout=300)
    except Exception:
        await m.reply_text("⏰ Timeout!")
        return

    links = _parse_txt(txt.text)
    if not links:
        await m.reply_text("❌ No valid links found!")
        return

    status = await m.reply_text(f"✅ Found {len(links)} links.")
    await _show_and_download(bot, m, status, m.from_user.id, links)


# ── Single URL ────────────────────────────────────────────────────────────────

@AFK.on_message(filters.command("dl", prefixes))
async def single_dl(bot: AFK, m: Message):
    if not is_auth(m):
        return
    args = m.text.split(None, 1)
    if len(args) < 2:
        await m.reply_text("Usage: <code>/dl URL</code>", quote=True)
        return

    uid = m.from_user.id
    raw = args[1].strip()

    try:
        nm = await bot.ask(m.chat.id, "File name (or send 'skip'):", timeout=60)
        name = nm.text.strip() if nm.text.strip().lower() != "skip" else "video"
    except Exception:
        name = "video"

    sm     = await m.reply_text(f"📥 Starting: <code>{name}</code>")
    result = await _process_one(uid, name, raw, "best", Config.DOWNLOAD_DIR, sm)

    if result:
        ul = Upload_to_Tg(bot, m, result, name, get_thumb(uid),
                          Config.DOWNLOAD_DIR, sm, name)
        if result.endswith(".pdf"):
            await ul.upload_doc()
        else:
            await ul.upload_video()
    else:
        await sm.edit_text("❌ Download failed! Check logs.")


# ── Admin Commands ────────────────────────────────────────────────────────────

@AFK.on_message(filters.command("adduser", prefixes))
async def add_user(bot: AFK, m: Message):
    if m.from_user.id != Config.OWNER_ID:
        return
    args = m.text.split()
    if len(args) < 2:
        await m.reply_text("Usage: /adduser USER_ID", quote=True)
        return
    try:
        uid = int(args[1])
        if uid not in Config.AUTH_USERS:
            Config.AUTH_USERS.append(uid)
        await m.reply_text(f"✅ Added <code>{uid}</code>", quote=True)
    except Exception:
        await m.reply_text("❌ Invalid ID!", quote=True)


@AFK.on_message(filters.command("remuser", prefixes))
async def rem_user(bot: AFK, m: Message):
    if m.from_user.id != Config.OWNER_ID:
        return
    args = m.text.split()
    if len(args) < 2:
        return
    try:
        uid = int(args[1])
        Config.AUTH_USERS = [u for u in Config.AUTH_USERS if u != uid]
        await m.reply_text(f"✅ Removed <code>{uid}</code>", quote=True)
    except Exception:
        pass


@AFK.on_message(filters.command("users", prefixes))
async def list_users(bot: AFK, m: Message):
    if m.from_user.id != Config.OWNER_ID:
        return
    if Config.AUTH_USERS:
        lst = "\n".join(f"• <code>{u}</code>" for u in Config.AUTH_USERS)
        await m.reply_text(f"<b>👥 Auth Users:</b>\n{lst}", quote=True)
    else:
        await m.reply_text("No authorized users.", quote=True)


@AFK.on_message(filters.command("setthumb", prefixes))
async def set_thumb(bot: AFK, m: Message):
    if not is_auth(m):
        return
    args = m.text.split(None, 1)
    if len(args) < 2:
        await m.reply_text("Usage: /setthumb URL", quote=True)
        return
    _user_thumb[m.from_user.id] = args[1].strip()
    await m.reply_text("✅ Thumbnail updated!", quote=True)


@AFK.on_message(filters.command("restart", prefixes))
async def restart(bot: AFK, m: Message):
    if m.from_user.id != Config.OWNER_ID:
        return
    await m.reply_text("♻️ Restarting...")
    import sys
    os.execv(sys.executable, ["python"] + sys.argv)


@AFK.on_message(filters.command("help", prefixes))
async def help_cmd(bot: AFK, m: Message):
    if not is_auth(m):
        return
    await m.reply_text(
        "<b>📖 PW DRM Bot — Commands</b>\n\n"
        "<b>Login:</b>\n"
        "• /pwlogin — Login (saved to DB, no re-login needed)\n"
        "• /pwstatus — Check login\n"
        "• /pwlogout — Logout\n"
        "• /setpwtoken — Set token manually\n\n"
        "<b>Download:</b>\n"
        "• Send .txt file → select → auto batch download\n"
        "• /dl URL — Single URL download\n"
        "• /batch — Paste multiple links\n\n"
        "<b>Settings:</b>\n"
        "• /setthumb URL — Set upload thumbnail\n\n"
        "<b>Admin (owner only):</b>\n"
        "• /adduser /remuser /users\n"
        "• /restart\n\n"
        "<b>Txt format:</b>\n"
        "<code>Name:URL&parentId=X&childId=Y</code>",
        quote=True,
    )


# ── Core Batch Processor ──────────────────────────────────────────────────────

async def _show_and_download(
    bot: AFK,
    m: Message,
    status: Message,
    uid: int,
    links: list,
):
    videos = [l for l in links if not PWAPI.parse_url(l["url"])["is_pdf"]]
    pdfs   = [l for l in links if PWAPI.parse_url(l["url"])["is_pdf"]]

    summary = (
        f"<b>📋 {len(links)} links found:</b>\n"
        f"• 🎬 Videos: {len(videos)}\n"
        f"• 📄 PDFs:   {len(pdfs)}\n\n"
    )
    for i, l in enumerate(links[:30], 1):
        icon = "📄" if PWAPI.parse_url(l["url"])["is_pdf"] else "🎬"
        summary += f"{icon} {i}. {l['name'][:60]}\n"
    if len(links) > 30:
        summary += f"...and {len(links)-30} more\n"
    summary += "\n<b>Reply with:</b> index(es) (e.g. <code>1 3 5</code>) or <code>all</code>"

    await status.edit_text(summary)

    try:
        sel = await bot.ask(m.chat.id, "Selection:", timeout=300)
    except Exception:
        await m.reply_text("⏰ Timeout!")
        return

    text = sel.text.strip().lower()
    if text == "all":
        selected = list(range(len(links)))
    else:
        selected = [
            int(x) - 1
            for x in text.split()
            if x.isdigit() and 1 <= int(x) <= len(links)
        ]

    if not selected:
        await m.reply_text("❌ No valid selection!")
        return

    # Quality
    try:
        q_msg = await bot.ask(
            m.chat.id,
            "🎯 Quality? <code>best / 1080 / 720 / 480 / 360 / worst</code>\n(Default: best)",
            timeout=60,
        )
        q = q_msg.text.strip()
        quality = q if q in ["best", "1080", "720", "480", "360", "worst"] else "best"
    except Exception:
        quality = "best"

    await m.reply_text(
        f"🚀 <b>Starting {len(selected)} downloads (Quality: {quality})</b>"
    )

    ok = fail = skipped = 0
    path = Config.DOWNLOAD_DIR

    for i, idx in enumerate(selected):
        link   = links[idx]
        name   = link["name"]
        cnt    = f"[{i+1}/{len(selected)}]"

        sm = await m.reply_text(f"{cnt} 📥 <b>Processing:</b>\n<code>{name}</code>")

        try:
            file_path = await _process_one(uid, name, link["url"], quality, path, sm)

            if file_path == "SKIPPED":
                skipped += 1
                try:
                    await sm.edit_text(f"⏭ <b>Skipped (exists):</b> <code>{name}</code>")
                except Exception:
                    pass
                continue

            if file_path and os.path.isfile(file_path):
                ul = Upload_to_Tg(
                    bot, m, file_path, name, get_thumb(uid),
                    path, sm, name
                )
                if file_path.endswith(".pdf"):
                    await ul.upload_doc()
                else:
                    await ul.upload_video()
                ok += 1
            else:
                await sm.edit_text(
                    f"❌ <b>Failed:</b> <code>{name}</code>\n"
                    "Check /pwlogin status and server logs."
                )
                fail += 1

        except Exception as e:
            LOGGER.error(f"Error on [{name}]: {e}")
            try:
                await sm.edit_text(f"❌ <code>{name}</code>\nError: <code>{str(e)[:200]}</code>")
            except Exception:
                pass
            fail += 1
            # Continue with next item

    await m.reply_text(
        f"<b>🏁 Batch Complete!</b>\n"
        f"✅ Success:  {ok}\n"
        f"❌ Failed:   {fail}\n"
        f"⏭ Skipped:  {skipped}"
    )


async def _process_one(
    uid: int,
    name: str,
    raw_url: str,
    quality: str,
    output_dir: str,
    sm: Message,
) -> str:
    """
    Process one link (PDF or video).
    Returns: file path, 'SKIPPED', or '' on failure.
    """
    parsed = PWAPI.parse_url(raw_url)

    # ── PDF ──
    if parsed["is_pdf"]:
        await _edit(sm, f"📄 <b>Downloading PDF:</b>\n<code>{name}</code>")
        return await DRMDecrypt.download_pdf(parsed["mpd_url"], name, output_dir)

    # ── Video ──
    clean = re.sub(r'[\\/*?:"<>|]', "", name).strip()[:100]
    final = os.path.join(output_dir, f"{clean}.mp4")

    # Duplicate check
    if os.path.isfile(final) and os.path.getsize(final) > 102400:
        LOGGER.info(f"Skipping duplicate: {name}")
        return "SKIPPED"

    # Get MPD URL — try fresh from API if child_id available
    mpd_url = parsed["mpd_url"]
    if parsed["child_id"] and (await SessionManager.get(uid)):
        await _edit(sm, f"🔗 <b>Fetching fresh MPD URL...</b>\n<code>{name}</code>")
        fresh = await PWAPI.get_fresh_mpd_url(
            uid, parsed["child_id"], parsed["parent_id"], mpd_url
        )
        if fresh and fresh != mpd_url:
            mpd_url = fresh
            LOGGER.info(f"Using fresh MPD for {name}")

    # Verify MPD is accessible
    if parsed["is_drm"]:
        await _edit(sm, f"🔍 <b>Checking MPD...</b>\n<code>{name}</code>")
        mpd_info = await PWAPI.fetch_mpd_info(mpd_url)

        if mpd_info["status_code"] in (403, 404) and parsed["child_id"]:
            LOGGER.warning(f"MPD expired ({mpd_info['status_code']}), re-fetching...")
            await _edit(sm, f"♻️ <b>MPD expired, re-fetching...</b>\n<code>{name}</code>")
            fresh = await PWAPI.get_fresh_mpd_url(
                uid, parsed["child_id"], parsed["parent_id"], ""
            )
            if fresh:
                mpd_url = fresh
                mpd_info = await PWAPI.fetch_mpd_info(mpd_url)

    # DRM key
    drm_key = ""
    if parsed["is_drm"] and parsed["child_id"]:
        sess = await SessionManager.get(uid)
        if not sess or not sess.get("token"):
            await _edit(sm, f"⚠️ <b>No PW session — DRM may fail</b>\n<code>{name}</code>")
        else:
            await _edit(sm, f"🔑 <b>Fetching DRM key (up to 3 tries)...</b>\n<code>{name}</code>")
            drm_key = await PWAPI.get_drm_key(
                uid, parsed["child_id"], parsed["parent_id"]
            )
            if not drm_key:
                LOGGER.error(f"DRM key not obtained for [{name}] — stopping this video")
                await _edit(
                    sm,
                    f"❌ <b>DRM key fetch failed after 3 retries</b>\n"
                    f"<code>{name}</code>\n\nSkipping to avoid corrupted file."
                )
                return ""
            LOGGER.info(f"Key obtained for [{name}]: {drm_key[:20]}...")

    # Progress callback
    async def _progress(text: str):
        await _edit(sm, f"[{quality}] {text}\n<code>{name}</code>")

    await _edit(sm, f"⬇️ <b>Downloading... [{quality}]</b>\n<code>{name}</code>")

    return await DRMDecrypt.download_and_decrypt(
        name=name,
        mpd_url=mpd_url,
        key=drm_key,
        output_dir=output_dir,
        quality=quality,
        progress_cb=_progress,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_txt(content: str) -> list[dict]:
    links = []
    seen  = set()
    for line in content.strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        idx = line.find(":https://")
        if idx != -1:
            name = line[:idx].strip()
            url  = line[idx+1:].strip()
        elif line.startswith("https://"):
            name = line.split("/")[-1][:60]
            url  = line
        else:
            continue
        # Deduplicate by URL
        key = url.split("&")[0]
        if key in seen:
            continue
        seen.add(key)
        links.append({"name": name, "url": url})
    return links


async def _edit(msg: Message, text: str):
    try:
        await msg.edit_text(text)
    except Exception:
        pass
