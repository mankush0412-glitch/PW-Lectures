#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# PW Plugin — complete Telegram bot interface

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

_user_thumb: dict = {}

# Stores pending link lists per user (for confirmation flow)
# uid → {"links": [...], "msg_id": int}
_pending: dict = {}


def is_auth(m: Message) -> bool:
    uid = m.from_user.id
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
        [InlineKeyboardButton("🔑 PW Login",   callback_data="pw_login_help"),
         InlineKeyboardButton("📖 Help",       callback_data="help")],
        [InlineKeyboardButton("✅ My Status",  callback_data="status")],
    ])
    await m.reply_text(Msg.START_MSG, reply_markup=btn, quote=True)


@AFK.on_callback_query()
async def cb_handler(bot: AFK, cq):
    if not is_auth(cq.message):
        return
    uid = cq.from_user.id
    d   = cq.data

    # ── Confirmation flow ─────────────────────────────────────────────────
    if d == "confirm_proceed":
        data = _pending.get(uid)
        if not data:
            await cq.answer("Session expired, send file again!", show_alert=True)
            return
        await cq.answer("Chalte hain! 🚀")
        # Remove confirm buttons
        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        links = data["links"]
        await _ask_selection_and_download(bot, cq.message, uid, links)
        return

    if d == "confirm_cancel":
        _pending.pop(uid, None)
        await cq.answer("Cancelled!")
        try:
            await cq.message.edit_text("❌ <b>Cancelled.</b>")
        except Exception:
            pass
        return

    # ── Info callbacks ────────────────────────────────────────────────────
    if d == "help":
        await cq.message.edit_text(
            "<b>📖 Help</b>\n\n"
            "<b>Step 1:</b> /pwlogin → phone + OTP\n"
            "<b>Step 2:</b> Send .txt file\n"
            "<b>Step 3:</b> Confirm → select → quality\n"
            "<b>Bot does:</b> DRM key → Download → Decrypt → Upload\n\n"
            "<b>Txt format:</b>\n"
            "<code>Name:https://...master.mpd&parentId=X&childId=Y</code>\n"
            "<code>Name:https://static.pw.live/.../file.pdf</code>"
        )
    elif d == "pw_login_help":
        await cq.message.edit_text(
            "<b>🔑 PW Login</b>\n\n"
            "/pwlogin → phone → OTP\n"
            "Session saved in DB — no re-login needed!"
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

    sess = await SessionManager.get(uid)
    if sess and sess.get("token"):
        p = await PWAPI.get_profile(uid)
        if p:
            await m.reply_text(
                f"✅ <b>Already logged in!</b>\n"
                f"Name: {p.get('name','?')}\nMobile: +91{sess.get('mobile','?')}\n\n"
                f"Use /pwlogout to switch account.",
                quote=True,
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
        await m.reply_text("❌ Invalid number!")
        return

    wait = await m.reply_text("⏳ Sending OTP...")
    result = await PWAPI.send_otp(mobile)
    if result.get("error") or result.get("status") == "failure":
        await wait.edit_text(f"❌ OTP send failed!\n<code>{result}</code>")
        return

    await wait.edit_text("✅ <b>OTP sent!</b>\nEnter OTP:")
    try:
        otp_msg = await bot.ask(m.chat.id, "OTP:", timeout=120)
    except Exception:
        await m.reply_text("⏰ Timeout!")
        return

    otp = otp_msg.text.strip()
    if "/cancel" in otp:
        await m.reply_text("❌ Cancelled!")
        return

    ver = await m.reply_text("⏳ Verifying...")
    result2 = await PWAPI.verify_otp(uid, mobile, otp)
    if result2.get("error") or not (await SessionManager.get(uid)):
        await ver.edit_text(f"❌ <b>Login failed!</b>\n<code>{result2.get('message', str(result2))}</code>")
        return

    p = await PWAPI.get_profile(uid)
    await ver.edit_text(
        f"✅ <b>Login Successful!</b>\n\n"
        f"Name: {p.get('name','?')}\nMobile: +91{mobile}\n\n"
        f"Session saved! Send your .txt file now."
    )
    LOGGER.info(f"Login OK uid={uid}")


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
    await SessionManager.save(m.from_user.id, args[1].strip(), "", "manual")
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

    sess = await SessionManager.get(uid)
    if not sess or not sess.get("token"):
        await m.reply_text(
            "⚠️ <b>PW mein login nahi hai!</b>\n"
            "DRM videos decrypt nahi honge.\n"
            "Use /pwlogin for DRM content.\n\n"
            "Non-DRM videos aur PDFs kaam karenge.",
            quote=True,
        )

    status = await m.reply_text("📥 <b>File padh raha hoon...</b>", quote=True)
    dl = await bot.download_media(
        m.document,
        file_name=os.path.join(Config.DOWNLOAD_DIR, fname),
    )
    with open(dl, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    os.remove(dl)

    links = _parse_txt(content)
    if not links:
        await status.edit_text("❌ Koi valid links nahi mila file mein!")
        return

    await _show_preview_with_confirm(bot, m, status, uid, links)


# ── Batch (text paste) ────────────────────────────────────────────────────────

@AFK.on_message(filters.command("batch", prefixes))
async def batch_cmd(bot: AFK, m: Message):
    if not is_auth(m):
        return
    await m.reply_text(
        "📋 <b>Links paste karo</b> (ek line mein ek, Name:URL format):",
        quote=True,
    )
    try:
        txt = await bot.ask(m.chat.id, "Links:", timeout=300)
    except Exception:
        await m.reply_text("⏰ Timeout!")
        return

    links = _parse_txt(txt.text)
    if not links:
        await m.reply_text("❌ Koi valid links nahi mila!")
        return

    status = await m.reply_text(f"📋 <b>{len(links)} links mili.</b>")
    await _show_preview_with_confirm(bot, m, status, m.from_user.id, links)


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
        nm = await bot.ask(m.chat.id, "File name bhejo (ya 'skip'):", timeout=60)
        name = nm.text.strip() if nm.text.strip().lower() != "skip" else "video"
    except Exception:
        name = "video"

    sm     = await m.reply_text(f"📥 Shuru: <code>{name}</code>")
    result = await _process_one(uid, name, raw, "best", Config.DOWNLOAD_DIR, sm)

    if result and result != "SKIPPED":
        ul = Upload_to_Tg(bot, m, result, name, get_thumb(uid),
                          Config.DOWNLOAD_DIR, sm, name)
        await (ul.upload_doc() if result.endswith(".pdf") else ul.upload_video())
    elif result == "SKIPPED":
        await sm.edit_text(f"⏭ Already downloaded: <code>{name}</code>")
    else:
        await sm.edit_text("❌ Download failed! /pwlogin check karo.")


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
        "• /pwlogin — Login (DB mein save, dobara nahi karna)\n"
        "• /pwstatus — Login check\n"
        "• /pwlogout — Logout\n"
        "• /setpwtoken — Token manually set\n\n"
        "<b>Download:</b>\n"
        "• .txt file bhejo → links dikhenge → confirm karo → shuru!\n"
        "• /dl URL — Single URL\n"
        "• /batch — Links paste karo\n\n"
        "<b>Settings:</b>\n"
        "• /setthumb URL — Upload thumbnail\n\n"
        "<b>Admin:</b>\n"
        "• /adduser /remuser /users /restart\n\n"
        "<b>Txt format:</b>\n"
        "<code>Name:URL&parentId=X&childId=Y</code>",
        quote=True,
    )


# ── Core: Show preview + confirm ──────────────────────────────────────────────

async def _show_preview_with_confirm(
    bot: AFK,
    m: Message,
    status: Message,
    uid: int,
    links: list,
):
    """
    Show the full extracted link list to the user, then ask:
      ✅ Haan, Shuru Karo  |  ❌ Cancel
    Only proceeds when user clicks Haan.
    """
    videos = [l for l in links if not PWAPI.parse_url(l["url"])["is_pdf"]]
    pdfs   = [l for l in links if PWAPI.parse_url(l["url"])["is_pdf"]]

    # Build preview text
    preview  = f"<b>📂 File se {len(links)} links nikle:</b>\n"
    preview += f"├ 🎬 Videos : <b>{len(videos)}</b>\n"
    preview += f"└ 📄 PDFs   : <b>{len(pdfs)}</b>\n\n"
    preview += "<b>📋 List:</b>\n"

    for i, l in enumerate(links[:40], 1):
        parsed = PWAPI.parse_url(l["url"])
        icon   = "📄" if parsed["is_pdf"] else ("🔒" if parsed["is_drm"] else "🎬")
        preview += f"{icon} <b>{i}.</b> {l['name'][:55]}\n"

    if len(links) > 40:
        preview += f"\n<i>...aur {len(links)-40} links</i>\n"

    preview += "\n<b>Inhe download karna hai?</b>"

    # Save pending
    _pending[uid] = {"links": links}

    btn = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Haan, Shuru Karo", callback_data="confirm_proceed"),
            InlineKeyboardButton("❌ Cancel",            callback_data="confirm_cancel"),
        ]
    ])

    await status.edit_text(preview, reply_markup=btn)


