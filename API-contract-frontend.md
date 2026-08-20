# API Contract — Moban FU Case Tracker (untuk Tim Frontend)

**Versi:** 1.1 · **Tanggal:** 19 Agustus 2026 · **Backend:** FastAPI · **Base path:** `/api`
**Referensi:** PRD v1.1, schema-v1.1.sql

> Catatan: backend FastAPI juga mengekspos dokumentasi interaktif otomatis di `GET /docs` (Swagger UI) dan skema mesin di `GET /openapi.json` — bisa diimpor ke Postman. Dokumen ini adalah kontrak human-readable yang jadi acuan utama.

---

## 1. Gambaran Umum

| Item | Nilai |
|---|---|
| Base URL (dev) | `http://<vps-ip>:8000` |
| Base URL (prod) | `https://api.<domain>` (via reverse proxy, M6) |
| Format | JSON, `Content-Type: application/json` |
| Auth | Header `X-API-Key: <key>` — **diterapkan di M6**; FE wajib siapkan mekanisme header dari sekarang |
| Encoding waktu | ISO 8601 dengan timezone (TIMESTAMPTZ), contoh `2026-08-19T16:20:11.345+07:00` |
| Realtime | Belum ada websocket. FE disarankan polling `GET /api/cases` tiap 30 dtk atau saat window focus |

## 2. Enum & Konstanta

| Field | Nilai |
|---|---|
| `case_type` | `stc` · `smooa` · `mobile` · `ufo` · `other` |
| `status` (case) | `open` · `in_progress` · `done` · `issue` |
| `ack` (pesan keluar) | `PENDING` · `SERVER` · `DEVICE` · `READ` — progresif, pakai yang terakhir |
| `source` (update) | `rule` · `reply` · `chain` · `llm` · `crawl` · `manual` |

Saran mapping badge status di UI: `open` → abu, `in_progress` → biru, `done` → hijau, `issue` → merah.

## 3. Endpoints

### 3.1 `POST /api/cases` — Buat & kirim case ke grup WA

**Request:**
```json
{
  "case_type": "stc",
  "mentions": [
    { "number": "6281113021236", "name": "Mas Habib Spv Tsel" }
  ],
  "fields": {
    "ticket_remedy": "INC000023470570",
    "no_indihome": "142401135588",
    "order_id": "MOk4260811023440131b25f60",
    "last_milestone": "TSEL_ACTIVATION_FALLOUT",
    "milestone_info": "UPCF-12302-The subscriber does not exist",
    "detail_case": "Moban dibantu add subsnya di domain/realm telkom.net dan package INETFN50M...",
    "evidence": "https://prnt.sc/OB6aazyzxFAU (telkom.net/INET50M)"
  }
}
```

Aturan:
- `case_type` wajib; nilai di luar enum di-downgrade ke `other`.
- `fields` — semua key opsional; yang kosong tidak muncul di teks WA. Lihat §5 untuk daftar key per `case_type`.
- `mentions` opsional. `number` = nomor WA format internasional **tanpa `+`** (`628xxx`). `name` opsional, hanya untuk tampilan teks.
- `case_code` diturunkan backend dari `fields.ticket_remedy` atau `fields.case_id`. Bisa `null` untuk `case_type=other` tanpa tiket — FE pakai fallback tampilan `#<id>`.
- Mengirim ulang `case_code` yang sudah ada = **re-FU**: status kembali `open`, jangkar pesan diperbarui. Bukan error.

**Response `201`:**
```json
{
  "id": 42,
  "case_code": "INC000023470570",
  "wa_message_id": "true_120363xxx@g.us_3EB0A1B2C3",
  "text": "punten mas @Mas Habib Spv Tsel moban FU case berikut\n\n#STC\nTicket Remedy : INC000023470570\n..."
}
```
`text` adalah pesan final persis yang terkirim ke grup — tampilkan di toast/modal sukses sebagai bukti.

**Error:** `422` field tidak valid · `502` WAHA tidak terjangkau / session tidak WORKING (case **tidak** tersimpan, suruh user retry).

---

### 3.2 `GET /api/cases` — Daftar case (dashboard list)

**Query params (semua opsional, bisa dikombinasi):**

