#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# PW DRM Bot — all handlers (python-telegram-bot 20.x)

import os
import re
import asyncio

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes,
)
from main import Config, Msg, LOGGER
from handlers.session import SessionManager
from handlers.pw_api import PWAPI
from handlers.drm_decrypt import DRMDecrypt
from handlers.uploader import Upload_to_Tg
from helpers.conversation import conv_ask, conv_resolve

_user_thumb: dict = {}


# ── Auth helpers ──────────────────────────────────────────────────────────────

def is_auth(uid: int) -> bool:
    return uid == Config.OWNER_ID or uid in Config.AUTH_USERS


def get_thumb(uid: int) -> str:
    return _user_thumb.get(uid, Config.THUMB_URL or "")


# ── Text router — MUST run before commands so conversations resolve ────────────

async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.effective_user:
        conv_resolve(update.effective_chat.id, update.message)


# ── Debug — log every incoming update ─────────────────────────────────────────

async def debug_any(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid  = update.effective_user.id if update.effective_user else "?"
        chat = update.effective_chat.id if update.effective_chat else "?"
        txt  = ""
        if update.message:
            txt = (update.message.text or update.message.caption or "[media]")[:60]
        LOGGER.info(f"[IN] uid={uid} chat={chat} text={txt!r}")
    except Exception:
        pass


# ── /ping ─────────────────────────────────────────────────────────────────────

async def ping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ok  = is_auth(uid)
    await update.message.reply_text(
        f"🏓 <b>Pong!</b>\n"
        f"Your ID: <code>{uid}</code>\n"
        f"OWNER_ID: <code>{Config.OWNER_ID}</code>\n"
        f"Auth: {'✅ YES' if ok else '❌ NO — /adduser se add karo'}",
        parse_mode="HTML",
    )


# ── /start ────────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_auth(uid):
        await update.message.reply_text("⛔ <b>Not authorized!</b>", parse_mode="HTML")
        return
    btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔑 PW Login",   callback_data="pw_login_help"),
         InlineKeyboardButton("📖 Help",       callback_data="help")],
        [InlineKeyboardButton("✅ My Status",  callback_data="status")],
    ])
    await update.message.reply_text(Msg.START_MSG, reply_markup=btn, parse_mode="HTML")


# ── Callback queries ──────────────────────────────────────────────────────────

async def cb_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cq  = update.callback_query
    uid = cq.from_user.id
    if not is_auth(uid):
        await cq.answer("⛔ Not authorized!")
        return
    await cq.answer()
    d = cq.data

    if d == "help":
        await cq.message.edit_text(
            "<b>📖 Help</b>\n\n"
            "<b>Step 1:</b> /pwlogin → phone + OTP\n"
            "<b>Step 2:</b> Send <code>.txt</code> file\n"
            "<b>Step 3:</b> Select videos → choose quality\n\n"
            "<b>Txt format:</b>\n"
            "<code>Name:https://...master.mpd&amp;parentId=X&amp;childId=Y</code>\n"
            "<code>Name:https://static.pw.live/.../file.pdf</code>",
            parse_mode="HTML",
        )
    elif d == "pw_login_help":
        await cq.message.edit_text(
            "<b>🔑 PW Login</b>\n\n"
            "/pwlogin → Enter phone → Enter OTP\n"
            "Session saved in MongoDB — no re-login needed!",
            parse_mode="HTML",
        )
    elif d == "status":
        sess = await SessionManager.get(uid)
        if sess and sess.get("token"):
            p = await PWAPI.get_profile(uid)
            await cq.message.edit_text(
                f"✅ <b>Logged in</b>\n"
                f"Name: {p.get('name', '?')}\n"
                f"Mobile: +91{sess.get('mobile', '?')}",
                parse_mode="HTML",
            )
        else:
            await cq.message.edit_text(
                "❌ <b>Not logged in</b>\nUse /pwlogin", parse_mode="HTML"
            )
    elif d == "back":
        btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔑 PW Login",  callback_data="pw_login_help"),
             InlineKeyboardButton("📖 Help",      callback_data="help")],
            [InlineKeyboardButton("✅ My Status", callback_data="status")],
        ])
        await cq.message.edit_text(Msg.START_MSG, reply_markup=btn, parse_mode="HTML")


# ── /pwlogin ──────────────────────────────────────────────────────────────────

