"""
HAFJET WhatsApp Chatbot — Webhook Listener (v2.0)
Hybrid AI + Static Menu + Repair Job Tracking

Menerima mesej dari WhatsApp Cloud API via webhook.
- Menu statik (1,2,3,4) → handle locally
- Job ID (JOB-XXXX-XXX) → check_repair_status()
- Pertanyaan lain → Hermes Agent Core (AI)
"""

import asyncio
import os
import sys
import json
import hashlib
import hmac
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse

# ── Load Config ─────────────────────────────────────────────────────
# Load .env file kalau exist (local development)
# Dalam Azure, env vars dah set oleh platform — JANGAN override
_env_path = os.path.expanduser("~/.hermes/whatsapp-bot/.env")
if os.path.exists(_env_path):
    load_dotenv(_env_path, override=False)

WHATSAPP_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
WHATSAPP_PHONE_ID = os.getenv("WHATSAPP_PHONE_ID", "")
APP_SECRET = os.getenv("APP_SECRET", "")
WEBHOOK_VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "HAFJET_RAUB_RAK")

# ── Import Modules ──────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hermes_ai import ask_hermes
from repair_db import check_repair_status, format_job_status

# ── Logging ─────────────────────────────────────────────────────────
os.makedirs(os.path.expanduser("~/.hermes/logs"), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.expanduser("~/.hermes/logs/webhook.log")),
    ],
)
log = logging.getLogger("hafjet-whatsapp")

# ── FastAPI App ─────────────────────────────────────────────────────
app = FastAPI(title="HAFJET WhatsApp Bot", version="2.0.0")

MYT = timezone(timedelta(hours=8))

# ── Anti-dedup tracking ────────────────────────────────────────────
_processed_messages: dict[str, datetime] = {}  # msg_id -> timestamp
_DEDUP_WINDOW = 300  # 5 minutes

def _is_duplicate(msg_id: str) -> bool:
    """Check sama ada mesej sudah diprocess dalam 5 minit lepas."""
    now = datetime.now(MYT)
    if msg_id in _processed_messages:
        elapsed = (now - _processed_messages[msg_id]).total_seconds()
        if elapsed < _DEDUP_WINDOW:
            log.info(f"⏭ Dedup: skip msg {msg_id} (processed {elapsed:.0f}s ago)")
            return True
    _processed_messages[msg_id] = now
    # Cleanup old entries
    expired = [k for k, v in _processed_messages.items() if (now - v).total_seconds() > _DEDUP_WINDOW]
    for k in expired:
        del _processed_messages[k]
    return False


# ═══════════════════════════════════════════════════════════════════
#  WEBHOOK ENDPOINTS
# ═══════════════════════════════════════════════════════════════════

@app.get("/webhook")
async def verify_webhook(request: Request):
    """Meta verification — return hub.challenge kalau token betul."""
    params = dict(request.query_params)
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == WEBHOOK_VERIFY_TOKEN:
        log.info("✅ Webhook verified successfully")
        return JSONResponse(content=int(challenge) if challenge.isdigit() else challenge)
    else:
        log.warning(f"❌ Verification failed: mode={mode}")
        raise HTTPException(status_code=403, detail="Verification failed")


@app.post("/webhook")
async def receive_message(request: Request):
    """Terima mesej masuk dari WhatsApp Cloud API."""
    signature = request.headers.get("X-Hub-Signature-256", "")
    body = await request.body()

    if APP_SECRET and not _verify_signature(body, signature):
        log.warning("❌ Invalid webhook signature!")
        raise HTTPException(status_code=403, detail="Invalid signature")

    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError) as e:
        log.error(f"❌ Invalid JSON body: {e}")
        return JSONResponse(content={"status": "error", "detail": "invalid json"})
    log.info(f"📩 Webhook received: {json.dumps(data, indent=2)[:500]}")

    try:
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for msg in value.get("messages", []):
                    await _process_message(msg, value)
    except Exception as e:
        log.error(f"❌ Error processing webhook: {e}", exc_info=True)

    return JSONResponse(content={"status": "ok"})


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "HAFJET WhatsApp Bot v2.0",
        "timestamp": datetime.now(MYT).isoformat(),
        "configured": bool(WHATSAPP_TOKEN and WHATSAPP_PHONE_ID),
        "features": ["hybrid_ai", "repair_tracking", "static_menu"],
    }


# ═══════════════════════════════════════════════════════════════════
#  SIGNATURE VERIFICATION
# ═══════════════════════════════════════════════════════════════════

