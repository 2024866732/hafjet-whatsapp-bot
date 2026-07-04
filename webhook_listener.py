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
import re as _re
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
import jwt
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException, WebSocket, Depends
from fastapi.responses import JSONResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles

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
DASHBOARD_API_KEY = os.getenv("DASHBOARD_API_KEY", "")
STAFF_JWT_SECRET = os.getenv("STAFF_JWT_SECRET", "")

# ── Import Modules ──────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hermes_ai import ask_hermes
from repair_db import check_repair_status, format_job_status
from db_logger import (
    init_db, log_inbound, log_outbound,
    register_ws, unregister_ws,
    get_recent_messages, get_customer_messages,
    get_customer_list, get_stats,
    broadcast_ws,
    resolve_customer, escalate_customer,
    update_customer_note, get_customer_detail, _get_db,
    create_blast, update_blast_status, get_blast_history, get_all_customer_phones,
    get_contacts, update_contact, export_contacts, import_contacts_csv,
    get_analytics_overview, get_analytics_chart,
    get_response_time_stats, get_agent_performance,
    get_analytics_timeseries, get_export_csv,
    get_all_keywords, create_keyword as db_create_keyword, update_keyword as db_update_keyword,
    delete_keyword as db_delete_keyword, toggle_keyword as db_toggle_keyword,
    reorder_keywords as db_reorder_keywords,
    create_staff, get_staff_by_email, get_staff_by_id, get_all_staff,
    update_staff_status, assign_conversation, get_inbox_conversations,
    update_conversation_status, get_staff_whatsapp, set_escalation_notified,
    get_customer_detail,
)

# ── APScheduler ──────────────────────────────────────────────────────
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

# ── Logging ─────────────────────────────────────────────────────────
class SecureFormatter(logging.Formatter):
    """Targeted masking: only known sensitive patterns."""
    _PATTERNS = [
        # WhatsApp access token (EAAdm4...)
        (_re.compile(r'EAAdm4[A-Za-z0-9_-]{20,}'), 'EAAdm4***'),
        # Generic bearer tokens / API keys in key=value logs
        (_re.compile(r'(?:token|key|secret|password|api_key)=([^\s&]{8,})', _re.I), r'\1=***'),
        # Phone numbers in isolation (not in URLs/paths)
        (_re.compile(r'(?<!["\w])(\+?60\d{8,11})(?!["\w])'), '***'),
    ]

    def format(self, record):
        msg = super().format(record)
        for pattern, replacement in self._PATTERNS:
            msg = pattern.sub(replacement, msg)
        return msg

os.makedirs(os.path.expanduser("~/.hermes/logs"), exist_ok=True)
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(SecureFormatter(
    fmt="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
))
logging.basicConfig(
    level=logging.INFO,
    handlers=[
        _handler,
        logging.FileHandler(os.path.expanduser("~/.hermes/logs/webhook.log")),
    ],
)
log = logging.getLogger("hafjet-whatsapp")

# ── APScheduler ──────────────────────────────────────────────────────
_scheduler = AsyncIOScheduler()

# ── Escalation keywords (from runtime config, fallback to hardcoded) ─
DEFAULT_ESCALATION_KEYWORDS = [
    "staff", "human", "agent", "orang", "manusia",
    "bantuan", "help", "tolong",
    "complaint", "aduan", "rayuan",
    "urgent", "segera", "cepat",
    "manager", "bos", "ketua",
    "tak puas", "not satisfied", "tak berpuas hati",
]

# ── Constant: escalation notification message ────────────────────────
ESCALATION_REPLY = "Anda akan dihubungi oleh staff kami tidak lama lagi. Sila tunggu."
TIMEOUT_RESUME_MSG = "Staff sedang sibuk. Boleh saya bantu sementara ini?"

# ── FastAPI App ─────────────────────────────────────────────────────
app = FastAPI(title="HAFJET WhatsApp Bot", version="2.1.0")

MYT = timezone(timedelta(hours=8))

# ── Write-Route Auth Middleware ─────────────────────────────────────
_PROTECTED_PREFIXES = [
    "/api/settings",
    "/api/customers/",
]

@app.middleware("http")
async def protect_sensitive_routes(request: Request, call_next):
    if not DASHBOARD_API_KEY:
        return JSONResponse(
            status_code=500,
            content={"error": "Server misconfigured: DASHBOARD_API_KEY not set"},
        )

    path = request.url.path
    method = request.method

    _is_protected = False
    for prefix in _PROTECTED_PREFIXES:
        if path.startswith(prefix):
            if prefix == "/api/settings":
                _is_protected = True
                break
            if method in ("POST", "PUT", "PATCH", "DELETE"):
                _is_protected = True
                break

    if _is_protected:
        provided = request.headers.get("X-API-Key", "")
        if provided != DASHBOARD_API_KEY:
            client = request.client.host if request.client else "unknown"
            log.warning(f"🔒 Auth failed: {method} {path} from {client}")
            return JSONResponse(
                status_code=401,
                content={"error": "Invalid or missing X-API-Key"},
            )

    return await call_next(request)

# ── Dashboard Static Files Mount ─────────────────────────────────────
# Serve React dashboard build dari /dashboard
# Parse: dashboard/dist/index.html, dashboard/dist/assets/*
DASHBOARD_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "dashboard", "dist"
)
if os.path.exists(DASHBOARD_PATH):
    # Mount static assets FIRST (CSS, JS, images)
    app.mount(
        "/dashboard/assets",
        StaticFiles(directory=os.path.join(DASHBOARD_PATH, "assets")),
        name="dashboard-assets",
    )
    log.info(f"📊 Dashboard mounted at {DASHBOARD_PATH}")
else:
    log.warning(f"⚠️ Dashboard dist not found at {DASHBOARD_PATH} — dashboard disabled")

# ── Anti-dedup tracking ────────────────────────────────────────────
_processed_messages: dict[str, datetime] = {}  # msg_id -> timestamp
_DEDUP_WINDOW = 300  # 5 minutes

# ── Runtime Settings Cache ─────────────────────────────────────────
# Loaded from bot_settings table, cached for TTL seconds.
# Dashboard save → invalidate immediately via _refresh_settings().
_settings_cache: dict = {}
_settings_cache_at: float = 0
_SETTINGS_TTL = 60  # seconds

def _refresh_settings():
    """Load settings from DB into cache."""
    import time
    global _settings_cache, _settings_cache_at
    try:
        conn = _get_db()
        rows = conn.execute("SELECT key, value FROM bot_settings").fetchall()
        conn.close()
        _settings_cache = {row["key"]: row["value"] for row in rows}
    except Exception:
        # Keep old cache on DB error
        pass
    _settings_cache_at = time.time()

# Runtime Settings Cache ─ Env overrides for kritikal keys
_ENV_OVERRIDE_MAP = {
    "ai_model": "OPENROUTER_MODEL",
    "ai_enabled": "AI_ENABLED",
    "bot_active": "BOT_ACTIVE",
}

def get_runtime(key: str, default: str = "") -> str:
    """Get a runtime setting (cached, with DB fallback, env overrides)."""
    import time
    env_key = _ENV_OVERRIDE_MAP.get(key)
    if env_key:
        env_val = os.getenv(env_key)
        if env_val is not None:
            return env_val
    if not _settings_cache or (time.time() - _settings_cache_at) > _SETTINGS_TTL:
        _refresh_settings()
    return _settings_cache.get(key, default)