async def pw_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    chat = update.effective_chat.id
    if not is_auth(uid):
        return

    sess = await SessionManager.get(uid)
    if sess and sess.get("token"):
        p = await PWAPI.get_profile(uid)
        if p:
            await update.message.reply_text(
                f"✅ <b>Already logged in!</b>\n"
                f"Name: {p.get('name', '?')}\n"
                f"Mobile: +91{sess.get('mobile', '?')}\n\n"
                f"Use /pwlogout to switch account.",
                parse_mode="HTML",
            )
            return

    await update.message.reply_text(
        "<b>📱 PW Login</b>\n\nEnter your PW registered phone number:",
        parse_mode="HTML",
    )
    try:
        phone_msg = await conv_ask(context.bot, chat, "📞 Phone number:", timeout=120)
    except Exception:
        await update.message.reply_text("⏰ Timeout! Try /pwlogin again.")
        return

    phone = phone_msg.text.strip()
    if "/cancel" in phone:
        await update.message.reply_text("❌ Cancelled!")
        return

    mobile = re.sub(r"\D", "", phone)
    if mobile.startswith("91") and len(mobile) == 12:
        mobile = mobile[2:]
    if not mobile.isdigit() or len(mobile) != 10:
        await update.message.reply_text("❌ Invalid number! Enter 10-digit mobile.")
        return

    wait = await update.message.reply_text("⏳ Sending OTP...", parse_mode="HTML")
    result = await PWAPI.send_otp(mobile)

    if result.get("error") or result.get("status") == "failure":
        await wait.edit_text(f"❌ OTP send failed!\n<code>{result}</code>", parse_mode="HTML")
        return

    await wait.edit_text("✅ <b>OTP sent!</b> Check PW app or SMS.", parse_mode="HTML")
    try:
        otp_msg = await conv_ask(context.bot, chat, "🔢 Enter OTP:", timeout=120)
    except Exception:
        await update.message.reply_text("⏰ OTP timeout! Try /pwlogin again.")
        return

    otp = otp_msg.text.strip()
    if "/cancel" in otp:
        await update.message.reply_text("❌ Cancelled!")
        return

    ver = await update.message.reply_text("⏳ Verifying OTP...", parse_mode="HTML")
    result2 = await PWAPI.verify_otp(uid, mobile, otp)

    if result2.get("error") or not (await SessionManager.get(uid)):
        msg = result2.get("message", str(result2))
        await ver.edit_text(f"❌ <b>Login failed!</b>\n<code>{msg}</code>", parse_mode="HTML")
        return

    p = await PWAPI.get_profile(uid)
    await ver.edit_text(
        f"✅ <b>PW Login Successful!</b>\n\n"
        f"Name: {p.get('name', '?')}\nMobile: +91{mobile}\n\n"
        f"Now send your .txt file!",
        parse_mode="HTML",
    )
    LOGGER.info(f"Login OK uid={uid} mobile={mobile}")


