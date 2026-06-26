"""
repair_db.py — Repair Job Database Module
Simulasi database untuk tracking repair jobs.
Boleh ditukar kepada SQLite/PostgreSQL/MySQL dalam production.
"""

import os
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

log = logging.getLogger("hafjet-whatsapp.db")

MYT = timezone(timedelta(hours=8))

# ── Database File ──────────────────────────────────────────────────
DB_PATH = os.path.expanduser("~/.hermes/whatsapp-bot/repair_jobs.json")


# ── Simulated Database (JSON file) ─────────────────────────────────
# Dalam production, tukar kepada SQLite/PostgreSQL/MySQL

DEFAULT_JOBS = {
    "JOB-2026-001": {
        "job_id": "JOB-2026-001",
        "customer_name": "Ahmad Razak",
        "phone": "+60123456789",
        "device": "iPhone 14 Pro",
        "issue": "Skrin retak",
        "status": "completed",
        "status_text": "✅ Siap — boleh diambil",
        "estimated_completion": "2026-06-20",
        "actual_completion": "2026-06-20",
        "cost": 280.00,
        "notes": "Skrin OEM replacement. Warranty 30 hari.",
        "created_at": "2026-06-18T10:30:00+08:00",
        "updated_at": "2026-06-20T15:00:00+08:00",
    },
    "JOB-2026-002": {
        "job_id": "JOB-2026-002",
        "customer_name": "Siti Nurhaliza",
        "phone": "+60198765432",
        "device": "Samsung Galaxy S24",
        "issue": "Battery cepat habis",
        "status": "in_progress",
        "status_text": "🔧 Dalam proses repair",
        "estimated_completion": "2026-06-23",
        "actual_completion": None,
        "cost": 150.00,
        "notes": "Battery replacement dalam proses.",
        "created_at": "2026-06-21T09:00:00+08:00",
        "updated_at": "2026-06-22T11:00:00+08:00",
    },
    "JOB-2026-003": {
        "job_id": "JOB-2026-003",
        "customer_name": "Rajesh Kumar",
        "phone": "+60112233445",
        "device": "iPhone 13",
        "issue": "Charging port tak jalan",
        "status": "waiting_parts",
        "status_text": "⏳ Menunggu spare part",
        "estimated_completion": "2026-06-25",
        "actual_completion": None,
        "cost": 120.00,
        "notes": "Charging port module perlu diorder. ETA 2 hari.",
        "created_at": "2026-06-22T14:00:00+08:00",
        "updated_at": "2026-06-22T16:00:00+08:00",
    },
    "JOB-2026-004": {
        "job_id": "JOB-2026-004",
        "customer_name": "Tan Wei Ming",
        "phone": "+60144556677",
        "device": "iPad Air 5",
        "issue": "Water damage",
        "status": "diagnosing",
        "status_text": "🔍 Dalam diagnosis",
        "estimated_completion": "2026-06-24",
        "actual_completion": None,
        "cost": None,
        "notes": "Water damage assessment dalam proses. Kos akan selepas diagnosis.",
        "created_at": "2026-06-22T16:30:00+08:00",
        "updated_at": "2026-06-22T16:30:00+08:00",
    },
    "JOB-2026-005": {
        "job_id": "JOB-2026-005",
        "customer_name": "Muhammad Hafizi",
        "phone": "+60148537446",
        "device": "iPhone 15 Pro Max",
        "issue": "Motherboard issue — tak boleh power on",
        "status": "pending",
        "status_text": "📋 Dalam giliran",
        "estimated_completion": "2026-06-26",
        "actual_completion": None,
        "cost": None,
        "notes": "Motherboard repair — perlu diagnosis mendalam. Anggaran 3-5 hari.",
        "created_at": "2026-06-22T17:00:00+08:00",
        "updated_at": "2026-06-22T17:00:00+08:00",
    },
}


