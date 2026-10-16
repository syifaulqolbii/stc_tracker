# PRD — IT Tracker

**Nama produk:** IT Tracker (sebelumnya "Moban FU Case Tracker" — lihat catatan di §15.4)
**Versi dokumen:** 2.0 · **Tanggal:** 16 Oktober 2026
**Menggantikan:** `PRD-backend-it-tracker-v1-1.md` (v1.5, 7 Sep 2026) — sudah tidak akurat, lihat §13.
**Basis kode:** `main-v1-1.py` (3.373 baris, `version="1.34.0"`), `templates.py`, 11 file `schema-*.sql`, `requirements.txt`
**Kontrak API turunan (per-endpoint, human-readable):** `API-contract-frontend.md` v1.34 (106 KB)
**Stack:** FastAPI (Python 3.13) · PostgreSQL 16 · WAHA (WhatsApp HTTP API) · LLM fallback OpenAI-compatible · Nginx + TLS · Docker Compose
**Prod:** `https://api.stc.syfa.site` (VPS 43.157.212.98) · Swagger: `GET /docs` · OpenAPI: `GET /openapi.json`

> **Cara pakai dokumen ini:** §1–§7 = produk & kebutuhan (untuk semua pembaca). §8 = arsitektur backend. §9 = spesifikasi API ringkas, per-detail di `API-contract-frontend.md`. §10 = spesifikasi frontend. §11–§12 = data & operasional. §13 = temuan/keterbatasan yang diketahui. Setiap klaim teknis dapat dilacak ke `file:line`.

---

## 1. Latar Belakang & Masalah

Moban (follow-up) case berjalan di grup WhatsApp berisi agen dan tim solver. Di produk ini fitur tersebut bernama **IT Tracker**. Masalah yang disasar:

| # | Masalah | Dampak |
|---|---|---|
| M1 | **Eskalasi gelap.** Agen kirim case → Solver 1 lempar ke Solver 2 (quote/mention di grup) → Solver 2 cukup **reply ke pesan Solver 1**, bukan ke agen. Agen tidak tahu case selesai. | Case terlihat "menggantung" berhari-hari, follow-up manual berulang |
| M2 | **Tracking manual.** Agen scroll grup untuk mencari update case. | Waktu terbuang; tidak ada rekap |
| M3 | **Format case berulang** (STC, SMOOA, Case Mobile, UFO, AO) tapi diketik manual tiap kali. | Salah ketik, data tidak seragam, tidak bisa dicari |
| M4 | **Tidak ada rekap kinerja solver.** Tidak ada data berapa lama solver merespons/menyelesaikan. | Tidak bisa evaluasi SLA |

Inti solusi: bot di grup menerima webhook tiap pesan, **menelusuri rantai reply ke atas** untuk menemukan case yang dirujuk, lalu memperbarui status case di database — sehingga dashboard web selalu sinkron tanpa membaca WhatsApp manual.

## 2. Tujuan & Ukuran Keberhasilan

| # | Goal | Ukuran |
|---|---|---|
| G1 | Input case dari web → DB + terkirim ke grup dengan format konsisten | 100% case terkirim, `wa_message_id` root tercatat |
| G2 | Status ter-update otomatis dari balasan solver, **termasuk reply ke pesan solver lain** (rantai eskalasi) | Balasan "done" di kedalaman rantai manapun mengubah status |
| G3 | Rantai eskalasi terekam sebagai timeline | Timeline case menampilkan semua peserta + siapa membalas siapa |
| G4 | Histori grup bisa di-crawl untuk backfill | `POST /api/crawl` memproses N pesan terakhir per grup |
| G5 | Reminder otomatis/manual agar case idle tersundul | Case idle >2 jam (default) di-reminder dengan mention solver |
| G6 | Agen bisa membalas solver dari web tanpa membuka WhatsApp | `POST /api/cases/{id}/replies` (teks + maks 3 lampiran) |
| G7 | Pengukuran kinerja solver | `GET /api/metrics/solver-response` (menit) & `/solver-resolution` (jam) |
| G8 | Rekap data untuk pelaporan | `GET /api/cases/export.xlsx` (filter identik dashboard) |
| G9 | Monitoring mandiri saat WhatsApp/DB mati | `/health` + alert Telegram, dicek cron 5 menit |

**Non-goal fase ini:** notifikasi proaktif ke agen saat case `done`; akun multi-user (saat ini kode akses bersama); multi-nomor WA per regional (rencana terpisah, §14); retensi/arsip otomatis.

## 3. Pengguna & Peran

| Peran | Kebutuhan utama | Catatan implementasi |
|---|---|---|
| **Agen** | Input case cepat, lihat daftar & status, balas solver dari web | Satu kode akses untuk semua (belum akun per-orang) |
| **Solver** | Tidak memakai web; tetap di WhatsApp | Balasan WA-nya ditangkap bot dan mengubah status |
| **Admin** | Kelola grup WA, kontak solver, crawl, pantau metrik | Butuh halaman admin (grup/kontak/crawl), `GET /api/waha/groups` untuk ambil `chat_id` |
| **Ops** | Jaga WAHA hidup, backup, rotasi secret, pantau alert Telegram | Lihat `docs/production-runbook.md` |

**Batasan akses saat ini:** autentikasi ke API = header `X-API-Key` (satu key global); gate frontend = `POST /api/auth/access-code` mengembalikan JWT (kode dari env `ACCESS_CODES`, `expires_in` default 86400 detik). **Backend tidak memverifikasi JWT** — token dipakai FE sebagai gate lokal (§10.2).

## 4. Ruang Lingkup

### 4.1 Alur utama yang harus bekerja end-to-end
1. **Buat case:** form dinamis → preview teks → kirim test ke grup default → kirim ke grup asli → case muncul di dashboard.
2. **Balasan solver:** webhook → waterfall pencocokan → status case berubah → notifikasi ringkas ke grup default.
3. **Agen membalas dari web:** timeline → tombol Balas → teks/lampiran/gambar paste → tercatat sebagai bagian rantai.
4. **Reminder:** cron → case idle tersundul dengan mention solver; riwayat terlihat di detail case.
5. **Pelaporan:** filter dashboard → tabel → export Excel dengan filter identik.
6. **Metrik:** response time (menit) & resolution time (jam), per solver/regional/grup/rentang tanggal.

### 4.2 Fitur yang sudah ada di produksi (daftar lengkap)

| Fitur | Status | Referensi |
|---|---|---|
| Multi-grup WA (switcher) | ✅ | §8.4 |
| Grup default (fallback, tersembunyi dari switcher) | ✅ | §8.4 |
| Discovery grup dari WAHA | ✅ | `GET /api/waha/groups` |
| Waterfall pencocokan (rule → reply → chain → LLM) | ✅ | §8.3 |
| Auto `in_progress` pada balasan pertama | ✅ (v1.29) | §8.3 |
| Preview & test-send tanpa membuat case | ✅ (v1.14) | §9.3 |
| Auto-mention `@nomor` yang diketik manual | ✅ (v1.17–v1.18) | §9.3 |
| Balas solver dari web + lampiran base64 | ✅ (v1.26–v1.27) | §9.6 |
| Reminder manual + cron + riwayat | ✅ | §9.7 |
| Pagination opt-in | ✅ (v1.20) | §9.4 |
| Export Excel (filter identik) | ✅ (v1.21) | §9.5 |
| Filter rentang tanggal inklusif | ✅ (v1.22) | §9.4 |
| Pencarian seluruh `fields` JSONB | ✅ (v1.24) | §9.4 |
| Metrik response & resolution solver | ✅ (v1.32, v1.34) | §9.8 |
| Health detail + alert Telegram | ✅ (v1.33) | §9.9 |
| Media timeline (image/video/dokumen) | ✅ | §9.10 |
| Solver contacts CRUD + soft delete | ✅ | §9.11 |

### 4.3 Belum ada / tidak dikerjakan
- Notifikasi balik ke agen saat case `done` (bot belum DM agen).
- Event `message.revoked` (pesan dihapus tidak mengubah catatan).
- Edit pesan tidak tercatat.
- Websocket; realtime hanya polling.
- Validasi `REQUIRED_FIELDS` di backend — **didefinisikan tetapi tidak ditegakkan** (§13, T1); enforcement saat ini hanya di UI.
- Multi-nomor WA (§14).

---

## 5. Perjalanan Pengguna (User Journeys)

### J1 — Agen membuat case
```
1. Login: masukkan kode akses → POST /api/auth/access-code → simpan token
2. Muat lookup: GET /api/areas, /api/sumber-tickets, /api/jenis-cases, /api/groups
3. Pilih Area → GET /api/areas/{id}/regionals → Regional
4. Pilih Sumber Ticket; jika "Grapari" → munculkan input Asal Grapari
5. Pilih Jenis Case (Non Order / Non AO / AO / Mobile) → form field menyesuaikan
6. Pilih solver (multi-select, sumber /api/solver-contacts) → mentions[]
7. (Opsional) Tulis custom header, boleh pakai {phone}
8. Tekan "Preview" → POST /api/cases/preview → tampilkan teks persis
9. Tekan "Kirim Test" → POST /api/cases/test-send → cek di grup test (WA)
10. Tekan "Kirim" → POST /api/cases → tampilkan toast berisi response.text
```
**Aturan wajib:** jika grup tidak dipilih → **hilangkan field `group_id`** (jangan kirim `0`/`""`). Aturan per-jenis untuk `group_id` di endpoint test ada di §9.3.

### J2 — Solver menyelesaikan case (tanpa web)
```
1. Solver reply pesan case (root bot) → "done mas"        → source=reply,  status=done
2. Atau Solver 2 reply pesan Solver 1 → "sudah diluruskan" → source=chain,  status=done
3. Atau pesan bebas tanpa quote & tanpa INC yang mengandung info tiket → source=llm (confidence ≥0.7)
4. Balasan pertama pada case `open` (tanpa keyword status) → otomatis in_progress
5. Bot mengirim notifikasi ringkas ke grup default (bukan grup asal)
```
### J3 — Agen melihat & membalas dari web
```
1. Dashboard: GET /api/cases (+filter, +pagination) → tabel, badge status, indikator ack
2. Klik baris → GET /api/cases/{id} → tree messages via quoted_id, updates[], participants[]
3. Tombol Balas pada bubble solver → POST /api/cases/{id}/replies
   - teks saja, atau 1–3 lampiran (image/jpeg|png|webp, video/mp4, application/pdf, maks 5 MB/file)
   - paste image clipboard (Ctrl+V) didukung tanpa endpoint baru
4. Setelah sukses → **refresh timeline**
5. Koreksi status manual → POST /api/cases/{id}/status  → refresh
```
**Guardrail kritis:** jangan pernah balas dari HP akun bot. Pesan manual dari akun bot (`fromMe=true`) dilewati webhook sehingga tidak tercatat → rantai putus. Selalu lewat endpoint replies.

### J4 — Admin
```
- Discovery grup: GET /api/waha/groups → salin chat_id yang `registered:false`
- Tambah grup: POST /api/groups {"name","chat_id"} (opsional "is_default":true)
- Set grup test: PUT /api/groups/{id} {"is_default": true}  (otomatis menggeser default lama)
- Nonaktifkan grup: DELETE /api/groups/{id} (soft: is_active=false)
- Kelola kontak solver: CRUD /api/solver-contacts
- Backfill histori: POST /api/crawl?limit=200&group_id=… (berat — sediakan konfirmasi di UI)
```
### J5 — Pelaporan & metrik
```
- Filter dashboard (status/jenis/area/regional/grup/sumber/q/tanggal) → tabel
- Tekan Export → fetch /api/cases/export.xlsx dengan filter aktif → blob → unduh
- Halaman metrik: response time (menit) & resolution time (jam) → KPI median, distribusi, tabel per solver, watchlist
```

---

## 6. Kebutuhan Fungsional

### 6.1 Case (FR-C)
| ID | Kebutuhan | Prioritas |
|---|---|---|
| FR-C1 | Input case dengan hierarchy Area → Regional yang saling terikat | Wajib |
| FR-C2 | Empat jenis case dengan set field berbeda: Non Order, Non AO, AO, Mobile | Wajib |
| FR-C3 | Sumber ticket: STC, Grapari, Web IT; field Asal Grapari muncul hanya bila Grapari | Wajib |
| FR-C4 | Mention solver via dropdown (multi) dan/atau diketik `@nomor` di teks | Wajib |
| FR-C5 | Custom header dengan placeholder `{phone}` | Wajib |
| FR-C6 | Preview teks persis sebelum kirim | Wajib |
| FR-C7 | Kirim test ke grup default tanpa membuat case | Wajib |
| FR-C8 | Kirim ke grup default bila `group_id` kosong | Wajib |
| FR-C9 | Re-FU `case_code` yang sama = buka ulang case (status `open`, `deleted_at` dibersihkan), bukan duplikat | Wajib |
| FR-C10 | Mode textarea: user copy-paste wording case langsung | Sebaiknya |
| FR-C11 | Validasi `ticket_remedy` format `INC<9+ digit>`; kode non-INC dikirim via `fields.case_id` | Wajib |
| FR-C12 | Soft delete case (data tidak hilang, default tersembunyi) | Wajib |

