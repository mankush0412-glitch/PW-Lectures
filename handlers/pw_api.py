#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Physics Wallah API Handler
# - Per-user token via SessionManager
# - Auto token refresh on 401
# - DRM key retry (3 attempts, multiple endpoints)
# - Fresh MPD URL fetch (re-fetch if expired/403)
# - PSSH extraction from MPD XML

import re
import time
import base64
import struct
import asyncio
import aiohttp
import xml.etree.ElementTree as ET
from main import LOGGER, Config
from handlers.session import SessionManager


PW_BASE      = "https://api.pw.live"
PENPENCIL    = "https://api.penpencil.co"
WV_SYSTEM_ID = "edef8ba979d64acea3c827dcd51d21ed"

_BASE_HEADERS = {
    "User-Agent":     (
        "Mozilla/5.0 (Linux; Android 12; 2201116PI Build/SKQ1.220303.001; wv) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/107.0.5304.105 "
        "Mobile Safari/537.36"
    ),
    "Content-Type":   "application/json",
    "Accept":         "application/json",
    "Origin":         "https://www.pw.live",
    "Referer":        "https://www.pw.live/",
    "client-id":      "5eb393ee95fab7468a79d189",
    "client-version": "43",
    "region":         "IN",
}

# Max retries for DRM key
KEY_RETRIES    = 3
KEY_RETRY_WAIT = 2   # seconds between retries


