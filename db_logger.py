"""
db_logger.py — Async SQLite logging for WhatsApp messages + WebSocket broadcast.
Fast, non-blocking, zero config. Data persists across restarts.
"""

import os
import json
import asyncio
import sqlite3
import logging
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Optional

log = logging.getLogger("hafjet-whatsapp.db")

# ── Config ─────────────────────────────────────────────────────────
# In Azure: /home/data/bot_data.db (persistent across redeploys)
# In local: ~/.hermes/whatsapp-bot/bot_data.db
_is_azure = bool(os.environ.get("WEBSITE_SITE_NAME"))
if _is_azure:
    DB_PATH = "/home/data/bot_data.db"
    os.makedirs("/home/data", exist_ok=True)
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
            status VARCHAR(20) DEFAULT 'active',
            escalated_at TIMESTAMP,
            resolved_at TIMESTAMP,
            handoff_at TIMESTAMP,
            assigned_to VARCHAR(50),
            note TEXT
        );
    """)

    # Migration: add new columns if missing from old DB
    # SQLite doesn't support IF NOT EXISTS for ALTER TABLE
    for col in ["escalated_at TIMESTAMP", "resolved_at TIMESTAMP", "handoff_at TIMESTAMP", "assigned_to VARCHAR(50)", "note TEXT", "tags TEXT DEFAULT ''"]:
        try:
            conn.execute(f"ALTER TABLE customers ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass  # Column already exists

    # Migration v2.1.1: escalation + bot pause fields
    for col in ["escalation_notified INTEGER DEFAULT 0", "bot_paused INTEGER DEFAULT 0"]:
        try:
            conn.execute(f"ALTER TABLE customers ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass

    # Migration v2.1.1: staff WhatsApp number for notifications
    for col in ["whatsapp_number VARCHAR(20) DEFAULT ''"]:
        try:
            conn.execute(f"ALTER TABLE staff ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass

    conn.executescript("""
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

        CREATE TABLE IF NOT EXISTS blast_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message TEXT,
            recipients TEXT,  -- 'all' OR comma-separated phones
            total_recipients INT,
            sent_count INT DEFAULT 0,
            failed_count INT DEFAULT 0,
            status VARCHAR(20) DEFAULT 'pending',  -- pending/sending/sent/failed
            schedule_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT (datetime('now')),

            sent_at TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS keyword_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword TEXT NOT NULL,
            reply TEXT NOT NULL,
            priority INTEGER DEFAULT 10,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            match_type TEXT DEFAULT 'contains'
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

        CREATE TABLE IF NOT EXISTS staff (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT DEFAULT 'agent',
            status TEXT DEFAULT 'offline',
            assigned_phone TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_staff_email ON staff(email);
    """)
    # Seed default admin if no staff exists
    cur = conn.execute("SELECT COUNT(*) FROM staff")
    if cur.fetchone()[0] == 0:
        default_pwd = hashlib.sha256("admin123".encode()).hexdigest()
        conn.execute(
            "INSERT INTO staff (name, email, password_hash, role, status) VALUES (?, ?, ?, ?, ?)",
            ("Tuan Hafizi (Admin)", "hafizi@hafjet.com", default_pwd, "admin", "online")
        )
        conn.commit()
        log.info("✅ Default admin created: hafizi@hafjet.com / admin123")
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
    """Sync: Save outbound message + update customer last_contact."""
    conn = _get_db()
    try:
        conn.execute(
            "INSERT INTO messages (customer_phone, direction, content, routing_path, latency_ms, fallback_used, wamid) VALUES (?,?,?,?,?,?,?)",
            (phone, "outbound", content, routing, latency, fallback, wamid)
        )
        # Update customer's last_contact so inbox shows recent activity
        conn.execute(
            "UPDATE customers SET last_contact = datetime('now'), total_messages = total_messages + 1 WHERE phone = ?",
            (phone,)
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


async def get_stats(date_str: str = None) -> dict:
    """Get dashboard stats for a given date (default today)."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _query_stats, date_str)


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


def _query_stats(date_str: str = None) -> dict:
    conn = _get_db()
    if not date_str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    stats = {
        "total_inbound": conn.execute("SELECT COUNT(*) FROM messages WHERE direction='inbound'").fetchone()[0],
        "total_outbound": conn.execute("SELECT COUNT(*) FROM messages WHERE direction='outbound'").fetchone()[0],
        "today_inbound": conn.execute(
            "SELECT COUNT(*) FROM messages WHERE direction='inbound' AND date(timestamp)=?", (date_str,)
        ).fetchone()[0],
        "today_outbound": conn.execute(
            "SELECT COUNT(*) FROM messages WHERE direction='outbound' AND date(timestamp)=?", (date_str,)
        ).fetchone()[0],
        "total_customers": conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0],
        "avg_latency": conn.execute(
            "SELECT AVG(latency_ms) FROM messages WHERE latency_ms > 0"
        ).fetchone()[0] or 0,
        "today_avg_latency": conn.execute(
            "SELECT AVG(latency_ms) FROM messages WHERE direction='outbound' AND latency_ms > 0 AND date(timestamp)=?", (date_str,)
        ).fetchone()[0] or 0,
        "fallback_count": conn.execute(
            "SELECT COUNT(*) FROM messages WHERE fallback_used=1"
        ).fetchone()[0],
        "today_fallback_count": conn.execute(
            "SELECT COUNT(*) FROM messages WHERE fallback_used=1 AND date(timestamp)=?", (date_str,)
        ).fetchone()[0],
        "today_ai_calls": conn.execute(
            "SELECT COUNT(*) FROM messages WHERE routing_path='ai_query' AND date(timestamp)=?", (date_str,)
        ).fetchone()[0],
        "date": date_str,
    }
    conn.close()
    today_ai_calls = stats["today_ai_calls"] or 0
    today_fallback = stats["today_fallback_count"] or 0
    today_total_calls = today_ai_calls + today_fallback
    stats["today_fallback_rate"] = round((today_fallback / today_total_calls) * 100) if today_total_calls > 0 else 0
    return stats