### 6.2 Tracking (FR-T)
| ID | Kebutuhan | Prioritas |
|---|---|---|
| FR-T1 | Setiap pesan grup terdaftar direkam (semua pesan, untuk rantai) | Wajib |
| FR-T2 | Pencocokan waterfall: regex INC → kode non-INC persis → reply ke root → chain (maks 5 level) → LLM (confidence ≥0.7) | Wajib |
| FR-T3 | Verifikasi grup: case hanya di-link dari pesan di grupnya sendiri | Wajib |
| FR-T4 | Balasan pertama pada case `open` otomatis `in_progress` | Wajib |
| FR-T5 | Status dari keyword (`done/selesai/beres/kelar/solved/closed/terkirim/completed`; `proses/progress/diproses/otw/dicek/cek dulu/follow up/fu`) dengan word-boundary + guard negasi | Wajib |
| FR-T6 | Ack pesan keluar (PENDING/SERVER/DEVICE/READ) tersimpan | Sebaiknya |
| FR-T7 | Notifikasi ringkas balasan solver ke grup default (teks + media, maks 300 char kutipan) | Wajib |
| FR-T8 | Nama kontak di-resolve (`@lid` → nama), mention di `progress_updates` ditulis sebagai nama | Wajib |

### 6.3 Aksi dari Web (FR-A)
| ID | Kebutuhan | Prioritas |
|---|---|---|
| FR-A1 | Koreksi status manual (`open`/`in_progress`/`done`); `issue` ditolak | Wajib |
| FR-A2 | Balas solver dengan teks dan/atau maks 3 lampiran | Wajib |
| FR-A3 | Paste gambar dari clipboard langsung jadi lampiran | Sebaiknya |
| FR-A4 | Reminder manual per case | Wajib |
| FR-A5 | Riwayat reminder per case | Sebaiknya |
| FR-A6 | Crawl backfill histori per grup atau semua grup | Wajib (admin) |

### 6.4 Pelaporan (FR-R)
| ID | Kebutuhan | Prioritas |
|---|---|---|
| FR-R1 | Daftar case dengan filter kombinatif + pencarian bebas | Wajib |
| FR-R2 | Pagination opt-in (tanpa `limit` tetap array polos) | Wajib |
| FR-R3 | Export Excel memakai filter identik dashboard | Wajib |
| FR-R4 | Metrik response time solver (menit) | Wajib |
| FR-R5 | Metrik resolution time solver (jam) | Wajib |
| FR-R6 | Watchlist: case belum dibalas / belum selesai | Wajib |

### 6.5 Administrasi & Operasional (FR-O)
| ID | Kebutuhan | Prioritas |
|---|---|---|
| FR-O1 | CRUD grup WA + flag `is_default` (maks 1 di level DB) | Wajib |
| FR-O2 | CRUD kontak solver (soft delete) | Wajib |
| FR-O3 | Discovery grup dari WAHA | Wajib |
| FR-O4 | Health detail (DB + WAHA HTTP + status tiap session) | Wajib |
| FR-O5 | Alert Telegram otomatis saat service turun, re-alert, dan saat pulih | Wajib |
| FR-O6 | `GET /api/waha/sessions` — **belum ada**, lihat §14 | Nice to have |

---

## 7. Kebutuhan Non-Fungsional

| Aspek | Target / nilai saat ini | Sumber |
|---|---|---|
| Performa webhook | Match regex/chain ≈50 ms; LLM hanya dipanggil bila regex/chain gagal | §8.3 |
| Rata-rata beban | ±108 case/bulan (±3–4/hari kerja, ~54/grup/bulan untuk 2 grup) | `docs/pitch-deck-arsitektur.md` |
| Kapasitas server | 2 vCPU · 2 GB RAM · limit compose 2 CPU/1 GB untuk app | `docker-compose.prod.yml` |
| Rate limit | Umum: `RATE_LIMIT_RPM` default 60/menit/IP (prod 120). Login: `LOGIN_RATE_LIMIT` default 5/menit/IP + nginx `5r/m burst=2` | §8.8 |
| Realtime | Polling `GET /api/cases` tiap 30 detik atau saat window focus | `API-contract-frontend.md` §1 |
| Autentikasi | `X-API-Key` untuk API; access-code → JWT untuk gate FE | §9.2 |
| Keamanan | Webhook secret, anti-SSRF media proxy, soft-delete dihormati webhook, port app bind `127.0.0.1` | §8.9 |
| Backup | `scripts/backup-db.sh`, retensi 14 hari | §12 |
| Monitoring | `/health` tiap 5 menit via cron + Telegram | §9.9 |
| Retensi data | Tidak terbatas (media di Docker volume) | — |
| Bahasa | UI & pesan WhatsApp: Bahasa Indonesia (campur istilah teknis) | — |
| Aksesibilitas data | Semua waktu ISO 8601 dengan timezone; kolom `TIMESTAMPTZ` | — |

---

## 8. Arsitektur Backend

### 8.1 Diagram komponen

```mermaid
flowchart TB
    subgraph Klien
      FE["Frontend SPA<br/>login kode akses + X-API-Key"]
      WA["Aplikasi WhatsApp<br/>agen &amp; solver"]
    end

    NGINX["Nginx · TLS<br/>api.stc.syfa.site"]

    subgraph Backend["FastAPI · uvicorn --workers 2"]
      API["REST API<br/>/api/* + /health"]
      WH["Webhook<br/>POST /webhooks/waha"]
      PIPE["Waterfall matcher<br/>rule → reply → chain → LLM"]
      MED["Media store<br/>/app/media · volume"]
      CRON["Cron host<br/>health 5m · reminder 2j · backup harian"]
    end

    PG[("PostgreSQL 16<br/>10 tabel")]
    WAHA["WAHA (WEBJS)<br/>session: default"]
    TG["Telegram Bot API"]

    FE --> NGINX --> API
    WA --> WAHA
    WAHA -- "webhook: message, message.ack" --> NGINX --> WH --> PIPE
    API --> PG
    PIPE --> PG
    WH --> MED
    API --> WAHA
    CRON --> API
    CRON --> TG
    API --> TG
```

### 8.2 Titik masuk & runtime

| Aspek | Nilai | Lokasi |
|---|---|---|
| App title | `"IT Tracker"` — sudah di-rename dari `"Moban FU Tracker"` (§15.4 A1) | `main-v1-1.py:199` |
| Version (Swagger) | `"1.34.0"` | `main-v1-1.py:211` |
| Docs / OpenAPI | `/docs`, `/redoc`, `/openapi.json` | `main-v1-1.py:212-213` |
| OpenAPI tags | `Auth, Cases, Groups, Lookup, Solver Contacts, Reminders, Webhooks, Media, System` | `main-v1-1.py:215-224` |
| Startup (`lifespan`) | 1) `_ensure_alert_state_table()` 2) `_seed_default_group()` 3) `await _fetch_contacts()` | `main-v1-1.py:193-197` |
| Shutdown hook | tidak ada (pool tidak ditutup eksplisit) | `main-v1-1.py:192-197` |
| Log | logger `moban-tracker` (**nama lama**, §15.4), format `%(asctime)s %(levelname)s [%(name)s] %(message)s`, level `INFO` | `main-v1-1.py:48-53` |
| Request ID | header respons `X-Request-ID` (8 char hex); log akses `METHOD PATH STATUS DURASI [reqid]` | `main-v1-1.py:248-256` |
| Security headers | `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `X-XSS-Protection: 1; mode=block`, `Referrer-Policy: strict-origin-when-cross-origin` | `main-v1-1.py:237-245` |
| CORS | `ALLOWED_ORIGINS` (split koma, default `http://localhost:3000`), `allow_credentials=True`, methods/headers `*` | `main-v1-1.py:225-235` |
| Pool DB | `min_size=2`, `max_size=10`, `row_factory=dict_row`, commit manual | `main-v1-1.py:304-317` |
| Container | `python:3.13-slim`, user non-root `moban` (**nama lama**, §15.4), `EXPOSE 8000`, healthcheck `curl -f http://localhost:8000/health` (`30s/5s/3/10s`) | `Dockerfile:21-61` |
| CMD | `uvicorn main-v1-1:app --host 0.0.0.0 --port 8000 --workers 2 --log-level info --access-log --proxy-headers --forwarded-allow-ips *` | `Dockerfile:54-61` |

> ⚠️ `WORKERS=4` dan `LOG_LEVEL=warning` di `docker-compose.prod.yml` **tidak berefek** — CMD Dockerfile hardcoded `2`/`info`. `run.sh` (dan systemd unit) membaca `WORKERS`/`LOG_LEVEL`; Docker tidak.

### 8.3 Pipeline pencocokan pesan masuk (inti sistem)

**Urutan waterfall** — `handle_message` (`main-v1-1.py:1094-1197`):

| # | Langkah | Detail | `file:line` |
|---|---|---|---|
| 0 | Skip `fromMe=true` | Pesan yang dikirim akun bot sendiri diabaikan | `1099-1101` |
| 1 | Filter grup | `_get_group_by_chat` = `wa_groups WHERE chat_id = %s AND is_active = true`; tak terdaftar → skip | `1102-1105` |
| 2 | Skip kosong | Tanpa `body` **dan** tanpa `hasMedia` → skip | `1112-1113` |
| 3 | Unduh media | `_download_and_rewrite_media` (anti-SSRF, simpan ke `MEDIA_DIR`) | `1120` |
| 4 | Rewrite mention | `rewrite_mentions` untuk **parsing & `progress_updates`**; DB tetap simpan body mentah | `1121-1125` |
| 5 | Rekam pesan | `store_message` — semua pesan, `ON CONFLICT (wa_message_id) DO NOTHING` | `1125` |
| 6 | Resolve nama | `resolve_contact_name` → `wa_messages.author_name` (kegagalan non-fatal) | `1128-1134` |
| 7 | Regex INC | `\bINC\d{9,}\b` (case-insensitive) → `find_case_by_code` → `source=rule` | `478`, `1139-1141` |
| 8 | Kode non-INC persis | Hanya bila ada keyword status: cocokkan **persis** `case_code` terbuka di grup ini, guard `(?<![\w-])…(?![\w-])` → `rule` | `1142-1149` |
| 9 | Reply / chain | `find_case_by_chain(quoted)` → `source` = `reply` (depth 0) / `chain` | `1150-1154` |
| 10 | LLM fallback | Hanya bila case belum ketemu; ambang confidence **≥ 0.7** | `1155-1164` |
| 11 | Verifikasi grup | `case.group_id != group.id` → case dibatalkan (anti false-positive lintas grup) | `1166-1169` |
| 12 | Auto in_progress | Case `open` + tanpa keyword status → `in_progress` (v1.29) | `1170-1174` |
| 13 | Label crawl | `crawl=True` dan source ∈ {reply, chain, rule} → `source=crawl` | `1179-1180` |
| 14 | Tulis DB | `link_and_update` | `1181-1184` |
| 15 | Notifikasi | `notify_case_reply` ke grup default (kegagalan non-fatal) | `1185-1189` |

**Parser aturan** — `parse_rule` (`main-v1-1.py:500-527`):

| Elemen | Nilai literal |
|---|---|
| Regex INC | `\bINC\d{9,}\b` (re.I) → di-uppercase |
| Persen | `(\d{1,3})\s*%` → `min(100, int(...))`; **hanya dihitung, tidak ditulis ke DB** |
| Keyword `done` | `done, selesai, beres, kelar, solved, closed, terkirim, completed` — word-boundary `\b…\b` |
| Guard negasi | negator `belum / masih belum / belum bisa / belum sempat` berakhir **0 < jarak ≤ 60 char** di kiri keyword, dan tak ada pemisah klausa `tapi / tapi mungkin / namun / eh sudah / ternyata sudah` |
| Keyword `in_progress` | `proses, progress, diproses, otw, dicek, cek dulu, follow up, fu` (word-boundary) |
| Keyword `issue` | **DIHAPUS sejak v1.29** — "kendala/gagal/error" tidak lagi mengubah status |
| Prioritas | `done` menang atas `in_progress` (elif) |

**Chain traversal** — `find_case_by_chain` (`main-v1-1.py:627-654`): maksimum `MAX_CHAIN_DEPTH = 5` (`:184`). Tiap iterasi: (a) cari case dengan `wa_message_id = cur` **atau** `LIKE %cur%` → `reply` bila depth 0, `chain` bila lebih; (b) telusuri `wa_messages(quoted_id, case_id)`; (c) bila `case_id` terisi → ambil case → `chain`; (d) naik ke `quoted_id`. Semua query memakai `deleted_at IS NULL`.

