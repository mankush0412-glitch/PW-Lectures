# 🤖 Advanced PW DRM Bot

A Telegram bot that downloads **DRM-protected** and non-DRM videos from **Physics Wallah (PW)**, plus PDFs — and uploads them directly to Telegram.

---

## ✨ Features

| Feature | Details |
|---------|---------|
| 🔑 PW Login | Phone + OTP inside Telegram (`/pwlogin`) |
| 🎬 DRM Videos | Widevine key from PW API → mp4decrypt → ffmpeg merge |
| 🔍 PSSH Extraction | Parses MPD manifest, extracts PSSH & KID |
| 📄 PDFs | Direct download from `static.pw.live` |
| ⚡ N_m3u8DL-RE | Optional faster downloader instead of yt-dlp |
| 🎯 Quality Select | best / 1080 / 720 / 480 / 360 / worst |
| 👥 Multi-user | Owner can authorize users via `/adduser` |
| 📤 Uploads to TG | Video + progress bar, fallback to document |
| 🖼 Custom Thumb | `/setthumb URL` |

---

## 📋 Input `.txt` File Format

```
Lecture Name:https://d1d34p8vz63oiq.cloudfront.net/drm/UUID/master.mpd&parentId=XXXX&childId=YYYY
Non-DRM Name:https://d1d34p8vz63oiq.cloudfront.net/UUID/master.mpd&parentId=XXXX&childId=YYYY
Class Notes:https://static.pw.live/5eb393ee95fab7468a79d189/ADMIN/file.pdf
```

The bot auto-detects DRM vs non-DRM vs PDF based on the URL structure.

---

## 🚀 Quick Start

### 1. Clone / unzip this repo

### 2. Create `.env` from example

```bash
cp .env.example .env
nano .env    # fill in BOT_TOKEN, API_ID, API_HASH, OWNER_ID
```

### 3. Install system dependencies

```bash
# Ubuntu/Debian
sudo apt install ffmpeg

# Install mp4decrypt (from Bento4)
wget https://www.bok.net/Bento4/binaries/Bento4-SDK-1-6-0-641.x86_64-unknown-linux.zip
unzip *.zip && cp Bento4-SDK-*/bin/mp4decrypt /usr/local/bin/
```

### 4. Install Python packages

```bash
pip install -r requirements.txt
```

### 5. Run

```bash
python main.py
```

Or with Docker:

```bash
docker build -t pw-drm-bot .
docker run --env-file .env pw-drm-bot
```

---

## 🤖 Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome + buttons |
| `/pwlogin` | Login with PW phone + OTP |
| `/pwstatus` | Check if logged in |
| `/pwlogout` | Logout |
| `/setpwtoken TOKEN` | Manually set PW Bearer token |
| `/dl URL` | Download a single URL |
| `/batch` | Paste multiple links |
| `/setthumb URL` | Set thumbnail for uploads |
| `/adduser ID` | Authorize a user (owner only) |
| `/remuser ID` | Remove user |
| `/users` | List authorized users |
| `/restart` | Restart bot (owner only) |
| `/help` | Full help |

---

## 🔑 DRM Key Flow

```
1. User sends .txt file
2. Bot parses Name:URL&parentId=XXX&childId=YYY
3. For DRM videos:
   a. POST /api/v1/payment/account/drm/licence  →  KID:KEY
   b. If that fails → try penpencil API endpoints
   c. If all fail → fetch MPD, extract PSSH + KID for manual key input
4. yt-dlp --allow-unplayable-formats → encrypted video + audio files
5. mp4decrypt --key KID:KEY → decrypted tracks
6. ffmpeg → merged final .mp4
7. Upload to Telegram with progress bar
```

---

## 🔧 Optional: N_m3u8DL-RE

[N_m3u8DL-RE](https://github.com/nilaoda/N_m3u8DL-RE) is a faster alternative to the yt-dlp + mp4decrypt pipeline.

```bash
# Download the binary for your OS from:
# https://github.com/nilaoda/N_m3u8DL-RE/releases

# Set path in .env:
N_M3U8DL_RE_PATH=/path/to/N_m3u8DL-RE
```

---

## 📦 Requirements

- Python 3.11+
- `ffmpeg` (system)
- `mp4decrypt` from [Bento4](https://www.bento4.com/)
- `yt-dlp` (pip)
- Telegram API credentials from [my.telegram.org](https://my.telegram.org)
- A PW account (for DRM keys)

---

## ⚠️ Notes

- This is for **personal/educational** use only.
- You must own the PW subscription for the content you download.
- mp4decrypt must be in `PATH` or set `MP4DECRYPT_PATH` in `.env`.

---

## 🚀 Render Web Service Deploy (Band Nahi Hoga)

### Step 1 — Render pe Web Service banao (Worker nahi!)
1. [render.com](https://render.com) → **New → Web Service**
2. GitHub repo connect karo
3. Settings:
   - **Runtime:** Python 3
   - **Build Command:** (render.yaml se auto-fill ho jaata hai)
   - **Start Command:** `python main.py`

### Step 2 — Environment Variables set karo

| Variable    | Value                        | Zaruri? |
|-------------|------------------------------|---------|
| BOT_TOKEN   | bot ka token                 | ✅      |
| API_ID      | Telegram API ID              | ✅      |
| API_HASH    | Telegram API Hash            | ✅      |
| OWNER_ID    | **tumhara Telegram user ID** | ✅      |
| MONGO_URI   | MongoDB Atlas connection URL | ✅      |
| RENDER_URL  | https://YOUR-BOT.onrender.com| ✅      |
| AUTH_USERS  | extra IDs comma-separated    | ❌      |

> ⚠️ **OWNER_ID** must hona chahiye — bina iske bot kisi ko reply nahi karega!
> Tumhara Telegram ID: [@userinfobot](https://t.me/userinfobot) pe /start bhejo

### Step 3 — RENDER_URL set karo (important!)
- Deploy ke baad tumhare service ka URL milega, jaise: `https://pw-drm-bot-xxxx.onrender.com`
- Woh URL ko `RENDER_URL` env var mein set karo
- Isse bot khud ko ping karta rahega aur band nahi hoga

### Bot kaam kar raha hai ya nahi?
- Render logs mein dekho: `Bot @YourBot started! OWNER_ID=XXXXXXX`
- Agar OWNER_ID=0 dikh raha hai → OWNER_ID env var set nahi hua
