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
import time
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
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

    # SPX self-collection
    get_spx_due_orders, get_all_spx_orders, update_spx_reminder_state, get_spx_stats,
    get_spx_order_by_tracking, VALID_SPX_STATUSES,
    import_spx_csv, upsert_spx_order, get_orders_missing_phone,
    update_order_phone, save_spx_cookies, get_spx_cookies,
    count_spx_reminders_sent_today,
    load_sync_progress, save_sync_progress,
    bulk_map_phones, _normalize_phone,
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
_spx_lock = asyncio.Lock()

# ── SPX Incremental Sync Progress State (DB-backed) ────────────────
_sync_progress = load_sync_progress()

_SYNC_BATCH_SIZE = 150         # orders per page (was 50)
_SYNC_PHONE_BATCH = 10         # phone fetches per tick
_SYNC_INTERVAL_SECONDS = 15    # seconds between ticks (was 30)
_SYNC_MAX_IDLE_SECONDS = 900   # 15 min — auto-terminate if no progress

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
    "/api/spx/phones/from-agent",
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


async def _check_spx_reminders():
    """SPX reminder scheduler — pilot batch: max 60/day, ReadyForCollection + 7 days, live send."""
    loop = asyncio.get_event_loop()
    try:
        today_sent = await loop.run_in_executor(None, count_spx_reminders_sent_today)
        if today_sent >= 60:
            log.info(f"[SPX-LIMIT] daily cap 60 reached at {datetime.now(timezone.utc).isoformat()}")
            return

        # Enforce send window using Malaysia local time (Asia/Kuala_Lumpur)
        now_utc = datetime.now(timezone.utc)
        now_my = now_utc.astimezone(ZoneInfo("Asia/Kuala_Lumpur"))
        if not (8 <= now_my.hour < 21):
            log.info(f"[SPX] Outside Malaysia send window: {now_my.strftime('%H:%M')} MYT (server UTC {now_utc.strftime('%H:%M')})")
            return

        # Cursor: only ReadyForCollection + inbound within 7 days
        due = []
        try:
            all_orders = await loop.run_in_executor(None, get_spx_due_orders, 200, 0)
            now = datetime.now(timezone.utc)
            cutoff = now - timedelta(days=7)
            for o in all_orders:
                if o.get("spx_status") != "ReadyForCollection":
                    continue
                inb = o.get("inbound_time")
                if not inb:
                    continue
                try:
                    inb_dt = datetime.fromisoformat(inb.replace(" ", "T"))
                    if inb_dt.tzinfo is None:
                        inb_dt = inb_dt.replace(tzinfo=timezone.utc)
                except Exception:
                    continue
                if inb_dt < cutoff:
                    continue
                due.append(o)
        except Exception:
            log.exception("[SPX] Failed to load due orders")
            return

        if not due:
            log.info("[SPX] Pilot: no eligible ReadyForCollection orders in last 7 days")
            return

        log.info(f"[SPX] Pilot candidates: {len(due)} | sent today: {today_sent}")
        sent_today = today_sent

        for order in due:
            if sent_today >= 60:
                log.info("[SPX-LIMIT] daily cap 60 reached mid-batch")
                break

            # Guards
            phone = order.get("recipient_phone")
            if not phone:
                continue
            if order.get("is_paused") == 1:
                continue
            state = order.get("hafjet_reminder_state", "Pending")
            if state in ("Completed", "CollectionFailed"):
                continue

            last_sent = order.get("last_reminder_sent_at")
            if last_sent:
                try:
                    last_dt = datetime.fromisoformat(last_sent.replace(" ", "T"))
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.replace(tzinfo=timezone.utc)
                    if (datetime.now(timezone.utc) - last_dt).total_seconds() < 12 * 3600:
                        continue
                except Exception:
                    pass

            # Determine next reminder state
            next_state = _resolve_next_reminder_state(order)
            if not next_state:
                continue

            text = _build_reminder_text(order, next_state)
            # Send via free-form WhatsApp
            ok = await send_whatsapp_message(phone, text)
            if ok:
                now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                await loop.run_in_executor(
                    None,
                    update_spx_reminder_state,
                    order["id"],
                    next_state,
                    now_str,
                )
                await log_outbound(phone, text, "spx_reminder", 0, False)
                log.info(
                    f"[SPX-PILOT] sent tracking={order['spx_tracking_number']} "
                    f"phone={phone} template={next_state} time={now_str}"
                )
                sent_today += 1
            else:
                log.warning(
                    f"[SPX-PILOT] send failed tracking={order['spx_tracking_number']} phone={phone}"
                )

    except Exception as e:
        log.error(f"❌ _check_spx_reminders error: {e}", exc_info=True)