def _verify_signature(payload: bytes, signature: str) -> bool:
    """Verify HMAC-SHA256 signature dari Meta."""
    if not APP_SECRET:
        log.error("❌ APP_SECRET not configured — signature verification disabled!")
        return False
    if not signature:
        return False
    expected = hmac.new(APP_SECRET.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)


# ═══════════════════════════════════════════════════════════════════
#  MESSAGE PROCESSOR
# ═══════════════════════════════════════════════════════════════════

async def _process_message(msg: dict, value: dict):
    """Process satu mesej masuk."""
    msg_type = msg.get("type", "")
    from_number = msg.get("from", "")
    msg_id = msg.get("id", "")

    # ── Anti-dedup ──────────────────────────────────────────────
    if _is_duplicate(msg_id):
        return

    contacts = value.get("contacts", [])
    sender_name = contacts[0].get("profile", {}).get("name", from_number) if contacts else from_number

    log.info(f"📨 From {sender_name} ({from_number}): type={msg_type}")

    # Extract message content
    user_message = _extract_message(msg, msg_type)
    if not user_message:
        return

    log.info(f"💬 User said: '{user_message}'")

    # ── Route & Generate Reply ───────────────────────────────────
    reply_text = await generate_reply(user_message, sender_name, from_number)

    if reply_text:
        await send_whatsapp_message(from_number, reply_text)


def _extract_message(msg: dict, msg_type: str) -> str:
    """Extract teks mesej dari pelbagai jenis message."""
    if msg_type == "text":
        return msg.get("text", {}).get("body", "")
    elif msg_type == "interactive":
        interactive = msg.get("interactive", {})
        if interactive.get("type") == "button_reply":
            return interactive.get("button_reply", {}).get("id", "")
        elif interactive.get("type") == "list_reply":
            return interactive.get("list_reply", {}).get("id", "")
    elif msg_type == "button":
        return msg.get("button", {}).get("text", "")
    return ""


# ═══════════════════════════════════════════════════════════════════
#  CANONICAL REPLY CONSTANTS
# ═══════════════════════════════════════════════════════════════════

CANONICAL_GREETING = (
    "Waalaikumsalam! 👋 Selamat datang ke *HAFJET* — kedai repair phone kami.\n\n"
    "Macam mana kami boleh bantu awak hari ni? 😊\n\n"
    "Tulis *1* untuk harga repair\n"
    "Tulis *2* untuk semak status job\n"
    "Tulis *3* untuk hubungi staff\n"
    "Tulis *4* untuk lokasi & waktu operasi"
)

CANONICAL_FALLBACK = (
    "Terima kasih atas mesej anda! 😊\n\n"
    "Saya tak faham soalan itu sementara. Sila pilih dari menu:\n\n"
    "1️⃣ Semak harga repair\n"
    "2️⃣ Semak status job\n"
    "3️⃣ Hubungi staff\n"
    "4️⃣ Lokasi & waktu operasi\n\n"
    "Atau hubungi kami terus di WhatsApp ni."
)

# ═══════════════════════════════════════════════════════════════════
#  HYBRID REPLY GENERATOR (v2.0 — Anti-duplicate)
# ═══════════════════════════════════════════════════════════════════

async def generate_reply(message: str, sender_name: str, sender_number: str) -> str:
    """
    Hybrid Model Router:
    1. Job ID pattern → check_repair_status()
    2. Static menu (1,2,3,4, greetings) → handle locally ONLY
    3. Everything else → Hermes Agent Core (AI)
    RULE: One inbound = ONE outbound. No double-send.
    """
    msg_lower = message.lower().strip()

    # ── STEP 1: Check Job ID ─────────────────────────────────────
    if _is_job_id(message):
        job_id = message.strip().upper().replace(" ", "")
        log.info(f"🔍 Checking repair status for: {job_id}")
        loop = asyncio.get_event_loop()
        job = await loop.run_in_executor(None, check_repair_status, job_id)
        if job:
            return format_job_status(job)
        else:
            return (
                f"❌ *Job tidak dijumpai: {job_id}*\n\n"
                f"Sila semak semula No. Job anda.\n"
                f"Format: JOB-2026-XXX\n\n"
                f"Au boleh hubungi kami terus di WhatsApp ni."
            )

    # ── STEP 2: Greeting → CANONICAL ONLY, NO AI fallback ──────
    if _is_greeting(msg_lower):
        log.info("👋 Greeting detected — using canonical reply")
        return CANONICAL_GREETING

    # ── STEP 3: Static Menu (fast response) ──────────────────────
    reply = _static_menu_handler(msg_lower, sender_name, message)
    if reply:
        return reply

    # ── STEP 4: AI-Powered (Hermes Agent Core) ───────────────────
    log.info(f"🤖 Routing to Hermes AI: '{message[:50]}'")
    ai_reply = await ask_hermes(message, sender_name)
    if ai_reply and not _is_too_long(ai_reply):
        return ai_reply

    # ── Fallback (satu reply je) ────────────────────────────────
    log.warning("⚠ AI fallback — using default response")
    return CANONICAL_FALLBACK