# ═══════════════════════════════════════════════════════════════════
#  OPERATOR ACTIONS — Resolve, Escalate, Note
# ═══════════════════════════════════════════════════════════════════

def resolve_customer(phone: str, operator: str = "dashboard") -> dict:
    """Mark customer as resolved."""
    conn = _get_db()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "UPDATE customers SET status='resolved', resolved_at=?, assigned_to=? WHERE phone=?",
        (now, operator, phone)
    )
    conn.commit()
    row = conn.execute("SELECT * FROM customers WHERE phone=?", (phone,)).fetchone()
    conn.close()
    return dict(row) if row else {}


def escalate_customer(phone: str, operator: str = "dashboard") -> dict:
    """Mark customer as escalated to staff."""
    conn = _get_db()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "UPDATE customers SET status='escalated', escalated_at=?, assigned_to=? WHERE phone=?",
        (now, operator, phone)
    )
    conn.commit()
    row = conn.execute("SELECT * FROM customers WHERE phone=?", (phone,)).fetchone()
    conn.close()
    return dict(row) if row else {}


def update_customer_note(phone: str, note: str, operator: str = "dashboard") -> dict:
    """Add/update customer note."""
    conn = _get_db()
    conn.execute(
        "UPDATE customers SET note=?, assigned_to=? WHERE phone=?",
        (note, operator, phone)
    )
    conn.commit()
    row = conn.execute("SELECT * FROM customers WHERE phone=?", (phone,)).fetchone()
    conn.close()
    return dict(row) if row else {}


def get_customer_detail(phone: str) -> Optional[dict]:
    """Get single customer with full details."""
    conn = _get_db()
    row = conn.execute("SELECT * FROM customers WHERE phone=?", (phone,)).fetchone()
    conn.close()
    return dict(row) if row else None


# ═══════════════════════════════════════════════════════════════════
#  BLAST / BROADCAST
# ═══════════════════════════════════════════════════════════════════

def create_blast(message: str, recipients: str, total_recipients: int, schedule_at: str = None) -> int:
    """Create a new blast entry. Returns blast ID."""
    conn = _get_db()
    cur = conn.execute(
        "INSERT INTO blast_logs (message, recipients, total_recipients, schedule_at, status) VALUES (?,?,?,?,?)",
        (message, recipients, total_recipients, schedule_at, 'pending' if schedule_at else 'sending')
    )
    blast_id = cur.lastrowid
    conn.commit()
    conn.close()
    return blast_id