def _resolve_next_reminder_state(order: dict) -> str | None:
    """Return next reminder state based on order deadlines."""
    now = datetime.now(timezone.utc)
    try:
        inb = order.get("inbound_time")
        cbd = order.get("collect_by_date")
        if not cbd:
            return None
        inb_dt = datetime.fromisoformat(inb.replace(" ", "T")) if inb else None
        cbd_dt = datetime.fromisoformat(cbd.replace(" ", "T"))
        if inb_dt:
            if inb_dt.tzinfo is None:
                inb_dt = inb_dt.replace(tzinfo=timezone.utc)
        if cbd_dt.tzinfo is None:
            cbd_dt = cbd_dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None

    # Thresholds
    t1 = inb_dt.replace(hour=8, minute=0, second=0, microsecond=0) + timedelta(days=1) if inb_dt else None
    t2 = cbd_dt.replace(hour=8, minute=0, second=0, microsecond=0) - timedelta(days=3)
    t3 = cbd_dt.replace(hour=8, minute=0, second=0, microsecond=0) - timedelta(days=1)
    t4 = cbd_dt.replace(hour=8, minute=0, second=0, microsecond=0)
    t5 = cbd_dt.replace(hour=9, minute=0, second=0, microsecond=0) + timedelta(days=1)

    cur = order.get("hafjet_reminder_state", "Pending")
    if cur == "Pending":
        if now >= t5:
            return "CollectionFailed"
        if now >= t4:
            return "Remind4"
        if now >= t3:
            return "Remind3"
        if now >= t2:
            return "Remind2"
        if t1 and now >= t1:
            return "Remind1"
    elif cur == "Remind1_Sent":
        if now >= t5:
            return "CollectionFailed"
        if now >= t4:
            return "Remind4"
        if now >= t3:
            return "Remind3"
        if now >= t2:
            return "Remind2"
    elif cur == "Remind2_Sent":
        if now >= t5:
            return "CollectionFailed"
        if now >= t4:
            return "Remind4"
        if now >= t3:
            return "Remind3"
    elif cur == "Remind3_Sent":
        if now >= t5:
            return "CollectionFailed"
        if now >= t4:
            return "Remind4"
    elif cur == "Remind4_Sent":
        if now >= t5:
            return "CollectionFailed"
    return None


_REMINDER_TEMPLATES = {
    "Remind1": "Salam {name}, parcel anda ({tracking}) telah tiba di HAFJET Collection Point. Sila ambil sebelum {collect_date}. Terima kasih!",
    "Remind2": "Peringatan 2: Parcel anda ({tracking}) masih belum diambil. Tarikh akhir: {collect_date}. Sila ambil segera.",
    "Remind3": "⚠️ Esok tarikh akhir! Parcel ({tracking}) perlu diambil sebelum {collect_date}. Hubungi kami jika ada masalah.",
    "Remind4": "🚨 HARI INI tarikh akhir! Parcel ({tracking}) MESTI diambil hari ini atau akan dipulangkan.",
    "CollectionFailed": "Maaf, parcel anda ({tracking}) tidak berjaya diambil dan akan dipulangkan kepada penghantar. Hubungi Shopee untuk bantuan lanjut.",
}


def _build_reminder_text(order: dict, state: str) -> str:
    tpl = _REMINDER_TEMPLATES.get(state, _REMINDER_TEMPLATES["Remind1"])
    try:
        cbd = order.get("collect_by_date", "")
        # convert "YYYY-MM-DD HH:MM:SS" -> "DD/MM/YYYY HH:MM" for friendliness
        display = cbd
        if cbd:
            try:
                dt = datetime.fromisoformat(cbd.replace(" ", "T"))
                display = dt.strftime("%d/%m/%Y %H:%M")
            except Exception:
                pass
        return tpl.format(
            name=order.get("recipient_name", "Customer"),
            tracking=order.get("spx_tracking_number", ""),
            collect_date=display,
        )
    except Exception:
        return tpl.format(name="Customer", tracking=order.get("spx_tracking_number", ""), collect_date=order.get("collect_by_date", ""))



# ═══════════════════════════════════════════════════════════════════
#  SPX API INTEGRATION (TASK B — confirmed endpoints)
# ═══════════════════════════════════════════════════════════════════

_SPX_STATUS_MAP = {
    1: "ReadyForCollection",
    2: "Remind1",
    3: "Remind2",
    4: "Remind3",
    5: "Remind4",
    6: "Collected",
    7: "CollectionFailed",
    8: "Return_Outbound",
    9: "Return_Packing",
}

# Normalise display names → DB/internal names for filter matching
_SPX_STATUS_ALIASES = {
    "ready for collection": "ReadyForCollection",
    "readyforcollection": "ReadyForCollection",
    "collection failed": "CollectionFailed",
    "collectionfailed": "CollectionFailed",
    "remind1": "Remind1",
    "remind2": "Remind2",
    "remind3": "Remind3",
    "remind4": "Remind4",
    "collected": "Collected",
    "return_outbound": "Return_Outbound",
    "return_packing": "Return_Packing",
}


def normalize_spx_status(status_int: int) -> str:
    return _SPX_STATUS_MAP.get(status_int, f"Unknown_{status_int}")


