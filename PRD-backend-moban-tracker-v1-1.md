# PRD — Backend Moban FU Case Tracker

**Versi:** 1.1 (supersedes v1.0) · **Tanggal:** 19 Agustus 2026 · **Owner:** Backend
**Stack:** FastAPI (Python) · Supabase (PostgreSQL) · WAHA (WhatsApp HTTP API) · OpenRouter (LLM fallback)
**Perubahan v1.1:** arsitektur multi-group dibatalkan — semua tim solusi (1–5) berada di **satu grup WA yang sama**. Mekanisme tracking diganti dari "hop lintas grup" menjadi **reply-chain traversal di dalam satu grup**.

---

## 1. Latar Belakang & Masalah

Moban (follow-up) case berjalan di **satu grup WhatsApp** berisi agen dan semua tim solver (Solusi 1–5). Masalah:

1. **Eskalasi gelap di dalam grup.** Agen kirim case → Solusi 1 tidak bisa solve → dilempar ke Solusi 2 (quote/mention di grup yang sama) → Solusi 2 menyelesaikan dan cukup **reply ke pesan Solusi 1**, bukan ke agen. Solusi 1 tidak meneruskan kabar, agen tidak tahu case-nya sudah selesai.
2. **Tracking manual.** Agen scroll grup untuk mencari update case.
3. **Format case berulang** (STC, SMOOA, Case Mobile, UFO) tapi diketik manual.

## 2. Tujuan

| # | Goal | Ukuran keberhasilan |
|---|------|--------------------|
| G1 | Input case dari web → DB + terkirim ke grup dengan format konsisten | 100% case terkirim, message ID tercatat |
| G2 | Status ter-update otomatis dari reply, **termasuk reply ke pesan orang lain** (rantai eskalasi) | Reply "done" di kedalaman rantai manapun mengubah status case |
| G3 | Rantai eskalasi (case lewat solver mana saja) terekam | Timeline case menampilkan semua peserta rantai |
| G4 | Histori grup bisa di-crawl untuk backfill | Endpoint crawl memproses N pesan terakhir |

**Non-goal (fase ini):** frontend, multi-grup, multi-nomor WA, notifikasi proaktif (fase 2).

## 3. User Stories

- **US-1** Agen input case lewat form → sistem kirim pesan terformat + mention ke grup.
- **US-2** Agen melihat daftar case beserta status (open / in_progress / done / issue) tanpa membuka WA.
- **US-3** Ketika solver menyelesaikan case dengan reply ke pesan solver lain (bukan ke pesan agen/bot), status case tetap berubah di dashboard.
- **US-4** Agen melihat timeline satu case: urutan pesan, siapa membalas siapa, kapan.
- **US-5** (Fase 2) Bot mengabari agen saat case selesai.

## 4. Arsitektur

```
Form Web ──► Backend API ──► Supabase (PostgreSQL)
                 │
                 ├────► WAHA ──► GRUP (satu grup, semua tim)  (kirim case)
                 │
                 ◄──── WAHA webhook (message, message.ack)
                 │
            Pencocokan: regex INC → reply-chain traversal → LLM fallback
```

**Kemampuan WAHA yang dipakai:**
- `POST /api/sendText` — kirim pesan, `mentions`, mengembalikan message ID.
- Webhook `message` — tiap pesan masuk membawa `payload.id`, `from`, `body`, dan referensi quoted message (`replyTo` / `_data.quotedMsgId`) saat pesan itu reply.
- Webhook `message.ack` — status terkirim/dibaca pesan keluar.
- `GET /api/{session}/chats/{chatId}/messages?limit=` — crawl histori grup (quoted-info ikut terbawa di `_data`).
- Bot cukup anggota **satu grup** ini saja.

## 5. Data Model (Supabase / PostgreSQL)