**LLM fallback** — `parse_llm` (`main-v1-1.py:1026-1089`): POST `{LLM_BASE_URL}/chat/completions` (default `https://9router.tefambo.site/v1`), model `OPENROUTER_MODEL` (default `WAHA`), bearer `OPENROUTER_API_KEY`; `response_format=json_object`; timeout 30 s; retry `LLM_MAX_RETRIES=3` backoff `1.0 × 2^attempt` (hanya 5xx / error jaringan / JSON rusak). Respons divalidasi: `status` ∈ {`done`, `in_progress`, null}, `confidence` di-clamp 0–1.

**Penulisan hasil** — `link_and_update` (`main-v1-1.py:667-690`), satu transaksi:
1. `UPDATE wa_messages SET case_id = %s WHERE wa_message_id = %s`
2. `INSERT INTO progress_updates (…) ON CONFLICT (wa_message_id) DO NOTHING`
3. `UPDATE cases SET status = %s, updated_at = now()` bila ada status; bila tidak, **tetap** `UPDATE cases SET updated_at = now()` (v1.25 — semua balasan ter-link mengangkat `updated_at`)

**Notifikasi balasan** — `notify_case_reply` (`main-v1-1.py:693-793`):

```
💬 Update Case
Ticket Remedy : INC01239221 | Case ID : 1-SO9BLGS (IH 141410121054)
Dibalas oleh IT - SMOPS:
"silahkan dilakukan pelurusan realm …"
Status: in_progress     ← hanya bila balasan mengubah status
```
- Identifier = semua yang ada (`Ticket Remedy` dan/atau `Case ID`, dipisah ` | `) + `(IH <no_indihome>)`; keduanya kosong → `Case : <case_code>`.
- Kutipan dipotong `NOTIF_BODY_MAX = 300` char + `…`; newline jadi spasi.
- **Guard anti-loop:** grup default == grup asal balasan → tidak dikirim.
- Media (v1.28): file dari `MEDIA_DIR` dikirim via `sendImage`/`sendFile` dengan notif sebagai caption; gagal → fallback teks polos.
- Seluruh fungsi dibungkus `try/except` — **tidak pernah** menggagalkan pemrosesan webhook.

**Ack** — `handle_ack` (`main-v1-1.py:1200-1207`): `ackName` dari payload WAHA disimpan apa adanya ke `cases.ack` (`VARCHAR(20)`).

### 8.4 Model multi-grup

| Konsep | Perilaku |
|---|---|
| Grup aktif | `wa_groups.is_active = true`; webhook dari grup tak terdaftar diabaikan |
| Grup default | `is_default = true`, **maks 1 baris** (partial unique index `idx_wa_groups_default`). Fallback bila `group_id` kosong; tujuan notifikasi balasan & test-send |
| Tersembunyi dari switcher | `GET /api/groups` menyaring grup default; admin pakai `?include_default=true` |
| Seed awal | Bila `wa_groups` kosong dan `WA_GROUP_ID` diisi → insert `('Grup A', WA_GROUP_ID)` + backfill `cases.group_id IS NULL` |
| Isolasi per-grup | Case hanya ter-link dari pesan di grup yang sama dengannya |
| Bot harus di-add | Ke **semua** grup aktif termasuk grup default, agar webhook diterima |

### 8.5 Model data (10 tabel)

| Tabel | Peran |
|---|---|
| `cases` | Entitas utama: `case_code` (UNIQUE), `fields` JSONB, `message_text`, `wa_message_id` (root), `status`, `ack`, lookup FK, `group_id`, `mentions`, `reminder_count`, `last_reminder_at`, `deleted_at` |
| `wa_messages` | **Semua** pesan grup (bahan rantai): `wa_message_id` PK, `quoted_id`, `case_id`, `author`, `author_name`, `body` (mentah), `from_me`, `media_url`, `media_type` |
| `progress_updates` | Hasil parse per pesan: `parsed_status`, `parsed_note`, `source`, `confidence`; `wa_message_id` UNIQUE (dedup alami) |
| `areas`, `regionals`, `sumber_tickets`, `jenis_cases` | Tabel lookup (Area → Regional bergantung pada area) |
| `wa_groups` | Grup WA tujuan + `is_active` + `is_default` |
| `solver_contacts` | Kontak solver untuk dropdown mention; unique `phone_number` **hanya di antara baris aktif** |
| `reminder_log` | Riwayat reminder: `case_id`, `wa_message_id`, `message`, `triggered_by` (`manual`/`cron`) |
| `alert_state` | State monitoring Telegram — **dibuat runtime oleh kode**, bukan file SQL (§11.3) |

Detail kolom/tip/constraint per tabel: §11.1.

**Enum yang dipakai sistem:**

| Domain | Nilai | Catatan |
|---|---|---|
| `cases.status` | `open`, `in_progress`, `done` | `issue` historis (v1.29+), tidak bisa di-set |
| `cases.case_type` | `non_order`, `non_ao`, `ao`, `mobile` | Legacy: `stc`, `smooa`, `ufo`, `other` → `non_order` |
| `progress_updates.source` | `rule`, `reply`, `chain`, `llm`, `crawl`, `manual` | `manual` hanya dari koreksi status |
| `cases.ack` | `PENDING`, `SERVER`, `DEVICE`, `READ` | Disimpan apa adanya dari `ackName` |

### 8.6 Klien WAHA

| Operasi | Endpoint | Catatan |
|---|---|---|
| Kirim teks | `POST {WAHA_URL}/api/sendText` | payload `session`, `chatId`, `text`, `mentions?`, `reply_to?`; timeout 30 s; **tanpa retry**; error → `502` |
| Kirim media | `POST {WAHA_URL}/api/{sendImage\|sendFile}` | `file: {mimetype, filename, data:<base64>}`; timeout 60 s; `sendImage` bila MIME `image/*`, sisanya `sendFile` |
| Ambil grup | `GET {WAHA_URL}/api/{session}/chats` | Dipakai `GET /api/waha/groups`; disaring `kind == "group"` |
| Crawl | `GET {WAHA_URL}/api/{session}/chats/{chatId}/messages` | `limit` (default 200), `download=true`, timeout 60 s |
| Health | `GET {WAHA_URL}/api/sessions` | timeout 5 s; sehat bila `status == "WORKING"` |
| Kontak | `GET /api/contacts/all?session=&limit=500`; `GET /api/{session}/lids`; `GET /api/contacts?contactId=` | Resolusi nama; cache in-memory tanpa TTL |

**Media base64 (v1.26):** lampiran dikirim sebagai `file.data` (base64), bukan `file.url`. Jangan dikembalikan ke `file.url` — WAHA membalas 500.

### 8.7 Pelaporan, export, dan metrik

- **Satu SQL builder** `_cases_filter_sql` (`main-v1-1.py:1533-1601`) dipakai `GET /api/cases` **dan** `GET /api/cases/export.xlsx` → filter dashboard dan isi Excel **dijamin identik**.
- **Sort default** `ORDER BY c.updated_at DESC` (`:1740`).
- **Pencarian** `q` menyisir `case_code`, `title`, **dan seluruh `fields::text`** (v1.24) dengan 3 pola `%q%` (`:1590-1594`).
- **Tanggal** `created_at >= date_from`; `created_at < date_to + 1 hari` → inklusif (`:1596-1600`).
- **Pagination** hanya bila `limit` dikirim (1–100); `count_sql` dibangun dari `sql[sql.index(" FROM cases c"):]` tanpa ` ORDER BY` (`:1742-1753`).
- **Export** kolom: `ID, Case Code, Jenis Case, Judul, Status, Nomor Indihome, Area, Regional, Sumber Ticket, Grup WA, Reminder Count, Created At, Updated At`; tanggal `%Y-%m-%d %H:%M`; header bold; sheet `cases`; nama file `cases_export_YYYYMMDD-HHMM.xlsx`.
- **Metrik** dihitung SQL di DB, agregasi (avg/median/distribusi) di Python `statistics.median` — definisi t0/t1 di §9.8.

### 8.8 Autentikasi, rate limit, keamanan

| Mekanisme | Detail | Lokasi |
|---|---|---|
| API key | Header `X-API-Key`; bila `BACKEND_API_KEY` kosong → **semua request lolos tanpa log** | `main-v1-1.py:290-297` |
| Login FE | `POST /api/auth/access-code` → JWT HS256, klaim `{sub:"access_code", exp}`; `ACCESS_CODES` kosong → `503`; kode salah → `401` | `main-v1-1.py:1373-1389` |
| Verifikasi JWT | **Tidak ada** `jwt.decode` di backend — token murni gate FE | temuan §13 (T4) |
| Rate limit umum | Sliding window in-memory 60 s, key = IP; `RATE_LIMIT_RPM` default 60; `429 "Rate limit exceeded. Try again later."`; tanpa header `Retry-After` | `main-v1-1.py:260-271` |
| Rate limit login | `LOGIN_RATE_LIMIT` default 5/menit/IP; `429 "Terlalu banyak percobaan login. Coba lagi nanti."`; semua percobaan dihitung | `main-v1-1.py:275-289` |
| Rate limit nginx | `limit_req_zone … zone=auth_login:10m rate=5r/m` + `burst=2 nodelay` pada `location /api/auth/` | `nginx.conf:10,34-36` |
| Webhook secret | Bila `WAHA_WEBHOOK_SECRET` ter-set: header `X-Webhook-Secret` **atau** query `?token=`; `secrets.compare_digest`; gagal → `401` | `main-v1-1.py:2651-2661` |
| Anti-SSRF media | `_is_waha_url`: skema http/https, **hostname identik** `WAHA_URL`, port efektif identik; redirect tidak diikuti | `main-v1-1.py:152-172` |
| Path media | Nama file divalidasi `^[a-f0-9\-]+\.\w{2,5}$` → `400` bila tidak cocok | `main-v1-1.py:3136-3138` |
| Endpoint tanpa auth | `/health`, `POST /webhooks/waha`, `POST /api/auth/access-code`, `GET /api/media/file/{filename}`, `GET /api/media/proxy` | — |
| Nginx | `client_max_body_size 25m` (wajib untuk lampiran base64); port app bind `127.0.0.1:8000` | `nginx.conf:31`, `docker-compose.yml` |

> **Kapasitas rate limit nyata** = `RATE_LIMIT_RPM × jumlah worker` karena bucket in-memory per-proses (`main-v1-1.py:190`). Dengan 2 worker dan 60/menit → efektif 120/menit/IP.

### 8.9 Peta endpoint (grup fungsional)

```mermaid
flowchart LR
    A["Auth<br/>POST /api/auth/access-code"] --> B["Cases<br/>preview · test-send · POST · GET list ·<br/>GET detail · status · replies · DELETE · export.xlsx"]
    B --> C["Lookup<br/>areas · areas/id/regionals ·<br/>sumber-tickets · jenis-cases"]
    B --> D["Reminders<br/>POST id/reminder · GET id/reminder ·<br/>POST run · GET pending"]
    B --> E["Groups<br/>CRUD /api/groups ·<br/>GET /api/waha/groups"]
    B --> F["Solver Contacts<br/>CRUD /api/solver-contacts"]
    B --> G["Media<br/>GET file/filename · GET proxy"]
    B --> H["System<br/>GET /health · POST health/alert ·<br/>POST crawl · metrics (2 endpoint)"]
    H --> I["Webhooks<br/>POST /webhooks/waha"]
```

---

## 9. Spesifikasi API (ringkas)

> Detail per-endpoint lengkap (contoh request/response, tabel error) ada di `API-contract-frontend.md` v1.34. Bagian ini peta ringkas + aturan yang **wajib** dipatuhi FE. Base URL prod: `https://api.stc.syfa.site`; base path `/api`; format JSON.
>
> ⚠️ **`API-contract-frontend.md` masih punya cacat yang belum dibetulkan — bila dokumen itu dan §9 ini berbeda, ikuti §9 ini.** Daftar cacat (audit 16 Okt 2026): contoh response `GET /api/cases/{id}/reminder` tertukar dengan `POST /api/reminders/run`; blok `Response 200` kosong pada contract §5.1, contract §7.1, contract §7.4; heading contract §5.4 menggabungkan `PUT` dan `DELETE` sehingga `DELETE /api/solver-contacts/{id}` tidak terdokumentasi; kontradiksi status grup nonaktif (`404` di satu tempat, `409` di tempat lain — yang benar: tak dikenal `422`, nonaktif `409`); contoh `GET /api/jenis-cases` belum memuat `AO`; klaim field wajib per jenis case tidak ditegakkan backend (T1); nomor seksi melompat (7.1→7.2→7.4) dan sub-heading contract §12 memakai penomoran 11.x. Lihat §13.1 untuk daftar pembetulan.

### 9.1 Ringkasan endpoint