def _safe_json(text: str) -> dict:
    """Parse JSON from SPX API response, stripping non-JSON prefix/suffix.
    Shopee SPX API sometimes wraps JSON with anti-hijacking prefix like ``)]}'\n``
    or may append trailing whitespace/garbage.
    Returns {} on failure (never raises)."""
    text = (text or "").strip()
    if not text:
        log.warning("🔸 _safe_json: empty body, returning {}")
        return {}

    # Locate first JSON bracket: '{' for object, '[' for array
    start = -1
    for ch in ("{", "["):
        idx = text.find(ch)
        if idx != -1 and (start == -1 or idx < start):
            start = idx
    if start == -1:
        log.warning(f"🔸 _safe_json: no JSON bracket found in {len(text)}-byte response, returning {{}}")
        return {}

    trimmed = text[start:]

    # Find matching end bracket via nesting counter
    end = -1
    depth = 0
    in_str = False
    esc = False
    for i, ch in enumerate(trimmed):
        if esc:
            esc = False
            continue
        if ch == "\\" and in_str:
            esc = True
            continue
        if ch == '"' and not esc:
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == trimmed[0]:
            depth += 1
        elif (trimmed[0] == "{" and ch == "}") or (trimmed[0] == "[" and ch == "]"):
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    if end == -1:
        log.warning(f"🔸 _safe_json: unmatched bracket in {len(trimmed)}-byte response, returning {{}}")
        return {}

    try:
        return json.loads(trimmed[:end])
    except json.JSONDecodeError as e:
        log.warning(f"🔸 _safe_json: parse failed at pos {e.pos}: {e.msg[:80]}, returning {{}}")
        return {}

async def fetch_spx_order_list(cookies: str, pageno: int = 1, count: int = 50) -> dict:
    url = "https://sp.spx.shopee.com.my/sp-api/point/order/collection/list"
    now = int(time.time())
    params = {
        "inbound_time_start": now - (90 * 24 * 3600),
        "inbound_time_end": now,
        "pageno": pageno,
        "count": count,
    }
    headers = {
        "Cookie": cookies,
        "Referer": "https://sp.spx.shopee.com.my/",
        "Origin": "https://sp.spx.shopee.com.my",
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, params=params, headers=headers)
        body = resp.text or ""
        # Log truncated raw body for debugging JSON parse errors
        log.debug("[SPX-DEBUG] GET %s status=%s body(500)=%.500s", url, resp.status_code, body)
        if resp.status_code == 401:
            raise Exception("SPX_SESSION_EXPIRED")
        resp.raise_for_status()
        blen = len(body.strip())
        if blen == 0:
            log.warning("⚠️ SPX order list response body empty — treating as no orders")
            return {}
        try:
            return resp.json()
        except json.JSONDecodeError:
            return _safe_json(body)


def _extract_sap_headers(cookies: str) -> dict:
    """Extract SPX SAP security headers from cookie string, if present.
    x-sap-ri / x-sap-sec are JS-generated headers (NOT stored as cookies).
    They may be embedded as sap_ri= / sap_sec= in the stored cookie string
    by the dashboard SPX config page for manual paste.
    Returns empty dict if not found (caller falls back to cookies-only)."""
    result = {}
    if not cookies:
        return result
    for name, pattern in [
        ("x-sap-ri", r'(?:x-)?sap[-_]ri=([^;\s]+)'),
        ("x-sap-sec", r'(?:x-)?sap[-_]sec=([^;\s]+)'),
    ]:
        m = _re.search(pattern, cookies, _re.I)
        if m:
            result[name] = m.group(1)
            log.debug("[SPX-SAP] ✅ %s extracted (len=%d)", name, len(result[name]))
    return result


async def fetch_spx_phone(cookies: str, entity_id: str, tracking_number: str) -> str | None:
    url = "https://sp.spx.shopee.com.my/sp-api/order/show_secret"
    payload = {
        "entity_id": str(entity_id),
        "entity_type": 2,
        "info_type": 2,
        "query_id": tracking_number,
        "view_channel": 2,
    }
    sap_headers = _extract_sap_headers(cookies)
    headers = {
        "Cookie": cookies,
        "Content-Type": "application/json;charset=UTF-8",
        "Referer": "https://sp.spx.shopee.com.my/",
        "Origin": "https://sp.spx.shopee.com.my",
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "device-id": "v2-MmYeRgaFMgI2X-ZbOc_fC",
        "app": "SP Portal",
        "version": "@servicepoint/vue-project:1.0.0-231130",
    }
    if sap_headers.get("x-sap-ri"):
        headers["x-sap-ri"] = sap_headers["x-sap-ri"]
    if sap_headers.get("x-sap-sec"):
        headers["x-sap-sec"] = sap_headers["x-sap-sec"]
    if not sap_headers:
        log.warning("[SPX-PHONE] ⚠️ No SAP headers in cookies — phone fetch may fail for %s", tracking_number)
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json=payload, headers=headers)
        body = resp.text or ""
        # Log truncated raw body for debugging JSON parse errors
        log.debug("[SPX-DEBUG] POST %s status=%s body(500)=%.500s", url, resp.status_code, body)
        if resp.status_code == 401:
            raise Exception("SPX_SESSION_EXPIRED")
        if resp.status_code != 200:
            log.warning("⚠️ SPX phone fetch non-200: %s body(300)=%.300s", resp.status_code, body)
            return None
        blen = len(body.strip())
        if blen == 0:
            log.warning("⚠️ SPX phone response body empty — treating as no phone")
            return None
        try:
            data = resp.json()
        except json.JSONDecodeError:
            data = _safe_json(body)
        if data.get("retcode") == 0:
            return data.get("data", {}).get("real_message")
        log.warning("⚠️ SPX phone fetch retcode=%s body(300)=%.300s", data.get("retcode"), body)
        return None