| Param | Contoh | Keterangan |
|---|---|---|
| `status` | `open` | filter enum status |
| `case_type` | `stc` | filter jenis case |
| `q` | `INC0000234` | pencarian substring di `case_code` dan `title` (case-insensitive) |

**Response `200`:**
```json
[
  {
    "id": 42,
    "case_code": "INC000023470570",
    "case_type": "stc",
    "title": "Moban dibantu add subsnya di domain/realm telkom.net...",
    "status": "in_progress",
    "ack": "READ",
    "created_at": "2026-08-19T09:14:02+07:00",
    "updated_at": "2026-08-19T10:31:55+07:00"
  }
]
```
Diurutkan `updated_at DESC` — case yang baru ada aktivitas selalu di atas. `ack` menunjukkan pesan case sudah dibaca grup atau belum (berguna untuk indikator "✓✓ biru").

---

### 3.3 `GET /api/cases/{id}` — Detail + timeline rantai

**Response `200`:**
```json
{
  "case": {
    "id": 42,
    "case_code": "INC000023470570",
    "case_type": "stc",
    "title": "...",
    "fields": { "ticket_remedy": "INC000023470570", "...": "..." },
    "message_text": "punten mas ...",
    "wa_message_id": "true_120363xxx@g.us_3EB0A1B2C3",
    "status": "done",
    "ack": "READ",
    "created_at": "...",
    "updated_at": "..."
  },
  "messages": [
    { "wa_message_id": "true_..._AAA", "quoted_id": null, "author": null, "body": "punten mas ...", "from_me": true, "created_at": "..." },
    { "wa_message_id": "false_..._BBB", "quoted_id": "true_..._AAA", "author": "6281113021236@c.us", "body": "dicek dulu mas", "from_me": false, "created_at": "..." },
    { "wa_message_id": "false_..._CCC", "quoted_id": "false_..._BBB", "author": "6281299887766@c.us", "body": "done mas, sudah diluruskan", "from_me": false, "created_at": "..." }
  ],
  "updates": [
    { "id": 7, "case_id": 42, "wa_message_id": "false_..._CCC", "author": "6281299887766@c.us", "body": "done mas, sudah diluruskan", "parsed_status": "done", "parsed_note": "done mas, sudah diluruskan", "source": "chain", "confidence": null, "created_at": "..." }
  ],
  "participants": ["6281113021236@c.us", "6281299887766@c.us"]
}
```

Cara render timeline:
- `messages` membentuk **pohon** lewat `quoted_id`; root = pesan dengan `wa_message_id == case.wa_message_id` (juga satu-satunya yang `from_me: true`).
- Render sebagai thread bersarang (indent per level) atau flat chronological — data dua-duanya cukup.
- `participants` = daftar solver yang terlibat rantai — cocok untuk chip "Ditangani oleh:".
- `updates[].source` menjelaskan bagaimana update tertangkap; `chain` berarti "reply ke pesan orang lain (eskalasi)" — layak diberi ikon khusus.
- `author` berformat `62xxx@c.us`; tampilkan nomornya saja atau map ke nama kontak di sisi FE.

**Error:** `404 { "detail": "Case not found" }`.

---

### 3.4 `POST /api/cases/{id}/status` — Koreksi status manual

Untuk tombol "Tandai selesai" / "Buka ulang" di UI.

**Request:** `{ "status": "done", "note": "konfirmasi via telpon" }`
- `status` wajib, enum status. `note` opsional, tampil di timeline sebagai update `source: manual`.

**Response `200`:** `{ "ok": true }`

---

### 3.5 `POST /api/crawl` — Backfill histori grup (admin)

**Query:** `limit` (default 200, maks mengikuti WAHA).

**Response `200`:** `{ "fetched": 200, "stored": 187, "updates_applied": 12 }`

Operasi ini berat (tarik histori WA + proses). Jangan dipanggil otomatis dari UI utama — sediakan di halaman admin/pengaturan dengan konfirmasi.

---

### 3.6 `GET /health`

**Response `200`:** `{ "status": "ok", "db": "ok", "waha": "ok" }`
Berguna untuk banner "sistem gangguan" di UI. Nilai selain `ok` pada `db`/`waha` = backend atau WAHA bermasalah.

---

## 4. Format Error

FastAPI default: `{ "detail": "pesan error" }` dengan status code sesuai. Validasi body gagal → `422` dengan `detail` berisi array lokasi field. FE cukup menampilkan `detail` apa adanya.