| # | Method & Path | Auth | Guna |
|---|---|---|---|
| 1 | `POST /api/auth/access-code` | — | Login kode akses → JWT |
| 2 | `POST /api/cases/preview` | API key | Render teks case tanpa kirim/DB |
| 3 | `POST /api/cases/test-send` | API key | Kirim ke grup default tanpa buat case |
| 4 | `POST /api/cases` | API key | Buat + kirim case (201) |
| 5 | `GET /api/cases` | API key | Daftar case (filter + pagination opt-in) |
| 6 | `GET /api/cases/export.xlsx` | API key | Export Excel (filter identik #5) |
| 7 | `GET /api/cases/{id}` | API key | Detail + timeline + updates + participants |
| 8 | `POST /api/cases/{id}/status` | API key | Koreksi status manual |
| 9 | `POST /api/cases/{id}/replies` | API key | Balas solver dari web (teks/lampiran) |
| 10 | `DELETE /api/cases/{id}` | API key | Soft delete |
| 11 | `POST /api/cases/{id}/reminder` | API key | Reminder manual |
| 12 | `GET /api/cases/{id}/reminder` | API key | Riwayat reminder case |
| 13 | `POST /api/reminders/run` | API key | Auto-reminder batch (cron) |
| 14 | `GET /api/reminders/pending` | API key | Case yang perlu reminder |
| 15 | `GET /api/areas` | API key | Lookup Area |
| 16 | `GET /api/areas/{id}/regionals` | API key | Lookup Regional per Area |
| 17 | `GET /api/sumber-tickets` | API key | Lookup Sumber Ticket |
| 18 | `GET /api/jenis-cases` | API key | Lookup Jenis Case |
| 19 | `GET /api/groups` | API key | Grup untuk switcher (safe by default) |
| 20 | `POST /api/groups` | API key | Tambah grup (201) |
| 21 | `GET /api/groups/{id}` | API key | Detail grup |
| 22 | `PUT /api/groups/{id}` | API key | Update grup (partial) |
| 23 | `DELETE /api/groups/{id}` | API key | Nonaktifkan grup (soft) |
| 24 | `GET /api/waha/groups` | API key | Discovery grup dari WAHA |
| 25 | `GET /api/solver-contacts` | API key | Daftar kontak solver |
| 26 | `POST /api/solver-contacts` | API key | Tambah kontak (201) |
| 27 | `GET /api/solver-contacts/{id}` | API key | Detail kontak |
| 28 | `PUT /api/solver-contacts/{id}` | API key | Update kontak |
| 29 | `DELETE /api/solver-contacts/{id}` | API key | Soft delete kontak |
| 30 | `POST /api/crawl` | API key | Backfill histori (berat) |
| 31 | `GET /api/metrics/solver-response` | API key | Metrik response time (menit) |
| 32 | `GET /api/metrics/solver-resolution` | API key | Metrik resolution time (jam) |
| 33 | `GET /api/media/file/{filename}` | — | Serve media lokal |
| 34 | `GET /api/media/proxy` | — | Proxy media WAHA (legacy) |
| 35 | `GET /health` | — | Health detail |
| 36 | `POST /api/health/alert` | API key | Cek + kirim alert Telegram (cron) |
| 37 | `POST /webhooks/waha` | secret | Receiver event WAHA |

### 9.2 Auth

- **API key:** header `X-API-Key: <key>` untuk semua endpoint ber-API key.
- **Login:** `POST /api/auth/access-code` `{"code": "..."}` → `200 {"token", "expires_in"}`; `401` kode salah; `503` access codes belum dikonfigurasi. `expires_in` = `JWT_EXPIRY_HOURS × 3600` (default 86400).
- FE menyimpan token di `localStorage` dan mengirim `Authorization: Bearer <token>` — **backend tidak memverifikasinya**; gate hanya di sisi FE.
- **`429`** rate limit: umum `RATE_LIMIT_RPM`, login `LOGIN_RATE_LIMIT` (dan nginx).

### 9.3 Case: preview, test-send, create

**Body `CaseIn`** (dipakai #2, #3, #4):

| Field | Tipe | Wajib | Catatan |
|---|---|---|---|
| `group_id` | int ≥1 / null | Tidak | Dikosongkan → grup default. **Jangan kirim `0` atau `""`** |
| `area_id` | int / null | Tidak | Harus ID valid → `422` bila tidak dikenal |
| `regional_id` | int / null | Tidak | Harus valid → `422` |
| `sumber_ticket` | string / null | Tidak | `STC` / `Grapari` / `Web IT` |
| `jenis_case` | string / null | Tidak | `Non Order` / `Non AO` / `AO` / `Mobile`; nilai lain → `Non Order`. Resolusi ID lookup **case-sensitive** |
| `asal_grapari` | string / null | Tidak | Free text; hanya relevan bila sumber = Grapari |
| `mentions` | `[{number, name?}]` | Tidak | `number` format internasional tanpa `+` (`628xxx`) |
| `custom_header` | string / null | Tidak | Placeholder `{phone}` |
| `fields` | object | Tidak | Lihat tabel di bawah |

**`fields` per jenis case** (`templates.py:22-67`):

| Key | Non Order | Non AO | AO | Mobile |
|---|---|---|---|---|
| `ticket_remedy` (pola `INC\d{9,}`) | ✓ | ✓ | ✓ | ✓ |
| `case_id` | ✓ | ✓ | ✓ | ✓ |
| `no_indihome` | ✓ | ✓ | ✓ | — |
| `order_id` | — | ✓ | ✓ | — |
| `last_milestone` | — | ✓ | ✓ | — |
| `msisdn` | — | — | — | ✓ |
| `request_case` | ✓ | ✓ | ✓ | ✓ |
| `detail_case` | ✓ | ✓ | ✓ | ✓ |
| `link_evidence` | ✓ | ✓ | ✓ | ✓ |

`link_evidence`: array `{label?, url}`; `url` boleh string tunggal **atau** array. String URL lama tetap diterima.

**Mode textarea:** FE menyediakan placeholder wording per jenis case (`templates.py:86`) untuk disalin user.

**Urutan validasi `POST /api/cases`** — semua sebelum pesan dikirim:

| # | Kondisi | Status | Pesan |
|---|---|---|---|
| 1 | `fields.ticket_remedy` tidak match `INC\d{9,}` | `422` | `fields.ticket_remedy harus format INC (contoh: INC012345678). Kode non-INC kirim sebagai fields.case_id` |
| 2 | `custom_header` berisi `{phone}` tapi `mentions` kosong | `422` | `custom_header mengandung {phone} tapi mentions kosong — pilih solver dari dropdown (atau hapus token {phone})` |
| 3 | `area_id` tidak dikenal | `422` | `area_id <n> tidak dikenal — pakai ID dari GET /api/areas` |
| 4 | `regional_id` tidak dikenal | `422` | `regional_id <n> tidak dikenal — pakai ID dari GET /api/areas/<area>/regionals` |
| 5 | `group_id` diisi tapi tidak terdaftar | `422` | `group_id <n> tidak dikenal — pakai ID dari GET /api/groups atau hilangkan field untuk mengirim ke grup default` |
| 6 | Grup terdaftar tapi nonaktif | `409` | `grup '<nama>' sedang dinonaktifkan admin — pilih grup lain atau minta grup ini diaktifkan kembali` |
| 7 | `group_id` kosong & tak ada grup default | `400` | `Belum ada grup default — kirim group_id atau set via PUT /api/groups/{id} {"is_default": true}` |
| 8 | WAHA error / tidak terjangkau | `502` | `WAHA error: <status>` / `WAHA service unavailable` (case **tidak** tersimpan) |

**Urutan eksekusi:** validasi → `_render_case_payload` → `waha_send` → upsert `cases` + insert `wa_messages` root (satu transaksi) → `201 {id, case_code, wa_message_id, group_id, group_name, text}`. `text` = pesan final persis yang terkirim; tampilkan di toast/modal sukses.

**Re-FU:** `ON CONFLICT (case_code) DO UPDATE` → `status='open'`, `deleted_at=NULL`, jangkar `wa_message_id` diperbarui. Bukan error.

**`case_code`** = `ticket_remedy` **atau** `case_id` (fallback), `strip().upper()`; bisa `null` (tidak konflik → selalu baris baru).

**Auto-mention:** token `@628xxx` (juga `@+62 811-9298-880`, `@081…`) di `custom_header`/`detail_case`/teks lain otomatis diekstrak dan digabung dengan `mentions` dropdown (dedupe), dinormalisasi ke `^62\d{7,14}$`.

**`test-send` (#3):** body sama + `test_group_id` opsional.
- `test_group_id` tidak dikenal → `422`; grup test nonaktif → `409`; kosong & tak ada grup default → `400`.
- **Tidak membuat row case.** Respons `{ok, test_group_id, test_group_name, wa_message_id, text}`.
- ⚠️ Berbeda dari `POST /api/cases`: `group_id` hanya divalidasi bila **diisi** dan **tidak** fallback ke grup default. UI test jangan mengirim `group_id` yang salah.

**Format pesan WhatsApp** (contoh Non AO):
```
punten rekan @6281113021236 mohon bantuannya untuk case Non AO ada 1 case lagi

#STC_Non AO_Area 1_Sumbagut
Ticket Remedy : INC000023470570
Order ID : MOk4260811023440131b25f60
Nomer Indihome : 0211234567
Last Milestone : TSEL_ACTIVATION_FALLOUT
Request Case : Mohon bantuannya follow up aktivasi
Detail Case : Pelanggan kendala aktivasi...
Link Evidence :
https://prnt.sc/example1
https://prnt.sc/example2
```
- Header compact `#SumberTicket_JenisCase_Area_Regional`; bagian kosong dilewati.
- `Asal Grapari : …` muncul hanya bila diisi.
- Header mention default: `punten rekan @<phone> mohon bantuannya untuk case <Label> ada 1 case lagi`.
- `custom_header` tanpa `{phone}` → mention ditambahkan di akhir; dengan `{phone}` → token diganti **semua** nomor mention.
- Mention memakai **nomor**, bukan nama; WA merender nama dari phone book.

### 9.4 Daftar case & filter (#5)

| Param | Tipe | Keterangan |
|---|---|---|
| `status` | enum | `open`/`in_progress`/`done`/`issue` (historis) |
| `case_type` | string | nama jenis case persis |
| `area_id`, `regional_id`, `group_id` | int | filter FK |
| `sumber_ticket` | string | nama sumber ticket |
| `q` | string | substring case-insensitive di `case_code`, `title`, **dan seluruh `fields`** |
| `date_from`, `date_to` | `YYYY-MM-DD` | `created_at` inklusif dua ujung; salah format / `date_from > date_to` → `422` |
| `include_deleted` | bool | default false |
| `page` | int ≥1 | hanya berlaku bila `limit` dikirim |
| `limit` | 1–100 | **opt-in**; tanpa `limit` → array polos (legacy); `limit=0`/`>100`/`page<1` → `422` |

**Respons:** array polos (`id, case_code, case_type, title, status, ack, area_id, regional_id, sumber_ticket_id, jenis_case_id, asal_grapari, no_indihome, group_id, group_name, area_name, regional_name, sumber_ticket_name, jenis_case_name, created_at, updated_at`) **atau** envelope `{data, pagination:{page, limit, total, total_pages, has_next, has_prev}}` bila `limit` diisi. Urut `updated_at DESC`.

### 9.5 Export Excel (#6)

- Query param **identik** #5 (tanpa `page`/`limit`).
- `Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`; `Content-Disposition: attachment; filename="cases_export_YYYYMMDD-HHMM.xlsx"`.
- **Wajib `fetch` + blob**, jangan `<a href>` langsung — API key bocor di URL/history.
- 0 baris lolos filter → file tetap valid (header saja).

### 9.6 Detail, status, balasan (#7–#9)

**`GET /api/cases/{id}`** → `{case, messages[], updates[], participants[]}`; case terhapus → `404 "Case not found or already deleted"`.
- `messages[]`: `{wa_message_id, quoted_id, author, author_name, body, from_me, media_url, media_type, created_at}`, urut `created_at`; membentuk pohon via `quoted_id` (root = `case.wa_message_id`, satu-satunya `from_me: true`).
- `updates[]`: baris `progress_updates` (`source`: `rule`/`reply`/`chain`/`llm`/`crawl`/`manual`).
- `participants[]`: `{author, name}` — hanya penulis `from_me=false`.
- `media_url` = URL lokal siap pakai (`/api/media/file/{uuid}.{ext}`) atau proxy legacy; `media_type` = MIME.

**`POST /api/cases/{id}/status`** — `{"status": "open"|"in_progress"|"done", "note"?: "..."}` → `200 {"ok": true}`.
- `issue` → `422`; `404` bila case tidak ada (query **tidak** menyaring `deleted_at`).
- Efek: insert `wa_messages` (`author='manual'`, id `manual-<case>-<status>-<uuid>`) + `UPDATE cases SET status, updated_at`.

**`POST /api/cases/{id}/replies`** — `{"message"?, "reply_to_wa_message_id", "attachments"?:[{filename, mimetype, data_base64}], "mentions"?:[…]}`

| Aturan | Nilai | Gagal |
|---|---|---|
| Minimal salah satu `message`/`attachments` | — | `422 "message atau attachments wajib diisi"` |
| Maksimum lampiran | 3 | `422 "Maksimal 3 file per balasan"` |
| MIME | `image/jpeg`, `image/png`, `image/webp`, `video/mp4`, `application/pdf` | `422 "mimetype <x> tidak didukung"` |
| Ukuran decoded per file | ≤ 5 MB | `413 "<nama> melebihi 5 MB"` |
| `reply_to_wa_message_id` | harus milik case ini | `422 "reply_to_wa_message_id bukan pesan case ini"` |
| Base64 valid | `b64decode(validate=True)` | `422 "data_base64 <nama> bukan base64 valid"` |
| Case | ada & belum dihapus | `404 "Case not found"` |
| Grup aktif | ada | `400 "Case tidak punya grup aktif"` |
| WAHA | sukses | `502` |

- Image → `sendImage`; lainnya → `sendFile`. **Teks jadi caption** (anti double-send v1.27): media pertama memakai `message`, berikutnya caption kosong / nama file.
- `mentions` **hanya berlaku untuk balasan teks** (media-only mengabaikan mentions).
- Respons `{ok, wa_message_ids[]}` → setelah sukses **wajib refresh timeline**.
- Paste image clipboard: handler `paste` → `FileReader.readAsDataURL` → buang prefix `data:...;base64,`.
- ⚠️ Selalu balas dari web, **jangan dari HP akun bot** — pesan `fromMe=true` manual dilewati webhook → rantai putus.

### 9.7 Reminder (#11–#14)

| Endpoint | Param | Perilaku |
|---|---|---|
| `POST /api/cases/{id}/reminder` | body `{message?}` | `404` case tidak ada; `400 "Case sudah done, tidak perlu reminder"`; `400 "Case tidak punya grup aktif"`. Kirim `reply_to` = pesan root + mention dari `cases.mentions`; naikkan `reminder_count`, set `last_reminder_at`, catat `reminder_log` (`triggered_by='manual'`), **dan** simpan ke `wa_messages` (`quoted_id`→root) agar balasan solver ke reminder tetap ter-chain |
| `GET /api/cases/{id}/reminder` | — | Array `reminder_log` urut `created_at`; `404` bila case tidak ada (tidak menyaring `deleted_at`) |
| `POST /api/reminders/run` | `hours` (default 2) | Cari `status IN ('open','in_progress')`, `deleted_at IS NULL`, `updated_at < now()-hours`, `last_reminder_at IS NULL OR < now()-hours`; kirim berurutan; error per case tidak menggagalkan batch. Respons `{checked, reminded, cases[]}` |
| `GET /api/reminders/pending` | `hours` (2), `limit` (50), `area_id`, `regional_id`, `sumber_ticket`, `jenis_case`, `group_id` | Array polos + kolom `idle_hours` |

### 9.8 Metrik (#31–#32)

**Response time (menit)** — t0 = `cases.created_at`; t1 = **balasan PERTAMA** solver (`wa_messages.from_me=false`, `case_id` ter-link, `ORDER BY created_at ASC`); semua status ikut. `response_menit = (t1−t0)/60`.
- Bucket: `lt_5` <5, `b_5_15` 5–15, `b_15_60` 15–60, `gt_60` >60.
- `summary`: `{total_cases, avg_menit, min_menit, max_menit, median_menit}` — semua `null` bila kosong; `distribution` = `[]` bila kosong.
- `per_solver` urut `median_menit ASC`; solver kosong → `"unknown"`.
- `unanswered`: case **tanpa satupun** balasan solver (tanpa filter status); `menit_sejak_kirim` dihitung live. Filter `solver` di-skip pada query ini.

**Resolution time (jam)** — t0 sama; t1 = **balasan TERAKHIR** solver (`ORDER BY created_at DESC`); syarat `cases.status = 'done'`. `resolution_jam = (t1−t0)/3600`.
- Bucket: `lt_1h`, `b_1_6h`, `b_6_24h`, `gt_24h`.
- `unresolved` = **dua** kelompok: (a) status ≠ done; (b) status `done` **tanpa** balasan solver → FE membedakan lewat field `status`.

Filter keduanya identik: `regional_id`, `solver` (substring case-insensitive pada nama solver), `group_id`, `date_from`, `date_to` (inklusif); tanggal salah → `422`.

> ⚠️ **Satuan berbeda:** response = **menit**, resolution = **jam**. Jangan campur formatter.

### 9.9 Health & alert (#35–#36)

`GET /health` (tanpa auth) → **selalu HTTP 200**:

```json
{
  "status": "ok",
  "timestamp": "2026-10-09T03:12:44.123456+00:00",
  "uptime_seconds": 84512,
  "db": { "status": "ok", "error": null },
  "waha": {
    "http": { "status": "ok", "status_code": 200 },
    "sessions": [{ "name": "default", "status": "WORKING", "push_name": "STC Tracker", "engine_state": "CONNECTED" }]
  }
}
```
- `degraded` bila: DB `SELECT 1` gagal, HTTP WAHA ≠ 200, atau **tidak ada** session `WORKING`. `waha.error` hanya muncul bila WAHA tidak terjangkau.
- FE **wajib** membaca `body.status`, bukan `resp.ok`.
- Session `SCAN_QR_CODE` = perlu scan QR ulang → tampilkan instruksi khusus.

`POST /api/health/alert` (cron 5 menit, `X-API-Key`): bandingkan dengan tabel `alert_state`, hasil `alerted` / `realerted` (masih down ≥ `HEALTH_ALERT_REMINDER_HOURS`, default 6) / `recovered` / `noop`. Key: `db`, `waha.http`, `waha.session:<nama>`. Tanpa token/chat Telegram → di-skip (log warning). FE tidak perlu memanggil endpoint ini.

### 9.10 Media (#33–#34)

- `GET /api/media/file/{filename}` — tanpa auth; nama divalidasi `^[a-f0-9\-]+\.\w{2,5}$` (`400` bila tidak match); `404` bila tidak ada; MIME dari ekstensi.
- `GET /api/media/proxy?url=…` — legacy; hanya mem-proxy URL dengan host+port **tepat** `WAHA_URL` (lain → `400 "Invalid media URL"`); migrasi malas: media diunduh, disimpan, lalu `media_url` di DB diperbarui ke `/api/media/file/...`.
- Media tersimpan di volume Docker `media_data` (`/app/media`) → **persist** meski container restart.

### 9.11 Grup & kontak solver (#19–#29)

**`GET /api/groups`** — SAFE BY DEFAULT: hanya grup `is_active=true` **dan** `is_default=false`. Param `include_inactive=true` / `include_default=true` untuk admin. (Param lama `is_active` diabaikan.)
**`POST/PUT /api/groups`** — `chat_id` wajib `^\d+@g\.us$` (salah → `422`); duplikat → `409`; `is_default: true` otomatis melepas default lama; `PUT` tanpa field yang berubah → `422`.
**`DELETE /api/groups/{id}`** — soft (`is_active=false`); `404` bila tidak ada / sudah nonaktif.
**`GET /api/waha/groups`** — baca langsung dari WAHA; `[{chat_id, name, registered, group_id, is_active, is_default}]`, yang **belum terdaftar di atas**; query `limit` (default 500), `search`; `502` bila WAHA error.
**Solver contacts** — `POST` wajib `name` + `phone_number` (unik di antara kontak aktif → `409`); `DELETE` soft (`is_active=false`) → `{ok:true}`; `q` mencari di nama **atau** role.

### 9.12 Crawl (#30)

- `POST /api/crawl?limit=200&group_id=` — 2 pass: (1) simpan semua pesan; (2) proses waterfall, `source` dilabeli `crawl`.
- Tanpa `group_id` → semua grup aktif berurutan. `404` bila grup tidak ada/nonaktif; `400` bila tidak ada grup aktif.
- Respons `{fetched, stored, updates_applied, store_errors, process_errors, groups:[{…}]}`.
- **Operasi berat** — jangan dipanggil otomatis dari UI utama; sediakan di halaman admin dengan konfirmasi.
- ⚠️ Pesan hasil crawl disimpan dengan `created_at` = **waktu crawl**, bukan waktu asli WA → latensi metrik bisa terlihat lebih besar (§10.6).

### 9.13 Format error

FastAPI standar `{"detail": "..."}`. `401` API key / webhook secret salah · `404` resource tidak ada · `409` konflik (grup nonaktif, duplikat) · `413` lampiran >5 MB · `422` validasi · `429` rate limit · `502` WAHA error. Bila `detail` berupa array (validasi Pydantic), ambil `msg` pertama.

---

## 10. Spesifikasi Frontend

### 10.1 Peta halaman & routing

```mermaid
flowchart TB
    LOGIN["/login<br/>Kode akses"] --> DASH
    DASH["/  Dashboard case<br/>tabel · filter bar · export"] --> DETAIL["/cases/:id<br/>timeline rantai · balas · ubah status"]
    DASH --> CREATE["/cases/new<br/>form dinamis · preview · test-send"]
    DASH --> MET["/metrics<br/>response &amp; resolution (tab)"]
    ADMIN["/admin<br/>grup · kontak solver · crawl · health"] --> DASH
```

| Halaman | Data utama | Aksi |
|---|---|---|
| Login | — | `POST /api/auth/access-code` → simpan token |
| Dashboard | `GET /api/cases` (+filter, `?page&limit`) | buka detail, buat case, export, refresh (polling 30 s / window focus) |
| Detail case | `GET /api/cases/{id}` | balas solver, ubah status, reminder manual, riwayat reminder, lihat media |
| Buat case | lookup (areas/regionals/sumber/jenis/grup/solver-contacts) | preview, test-send, kirim |
| Metrik | `GET /api/metrics/solver-response`, `…/solver-resolution` | filter, ganti tab satuan |
| Admin | `GET/POST/PUT/DELETE /api/groups`, `/api/solver-contacts`, `GET /api/waha/groups`, `POST /api/crawl`, `GET /health` | tambah grup dari discovery, set default, nonaktifkan, crawl |

### 10.2 Autentikasi & sesi

1. Token access-code disimpan di `localStorage`; tidak ada endpoint refresh/verify — saat API membalas `401`, arahkan user login ulang dan bersihkan token.
2. Setiap request **selain** endpoint tanpa-auth mengirim `X-API-Key` (dari env build, mis. `VITE_API_KEY` — **jangan** hardcode di repo).
3. `expires_in` = 86400 detik; FE boleh menampilkan countdown dan memaksa login ulang saat habis.

### 10.3 Form buat case (spesifikasi dinamis)

**Urutan render:** Area → Regional (dimuat setelah Area) → Sumber Ticket → *(Asal Grapari bila Grapari)* → Jenis Case → field sesuai jenis → Mention (multi-select) → Custom header → Preview/Test/Kirim.

**Perilaku wajib:**
- Ganti Area → **reset Regional** lalu `GET /api/areas/{id}/regionals`.
- Ganti Jenis Case → render ulang blok field (§9.3); jangan bawa nilai field yang tidak relevan.
- Sumber Ticket ≠ Grapari → sembunyikan `asal_grapari` dan jangan kirim nilainya.
- Grup tidak dipilih → **hilangkan `group_id`** dari payload.
- "Kirim Test" → kirim payload sama **tanpa** `group_id` (atau dengan `test_group_id` yang benar).
- "Kirim" → `POST /api/cases`, tampilkan `response.text`.
- Validasi klien sebelum kirim: `ticket_remedy` cocok `^INC\d{9,}$`; `{phone}` di custom header → minimal 1 mention; nomor mention `^62\d+$`.

**Mention (ala WhatsApp):** ketik `@` (awal teks atau setelah spasi) → dropdown terfilter dari `GET /api/solver-contacts` → `↑`/`↓`/`Enter`/klik pilih → chip `@Nama` + token `{phone}` disisipkan di posisi kursor. Satu token `{phone}` meng-expand ke **semua** nomor mention — placeholder per-orang tidak didukung. Semua token dihapus → kosongkan `mentions`.

### 10.4 Dashboard list

- Tabel: `Case Code`, `Nomor Indihome` (`no_indihome`, tanpa fetch detail), `Jenis Case`, `Judul`, `Status` (badge), `Ack` (indikator centang), `Grup`, `Regional`, `Reminder Count`, `Updated At`.
- Filter bar: status, jenis case, area, regional, grup, sumber ticket, pencarian `q`, rentang tanggal (`<input type="date">` + preset "7 hari"/"30 hari"/"bulan ini"). State filter dipakai **sekali** untuk list **dan** export.
- Pagination: kirim `page` + `limit` (disarankan 25/50) → baca `resp.data` + `resp.pagination`. Mode legacy (tanpa `limit`) → respons array, jangan akses `resp.data`.
- Polling `GET /api/cases` tiap 30 detik atau saat window fokus; halaman detail jangan lebih cepat dari 10 detik.

### 10.5 Detail case & balas

- Timeline: render `messages` sebagai thread bersarang (indent per level `quoted_id`) atau flat kronologis. Tandai `from_me=true` sebagai bot/agen; beri ikon khusus `updates[].source === "chain"` (eskalasi).
- Media: `media_type` `image/*` → `<img>`; `video/*` → `<video>`; lainnya → link unduh.
- Chip "Ditangani oleh": dari `participants`.
- Form balas: textarea + pemilih lampiran (maks 3, 5 MB/file) + handler paste; setelah sukses refresh timeline. Validasi MIME klien memakai whitelist yang sama.
- Tombol status: "Tandai selesai" (`done`) / "Buka ulang" (`open`) / "Sedang dikerjakan" (`in_progress`) — **jangan** tawarkan `issue`.
- Tombol Balas tetap aktif walau case `done`.

### 10.6 Halaman metrik — guardrail

| Guardrail | Alasan |
|---|---|
| KPI utama memakai **median**, bukan rata-rata | Rata-rata terseret case lambat/data crawl |
| Jangan tampilkan `max_menit`/`max_jam` mentah di KPI | Latensi data crawl bisa palsu besar |
| `per_solver` dibandingkan dengan median; kolom `jumlah_case` wajib tampil | 3 case ≠ 44 case |
| `unanswered`/`unresolved` **tidak** ikut total summary | Keduanya watchlist; jangan dijumlahkan |
| Satuan: response = menit, resolution = jam | Konversi hanya di layer tampilan |
| Format durasi | `<60` mnt → "7,9 mnt"; `≥60` → jam 1 desimal; `≥24 j` → hari; `null` → "—" |
| Label bucket wajib diterjemahkan | response: `< 5 mnt` (hijau), `5–15` (hijau muda), `15–60` (kuning), `> 60` (merah); resolution: `<1 jam`, `1–6 jam`, `6–24 jam`, `>24 jam` |
| `unresolved` dengan `status="done"` diberi badge khusus | Anomali: ditutup tanpa balasan solver |

### 10.7 Admin

- **Kelola grup:** daftar dari `GET /api/groups?include_inactive=true&include_default=true`; tombol "Tambah dari WAHA" memakai `GET /api/waha/groups` (tampilkan `registered:false` paling atas, salin `chat_id`); "Jadikan grup test" (`PUT {"is_default": true}`) dengan konfirmasi karena menggeser default lama.
- **Kontak solver:** CRUD; `DELETE` = soft delete.
- **Crawl:** form grup + limit, tombol dengan konfirmasi ganda; tampilkan breakdown `groups[]`.
- **Health:** panel `GET /health`; `SCAN_QR_CODE` → instruksi scan QR, bukan pesan error umum.

### 10.8 Penanganan error & state

| Kondisi | Perilaku UI |
|---|---|
| `401` | Logout, bersihkan token, arahkan ke `/login` |
| `429` | Toast "Terlalu banyak permintaan, coba lagi sebentar" + nonaktifkan tombol sementara |
| `422` | Tampilkan `detail` (string atau `msg` pertama) di dekat field terkait |
| `502` | "WhatsApp gateway bermasalah, coba lagi" — jangan menandai sukses |
| `503` (login) | "Kode akses belum dikonfigurasi di server" → hubungi admin |
| Export | Selalu `fetch` + blob; tidak ada API key di URL |
| Loading balasan | 2–10 detik/lampiran; tampilkan spinner, nonaktifkan tombol |
| Empty state | List: "Belum ada case yang lolos filter"; metrik response: "Belum ada case yang lolos filter"; metrik resolution: "Belum ada case done yang lolos filter" |

---

## 11. Data & Skema

### 11.1 Tabel (gabungan seluruh file SQL + tabel runtime)

**`cases`**

| Kolom | Tipe | Constraint / Default | Sumber |
|---|---|---|---|
| `id` | SERIAL | PK | `schema-v1-1.sql:5` |
| `case_code` | VARCHAR(50) | UNIQUE | `schema-v1-1.sql:6` |
| `case_type` | VARCHAR(30) | NOT NULL | `schema-v1-1.sql:7` |
| `title` | TEXT | — | `schema-v1-1.sql:8` |
| `fields` | JSONB | NOT NULL DEFAULT `'{}'` | `schema-v1-1.sql:9` |
| `message_text` | TEXT | NOT NULL | `schema-v1-1.sql:10` |
| `wa_message_id` | VARCHAR(128) | UNIQUE | `schema-v1-1.sql:11` |
| `status` | VARCHAR(20) | NOT NULL DEFAULT `'open'` | `schema-v1-1.sql:12` |
| `ack` | VARCHAR(20) | — | `schema-v1-1.sql:13` |
| `created_at` | TIMESTAMPTZ | DEFAULT `now()` | `schema-v1-1.sql:14` |
| `updated_at` | TIMESTAMPTZ | DEFAULT `now()` | `schema-v1-1.sql:15` |
| `area_id` | INT | FK `areas(id) ON DELETE SET NULL` | `schema-v1-2.sql:50` |
| `regional_id` | INT | FK `regionals(id) ON DELETE SET NULL` | `schema-v1-2.sql:51` |
| `sumber_ticket_id` | INT | FK `sumber_tickets(id) ON DELETE SET NULL` | `schema-v1-2.sql:52` |
| `jenis_case_id` | INT | FK `jenis_cases(id) ON DELETE SET NULL` | `schema-v1-2.sql:53` |
| `asal_grapari` | TEXT | — | `schema-v1-2.sql:54` |
| `group_id` | INT | FK `wa_groups(id) ON DELETE SET NULL` | `schema-multi-group.sql:33-34` |
| `mentions` | JSONB | DEFAULT `'[]'` | `schema-reminder.sql:6` |
| `reminder_count` | INT | DEFAULT `0` | `schema-reminder.sql:9` |
| `last_reminder_at` | TIMESTAMPTZ | — | `schema-reminder.sql:12` |
| `deleted_at` | TIMESTAMPTZ | — | `schema-migration-deleted-at.sql:4` |

Index: `idx_cases_status`, `idx_cases_area`, `idx_cases_regional`, `idx_cases_sumber`, `idx_cases_jenis`, `idx_cases_group`, `idx_cases_deleted` (partial `WHERE deleted_at IS NULL`).

**`wa_messages`**

| Kolom | Tipe | Constraint / Default | Sumber |
|---|---|---|---|
| `wa_message_id` | VARCHAR(128) | **PK** | `schema-v1-1.sql:24` |
| `quoted_id` | VARCHAR(128) | — | `schema-v1-1.sql:25` |
| `case_id` | INT | FK `cases(id) ON DELETE SET NULL` | `schema-v1-1.sql:26` |
| `author` | VARCHAR(64) | — | `schema-v1-1.sql:27` |
| `author_name` | VARCHAR(128) | — | `schema-v1-1.sql:28` |
| `body` | TEXT | — | `schema-v1-1.sql:29` |
| `from_me` | BOOLEAN | DEFAULT `false` | `schema-v1-1.sql:30` |
| `created_at` | TIMESTAMPTZ | DEFAULT `now()` | `schema-v1-1.sql:31` |
| `media_url` | TEXT | — | `schema-media-columns.sql:5` |
| `media_type` | VARCHAR(50) | — | `schema-media-columns.sql:6` |

Index: `idx_wamsg_quoted`, `idx_wamsg_case`.

**`progress_updates`** — `id` SERIAL PK · `case_id` FK SET NULL · `wa_message_id` VARCHAR(128) **UNIQUE** FK · `author` · `body` · `parsed_status` VARCHAR(20) · `parsed_note` TEXT · `source` VARCHAR(10) · `confidence` REAL · `created_at` (`schema-v1-1.sql:38-50`). Index `idx_updates_case`.

**Lookup**

| Tabel | Kolom |
|---|---|
| `areas` | `id` PK, `name` VARCHAR(100) NOT NULL UNIQUE, `created_at` |
| `regionals` | `id` PK, `area_id` INT NOT NULL FK CASCADE, `name` VARCHAR(100) NOT NULL, `created_at`, **UNIQUE(area_id, name)** |
| `sumber_tickets` | `id` PK, `name` VARCHAR(50) NOT NULL UNIQUE, `created_at` |
| `jenis_cases` | `id` PK, `name` VARCHAR(50) NOT NULL UNIQUE, `created_at` |

**`wa_groups`** — `id` PK · `name` VARCHAR(100) NOT NULL UNIQUE · `chat_id` VARCHAR(128) NOT NULL UNIQUE · `is_active` BOOL DEFAULT true · `is_default` BOOL DEFAULT false · timestamp. Index `idx_wa_groups_active`; **partial unique** `idx_wa_groups_default ON (is_default) WHERE is_default = true`.

**`reminder_log`** — `id` PK · `case_id` FK CASCADE · `wa_message_id` VARCHAR(128) (tanpa FK) · `message` TEXT · `triggered_by` VARCHAR(20) (`manual`/`cron`) · `created_at`.

**`solver_contacts`** — `id` PK · `name` VARCHAR(128) NOT NULL · `phone_number` VARCHAR(20) NOT NULL · `role` VARCHAR(100) · `is_active` BOOL DEFAULT true · timestamp. Partial unique `idx_solver_contacts_phone ON (phone_number) WHERE is_active = true`.

**`alert_state`** (runtime, dibuat kode) — `service` TEXT PK · `state` TEXT DEFAULT `'ok'` · `detail` TEXT · `first_seen_down_at` · `last_alert_at` · `last_ok_at` · `alert_count` INT DEFAULT 0 · `updated_at` (`main-v1-1.py:935-946`).

### 11.2 Urutan migrasi

| Konteks | Urutan |
|---|---|
| **Instalasi baru (Docker initdb)** | 1. `schema-v1-2.sql` → 2. `seed-v1-2.sql` → 3. `schema-multi-group.sql` (dipasang sebagai `docker-entrypoint-initdb.d/01..03`, `docker-compose.yml:64-66`) |
| **Upgrade dari v1.1** | `schema-migration-v1-2.sql` (buat lookup → `ALTER TABLE cases` 5 kolom → seed → backfill `case_type` lama) |
| **Wajib manual setelah init** | `schema-solver-contacts.sql` → `schema-reminder.sql` → `schema-migration-deleted-at.sql` → `schema-media-columns.sql` (urut ini di `docs/production-runbook.md:98-104`) |
| **Otomatis** | `alert_state` (kode saat startup); grup "Grup A" dari `WA_GROUP_ID` bila `wa_groups` kosong |
| Redundan (idempotent) | `schema-migration-author-name.sql`, `schema-migration-group-default.sql` |

> ⚠️ `schema-v1-1.sql` **tidak** dipakai di jalur instalasi baru (`schema-v1-2.sql` sudah memuat `CREATE TABLE IF NOT EXISTS cases` versi lengkap). Tanpa 4 file migrasi manual, instalasi baru akan gagal pada `reminder_log`, `deleted_at`, `media_url/media_type`, dan `solver_contacts` (§13, T2).

### 11.3 Seed

- `sumber_tickets`: `STC`, `Grapari`, `Web IT`.
- `jenis_cases`: `Non Order`, `Non AO`, `Mobile` — **`AO` tidak ada di seed** (§13, T3).
- Areas/Regionals tidak di-seed (diisi admin/manual).
- `wa_groups`: `('Grup A', $WA_GROUP_ID)` bila tabel kosong.

### 11.4 Aturan integritas yang harus dijaga

1. `case_code` unik global → satu case tidak bisa aktif di dua grup; re-FU dengan grup berbeda memindahkan case.
2. `progress_updates.wa_message_id` unik → mencegah update ganda untuk pesan yang sama.
3. Maksimum satu `wa_groups.is_default = true` (dijaga DB).
4. `phone_number` unik hanya di antara kontak solver aktif.
5. Semua query pencarian case memakai `deleted_at IS NULL` (soft delete dihormati di webhook, reminder, crawl, metrik).
6. Media tidak disimpan di DB — hanya URL; file ada di volume.

---

## 12. Operasional & Deployment

### 12.1 Lingkungan

| Env | Default (kode) | Guna |
|---|---|---|
| `DATABASE_URL` | `postgresql://postgres:postgres@localhost:5432/moban` (nama DB lama, §15.4) | Koneksi PostgreSQL |
| `WAHA_URL` | `http://localhost:3000` | Base WAHA |
| `WAHA_API_KEY` | `""` | Header `X-Api-Key` ke WAHA |
| `WAHA_SESSION` | `default` | Nama session WAHA (**satu nomor — §14**) |
| `WAHA_WEBHOOK_SECRET` | `""` | Bila kosong → webhook tidak terproteksi (log warning) |
| `WA_GROUP_ID` | `""` | Seed "Grup A" saat `wa_groups` kosong |
| `BACKEND_API_KEY` | `""` | Bila kosong → **semua endpoint terbuka** |
| `ACCESS_CODES` | `""` | Daftar kode akses login (koma) |
| `JWT_SECRET` | `uuid4().hex` acak | Penanda JWT — **wajib di-set di produksi** |
| `JWT_EXPIRY_HOURS` | `24` | Masa berlaku token FE |
| `RATE_LIMIT_RPM` | `60` (prod compose `120`) | Rate limit umum/menit/IP |
| `LOGIN_RATE_LIMIT` | `5` | Rate limit login/menit/IP |
| `ALLOWED_ORIGINS` | `http://localhost:3000` | CORS |
| `BACKEND_PUBLIC_URL` | `http://localhost:8000` | Basis URL media |
| `MEDIA_DIR` | `/app/media` | Direktori media |
| `OPENROUTER_API_KEY` / `OPENROUTER_MODEL` / `LLM_BASE_URL` | `""` / `WAHA` / `https://9router.tefambo.site/v1` | LLM fallback |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | `""` / `""` | Alert |
| `HEALTH_ALERT_REMINDER_HOURS` | `6` | Interval re-alert |
| `POSTGRES_PASSWORD` | `postgres123` (compose) | DB container |

### 12.2 Docker & jaringan

- Service: `app` (FastAPI), `db` (postgres:16-alpine), `adminer`, `nginx`. WAHA dijalankan/di-update terpisah oleh `scripts/update-waha.sh` (volume `waha_waha_sessions`, network `stc_tracker_moban-net` — **nama lama, §15.4**).
- Port app bind `127.0.0.1:8000` → akses publik hanya lewat nginx (`api.stc.syfa.site`, TLS Let's Encrypt, `client_max_body_size 25m`).
- Media: volume `./media:/app/media` (`media_data` di prod).
- Prod: `docker-compose.prod.yml` (limit app 2 CPU/1 GB, db 1 CPU/512 MB, logging json-file 10 MB × 3, `restart: always`).

### 12.3 Deploy

- `.github/workflows/deploy.yml`: push ke `main` → job `test` (`pytest -q`, Python 3.13) → job `deploy` (build & push `ghcr.io/<repo>:latest` → trigger webhook Dokploy).
- WAHA (engine WEBJS) perlu **update bulanan** — cron `15 4 1 * * … bash scripts/update-waha.sh`; gejala khas bila tertinggal: `sendText` sukses tetapi `sendImage`/`sendFile` gagal 500.

### 12.4 Cron (rekomendasi)

| Job | Perintah | Frekuensi |
|---|---|---|
| Health + alert | `scripts/health-alert-cron.sh` → `POST /api/health/alert` | setiap 5 menit |
| Backup DB | `scripts/backup-db.sh` (retensi 14 hari) | harian |
| Auto-reminder | `POST /api/reminders/run?hours=2` | setiap 2 jam (**status: HOLD** — belum diaktifkan di produksi) |
| Update WAHA | `scripts/update-waha.sh` | bulanan |

### 12.5 Runbook singkat

| Situasi | Tindakan |
|---|---|
| Session WAHA `SCAN_QR_CODE`/`FAILED` | Scan QR ulang di dashboard WAHA; volume sessions **jangan** dihapus saat maintenance |
| `/health` `degraded` | Baca `db.error` / `waha.http.status_code` / `sessions[].status`; alert Telegram sudah dikirim otomatis |
| Backup/restore | `scripts/backup-db.sh`; restore: schema dulu, lalu data (`docs/production-runbook.md`) |
| Reset data uji | `scripts/clear-db.sh` (menghapus data transaksional, mempertahankan tabel referensi) |
| Rotasi secret webhook | `openssl rand -hex 24` → `.env` → `docker compose up -d app` → update `?token=` di URL webhook WAHA |
| Media hilang | Cek volume `media_data`; `api/media/proxy` akan memigrasikan media legacy saat diakses |
| Deploy ulang | Push ke `main` (CI test → build image → Dokploy) |

> ⚠️ Jangan hapus `/home/ubuntu/frontend/nginx/` di VPS — di sana ada config nginx live + sertifikat TLS + target cron certbot renew. **Selalu backup sebelum operasi destruktif.**

---

## 13. Keterbatasan & Temuan yang Diketahui (audit kode 16 Okt 2026)

Diurutkan berdasarkan risiko. Semua diverifikasi langsung di kode/suite test.

| # | Temuan | Bukti | Dampak | Rekomendasi |
|---|---|---|---|---|
| **T1** | `REQUIRED_FIELDS` didefinisikan tetapi **tidak ditegakkan** backend | `templates.py:10-15`; tidak dipakai di `create_case` (`main-v1-1.py:1391-1477`) | Case bisa dikirim tanpa `no_indihome`/`order_id`/`msisdn` → data tracking tidak lengkap | Tegakkan di backend (bukan hanya UI) atau dokumentasikan sebagai opsional |
| **T2** | Instalasi baru **tidak lengkap**: 4 file migrasi wajib manual | `docker-compose.yml:64-66` hanya memuat 3 file; `deleted_at`, `media_url/type`, `reminder_log`, `solver_contacts` tidak ada | Fresh install gagal di reminder/webhook/media/kontak | Masukkan `schema-reminder.sql`, `schema-migration-deleted-at.sql`, `schema-media-columns.sql`, `schema-solver-contacts.sql` ke `initdb` |
| **T3** | `jenis_cases` **tidak di-seed dengan `AO`** meski fitur AO aktif sejak v1.31 | `seed-v1-2.sql:10-11`; `templates.py:8` | `_resolve_jenis_case("AO")` → `None`; `jenis_case_id` NULL; filter `case_type=AO` di list mengembalikan **0 hasil** (filter memakai `jenis_cases.name`) | Tambah `AO` ke seed + backfill baris yang NULL |
| **T4** | JWT diterbitkan tetapi **tidak pernah diverifikasi** backend | tidak ada `jwt.decode` di `main-v1-1.py` | Token FE hanya gate kosmetik; keamanan bergantung `X-API-Key` | Tambahkan dependency verifikasi JWT untuk endpoint FE, atau dokumentasikan `X-API-Key` sebagai satu-satunya kontrol |
| **T5** | `JWT_SECRET` default acak per proses | `main-v1-1.py:81` | Tanpa env, token tidak valid antar worker/restart | Wajib di-set (dan sudah ada di `.env.example`) |
| **T6** | `BACKEND_API_KEY` kosong = **semua endpoint terbuka tanpa log** | `main-v1-1.py:295-296` | Risiko tinggi bila env lupa di-set di produksi | Fail-closed atau minimal log warning saat startup |
| **T7** | Webhook **bukan background task** — diproses inline sebelum response | `main-v1-1.py:2669-2686`; tidak ada `BackgroundTasks` | WAHA menunggu selama pemrosesan (termasuk unduh media + potensi LLM) → risiko timeout/retry | Pindahkan ke `BackgroundTasks` atau queue |
| **T8** | Rate limit in-memory **per proses** | `main-v1-1.py:190`; prod 2–4 worker | Batas efektif = RPM × jumlah worker | Pakai limiter terpusat (Redis) atau andalkan nginx |
| **T9** | `progress_updates.parsed_status` komentar SQL masih menyebut `issue` | `schema-v1-2.sql:94` vs `parse_rule` (`main-v1-1.py:500-527`) | Dokumentasi DB menyesatkan | Perbarui komentar/migrasi constraint |
| **T10** | Kelas test duplikat `TestSoftDeleteWebhook` | `tests/test_api.py:1346` dan `:1391` | Kelas kedua menimpa yang pertama → **5 test tidak pernah dieksekusi** | Gabungkan kedua kelas |
| **T11** | **1 test gagal** di Windows/Python 3.13 + httpx 0.28/starlette 0.50 | `TestAuth::test_health_never_requires_auth` → `RecursionError` (mock `httpx.get` + `TestClient`); suite terkumpul 373, hasil `372 passed, 1 failed` | CI hijau (Linux) tetapi lokal merah; menutupi regresi nyata di mesin dev | Ganti mock `httpx.get` dengan `httpx.MockTransport`, atau `patch.object(collect_health)` |
| **T12** | Docstring `send_telegram` mengklaim `True` saat token kosong, kode `return False` | `main-v1-1.py:3256-3262` | Membingungkan pembaca kode | Sinkronkan docstring |
| **T13** | `verify_api_key` memakai `!=` (bukan constant-time) sedangkan webhook memakai `secrets.compare_digest` | `main-v1-1.py:297` | Timing attack teoretis | Pakai `secrets.compare_digest` |
| **T14** | `_resolve_jenis_case` / `_resolve_sumber_ticket` lookup **case-sensitive** | `main-v1-1.py:794-812` | Nama dengan kapitalisasi berbeda → `jenis_case_id`/`sumber_ticket_id` NULL (teks tetap benar karena `_render_case_payload` menormalkan) | Gunakan `ILIKE`/`LOWER()` |
| **T15** | `docker-compose.prod.yml` menetapkan `WORKERS=4`/`LOG_LEVEL=warning` yang **tidak dibaca** CMD Dockerfile | `Dockerfile:54-61`; `docker-compose.prod.yml:15` | Kapasitas produksi lebih kecil dari yang diasumsikan | Ubah CMD agar membaca env atau pindahkan ke `run.sh` |
| **T16** | `GET /api/cases/{id}/reminder` tidak menyaring `deleted_at`; `set_status` juga tidak | `main-v1-1.py:1799-1801`, `2193-2205` | Case terhapus masih bisa diubah statusnya/dibaca riwayatnya via API | Tambahkan `deleted_at IS NULL` |
| **T17** | Cache kontak tanpa TTL + placeholder `None` | `main-v1-1.py:367`, `:457` | Nama kontak baru tidak ikut ter-cache sampai restart; LID tak dikenal memicu HTTP tiap pesan | Tambah TTL/refresh berkala, jangan simpan `None` |
| **T18** | `POST /api/crawl` tidak membatasi `limit` maksimum | `main-v1-1.py:3040` | Nilai besar bisa membebani WAHA/DB | Batasi (mis. ≤500) |
| **T19** | **Sisa nama produk "Moban"** (teks/display sudah di-rename ke IT Tracker; identitas infrastruktur belum) | Belum: `main-v1-1.py:56,69`; `Dockerfile:32,41,43`; `nginx.conf:2,37,45`; `docker-compose.yml`; `.env.example:5`; `moban-tracker.service` (nama file); `scripts/*.sh`; `reports/*.sql` | Nama lama masih muncul di Swagger **log**, nama container/service systemd, network, dan DB | Lihat checklist §15.4 — grup A **sudah selesai**, grup B (container/network/DB/user) menunggu maintenance |

> **Catatan:** `API-contract-frontend.md` sudah selaras dengan kode v1.34 pada sisi **versi/endpoint**, tetapi punya cacat isi & struktur yang harus dibetulkan — lihat §13.1. `PRD-backend-it-tracker-v1-1.md` **tidak** selaras (enum jenis case lama, tanpa pagination/export/metrik/health-detail) → jangan dipakai sebagai acuan.

### 13.1 Pembetulan yang diperlukan pada `API-contract-frontend.md`

Cacat isi (bukan kosmetik) — hasil audit 16 Okt 2026. **Prioritas: C1 dan C3 bisa menyesatkan implementasi FE.**

| # | Lokasi | Masalah | Perbaikan |
|---|---|---|---|
| CC1 | contract §7.2 (baris 1255-1266) | Contoh response `{checked, reminded, cases[]}` ditulis pada **riwayat reminder case** (`GET /api/cases/{id}/reminder`); itu milik `POST /api/reminders/run` | Ganti contoh dengan array `reminder_log`; pindahkan contoh `{checked, reminded}` ke contract §7.3 (`POST /api/reminders/run` yang saat ini hilang) |
| CC2 | contract §5.1, contract §7.1, contract §7.4 | Blok `**Response 200:**` **kosong** | Isi: contract §5.1 → array kontak; contract §7.1 → `{ok, reminder_count, wa_message_id, message}`; contract §7.4 → array case + `idle_hours` |
| CC3 | contract §5.4 (baris 1096-1101) | Heading rusak: `PUT …{id\}json {…} — Soft delete kontak` — `PUT` dan `DELETE` digabung, **`DELETE /api/solver-contacts/{id}` tak terdokumentasi** | Pisah jadi contract §5.4 `PUT` (update parsial, contoh body `{name, role}`) dan contract §5.5 `DELETE` (soft delete → `{ok:true}`) |
| CC4 | baris 228 vs 293 | `group_id` tidak valid → `404` (baris 228) vs `409` (baris 293) | Samakan: tak dikenal → **`422`**; terdaftar tapi nonaktif → **`409`** |
| CC5 | contract §4.4 (baris 1040-1044) | Contoh `GET /api/jenis-cases` belum memuat `AO` | Tambahkan `{ "id": 4, "name": "AO" }` — sekaligus **perbaiki seed DB** (contract §13 T3) |
| CC6 | baris 229 | Klaim field wajib per jenis case — backend tidak menegakkannya | Tandai sebagai "disarankan di UI, tidak divalidasi server" sampai T1 dikerjakan |
| CC7 | baris 4 | "Referensi: PRD v1.5" (superseded) | Ganti ke `PRD-it-tracker-v2.md` |
| CC8 | baris 1088, 1240, 1255 | Escaping `{id\}` pada heading + fence ` ``` ` hilang (baris 1258) | Bersihkan escaping dan tutup fence |
| CC9 | contract §7 | Nomor melompat 7.1 → 7.2 → **7.4** (7.3 tidak ada) | Tambahkan contract §7.3 `POST /api/reminders/run` (dokumentasi lengkap; saat ini hanya muncul sebagai contoh crontab) |
| CC10 | contract §12 (baris 1534) + sub-heading baris 1540-1991 | Section 12 tapi anak-anaknya bernomor 11.1–11.9 | Renomori menjadi 12.1–12.12 |

---

## 14. Roadmap & Rencana

| Prioritas | Item | Catatan |
|---|---|---|
| **P1** | Tutup temuan T1–T6 (§13) | Risiko data/keamanan nyata; relatif murah |
| **P1** | Aktifkan cron auto-reminder di produksi | Saat ini HOLD by decision |
| **P2** | Halaman admin lengkap (grup/kontak/crawl/health) | Lihat §10.7 |
| **P2** | Notifikasi balik ke agen saat case `done` | Kebutuhan US-5 fase 2 |
| **P2** | `GET /api/waha/sessions` (daftar session + status untuk UI) | Saat ini hanya muncul di `/health`; §6.5 FR-O6 |
| **P3** | Event `message.revoked` (case dihapus pesannya) | Belum ada |
| **P3** | TTL cache kontak + refresh terjadwal | T17 |
| **P3** | Endpoint `PATCH /api/cases/{id}` untuk edit field case | Belum ada (hanya status) |
| **P4** | Multi-nomor WA per regional | **Rencana DRAFT — belum dieksekusi**: `docs/plan-multi-nomor-regional.md`. Ringkas: butuh tabel `wa_sessions`, kolom `session_id` di `wa_groups`/`cases`/`wa_messages`, refactor 11 titik kode yang membaca `WAHA_SESSION` global, cache kontak per session; **wajib Fase 0 (uji RAM 3 session + uji error 463/475 WhatsApp)** sebelum menulis kode. Dokumen tersebut merekomendasikan Opsi A (tetap 1 nomor, label regional sudah ada di header pesan) atau Opsi B (2–3 nomor) sebelum Opsi C (12 nomor). Belum ada `session_id` di kode maupun SQL mana pun |

---

## 15. Test & Kriteria Penerimaan

### 15.1 Kondisi suite saat ini (dijalankan 16 Okt 2026, Python 3.13.5 / httpx 0.28.1 / starlette 0.50.0 / fastapi 0.128.0)

```
373 tests collected · 372 passed · 1 failed (44.7 s)
```

| File | Jumlah test | Fokus |
|---|---|---|
| `tests/test_api.py` | 224 | Integration HTTP lewat `TestClient` (mock DB) — create case, grup, auth, webhook, media, anti-SSRF, reminder, preview/test-send, pagination, export, metrik |
| `tests/test_templates.py` | 49 | Rendering pesan murni (tanpa DB) |
| `tests/test_chain.py` | 35 | Chain traversal, `parse_rule`, word-boundary, `quoted_id`, `norm_id` |
| `tests/test_llm_crawl.py` | 24 | LLM parsing/validasi/retry + crawl 2-pass |
| `tests/test_groups.py` | 16 | Filter & verifikasi grup, auto `in_progress` |
| `tests/test_groups_notif.py` | 16 | Notifikasi balasan solver + media + bump `updated_at` |
| `tests/test_health_alert.py` | 9 | `collect_health`, `send_telegram`, endpoint alert |

**Yang gagal:** `TestAuth::test_health_never_requires_auth` (`RecursionError` — lihat T11). **Semua test memakai DB mock**; tidak ada verifikasi skema end-to-end.

### 15.2 Kriteria penerimaan (per fitur)

| Fitur | Kriteria lolos |
|---|---|
| Buat case | Case muncul di grup yang dipilih/default; row `cases` + `wa_messages` root terbentuk; respons `text` sama persis dengan pesan di WA |
| Preview vs kirim | `text` dari `/preview` **identik** dengan `text` dari `/cases` untuk input sama |
| Test-send | Tidak ada row `cases` baru; pesan muncul di grup test |
| Re-FU | `case_code` sama → jumlah case tidak bertambah, status kembali `open`, case muncul lagi di dashboard |
| Balasan solver | Reply "done" langsung ke root → status `done` (`source=reply`); reply ke pesan solver lain → `chain`; balasan pertama tanpa keyword → `in_progress` |
| Isolasi grup | Pesan dari grup A tidak mengubah case milik grup B |
| Balas dari web | Pesan muncul di WA sebagai reply ke pesan solver; tercatat `wa_messages` dengan `case_id`; rantai lanjut untuk balasan berikutnya |
| Reminder | Mention solver terkirim; `reminder_count` naik; balasan solver ke pesan reminder ter-`chain` |
| Export | Isi Excel = isi dashboard untuk filter identik; 0 row tetap file valid; tidak ada API key di URL |
| Pagination | Tanpa `limit` respons array; dengan `limit` envelope + `pagination` benar |
| Metrik | Median & bucket benar untuk data uji; `unanswered`/`unresolved` terpisah dari summary |
| Health | Session WAHA tidak `WORKING` → `status: degraded` **dengan HTTP 200** |
| Alert | Transisi ok→down kirim Telegram sekali; masih down <6 jam → `noop`; pulih → pesan recovery |

### 15.3 Regresi wajib sebelum rilis

1. `pytest -q` → 372+ lulus, tanpa kegagalan **baru** (T11 masih terbuka di Windows; jangan tambah kegagalan lain).
2. Smoke manual: buat case di grup test → lihat pesan terformat di WA → balas "done" dari akun solver → cek status di dashboard berubah → balas dari web → cek rantai di timeline.
3. `curl /health` → baca `body.status`, bukan HTTP code.
4. Export Excel dengan filter tanggal → bandingkan jumlah baris dengan dashboard.

---

### 15.4 Checklist Rename: "Moban FU Case Tracker" → "IT Tracker"

Nama produk baru: **IT Tracker**. Istilah **"moban"** sebagai *domain term* (follow-up case) tetap dipakai di narasi WhatsApp (misal judul case berisi kata "moban"), tetapi sebagai **nama produk/identitas teknis** sudah diganti.

> **Status per 16 Okt 2026:** Grup A (teks/display) **sudah diterapkan**. Grup B (nama container/network/DB/user OS) **belum** — menunggu maintenance karena berdampak downtime.

#### A. ✅ SUDAH diterapkan (teks/display saja)

| # | Lokasi | Perubahan | Verifikasi |
|---|---|---|---|
| A1 | `main-v1-1.py:200` | `title="Moban FU Tracker"` → `title="IT Tracker"` | `app.title == 'IT Tracker'` (Swagger `GET /docs`) |
| A2 | `main-v1-1.py:1` | docstring modul → `"""IT Tracker v1.34 …"""` | grep |
| A3 | `main-v1-1.py:202` | deskripsi app `"Backend API untuk IT Tracker v1.34. "` | grep |
| A4 | `Dockerfile:1` | `# IT Tracker — Dockerfile` | grep |
| A5 | `run.sh:2,18` | komentar + `echo "Starting IT Tracker..."` | grep |
| A6 | `docker-compose.yml:1`, `docker-compose.prod.yml:1` | komentar header | grep |
| A7 | `.env.example:1` | komentar header | grep |
| A8 | `requirements.txt:1` | `# IT Tracker — Python Dependencies` | grep |
| A9 | `templates.py:1` | `"""Template rendering for IT Tracker v1.2.` | grep |
| A10 | `tests/test_api.py:1` | docstring `"""Integration tests for IT Tracker …` | grep |
| A11 | 11 file `schema-*.sql` + `seed-v1-2.sql` baris 1 | komentar header | grep |
| A12 | `moban-tracker.service:2` | `Description=IT Tracker Backend` | grep |
| A13 | `API-contract-frontend.md:1` | `# API Contract — IT Tracker (untuk Tim Frontend)` | grep |
| A14 | `docs/pitch-deck-arsitektur.md:1` | `# IT Tracker — Pitch Deck` | grep |
| A15 | `docs/production-runbook.md:1` | `# Runbook Verifikasi Produksi — IT Tracker (v1.10)` | grep |
| A16 | `docs/superpowers/specs/2026-10-08-health-alert-telegram-design.md` | judul design doc | grep |
| A17 | Nama file PRD | `PRD-backend-it-tracker-v1-1.md`, `PRD-it-tracker-v2.md` | `ls PRD*` |

**Hasil verifikasi grup A:**
- `python -c "import main; print(m.app.title)"` → `IT Tracker` ✓
- `pytest -q` → **372 passed, 1 failed** (kegagalan T11 yang sudah ada sebelumnya, tidak terkait rename) ✓
- Kemunculan kata "Moban" yang tersisa semuanya identitas infrastruktur (grup B) atau rujukan historis ✓

#### B. ⏸ BELUM diterapkan — perlu keputusan & maintenance

| # | Lokasi | Nilai sekarang | Risiko bila diubah |
|---|---|---|---|
| B1 | `Dockerfile:32,41,43` | user/group Linux `moban` | Perubahan user container → volume `media`/`/app` yang ada dimiliki uid lama → perlu `chown` ulang; tidak memengaruhi data DB |
| B2 | `docker-compose.yml` | `container_name: moban-tracker`, `moban-db`, `moban-adminer`, `moban-nginx` | Script ops memakai `docker exec moban-db` (`scripts/backup-db.sh`, `scripts/clear-db.sh`) — wajib diubah bersamaan |
| B3 | `docker-compose.yml`, `nginx.conf:2,37,45` | network `moban-net`/`stc_tracker_moban-net`, `proxy_pass http://moban-tracker:8000` | **Wajib sinkron**: rename network + service, dan WAHA (dijalankan manual) harus di-reconnect |
| B4 | `moban-tracker.service` (nama file) | file + unit systemd | Hanya mode non-Docker; rename file = `systemctl disable/enable` ulang |
| B5 | `.env.example:5`, `docker-compose.yml:17,57,64`, `main-v1-1.py:69` | nama database `moban` | ⚠️ **Breaking**: butuh `ALTER DATABASE moban RENAME TO it_tracker;` + ubah `DATABASE_URL` **dan** `POSTGRES_DB` bersamaan, lalu `docker compose up -d` |
| B6 | `.github/workflows/deploy.yml` | image `ghcr.io/<repo>:latest`, secret `DOKPLOY_DEPLOY_WEBHOOK_URL` | Mengikuti nama repo GitHub; rename repo → perbarui registry path & webhook |
| B7 | `tests/test_api.py` (3×) + semua impor | `importlib.import_module("main-v1-1")` | Bila `main-v1-1.py` di-rename → `Dockerfile:41-42`, `run.sh:26`, systemd, dan semua test harus ikut |
| B8 | `scripts/update-waha.sh:31` | `NETWORK_APP="stc_tracker_moban-net"` | Harus diubah bersamaan dengan B3 |
| B9 | `reports/*.sql` baris 5 | contoh perintah `docker exec moban-db … -d moban` | Dokumentasi; ikut B2/B5 |

#### C. Urutan eksekusi untuk grup B (yang disarankan)

```mermaid
flowchart LR
    S1["1 · Edit script ops dulu<br/>(B2,B8,B9)<br/>pakai nama baru"] --> S2["2 · Rename service/container/network<br/>(B2,B3) + reconnect WAHA"]
    S2 --> S3["3 · Rename DB<br/>ALTER DATABASE + env (B5)<br/>maintenance window"]
    S3 --> S4["4 · Rename file/sistem<br/>(B4,B7)<br/>opsional"]
```

**Wajib sebelum mulai:** `scripts/backup-db.sh` dan catat `docker ps` + `docker network ls`.
**Jangan lakukan sekaligus dalam satu commit** — B5 (nama DB) satu-satunya yang menyentuh data; pisahkan supaya rollback mudah.

#### D. Kriteria selesai rename (keseluruhan)

1. `grep -ri "moban" --exclude-dir=.git .` hanya menyisakan rujukan historis (nama file lama di changelog, laporan audit, judul case contoh) — bukan identitas aktif. **Status sekarang:** ✅ grup A bersih; sisa = grup B + historis.
2. `GET /docs` menampilkan title `IT Tracker`. ✅ terpenuhi.
3. `docker compose ps` menampilkan nama container baru; script ops tetap berjalan. ⏸ belum.
4. Backup & restore DB berhasil setelah rename database. ⏸ belum.
5. `pytest -q` masih 372+ lulus (tanpa kegagalan baru). ✅ terpenuhi.