async def batch_sync_spx_orders() -> dict:
    cookies = get_spx_cookies()
    if not cookies:
        return {"error": "No SPX session configured"}

    stats = {"synced": 0, "phones_fetched": 0, "errors": []}
    stats["_version"] = "spx-incremental-sync-v2.2.1-csrf-fix"

    # Step 1: Fetch all orders from SPX API
    pageno = 1
    while True:
        try:
            result = await fetch_spx_order_list(cookies, pageno=pageno, count=50)
        except Exception as e:
            stats["errors"].append(str(e))
            break

        orders = result.get("data", {}).get("list", [])
        if not orders:
            break

        # Debug: log first order field types
        if pageno == 1 and orders:
            sample = orders[0]
            log.info("🔍 First SPX order sample keys: %s", list(sample.keys())[:20])
            for _k in ["id", "shipment_id", "scan_tracking_number", "recipient_name",
                        "inbound_time", "outbound_time", "collect_time", "status", "storage_id"]:
                _v = sample.get(_k)
                log.info("🔍 SPX field %r type=%s repr=%s", _k, type(_v).__name__, repr(_v)[:80])

        for order in orders:
            inbound = datetime.fromtimestamp(order["inbound_time"]).strftime("%Y-%m-%d %H:%M:%S") if order.get("inbound_time") else None
            outbound = datetime.fromtimestamp(order["outbound_time"]).strftime("%Y-%m-%d %H:%M:%S") if order.get("outbound_time") else None
            collect = datetime.fromtimestamp(order["collect_time"]).strftime("%Y-%m-%d %H:%M:%S") if order.get("collect_time") else None

            try:
                # Convert all fields to safe types before upsert
                upsert_spx_order({
                    "entity_id": str(order["id"]),
                    "spx_tracking_number": str(order.get("shipment_id", "")),
                    "scan_tracking_number": str(order.get("scan_tracking_number") or ""),
                    "recipient_name": str(order.get("recipient_name") or ""),
                    "inbound_time": inbound,
                    "outbound_time": outbound,
                    "collect_by_date": collect,
                    "spx_status": normalize_spx_status(order.get("status", 0)),
                    "storage_id": str(order.get("storage_id", "")),
                })
                stats["synced"] += 1
            except Exception as exc:
                log.error(f"❌ upsert_spx_order FAILED for {order.get('shipment_id')}", exc_info=True)
                stats["errors"].append(f"upsert {order.get('shipment_id')}: {exc}")

        total = result.get("data", {}).get("total", 0)
        if pageno * 50 >= total:
            break
        pageno += 1
        await asyncio.sleep(0.3)

    # Step 2: Batch fetch phones
    missing = get_orders_missing_phone()
    for order in missing:
        if not order.get("entity_id"):
            continue
        try:
            phone = await fetch_spx_phone(cookies, order["entity_id"], order["spx_tracking_number"])
            if phone:
                update_order_phone(order["id"], phone)
                stats["phones_fetched"] += 1
        except Exception as exc:
            # Bubble up session expiry so caller can report it
            if "SPX_SESSION_EXPIRED" in str(exc):
                stats["errors"].append("SPX_SESSION_EXPIRED")
                break
            stats["errors"].append(f"{order['spx_tracking_number']}: {exc}")
        await asyncio.sleep(0.5)

    return stats


# ── Incremental Background Sync (triggered by api_spx_sync, runs every 30s) ──