# ═══════════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════════

def _is_greeting(msg_lower: str) -> bool:
    """Check sama ada mesej adalah greeting/sapaan."""
    greetings_exact = [
        "hi", "hello", "hey", "halo", "hai", "helo", "hallo",
        "selamat pagi", "selamat petang", "selamat malam", "selamat tengahari",
        "assalamualaikum", "waalaikumsalam", "assalam", "salam",
        "apa khabar", "apa kabar", "howdy", "yo", "oi",
    ]
    greetings_startswith = [
        "hi ", "hello ", "hey ", "halo ", "hai ", "selamat ",
        "assalam", "waalaikum", "good morning", "good evening",
        "apa khabar", "apa kabar", "aw", " awake",
    ]
    if msg_lower in greetings_exact:
        return True
    if msg_lower.startswith(tuple(greetings_startswith)):
        return True
    return False


def _is_too_long(text: str, max_len: int = 500) -> bool:
    """Check kalau reply terlalu panjang untuk WhatsApp."""
    return len(text) > max_len


def _is_job_id(message: str) -> bool:
    """Check sama ada mesej adalah Job ID."""
    cleaned = message.strip().upper().replace(" ", "")
    # Pattern: JOB-XXXX-XXX
    if cleaned.startswith("JOB-") and len(cleaned) >= 8:
        return True
    # Pattern: RECEIPT-XXXX-XXX
    if cleaned.startswith("RECEIPT-") and len(cleaned) >= 12:
        return True
    # Pattern: numeric only (short job number) — only if 3+ digits
    if cleaned.isdigit() and 3 <= len(cleaned) <= 5:
        return True
    return False