```sql
CREATE TABLE cases (
    id            SERIAL PRIMARY KEY,
    case_code     VARCHAR(50) UNIQUE,      -- INC000023470570 / Case ID; fallback CASE-0001
    case_type     VARCHAR(30) NOT NULL,    -- stc | smooa | mobile | ufo | other
    title         TEXT,
    fields        JSONB NOT NULL DEFAULT '{}',
    message_text  TEXT NOT NULL,
    wa_message_id VARCHAR(128) UNIQUE,     -- ROOT pesan case di grup (jangkar rantai)
    status        VARCHAR(20) NOT NULL DEFAULT 'open',
    ack           VARCHAR(20),
    created_at    TIMESTAMPTZ DEFAULT now(),
    updated_at    TIMESTAMPTZ DEFAULT now()
);

-- SEMUA pesan grup + parent-nya = bahan baku reply-chain traversal
CREATE TABLE wa_messages (
    wa_message_id VARCHAR(128) PRIMARY KEY,
    quoted_id     VARCHAR(128),            -- parent (pesan yang di-reply), NULL jika bukan reply
    case_id       INT REFERENCES cases(id) ON DELETE SET NULL,  -- diisi setelah tercocokkan
    author        VARCHAR(64),
    body          TEXT,
    from_me       BOOLEAN DEFAULT false,
    created_at    TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_wamsg_quoted ON wa_messages(quoted_id);

CREATE TABLE progress_updates (
    id            SERIAL PRIMARY KEY,
    case_id       INT REFERENCES cases(id) ON DELETE SET NULL,
    wa_message_id VARCHAR(128) UNIQUE REFERENCES wa_messages(wa_message_id),
    author        VARCHAR(64),
    body          TEXT,
    parsed_status VARCHAR(20),
    parsed_note   TEXT,
    source        VARCHAR(10),             -- rule | reply | chain | llm | crawl | manual
    confidence    REAL,
    created_at    TIMESTAMPTZ DEFAULT now()
);
```

## 6. Logika Inti

### 6.1 Kirim case (US-1)
`POST /api/cases` → rakit teks via template → WAHA `sendText` (dengan `mentions`) → simpan `cases` (`wa_message_id` = root) + baris `wa_messages` (`from_me=true`).

### 6.2 Pencocokan pesan masuk (waterfall)
Setiap event `message` dari grup:

1. **Rekam dulu** ke `wa_messages` (id, quoted_id, author, body) — semua pesan, apapun isinya.
2. **Regex INC** di body → case ketemu (`source=rule`).
3. **Reply langsung ke root:** `quoted_id == cases.wa_message_id` → case ketemu (`source=reply`).
4. **Chain traversal:** telusuri parent ke atas maks. 5 level:
   `quoted_id → wa_messages.quoted_id → ...` sampai menemukan baris yang `wa_message_id`-nya adalah root case atau sudah punya `case_id` terisi. Ini yang menangkap "Solusi 2 reply ke pesan Solusi 1" (`source=chain`).
5. **LLM fallback (OpenRouter):** tidak ada INC & rantai buntu → kirim body + daftar case open ke model, terapkan jika confidence ≥ 0.7 (`source=llm`).
6. Parse status: keyword (`done/selesai/beres/lurus` → done; `kendala/gagal/stuck` → issue; `proses/dicek/otw/FU` → in_progress), persen (`75%`). LLM juga mengembalikan status untuk kalimat bebas.
7. Simpan `progress_updates` (dedup by `wa_message_id`), isi `wa_messages.case_id`, update `cases.status`.

### 6.3 Rantai eskalasi (G3)
`GET /api/cases/{id}` mengembalikan timeline: semua `wa_messages` dengan `case_id` tsb, diurutkan waktu, membentuk pohon via `quoted_id`. Daftar peserta unik (author) pada rantai = riwayat solver yang menangani case.

### 6.4 Crawl (G4)
`POST /api/crawl?limit=200` → tarik histori via WAHA → **pass 1:** simpan semua pesan ke `wa_messages` (agar rantai lengkap) → **pass 2:** proses tiap pesan dengan waterfall 6.2. Dua pass penting karena urutan pesan histori tidak menjamin parent diproses sebelum child.

