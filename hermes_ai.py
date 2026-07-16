import os
import requests
import subprocess
import traceback
import time
from typing import Optional

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "nvidia/nemotron-3-super-120b-a12b:free").strip()
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SYSTEM_PROMPT_PATH = os.path.join(BASE_DIR, "system_prompt.txt")
BUSINESS_INFO_PATH = os.path.join(BASE_DIR, "business_info.txt")

DEFAULT_SYSTEM_PROMPT = """Anda ialah HAFJET AI Assistant.
Bercakap dalam Bahasa Melayu yang santai, mesra, dan membantu.
Jangan flirt, jangan janji harga final tanpa semakan, jangan janji kelulusan ansuran, dan jangan reka fakta.
Jika tak pasti, cadangkan pelanggan sambung dengan staff manusia.
"""

DEFAULT_BUSINESS_INFO = """HAFJET ialah kedai telefon dan repair di Raub, Pahang.
Waktu operasi:
- Setiap Hari: 9:00 AM - 9:00 PM
Kaedah bayaran:
- Kad Debit
- Kad Kredit
- QR Code
- Shopee SPayLater
- AEON Easy Payment
- Boost PayFlex
Nombor rasmi:
+60 16-980 8736
Lokasi:
No. 890 Jalan Lestari 20, Taman Amalina Lestari, 27600 Raub, Pahang
Google Maps: https://g.co/kgs/95C9TB
"""

def safe_read_text(path: str, fallback: str) -> str:
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                return content if content else fallback
    except Exception as e:
        print(f"[hermes_ai] warning: failed reading {path}: {e}")
    return fallback

SYSTEM_PROMPT = safe_read_text(SYSTEM_PROMPT_PATH, DEFAULT_SYSTEM_PROMPT)
BUSINESS_INFO = safe_read_text(BUSINESS_INFO_PATH, DEFAULT_BUSINESS_INFO)

SOUL_CONTEXT = f"""
{SYSTEM_PROMPT}

================ BUSINESS INFO ================
{BUSINESS_INFO}
==============================================

PERATURAN TAMBAHAN:
- Jawapan mesti ringkas, mesra, dan sesuai untuk WhatsApp.
- Jika pelanggan tanya harga, beri anggaran / rujukan sahaja melainkan ada fakta tepat.
- Jika pelanggan tanya stok, kelulusan ansuran, atau harga final repair, jangan confirm jika tidak pasti.
- Jika pelanggan tanya perkara yang perlukan semakan lanjut, arahkan kepada staff manusia.
- Jika pelanggan cuba mengorat atau borak luar topik, balas sopan dan redirect semula kepada urusan HAFJET.
- Jangan sebut promosi tetap kerana tiada promosi tetap buat masa ini melainkan staff telah sahkan.
"""

# ── AI Memory: pull last N messages for a phone from db_logger ──────────
def build_memory_context(phone: Optional[str], limit: int = 8) -> str:
    """Return recent conversation history for personalization (AI Memory step)."""
    if not phone:
        return ""
    try:
        from db_logger import _query_customer
        rows = _query_customer(phone, limit)
        if not rows:
            return ""
        lines = []
        for r in rows:
            direction = "Pelanggan" if r.get("direction") == "inbound" else "Bot"
            content = (r.get("content") or "").strip().replace("\n", " ")
            if content:
                lines.append(f"{direction}: {content}")
        if lines:
            return "\n".join(lines[-limit:])
    except Exception as e:
        print(f"[hermes_ai] memory build failed: {e}")
    return ""

POWER_QUESTION = (
    "Nak saya bantu yang mana satu ni? 👇\n"
    "1️⃣ Repair phone\n"
    "2️⃣ Beli phone (cash / ansuran)\n"
    "3️⃣ Semak status parcel SPX / job repair\n"
    "4️⃣ Tanya pasal ansuran"
)

