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

        CREATE TABLE IF NOT EXISTS spx_self_collection_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id VARCHAR(50) NOT NULL,
            spx_tracking_number VARCHAR(50) UNIQUE NOT NULL,
            scan_tracking_number VARCHAR(50),
            recipient_name VARCHAR(100),
            recipient_phone VARCHAR(20),
            payment_method VARCHAR(50),
            transaction_method VARCHAR(50),
            transaction_amount VARCHAR(20),
            storage_id VARCHAR(50),
            inbound_time TIMESTAMP,
            outbound_time TIMESTAMP,
            collect_by_date TIMESTAMP NOT NULL,
            spx_status VARCHAR(50) DEFAULT 'ReadyForCollection',
            hafjet_reminder_state VARCHAR(20) DEFAULT 'Pending',
            last_reminder_sent_at TIMESTAMP,
            is_paused INTEGER DEFAULT 0,
            reminder_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT (datetime('now')),
            updated_at TIMESTAMP DEFAULT (datetime('now')),
            notes TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_spx_tracking ON spx_self_collection_orders(spx_tracking_number);
        CREATE INDEX IF NOT EXISTS idx_spx_phone ON spx_self_collection_orders(recipient_phone);
        CREATE INDEX IF NOT EXISTS idx_spx_deadline ON spx_self_collection_orders(collect_by_date);
        CREATE INDEX IF NOT EXISTS idx_spx_status ON spx_self_collection_orders(spx_status);
        CREATE INDEX IF NOT EXISTS idx_spx_reminder_state ON spx_self_collection_orders(hafjet_reminder_state);
        CREATE INDEX IF NOT EXISTS idx_spx_storage ON spx_self_collection_orders(storage_id);

        CREATE TABLE IF NOT EXISTS spx_session (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            cookies TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS spx_sync_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            state_json TEXT NOT NULL DEFAULT '{}',
            updated_at TIMESTAMP DEFAULT (datetime('now'))
        );
    """)

    # SPX v2 schema migration: add entity_id; make recipient_phone nullable
    _need_rebuild = False
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(spx_self_collection_orders)").fetchall()]
        if "entity_id" not in cols:
            _need_rebuild = True
    except sqlite3.OperationalError:
        _need_rebuild = True

    if _need_rebuild:
        conn.execute("DROP TABLE IF EXISTS spx_self_collection_orders")
        conn.execute("""
            CREATE TABLE spx_self_collection_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id VARCHAR(50) NOT NULL,
                spx_tracking_number VARCHAR(50) UNIQUE NOT NULL,
                scan_tracking_number VARCHAR(50),
                recipient_name VARCHAR(100),
                recipient_phone VARCHAR(20),
                payment_method VARCHAR(50),
                transaction_method VARCHAR(50),
                transaction_amount VARCHAR(20),
                storage_id VARCHAR(50),
                inbound_time TIMESTAMP,
                outbound_time TIMESTAMP,
                collect_by_date TIMESTAMP NOT NULL,
                spx_status VARCHAR(50) DEFAULT 'ReadyForCollection',
                hafjet_reminder_state VARCHAR(20) DEFAULT 'Pending',
                last_reminder_sent_at TIMESTAMP,
                is_paused INTEGER DEFAULT 0,
                reminder_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT (datetime('now')),
                updated_at TIMESTAMP DEFAULT (datetime('now')),
                notes TEXT
            )
        """)
        conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_spx_tracking ON spx_self_collection_orders(spx_tracking_number);
            CREATE INDEX IF NOT EXISTS idx_spx_phone ON spx_self_collection_orders(recipient_phone);
            CREATE INDEX IF NOT EXISTS idx_spx_deadline ON spx_self_collection_orders(collect_by_date);
            CREATE INDEX IF NOT EXISTS idx_spx_status ON spx_self_collection_orders(spx_status);
            CREATE INDEX IF NOT EXISTS idx_spx_reminder_state ON spx_self_collection_orders(hafjet_reminder_state);
            CREATE INDEX IF NOT EXISTS idx_spx_storage ON spx_self_collection_orders(storage_id);
        """)
        conn.commit()
    # Seed default spx_session row id=1 if missing
    cur = conn.execute("SELECT COUNT(*) FROM spx_session")
    if cur.fetchone()[0] == 0:
        conn.execute("INSERT OR IGNORE INTO spx_session (id, cookies) VALUES (1, '')")
        conn.commit()

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
#  SPX SELF-COLLECTION ORDERS
# ═══════════════════════════════════════════════════════════════════

VALID_SPX_STATUSES = {
    "Ready For Collection", "Remind1", "Remind2", "Remind3",
    "Remind4", "Collected", "Collection Failed",
    "Return_Outbound", "Return_Packing", "SP_Inbound",
}