def get_runtime_bool(key: str, default: bool = True) -> bool:
    """Get boolean runtime setting. Env > true-from-DB > default. DB cannot silently disable."""
    env_map = {
        "ai_enabled": "AI_ENABLED",
        "bot_active": "BOT_ACTIVE",
    }
    env_key = env_map.get(key)
    if env_key:
        env_val = os.getenv(env_key)
        if env_val is not None:
            return env_val.lower() in ("true", "1", "yes")
    # Only allow DB to enable, never disable; default is the safety floor
    db_val = get_runtime(key, "").lower()
    if db_val in ("true", "1", "yes"):
        return True
    return default

def get_runtime_int(key: str, default: int = 0) -> int:
    """Get integer runtime setting."""
    try:
        return int(get_runtime(key, str(default)))
    except ValueError:
        return default

def get_runtime_float(key: str, default: float = 0.0) -> float:
    """Get float runtime setting."""
    try:
        return float(get_runtime(key, str(default)))
    except ValueError:
        return default

# Initial load
_refresh_settings()

# ── Effective OpenRouter model & env mirror ─────────
_OPENROUTER_MODEL_KEY = "OPENROUTER_MODEL"
_AI_ENABLED_KEY = "AI_ENABLED"
_BOT_ACTIVE_KEY = "BOT_ACTIVE"

def _effective_model(default_fallback: str = "nvidia/nemotron-3-super-120b-a12b:free") -> str:
    env_model = os.getenv(_OPENROUTER_MODEL_KEY)
    db_model = get_runtime("ai_model", "")
    return env_model or db_model or default_fallback

# Sync effective model to env + hermes_ai module state so runtime picks it up
_effective_ai_model = _effective_model()
os.environ[_OPENROUTER_MODEL_KEY] = _effective_ai_model
if "hermes_ai" in sys.modules:
    import hermes_ai as _hermes_ai
    _hermes_ai.OPENROUTER_MODEL = _effective_ai_model

def _is_duplicate(msg_id: str) -> bool:
    """Check sama ada mesej sudah diprocess dalam dedup window."""
    now = datetime.now(MYT)
    dedup_sec = get_runtime_int("dedup_window", 300)
    if msg_id in _processed_messages:
        elapsed = (now - _processed_messages[msg_id]).total_seconds()
        if elapsed < dedup_sec:
            log.info(f"⏭ Dedup: skip msg {msg_id} (processed {elapsed:.0f}s ago, window={dedup_sec}s)")
            return True
    # Record this message ID to prevent duplicate processing
    _processed_messages[msg_id] = now
    # Cleanup expired entries
    expired = [k for k, v in _processed_messages.items() if (now - v).total_seconds() > dedup_sec]
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

    # ── Signature verification (fail closed) ─────────────────────
    if not APP_SECRET:
        log.error("❌ APP_SECRET not configured — rejecting webhook")
        raise HTTPException(status_code=500, detail="Server misconfigured")
    if not signature:
        client = request.client.host if request.client else "unknown"
        log.warning(f"❌ Missing X-Hub-Signature-256 from {client}")
        raise HTTPException(status_code=403, detail="Missing signature")
    if not _verify_signature(body, signature):
        client = request.client.host if request.client else "unknown"
        log.warning(f"❌ Invalid webhook signature from {client}")
        raise HTTPException(status_code=403, detail="Invalid signature")

    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError) as e:
        log.error(f"❌ Invalid JSON body: {e}")
        return JSONResponse(content={"status": "error", "detail": "invalid json"})

    # Log summary only (no sensitive payload data)
    _entry_count = len(data.get("entry", []))
    _msg_count = sum(
        len(c.get("value", {}).get("messages", []))
        for e in data.get("entry", [])
        for c in e.get("changes", [])
    )
    log.info(f"📩 Webhook: {_entry_count} entry, {_msg_count} msg")

    try:
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for msg in value.get("messages", []):
                    await _process_message(msg, value)
    except Exception as e:
        log.error(f"❌ Error processing webhook: {e}", exc_info=True)

    return JSONResponse(content={"status": "ok"})


@app.get("/")
async def root():
    return {"status": "ok", "service": "HAFJET WhatsApp Bot v2.0"}