def _static_menu_handler(msg_lower: str, sender_name: str, original_msg: str) -> Optional[str]:
    """
    Handle menu statik yang tak perlu AI.
    Returns reply string atau None kalau bukan static menu.
    """

    # ── Greeting ────────────────────────────────────────────────
    greetings = ["hi", "hello", "hey", "halo", "selamat pagi", "selamat petang", "selamat malam"]
    if msg_lower in greetings or msg_lower.startswith("hi ") or msg_lower.startswith("halo "):
        return (
            f"👋 Selamat datang ke *HAFJET*!\n\n"
            f"Saya ialah pembantu automatik HAFJET. "
            f"Apa yang boleh saya bantu hari ini?\n\n"
            f"📋 *Menu:*\n"
            f"1️⃣ Semak harga repair\n"
            f"2️⃣ Semak status job\n"
            f"3️⃣ Hubungi staff\n"
            f"4️⃣ Lokasi / Waktu operasi\n\n"
            f"Tulis *1*, *2*, *3* atau *4* untuk pilih.\n"
            f"Au terus tanya apa-apa — saya akan cuba bantu! 😊"
        )

    # ── Menu 1: Semak Harga Repair ──────────────────────────────
    if msg_lower in ["1", "1️⃣", "harga", "semak harga"]:
        return (
            "🔧 *Harga Repair HAFJET*\n\n"
            "Berikut adalah harga purata:\n\n"
            "• iPhone Screen Replacement — RM180-350\n"
            "• iPhone Battery Replacement — RM120-200\n"
            "• Android Screen Repair — RM150-400\n"
            "• Charging Port Repair — RM80-150\n"
            "• Water Damage Treatment — RM100-250\n"
            "• Motherboard Repair — RM200-500\n\n"
            "⚠ Harga bergantung pada model dan kerosakan.\n"
            "Untuk harga tepat, sila hantar telefon ke kedai kami.\n\n"
            "Tulis *menu* untuk kembali ke menu utama."
        )

    # ── Menu 2: Semak Status Job ────────────────────────────────
    if msg_lower in ["2", "2️⃣", "semak status", "status job"]:
        return (
            "📋 *Semak Status Job*\n\n"
            "Sila masukkan *No. Job* anda.\n"
            "Contoh: *JOB-2026-001*\n\n"
            "Atau hubungi kami di:\n"
            "📞 [Nombor HAFJET]\n\n"
            "Tulis *menu* untuk kembali ke menu utama."
        )

    # ── Menu 3: Hubungi Staff ───────────────────────────────────
    if msg_lower in ["3", "3️⃣", "staff", "hubungi"]:
        return (
            "📞 *Hubungi HAFJET*\n\n"
            "Waktu Operasi:\n"
            "Isnin – Sabtu: 9:00 AM – 7:00 PM\n"
            "Ahad: 10:00 AM – 5:00 PM\n\n"
            "📍 Alamat:\n"
            "[Alamat Kedai HAFJET]\n\n"
            "📞 Telefon: [Nombor HAFJET]\n"
            "📱 WhatsApp: Mesej ini\n\n"
            "Tulis *menu* untuk kembali ke menu utama."
        )

    # ── Menu 4: Lokasi / Waktu Operasi ──────────────────────────
    if msg_lower in ["4", "4️⃣", "lokasi", "waktu operasi"]:
        return (
            "📍 *Lokasi HAFJET*\n\n"
            "[Alamat Penuh Kedai]\n\n"
            "🕐 *Waktu Operasi:*\n"
            "Isnin – Sabtu: 9:00 AM – 7:00 PM\n"
            "Ahad: 10:00 AM – 5:00 PM\n"
            "Cuti Kebangsaan: Tutup\n\n"
            "🗺 [Google Maps link]\n\n"
            "Tulis *menu* untuk kembali ke menu utama."
        )

    # ── Menu: Kembali ke menu utama ─────────────────────────────
    if msg_lower in ["menu", "main", "balik", "kembali"]:
        return (
            "📋 *Menu Utama HAFJET*\n\n"
            "1️⃣ Semak harga repair\n"
            "2️⃣ Semak status job\n"
            "3️⃣ Hubungi staff\n"
            "4️⃣ Lokasi / Waktu operasi\n\n"
            "Tulis *1*, *2*, *3* atau *4* untuk pilih.\n"
            "Au terus tanya apa-apa — saya akan cuba bantu! 😊"
        )

    # ── Help ────────────────────────────────────────────────────
    if msg_lower in ["/help", "help", "bantu"]:
        return (
            "ℹ️ *Bantuan HAFJET Bot*\n\n"
            "Saya boleh membantu anda dengan:\n"
            "• Semak harga repair — tulis *1*\n"
            "• Semak status job — tulis *2* atau hantar No. Job\n"
            "• Hubungi staff — tulis *3*\n"
            "• Lokasi & waktu operasi — tulis *4*\n\n"
            "Anda juga boleh terus tanya soalan dalam Bahasa Melayu "
            "dan saya akan cuba jawab untuk anda! 😊\n\n"
            "Tulis *menu* untuk paparan menu."
        )

    # ── Not a static menu → return None (will route to AI) ──────
    return None


# ═══════════════════════════════════════════════════════════════════
#  WHATSAPP API — SEND MESSAGE
# ═══════════════════════════════════════════════════════════════════

async def send_whatsapp_message(to_number: str, message: str):
    """Hantar balasan ke WhatsApp via Cloud API."""
    if not WHATSAPP_TOKEN or not WHATSAPP_PHONE_ID:
        log.error("❌ WhatsApp token or phone ID not configured!")
        return False

    url = f"https://graph.facebook.com/v21.0/{WHATSAPP_PHONE_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {"body": message, "preview_url": False},
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, json=payload, headers=headers)
            result = resp.json()

            if resp.status_code == 200:
                log.info(f"✅ Reply sent to {to_number}: {message[:80]}...")
                return True
            else:
                log.error(f"❌ Failed to send: {resp.status_code} — {json.dumps(result)}")
                return False
    except Exception as e:
        log.error(f"❌ Error sending message: {e}", exc_info=True)
        return False


# ═══════════════════════════════════════════════════════════════════
#  RUN
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("WEBHOOK_PORT", 8443))
    log.info(f"🚀 Starting HAFJET WhatsApp Bot v2.0 on port {port}")
    log.info(f"   Features: Hybrid AI + Repair Tracking + Static Menu")

    uvicorn.run(
        "webhook_listener:app",
        host="0.0.0.0",
        port=port,
        reload=False,
        log_level="info",
    )