async def _sync_spx_batch():
    """Process one batch of SPX sync per tick (called by APScheduler).
    State machine: idle → fetching_orders → fetching_phones → completed.
    Runs only if _sync_progress['running'] is True."""
    global _sync_progress
    prog = _sync_progress
    if not prog["running"]:
        return  # nothing to do

    # ── Auto-terminate if no progress for >15 min ──
    if prog["last_progress_at"] and prog["phase"] in ("fetching_orders", "fetching_phones"):
        elapsed = time.time() - prog["last_progress_at"]
        if elapsed > _SYNC_MAX_IDLE_SECONDS:
            log.warning("[SPX-SYNC] ⏰ Auto-terminate — no progress for %.0fs (>%ds)",
                        elapsed, _SYNC_MAX_IDLE_SECONDS)
            prog["running"] = False
            prog["phase"] = "failed"
            prog["last_error"] = f"Auto-terminated: no progress for {elapsed:.0f}s"
            return

    async with _spx_lock:
        try:
            cookies = get_spx_cookies()
            if not cookies:
                prog["running"] = False
                prog["phase"] = "failed"
                prog["last_error"] = "No SPX session configured"
                return

            # ── Phase 1: Fetch orders page-by-page ──
            if prog["phase"] == "fetching_orders":
                prog["current_page"] += 1
                page = prog["current_page"]
                log.info("[SPX-SYNC] ⏳ Page %d/%d — syncing orders...", page, prog["total_pages"])

                result = await fetch_spx_order_list(cookies, pageno=page, count=_SYNC_BATCH_SIZE)
                orders = result.get("data", {}).get("list", [])
                if not orders:
                    # No more orders — move to phone fetching
                    prog["phase"] = "fetching_phones"
                    missing = get_orders_missing_phone()
                    prog["total_missing_phones"] = len(missing)
                    log.info("[SPX-SYNC] ✅ All pages done. %d orders missing phone. Starting phone fetch...", len(missing))
                    return  # next tick will pick up phones

                for order in orders:
                    inbound = datetime.fromtimestamp(order["inbound_time"]).strftime("%Y-%m-%d %H:%M:%S") if order.get("inbound_time") else None
                    outbound = datetime.fromtimestamp(order["outbound_time"]).strftime("%Y-%m-%d %H:%M:%S") if order.get("outbound_time") else None
                    collect = datetime.fromtimestamp(order["collect_time"]).strftime("%Y-%m-%d %H:%M:%S") if order.get("collect_time") else None
                    try:
                        upsert_spx_order({
                            "entity_id": str(order["id"]),
                            "spx_tracking_number": str(order.get("shipment_id", "")),
                            "scan_tracking_number": str(order.get("scan_tracking_number") or ""),
                            "recipient_name": str(order.get("recipient_name") or ""),
                            "inbound_time": inbound,
                            "outbound_time": outbound,
                            "collect_by_date": collect,
                            "spx_status": normalize_spx_status(order.get("status", 0)),
                            "storage_id": str(order.get("storage_id", "")),
                        })
                        prog["synced"] += 1
                    except Exception as exc:
                        log.error("[SPX-SYNC] ❌ upsert FAILED for %s: %s", order.get("shipment_id"), exc)
                        prog["errors"].append(f"upsert {order.get('shipment_id')}: {exc}")
                        prog["last_error"] = str(exc)

                total = result.get("data", {}).get("total", 0)
                prog["total_orders"] = total
                prog["total_pages"] = (total + _SYNC_BATCH_SIZE - 1) // _SYNC_BATCH_SIZE
                prog["last_progress_at"] = time.time()
                log.info("[SPX-SYNC] ✅ Page %d/%d done — %d synced so far", page, prog["total_pages"], prog["synced"])

                # If this was the last page, switch to phone fetching
                if page * _SYNC_BATCH_SIZE >= total:
                    prog["phase"] = "fetching_phones"
                    prog["phone_fetch_offset"] = 0
                    missing = get_orders_missing_phone(limit=1, offset=0)
                    prog["total_missing_phones"] = len(missing)
                    log.info("[SPX-SYNC] ✅ All pages done. %d orders missing phone. Starting phone fetch...", len(missing))

            # ── Phase 2: Fetch phones in batches ──
            elif prog["phase"] == "fetching_phones":
                offset = prog["phone_fetch_offset"]
                missing = get_orders_missing_phone(limit=_SYNC_PHONE_BATCH, offset=offset)
                if not missing:
                    prog["running"] = False
                    prog["phase"] = "completed"
                    prog["completed_at"] = datetime.now(timezone.utc).isoformat()
                    log.info("[SPX-SYNC] ✅ COMPLETE — %d synced, %d phones fetched, %d errors",
                             prog["synced"], prog["phones_fetched"], len(prog["errors"]))
                    return

                for order in missing:
                    try:
                        phone = await fetch_spx_phone(cookies, order["entity_id"], order["spx_tracking_number"])
                        if phone:
                            update_order_phone(order["id"], phone)
                            prog["phones_fetched"] += 1
                    except Exception as exc:
                        if "SPX_SESSION_EXPIRED" in str(exc):
                            prog["errors"].append("SPX_SESSION_EXPIRED")
                            prog["last_error"] = "SPX_SESSION_EXPIRED"
                            prog["running"] = False
                            prog["phase"] = "failed"
                            return
                        prog["errors"].append(f"{order['spx_tracking_number']}: {exc}")
                        prog["last_error"] = str(exc)
                    await asyncio.sleep(0.5)

                # Move offset forward regardless of success/failure — avoids infinite loop
                prog["phone_fetch_offset"] = offset + len(missing)

                remaining = max(0, prog["total_missing_phones"] - prog["phones_fetched"])
                prog["last_progress_at"] = time.time()
                log.info("[SPX-SYNC] 📞 Phone batch done — %d fetched, ~%d remaining",
                         prog["phones_fetched"], remaining)

        except Exception as exc:
            prog["running"] = False
            prog["phase"] = "failed"
            prog["last_error"] = str(exc)
            log.error("[SPX-SYNC] ❌ Batch failed: %s", exc, exc_info=True)
        finally:
            # Persist progress to DB — survives restarts & consistent across workers
            save_sync_progress(prog)


