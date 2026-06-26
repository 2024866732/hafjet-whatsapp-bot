# HAFJET WhatsApp Bot v2.1 — Roadmap & Dashboard Specification

## Executive Summary

HAFJET WhatsApp Bot v2.0 telah berjaya mencapai status production-ready dengan routing greeting, menu, AI price query, dan job status yang stabil. Roadmap v2.1 ini menumpukan pada dua hala tuju utama: (1) penambahan fungsi bot yang lebih dalam, dan (2) pembinaan dashboard pemantauan setara WhatsApp Web yang membolehkan operator memantau, mengurus, dan mengkonfigurasi bot secara langsung melalui antara muka grafik.

---

## Bahagian 1 — Roadmap Feature v2.1

### 1.1 Fasa Bot Enhancements

#### Modul Handoff ke Staff

Bot perlu boleh kesan bila pelanggan memerlukan manusia dan hantar notifikasi kepada staff.

- Tambah keyword detection: `tolong`, `urgent`, `complaint`, `nak bercakap dengan manusia`
- Bila triggered: bot reply "Saya akan sambungkan anda dengan staff kami dalam masa singkat."
- Hantar alert ke nombor staff melalui WhatsApp API atau webhook dalaman
- Tandakan conversation sebagai `ESCALATED` dalam database

#### Modul Customer Memory

Simpan context pelanggan supaya bot ingat interaksi sebelumnya.

- Rekod nama pelanggan daripada WhatsApp profile
- Simpan sejarah jenis soalan yang pernah ditanya
- Personalise reply: "Macam yang Tuan pernah tanya sebelum ini..."
- Database schema: `customers(phone, name, first_contact, last_contact, query_history)`

#### Modul FAQ Repair Dinamik

Gantikan hardcoded harga dengan table yang boleh dikemas kini tanpa deploy semula.

- Buat table `repair_faq(device, issue, price_min, price_max, turnaround)`
- Bot query table dahulu sebelum hantar ke OpenRouter
- Admin boleh update harga terus dari dashboard
- Cache FAQ selama 10 minit untuk kurangkan DB queries

#### Modul Broadcast Message

Hantar mesej bulk kepada pelanggan lama untuk promosi atau notifikasi.

- List penerima disimpan dalam `broadcast_lists`
- Template mesej perlu diluluskan oleh Meta sebelum hantar
- Rate limit: maksimum 1,000 mesej sehari pada tier semasa
- Log delivery status setiap broadcast

#### Modul Appointment / Booking

Pelanggan boleh buat temujanji repair melalui bot.

- Flow: pilih tarikh → pilih masa → confirm → bot save ke DB
- Reminder automatik 1 jam sebelum temujanji
- Operator boleh semak dan confirm melalui dashboard

---

### 1.2 Timeline Pembangunan

| Fasa | Feature | Tempoh Anggaran | Priority |
|------|---------|----------------|----------|
| v2.1.0 | Dashboard asas (monitor chat) | 2–3 minggu | 🔴 Tinggi |
| v2.1.1 | Handoff ke staff + escalation | 1 minggu | 🔴 Tinggi |
| v2.1.2 | FAQ repair dinamik dari DB | 1 minggu | 🟠 Sederhana |
| v2.1.3 | Customer memory | 1–2 minggu | 🟠 Sederhana |
| v2.2.0 | Broadcast message | 2 minggu | 🟡 Rendah |
| v2.2.1 | Appointment / booking | 2–3 minggu | 🟡 Rendah |

---

## Bahagian 2 — Dashboard Specification

### 2.1 Gambaran Keseluruhan

Dashboard HAFJET adalah antara muka web berasaskan browser yang berfungsi seperti WhatsApp Web tetapi khusus untuk operator bot. Ia membolehkan operator melihat semua perbualan secara langsung, melihat apa yang bot reply, mengambil alih perbualan, dan mengkonfigurasi bot tanpa perlu masuk ke kod.

**Tech Stack Cadangan:**

| Layer | Teknologi | Sebab |
|-------|-----------|-------|
| Frontend | React + Vite + TailwindCSS | Fast, component-based, sesuai untuk chat UI |
| Backend API | FastAPI (Python) | Sama bahasa dengan bot, mudah integrate |
| Database | PostgreSQL / SQLite | Simpan log chat, config, customer data |
| Realtime | WebSocket (FastAPI WebSocket) | Live chat update tanpa refresh |
| Auth | JWT + bcrypt | Selamat, stateless, mudah implement |
| Hosting | Azure App Service (sama dengan bot) | Satu platform, kurang overhead |

---

### 2.2 Senarai Halaman Dashboard

#### Halaman 1 — Live Chat Monitor (Utama)

Setara dengan WhatsApp Web. Bahagian paling penting.

