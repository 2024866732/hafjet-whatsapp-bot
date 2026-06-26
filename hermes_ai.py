"""
hermes_ai.py — Hermes AI Integration Module (Azure-Safe)
Menghubungkan WhatsApp chatbot dengan AI provider via HTTP API.

Priority:
  1. OpenRouter API (Azure & local) — primary AI source
  2. Hermes CLI (local dev only) — fallback kalau OpenRouter fail
  3. Default response — final fallback
"""

import os
import json
import logging
import asyncio
import httpx
from typing import Optional

log = logging.getLogger("hafjet-whatsapp.ai")

# ── AI Provider Config ──────────────────────────────────────────────
# OpenRouter — free tier cukup untuk production
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openrouter/owl-alpha")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_TIMEOUT = int(os.getenv("AI_TIMEOUT", "30"))

# Hermes CLI — untuk local development je
HERMES_CLI = "hermes"
HERMES_TIMEOUT = 30

# ── SOUL Context untuk AI ──────────────────────────────────────────
SOUL_CONTEXT = """\
You are the HAFJET WhatsApp assistant, an automated customer service bot for HAFJET (M) SDN BHD, \
a mobile phone repair shop in Malaysia. You help customers with:

1. Repair pricing inquiries (iPhone, Android, screen, battery, charging port, water damage, motherboard)
2. Job status tracking (customers send job numbers like JOB-2026-XXX)
3. Store location and operating hours
4. General customer service

Guidelines:
- Reply in casual Bahasa Malaysia with friendly tone
- Keep responses concise (WhatsApp style, not too long)
- Use emojis sparingly but appropriately
- If you don't know something, direct them to call the shop
- Operating hours: Mon-Sat 9AM-7PM, Sun 10AM-5PM
- Shop phone: +60 11-4956 1698

When asked about repair prices, give estimates:
- iPhone Screen: RM180-350
- iPhone Battery: RM120-200
- Android Screen: RM150-400
- Charging Port: RM80-150
- Water Damage: RM100-250
- Motherboard: RM200-500

Always end with a helpful next step or question. \
Reply in Bahasa Malaysia (casual/informal). Keep it short, 2-4 sentences max.\
"""


async def ask_hermes(user_message: str, sender_name: str) -> Optional[str]:
    """
    Generate AI reply. Tries methods in order:
    1. OpenRouter HTTP API (works in both Azure and local)
    2. Hermes CLI (local dev only)
    3. None (triggers default fallback in caller)
    """
    prompt = f"{SOUL_CONTEXT}\n\nCustomer ({sender_name}) said: {user_message}\n\nReply in casual Malay:"

    # ── Method 1: OpenRouter HTTP API (primary — Azure-safe) ──────
    if OPENROUTER_API_KEY:
        reply = await _ask_openrouter(prompt)
        if reply:
            return reply
        log.warning("⚠ OpenRouter returned empty, trying fallback")
    else:
        log.info("ℹ OPENROUTER_API_KEY not set, skipping OpenRouter")

    # ── Method 2: Hermes CLI (local dev only) ────────────────────
    reply = await _ask_hermes_cli(prompt)
    if reply:
        return reply

    # ── Return None — caller will use default fallback ────────────
    log.warning("⚠ All AI methods failed, returning None for default fallback")
    return None


async def _ask_openrouter(prompt: str) -> Optional[str]:
    """Call OpenRouter Chat Completions API."""
    url = f"{OPENROUTER_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": os.getenv("APP_REFERER", "https://hafjet.com"),
        "X-Title": "HAFJET WhatsApp Bot",
    }
    payload = {
        "model": OPENROUTER_MODEL,
        "messages": [
            {"role": "system", "content": (
                "You are HAFJET WhatsApp bot for phone repair shop. "
                "Reply in casual Bahasa Malaysia ONLY, 2-3 sentences max, under 300 characters. "
                "Keep it short and direct. No extra commentary."
            )},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 150,  # ── Batas reply pendek (2-3 sentences)
        "temperature": 0.3, # ── Lower temp = lebih consistent, tak vary-vary
    }

    try:
        # Fail-fast: connect=5s, read=10s — jangan block webhook lama
        timeout_cfg = httpx.Timeout(
            connect=5.0,
            read=10.0,
            write=5.0,
            pool=5.0,
        )
        async with httpx.AsyncClient(timeout=timeout_cfg) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                reply = data["choices"][0]["message"]["content"].strip()
                if reply:
                    log.info(f"🤖 OpenRouter reply: {reply[:100]}...")
                    return reply
                else:
                    log.warning("⚠ OpenRouter returned empty content")
            elif resp.status_code == 429:
                log.warning("⚠ OpenRouter rate limited (429) — try again later")
            elif resp.status_code == 401:
                log.error("⚠ OpenRouter auth failed (401) — check API key")
            elif resp.status_code == 503:
                log.warning("⚠ OpenRouter service unavailable (503) — try again later")
            else:
                log.error(f"⚠ OpenRouter API error: {resp.status_code} — {resp.text[:200]}")
    except httpx.TimeoutException:
        log.error("⏱ OpenRouter API timeout (fail-fast triggered)")
    except Exception as e:
        log.error(f"❌ OpenRouter API error: {e}")

    return None


async def _ask_hermes_cli(prompt: str) -> Optional[str]:
    """Fallback: Call Hermes CLI (local development only)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            HERMES_CLI, "ask", "--no-stream", prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=HERMES_TIMEOUT
        )
        if proc.returncode == 0 and stdout.decode().strip():
            reply = stdout.decode().strip()
            log.info(f"🤖 Hermes CLI reply: {reply[:100]}...")
            return reply
        else:
            log.warning(f"⚠ Hermes CLI returned code={proc.returncode}")
    except FileNotFoundError:
        log.info("ℹ Hermes CLI not found (expected in Azure)")
    except asyncio.TimeoutError:
        log.warning("⏱ Hermes CLI timeout")
    except Exception as e:
        log.error(f"❌ Hermes CLI error: {e}")

    return None


def should_use_ai(message: str) -> bool:
    """
    Tentukan sama ada mesej perlu dihantar ke AI atau boleh handle statik.
    Returns True jika perlu AI, False jika boleh handle statik.
    """
    msg_lower = message.lower().strip()

    # ── Static patterns (handle locally, tak perlu AI) ──────────
    static_triggers = {
        "1", "2", "3", "4",
        "1️⃣", "2️⃣", "3️⃣", "4️⃣",
        "menu", "main", "balik", "kembali",
        "/help", "help", "bantu",
        "semak status", "status job",
        "semak harga",
        "hubungi", "staff",
        "lokasi", "waktu operasi",
    }

    if msg_lower in static_triggers:
        return False

    if msg_lower.startswith("job-") or msg_lower.startswith("receipt-"):
        return False

    # Extended greeting detection — sama dengan _is_greeting() di webhook_listener
    greetings_exact = [
        "hi", "hello", "hey", "halo", "hai", "helo", "hallo",
        "selamat pagi", "selamat petang", "selamat malam", "selamat tengahari",
        "assalamualaikum", "waalaikumsalam", "assalam", "salam",
        "apa khabar", "apa kabar", "howdy", "yo", "oi",
    ]
    greetings_startswith = [
        "hi ", "hello ", "hey ", "halo ", "hai ", "selamat ",
        "assalam", "waalaikum", "good morning", "good evening",
        "apa khabar", "apa kabar",
    ]
    if msg_lower in greetings_exact:
        return False
    if msg_lower.startswith(tuple(greetings_startswith)):
        return False

    return True