async def _ensure_sync_job():
    """Add the incremental sync job to the scheduler if not already added."""
    if not _scheduler.get_job("spx_incremental_sync"):
        _scheduler.add_job(
            _sync_spx_batch,
            IntervalTrigger(seconds=_SYNC_INTERVAL_SECONDS),
            id="spx_incremental_sync",
            replace_existing=True,
        )
        log.info("[SPX-SYNC] 🔄 Incremental sync job registered (every %ds)", _SYNC_INTERVAL_SECONDS)


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
                    await websocket.send_json({"type": "pong"})
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "pong"})
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
    # Start APScheduler for SPX self-collection reminders (dry-run)
    _scheduler.add_job(
        _check_spx_reminders,
        IntervalTrigger(minutes=15),
        id="spx_reminder_check",
        replace_existing=True,
    )
    # Register incremental sync job (paused — activated by POST /api/spx/sync)
    _scheduler.add_job(
        _sync_spx_batch,
        IntervalTrigger(seconds=_SYNC_INTERVAL_SECONDS),
        id="spx_incremental_sync",
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
#  SPX SELF-COLLECTION API
# ═══════════════════════════════════════════════════════════════════

@app.get("/api/spx/orders")
async def api_spx_orders(
    status: str = "",
    storage_id: str = "",
    search: str = "",
    page: int = 1,
    limit: int = 50,
    staff: dict = Depends(get_current_staff),
):
    """List SPX orders with optional filters."""
    try:
        loop = asyncio.get_event_loop()
        # Build query with filters (lightweight — use Python filtering for small datasets)
        all_orders = await loop.run_in_executor(None, get_all_spx_orders, 10000, 0)
        if status:
            _normalised = _SPX_STATUS_ALIASES.get(status.lower().strip(), status)
            all_orders = [o for o in all_orders if o.get("spx_status") == _normalised]
        if storage_id:
            all_orders = [o for o in all_orders if o.get("storage_id") == storage_id]
        if search:
            s = search.lower()
            all_orders = [
                o for o in all_orders
                if s in (o.get("spx_tracking_number") or "").lower()
                or s in (o.get("recipient_name") or "").lower()
                or s in (o.get("recipient_phone") or "").lower()
            ]
        total = len(all_orders)
        start = (page - 1) * limit
        page_items = all_orders[start:start + limit]
        return {"orders": page_items, "total": total, "page": page, "limit": limit}
    except Exception as e:
        log.error(f"❌ api_spx_orders failed: {e}", exc_info=True)
        return {"orders": [], "total": 0, "page": 1, "limit": limit}


@app.post("/api/spx/import-csv")
async def api_spx_import_csv(request: Request, staff: dict = Depends(get_current_staff)):
    """Import SPX orders from CSV upload."""
    try:
        form = await request.form()
        upload = form.get("file")
        if not upload:
            return JSONResponse({"error": "CSV file is required"}, status_code=400)
        content = await upload.read()
        csv_text = content.decode("utf-8", errors="replace")
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, import_spx_csv, csv_text)
        return {"status": "ok", **result}
    except Exception as e:
        log.error(f"❌ api_spx_import_csv failed: {e}", exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


def _process_agent_phones(orders: list) -> dict:
    """Process phone data from browser agent. Idempotent — safe to re-send.
    
    Each order dict: {tracking, phone, name?, status?, source?, seen_at?}
    Returns: {updated, skipped, not_found, errors}
    """
    conn = _get_db()
    updated = skipped = not_found = 0
    errors = []
    
    for order in orders:
        tracking = (order.get("tracking") or "").strip().upper()
        phone_raw = (order.get("phone") or "").strip()
        name = (order.get("name") or "").strip()
        
        if not tracking:
            errors.append({"tracking": tracking, "reason": "Missing tracking number"})
            skipped += 1
            continue
        
        # Normalize phone (same logic as db_logger._normalize_phone)
        phone = _normalize_phone(phone_raw) if phone_raw else ""
        if not phone:
            errors.append({"tracking": tracking, "reason": "Invalid phone: {}".format(phone_raw)})
            skipped += 1
            continue
        
        # Find existing order
        row = conn.execute(
            "SELECT id, recipient_phone, recipient_name FROM spx_self_collection_orders WHERE spx_tracking_number=?",
            (tracking,),
        ).fetchone()
        
        if not row:
            errors.append({"tracking": tracking, "reason": "Tracking not found in DB"})
            not_found += 1
            continue
        
        # Skip if phone already matches
        existing_phone = row["recipient_phone"] or ""
        if existing_phone == phone:
            skipped += 1
            continue
        
        # Update phone (and name if provided)
        if name and (row["recipient_name"] or "") != name:
            conn.execute(
                "UPDATE spx_self_collection_orders SET recipient_phone=?, recipient_name=?, updated_at=datetime('now') WHERE id=?",
                (phone, name, row["id"]),
            )
        else:
            conn.execute(
                "UPDATE spx_self_collection_orders SET recipient_phone=?, updated_at=datetime('now') WHERE id=?",
                (phone, row["id"]),
            )
        updated += 1
    
    conn.commit()
    conn.close()
    
    log.info("[SPX-AGENT] Processed {} orders: updated={}, skipped={}, not_found={}, errors={}".format(
        len(orders), updated, skipped, not_found, len(errors)))
    
    return {"updated": updated, "skipped": skipped, "not_found": not_found, "errors": errors[:50]}


@app.post("/api/spx/phones/bulk")
async def api_spx_phones_bulk(data: dict, staff: dict = Depends(get_current_staff)):
    """Bulk map tracking_number → phone from text input.
    Request: {"text": "SPXMY... 0123456789\\nSPXMY... 0123456790"}"""
    try:
        text = (data.get("text") or "").strip()
        if not text:
            return JSONResponse({"error": "text is required"}, status_code=400)
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, bulk_map_phones, text)
        return {"status": "ok", **result}
    except Exception as e:
        log.error(f"❌ api_spx_phones_bulk failed: {e}", exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/spx/phones/from-agent")
async def api_spx_phones_from_agent(request: Request):
    """Receive phone data from browser-side SPX agent (Tampermonkey).
    
    Auth: X-API-Key header (DASHBOARD_API_KEY) — checked by middleware.
    
    Request body:
      Single: {"tracking": "SPXMY...", "phone": "601133114781", "name": "...", "status": "ReadyForCollection", "source": "spx-agent", "seen_at": "..."}
      Batch:  {"orders": [{...}, {...}]}
    
    Backend handles: normalize phone, find order by tracking, update DB.
    Idempotent — safe to re-send same tracking+phone.
    """
    try:
        body = await request.json()
        
        # Accept both single and batch
        orders = body.get("orders", [body] if "tracking" in body else [])
        if not orders:
            return JSONResponse({"error": "Missing 'tracking' field or 'orders' array"}, status_code=400)
        
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _process_agent_phones, orders)
        return {"status": "ok", **result}
    except Exception as e:
        log.error(f"api_spx_phones_from_agent failed: {e}", exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


@app.patch("/api/spx/orders/{order_id}")
async def api_spx_update_order(order_id: int, data: dict, staff: dict = Depends(get_current_staff)):
    """Update SPX order fields: spx_status, is_paused, notes."""
    try:
        allowed = {"spx_status", "is_paused", "notes"}
        updates = {k: v for k, v in data.items() if k in allowed}
        if not updates:
            return JSONResponse({"error": "No updatable fields provided"}, status_code=400)
        conn = _get_db()
        set_clause = ", ".join(f"{k}=?" for k in updates)
        vals = list(updates.values()) + [datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), order_id]
        conn.execute(f"UPDATE spx_self_collection_orders SET {set_clause}, updated_at=? WHERE id=?", vals)
        conn.commit()
        row = conn.execute("SELECT * FROM spx_self_collection_orders WHERE id=?", (order_id,)).fetchone()
        conn.close()
        return {"status": "ok", "order": dict(row) if row else None}
    except Exception as e:
        log.error(f"❌ api_spx_update_order failed: {e}", exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/spx/orders/{order_id}/collected")
async def api_spx_mark_collected(order_id: int, staff: dict = Depends(get_current_staff)):
    """Mark order as Collected and stop reminders."""
    try:
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        conn = _get_db()
        conn.execute(
            """UPDATE spx_self_collection_orders
               SET spx_status='Collected', hafjet_reminder_state='Completed',
                   outbound_time=?, updated_at=?
               WHERE id=?""",
            (now_str, now_str, order_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM spx_self_collection_orders WHERE id=?", (order_id,)).fetchone()
        conn.close()
        return {"status": "ok", "order": dict(row) if row else None}
    except Exception as e:
        log.error(f"❌ api_spx_mark_collected failed: {e}", exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/spx/stats")
async def api_spx_stats(staff: dict = Depends(get_current_staff)):
    """Get SPX reminder dashboard stats."""
    try:
        loop = asyncio.get_event_loop()
        stats = await loop.run_in_executor(None, get_spx_stats)
        return stats
    except Exception as e:
        log.error(f"❌ api_spx_stats failed: {e}", exc_info=True)
        return {"total": 0, "by_status": {}, "rows": []}


@app.post("/api/spx/session")
async def api_spx_set_session(data: dict, staff: dict = Depends(get_current_staff)):
    """Save SPX session cookies."""
    cookies = (data.get("cookies") or "").strip()
    if not cookies:
        return JSONResponse({"error": "cookies is required"}, status_code=400)
    try:
        await asyncio.get_event_loop().run_in_executor(None, save_spx_cookies, cookies)
        return {"saved": True}
    except Exception as e:
        log.error(f"❌ api_spx_set_session failed: {e}", exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/spx/session-status")
async def api_spx_session_status(staff: dict = Depends(get_current_staff)):
    """Test SPX session by making a lightweight list call."""
    cookies = get_spx_cookies()
    if not cookies:
        return {"active": False, "error": "No session configured"}
    try:
        result = await fetch_spx_order_list(cookies, pageno=1, count=1)
        total = result.get("data", {}).get("total", 0)
        return {"active": True, "total": total, "error": None, "deploy_version": "spx-incremental-sync-v2.2.1-csrf-fix"}
    except Exception as exc:
        err = str(exc)
        if "SPX_SESSION_EXPIRED" in err:
            return JSONResponse({"active": False, "error": "SPX_SESSION_EXPIRED"}, status_code=401)
        return JSONResponse({"active": False, "error": err}, status_code=500)


@app.post("/api/spx/sync")
async def api_spx_sync(staff: dict = Depends(get_current_staff)):
    """Trigger background incremental SPX sync (returns immediately)."""
    global _sync_progress
    try:
        cookies = get_spx_cookies()
        if not cookies:
            return JSONResponse({"error": "No SPX session configured"}, status_code=400)

        # Reset progress state and persist immediately
        _sync_progress = {
            "running": True,
            "phase": "fetching_orders",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": None,
            "last_progress_at": time.time(),
            "current_page": 0,
            "total_pages": 0,
            "total_orders": 0,
            "synced": 0,
            "phones_fetched": 0,
            "total_missing_phones": 0,
            "phone_fetch_offset": 0,
            "errors": [],
            "last_error": None,
        }
        save_sync_progress(_sync_progress)

        # Ensure the incremental job is registered
        await _ensure_sync_job()

        log.info("[SPX-SYNC] 🔄 Sync triggered — processing %d orders/page every %ds",
                 _SYNC_BATCH_SIZE, _SYNC_INTERVAL_SECONDS)
        return JSONResponse({
            "status": "accepted",
            "message": f"Sync started — processing {_SYNC_BATCH_SIZE} orders per batch, check /api/spx/sync-progress for status",
        }, status_code=202)
    except Exception as e:
        log.error(f"❌ api_spx_sync trigger failed: {e}", exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/spx/sync-progress")
async def api_spx_sync_progress(staff: dict = Depends(get_current_staff)):
    """Get current sync progress (polled by frontend)."""
    global _sync_progress
    return {
        **_sync_progress,
        "errors": _sync_progress["errors"][-10:],  # last 10 errors only
        "deploy_version": "spx-incremental-sync-v2.2.1-csrf-fix",
    }


@app.post("/api/spx/sync-reset")
async def api_spx_sync_reset(staff: dict = Depends(get_current_staff)):
    """Force-reset sync state (stuck recovery)."""
    global _sync_progress
    _sync_progress = {
        "running": False,
        "phase": "idle",
        "started_at": None,
        "completed_at": None,
        "last_progress_at": None,
        "current_page": 0,
        "total_pages": 0,
        "total_orders": 0,
        "synced": 0,
        "phones_fetched": 0,
        "total_missing_phones": 0,
        "phone_fetch_offset": 0,
        "errors": [],
        "last_error": None,
    }
    save_sync_progress(_sync_progress)
    log.info("[SPX-SYNC] 🔄 Sync state force-reset by staff")
    return {"status": "ok", "message": "Sync state reset"}


@app.post("/api/spx/fetch-phones")
async def api_spx_fetch_phones(staff: dict = Depends(get_current_staff)):
    """Dedicated endpoint to ONLY fetch phone numbers for orders missing them.
    Does NOT re-fetch orders from SPX — just fills in missing phone numbers.
    Useful when phone column shows '-' after orders are already synced."""
    try:
        cookies = get_spx_cookies()
        if not cookies:
            return JSONResponse({"error": "No SPX session configured"}, status_code=400)

        # Count missing phones
        missing = get_orders_missing_phone(limit=1, offset=0)
        total_missing = get_orders_missing_phone(limit=99999, offset=0)
        total_count = len(total_missing) if isinstance(total_missing, list) else 0

        if total_count == 0:
            return {"status": "ok", "processed": 0, "total_missing": 0,
                    "message": "No orders missing phone numbers"}

        # Process in batches to avoid timeout
        batch_size = 10
        processed = 0
        errors = []
        sap_headers = _extract_sap_headers(cookies)

        for offset in range(0, total_count, batch_size):
            batch = get_orders_missing_phone(limit=batch_size, offset=offset)
            if not batch:
                break
            for order in batch:
                if not order.get("entity_id"):
                    continue
                try:
                    phone = await fetch_spx_phone(cookies, order["entity_id"], order["spx_tracking_number"])
                    if phone:
                        update_order_phone(order["id"], phone)
                        processed += 1
                except Exception as exc:
                    if "SPX_SESSION_EXPIRED" in str(exc):
                        return JSONResponse({"error": "SPX_SESSION_EXPIRED",
                                             "processed": processed,
                                             "message": "Session expired — please re-login to SPX"},
                                            status_code=401)
                    errors.append(f"{order['spx_tracking_number']}: {exc}")
                await asyncio.sleep(0.5)
            await asyncio.sleep(1)  # rate limit between batches

        return {
            "status": "ok",
            "processed": processed,
            "total_missing": total_count,
            "errors": errors[:10],  # last 10 only
            "message": f"Fetched {processed}/{total_count} phone numbers",
        }
    except Exception as e:
        log.error(f"❌ api_spx_fetch_phones failed: {e}", exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


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