**Panel Kiri — Senarai Perbualan:**
- Senarai semua pelanggan yang pernah atau sedang berbual
- Setiap item tunjuk: nama/nombor, mesej terakhir, masa, badge status
- Badge status: `🟢 Active`, `🔴 Escalated`, `⚫ Closed`, `🤖 Bot Handling`
- Search bar untuk cari nombor atau nama pelanggan
- Filter: Semua / Bot / Escalated / Hari ini

**Panel Tengah — Conversation View:**
- Paparan chat seperti WhatsApp Web
- Mesej pelanggan di kiri (warna lain), bot reply di kanan
- Label setiap mesej: `[BOT]` atau `[STAFF]` atau `[SYSTEM]`
- Timestamp setiap mesej
- Scroll history ke atas untuk lihat perbualan lama
- Input box untuk operator reply manual (bypass bot)

**Panel Kanan — Info Pelanggan:**
- Nombor telefon, nama profile WhatsApp
- Bilangan kali hubungi
- Tarikh pertama dan terakhir hubungi
- Senarai job yang pernah dibuat
- Butang: "Assign to Staff", "Mark Resolved", "Block"

---

#### Halaman 2 — Analytics Dashboard

**KPI Cards (baris atas):**
- Total mesej masuk hari ini
- Total reply bot hari ini
- Kadar AI reply vs static reply (%)
- Bilangan escalation hari ini
- Average response time (saat)

**Chart 1 — Message Volume:**
- Bar chart: mesej masuk vs keluar per jam (24 jam terakhir)
- Line chart: trend minggu ini vs minggu lepas

**Chart 2 — Routing Breakdown:**
- Pie chart: % Greeting / Menu / AI Query / Job Status / Escalated

**Chart 3 — AI Performance:**
- Average token used per reply
- Fallback rate (%)
- Response latency trend

**Table — Top Queries:**
- 10 soalan paling kerap ditanya hari ini
- Berguna untuk update FAQ

---

#### Halaman 3 — Bot Settings

**Seksyen: Reply Configuration**
- Toggle: Bot Active / Inactive (tanpa perlu redeploy)
- Edit canonical greeting text
- Edit fallback reply text
- Edit menu options (label + response text)
- Toggle AI mode: On / Off
- Set AI response language: BM / EN / Auto

**Seksyen: AI Settings**
- Dropdown: pilih model OpenRouter
- Slider: temperature (0.0 – 1.0)
- Input: max tokens
- Input: system prompt (editable textarea)
- Input: AI timeout (saat)

**Seksyen: Dedup & Routing**
- Input: dedup window (minit)
- Toggle: Greeting early return On/Off
- Input: greeting keyword list (editable)
- Input: escalation keyword list

**Seksyen: API Keys (masked)**
- Tunjuk nama variable sahaja, bukan nilai
- Butang "Test Connection" untuk semak setiap key
- Status: ✅ Connected / ❌ Failed

---

#### Halaman 4 — Job & Customer Management

**Tab 1 — Active Jobs:**
- Table: Job ID, Pelanggan, Peranti, Isu, Status, Tarikh Masuk, Tarikh Dijangka Siap
- Filter: semua / in progress / completed / waiting parts
- Butang: Update Status, Add Note, Mark Complete

**Tab 2 — Customer List:**
- Table: Nombor, Nama, Total Mesej, Tarikh Pertama Hubungi, Status
- Search & filter
- Klik untuk lihat semua perbualan pelanggan tersebut

**Tab 3 — Repair FAQ (Admin):**
- Table: Peranti, Isu, Harga Min, Harga Max, Turnaround
- Butang Add / Edit / Delete
- Perubahan langsung update bot tanpa deploy

---

#### Halaman 5 — Broadcast Manager

- Buat template mesej baru (perlu Meta approval)
- Lihat senarai template yang diluluskan
- Pilih segment pelanggan sebagai penerima
- Schedule broadcast atau hantar segera
- Lihat delivery report: sent / delivered / read / failed

---

#### Halaman 6 — Activity Log

- Log semua event: mesej masuk, reply keluar, error, fallback, escalation
- Filter: by type, by date range, by customer
- Export ke CSV
- Berguna untuk debug dan audit

---

### 2.3 Database Schema Asas