class PWAPI:

    # ── Headers helper ────────────────────────────────────────────────────

    @staticmethod
    def _headers(token: str = "") -> dict:
        h = dict(_BASE_HEADERS)
        if token:
            # PW API accepts both plain and Bearer prefix — always send Bearer
            tok = token if token.startswith("Bearer ") else f"Bearer {token}"
            h["Authorization"] = tok
        return h

    # ── Auth ─────────────────────────────────────────────────────────────

    @staticmethod
    async def send_otp(mobile: str) -> dict:
        url     = f"{PW_BASE}/api/v1/auth/login"
        payload = {
            "phoneNumber": re.sub(r"\D", "", mobile)[-10:],
            "countryCode": "+91",
            "region":      "IN",
        }
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(url, json=payload, headers=_BASE_HEADERS,
                                  timeout=aiohttp.ClientTimeout(total=20)) as r:
                    LOGGER.info(f"OTP send → {r.status}")
                    return await r.json(content_type=None)
        except Exception as e:
            LOGGER.error(f"OTP send error: {e}")
            return {"error": str(e)}

    @staticmethod
    async def verify_otp(uid: int, mobile: str, otp: str) -> dict:
        url     = f"{PW_BASE}/api/v1/auth/verify"
        payload = {
            "phoneNumber": re.sub(r"\D", "", mobile)[-10:],
            "countryCode": "+91",
            "otp":         otp,
            "region":      "IN",
            "type":        "PHONE_OTP",
            "deviceType":  "WEB",
        }
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(url, json=payload, headers=_BASE_HEADERS,
                                  timeout=aiohttp.ClientTimeout(total=20)) as r:
                    data = await r.json(content_type=None)
                    if r.status == 200:
                        token   = data.get("data", {}).get("token", "")
                        refresh = data.get("data", {}).get("refreshToken", "")
                        if token:
                            await SessionManager.save(uid, token, refresh, re.sub(r"\D","",mobile)[-10:])
                            LOGGER.info(f"Login OK → uid={uid}")
                    return data
        except Exception as e:
            LOGGER.error(f"OTP verify error: {e}")
            return {"error": str(e)}

    @staticmethod
    async def refresh_token(uid: int) -> bool:
        sess = await SessionManager.get(uid)
        if not sess or not sess.get("refresh_token"):
            return False
        url = f"{PW_BASE}/api/v1/auth/refresh"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    url,
                    json={"refreshToken": sess["refresh_token"]},
                    headers=PWAPI._headers(sess["token"]),
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as r:
                    if r.status == 200:
                        data      = await r.json(content_type=None)
                        new_token = data.get("data", {}).get("token", "")
                        if new_token:
                            await SessionManager.save(
                                uid, new_token,
                                sess["refresh_token"],
                                sess.get("mobile", "")
                            )
                            LOGGER.info(f"Token refreshed → uid={uid}")
                            return True
        except Exception as e:
            LOGGER.error(f"Token refresh error: {e}")
        return False

    @staticmethod
    async def get_profile(uid: int) -> dict:
        sess = await SessionManager.get(uid)
        if not sess:
            return {}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    f"{PW_BASE}/api/v1/auth/user/profile",
                    headers=PWAPI._headers(sess["token"]),
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as r:
                    if r.status == 200:
                        return (await r.json(content_type=None)).get("data", {})
                    if r.status == 401:
                        refreshed = await PWAPI.refresh_token(uid)
                        if refreshed:
                            return await PWAPI.get_profile(uid)
        except Exception as e:
            LOGGER.error(f"Profile fetch error: {e}")
        return {}

    # ── DRM Key (with retries + multiple endpoints) ───────────────────────

    @staticmethod
    async def get_drm_key(uid: int, child_id: str, parent_id: str = "") -> str:
        """
        Try 3 times on all endpoints. Returns 'KID:KEY' or '' on failure.
        Stops immediately if key is invalid/null.
        """
        sess = await SessionManager.get(uid)
        if not sess or not sess.get("token"):
            LOGGER.warning(f"No session for uid={uid} — cannot fetch DRM key")
            return ""

        for attempt in range(1, KEY_RETRIES + 1):
            LOGGER.info(f"DRM key attempt {attempt}/{KEY_RETRIES} for {child_id}")

            # Refresh session object each retry (token may have changed)
            sess = await SessionManager.get(uid)
            token = sess.get("token", "")

            # Endpoint 1: v2-style batches content endpoint (most reliable)
            if parent_id:
                key = await PWAPI._key_batches(token, child_id, parent_id)
                if key and PWAPI._valid_key(key):
                    return key

            # Endpoint 2: Primary DRM licence endpoint
            key = await PWAPI._key_primary(token, uid, child_id, parent_id)
            if key and PWAPI._valid_key(key):
                return key

            # Endpoint 3: Penpencil drm-key endpoints
            key = await PWAPI._key_penpencil(token, child_id, parent_id)
            if key and PWAPI._valid_key(key):
                return key

            if attempt < KEY_RETRIES:
                LOGGER.warning(f"Key not found (attempt {attempt}), retrying in {KEY_RETRY_WAIT}s...")
                await asyncio.sleep(KEY_RETRY_WAIT)

        LOGGER.error(f"All DRM key attempts failed for {child_id}")
        return ""

    @staticmethod
    async def _key_primary(token: str, uid: int, child_id: str, parent_id: str) -> str:
        url     = f"{PW_BASE}/api/v1/payment/account/drm/licence"
        payload = {
            "videoId":   child_id,
            "videoType": "BATCH",
            "type":      "encryptedVideo",
        }
        if parent_id:
            payload["batchId"] = parent_id

        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    url, json=payload,
                    headers=PWAPI._headers(token),
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as r:
                    LOGGER.debug(f"Primary key endpoint → {r.status}")
                    if r.status == 401:
                        if await PWAPI.refresh_token(uid):
                            sess  = await SessionManager.get(uid)
                            token = sess.get("token", "")
                        # retry in outer loop
                        return ""
                    if r.status == 200:
                        return PWAPI._parse_key(await r.json(content_type=None))
        except Exception as e:
            LOGGER.debug(f"Primary key error: {e}")
        return ""

    @staticmethod
    async def _key_batches(token: str, child_id: str, parent_id: str) -> str:
        """
        v2-style batches endpoint — returns drmKeys array.
        Most reliable endpoint for PW batch content.
        """
        endpoints = [
            f"{PENPENCIL}/v3/batches/{parent_id}/contents/{child_id}/video-datas?mode=0",
            f"{PENPENCIL}/v3/batches/{parent_id}/contents/{child_id}?mode=0",
            f"{PW_BASE}/v3/batches/{parent_id}/contents/{child_id}/video-datas?mode=0",
        ]
        for url in endpoints:
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(
                        url,
                        headers=PWAPI._headers(token),
                        timeout=aiohttp.ClientTimeout(total=20),
                    ) as r:
                        LOGGER.info(f"Batches key endpoint → {r.status}: {url[:80]}")
                        if r.status != 200:
                            continue
                        data  = await r.json(content_type=None)
                        inner = data.get("data") or {}
                        # drmKeys array
                        keys = inner.get("drmKeys") or inner.get("encryptedKeys") or []
                        if isinstance(keys, list) and keys:
                            k = str(keys[0]).strip()
                            LOGGER.info(f"Got key from batches endpoint: {k[:30]}...")
                            return k
                        # videoUrl list may contain drmKey
                        vu = inner.get("videoUrl") or []
                        if isinstance(vu, list):
                            for v in vu:
                                if isinstance(v, dict) and v.get("drmKey"):
                                    return str(v["drmKey"])
                        # Generic parse
                        k = PWAPI._parse_key(data)
                        if k:
                            return k
            except Exception as e:
                LOGGER.debug(f"Batches key error {url}: {e}")
        return ""

    @staticmethod
    async def _key_penpencil(token: str, child_id: str, parent_id: str) -> str:
        urls = [
            f"{PENPENCIL}/v3/videos/{child_id}/drm-key",
            f"{PENPENCIL}/v2/videos/{child_id}/drm-key",
        ]
        params = {}
        if parent_id:
            params["batchId"] = parent_id

        for url in urls:
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(
                        url,
                        headers=PWAPI._headers(token),
                        params=params,
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as r:
                        LOGGER.debug(f"Penpencil key endpoint → {r.status}: {url}")
                        if r.status == 200:
                            key = PWAPI._parse_key(await r.json(content_type=None))
                            if key:
                                return key
            except Exception as e:
                LOGGER.debug(f"Penpencil key error {url}: {e}")
        return ""

    @staticmethod
    def _parse_key(data: dict) -> str:
        """Extract KID:KEY string from any known PW response format."""
        if not isinstance(data, dict):
            return ""
        d = data.get("data") if "data" in data else data

        for field in ["key", "licenceKey", "drm_key", "drmKey"]:
            if isinstance(d, dict) and d.get(field):
                return str(d[field])

        if isinstance(d, dict):
            lic = d.get("licence") or d.get("license") or {}
            if isinstance(lic, dict):
                kid = lic.get("kid", "")
                key = lic.get("key", "")
                if kid and key:
                    return f"{kid}:{key}"
                if key:
                    return key

            # ClearKey JSON format
            keys = d.get("keys", [])
            if isinstance(keys, list) and keys:
                parts = []
                for k in keys:
                    try:
                        kid_hex = base64.urlsafe_b64decode(k["kid"] + "==").hex()
                        key_hex = base64.urlsafe_b64decode(k["k"]   + "==").hex()
                        parts.append(f"{kid_hex}:{key_hex}")
                    except Exception:
                        pass
                if parts:
                    return ";".join(parts)
        return ""

    @staticmethod
    def _valid_key(key: str) -> bool:
        """Reject empty, null, placeholder keys."""
        if not key:
            return False
        if key.lower() in ("null", "none", "undefined", ""):
            return False
        # A valid KID:KEY pair has hex chars and colon
        if ":" in key:
            parts = key.split(";")[0].split(":")
            if len(parts) >= 2 and len(parts[0]) >= 16 and len(parts[1]) >= 16:
                return True
        # Bare key (32+ hex chars)
        bare = key.split(";")[0].replace(":", "")
        return len(bare) >= 32 and all(c in "0123456789abcdefABCDEF" for c in bare)

    # ── MPD Handling ──────────────────────────────────────────────────────

    @staticmethod
    async def get_fresh_mpd_url(uid: int, child_id: str, parent_id: str,
                                fallback_url: str = "") -> str:
        """
        Try to get a fresh signed MPD URL via PW API.
        Falls back to the original URL if API doesn't return one.
        """
        sess = await SessionManager.get(uid)
        if not sess:
            return fallback_url

        token = sess.get("token", "")
        endpoints = [
            f"{PW_BASE}/api/v1/payment/account/content/{child_id}/video-otp",
            f"{PENPENCIL}/v3/videos/{child_id}/otp",
            f"{PENPENCIL}/v2/videos/{child_id}/otp",
        ]
        params = {}
        if parent_id:
            params["batchId"] = parent_id

        for url in endpoints:
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(
                        url,
                        headers=PWAPI._headers(token),
                        params=params,
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as r:
                        if r.status == 200:
                            data = await r.json(content_type=None)
                            d    = data.get("data", data)
                            # Various response shapes
                            for field in ["playbackInfo", "otp", "url", "mpd_url", "videoUrl"]:
                                if isinstance(d, dict) and d.get(field):
                                    val = d[field]
                                    if isinstance(val, str) and (".mpd" in val or val.startswith("http")):
                                        LOGGER.info(f"Fresh MPD from API for {child_id}")
                                        return val
            except Exception as e:
                LOGGER.debug(f"Fresh MPD error {url}: {e}")

        LOGGER.debug(f"Using original URL for {child_id}")
        return fallback_url

    @staticmethod
    async def fetch_mpd_info(mpd_url: str) -> dict:
        """
        Download MPD and extract PSSH + KID.
        Returns: {pssh, kid, is_drm, ok, status_code}
        """
        result = {"pssh": "", "kid": "", "is_drm": False, "ok": False, "status_code": 0}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    mpd_url,
                    headers={"User-Agent": _BASE_HEADERS["User-Agent"],
                             "Referer": "https://www.pw.live/"},
                    timeout=aiohttp.ClientTimeout(total=30),
                    allow_redirects=True,
                ) as r:
                    result["status_code"] = r.status
                    if r.status != 200:
                        LOGGER.warning(f"MPD fetch → {r.status}: {mpd_url[:80]}")
                        return result
                    xml_text = await r.text(errors="replace")
        except Exception as e:
            LOGGER.error(f"MPD fetch error: {e}")
            return result

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            LOGGER.error(f"MPD XML parse error: {e}")
            return result

        pssh = ""
        kid  = ""
        for elem in root.iter():
            tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if tag != "ContentProtection":
                continue
            scheme = (elem.get("schemeIdUri") or "").lower().replace("-", "")
            for attr, val in elem.attrib.items():
                if "default_kid" in attr.lower() and val:
                    kid = val.replace("-", "").lower()
            if WV_SYSTEM_ID.replace("-","") in scheme or "widevine" in scheme:
                result["is_drm"] = True
                for child in elem:
                    ctag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                    if ctag.lower() == "pssh":
                        pssh = (child.text or "").strip()
                        break

        if result["is_drm"] and not pssh and kid:
            pssh = PWAPI._build_pssh(kid)

        result["pssh"] = pssh
        result["kid"]  = kid
        result["ok"]   = True
        return result

    @staticmethod
    def _build_pssh(kid_hex: str) -> str:
        try:
            kid_bytes = bytes.fromhex(kid_hex)
            wv_data   = b"\x12\x10" + kid_bytes
            sys_id    = bytes.fromhex(WV_SYSTEM_ID)
            box_inner = b"\x00\x00\x00\x00" + sys_id + struct.pack(">I", len(wv_data)) + wv_data
            box       = struct.pack(">I", 8 + len(box_inner)) + b"pssh" + box_inner
            return base64.b64encode(box).decode()
        except Exception:
            return ""

    # ── URL Parser ────────────────────────────────────────────────────────

    @staticmethod
    def parse_url(raw: str) -> dict:
        raw = raw.strip()
        r   = {"mpd_url": "", "parent_id": "", "child_id": "",
               "is_drm": False, "is_pdf": False}

        # PDF
        if raw.lower().endswith(".pdf") or "static.pw.live" in raw:
            r["is_pdf"]  = True
            r["mpd_url"] = raw.split("&")[0].split("?")[0]
            return r

        # Extract parentId / childId from anywhere in URL
        m_parent = re.search(r"[&?]?parentId=([a-f0-9]+)", raw, re.I)
        m_child  = re.search(r"[&?]?childId=([a-f0-9]+)",  raw, re.I)
        if m_parent:
            r["parent_id"] = m_parent.group(1)
        if m_child:
            r["child_id"] = m_child.group(1)

        # Strip params to get bare MPD URL
        mpd = raw
        for sep in ["&parentId", "?parentId", "&childId", "?childId"]:
            idx = mpd.find(sep)
            if idx != -1:
                mpd = mpd[:idx]
        r["mpd_url"] = mpd

        if "/drm/" in mpd or "drm" in mpd.lower():
            r["is_drm"] = True

        return r