def update_blast_status(blast_id: int, status: str, sent_count: int = None, failed_count: int = None):
    """Update blast status & counts."""
    conn = _get_db()
    parts = ["status=?"]
    vals = [status]
    if sent_count is not None:
        parts.append("sent_count=?")
        vals.append(sent_count)
    if failed_count is not None:
        parts.append("failed_count=?")
        vals.append(failed_count)
    if status == "sent":
        parts.append("sent_at=datetime('now')")
    vals.append(blast_id)
    conn.execute(f"UPDATE blast_logs SET {', '.join(parts)} WHERE id=?", vals)
    conn.commit()
    conn.close()


def get_blast_history(limit: int = 50) -> list:
    """Get blast history."""
    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM blast_logs ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_customer_phones() -> list:
    """Get list of all customer phone numbers."""
    conn = _get_db()
    rows = conn.execute("SELECT phone FROM customers").fetchall()
    conn.close()
    return [r["phone"] for r in rows]


# ═══════════════════════════════════════════════════════════════════
#  CONTACTS / PELANGGAN
# ═══════════════════════════════════════════════════════════════════

def _compute_auto_tags(customer: dict) -> list:
    """Compute auto-tags based on customer data."""
    tags = []
    # "Baru" = first contact dalam 7 hari
    if customer.get("first_contact"):
        try:
            fc = datetime.strptime(customer["first_contact"], "%Y-%m-%d %H:%M:%S")
            if (datetime.now() - fc).days <= 7:
                tags.append("Baru")
        except (ValueError, TypeError):
            pass
    # "Ulangan" = lebih dari 1 conversation
    if (customer.get("total_messages") or 0) > 1:
        tags.append("Ulangan")
    # "Escalated" = pernah escalate
    if customer.get("escalated_at"):
        tags.append("Escalated")
    return tags