async def pw_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_auth(uid):
        return
    sess = await SessionManager.get(uid)
    if not sess or not sess.get("token"):
        await update.message.reply_text("❌ Not logged in. Use /pwlogin", parse_mode="HTML")
        return
    p = await PWAPI.get_profile(uid)
    if p:
        await update.message.reply_text(
            f"✅ <b>Logged in</b>\nName: {p.get('name','?')}\nMobile: +91{sess.get('mobile','?')}",
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text("⚠️ Token may be expired. Use /pwlogin.", parse_mode="HTML")


async def pw_logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_auth(uid):
        return
    await SessionManager.delete(uid)
    await update.message.reply_text("✅ Logged out!")


async def set_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_auth(uid):
        return
    args = update.message.text.split(None, 1)
    if len(args) < 2:
        await update.message.reply_text("Usage: <code>/setpwtoken YOUR_TOKEN</code>", parse_mode="HTML")
        return
    await SessionManager.save(uid, args[1].strip(), "", "manual")
    await update.message.reply_text("✅ PW Token saved!")
    try:
        await update.message.delete()
    except Exception:
        pass


# ── TXT file handler ──────────────────────────────────────────────────────────

async def handle_txt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_auth(uid):
        return
    doc = update.message.document
    if not doc or not (doc.file_name or "").endswith(".txt"):
        return

    sess = await SessionManager.get(uid)
    if not sess or not sess.get("token"):
        await update.message.reply_text(
            "⚠️ <b>Not logged in to PW!</b>\n"
            "DRM videos won't decrypt without login.\n"
            "Use /pwlogin first.",
            parse_mode="HTML",
        )

    status = await update.message.reply_text("📥 Reading file...", parse_mode="HTML")
    local_path = os.path.join(Config.DOWNLOAD_DIR, doc.file_name)

    try:
        tg_file = await context.bot.get_file(doc.file_id)
        await tg_file.download_to_drive(local_path)
        with open(local_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        os.remove(local_path)
    except Exception as e:
        await status.edit_text(f"❌ Could not read file: <code>{e}</code>", parse_mode="HTML")
        return

    links = _parse_txt(content)
    if not links:
        await status.edit_text("❌ No valid links found in file!")
        return

    await _show_and_download(context.bot, update, status, uid, links)


# ── /batch ────────────────────────────────────────────────────────────────────

async def batch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    chat = update.effective_chat.id
    if not is_auth(uid):
        return
    try:
        txt = await conv_ask(context.bot, chat, "📋 Paste links (Name:URL format, one per line):", timeout=300)
    except Exception:
        await update.message.reply_text("⏰ Timeout!")
        return

    links = _parse_txt(txt.text)
    if not links:
        await update.message.reply_text("❌ No valid links found!")
        return

    status = await update.message.reply_text(f"✅ Found {len(links)} links.")
    await _show_and_download(context.bot, update, status, uid, links)


# ── /dl ───────────────────────────────────────────────────────────────────────

async def single_dl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    chat = update.effective_chat.id
    if not is_auth(uid):
        return
    args = update.message.text.split(None, 1)
    if len(args) < 2:
        await update.message.reply_text("Usage: <code>/dl URL</code>", parse_mode="HTML")
        return

    raw = args[1].strip()
    try:
        nm   = await conv_ask(context.bot, chat, "📝 File name (or send <code>skip</code>):", timeout=60)
        name = nm.text.strip() if nm.text.strip().lower() != "skip" else "video"
    except Exception:
        name = "video"

    sm     = await update.message.reply_text(f"📥 Starting: <code>{name}</code>", parse_mode="HTML")
    result = await _process_one(uid, name, raw, "best", Config.DOWNLOAD_DIR, sm)

    if result and os.path.isfile(result):
        ul = Upload_to_Tg(
            context.bot, chat, update.message.message_id,
            result, name, get_thumb(uid), Config.DOWNLOAD_DIR, sm, name,
        )
        if result.endswith(".pdf"):
            await ul.upload_doc()
        else:
            await ul.upload_video()
    else:
        await _edit(sm, "❌ Download failed! Check logs.")


# ── Admin commands ────────────────────────────────────────────────────────────

async def add_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != Config.OWNER_ID:
        return
    args = update.message.text.split()
    if len(args) < 2:
        await update.message.reply_text("Usage: /adduser USER_ID")
        return
    try:
        uid = int(args[1])
        if uid not in Config.AUTH_USERS:
            Config.AUTH_USERS.append(uid)
        await update.message.reply_text(f"✅ Added <code>{uid}</code>", parse_mode="HTML")
    except Exception:
        await update.message.reply_text("❌ Invalid ID!")


async def rem_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != Config.OWNER_ID:
        return
    args = update.message.text.split()
    if len(args) < 2:
        return
    try:
        uid = int(args[1])
        Config.AUTH_USERS = [u for u in Config.AUTH_USERS if u != uid]
        await update.message.reply_text(f"✅ Removed <code>{uid}</code>", parse_mode="HTML")
    except Exception:
        pass


async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != Config.OWNER_ID:
        return
    if Config.AUTH_USERS:
        lst = "\n".join(f"• <code>{u}</code>" for u in Config.AUTH_USERS)
        await update.message.reply_text(f"<b>👥 Auth Users:</b>\n{lst}", parse_mode="HTML")
    else:
        await update.message.reply_text("No authorized users.")


async def set_thumb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_auth(uid):
        return
    args = update.message.text.split(None, 1)
    if len(args) < 2:
        await update.message.reply_text("Usage: /setthumb URL")
        return
    _user_thumb[uid] = args[1].strip()
    await update.message.reply_text("✅ Thumbnail updated!")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_auth(uid):
        return
    await update.message.reply_text(
        "<b>📖 PW DRM Bot — Help</b>\n\n"
        "<b>Login:</b>\n"
        "• /pwlogin — Login with phone + OTP\n"
        "• /pwstatus — Check login status\n"
        "• /pwlogout — Logout\n"
        "• /setpwtoken TOKEN — Set token manually\n\n"
        "<b>Download:</b>\n"
        "• Send <code>.txt</code> file → batch download\n"
        "• /dl URL — Single video/PDF\n"
        "• /batch — Paste links\n\n"
        "<b>Settings:</b>\n"
        "• /setthumb URL — Set thumbnail\n\n"
        "<b>Admin (owner only):</b>\n"
        "• /adduser ID  /remuser ID  /users\n\n"
        "• /ping — Check bot is alive",
        parse_mode="HTML",
    )


# ── Core batch processor ──────────────────────────────────────────────────────

async def _show_and_download(bot, update: Update, status, uid, links):
    chat   = update.effective_chat.id
    msg_id = update.message.message_id

    videos = [l for l in links if not PWAPI.parse_url(l["url"])["is_pdf"]]
    pdfs   = [l for l in links if PWAPI.parse_url(l["url"])["is_pdf"]]

    summary = (
        f"<b>📋 {len(links)} links found:</b>\n"
        f"🎬 Videos: {len(videos)}   📄 PDFs: {len(pdfs)}\n\n"
    )
    for i, l in enumerate(links[:30], 1):
        icon = "📄" if PWAPI.parse_url(l["url"])["is_pdf"] else "🎬"
        summary += f"{icon} {i}. {l['name'][:60]}\n"
    if len(links) > 30:
        summary += f"...and {len(links) - 30} more\n"
    summary += "\n<b>Reply:</b> <code>1 3 5</code> or <code>all</code>"

    await status.edit_text(summary, parse_mode="HTML")

    try:
        sel = await conv_ask(bot, chat, "✏️ Selection:", timeout=300)
    except Exception:
        await bot.send_message(chat, "⏰ Timeout!", parse_mode="HTML")
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
        await bot.send_message(chat, "❌ No valid selection!", parse_mode="HTML")
        return

    try:
        q_msg   = await conv_ask(
            bot, chat,
            "🎯 Quality? <code>best / 1080 / 720 / 480 / 360 / worst</code>",
            timeout=60,
        )
        q       = q_msg.text.strip()
        quality = q if q in ["best", "1080", "720", "480", "360", "worst"] else "best"
    except Exception:
        quality = "best"

    await bot.send_message(
        chat,
        f"🚀 <b>Starting {len(selected)} downloads (Quality: {quality})</b>",
        parse_mode="HTML",
    )

    ok = fail = skipped = 0
    path = Config.DOWNLOAD_DIR

    for i, idx in enumerate(selected):
        link = links[idx]
        name = link["name"]
        cnt  = f"[{i+1}/{len(selected)}]"
        sm   = await bot.send_message(
            chat,
            f"{cnt} 📥 <b>Processing:</b>\n<code>{name}</code>",
            parse_mode="HTML",
        )

        try:
            file_path = await _process_one(uid, name, link["url"], quality, path, sm)

            if file_path == "SKIPPED":
                skipped += 1
                await _edit(sm, f"⏭ <b>Skipped (already done):</b>\n<code>{name}</code>")
                continue

            if file_path and os.path.isfile(file_path):
                ul = Upload_to_Tg(
                    bot, chat, msg_id,
                    file_path, name, get_thumb(uid), path, sm, name,
                )
                if file_path.endswith(".pdf"):
                    await ul.upload_doc()
                else:
                    await ul.upload_video()
                ok += 1
            else:
                await _edit(sm, f"❌ <b>Failed:</b>\n<code>{name}</code>")
                fail += 1

        except Exception as e:
            LOGGER.error(f"Error processing [{name}]: {e}")
            await _edit(sm, f"❌ <code>{name}</code>\nError: <code>{str(e)[:200]}</code>")
            fail += 1

    await bot.send_message(
        chat,
        f"<b>🏁 All done!</b>\n✅ {ok}  ❌ {fail}  ⏭ {skipped}",
        parse_mode="HTML",
    )


async def _process_one(uid, name, raw_url, quality, output_dir, sm):
    parsed = PWAPI.parse_url(raw_url)

    if parsed["is_pdf"]:
        await _edit(sm, f"📄 <b>Downloading PDF:</b>\n<code>{name}</code>")
        return await DRMDecrypt.download_pdf(parsed["mpd_url"], name, output_dir)

    clean = re.sub(r'[\\/*?:"<>|]', "", name).strip()[:100]
    final = os.path.join(output_dir, f"{clean}.mp4")

    if os.path.isfile(final) and os.path.getsize(final) > 102400:
        return "SKIPPED"

    mpd_url = parsed["mpd_url"]
    if parsed["child_id"] and (await SessionManager.get(uid)):
        await _edit(sm, f"🔗 <b>Fetching fresh MPD...</b>\n<code>{name}</code>")
        fresh = await PWAPI.get_fresh_mpd_url(uid, parsed["child_id"], parsed["parent_id"], mpd_url)
        if fresh and fresh != mpd_url:
            mpd_url = fresh

    if parsed["is_drm"]:
        mpd_info = await PWAPI.fetch_mpd_info(mpd_url)
        if mpd_info["status_code"] in (403, 404) and parsed["child_id"]:
            await _edit(sm, f"♻️ <b>MPD expired, re-fetching...</b>\n<code>{name}</code>")
            fresh = await PWAPI.get_fresh_mpd_url(uid, parsed["child_id"], parsed["parent_id"], "")
            if fresh:
                mpd_url = fresh

    drm_key = ""
    if parsed["is_drm"] and parsed["child_id"]:
        sess = await SessionManager.get(uid)
        if not sess or not sess.get("token"):
            await _edit(sm, f"⚠️ <b>No PW session — DRM may fail</b>\n<code>{name}</code>")
        else:
            await _edit(sm, f"🔑 <b>Fetching DRM key...</b>\n<code>{name}</code>")
            drm_key = await PWAPI.get_drm_key(uid, parsed["child_id"], parsed["parent_id"])
            if not drm_key:
                await _edit(sm, f"❌ <b>DRM key failed</b>\n<code>{name}</code>")
                return ""

    await _edit(sm, f"⬇️ <b>Downloading [{quality}]...</b>\n<code>{name}</code>")
    return await DRMDecrypt.download_and_decrypt(
        name=name, mpd_url=mpd_url, key=drm_key,
        output_dir=output_dir, quality=quality,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_txt(content: str) -> list:
    links = []
    seen  = set()
    for line in content.strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        idx = line.find(":https://")
        if idx != -1:
            name = line[:idx].strip()
            url  = line[idx + 1:].strip()
        elif line.startswith("https://"):
            name = line.split("/")[-1][:60]
            url  = line
        else:
            continue
        key = url.split("&")[0]
        if key in seen:
            continue
        seen.add(key)
        links.append({"name": name, "url": url})
    return links


async def _edit(msg, text: str):
    try:
        await msg.edit_text(text, parse_mode="HTML")
    except Exception:
        pass


# ── Register all handlers with the Application ────────────────────────────────

def setup_handlers(app: Application):
    # group -999: debug log every update (runs first)
    app.add_handler(MessageHandler(filters.ALL, debug_any), group=-999)

    # group -1: conversation resolver — runs before commands
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_router),
        group=-1,
    )

    # group 0: all command and message handlers
    app.add_handler(CommandHandler(["ping"],       ping_cmd))
    app.add_handler(CommandHandler(["start"],      start))
    app.add_handler(CommandHandler(["help"],       help_cmd))
    app.add_handler(CommandHandler(["pwlogin"],    pw_login))
    app.add_handler(CommandHandler(["pwlogout"],   pw_logout))
    app.add_handler(CommandHandler(["pwstatus"],   pw_status))
    app.add_handler(CommandHandler(["setpwtoken"], set_token))
    app.add_handler(CommandHandler(["dl"],         single_dl))
    app.add_handler(CommandHandler(["batch"],      batch_cmd))
    app.add_handler(CommandHandler(["adduser"],    add_user))
    app.add_handler(CommandHandler(["remuser"],    rem_user))
    app.add_handler(CommandHandler(["users"],      list_users))
    app.add_handler(CommandHandler(["setthumb"],   set_thumb))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_txt))
    app.add_handler(CallbackQueryHandler(cb_handler))

    LOGGER.info(f"All handlers registered!")