## 5. Spesifikasi Form Dinamis (per `case_type`)

Form utama = dropdown **Jenis Case** + field yang berubah mengikuti tabel ini. Semua field **opsional** secara API, tapi kolom bertanda ★ disarankan wajib di UI demi kualitas tracking.

### `stc` — Case STC (milestone Indihome)
| Key | Label UI | Tipe | |
|---|---|---|---|
| `ticket_remedy` | Ticket Remedy | text (pattern `INC\d+`) | ★ |
| `no_indihome` | No Indihome | text/tel | ★ |
| `order_id` | Order ID | text | |
| `last_milestone` | Last Milestone | text | ★ |
| `milestone_info` | Milestone Info | textarea | |
| `detail_case` | Detail Case | textarea | ★ |
| `evidence` | Evidence (link) | url | |

### `smooa` — Case SMOOA / GraPARI
| Key | Label UI | Tipe | |
|---|---|---|---|
| `grapari` | GraPARI | text | ★ |
| `ticket_remedy` | Ticket Remedy | text | ★ |
| `no_indihome` | No IH | text/tel | |
| `nama_pelanggan` | Nama Pelanggan | text | |
| `cp` | CP | tel | |
| `email` | Email | email | |
| `tgl_kejadian` | Tgl Kejadian | date/text | |
| `detail_case` | Case / Detail | textarea | ★ |
| `evidence` | Capture Lightspeed | url | |
| `smooa_parent` | No SMOOA Parent | tel | |
| `smooa_child` | No SMOOA Child | text (bisa koma-multi) | |

### `mobile` — Case Mobile
| Key | Label UI | Tipe | |
|---|---|---|---|
| `grapari` | GraPARI | text | ★ |
| `ticket_remedy` | Ticket Remedy | text | ★ |
| `tier` | Tier | select (Diamond/Gold/Silver/dll) | |
| `msisdn` | MSISDN | tel | ★ |
| `tgl_kejadian` | Tanggal Kejadian | date | |
| `lokasi` | Lokasi | text | |
| `detail_case` | Detail Kejadian | textarea | ★ |
| `evidence` | Evidence | url | |

### `ufo` — Case UFO / Order
| Key | Label UI | Tipe | |
|---|---|---|---|
| `grapari` | GraPARI | text | ★ |
| `case_id` | Case ID | text | ★ |
| `no_indihome` | Nomor Indihome | text/tel | |
| `email` | Email | email | |
| `cp` | CP Pelanggan | tel | |
| `tgl_kejadian` | Tgl Kejadian | date | |
| `order_id` | Order ID | text | |
| `status_case` | Status | text (mis. OPEN) | |
| `detail_case` | Detail Keperluan | textarea | ★ |
| `evidence` | Evidence | url | |

### `other`
| Key | Label UI | Tipe |
|---|---|---|
| `raw_text` | Pesan lengkap | textarea |

### Komponen Mention (semua jenis case)
- Multi-select kontak → dikirim sebagai `mentions: [{number, name}]`.
- Saran: daftar kontak solver di-hardcode di FE dulu (atau tabel config nanti), user tinggal centang.

## 6. Alur Integrasi yang Disarankan

1. **Form input:** render field per §5 → submit `POST /api/cases` → tampilkan `text` dari response sebagai konfirmasi "pesan terkirim ke grup".
2. **Dashboard:** `GET /api/cases` (+filter) → tabel dengan badge status & indikator ack. Polling 30 dtk.
3. **Detail:** klik baris → `GET /api/cases/{id}` → render thread `messages` + sidebar `updates` + chip `participants`.
4. **Aksi:** tombol koreksi status → `POST /api/cases/{id}/status` → refresh detail.

## 7. Open Items (akan dikonfirmasi backend)

- `POST /api/cases/preview` — render teks WA **sebelum** kirim (saat ini `text` baru tersedia setelah terkirim). Sementara FE bisa menampilkan konfirmasi pasca-kirim.
- Pagination `GET /api/cases` (saat ini mengembalikan semua; akan ditambah `limit`/`offset` bila data sudah besar).
- Auth `X-API-Key` aktif di M6 — siapkan header dari sekarang agar tidak ada rework.