def get_contacts(tag: str = "", search: str = "", page: int = 1, limit: int = 20) -> dict:
    """Get paginated contact list with optional filters."""
    conn = _get_db()
    where_clauses = []
    params = []

    if tag:
        where_clauses.append("tags LIKE ?")
        params.append(f"%{tag}%")
    if search:
        where_clauses.append("(phone LIKE ? OR name LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])

    where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

    # Total count
    total = conn.execute(
        f"SELECT COUNT(*) FROM customers WHERE {where_sql}", params
    ).fetchone()[0]

    # Pagination
    offset = (page - 1) * limit
    rows = conn.execute(
        f"SELECT * FROM customers WHERE {where_sql} ORDER BY last_contact DESC LIMIT ? OFFSET ?",
        params + [limit, offset]
    ).fetchall()
    conn.close()

    contacts = []
    for r in rows:
        c = dict(r)
        # Compute auto-tags
        auto_tags = _compute_auto_tags(c)
        manual_tags = [t.strip() for t in c.get("tags", "").split(",") if t.strip()]
        c["auto_tags"] = auto_tags
        c["manual_tags"] = manual_tags
        c["all_tags"] = list(set(auto_tags + manual_tags))
        contacts.append(c)

    return {
        "contacts": contacts,
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": max(1, (total + limit - 1) // limit),
    }


def update_contact(phone: str, name: str = None, tags: str = None, note: str = None) -> dict:
    """Update contact name, tags, and/or notes."""
    conn = _get_db()
    updates = []
    params = []
    if name is not None:
        updates.append("name=?")
        params.append(name)
    if tags is not None:
        updates.append("tags=?")
        params.append(tags)
    if note is not None:
        updates.append("note=?")
        params.append(note)
    if updates:
        params.append(phone)
        conn.execute(f"UPDATE customers SET {', '.join(updates)} WHERE phone=?", params)
        conn.commit()
    row = conn.execute("SELECT * FROM customers WHERE phone=?", (phone,)).fetchone()
    conn.close()
    return dict(row) if row else {}


def export_contacts() -> str:
    """Export all contacts as CSV string."""
    conn = _get_db()
    rows = conn.execute(
        "SELECT phone, name, COALESCE(tags,'') as tags, COALESCE(note,'') as note, "
        "total_messages, first_contact, last_contact, status "
        "FROM customers ORDER BY last_contact DESC"
    ).fetchall()
    conn.close()

    import csv, io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Phone", "Name", "Tags", "Note", "Total Messages", "First Contact", "Last Contact", "Status"])
    for r in rows:
        writer.writerow([r["phone"], r["name"], r["tags"], r["note"],
                         r["total_messages"], r["first_contact"], r["last_contact"], r["status"]])
    return output.getvalue()


def import_contacts_csv(csv_text: str) -> dict:
    """Import contacts from CSV text. Returns { imported, updated, errors }."""
    import csv, io
    reader = csv.DictReader(io.StringIO(csv_text))
    imported = 0
    updated = 0
    errors = 0
    conn = _get_db()
    for row in reader:
        try:
            phone = row.get("Phone", "").strip()
            if not phone:
                errors += 1
                continue
            name = row.get("Name", "").strip()
            tags = row.get("Tags", "").strip()
            note = row.get("Note", "").strip()

            existing = conn.execute("SELECT id FROM customers WHERE phone=?", (phone,)).fetchone()
            if existing:
                if name:
                    conn.execute("UPDATE customers SET name=?, tags=?, note=? WHERE phone=?",
                                 (name, tags, note, phone))
                    updated += 1
                else:
                    conn.execute("UPDATE customers SET tags=?, note=? WHERE phone=?",
                                 (tags, note, phone))
                    updated += 1
            else:
                conn.execute(
                    "INSERT INTO customers (phone, name, tags, note, first_contact, total_messages) VALUES (?,?,?,?,datetime('now'),0)",
                    (phone, name, tags, note)
                )
                imported += 1
        except Exception:
            errors += 1
    conn.commit()
    conn.close()
    return {"imported": imported, "updated": updated, "errors": errors}


# ═══════════════════════════════════════════════════════════════════
#  ANALYTICS
# ═══════════════════════════════════════════════════════════════════

def get_analytics_overview() -> dict:
    """Get analytics overview for dashboard cards."""
    conn = _get_db()
    tz = timezone(timedelta(hours=8))
    now_local = datetime.now(tz)

    # Today in local (UTC+8)
    today_str = now_local.strftime("%Y-%m-%d")

    # This month / last month strings for SQLite
    this_month_prefix = now_local.strftime("%Y-%m")
    first_of_this_month = now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    first_of_last_month = (first_of_this_month - timedelta(days=1)).replace(day=1)
    last_month_prefix = first_of_last_month.strftime("%Y-%m")

    # Total conversations — unique customer_phone with at least 1 message
    total_conversations = conn.execute(
        "SELECT COUNT(DISTINCT customer_phone) FROM messages"
    ).fetchone()[0] or 0

    # New last 7 days
    seven_days_ago = (now_local - timedelta(days=7)).strftime("%Y-%m-%d")
    new_last_7_days = conn.execute(
        "SELECT COUNT(*) FROM customers WHERE first_contact >= ? AND first_contact IS NOT NULL",
        (seven_days_ago,)
    ).fetchone()[0] or 0

    # Messages this month / last month (use UTC timestamps + offset)
    # SQLite stores timestamps in UTC (from datetime.now(timezone.utc))
    # We match local day by doing date(timestamp, '+8 hours')
    messages_this_month = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE strftime('%Y-%m', timestamp, '+8 hours') = ?",
        (this_month_prefix,)
    ).fetchone()[0] or 0

    messages_last_month = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE strftime('%Y-%m', timestamp, '+8 hours') = ?",
        (last_month_prefix,)
    ).fetchone()[0] or 0

    # Percentage change
    if messages_last_month > 0:
        messages_change_pct = round(
            ((messages_this_month - messages_last_month) / messages_last_month) * 100, 1
        )
    elif messages_this_month > 0:
        messages_change_pct = 100.0
    else:
        messages_change_pct = 0.0

    # AI Reply Rate: outbound non-fallback / total inbound * 100
    total_inbound = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE direction='inbound'"
    ).fetchone()[0] or 0
    ai_replies = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE direction='outbound' AND fallback_used=0"
    ).fetchone()[0] or 0
    ai_reply_rate = round((ai_replies / total_inbound) * 100, 1) if total_inbound > 0 else 0.0

    # Today messages (all directions)
    today_messages = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE date(timestamp, '+8 hours') = ?",
        (today_str,)
    ).fetchone()[0] or 0

    # Escalation count
    escalation_count = conn.execute(
        "SELECT COUNT(*) FROM customers WHERE escalated_at IS NOT NULL"
    ).fetchone()[0] or 0

    conn.close()

    return {
        "total_conversations": total_conversations,
        "new_last_7_days": new_last_7_days,
        "messages_this_month": messages_this_month,
        "messages_last_month": messages_last_month,
        "messages_change_pct": messages_change_pct,
        "ai_reply_rate": ai_reply_rate,
        "today_messages": today_messages,
        "escalation_count": escalation_count,
    }