def _load_db() -> dict:
    """Load database dari JSON file."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    if os.path.exists(DB_PATH):
        try:
            with open(DB_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            log.warning("⚠ DB file corrupted, resetting to defaults")
    return dict(DEFAULT_JOBS)


def _save_db(db: dict):
    """Save database ke JSON file."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with open(DB_PATH, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)


def init_db():
    """Initialize database dengan sample data."""
    db = _load_db()
    if not db:
        db = dict(DEFAULT_JOBS)
        _save_db(db)
        log.info(f"✅ Database initialized with {len(DEFAULT_JOBS)} sample jobs")
    return db


def check_repair_status(job_id: str) -> Optional[dict]:
    """
    Semakan status repair job berdasarkan job_id.
    
    Args:
        job_id: Job ID (e.g., "JOB-2026-001")
    
    Returns:
        Dict dengan job details atau None jika tak jumpa.
    """
    # Normalize job_id
    job_id = job_id.strip().upper().replace(" ", "")

    db = _load_db()
    job = db.get(job_id)

    if job:
        log.info(f"✅ Job found: {job_id} — Status: {job['status']}")
        return job

    # Try partial match
    for key, val in db.items():
        if job_id in key or key.endswith(job_id):
            log.info(f"✅ Job found (partial): {key}")
            return val

    log.warning(f"❌ Job not found: {job_id}")
    return None


def format_job_status(job: dict) -> str:
    """Format job status untuk WhatsApp message."""
    lines = [
        f"📋 *Status Job: {job['job_id']}*",
        "",
        f"📱 Peranti: {job['device']}",
        f"🔧 Isu: {job['issue']}",
        f"📊 Status: {job['status_text']}",
    ]

    if job.get("cost"):
        lines.append(f"💰 Kos: RM {job['cost']:.2f}")

    if job.get("estimated_completion"):
        lines.append(f"📅 Anggaran siap: {job['estimated_completion']}")

    if job.get("actual_completion"):
        lines.append(f"✅ Tarikh siap: {job['actual_completion']}")

    if job.get("notes"):
        lines.append(f"📝 Nota: {job['notes']}")

    lines.append("")
    lines.append("Tulis *menu* untuk kembali ke menu utama.")

    return "\n".join(lines)


def add_job(job_data: dict) -> str:
    """
    Tambah job baru ke database.
    Returns job_id yang dicipta.
    """
    db = _load_db()

    # Generate job ID
    now = datetime.now(MYT)
    count = len(db) + 1
    job_id = f"JOB-{now.year}-{count:03d}"

    job_data["job_id"] = job_id
    job_data["created_at"] = now.isoformat()
    job_data["updated_at"] = now.isoformat()
    job_data.setdefault("status", "pending")
    job_data.setdefault("status_text", "📋 Dalam giliran")
    job_data.setdefault("actual_completion", None)

    db[job_id] = job_data
    _save_db(db)

    log.info(f"✅ New job created: {job_id}")
    return job_id


def update_job_status(job_id: str, status: str, status_text: str, notes: str = None) -> bool:
    """Update status job."""
    db = _load_db()
    job_id = job_id.strip().upper()

    if job_id not in db:
        log.warning(f"❌ Job not found for update: {job_id}")
        return False

    db[job_id]["status"] = status
    db[job_id]["status_text"] = status_text
    db[job_id]["updated_at"] = datetime.now(MYT).isoformat()

    if notes:
        db[job_id]["notes"] = notes

    if status == "completed" and not db[job_id].get("actual_completion"):
        db[job_id]["actual_completion"] = datetime.now(MYT).strftime("%Y-%m-%d")

    _save_db(db)
    log.info(f"✅ Job updated: {job_id} → {status}")
    return True


def list_jobs(status_filter: str = None) -> list:
    """List semua jobs, optional filter by status."""
    db = _load_db()
    jobs = list(db.values())
    if status_filter:
        jobs = [j for j in jobs if j["status"] == status_filter]
    return sorted(jobs, key=lambda x: x["created_at"], reverse=True)


# ── Initialize on import ───────────────────────────────────────────
init_db()