def _normalize_phone(phone) -> str:
    """Normalize phone number to international format.
    Defensively handles non-string inputs (None, datetime, int, etc.)."""
    if phone is None:
        return ""
    phone = str(phone).strip().replace(" ", "").replace("-", "")
    if not phone:
        return ""
    if not phone.startswith("+"):
        if phone.startswith("60"):
            phone = "+" + phone
        elif phone.startswith("0"):
            phone = "+60" + phone[1:]
    return phone


def upsert_spx_order(data: dict) -> dict:
    """Upsert one SPX order row by spx_tracking_number.
    Never overwrite recipient_phone if order already has one.
    Returns row dict.
    """
    conn = _get_db()
    try:
        # SAFETY: convert ALL values to strings upfront to prevent .strip() on non-str
        for _k in list(data.keys()):
            _v = data[_k]
            if _v is not None and not isinstance(_v, (str, int, float)):
                data[_k] = str(_v)

        # Debug: log ALL field types before upsert
        for _k in data:
            _v = data[_k]
            if _v is not None and not isinstance(_v, (str, type(None), int, float)):
                import logging
                logging.getLogger("hafjet-whatsapp.db").warning("🔥 NON-STRING field %r type=%s val=%r", _k, type(_v).__name__, str(_v)[:120])
        # Debug: log types of incoming fields
        for _k in ("inbound_time", "outbound_time", "collect_by_date", "entity_id", "spx_tracking_number", "spx_status", "storage_id"):
            _v = data.get(_k)
            if _v is not None and not isinstance(_v, (str, type(None))):
                import logging
                logging.getLogger("db_logger").warning("⛔ SPX field %r type=%s val=%r", _k, type(_v).__name__, str(_v)[:80])

        existing = conn.execute(
            "SELECT id, recipient_phone FROM spx_self_collection_orders WHERE spx_tracking_number = ?",
            (_safe_str(data.get("spx_tracking_number", "")),),
        ).fetchone()

        phone = data.get("recipient_phone")
        if phone:
            phone = _normalize_phone(phone)

        if existing:
            # Update fields; preserve existing phone if blank in payload
            phone_to_set = phone if phone else existing["recipient_phone"]
            conn.execute(
                """UPDATE spx_self_collection_orders
                   SET entity_id=?, scan_tracking_number=?, recipient_name=?,
                       recipient_phone=?, payment_method=?, transaction_method=?,
                       transaction_amount=?, storage_id=?, inbound_time=?, outbound_time=?,
                       collect_by_date=?, spx_status=?, updated_at=datetime('now')
                   WHERE spx_tracking_number=?""",
                (
                    data.get("entity_id"),
                    _safe_str(data.get("scan_tracking_number", "")) or None,
                    _safe_str(data.get("recipient_name", "")) or None,
                    phone_to_set,
                    _safe_str(data.get("payment_method", "")),
                    _safe_str(data.get("transaction_method", "")),
                    _safe_str(data.get("transaction_amount", "")),
                    _safe_str(data.get("storage_id", "")),
                    _parse_sqlite_dt(data.get("inbound_time")),
                    _parse_sqlite_dt(data.get("outbound_time")),
                    _parse_sqlite_dt(data.get("collect_by_date")),
                    _safe_str(data.get("spx_status", "ReadyForCollection")),
                    _safe_str(data.get("spx_tracking_number", "")),
                ),
            )
            row = conn.execute(
                "SELECT * FROM spx_self_collection_orders WHERE spx_tracking_number = ?",
                (_safe_str(data.get("spx_tracking_number", "")),),
            ).fetchone()
        else:
            conn.execute(
                """INSERT INTO spx_self_collection_orders
                   (entity_id, spx_tracking_number, scan_tracking_number, recipient_name,
                    recipient_phone, payment_method, transaction_method,
                    transaction_amount, storage_id, inbound_time, outbound_time,
                    collect_by_date, spx_status, hafjet_reminder_state, notes)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    data.get("entity_id"),
                    _safe_str(data.get("spx_tracking_number", "")),
                    _safe_str(data.get("scan_tracking_number", "")) or None,
                    _safe_str(data.get("recipient_name", "")) or None,
                    phone,
                    _safe_str(data.get("payment_method", "")),
                    _safe_str(data.get("transaction_method", "")),
                    _safe_str(data.get("transaction_amount", "")),
                    _safe_str(data.get("storage_id", "")),
                    _parse_sqlite_dt(data.get("inbound_time")),
                    _parse_sqlite_dt(data.get("outbound_time")),
                    _parse_sqlite_dt(data.get("collect_by_date")),
                    _safe_str(data.get("spx_status", "ReadyForCollection")),
                    data.get("hafjet_reminder_state", "Pending"),
                    _safe_str(data.get("notes", "")),
                ),
            )
            row = conn.execute(
                "SELECT * FROM spx_self_collection_orders WHERE spx_tracking_number = ?",
                (_safe_str(data.get("spx_tracking_number", "")),),
            ).fetchone()
        conn.commit()
        return dict(row) if row else None
    except Exception as e:
        conn.rollback()
        import traceback
        log.error(f"❌ upsert_spx_order exception: {e}", exc_info=True)
        raise e
    finally:
        conn.close()


def create_spx_order(data: dict) -> dict:
    """Alias for upsert_spx_order for backward compatibility."""
    return upsert_spx_order(data)


def import_spx_csv(csv_text: str) -> dict:
    """Import SPX orders from CSV text. Returns {imported, updated, skipped, errors}."""
    imported = 0
    updated = 0
    skipped = 0
    errors = []

    conn = _get_db()
    for idx, row in enumerate(csv_text.splitlines()[1:], start=2):
        cols = [c.strip() for c in row.split(",")]
        if len(cols) < 11:
            errors.append({"row": idx, "reason": "Not enough columns"})
            skipped += 1
            continue

        try:
            spx_id = cols[0]
            scan_id = cols[1] or None
            name = cols[2] or None
            phone = _normalize_phone(cols[3])
            payment = cols[4] or None
            txn_method = cols[5] or None
            txn_amount = cols[6] or None
            storage = cols[7] or None
            inbound = _parse_sqlite_dt(cols[8])
            outbound = _parse_sqlite_dt(cols[9])
            collect_by = _parse_sqlite_dt(cols[10])
            status = cols[11].strip() if len(cols) > 11 else "Ready For Collection"
            notes = cols[12].strip() if len(cols) > 12 else None

            if not spx_id:
                errors.append({"row": idx, "reason": "Missing SPX Tracking Number"})
                skipped += 1
                continue
            if not collect_by:
                errors.append({"row": idx, "reason": "Invalid Collect by Date"})
                skipped += 1
                continue
            if status not in VALID_SPX_STATUSES:
                errors.append({"row": idx, "reason": f"Invalid status: {status}"})
                skipped += 1
                continue

            existing = conn.execute(
                "SELECT id FROM spx_self_collection_orders WHERE spx_tracking_number = ?",
                (spx_id,),
            ).fetchone()

            if existing:
                conn.execute(
                    """UPDATE spx_self_collection_orders
                       SET scan_tracking_number=?, recipient_name=?, recipient_phone=?,
                           payment_method=?, transaction_method=?, transaction_amount=?,
                           storage_id=?, inbound_time=?, outbound_time=?,
                           collect_by_date=?, spx_status=?, notes=?, updated_at=datetime('now')
                       WHERE spx_tracking_number=?""",
                    (
                        scan_id, name, phone, payment, txn_method,
                        txn_amount, storage, inbound, outbound,
                        collect_by, status, notes, spx_id,
                    ),
                )
                updated += 1
            else:
                conn.execute(
                    """INSERT INTO spx_self_collection_orders
                       (spx_tracking_number, scan_tracking_number, recipient_name,
                        recipient_phone, payment_method, transaction_method,
                        transaction_amount, storage_id, inbound_time, outbound_time,
                        collect_by_date, spx_status, hafjet_reminder_state, notes)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        spx_id, scan_id, name, phone, payment, txn_method,
                        txn_amount, storage, inbound, outbound,
                        collect_by, status, "Pending", notes,
                    ),
                )
                imported += 1
        except Exception:
            errors.append({"row": idx, "reason": "Unexpected error"})
            skipped += 1

    conn.commit()
    conn.close()
    return {"imported": imported, "updated": updated, "skipped": skipped, "errors": errors[:100]}