def get_analytics_chart(days: int = 7) -> dict:
    """Get daily messages in/out for chart."""
    conn = _get_db()
    tz = timezone(timedelta(hours=8))
    now_local = datetime.now(tz)

    labels = []
    messages_in = []
    messages_out = []

    for i in range(days - 1, -1, -1):
        d = now_local - timedelta(days=i)
        date_str = d.strftime("%Y-%m-%d")
        label = "Hari ini" if i == 0 else d.strftime("%-d %b")  # "3 Jul"

        in_count = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE date(timestamp, '+8 hours') = ? AND direction='inbound'",
            (date_str,)
        ).fetchone()[0] or 0

        out_count = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE date(timestamp, '+8 hours') = ? AND direction='outbound'",
            (date_str,)
        ).fetchone()[0] or 0

        labels.append(label)
        messages_in.append(in_count)
        messages_out.append(out_count)

    conn.close()
    return {"labels": labels, "messages_in": messages_in, "messages_out": messages_out}


# ═══════════════════════════════════════════════════════════════════
#  KEYWORD RULES
# ═══════════════════════════════════════════════════════════════════

def get_all_keywords() -> list:
    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM keyword_rules ORDER BY priority ASC, id ASC"
    ).fetchall()
    conn.close()
    keys = ["id", "keyword", "reply", "priority", "is_active", "created_at", "match_type"]
    return [dict(zip(keys, r)) for r in rows]


def create_keyword(keyword: str, reply: str, priority: int = 10, match_type: str = "contains") -> int:
    conn = _get_db()
    cur = conn.execute(
        "INSERT INTO keyword_rules (keyword, reply, priority, match_type) VALUES (?, ?, ?, ?)",
        (keyword, reply, priority, match_type),
    )
    conn.commit()
    conn.close()
    return cur.lastrowid


def update_keyword(rule_id: int, fields: dict) -> bool:
    if not fields:
        return False
    set_clause = ", ".join([f"{k} = ?" for k in fields])
    values = list(fields.values()) + [rule_id]
    conn = _get_db()
    conn.execute(f"UPDATE keyword_rules SET {set_clause} WHERE id = ?", values)
    conn.commit()
    conn.close()
    return True


def delete_keyword(rule_id: int) -> bool:
    conn = _get_db()
    cur = conn.execute("DELETE FROM keyword_rules WHERE id = ?", (rule_id,))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def toggle_keyword(rule_id: int) -> dict:
    conn = _get_db()
    row = conn.execute("SELECT id, is_active FROM keyword_rules WHERE id = ?", (rule_id,)).fetchone()
    if not row:
        conn.close()
        return {"status": "error", "error": "Keyword rule not found"}
    new_status = 0 if row[1] else 1
    conn.execute("UPDATE keyword_rules SET is_active = ? WHERE id = ?", (new_status, rule_id))
    conn.commit()
    conn.close()
    return {"status": "ok", "is_active": bool(new_status)}


def reorder_keywords(id_list: list) -> bool:
    conn = _get_db()
    for idx, rule_id in enumerate(id_list, start=1):
        conn.execute("UPDATE keyword_rules SET priority = ? WHERE id = ?", (idx, rule_id))
    conn.commit()
    conn.close()
    return True


# ═══════════════════════════════════════════════════════════════════
#  STAFF
# ═══════════════════════════════════════════════════════════════════

def create_staff(name: str, email: str, password_hash: str, role: str = "agent") -> int:
    conn = _get_db()
    cur = conn.execute(
        "INSERT INTO staff (name, email, password_hash, role) VALUES (?, ?, ?, ?)",
        (name, email, password_hash, role),
    )
    conn.commit()
    conn.close()
    return cur.lastrowid


def get_staff_by_email(email: str) -> Optional[dict]:
    conn = _get_db()
    row = conn.execute("SELECT * FROM staff WHERE email = ?", (email,)).fetchone()
    conn.close()
    if not row:
        return None
    keys = ["id", "name", "email", "password_hash", "role", "status", "assigned_phone", "created_at", "last_login"]
    return dict(zip(keys, row))


