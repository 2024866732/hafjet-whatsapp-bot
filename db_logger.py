"""
db_logger.py — Async SQLite logging for WhatsApp messages + WebSocket broadcast.
Fast, non-blocking, zero config. Data persists across restarts.
"""

import os
import json
import asyncio
import sqlite3
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

log = logging.getLogger("hafjet-whatsapp.db")

# ── Config ─────────────────────────────────────────────────────────
# In Azure: /home/site/wwwroot/bot_data.db
# In local: ~/.hermes/whatsapp-bot/bot_data.db
_azure_root = os.environ.get("HOME", "")
if _azure_root == "/home/site/wwwroot":
    DB_PATH = os.path.join(_azure_root, "bot_data.db")
else:
    DB_PATH = os.path.expanduser("~/.hermes/whatsapp-bot/bot_data.db")

# ── WebSocket subscribers ──────────────────────────────────────────
_ws_clients: set = set()

def register_ws(client):
    """Register a WebSocket client."""
    _ws_clients.add(client)

def unregister_ws(client):
    """Unregister a WebSocket client."""
    _ws_clients.discard(client)

def broadcast_ws(event_type: str, data: dict):
    """Broadcast event to all connected WebSocket clients."""
    if not _ws_clients:
        return
    message = json.dumps({"event": event_type, "data": data})
    for client in list(_ws_clients):
        try:
            asyncio.get_event_loop().create_task(client.send_text(message))
        except Exception:
            _ws_clients.discard(client)


# ── Database Setup ────────────────────────────────────────────────

def _get_db() -> sqlite3.Connection:
    """Get or create DB connection (per-call, thread-safe with check_same_thread=False)."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    """Create tables if not exist. Call once at startup."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = _get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone VARCHAR(20) UNIQUE NOT NULL,
            name VARCHAR(100),
            first_contact TIMESTAMP DEFAULT (datetime('now')),
            last_contact TIMESTAMP,
            total_messages INT DEFAULT 0,
            status VARCHAR(20) DEFAULT 'active'
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_phone VARCHAR(20),
            direction VARCHAR(10),
            content TEXT,
            msg_type VARCHAR(20),
            routing_path VARCHAR(50),
            latency_ms INT,
            fallback_used INTEGER DEFAULT 0,
            timestamp TIMESTAMP DEFAULT (datetime('now')),
            wamid VARCHAR(100) UNIQUE
        );

        CREATE TABLE IF NOT EXISTS bot_settings (
            key VARCHAR(100) PRIMARY KEY,
            value TEXT,
            updated_at TIMESTAMP DEFAULT (datetime('now')),
            updated_by VARCHAR(50)
        );

        CREATE TABLE IF NOT EXISTS repair_faq (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device VARCHAR(100),
            issue VARCHAR(200),
            price_min INT,
            price_max INT,
            turnaround VARCHAR(50),
            active INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS jobs (
            id VARCHAR(20) PRIMARY KEY,
            customer_phone VARCHAR(20),
            device VARCHAR(100),
            issue TEXT,
            status VARCHAR(30),
            created_at TIMESTAMP DEFAULT (datetime('now')),
            expected_done TIMESTAMP,
            completed_at TIMESTAMP,
            notes TEXT
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_wamid ON messages(wamid);
        CREATE INDEX IF NOT EXISTS idx_messages_customer ON messages(customer_phone);
        CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp);
    """)
    conn.close()
    log.info(f"✅ Database initialized at {DB_PATH}")


# ── Async Logging ─────────────────────────────────────────────────

async def log_inbound(customer_phone: str, content: str, msg_type: str, wamid: str):
    """Log an inbound (customer → bot) message."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _save_inbound, customer_phone, content, wamid)
    broadcast_ws("inbound_message", {
        "phone": customer_phone,
        "content": content,
        "msg_type": msg_type,
        "wamid": wamid,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


async def log_outbound(customer_phone: str, content: str, routing_path: str,
                       latency_ms: int, fallback_used: bool, wamid: str = None):
    """Log an outbound (bot → customer) message."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _save_outbound, customer_phone, content,
                               routing_path, latency_ms, int(fallback_used), wamid or f"out_{datetime.now().timestamp()}")
    broadcast_ws("outbound_message", {
        "phone": customer_phone,
        "content": content,
        "routing_path": routing_path,
        "latency_ms": latency_ms,
        "fallback_used": fallback_used,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


def _save_inbound(phone: str, content: str, wamid: str):
    """Sync: Save inbound message + upsert customer."""
    conn = _get_db()
    try:
        conn.execute(
            "INSERT INTO messages (customer_phone, direction, content, msg_type, wamid) VALUES (?,?,?,?,?)",
            (phone, "inbound", content, "text", wamid)
        )
        conn.execute("""
            INSERT INTO customers (phone, name, first_contact, total_messages)
            VALUES (?, ?, datetime('now'), 1)
            ON CONFLICT(phone) DO UPDATE SET
                last_contact = datetime('now'),
                total_messages = total_messages + 1
        """, (phone, content[:20]))
        conn.commit()
    except sqlite3.IntegrityError:
        pass  # Duplicate wamid, skip
    finally:
        conn.close()


def _save_outbound(phone: str, content: str, routing: str, latency: int, fallback: int, wamid: str):
    """Sync: Save outbound message."""
    conn = _get_db()
    try:
        conn.execute(
            "INSERT INTO messages (customer_phone, direction, content, routing_path, latency_ms, fallback_used, wamid) VALUES (?,?,?,?,?,?,?)",
            (phone, "outbound", content, routing, latency, fallback, wamid)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    finally:
        conn.close()


# ── Query Helpers ─────────────────────────────────────────────────

async def get_recent_messages(limit: int = 50) -> list:
    """Get recent messages for dashboard load."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _query_recent, limit)


async def get_customer_messages(phone: str, limit: int = 50) -> list:
    """Get messages from a specific customer."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _query_customer, phone, limit)


async def get_customer_list() -> list:
    """Get all customers sorted by last contact."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _query_customers)


async def get_stats() -> dict:
    """Get dashboard stats."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _query_stats)


def _query_recent(limit: int) -> list:
    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM messages ORDER BY timestamp DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _query_customer(phone: str, limit: int) -> list:
    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM messages WHERE customer_phone = ? ORDER BY timestamp ASC LIMIT ?",
        (phone, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _query_customers() -> list:
    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM customers ORDER BY last_contact DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _query_stats() -> dict:
    conn = _get_db()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    stats = {
        "total_inbound": conn.execute("SELECT COUNT(*) FROM messages WHERE direction='inbound'").fetchone()[0],
        "total_outbound": conn.execute("SELECT COUNT(*) FROM messages WHERE direction='outbound'").fetchone()[0],
        "today_inbound": conn.execute(
            "SELECT COUNT(*) FROM messages WHERE direction='inbound' AND date(timestamp)=?", (today,)
        ).fetchone()[0],
        "total_customers": conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0],
        "avg_latency": conn.execute(
            "SELECT AVG(latency_ms) FROM messages WHERE latency_ms > 0"
        ).fetchone()[0] or 0,
        "fallback_count": conn.execute(
            "SELECT COUNT(*) FROM messages WHERE fallback_used=1"
        ).fetchone()[0],
    }
    conn.close()
    return stats