### 6.5 Batasan
- Pesan balasan **tanpa quote dan tanpa INC** → hanya LLM (konteks case open) yang bisa menangkap; akurasi tidak dijamin. Mitigasi: sosialisasi "selalu reply/quote pesan case".
- Edit/hapus pesan tidak mengubah catatan (tambah event `message.revoked` di fase 2).
- Chain traversal dibatasi 5 level untuk mencegah loop; kedalaman rantai real jarang >3.

## 7. API Specification

| Method & Path | Deskripsi | Response |
|---|---|---|
| `POST /api/cases` | Buat & kirim case ke grup | `201 {id, case_code, wa_message_id, text}` |
| `GET /api/cases` | List case (query: `status`, `case_type`, `q`) | `[{id, case_code, title, status, updated_at}]` |
| `GET /api/cases/{id}` | Detail + timeline rantai + peserta | `{case, messages[] (pohon), updates[], participants[]}` |
| `POST /api/cases/{id}/status` | Koreksi manual | update `source=manual` |
| `POST /api/crawl` | Backfill histori (query: `limit`) | `{fetched, stored, updates_applied}` |
| `POST /webhooks/waha` | Receiver event WAHA | `200 {ok}` (proses async) |
| `GET /health` | Healthcheck db + WAHA | `{status, db, waha}` |

### Field template per jenis case (form → teks)
| Jenis | Fields |
|---|---|
| `stc` | ticket_remedy, no_indihome, order_id, last_milestone, milestone_info, detail_case, evidence |
| `smooa` | grapari, ticket_remedy, no_indihome, nama_pelanggan, cp, email, tgl_kejadian, detail_case, evidence, smooa_parent, smooa_child |
| `mobile` | grapari, ticket_remedy, tier, msisdn, tgl_kejadian, lokasi, detail_case, evidence |
| `ufo` | grapari, case_id, no_indihome, email, cp, tgl_kejadian, order_id, status_case, detail_case, evidence |

## 8. Konfigurasi

```bash
DATABASE_URL=postgresql://postgres.xxx:pass@aws-0-ap-southeast-1.pooler.supabase.com:6543/postgres
WAHA_URL=http://localhost:3000
WAHA_API_KEY=<plain key>
WAHA_SESSION=default
WA_GROUP_ID=<id grup @g.us — satu-satunya grup>
OPENROUTER_API_KEY=<opsional>
OPENROUTER_MODEL=google/gemini-2.0-flash-001
BACKEND_API_KEY=<auth endpoint /api>
```

Supabase: pakai connection string pooler (port 6543); free tier 500 MB sangat cukup. Webhook WAHA: events `message`, `message.ack`, `session.status`.

## 9. Non-Fungsional

- Webhook balas 200 cepat, proses di background task; retry send 3x backoff.
- WAHA tidak diekspos publik; API key backend wajib; data mengandung PII pelanggan — akses internal only.
- `progress_updates` berfungsi ganda sebagai audit trail; log terstruktur per event.

## 10. Milestones

| # | Deliverable | Kriteria selesai |
|---|---|---|
| M1 | Supabase + schema + koneksi | `GET /health` db:ok |
| M2 | Kirim case + template | Case muncul di grup, root tercatat |
| M3 | Webhook + rule/reply parser | Reply langsung "done" mengubah status |
| M4 | **Chain traversal** | Reply ke pesan solver lain mengubah status case |
| M5 | LLM fallback + crawl 2-pass | Kalimat bebas ter-parse; backfill jalan |
| M6 | Hardening (auth, retry, HTTPS) | Siap untuk tim frontend |

## 11. Fase 2

- Notifikasi balik ke agen saat case `done` (bot reply ke root case atau DM agen).
- Reminder case open > X hari; SLA per tim solver dari data `wa_messages`.
- Event `message.revoked`; deteksi mention untuk identifikasi tim solver tujuan eskalasi.