def get_staff_by_id(staff_id: int) -> Optional[dict]:
    conn = _get_db()
    row = conn.execute("SELECT * FROM staff WHERE id = ?", (staff_id,)).fetchone()
    conn.close()
    if not row:
        return None
    keys = ["id", "name", "email", "password_hash", "role", "status", "assigned_phone", "created_at", "last_login"]
    return dict(zip(keys, row))


def get_all_staff() -> list:
    conn = _get_db()
    rows = conn.execute("SELECT id, name, email, role, status, assigned_phone, created_at, last_login FROM staff ORDER BY id ASC").fetchall()
    conn.close()
    keys = ["id", "name", "email", "role", "status", "assigned_phone", "created_at", "last_login"]
    return [dict(zip(keys, r)) for r in rows]


def update_staff_status(staff_id: int, status: str) -> bool:
    conn = _get_db()
    cur = conn.execute("UPDATE staff SET status = ? WHERE id = ?", (status, staff_id))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def assign_conversation(phone: str, staff_id: Optional[int]) -> bool:
    conn = _get_db()
    assigned_to = str(staff_id) if staff_id else None
    cur = conn.execute("UPDATE customers SET assigned_to = ? WHERE phone = ?", (assigned_to, phone))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def get_assigned_phone(staff_id: int) -> Optional[str]:
    conn = _get_db()
    row = conn.execute("SELECT assigned_phone FROM staff WHERE id = ?", (staff_id,)).fetchone()
    conn.close()
    return row[0] if row else None


def get_staff_whatsapp(staff_id: int) -> Optional[str]:
    """Get staff's WhatsApp notification number."""
    conn = _get_db()
    row = conn.execute("SELECT whatsapp_number FROM staff WHERE id = ?", (staff_id,)).fetchone()
    conn.close()
    return row[0] if row else None


def update_conversation_status(phone: str, new_status: str) -> dict:
    """Update conversation status + bot_paused. Returns updated customer dict."""
    paused = 1 if new_status in ("escalated", "assigned") else 0
    conn = _get_db()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    if new_status == "escalated":
        conn.execute(
            "UPDATE customers SET status=?, escalated_at=?, bot_paused=? WHERE phone=?",
            (new_status, now, paused, phone)
        )
    elif new_status == "resolved":
        conn.execute(
            "UPDATE customers SET status=?, resolved_at=?, bot_paused=? WHERE phone=?",
            (new_status, now, paused, phone)
        )
    else:
        conn.execute(
            "UPDATE customers SET status=?, bot_paused=? WHERE phone=?",
            (new_status, paused, phone)
        )
    conn.commit()
    row = conn.execute("SELECT * FROM customers WHERE phone=?", (phone,)).fetchone()
    conn.close()
    return dict(row) if row else {}


def set_escalation_notified(phone: str) -> None:
    """Mark that escalation notification has been sent for this conversation."""
    conn = _get_db()
    conn.execute("UPDATE customers SET escalation_notified=1 WHERE phone=?", (phone,))
    conn.commit()
    conn.close()


def get_inbox_conversations(filter_type: str = "all", staff_id: Optional[int] = None) -> list:
    conn = _get_db()
    base_query = """
        SELECT c.phone, c.name, c.last_contact, c.total_messages, c.tags, c.note,
               c.assigned_to, c.status, c.bot_paused, c.escalated_at, c.resolved_at,
               s.name as agent_name, s.status as agent_status
        FROM customers c
        LEFT JOIN staff s ON s.assigned_phone = c.phone
    """
    params = ()
    if filter_type == "me" and staff_id:
        base_query += " WHERE c.assigned_to = ?"
        params = (str(staff_id),)
    elif filter_type == "unassigned":
        base_query += " WHERE c.assigned_to IS NULL OR c.assigned_to = ''"
    elif filter_type == "escalated":
        base_query += " WHERE c.status = 'escalated'"
    elif filter_type == "resolved":
        base_query += " WHERE c.status = 'resolved'"
    rows = conn.execute(base_query + " ORDER BY c.last_contact DESC", params).fetchall()
    conn.close()
    keys = ["phone", "name", "last_contact", "total_messages", "tags", "note", "assigned_to", "status", "bot_paused", "escalated_at", "resolved_at", "agent_name", "agent_status"]
    return [dict(zip(keys, r)) for r in rows]