def bulk_map_phones(text: str) -> dict:
    """Parse 'tracking_number phone' pairs (one per line).
    Returns {"mapped": int, "skipped": int, "not_found": int, "errors": list}.
    Idempotent — safe to run multiple times with same data.
    """
    mapped = skipped = not_found = 0
    errors = []
    conn = _get_db()
    for idx, line in enumerate(text.strip().splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue  # skip empty / comment lines
        parts = stripped.split()
        if len(parts) < 2:
            errors.append({"row": idx, "reason": f"Need at least tracking + phone, got {len(parts)} parts"})
            skipped += 1
            continue
        tracking = parts[0].strip().upper()
        phone_raw = parts[-1].strip()  # last token = phone
        phone = _normalize_phone(phone_raw)
        if not phone:
            errors.append({"row": idx, "reason": f"Invalid phone: {phone_raw}"})
            skipped += 1
            continue
        cur = conn.execute(
            "SELECT id, recipient_phone FROM spx_self_collection_orders WHERE spx_tracking_number=?",
            (tracking,),
        ).fetchone()
        if not cur:
            errors.append({"row": idx, "reason": f"Tracking not found: {tracking}"})
            not_found += 1
            continue
        # Update only if phone is new or different
        if cur["recipient_phone"] != phone:
            conn.execute(
                "UPDATE spx_self_collection_orders SET recipient_phone=?, updated_at=datetime('now') WHERE id=?",
                (phone, cur["id"]),
            )
        mapped += 1
    conn.commit()
    conn.close()
    return {"mapped": mapped, "skipped": skipped, "not_found": not_found, "errors": errors[:100]}


def save_spx_cookies(cookies_str: str):
    conn = _get_db()
    conn.execute(
        "INSERT INTO spx_session (id, cookies, updated_at) VALUES (1, ?, datetime('now')) "
        "ON CONFLICT(id) DO UPDATE SET cookies=excluded.cookies, updated_at=excluded.updated_at",
        (cookies_str,),
    )
    conn.commit()
    conn.close()


def get_spx_cookies() -> str | None:
    conn = _get_db()
    row = conn.execute("SELECT cookies FROM spx_session WHERE id=1").fetchone()
    conn.close()
    return row["cookies"] if row else None


def get_orders_missing_phone(limit: int = 500, offset: int = 0) -> list:
    conn = _get_db()
    rows = conn.execute(
        """SELECT * FROM spx_self_collection_orders
           WHERE recipient_phone IS NULL OR recipient_phone = ''
           ORDER BY collect_by_date ASC
           LIMIT ? OFFSET ?""",
        (limit, offset),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_order_phone(order_id: int, phone_str: str):
    phone = _normalize_phone(phone_str)
    conn = _get_db()
    conn.execute(
        "UPDATE spx_self_collection_orders SET recipient_phone=?, updated_at=datetime('now') WHERE id=?",
        (phone, order_id),
    )
    conn.commit()
    conn.close()


def count_spx_reminders_sent_today() -> int:
    conn = _get_db()
    row = conn.execute(
        """SELECT COUNT(*) FROM messages
           WHERE direction='outbound' AND routing_path='spx_reminder'
             AND date(timestamp)=date('now')"""
    ).fetchone()
    conn.close()
    return row[0] if row else 0


def _safe_str(value) -> str:
    """Convert any value to string safely; returns empty string on None."""
    try:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, bytes):
            return value.decode().strip()
        return str(value)
    except Exception as e:
        import logging
        logging.getLogger("db_logger").error(f"🔥 _safe_str({type(value).__name__}) failed: {e}")
        return str(value)


def _parse_sqlite_dt(value):
    """Parse datetime from string or datetime object to SQLite string format."""
    if value is None:
        return None
    if isinstance(value, datetime):
        import logging
        logging.getLogger("hafjet-whatsapp.db").info("✅ _parse_sqlite_dt got datetime obj: %s", value.strftime("%Y-%m-%d %H:%M:%S"))
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt).strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
        return s
    return None