```sql
-- Customers
CREATE TABLE customers (
    id SERIAL PRIMARY KEY,
    phone VARCHAR(20) UNIQUE NOT NULL,
    name VARCHAR(100),
    first_contact TIMESTAMP DEFAULT NOW(),
    last_contact TIMESTAMP,
    total_messages INT DEFAULT 0,
    status VARCHAR(20) DEFAULT 'active'
);

-- Conversations (log semua mesej)
CREATE TABLE messages (
    id SERIAL PRIMARY KEY,
    customer_phone VARCHAR(20),
    direction VARCHAR(10), -- 'inbound' or 'outbound'
    content TEXT,
    msg_type VARCHAR(20), -- 'greeting', 'menu', 'ai', 'staff', 'system'
    routing_path VARCHAR(50),
    latency_ms INT,
    fallback_used BOOLEAN DEFAULT FALSE,
    timestamp TIMESTAMP DEFAULT NOW(),
    wamid VARCHAR(100) UNIQUE -- WhatsApp message ID untuk dedup
);

-- Bot Settings (configurable dari dashboard)
CREATE TABLE bot_settings (
    key VARCHAR(100) PRIMARY KEY,
    value TEXT,
    updated_at TIMESTAMP DEFAULT NOW(),
    updated_by VARCHAR(50)
);

-- Repair FAQ
CREATE TABLE repair_faq (
    id SERIAL PRIMARY KEY,
    device VARCHAR(100),
    issue VARCHAR(200),
    price_min INT,
    price_max INT,
    turnaround VARCHAR(50),
    active BOOLEAN DEFAULT TRUE
);

-- Jobs
CREATE TABLE jobs (
    id VARCHAR(20) PRIMARY KEY, -- JOB-2026-001
    customer_phone VARCHAR(20),
    device VARCHAR(100),
    issue TEXT,
    status VARCHAR(30),
    created_at TIMESTAMP DEFAULT NOW(),
    expected_done TIMESTAMP,
    completed_at TIMESTAMP,
    notes TEXT
);
```

---

### 2.4 Realtime Architecture (WebSocket)

```
[WhatsApp Cloud API]
        │
        ▼
[FastAPI Webhook /webhook]
        │
        ├── Save message to DB
        │
        ├── Process bot logic
        │
        ├── Send WhatsApp reply
        │
        └── Broadcast to WebSocket clients
                │
                ▼
        [Dashboard Browser]
        (Update chat UI in real-time
         tanpa perlu refresh)
```

Setiap mesej masuk atau keluar akan dipancarkan (broadcast) kepada semua tab dashboard yang terbuka melalui WebSocket. Ini membolehkan operator lihat chat update secara langsung.

---

### 2.5 Prompt untuk Hermes — Mula Bina Dashboard

```
Yo Hermes — HAFJET WhatsApp Bot v2.0 dah production ready.
Sekarang kita nak bina dashboard pemantauan v2.1.

OBJECTIVE:
Bina web dashboard untuk operator HAFJET yang boleh:
1. Monitor semua chat WhatsApp secara realtime (macam WhatsApp Web)
2. Lihat apa yang bot reply dan routing path yang digunakan
3. Reply manual kepada pelanggan (bypass bot)
4. Configure bot settings tanpa deploy
5. Lihat analytics asas

TECH STACK:
- Backend: FastAPI (extend dari webhook_listener.py sedia ada)
- Frontend: React + Vite + TailwindCSS
- Database: SQLite (development) → PostgreSQL (production)
- Realtime: WebSocket via FastAPI
- Auth: JWT simple (username/password untuk operator)
- Hosting: Azure App Service (sama dengan bot)

PHASE 1 TARGET (v2.1.0):
Bina halaman utama dahulu:
1. FastAPI WebSocket endpoint untuk broadcast mesej ke dashboard
2. Database logging — simpan setiap mesej (inbound + outbound) ke table `messages`
3. Simple React dashboard dengan:
   - Panel kiri: senarai perbualan
   - Panel tengah: conversation view (chat bubbles)
   - Update realtime via WebSocket

CONSTRAINT:
- Jangan pecahkan webhook bot yang sedia ada
- Database logging kena non-blocking (async)
- WebSocket broadcast kena lightweight
- Dashboard auth simple: satu username/password untuk operator

LANGKAH 1:
Audit webhook_listener.py semasa dan tunjukkan:
- Di mana nak inject DB logging (selepas receive + selepas send reply)
- Di mana nak inject WebSocket broadcast
- Apa changes minimum yang perlu dibuat

OUTPUT:
A. Audit webhook_listener.py
B. DB schema untuk messages + customers
C. FastAPI WebSocket implementation
D. React component structure untuk chat UI
E. Deployment plan
```

---

## Bahagian 3 — Ringkasan Keutamaan

| Item | Kepentingan | Masa |
|------|-------------|------|
| Dashboard Live Chat Monitor | 🔴 Kritikal | 2–3 minggu |
| DB logging semua mesej | 🔴 Kritikal | 3 hari |
| Handoff ke staff | 🔴 Tinggi | 1 minggu |
| Bot Settings dari dashboard | 🟠 Sederhana | 1 minggu |
| Analytics KPI | 🟠 Sederhana | 1 minggu |
| FAQ dinamik | 🟠 Sederhana | 1 minggu |
| Customer memory | 🟡 Rendah | 2 minggu |
| Broadcast manager | 🟡 Rendah | 2 minggu |
| Appointment booking | 🟡 Rendah | 3 minggu |