# ── Selection + Quality + Download loop ───────────────────────────────────────

async def _ask_selection_and_download(
    bot: AFK,
    m: Message,
    uid: int,
    links: list,
):
    """Ask which items to download, then quality, then run batch."""
    # Selection
    sel_text = (
        f"<b>✅ {len(links)} links ready.</b>\n\n"
        "Kaunse download karne hain?\n"
        "• <code>all</code> — sab\n"
        "• <code>1 2 3</code> — selected numbers\n"
        "• <code>1-10</code> — range\n"
        "• <code>1-5 8 12</code> — mix"
    )

    try:
        sel_msg = await bot.ask(m.chat.id, sel_text, timeout=300)
    except Exception:
        await m.reply_text("⏰ Timeout! File dobara bhejo.")
        return

    text     = sel_msg.text.strip().lower()
    selected = _parse_selection(text, len(links))

    if not selected:
        await m.reply_text("❌ Valid selection nahi di!")
        return

    # Quality
    try:
        q_msg = await bot.ask(
            m.chat.id,
            "🎯 <b>Quality choose karo:</b>\n"
            "<code>best</code> / <code>1080</code> / <code>720</code> / "
            "<code>480</code> / <code>360</code> / <code>worst</code>\n"
            "(Default: best — bas Enter dabao)",
            timeout=60,
        )
        q       = q_msg.text.strip()
        quality = q if q in ["best", "1080", "720", "480", "360", "worst"] else "best"
    except Exception:
        quality = "best"

    total = len(selected)
    await m.reply_text(
        f"🚀 <b>{total} items download shuru hote hain!</b>\n"
        f"Quality: <b>{quality}</b>"
    )

    ok = fail = skipped = 0
    path = Config.DOWNLOAD_DIR

    for i, idx in enumerate(selected):
        link = links[idx]
        name = link["name"]
        cnt  = f"[{i+1}/{total}]"

        sm = await m.reply_text(f"{cnt} 📥 <b>Processing:</b>\n<code>{name}</code>")

        try:
            file_path = await _process_one(uid, name, link["url"], quality, path, sm)

            if file_path == "SKIPPED":
                skipped += 1
                try:
                    await sm.edit_text(f"⏭ <b>Pehle se hai:</b> <code>{name}</code>")
                except Exception:
                    pass
                continue

            if file_path and os.path.isfile(file_path):
                ul = Upload_to_Tg(bot, m, file_path, name, get_thumb(uid), path, sm, name)
                await (ul.upload_doc() if file_path.endswith(".pdf") else ul.upload_video())
                ok += 1
            else:
                await sm.edit_text(
                    f"❌ <b>Failed:</b> <code>{name}</code>\n"
                    "/pwlogin check karo ya logs dekho."
                )
                fail += 1

        except Exception as e:
            LOGGER.error(f"Error [{name}]: {e}")
            try:
                await sm.edit_text(
                    f"❌ <code>{name}</code>\nError: <code>{str(e)[:200]}</code>"
                )
            except Exception:
                pass
            fail += 1

    # Final summary
    await m.reply_text(
        f"<b>🏁 Batch Complete!</b>\n\n"
        f"✅ Success  : {ok}\n"
        f"❌ Failed   : {fail}\n"
        f"⏭ Skipped  : {skipped}\n"
        f"📦 Total    : {total}"
    )