def get_spx_order_by_tracking(spx_tracking_number: str) -> dict:
    conn = _get_db()
    row = conn.execute(
        "SELECT * FROM spx_self_collection_orders WHERE spx_tracking_number = ?",
        (spx_tracking_number,),
    ).fetchone()
    conn.close()
    return dict(row) if row else {}


def get_spx_due_orders(limit: int = 100, offset: int = 0) -> list:
    """Return orders eligible for reminder processing."""
    conn = _get_db()
    rows = conn.execute(
        """SELECT * FROM spx_self_collection_orders
           WHERE hafjet_reminder_state NOT IN ('Completed', 'CollectionFailed')
             AND is_paused = 0
             AND recipient_phone IS NOT NULL
             AND recipient_phone != ''
           ORDER BY collect_by_date ASC
           LIMIT ? OFFSET ?""",
        (limit, offset),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_spx_orders(limit: int = 1000, offset: int = 0) -> list:
    """Return ALL SPX orders — no phone/status filter, for dashboard listing."""
    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM spx_self_collection_orders ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_spx_reminder_state(order_id: int, state: str, last_sent_at: str = None) -> dict:
    conn = _get_db()
    conn.execute(
        """UPDATE spx_self_collection_orders
           SET hafjet_reminder_state = ?, last_reminder_sent_at = ?, updated_at = datetime('now')
           WHERE id = ?""",
        (state, last_sent_at, order_id),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM spx_self_collection_orders WHERE id = ?", (order_id,)).fetchone()
    conn.close()
    return dict(row) if row else {}


def get_spx_stats() -> dict:
    conn = _get_db()
    rows = conn.execute(
        """SELECT spx_status, hafjet_reminder_state, COUNT(*) as cnt
           FROM spx_self_collection_orders
           GROUP BY spx_status, hafjet_reminder_state"""
    ).fetchall()
    conn.close()
    total = 0
    by_status = {}
    for r in rows:
        key = f"{r['spx_status']}|{r['hafjet_reminder_state']}"
        cnt = r["cnt"] or 0
        total += cnt
        by_status[key] = cnt
    return {"total": total, "by_status": by_status, "rows": [dict(r) for r in rows]}


# ═══════════════════════════════════════════════════════════════════
#  ANALYTICS
# ═══════════════════════════════════════════════════════════════════

def get_analytics_overview(start_date: str = None, end_date: str = None) -> dict:
    """Get analytics overview for dashboard cards.
    Supports optional date range (YYYY-MM-DD)."""
    conn = _get_db()
    tz = timezone(timedelta(hours=8))
    now_local = datetime.now(tz)

    today_str = now_local.strftime("%Y-%m-%d")
    this_month_prefix = now_local.strftime("%Y-%m")
    first_of_this_month = now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    first_of_last_month = (first_of_this_month - timedelta(days=1)).replace(day=1)
    last_month_prefix = first_of_last_month.strftime("%Y-%m")
    seven_days_ago = (now_local - timedelta(days=7)).strftime("%Y-%m-%d")

    # Build date clause for filtering
    date_where = ""
    date_params = []
    if start_date:
        date_where += " AND timestamp >= ?"
        date_params.append(start_date)
    if end_date:
        date_where += " AND timestamp <= ?"
        date_params.append(end_date + " 23:59:59")

    # Total conversations — count distinct customer_phone with at least 1 message
    total_conversations = conn.execute(
        "SELECT COUNT(DISTINCT customer_phone) FROM messages"
    ).fetchone()[0] or 0

    # Inbound / Outbound totals
    inbound_total = conn.execute(
        f"SELECT COUNT(*) FROM messages WHERE direction='inbound'{date_where}",
        date_params
    ).fetchone()[0] or 0
    outbound_total = conn.execute(
        f"SELECT COUNT(*) FROM messages WHERE direction='outbound'{date_where}",
        date_params
    ).fetchone()[0] or 0

    # New last 7 days
    new_last_7_days = conn.execute(
        "SELECT COUNT(*) FROM customers WHERE first_contact >= ? AND first_contact IS NOT NULL",
        (seven_days_ago,)
    ).fetchone()[0] or 0

    # Messages this / last month
    messages_this_month = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE strftime('%Y-%m', timestamp, '+8 hours') = ?",
        (this_month_prefix,)
    ).fetchone()[0] or 0
    messages_last_month = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE strftime('%Y-%m', timestamp, '+8 hours') = ?",
        (last_month_prefix,)
    ).fetchone()[0] or 0

    if messages_last_month > 0:
        messages_change_pct = round(
            ((messages_this_month - messages_last_month) / messages_last_month) * 100, 1
        )
    elif messages_this_month > 0:
        messages_change_pct = 100.0
    else:
        messages_change_pct = 0.0

    # AI Reply Rate
    total_inbound = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE direction='inbound'"
    ).fetchone()[0] or 0
    ai_replies = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE direction='outbound' AND fallback_used=0"
    ).fetchone()[0] or 0
    ai_reply_rate = round((ai_replies / total_inbound) * 100, 1) if total_inbound > 0 else 0.0

    # Today messages
    today_messages = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE date(timestamp, '+8 hours') = ?",
        (today_str,)
    ).fetchone()[0] or 0

    # Escalation count
    escalation_count = conn.execute(
        "SELECT COUNT(*) FROM customers WHERE escalated_at IS NOT NULL"
    ).fetchone()[0] or 0

    # NEW: Resolved count
    resolved_count = conn.execute(
        "SELECT COUNT(*) FROM customers WHERE resolved_at IS NOT NULL"
    ).fetchone()[0] or 0

    # NEW: Average response time (seconds) — per-conversation avg of min inbound→outbound gap
    # First, get the first inbound and first outbound timestamps per customer
    avg_first_response = 0
    avg_response_time = 0
    avg_resolution_time = 0

    # Average first response time: for conversations with at least 1 inbound + 1 outbound
    rows = conn.execute("""
        SELECT c.customer_phone,
               MIN(CASE WHEN c.direction='inbound' THEN c.timestamp END) as first_in,
               MIN(CASE WHEN c.direction='outbound' THEN c.timestamp END) as first_out
        FROM messages c
        GROUP BY c.customer_phone
        HAVING first_in IS NOT NULL AND first_out IS NOT NULL
    """).fetchall()
    if rows:
        diffs = []
        for r in rows:
            try:
                fi = datetime.fromisoformat(r[1])
                fo = datetime.fromisoformat(r[2])
                if fo > fi:
                    diffs.append((fo - fi).total_seconds())
            except (ValueError, TypeError):
                pass
        if diffs:
            avg_first_response = round(sum(diffs) / len(diffs))

    # Average resolution time: for customers with resolved_at
    rows2 = conn.execute("""
        SELECT first_contact, resolved_at
        FROM customers
        WHERE resolved_at IS NOT NULL AND first_contact IS NOT NULL
    """).fetchall()
    if rows2:
        diffs2 = []
        for r in rows2:
            try:
                fc = datetime.fromisoformat(r[0])
                ra = datetime.fromisoformat(r[1])
                if ra > fc:
                    diffs2.append((ra - fc).total_seconds())
            except (ValueError, TypeError):
                pass
        if diffs2:
            avg_resolution_time = round(sum(diffs2) / len(diffs2))

    conn.close()

    return {
        "total_conversations": total_conversations,
        "inbound_total": inbound_total,
        "outbound_total": outbound_total,
        "new_last_7_days": new_last_7_days,
        "messages_this_month": messages_this_month,
        "messages_last_month": messages_last_month,
        "messages_change_pct": messages_change_pct,
        "ai_reply_rate": ai_reply_rate,
        "today_messages": today_messages,
        "escalation_count": escalation_count,
        "resolved_count": resolved_count,
        "avg_first_response_time_sec": avg_first_response,
        "avg_response_time_sec": avg_response_time,
        "avg_resolution_time_sec": avg_resolution_time,
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


def get_response_time_stats(start_date: str = None, end_date: str = None) -> dict:
    """Calculate response time statistics.
    - first_response: avg seconds from first inbound to first outbound per conversation.
    - avg_response: avg seconds for all inbound→outbound pairs.
    - resolution: avg seconds from first_contact to resolved_at.
    """
    conn = _get_db()
    tz = timezone(timedelta(hours=8))

    # Build date clause
    date_where = ""
    date_params = []

    # Average first response time
    first_response_avg = 0
    avg_reply_avg = 0
    resolution_avg = 0

    rows = conn.execute("""
        SELECT customer_phone,
               MIN(CASE WHEN direction='inbound' THEN timestamp END) as first_in,
               MIN(CASE WHEN direction='outbound' THEN timestamp END) as first_out
        FROM messages
        GROUP BY customer_phone
        HAVING first_in IS NOT NULL AND first_out IS NOT NULL
    """).fetchall()
    diffs = []
    for r in rows:
        try:
            fi = datetime.fromisoformat(r[1])
            fo = datetime.fromisoformat(r[2])
            if fo > fi:
                diffs.append((fo - fi).total_seconds())
        except (ValueError, TypeError):
            pass
    if diffs:
        first_response_avg = round(sum(diffs) / len(diffs))

    # Average inbound→outbound reply time (all pairs within each conversation)
    # Use a lag window: for each conversation, find inbound-outbound pairs
    # Simplified: avg of all outbound timestamps minus most recent inbound
    pairs_diffs = []
    convs = conn.execute("SELECT DISTINCT customer_phone FROM messages ORDER BY customer_phone").fetchall()
    for (phone,) in convs:
        msgs = conn.execute(
            "SELECT direction, timestamp FROM messages WHERE customer_phone=? ORDER BY timestamp ASC",
            (phone,)
        ).fetchall()
        last_in = None
        for m in msgs:
            if m[0] == 'inbound':
                try:
                    last_in = datetime.fromisoformat(m[1])
                except (ValueError, TypeError):
                    last_in = None
            elif m[0] == 'outbound' and last_in is not None:
                try:
                    out_ts = datetime.fromisoformat(m[1])
                    diff = (out_ts - last_in).total_seconds()
                    if diff > 0 and diff < 86400:  # cap at 24h
                        pairs_diffs.append(diff)
                except (ValueError, TypeError):
                    pass
                last_in = None
    if pairs_diffs:
        avg_reply_avg = round(sum(pairs_diffs) / len(pairs_diffs))

    # Average resolution time
    rows2 = conn.execute("""
        SELECT first_contact, resolved_at
        FROM customers
        WHERE resolved_at IS NOT NULL AND first_contact IS NOT NULL
    """).fetchall()
    diffs2 = []
    for r in rows2:
        try:
            fc = datetime.fromisoformat(r[0])
            ra = datetime.fromisoformat(r[1])
            if ra > fc:
                diffs2.append((ra - fc).total_seconds())
        except (ValueError, TypeError):
            pass
    if diffs2:
        resolution_avg = round(sum(diffs2) / len(diffs2))

    conn.close()
    return {
        "avg_first_response_time_sec": first_response_avg,
        "avg_response_time_sec": avg_reply_avg,
        "avg_resolution_time_sec": resolution_avg,
    }


def get_agent_performance(staff_id: int = None, start_date: str = None, end_date: str = None) -> list:
    """Get performance metrics per staff member.
    Returns list of dicts with: id, name, conversations_handled, avg_response_sec,
    escalated_count, resolved_count.
    """
    conn = _get_db()
    date_where = ""
    date_params = []
    if start_date:
        date_where += " AND timestamp >= ?"
        date_params.append(start_date)
    if end_date:
        date_where += " AND timestamp <= ?"
        date_params.append(end_date + " 23:59:59")

    # Get all staff (or a specific one)
    if staff_id:
        staff_rows = conn.execute(
            "SELECT id, name, assigned_phone FROM staff WHERE id=?", (staff_id,)
        ).fetchall()
    else:
        staff_rows = conn.execute(
            "SELECT id, name, assigned_phone FROM staff ORDER BY name"
        ).fetchall()

    result = []
    for sid, sname, assigned_phone in staff_rows:
        if not assigned_phone:
            result.append({
                "id": sid,
                "name": sname,
                "conversations_handled": 0,
                "avg_response_time_sec": 0,
                "escalated_count": 0,
                "resolved_count": 0,
                "messages_sent": 0,
            })
            continue

        # Conversations assigned to this staff
        convs = conn.execute(
            "SELECT phone FROM customers WHERE assigned_to=?",
            (str(sid),)
        ).fetchall()
        conv_phones = [r[0] for r in convs if r[0]]

        # Count messages sent by this staff (outbound to their assigned customers)
        if conv_phones:
            placeholders = ",".join("?" for _ in conv_phones)
            msgs_sent = conn.execute(
                f"SELECT COUNT(*) FROM messages WHERE direction='outbound' AND customer_phone IN ({placeholders}){date_where}",
                conv_phones + date_params
            ).fetchone()[0] or 0
        else:
            msgs_sent = 0

        # Count escalated conversations assigned to this staff
        esc_count = conn.execute(
            "SELECT COUNT(*) FROM customers WHERE assigned_to=? AND escalated_at IS NOT NULL",
            (str(sid),)
        ).fetchone()[0] or 0

        # Count resolved conversations assigned to this staff
        res_count = conn.execute(
            "SELECT COUNT(*) FROM customers WHERE assigned_to=? AND resolved_at IS NOT NULL",
            (str(sid),)
        ).fetchone()[0] or 0

        # Avg response time for their conversations
        avg_resp = 0
        if conv_phones:
            inbound_ts = []
            outbound_ts = []
            for cp in conv_phones:
                in_t = conn.execute(
                    "SELECT MIN(timestamp) FROM messages WHERE customer_phone=? AND direction='inbound'",
                    (cp,)
                ).fetchone()[0]
                out_t = conn.execute(
                    "SELECT MIN(timestamp) FROM messages WHERE customer_phone=? AND direction='outbound'",
                    (cp,)
                ).fetchone()[0]
                if in_t and out_t:
                    inbound_ts.append(in_t)
                    outbound_ts.append(out_t)
            diffs = []
            for fi, fo in zip(inbound_ts, outbound_ts):
                try:
                    if fo > fi:
                        diffs.append((datetime.fromisoformat(fo) - datetime.fromisoformat(fi)).total_seconds())
                except (ValueError, TypeError):
                    pass
            if diffs:
                avg_resp = round(sum(diffs) / len(diffs))

        result.append({
            "id": sid,
            "name": sname,
            "conversations_handled": len(conv_phones),
            "avg_response_time_sec": avg_resp,
            "escalated_count": esc_count,
            "resolved_count": res_count,
            "messages_sent": msgs_sent,
        })

    conn.close()
    return result


def get_analytics_timeseries(days: int = 7, start_date: str = None, end_date: str = None) -> dict:
    """Get daily time-series data: inbound/outbound, escalated/resolved, response time."""
    conn = _get_db()
    tz = timezone(timedelta(hours=8))
    now_local = datetime.now(tz)

    labels = []
    messages_in = []
    messages_out = []
    escalated = []
    resolved = []
    response_times = []

    date_range = range(days - 1, -1, -1)
    if start_date and end_date:
        # Override range with custom dates
        try:
            sd = datetime.strptime(start_date, "%Y-%m-%d")
            ed = datetime.strptime(end_date, "%Y-%m-%d")
            day_count = (ed - sd).days + 1
            labels_ts = [sd + timedelta(days=i) for i in range(day_count)]
        except ValueError:
            labels_ts = [now_local - timedelta(days=i) for i in range(days - 1, -1, -1)]
    else:
        labels_ts = [now_local - timedelta(days=i) for i in range(days - 1, -1, -1)]

    for d in labels_ts:
        date_str = d.strftime("%Y-%m-%d")
        label = d.strftime("%-d %b") if d.date() != now_local.date() else "Hari ini"

        in_count = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE date(timestamp, '+8 hours') = ? AND direction='inbound'",
            (date_str,)
        ).fetchone()[0] or 0
        out_count = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE date(timestamp, '+8 hours') = ? AND direction='outbound'",
            (date_str,)
        ).fetchone()[0] or 0
        esc_count = conn.execute(
            "SELECT COUNT(*) FROM customers WHERE date(escalated_at, '+8 hours') = ?",
            (date_str,)
        ).fetchone()[0] or 0
        res_count = conn.execute(
            "SELECT COUNT(*) FROM customers WHERE date(resolved_at, '+8 hours') = ?",
            (date_str,)
        ).fetchone()[0] or 0

        # Avg response time for this day (inbound→outbound within same day)
        day_diffs = []
        msgs = conn.execute(
            "SELECT direction, timestamp FROM messages WHERE date(timestamp, '+8 hours') = ? ORDER BY customer_phone, timestamp ASC",
            (date_str,)
        ).fetchall()
        last_in = None
        for m in msgs:
            if m[0] == 'inbound':
                try:
                    last_in = datetime.fromisoformat(m[1])
                except (ValueError, TypeError):
                    last_in = None
            elif m[0] == 'outbound' and last_in is not None:
                try:
                    dt = (datetime.fromisoformat(m[1]) - last_in).total_seconds()
                    if 0 < dt < 86400:
                        day_diffs.append(dt)
                except (ValueError, TypeError):
                    pass
                last_in = None
        avg_rt = round(sum(day_diffs) / len(day_diffs)) if day_diffs else 0

        labels.append(label)
        messages_in.append(in_count)
        messages_out.append(out_count)
        escalated.append(esc_count)
        resolved.append(res_count)
        response_times.append(avg_rt)

    conn.close()
    return {
        "labels": labels,
        "messages_in": messages_in,
        "messages_out": messages_out,
        "escalated": escalated,
        "resolved": resolved,
        "response_times_sec": response_times,
    }