def ask_openrouter(user_message: str, wa_name: Optional[str] = None, phone: Optional[str] = None) -> Optional[str]:
    start_time = time.time()
    print(f"[hermes_ai] [OPENROUTER] Request started at {start_time}")
    
    # Verify configuration
    if not OPENROUTER_API_KEY:
        print("[hermes_ai] [OPENROUTER] ERROR: OPENROUTER_API_KEY is empty or not set")
        return None
    print(f"[hermes_ai] [OPENROUTER] API key (last 6 chars): ...{OPENROUTER_API_KEY[-6:]}")
    
    if not OPENROUTER_MODEL:
        print("[hermes_ai] [OPENROUTER] ERROR: OPENROUTER_MODEL is empty")
        return None
    print(f"[hermes_ai] [OPENROUTER] Model: {OPENROUTER_MODEL}")
    
    # Ensure URL ends with /v1/chat/completions (basic check)
    if not OPENROUTER_URL.endswith("/v1/chat/completions"):
        print(f"[hermes_ai] [OPENROUTER] WARNING: Base URL might be incorrect. Current: {OPENROUTER_URL}")
    else:
        print(f"[hermes_ai] [OPENROUTER] Base URL: {OPENROUTER_URL}")
    
    customer_name_hint = wa_name.strip() if wa_name else "pelanggan"

    # ── AI Memory: recent history for personalization ──
    memory_ctx = build_memory_context(phone)
    memory_block = f"\n\n[HISTORY PERBUALAN LEPASTU]\n{memory_ctx}\n[/HISTORY]" if memory_ctx else ""

    # ── Power Question hanya untuk cold lead (tiada history) ──
    power_block = ""
    if not memory_ctx:
        power_block = f"\n\n[SUGGESTION PENUTUP] Akhiri mesej dengan Power Question ni:\n{POWER_QUESTION}"

    user_content = (
        f"Nama pelanggan: {customer_name_hint}\n"
        f"Nombor: {phone or 'tidak diketahui'}\n"
        f"Mesej pelanggan: {user_message}"
        f"{memory_block}{power_block}"
    )
    messages = [
        {"role": "system", "content": SOUL_CONTEXT},
        {"role": "user", "content": user_content}
    ]
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": OPENROUTER_MODEL,
        "messages": messages,
        "temperature": 0.5
    }
    
    timeout_val = int(os.getenv("OPENROUTER_TIMEOUT", "30"))
    print(f"[MODEL_DEBUG] model={OPENROUTER_MODEL} base_url={OPENROUTER_URL} timeout={timeout_val}")
    
    try:
        resp = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=timeout_val)
        print(f"[MODEL_DEBUG] status_code={resp.status_code}")
        end_time = time.time()
        duration = end_time - start_time
        print(f"[hermes_ai] [OPENROUTER] Raw response body (first 500 chars): {resp.text[:500]}")
        print(f"[hermes_ai] [OPENROUTER] Request completed in {duration:.2f} seconds")
        
        if resp.status_code < 200 or resp.status_code >= 300:
            if resp.status_code == 429:
                print("[hermes_ai] [OPENROUTER] Rate limited (429) — retrying once after 2s sleep")
                print(f"[hermes_ai] [OPENROUTER] Rate limit message: {resp.text[:300]}")
                time.sleep(2)
                resp = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=30)
                duration = time.time() - start_time
                print(f"[hermes_ai] [OPENROUTER] Retry status: {resp.status_code} (total time: {duration:.2f}s)")
                print(f"[hermes_ai] [OPENROUTER] Retry raw body: {resp.text[:500]}")
                if resp.status_code >= 200 and resp.status_code < 300:
                    try:
                        data = resp.json()
                        content = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                        if content:
                            print(f"[hermes_ai] [OPENROUTER] Retry succeeded! Content: {content[:100]}")
                            return content
                    except:
                        pass
                print("[hermes_ai] [OPENROUTER] Retry also failed — falling back")
                return None
            elif resp.status_code == 401:
                print("[hermes_ai] [OPENROUTER] ERROR: Unauthorized (401) — API key invalid or revoked")
            elif resp.status_code == 402:
                print("[hermes_ai] [OPENROUTER] ERROR: Payment required (402) — account needs top-up")
            elif resp.status_code == 404:
                print("[hermes_ai] [OPENROUTER] ERROR: Model not found (404) — check model name")
            else:
                print(f"[hermes_ai] [OPENROUTER] ERROR: Bad status code {resp.status_code}")
            return None
        
        try:
            data = resp.json()
        except Exception as e:
            print(f"[hermes_ai] [OPENROUTER] ERROR: Failed to parse JSON: {e}")
            traceback.print_exc()
            return None
        
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        if not content:
            print("[hermes_ai] [OPENROUTER] WARNING: Empty content in response")
            return None
        
        print(f"[hermes_ai] [OPENROUTER] Success: Returning content (first 100 chars): {content[:100]}")
        print(f"[MODEL_DEBUG] reply={content[:200]}")
        return content
        
    except Exception as e:
        end_time = time.time()
        duration = end_time - start_time
        print(f"[hermes_ai] [OPENROUTER] EXCEPTION after {duration:.2f} seconds: {e}")
        traceback.print_exc()
        return None

def ask_hermes_cli(user_message: str) -> Optional[str]:
    """Fallback to Hermes CLI — only if 'hermes' binary exists."""
    import shutil
    if not shutil.which("hermes"):
        print("[hermes_ai] Hermes CLI not found — skipping fallback")
        return None
    try:
        result = subprocess.run(
            ["hermes", "ask", user_message],
            capture_output=True,
            text=True,
            timeout=60
        )
        if result.returncode == 0:
            output = (result.stdout or "").strip()
            return output or None
        print(f"[hermes_ai] Hermes CLI failed: {result.stderr}")
        return None
    except Exception as e:
        print(f"[hermes_ai] Hermes CLI exception: {e}")
        traceback.print_exc()
        return None

def ask_hermes(user_message: str, wa_name: Optional[str] = None, phone: Optional[str] = None) -> Optional[str]:
    start_time = time.time()
    print(f"[hermes_ai] [ASK_HERMES] Request started at {start_time}")
    
    # Try OpenRouter first
    reply = ask_openrouter(user_message, wa_name=wa_name, phone=phone)
    if reply and reply.strip():
        print(f"[hermes_ai] [ASK_HERMES] OpenRouter succeeded")
        end_time = time.time()
        print(f"[hermes_ai] [ASK_HERMES] Total time: {end_time - start_time:.2f} seconds")
        return reply
    
    print(f"[hermes_ai] [ASK_HERMES] OpenRouter returned empty/None, falling back to Hermes CLI")
    # Fallback to Hermes CLI
    reply = ask_hermes_cli(user_message)
    if reply and reply.strip():
        print(f"[hermes_ai] [ASK_HERMES] Hermes CLI succeeded")
        end_time = time.time()
        print(f"[hermes_ai] [ASK_HERMES] Total time: {end_time - start_time:.2f} seconds")
        return reply
    
    print(f"[hermes_ai] [ASK_HERMES] Both OpenRouter and Hermes CLI failed, using fallback message")
    # Fallback message
    fallback_msg = "Maaf, sistem sibuk sekejap. Untuk bantuan segera WhatsApp admin:\n+60 16-980 8736 (https://hafjetraub.wasap.my/)"
    end_time = time.time()
    print(f"[hermes_ai] [ASK_HERMES] Total time: {end_time - start_time:.2f} seconds")
    return fallback_msg