# ── Per-item processor ────────────────────────────────────────────────────────

async def _process_one(
    uid: int,
    name: str,
    raw_url: str,
    quality: str,
    output_dir: str,
    sm: Message,
) -> str:
    """
    Process one link.
    Returns: file path | 'SKIPPED' | ''
    """
    parsed = PWAPI.parse_url(raw_url)

    # PDF
    if parsed["is_pdf"]:
        await _edit(sm, f"📄 <b>PDF download ho raha hai:</b>\n<code>{name}</code>")
        return await DRMDecrypt.download_pdf(parsed["mpd_url"], name, output_dir)

    # Duplicate check
    clean = re.sub(r'[\\/*?:"<>|]', "", name).strip()[:100]
    final = os.path.join(output_dir, f"{clean}.mp4")
    if os.path.isfile(final) and os.path.getsize(final) > 102400:
        LOGGER.info(f"Skipping duplicate: {name}")
        return "SKIPPED"

    # Fresh MPD
    mpd_url = parsed["mpd_url"]
    if parsed["child_id"] and (await SessionManager.get(uid)):
        await _edit(sm, f"🔗 <b>Fresh MPD URL fetch kar raha hoon...</b>\n<code>{name}</code>")
        fresh = await PWAPI.get_fresh_mpd_url(
            uid, parsed["child_id"], parsed["parent_id"], mpd_url
        )
        if fresh and fresh != mpd_url:
            mpd_url = fresh

    # Check if MPD is accessible, re-fetch if expired
    if parsed["is_drm"]:
        await _edit(sm, f"🔍 <b>MPD check kar raha hoon...</b>\n<code>{name}</code>")
        mpd_info = await PWAPI.fetch_mpd_info(mpd_url)
        if mpd_info["status_code"] in (403, 404) and parsed["child_id"]:
            await _edit(sm, f"♻️ <b>MPD expire hua, re-fetch ho raha hai...</b>\n<code>{name}</code>")
            fresh = await PWAPI.get_fresh_mpd_url(
                uid, parsed["child_id"], parsed["parent_id"], ""
            )
            if fresh:
                mpd_url = fresh

    # DRM key
    drm_key = ""
    if parsed["is_drm"] and parsed["child_id"]:
        sess = await SessionManager.get(uid)
        if not sess or not sess.get("token"):
            await _edit(sm, f"⚠️ <b>PW session nahi — DRM fail ho sakta hai</b>\n<code>{name}</code>")
        else:
            await _edit(sm, f"🔑 <b>DRM key fetch ho rahi hai (3 tries)...</b>\n<code>{name}</code>")
            drm_key = await PWAPI.get_drm_key(uid, parsed["child_id"], parsed["parent_id"])
            if not drm_key:
                await _edit(
                    sm,
                    f"❌ <b>DRM key nahi mila 3 tries ke baad</b>\n"
                    f"<code>{name}</code>\n\nCorrupt file se bachne ke liye skip kar raha hoon."
                )
                return ""
            LOGGER.info(f"Key mili [{name}]: {drm_key[:20]}...")

    # Progress callback
    async def _progress(text: str):
        await _edit(sm, f"[{quality}] {text}\n<code>{name}</code>")

    await _edit(sm, f"⬇️ <b>Download ho raha hai [{quality}]...</b>\n<code>{name}</code>")
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
    """Parse Name:URL lines from any text content."""
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
        key = url.split("&")[0].split("?")[0]
        if key in seen:
            continue
        seen.add(key)
        links.append({"name": name, "url": url})
    return links


def _parse_selection(text: str, total: int) -> list[int]:
    """
    Parse selection string into 0-based index list.
    Supports: 'all', '1 2 3', '1-5', '1-3 7 9'
    """
    if text == "all":
        return list(range(total))

    result = []
    for part in text.split():
        if "-" in part:
            try:
                a, b = part.split("-", 1)
                a, b = int(a), int(b)
                for x in range(a, b + 1):
                    if 1 <= x <= total and (x-1) not in result:
                        result.append(x - 1)
            except Exception:
                pass
        elif part.isdigit():
            x = int(part)
            if 1 <= x <= total and (x-1) not in result:
                result.append(x - 1)
    return result


async def _edit(msg: Message, text: str):
    try:
        await msg.edit_text(text)
    except Exception:
        pass