@app.get("/health")
async def health():
    stats = await get_stats()
    return {
        "status": "ok",
        "service": "HAFJET WhatsApp Bot v2.0",
        "timestamp": datetime.now(MYT).isoformat(),
        "configured": bool(WHATSAPP_TOKEN and WHATSAPP_PHONE_ID),
        "features": ["hybrid_ai", "repair_tracking", "static_menu", "dashboard", "db_logging"],
        "stats": stats,
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

# ── Staff Notification ──────────────────────────────────────────────

async def _notify_staff(customer_phone: str, message_preview: str, staff_id: int = None) -> bool:
    """Send WhatsApp notification to staff about escalation.
    Falls back to admin (staff_id=1) if staff_id is None or staff has no number.
    Respects 24-hour window — if outside window, logs and sends WebSocket instead.
    """
    loop = asyncio.get_event_loop()
    if staff_id:
        target_id = staff_id
    else:
        target_id = 1  # fallback admin

    staff_number = await loop.run_in_executor(None, get_staff_whatsapp, target_id)
    if not staff_number:
        log.warning(f"⚠️ Staff #{target_id} has no whatsapp_number configured — sending WS notification instead")
        broadcast_ws("notification", {
            "type": "escalation",
            "phone": customer_phone,
            "message_preview": message_preview[:80],
            "staff_id": target_id,
        })
        return False

    # Check 24-hour window: has this staff number received inbound recently?
    # For now, always attempt send — WhatsApp Cloud API enforces the window server-side.
    # We log the attempt and continue regardless.
    msg = (
        f"🔔 New escalation from {customer_phone}:\n"
        f"\"{message_preview[:80]}\"\n"
        f"Open dashboard to respond: https://hafjet-whatsapp-bot.azurewebsites.net/dashboard"
    )
    sent = await send_whatsapp_message(staff_number, msg)
    if sent:
        log.info(f"✅ Staff #{target_id} notified about escalation from {customer_phone}")
    else:
        log.warning(f"⚠️ Staff #{target_id} notification failed — sending WS fallback")
        broadcast_ws("notification", {
            "type": "escalation",
            "phone": customer_phone,
            "message_preview": message_preview[:80],
            "staff_id": target_id,
        })
    return sent


async def _check_escalation_timeout():
    """APScheduler job: check conversations escalated > 2 hours without staff reply.
    If found: set bot_paused=0, send resume message, log.
    Runs every 5 minutes.
    """
    loop = asyncio.get_event_loop()
    try:
        # Find escalated conversations where bot_paused=1 and escalated_at > 2 hours ago
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
        conn = await loop.run_in_executor(None, _get_db)
        rows = conn.execute(
            "SELECT phone FROM customers WHERE status='escalated' AND bot_paused=1 "
            "AND escalated_at IS NOT NULL AND escalated_at < ?",
            (cutoff,)
        ).fetchall()
        conn.close()
        for row in rows:
            phone = row[0]
            # Resume bot for this conversation
            await loop.run_in_executor(None, update_conversation_status, phone, "escalated")
            # bot_paused already 0 from update_conversation_status since 'escalated' sets paused=1...
            # Actually, we want to keep status='escalated' but set bot_paused=0
            conn2 = await loop.run_in_executor(None, _get_db)
            conn2.execute("UPDATE customers SET bot_paused=0 WHERE phone=?", (phone,))
            conn2.commit()
            conn2.close()
            # Send resume message
            await send_whatsapp_message(phone, TIMEOUT_RESUME_MSG)
            log.info(f"⏰ Escalation timeout: resumed bot for {phone}")
    except Exception as e:
        log.error(f"❌ _check_escalation_timeout error: {e}", exc_info=True)

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

    # ── Log inbound message to DB ────────────────────────────────
    await log_inbound(from_number, user_message, "text", msg_id)

    # ── Check bot_paused (escalated/assigned — staff takeover) ───
    loop_db = asyncio.get_event_loop()
    cust = await loop_db.run_in_executor(None, get_customer_detail, from_number)
    if cust and cust.get("bot_paused"):
        # If customer re-opens a resolved conversation, reset to bot_active
        if cust.get("status") == "resolved":
            await loop_db.run_in_executor(None, update_conversation_status, from_number, "bot_active")
            log.info(f"🔄 Re-opened resolved conversation for {from_number}")
        else:
            # Bot is paused — skip auto-reply, just acknowledge
            log.info(f"⏸ Bot paused for {from_number} (status={cust.get('status')}) — skipping auto-reply")
            reply_text = "Pesanan anda telah diterima. Staff kami akan membalas sebentar lagi."
            _latency_ms = 0
            await log_outbound(from_number, reply_text, "bot_paused", 0, False)
            return

    # ── Escalation keyword detection (TUGAS 2) ──────────────────
    esc_kw_raw = get_runtime("escalation_keywords", "")
    if esc_kw_raw:
        kw_list = [k.strip().lower() for k in esc_kw_raw.split(",") if k.strip()]
    else:
        kw_list = DEFAULT_ESCALATION_KEYWORDS

    msg_lower = user_message.lower().strip()
    matched_kw = None
    for kw in kw_list:
        if kw and kw in msg_lower:
            matched_kw = kw
            break

    if matched_kw:
        log.info(f"🚨 Escalation keyword '{matched_kw}' detected from {from_number}")
        # Set status = escalated, bot_paused = 1
        await loop_db.run_in_executor(None, update_conversation_status, from_number, "escalated")
        # Notify staff (or admin fallback)
        assigned_staff_id = cust.get("assigned_to") if cust else None
        asyncio.create_task(_notify_staff(from_number, user_message, assigned_staff_id))
        # Send escalation reply to customer
        _latency_ms = 0
        sent = await send_whatsapp_message(from_number, ESCALATION_REPLY)
        if sent:
            await log_outbound(from_number, ESCALATION_REPLY, "escalation", 0, False)
        else:
            log.error(f"❌ Escalation reply failed to send to {from_number}")
        return

    # ── Route & Generate Reply ───────────────────────────────────
    import time as _time
    _reply_start = _time.time()

    # Check keyword rules first (before AI)
    keyword_reply = await _check_keyword_rules(user_message)
    if keyword_reply:
        log.info("🔑 Keyword match — bypassing AI")
        _routing = "keyword"
        reply_text = keyword_reply
        _latency_ms = int((_time.time() - _reply_start) * 1000)
        sent = await send_whatsapp_message(from_number, reply_text)
        if sent:
            _fallback = False
            await log_outbound(from_number, reply_text, _routing, _latency_ms, _fallback)
        else:
            log.error(f"❌ Keyword reply FAILED to send to {from_number} — no outbound logged")
        return

    # Log routing decision
    _routing = _detect_routing(user_message)
    log.info(f"🔄 Routing: teks='{user_message}' → intent={_routing}")

    reply_text = await generate_reply(user_message, sender_name, from_number)
    _latency_ms = int((_time.time() - _reply_start) * 1000)

    log.info(f"📤 Reply: intent={_routing}, latency={_latency_ms}ms, fallback={reply_text == CANONICAL_FALLBACK}")

    if reply_text:
        _fallback = (reply_text == CANONICAL_FALLBACK)
        sent = await send_whatsapp_message(from_number, reply_text)
        if sent:
            # ── Log outbound message to DB ────────────────────────────
            await log_outbound(from_number, reply_text, _routing, _latency_ms, _fallback)
        else:
            log.error(f"❌ AI reply FAILED to send to {from_number} — no outbound logged")
    else:
        # No reply generated — log as no-reply
        await log_outbound(from_number, "[no_reply]", "none", _latency_ms, True)


def _detect_routing(message: str) -> str:
    """Detect routing path for logging purposes."""
    msg_lower = message.lower().strip()
    if _is_greeting(msg_lower):
        return "greeting"
    if msg_lower in {"1", "2", "3", "4", "menu", "main", "balik", "kembali", "/help", "help", "bantu"}:
        return "static_menu"
    if _is_job_id(message):
        return "job_status"
    return "ai_query"


async def _check_keyword_rules(message: str) -> str | None:
    """Check message against keyword rules. Return matching reply or None."""
    loop = asyncio.get_event_loop()
    rows = await loop.run_in_executor(None, get_all_keywords)
    msg_lower = message.lower().strip()
    for rule in rows:
        if not rule["is_active"]:
            continue
        kw = rule["keyword"].lower().strip()
        if rule["match_type"] == "exact":
            if msg_lower == kw:
                return rule["reply"]
        else:
            if kw and kw in msg_lower:
                return rule["reply"]
    return None


# ═══════════════════════════════════════════════════════════════════
#  DASHBOARD API ENDPOINTS (v2.1 Phase 1)
# ═══════════════════════════════════════════════════════════════════

@app.get("/api/stats")
async def api_stats(date: str = ""):
    """Dashboard: Get bot statistics, optionally for a specific date."""
    return await get_stats(date or None)


@app.get("/api/messages")
async def api_messages(limit: int = 50):
    """Dashboard: Get recent messages."""
    msgs = await get_recent_messages(limit)
    # Add alias fields for frontend compatibility
    for m in msgs:
        m["message"] = m.get("content", "")
        m["source"] = m.get("direction", "unknown")
    return msgs


@app.get("/api/customers")
async def api_customers():
    """Dashboard: Get customer list."""
    return await get_customer_list()


@app.get("/api/messages/{phone}")
async def api_customer_messages(phone: str, limit: int = 50):
    """Dashboard: Get messages from specific customer."""
    msgs = await get_customer_messages(phone, limit)
    # Add alias fields for frontend compatibility
    for m in msgs:
        m["message"] = m.get("content", "")
        m["source"] = m.get("direction", "unknown")
    return msgs


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Dashboard WebSocket: real-time message broadcast."""
    await websocket.accept()
    register_ws(websocket)
    try:
        # Send recent history on connect
        recent = await get_recent_messages(30)
        await websocket.send_json({"event": "history", "data": recent})
        # Keep alive and listen for client messages
        while True:
            try:
                msg = await asyncio.wait_for(websocket.receive_text(), timeout=60)
                if msg == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                await websocket.send_text("pong")
    except Exception:
        pass
    finally:
        unregister_ws(websocket)


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
    "Maaf, sistem sibuk sekejap. Untuk bantuan segera WhatsApp admin:\n+60 16-980 8736 (https://hafjetraub.wasap.my/)"
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
        greeting = get_runtime("greeting_message", CANONICAL_GREETING)
        return greeting

    # ── STEP 3: Static Menu (fast response) ──────────────────────
    reply = _static_menu_handler(msg_lower, sender_name, message)
    if reply:
        return reply

    # ── STEP 4: Escalation Keywords → route to staff ─────────────
    esc_kw_raw = get_runtime("escalation_keywords", "")
    if esc_kw_raw:
        esc_keywords = [k.strip().lower() for k in esc_kw_raw.split(",") if k.strip()]
        if any(kw in msg_lower for kw in esc_keywords):
            log.info(f"🔀 Escalation keyword detected — routing to staff")
            return (
                f"🔀 *Sambungan ke Staff*\n\n"
                f"Pesan anda akan diteruskan kepada staff kami.\n"
                f"Sila tunggu sebentar. Terima kasih!\n\n"
                f"Hubungi kami di +60 16-980 8736 jika urgent."
            )

    # ── STEP 5: AI-Powered (Hermes Agent Core) ───────────────────
    # Check if AI is enabled at runtime
    if not get_runtime_bool("ai_enabled", True):
        log.info("⚙️ AI disabled via settings — using fallback")
        return CANONICAL_FALLBACK

    log.info(f"🤖 Routing to Hermes AI: '{message[:50]}'")

    # FIX: ask_hermes() is sync — run in executor with hard timeout to avoid blocking threads forever
    loop = asyncio.get_event_loop()
    try:
        ai_reply = await asyncio.wait_for(
            loop.run_in_executor(None, ask_hermes, message, sender_name),
            timeout=75,
        )
    except asyncio.TimeoutError:
        log.error("❌ AI timeout after 75s — falling back", exc_info=True)
        ai_reply = None
    except Exception as e:
        log.error(f"❌ AI exception: {e}", exc_info=True)
        ai_reply = None

    if ai_reply and not _is_too_long(ai_reply):
        return ai_reply

    # ── Fallback (satu reply je) ────────────────────────────────
    log.warning("⚠ AI fallback — using default response")
    return get_runtime("fallback_message", CANONICAL_FALLBACK)


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
    # Check runtime greeting keywords
    greet_kw_raw = get_runtime("greeting_keywords", "")
    if greet_kw_raw:
        runtime_kw = [k.strip().lower() for k in greet_kw_raw.split(",") if k.strip()]
        if any(kw in msg_lower for kw in runtime_kw):
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
            "📞 +60 16-980 8736\n\n"
            "Tulis *menu* untuk kembali ke menu utama."
        )

    # ── Menu 3: Hubungi Staff ───────────────────────────────────
    if msg_lower in ["3", "3️⃣", "staff", "hubungi"]:
        return (
            "📞 *Hubungi HAFJET*\n\n"
            "Waktu Operasi:\n"
            "Setiap Hari: 9:00 AM – 9:00 PM\n"
            "📍 Alamat:\n"
            "No. 890 Jalan Lestari 20, Taman Amalina Lestari, 27600 Raub, Pahang\n\n"
            "📞 Telefon: +60 16-980 8736\n"
            "📱 WhatsApp: Mesej ini\n\n"
            "Tulis *menu* untuk kembali ke menu utama."
        )

    # ── Menu 4: Lokasi / Waktu Operasi ──────────────────────────
    if msg_lower in ["4", "4️⃣", "lokasi", "waktu operasi"]:
        return (
            "📍 *Lokasi HAFJET*\n\n"
            "No. 890 Jalan Lestari 20, Taman Amalina Lestari, 27600 Raub, Pahang\n\n"
            "🕐 *Waktu Operasi:*\n"
            "Setiap Hari: 9:00 AM – 9:00 PM\n"
            "Cuti Kebangsaan: Tutup\n\n"
            "🗺 https://g.co/kgs/95C9TB\n\n"
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

async def send_whatsapp_message(to_number: str, message: str) -> bool:
    """Hantar balasan ke WhatsApp via Cloud API.
    Returns True if sent successfully, False otherwise.
    Retries transient failures (502/503/504/timeout) up to 3x with backoff.
    """
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

    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url, json=payload, headers=headers)
                result = resp.json()

                if resp.status_code == 200:
                    log.info(f"✅ Reply sent to {to_number}: {message[:80]}...")
                    return True
                elif resp.status_code in (502, 503, 504) and attempt < max_retries:
                    backoff = 2 ** attempt
                    log.warning(f"⏳ Meta returned {resp.status_code} (attempt {attempt}/{max_retries}) — retrying in {backoff}s")
                    await asyncio.sleep(backoff)
                    continue
                else:
                    log.error(f"❌ Failed to send: {resp.status_code} — {json.dumps(result)[:300]}")
                    return False
        except (httpx.TimeoutException, httpx.NetworkError) as e:
            if attempt < max_retries:
                backoff = 2 ** attempt
                log.warning(f"⏳ Network error on attempt {attempt}/{max_retries}: {e} — retrying in {backoff}s")
                await asyncio.sleep(backoff)
                continue
            log.error(f"❌ Failed to send after {max_retries} attempts: {e}")
            return False
        except Exception as e:
            log.error(f"❌ Error sending message: {e}", exc_info=True)
            return False
    return False


# ═══════════════════════════════════════════════════════════════════
#  LIFESPAN — Startup & Shutdown
# ═══════════════════════════════════════════════════════════════════

@app.on_event("startup")
async def startup_event():
    """Initialize database and verify connections at startup."""
    init_db()
    # Start APScheduler for escalation timeout checks
    _scheduler.add_job(
        _check_escalation_timeout,
        IntervalTrigger(minutes=5),
        id="escalation_timeout_check",
        replace_existing=True,
    )
    _scheduler.start()
    log.info("🚀 HAFJET Bot startup complete — DB initialized, APScheduler started")


@app.on_event("shutdown")
async def shutdown_event():
    """Shutdown: stop APScheduler gracefully."""
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
        log.info("🛑 APScheduler shut down")


# ═══════════════════════════════════════════════════════════════════
#  STAFF JWT AUTH — moved up to avoid forward-reference in Depends()
# ═══════════════════════════════════════════════════════════════════

JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 8


def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def _create_access_token(staff_id: int, email: str, role: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(staff_id),
        "email": email,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=JWT_EXPIRY_HOURS)).timestamp()),
    }
    return jwt.encode(payload, STAFF_JWT_SECRET, algorithm=JWT_ALGORITHM)


async def get_current_staff(request: Request) -> dict:
    """Dependency: extract and verify JWT from Authorization header."""
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = auth_header.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, STAFF_JWT_SECRET, algorithms=[JWT_ALGORITHM])
        staff_id = int(payload["sub"])
        staff = await asyncio.get_event_loop().run_in_executor(None, get_staff_by_id, staff_id)
        if not staff:
            raise HTTPException(status_code=401, detail="Staff not found")
        return {
            "id": staff["id"],
            "name": staff["name"],
            "email": staff["email"],
            "role": staff["role"],
            "status": staff["status"],
            "assigned_phone": staff["assigned_phone"],
        }
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


# ═══════════════════════════════════════════════════════════════════
#  DASHBOARD API — Operator Actions
# ═══════════════════════════════════════════════════════════════════

@app.get("/api/customers/{phone}")
async def api_get_customer(phone: str):
    """Get single customer details."""
    data = get_customer_detail(phone)
    if not data:
        raise HTTPException(status_code=404, detail="Customer not found")
    return data


@app.post("/api/customers/{phone}/resolve")
async def api_resolve(phone: str, current_staff: dict = Depends(get_current_staff)):
    """Mark customer as resolved. (Legacy — prefer PATCH /api/conversations/{phone}/status)"""
    try:
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, update_conversation_status, phone, "resolved")
        if not data:
            raise HTTPException(status_code=404, detail="Customer not found")
        return {"status": "ok", "action": "resolved", "customer": data}
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"❌ api_resolve failed: {e}", exc_info=True)
        return {"status": "error", "success": False, "error": str(e)}


@app.post("/api/customers/{phone}/escalate")
async def api_escalate(phone: str, current_staff: dict = Depends(get_current_staff)):
    """Mark customer as escalated. (Legacy — prefer PATCH /api/conversations/{phone}/status)"""
    try:
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, update_conversation_status, phone, "escalated")
        if not data:
            raise HTTPException(status_code=404, detail="Customer not found")
        asyncio.create_task(_notify_staff(phone, "[dashboard-escalation]", current_staff["id"]))
        return {"status": "ok", "action": "escalated", "customer": data}
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"❌ api_escalate failed: {e}", exc_info=True)
        return {"status": "error", "success": False, "error": str(e)}


@app.post("/api/customers/{phone}/note")
async def api_note(phone: str, data: dict, operator: str = "dashboard"):
    """Add/update customer note."""
    try:
        note = data.get("note", "")
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, update_customer_note, phone, note, operator)
        if not result:
            raise HTTPException(status_code=404, detail="Customer not found")
        return {"status": "ok", "action": "note_updated", "customer": result}
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"❌ api_note failed: {e}", exc_info=True)
        return {"status": "error", "success": False, "error": str(e)}


@app.post("/api/customers/{phone}/handoff")
async def api_handoff(phone: str, data: dict, operator: str = "dashboard"):
    """
    Human handoff: mark customer as 'handoff' (staff takes over).
    If data['message'] present, send staff reply via WhatsApp.
    Webhook will reset to 'active' when customer replies inbound.
    """
    try:
        status = data.get("status", "handoff")
        message = data.get("message", "")

        # Optionally send a WhatsApp message as staff
        if message:
            asyncio.create_task(send_whatsapp_message(phone, message))

        # Update status via executor
        loop = asyncio.get_event_loop()

        def _do_handoff():
            conn_db = _get_db()
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            conn_db.execute(
                "UPDATE customers SET status=?, handoff_at=? WHERE phone=?",
                (status, now, phone)
            )
            conn_db.commit()
            conn_db.close()
            return get_customer_detail(phone)

        detail = await loop.run_in_executor(None, _do_handoff)
        broadcast_ws("customer_handoff", {"phone": phone, "status": "handoff"})
        if not detail:
            raise HTTPException(status_code=404, detail="Customer not found")
        return {"status": "ok", "action": "handoff", "customer": detail}
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"❌ api_handoff failed: {e}", exc_info=True)
        return {"status": "error", "success": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════
#  BOT SETTINGS API
# ═══════════════════════════════════════════════════════════════════

# Valid setting keys and their types/validation
VALID_SETTINGS = {
    "bot_active": {"type": "bool", "default": "true"},
    "ai_enabled": {"type": "bool", "default": "true"},
    "greeting_message": {"type": "text", "default": ""},
    "fallback_message": {"type": "text", "default": ""},
    "dedup_window": {"type": "int", "default": "300", "min": 30, "max": 3600},
    "ai_model": {"type": "text", "default": "nvidia/nemotron-3-super-120b-a12b:free"},
    "ai_timeout": {"type": "int", "default": "10", "min": 3, "max": 60},
    "ai_temperature": {"type": "float", "default": "0.3", "min": 0, "max": 2},
    "ai_max_tokens": {"type": "int", "default": "150", "min": 50, "max": 1000},
    "escalation_keywords": {"type": "text", "default": ""},
    "greeting_keywords": {"type": "text", "default": ""},
}

@app.get("/api/settings")
async def api_get_settings():
    """Get bot settings. Effective values: DB → env override → default."""
    conn_db = _get_db()
    rows = conn_db.execute("SELECT key, value FROM bot_settings").fetchall()
    conn_db.close()
    # Start with defaults
    settings = {k: v["default"] for k, v in VALID_SETTINGS.items()}
    # Override with DB values
    for row in rows:
        key = row["key"]
        if key in VALID_SETTINGS:
            settings[key] = row["value"]
    # Env overrides (highest precedence)
    env_map = {
        "ai_model": _OPENROUTER_MODEL_KEY,
        "ai_enabled": _AI_ENABLED_KEY,
        "bot_active": _BOT_ACTIVE_KEY,
    }
    for key, env_name in env_map.items():
        env_val = os.getenv(env_name)
        if env_val is not None:
            settings[key] = env_val
    return settings


@app.put("/api/settings")
async def api_update_settings(data: dict, operator: str = "dashboard"):
    """Update bot settings with validation."""
    import json as _json
    conn_db = _get_db()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    updated = []
    errors = []

    for key, value in data.items():
        if key.startswith("_"):
            continue
        if key not in VALID_SETTINGS:
            errors.append(f"Unknown setting: {key}")
            continue

        spec = VALID_SETTINGS[key]
        val_str = str(value).strip()

        # Type validation
        if spec["type"] == "bool":
            if val_str.lower() not in ("true", "false", "1", "0", "yes", "no"):
                errors.append(f"{key}: invalid boolean value")
                continue
            val_str = "true" if val_str.lower() in ("true", "1", "yes") else "false"
        elif spec["type"] == "int":
            try:
                val_int = int(val_str)
                if "min" in spec and val_int < spec["min"]:
                    errors.append(f"{key}: minimum {spec['min']}")
                    continue
                if "max" in spec and val_int > spec["max"]:
                    errors.append(f"{key}: maximum {spec['max']}")
                    continue
            except ValueError:
                errors.append(f"{key}: must be integer")
                continue
        elif spec["type"] == "float":
            try:
                val_float = float(val_str)
                if "min" in spec and val_float < spec["min"]:
                    errors.append(f"{key}: minimum {spec['min']}")
                    continue
                if "max" in spec and val_float > spec["max"]:
                    errors.append(f"{key}: maximum {spec['max']}")
                    continue
            except ValueError:
                errors.append(f"{key}: must be number")
                continue

        conn_db.execute(
            "INSERT OR REPLACE INTO bot_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?)",
            (key, val_str, now, operator)
        )
        updated.append(key)

    conn_db.commit()
    conn_db.close()
    _refresh_settings()  # Invalidate cache after save
    broadcast_ws("settings_updated", data)
    return {"status": "ok", "updated": updated, "errors": errors}


# ═══════════════════════════════════════════════════════════════════
#  MANUAL OPERATOR REPLY (Takeover)
# ═══════════════════════════════════════════════════════════════════

@app.post("/api/customers/{phone}/reply")
async def api_manual_reply(phone: str, data: dict, operator: str = "dashboard"):
    """
    Send manual WhatsApp message from operator (staff).
    Marks conversation as staff-handled.
    """
    message = data.get("message", "")
    if not message:
        raise HTTPException(status_code=400, detail="Message required")

    # Send via WhatsApp
    sent = await send_whatsapp_message(phone, message)

    # Log as outbound message
    await log_outbound(phone, message, "staff", 0, 0)

    # Update customer status
    conn_db = _get_db()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn_db.execute(
        "UPDATE customers SET status='handoff', handoff_at=? WHERE phone=?",
        (now, phone)
    )
    conn_db.commit()
    conn_db.close()

    # Broadcast to dashboard
    broadcast_ws("staff_reply_sent", {"phone": phone, "message": message, "operator": operator})

    return {"status": "ok", "action": "staff_reply", "sent": sent, "phone": phone}


# ═══════════════════════════════════════════════════════════════════
#  BLAST / BROADCAST API
# ═══════════════════════════════════════════════════════════════════

_blast_queue = []  # In-memory queue for background blast sending


@app.post("/api/blast")
async def api_create_blast(data: dict):
    """Create a new blast broadcast.
    Body: { message, recipients: "all" | [phone list], schedule_at: null | ISO datetime }
    """
    try:
        message = data.get("message", "").strip()
        if not message:
            raise HTTPException(status_code=400, detail="Message is required")

        recipients_param = data.get("recipients", "all")
        schedule_at = data.get("schedule_at")
        loop = asyncio.get_event_loop()

        if recipients_param == "all":
            phones = await loop.run_in_executor(None, get_all_customer_phones)
            if not phones:
                raise HTTPException(status_code=400, detail="No customers found")
            recipients_str = "all"
            total = len(phones)
        elif isinstance(recipients_param, list):
            phones = recipients_param
            recipients_str = ",".join(phones)
            total = len(phones)
        else:
            raise HTTPException(status_code=400, detail="recipients must be 'all' or a list of phone numbers")

        blast_id = await loop.run_in_executor(
            None, create_blast, message, recipients_str, total, schedule_at
        )

        if not schedule_at:
            # Start sending in background
            asyncio.create_task(_process_blast(blast_id, message, phones))

        return {
            "status": "ok",
            "action": "blast_created",
            "blast_id": blast_id,
            "total_recipients": total,
            "scheduled": bool(schedule_at),
        }
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"❌ api_create_blast failed: {e}", exc_info=True)
        return {"status": "error", "success": False, "error": str(e)}


async def _process_blast(blast_id: int, message: str, phones: list):
    """Background task: send blast messages one by one with rate limiting."""
    sent = 0
    failed = 0
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, update_blast_status, blast_id, "sending")
    WA_TOKEN = os.getenv("WHATSAPP_TOKEN")
    WA_PHONE_ID = os.getenv("WHATSAPP_PHONE_ID")

    for phone in phones:
        try:
            if WA_TOKEN and WA_PHONE_ID:
                url = f"https://graph.facebook.com/v21.0/{WA_PHONE_ID}/messages"
                payload = {
                    "messaging_product": "whatsapp",
                    "to": phone,
                    "type": "text",
                    "text": {"body": message},
                }
                headers = {
                    "Authorization": f"Bearer {WA_TOKEN}",
                    "Content-Type": "application/json",
                }
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.post(url, json=payload, headers=headers)
                    if resp.status_code == 200:
                        sent += 1
                        # Log as outbound message
                        await log_outbound(phone, message, "blast", 0, False,
                                           f"blast_{blast_id}_{phone}")
                    else:
                        failed += 1
                        log.warning(f"⚠️ Blast #{blast_id} failed for {phone}: {resp.status_code}")
            else:
                # No WhatsApp token — just log
                log.warning(f"⚠️ Blast #{blast_id}: no WA token, logging only for {phone}")
                sent += 1
                await log_outbound(phone, message, "blast", 0, False,
                                   f"blast_{blast_id}_{phone}")
        except Exception as e:
            failed += 1
            log.warning(f"⚠️ Blast #{blast_id} error for {phone}: {e}")

        # Rate limit: 1 msg per 2 seconds
        await asyncio.sleep(2)

    final_status = "sent" if failed == 0 else "failed" if sent == 0 else "sent"
    await loop.run_in_executor(None, update_blast_status, blast_id, final_status, sent, failed)
    log.info(f"📢 Blast #{blast_id} complete: {sent} sent, {failed} failed")


@app.get("/api/blast/history")
async def api_blast_history(limit: int = 50):
    """Get blast broadcast history."""
    try:
        loop = asyncio.get_event_loop()
        history = await loop.run_in_executor(None, get_blast_history, limit)
        return history
    except Exception as e:
        log.error(f"❌ api_blast_history failed: {e}", exc_info=True)
        return []


# ═══════════════════════════════════════════════════════════════════
#  CONTACTS / PELANGGAN API
# ═══════════════════════════════════════════════════════════════════


@app.get("/api/contacts")
async def api_get_contacts(tag: str = "", search: str = "", page: int = 1, limit: int = 20):
    """Get paginated contact list with filters."""
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, get_contacts, tag, search, page, limit)
        return result
    except Exception as e:
        log.error(f"❌ api_get_contacts failed: {e}", exc_info=True)
        return {"contacts": [], "total": 0, "page": 1, "limit": limit, "total_pages": 0}


@app.patch("/api/contacts/{phone}")
async def api_update_contact(phone: str, data: dict):
    """Update contact name, tags, or notes."""
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, update_contact, phone,
            data.get("name"), data.get("tags"), data.get("note")
        )
        if not result:
            raise HTTPException(status_code=404, detail="Contact not found")
        return {"status": "ok", "contact": result}
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"❌ api_update_contact failed: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


@app.post("/api/contacts/export")
async def api_export_contacts():
    """Export all contacts as CSV."""
    try:
        loop = asyncio.get_event_loop()
        csv_data = await loop.run_in_executor(None, export_contacts)
        return Response(
            content=csv_data,
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=hafjet-contacts.csv"}
        )
    except Exception as e:
        log.error(f"❌ api_export_contacts failed: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


@app.post("/api/contacts/import")
async def api_import_contacts(data: dict):
    """Import contacts from CSV text."""
    try:
        csv_text = data.get("csv", "")
        if not csv_text:
            raise HTTPException(status_code=400, detail="CSV content is required")
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, import_contacts_csv, csv_text)
        return {"status": "ok", **result}
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"❌ api_import_contacts failed: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


# ═══════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════
#  ANALYTICS API — v2.2.0
# ═══════════════════════════════════════════════════════════════════

@app.get("/api/analytics/overview")
async def api_analytics_overview(
    staff: dict = Depends(get_current_staff),
    start: str = None,
    end: str = None
):
    """Analytics overview: totals, response times, escalation/resolution."""
    try:
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, get_analytics_overview, start, end)
        return data
    except Exception as e:
        log.error(f"❌ api_analytics_overview failed: {e}", exc_info=True)
        return {
            "total_conversations": 0,
            "inbound_total": 0,
            "outbound_total": 0,
            "new_last_7_days": 0,
            "messages_this_month": 0,
            "messages_last_month": 0,
            "messages_change_pct": 0.0,
            "ai_reply_rate": 0.0,
            "today_messages": 0,
            "escalation_count": 0,
            "resolved_count": 0,
            "avg_first_response_time_sec": 0,
            "avg_response_time_sec": 0,
            "avg_resolution_time_sec": 0,
        }


@app.get("/api/analytics/timeseries")
async def api_analytics_timeseries(
    staff: dict = Depends(get_current_staff),
    days: int = 7,
    start: str = None,
    end: str = None
):
    """Time-series chart data: in/out, escalated/resolved, response time per day."""
    try:
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, get_analytics_timeseries, days, start, end)
        return data
    except Exception as e:
        log.error(f"❌ api_analytics_timeseries failed: {e}", exc_info=True)
        return {"labels": [], "messages_in": [], "messages_out": [],
                "escalated": [], "resolved": [], "response_times_sec": []}


@app.get("/api/analytics/agents")
async def api_analytics_agents(
    staff: dict = Depends(get_current_staff),
    staff_id: int = None,
    start: str = None,
    end: str = None
):
    """Agent performance metrics per staff member."""
    try:
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, get_agent_performance, staff_id, start, end)
        return {"agents": data}
    except Exception as e:
        log.error(f"❌ api_analytics_agents failed: {e}", exc_info=True)
        return {"agents": []}


@app.get("/api/analytics/export.csv")
async def api_analytics_export_csv(
    staff: dict = Depends(get_current_staff),
    export_type: str = "overview",
    start: str = None,
    end: str = None
):
    """Export analytics data as CSV download."""
    try:
        loop = asyncio.get_event_loop()
        csv_data = await loop.run_in_executor(None, get_export_csv, export_type, start, end)
        from fastapi.responses import StreamingResponse
        import io
        return StreamingResponse(
            io.StringIO(csv_data),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=analytics_{export_type}.csv"}
        )
    except Exception as e:
        log.error(f"❌ api_analytics_export_csv failed: {e}", exc_info=True)
        return {"error": "CSV export failed"}


@app.get("/api/analytics/chart")
async def api_analytics_chart(
    staff: dict = Depends(get_current_staff),
    days: int = 7
):
    """Legacy time-series chart data (backward compat)."""
    try:
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, get_analytics_chart, days)
        return data
    except Exception as e:
        log.error(f"❌ api_analytics_chart failed: {e}", exc_info=True)
        return {"labels": [], "messages_in": [], "messages_out": []}


# ═══════════════════════════════════════════════════════════════════
#  KEYWORD RULES API
# ═══════════════════════════════════════════════════════════════════

@app.get("/api/keywords")
async def api_keywords():
    try:
        loop = asyncio.get_event_loop()
        rows = await loop.run_in_executor(None, get_all_keywords)
        return {"keywords": rows}
    except Exception as e:
        log.error(f"❌ api_keywords failed: {e}", exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/keywords")
async def api_create_keyword(request: Request):
    try:
        data = await request.json()
        keyword = (data.get("keyword") or "").strip()
        reply = (data.get("reply") or "").strip()
        priority = int(data.get("priority") or 10)
        match_type = data.get("match_type") or "contains"
        if not keyword or not reply:
            return JSONResponse({"error": "keyword dan reply wajib diisi"}, status_code=400)
        loop = asyncio.get_event_loop()
        rule_id = await loop.run_in_executor(None, db_create_keyword, keyword, reply, priority, match_type)
        return {"id": rule_id, "message": "Keyword rule dicipta"}
    except Exception as e:
        log.error(f"❌ api_create_keyword failed: {e}", exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


@app.patch("/api/keywords/{rule_id}")
async def api_update_keyword(rule_id: int, request: Request):
    try:
        data = await request.json()
        allowed = {"keyword", "reply", "priority", "is_active", "match_type"}
        fields = {k: v for k, v in data.items() if k in allowed}
        if not fields:
            return JSONResponse({"error": "Tiada data untuk dikemaskini"}, status_code=400)
        loop = asyncio.get_event_loop()
        ok = await loop.run_in_executor(None, db_update_keyword, rule_id, fields)
        if not ok:
            return JSONResponse({"error": "Keyword rule tidak dijumpai"}, status_code=404)
        return {"message": "Keyword rule dikemaskini"}
    except Exception as e:
        log.error(f"❌ api_update_keyword failed: {e}", exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


@app.delete("/api/keywords/{rule_id}")
async def api_delete_keyword(rule_id: int):
    try:
        loop = asyncio.get_event_loop()
        ok = await loop.run_in_executor(None, db_delete_keyword, rule_id)
        if not ok:
            return JSONResponse({"error": "Keyword rule tidak dijumpai"}, status_code=404)
        return {"message": "Keyword rule dipadam"}
    except Exception as e:
        log.error(f"❌ api_delete_keyword failed: {e}", exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/keywords/test")
async def api_test_keyword(request: Request):
    try:
        data = await request.json()
        msg = (data.get("message") or "").strip()
        if not msg:
            return JSONResponse({"matched": False, "reply": None, "rule_id": None, "keyword": None, "match_type": None})
        rows = await asyncio.get_event_loop().run_in_executor(None, get_all_keywords)
        msg_lower = msg.lower()
        for rule in rows:
            if not rule["is_active"]:
                continue
            kw = rule["keyword"].lower().strip()
            if rule["match_type"] == "exact":
                if msg_lower == kw:
                    return {
                        "matched": True,
                        "rule_id": rule["id"],
                        "keyword": rule["keyword"],
                        "reply": rule["reply"],
                        "match_type": rule["match_type"],
                    }
            else:
                if kw and kw in msg_lower:
                    return {
                        "matched": True,
                        "rule_id": rule["id"],
                        "keyword": rule["keyword"],
                        "reply": rule["reply"],
                        "match_type": rule["match_type"],
                    }
        return {"matched": False, "rule_id": None, "keyword": None, "reply": None, "match_type": None}
    except Exception as e:
        log.error(f"❌ api_test_keyword failed: {e}", exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/keywords/reorder")
async def api_reorder_keywords(request: Request):
    try:
        data = await request.json()
        order = data.get("order") or []
        if not isinstance(order, list) or not order:
            return JSONResponse({"error": "order mesti berupa list ID"}, status_code=400)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, db_reorder_keywords, order)
        return {"message": "Susunan dikemaskini"}
    except Exception as e:
        log.error(f"❌ api_reorder_keywords failed: {e}", exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


# ═══════════════════════════════════════════════════════════════════
#  STAFF AUTH API
# ═══════════════════════════════════════════════════════════════════

@app.post("/api/auth/login")
async def api_login(request: Request):
    try:
        data = await request.json()
        email = (data.get("email") or "").strip().lower()
        password = data.get("password") or ""
        if not email or not password:
            return JSONResponse({"error": "Email dan password wajib diisi"}, status_code=400)
        loop = asyncio.get_event_loop()
        staff = await loop.run_in_executor(None, get_staff_by_email, email)
        if not staff:
            return JSONResponse({"error": "Email atau password salah"}, status_code=401)
        if _hash_password(password) != staff["password_hash"]:
            return JSONResponse({"error": "Email atau password salah"}, status_code=401)
        # Update last_login
        await loop.run_in_executor(None, update_staff_status, staff["id"], staff["status"] or "online")
        token = _create_access_token(staff["id"], staff["email"], staff["role"])
        return {
            "access_token": token,
            "staff": {
                "id": staff["id"],
                "name": staff["name"],
                "email": staff["email"],
                "role": staff["role"],
                "status": staff["status"],
            },
        }
    except Exception as e:
        log.error(f"❌ api_login failed: {e}", exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/auth/me")
async def api_auth_me(current_staff: dict = Depends(get_current_staff)):
    return {"staff": current_staff}


# ═══════════════════════════════════════════════════════════════════
#  STAFF MANAGEMENT API
# ═══════════════════════════════════════════════════════════════════

@app.get("/api/staff")
async def api_list_staff(current_staff: dict = Depends(get_current_staff)):
    try:
        loop = asyncio.get_event_loop()
        rows = await loop.run_in_executor(None, get_all_staff)
        return {"staff": rows}
    except Exception as e:
        log.error(f"❌ api_list_staff failed: {e}", exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/staff")
async def api_create_staff(request: Request, current_staff: dict = Depends(get_current_staff)):
    try:
        # Only admin can create staff
        if current_staff["role"] != "admin":
            return JSONResponse({"error": "Hanya admin boleh menambah staff"}, status_code=403)
        data = await request.json()
        name = (data.get("name") or "").strip()
        email = (data.get("email") or "").strip().lower()
        password = data.get("password") or ""
        role = data.get("role") or "agent"
        if not name or not email or not password:
            return JSONResponse({"error": "name, email dan password wajib diisi"}, status_code=400)
        loop = asyncio.get_event_loop()
        # Check duplicate
        existing = await loop.run_in_executor(None, get_staff_by_email, email)
        if existing:
            return JSONResponse({"error": "Email sudah wujud"}, status_code=400)
        password_hash = _hash_password(password)
        staff_id = await loop.run_in_executor(None, create_staff, name, email, password_hash, role)
        return {"id": staff_id, "message": "Staff dicipta"}
    except Exception as e:
        log.error(f"❌ api_create_staff failed: {e}", exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


@app.patch("/api/staff/{staff_id}/status")
async def api_update_staff_status(staff_id: int, request: Request, current_staff: dict = Depends(get_current_staff)):
    try:
        data = await request.json()
        status = data.get("status") or ""
        allowed = {"online", "busy", "offline"}
        if status not in allowed:
            return JSONResponse({"error": f"Status mesti salah satu dari: {allowed}"}, status_code=400)
        loop = asyncio.get_event_loop()
        # Can only update self unless admin
        if current_staff["id"] != staff_id and current_staff["role"] != "admin":
            return JSONResponse({"error": "Boleh ubah status sendiri sahaja"}, status_code=403)
        ok = await loop.run_in_executor(None, update_staff_status, staff_id, status)
        if not ok:
            return JSONResponse({"error": "Staff tidak dijumpai"}, status_code=404)
        return {"message": "Status dikemaskini"}
    except Exception as e:
        log.error(f"❌ api_update_staff_status failed: {e}", exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


# ═══════════════════════════════════════════════════════════════════
#  INBOX & CONVERSATION ASSIGNMENT API
# ═══════════════════════════════════════════════════════════════════

@app.get("/api/inbox")
async def api_inbox(filter: str = "all", current_staff: dict = Depends(get_current_staff)):
    """Get conversations for inbox.
    filter: all | me | unassigned | escalated | resolved
    """
    try:
        loop = asyncio.get_event_loop()
        convs = await loop.run_in_executor(None, get_inbox_conversations, filter, current_staff["id"])
        return {"conversations": convs, "filter": filter}
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"❌ api_inbox failed: {e}", exc_info=True)
        return {"conversations": [], "filter": filter, "error": str(e)}


@app.patch("/api/conversations/{phone}/status")
async def api_update_conversation_status(phone: str, request: Request, current_staff: dict = Depends(get_current_staff)):
    """Update conversation status: bot_active | assigned | escalated | resolved
    Triggers staff notification if escalated.
    """
    try:
        data = await request.json()
        new_status = data.get("status", "").strip()
        allowed = {"bot_active", "assigned", "escalated", "resolved"}
        if new_status not in allowed:
            return JSONResponse({"error": f"Status mesti salah satu dari: {allowed}"}, status_code=400)
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, update_conversation_status, phone, new_status)
        if not result:
            return JSONResponse({"error": "Customer tidak dijumpai"}, status_code=404)
        # If escalated, notify staff
        if new_status == "escalated":
            asyncio.create_task(_notify_staff(phone, "[auto-escalation]", current_staff["id"]))
        return {"status": "ok", "action": f"status->{new_status}", "customer": result}
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"❌ api_update_conversation_status failed: {e}", exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


@app.patch("/api/conversations/{phone}/assign")
async def api_assign_conversation(phone: str, request: Request, current_staff: dict = Depends(get_current_staff)):
    try:
        data = await request.json()
        staff_id = data.get("staff_id")
        # staff_id can be null to unassign
        loop = asyncio.get_event_loop()
        if staff_id is not None:
            # Verify staff exists
            target = await loop.run_in_executor(None, get_staff_by_id, int(staff_id))
            if not target:
                return JSONResponse({"error": "Staff tidak dijumpai"}, status_code=404)
            await loop.run_in_executor(None, assign_conversation, phone, int(staff_id))
            # Update staff assigned_phone
            await loop.run_in_executor(None, update_staff_status, int(staff_id), target["status"])
            # Also set assigned_phone on staff record
            db = _get_db()
            db.execute("UPDATE staff SET assigned_phone = ? WHERE id = ?", (phone, int(staff_id)))
            db.commit()
            db.close()
        else:
            await loop.run_in_executor(None, assign_conversation, phone, None)
            # Clear previous assignee
            db = _get_db()
            db.execute("UPDATE staff SET assigned_phone = NULL WHERE assigned_phone = ?", (phone,))
            db.commit()
            db.close()
        return {"message": "Conversation assignment updated"}
    except Exception as e:
        log.error(f"❌ api_assign_conversation failed: {e}", exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


# ═════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════
# Letak DI ATAS if __name__ supaya load dalam gunicorn production juga

@app.get("/dashboard/{full_path:path}")
async def serve_dashboard(full_path: str):
    """Serve React dashboard SPA — return index.html for client-side routing."""
    index_file = os.path.join(DASHBOARD_PATH, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return JSONResponse({"error": "Dashboard not built. Run 'npm run build' in dashboard/"})


@app.get("/dashboard", include_in_schema=False)
async def serve_dashboard_root():
    """Serve React dashboard root."""
    index_file = os.path.join(DASHBOARD_PATH, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return JSONResponse({"error": "Dashboard not built. Run 'npm run build' in dashboard/"})


# Catch-all SPA route — serve index.html for any unknown path (e.g. /contacts, /blast)
# Must be LAST route registered, AFTER all /api/* routes
@app.get("/{full_path:path}", include_in_schema=False)
async def spa_catch_all(full_path: str):
    """Serve React SPA for client-side routing paths."""
    # Skip API routes — let them 404 naturally
    if full_path.startswith("api/") or full_path.startswith("dashboard") or full_path == "":
        raise HTTPException(status_code=404)
    index_file = os.path.join(DASHBOARD_PATH, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return JSONResponse({"error": "Dashboard not built"})


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("WEBHOOK_PORT", 8443))
    log.info(f"🚀 Starting HAFJET WhatsApp Bot v2.0 on port {port}")
    log.info(f"   Features: Hybrid AI + Repair Tracking + Static Menu + Dashboard + DB")

    uvicorn.run(
        "webhook_listener:app",
        host="0.0.0.0",
        port=port,
        reload=False,
        log_level="info",
    )