def get_export_csv(export_type: str = "overview", start_date: str = None, end_date: str = None) -> str:
    """Generate CSV for analytics data."""
    import io, csv

    output = io.StringIO()
    writer = csv.writer(output)

    if export_type == "overview":
        writer.writerow(["Metric", "Value"])
        overview = get_analytics_overview(start_date, end_date)
        for key, val in overview.items():
            writer.writerow([key.replace("_", " ").title(), val])

    elif export_type == "timeseries":
        writer.writerow(["Date", "Messages In", "Messages Out", "Escalated", "Resolved", "Avg Response (sec)"])
        ts = get_analytics_timeseries(7, start_date, end_date)
        for i, label in enumerate(ts["labels"]):
            writer.writerow([
                label,
                ts["messages_in"][i],
                ts["messages_out"][i],
                ts["escalated"][i],
                ts["resolved"][i],
                ts["response_times_sec"][i],
            ])

    elif export_type == "agents":
        writer.writerow(["Staff ID", "Name", "Conversations Handled", "Avg Response (sec)", "Escalated", "Resolved", "Messages Sent"])
        agents = get_agent_performance(None, start_date, end_date)
        for a in agents:
            writer.writerow([
                a["id"], a["name"], a["conversations_handled"],
                a["avg_response_time_sec"], a["escalated_count"],
                a["resolved_count"], a["messages_sent"],
            ])

    return output.getvalue()


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
    base_query = """\
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


# ── SPX Sync Progress (DB-backed, survives restarts) ──────────────

SYNC_PROGRESS_DEFAULTS = {
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


def load_sync_progress() -> dict:
    """Load sync progress from DB (persistent across restarts).
    Falls back to defaults if no saved state exists."""
    try:
        conn = _get_db()
        row = conn.execute("SELECT state_json FROM spx_sync_state WHERE id=1").fetchone()
        conn.close()
        if row:
            data = json.loads(row["state_json"])
            # Merge with defaults to handle missing keys
            merged = dict(SYNC_PROGRESS_DEFAULTS)
            merged.update(data)
            return merged
    except Exception as e:
        log.warning("[DB] load_sync_progress failed: %s, using defaults", e)
    return dict(SYNC_PROGRESS_DEFAULTS)


def save_sync_progress(data: dict) -> None:
    """Save sync progress to DB.
    Converts non-serialisable types (datetime, etc.) to strings."""
    try:
        cleaned = {}
        for k, v in data.items():
            if isinstance(v, (str, int, float, bool, list, dict)):
                cleaned[k] = v
            elif v is None:
                cleaned[k] = None
            else:
                cleaned[k] = str(v)
        conn = _get_db()
        conn.execute(
            "INSERT INTO spx_sync_state (id, state_json, updated_at) VALUES (1, ?, datetime('now')) "
            "ON CONFLICT(id) DO UPDATE SET state_json=excluded.state_json, updated_at=excluded.updated_at",
            (json.dumps(cleaned, default=str),),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        log.warning("[DB] save_sync_progress failed: %s", e)
