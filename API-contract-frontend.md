# API Contract — Moban FU Case Tracker (untuk Tim Frontend)

**Versi:** 1.2 · **Tanggal:** 20 Agustus 2026 · **Backend:** FastAPI · **Base path:** `/api`
**Referensi:** PRD v1.1, schema-v1-1.sql

> Catatan: backend FastAPI juga mengekspos dokumentasi interaktif otomatis di `GET /docs` (Swagger UI) dan skema mesin di `GET /openapi.json` — bisa diimpor ke Postman. Dokumen ini adalah kontrak human-readable yang jadi acuan utama.

---

## 1. Gambaran Umum

| Item | Nilai |
|---|---| 
| Base URL (prod) | `https://api.stc.it-jaya.id` (via nginx + SSL) |
| Format | JSON, `Content-Type: application/json` |
| Auth | Header `X-API-Key: <key>` — **wajib** untuk semua endpoint kecuali `/health` dan `/webhooks/waha` |
| Encoding waktu | ISO 8601 dengan timezone (TIMESTAMPTZ), contoh `2026-08-20T16:20:11.345+07:00` |
| Realtime | Belum ada websocket. FE disarankan polling `GET /api/cases` tiap 30 dtk atau saat window focus |
| Contact Names | Backend resolve `@lid` → nama kontak via WAHA API. `author_name` tersedia di messages & participants |

## 2. Enum & Konstanta

| Field | Nilai |
|---|---| 
| `case_type` | `stc` · `smooa` · `mobile` · `ufo` · `other` |
| `status` (case) | `open` · `in_progress` · `done` · `issue` |
| `ack` (pesan keluar) | `PENDING` · `SERVER` · `DEVICE` · `READ` — progresif, pakai yang terakhir |
| `source` (update) | `rule` · `reply` · `chain` · `llm` · `crawl` · `manual` |

Saran mapping badge status di UI: `open` → abu, `in_progress` → biru, `done` → hijau, `issue` → merah.

### Source Labels
| Source | Keterangan | Contoh |
|---|---|---|
| `rule` | Pesan mengandung kode INC yang match dengan case yang sedang open | "INC000023470570 sudah done" |
| `reply` | Reply langsung ke pesan root bot di grup | Tekan lama → Reply → "done mas" |
| `chain` | Reply ke pesan orang lain (bukan root), atau reply ke reply | A reply ke bot → B reply ke A → source: chain |
| `llm` | Tidak match regex/chain, tapi LLM mendeteksi case + status | Pesan bebas yang mengandung info tiket |
| `crawl` | Diproses dari backfill histori grup | `POST /api/crawl` |
| `manual` | Diubah manual via API | `POST /api/cases/{id}/status` |

## 3. Endpoints

### 3.1 `POST /api/cases` — Buat & kirim case ke grup WA

**Headers:**
```
X-API-Key: <key>
Content-Type: application/json
```

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

**Format pesan WhatsApp (otomatis):**
```
punten rekan @6281113021236 mohon bantuannya untuk case STC ada 1 case lagi

#STC
Ticket Remedy : INC000023470570
No Indihome : 142401135588
...
```

> **Catatan mention:** Backend menggunakan `@<nomor telepon>` di text, bukan `@<nama>`. WhatsApp otomatis render nama kontak dari phone book. Mention hanya work untuk kontak yang sudah save nomor bot.

**Response `201`:**
```json
{
  "id": 42,
  "case_code": "INC000023470570",
  "wa_message_id": "true_120363xxx@g.us_3EB0A1B2C3",
  "text": "punten rekan @6281113021236 mohon bantuannya untuk case STC ada 1 case lagi\n\n#STC\nTicket Remedy : INC000023470570\n..."
}
```
`text` adalah pesan final persis yang terkirim ke grup — tampilkan di toast/modal sukses sebagai bukti.

**Error:** `401` API key tidak valid · `422` field tidak valid · `502` WAHA tidak terjangkau / session tidak WORKING (case **tidak** tersimpan, suruh user retry).

---

### 3.2 `GET /api/cases` — Daftar case (dashboard list)

**Headers:**
```
X-API-Key: <key>
```

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

**Headers:**
```
X-API-Key: <key>
```

**Response `200`:**
```json
{
  "case": {
    "id": 42,
    "case_code": "INC000023470570",
    "case_type": "stc",
    "title": "...",
    "fields": { "ticket_remedy": "INC000023470570", "...": "..." },
    "message_text": "punten rekan ...",
    "wa_message_id": "true_120363xxx@g.us_3EB0A1B2C3",
    "status": "done",
    "ack": "READ",
    "created_at": "...",
    "updated_at": "..."
  },
  "messages": [
    {
      "wa_message_id": "true_..._AAA",
      "quoted_id": null,
      "author": null,
      "author_name": null,
      "body": "punten rekan @628xxx ...",
      "from_me": true,
      "created_at": "..."
    },
    {
      "wa_message_id": "false_..._BBB",
      "quoted_id": "3EB0A1B2C3",
      "author": "6281113021236@lid",
      "author_name": "Mas Habib",
      "body": "dicek dulu mas",
      "from_me": false,
      "created_at": "..."
    },
    {
      "wa_message_id": "false_..._CCC",
      "quoted_id": "3EB0D4E5F6",
      "author": "6281299887766@lid",
      "author_name": "Budi Santoso",
      "body": "done mas, sudah diluruskan",
      "from_me": false,
      "created_at": "..."
    }
  ],
  "updates": [
    {
      "id": 7,
      "case_id": 42,
      "wa_message_id": "false_..._BBB",
      "author": "6281113021236@lid",
      "body": "dicek dulu mas",
      "parsed_status": null,
      "parsed_note": "dicek dulu mas",
      "source": "reply",
      "confidence": null,
      "created_at": "..."
    },
    {
      "id": 8,
      "case_id": 42,
      "wa_message_id": "false_..._CCC",
      "author": "6281299887766@lid",
      "body": "done mas, sudah diluruskan",
      "parsed_status": "done",
      "parsed_note": "done mas, sudah diluruskan",
      "source": "chain",
      "confidence": null,
      "created_at": "..."
    }
  ],
  "participants": [
    { "author": "6281113021236@lid", "name": "Mas Habib" },
    { "author": "6281299887766@lid", "name": "Budi Santoso" }
  ]
}
```

Cara render timeline:
- `messages` membentuk **pohon** lewat `quoted_id`; root = pesan dengan `wa_message_id == case.wa_message_id` (juga satu-satunya yang `from_me: true`).
- Render sebagai thread bersarang (indent per level) atau flat chronological — data dua-duanya cukup.
- `participants` = daftar solver yang terlibat rantai — format `{author, name}`. Cocok untuk chip "Ditangani oleh:" dengan nama tampil.
- `updates[].source` menjelaskan bagaimana update tertangkap:
  - `reply` = langsung reply ke pesan root bot
  - `chain` = reply ke pesan orang lain (eskalasi) — layak diberi ikon khusus
- `author` berformat `xxx@lid` (WhatsApp LID). `author_name` adalah nama kontak yang di-resolve otomatis dari phone book via WAHA API.

**Performance:**
- Webhook diproses dalam ~50ms untuk message yang match via regex/chain
- LLM hanya dipanggil saat message TIDAK match regex/chain (fallback terakhir)
- Contact name di-cache di memory, resolve on-demand untuk kontak baru

**Error:** `401` API key tidak valid · `404 { "detail": "Case not found" }`.

---

### 3.4 `POST /api/cases/{id}/status` — Koreksi status manual

Untuk tombol "Tandai selesai" / "Buka ulang" di UI.

**Headers:**
```
X-API-Key: <key>
Content-Type: application/json
```

**Request:**
```json
{ "status": "done", "note": "konfirmasi via telpon" }
```
- `status` wajib, enum status. `note` opsional, tampil di timeline sebagai update `source: manual`.

**Response `200`:** `{ "ok": true }`

---

### 3.5 `POST /api/crawl` — Backfill histori grup (admin)

**Headers:**
```
X-API-Key: <key>
```

**Query:** `limit` (default 200, maks mengikuti WAHA).

**Response `200`:**
```json
{
  "fetched": 200,
  "stored": 187,
  "updates_applied": 12,
  "store_errors": 0,
  "process_errors": 1
}
```

Operasi ini berat (tarik histori WA + proses). Jangan dipanggil otomatis dari UI utama — sediakan di halaman admin/pengaturan dengan konfirmasi.

---

### 3.6 `GET /health` — Health check

**Tidak perlu auth.**

**Response `200`:** `{ "status": "ok", "db": "ok", "waha": "ok" }`

Berguna untuk banner "sistem gangguan" di UI. Nilai selain `ok` pada `db`/`waha` = backend atau WAHA bermasalah.

---

## 4. Format Error

FastAPI default: `{ "detail": "pesan error" }` dengan status code sesuai. Validasi body gagal → `422` dengan `detail` berisi array lokasi field. FE cukup menampilkan `detail` apa adanya.

| Status | Keterangan |
|---|---| 
| `401` | API key tidak valid atau tidak dikirim |
| `404` | Resource tidak ditemukan |
| `422` | Request body tidak valid (validasi gagal) |
| `429` | Rate limit terlampaui (default 60 req/menit per IP) |
| `502` | WAHA tidak terjangkau atau session tidak WORKING |

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
3. **Detail:** klik baris → `GET /api/cases/{id}` → render thread `messages` + sidebar `updates` + chip `participants` dengan nama.
4. **Aksi:** tombol koreksi status → `POST /api/cases/{id}/status` → refresh detail.

### Contoh render participants
```
Ditangani oleh:
  [chip] Mas Habib (@6281113021236)
  [chip] Budi Santoso (@6281299887766)
```

### Contoh render timeline
```
🤖 Bot: punten rekan @6281113021236 mohon bantuannya untuk case STC ...
  ↳ Mas Habib: dicek dulu mas                    [reply · 10:30]
    ↳ Budi Santoso: done mas, sudah diluruskan    [chain · 10:32] ✅ done
```

## 7. Changelog

### v1.2 (20 Agustus 2026)
- **Contact name resolution**: Backend resolve `@lid` → nama kontak via WAHA API. Field `author_name` di messages, `participants` berformat `{author, name}`.
- **Header format**: `punten rekan @<phone> mohon bantuannya untuk case <TYPE> ada 1 case lagi`
- **Mention pakai phone number**: `@6281113021236` bukan `@Nama` — WhatsApp auto-render nama.
- **Source label**: `reply` = langsung ke root, `chain` = reply ke reply (eskalasi).
- **Webhook performance**: ~50ms untuk message yang match via regex/chain (sebelumnya 9-10 detik karena LLM).
- **LLM optimization**: LLM hanya dipanggil saat case TIDAK ditemukan via regex/chain.
- **Connection pool**: psycopg ConnectionPool (2-10 koneksi) menggantikan connect baru per request.
- **Async health check**: Health check async dengan 3s WAHA timeout.
- **Rate limiting**: 60 requests/minute per IP.
- **CORS**: Configurable via `ALLOWED_ORIGINS` env var.
- **Security headers**: HSTS, X-Frame-Options, X-Content-Type-Options.
- **Schema**: Kolom `author_name` ditambahkan ke `wa_messages` table.

### v1.1 (19 Agustus 2026)
- Initial release: single group + reply-chain traversal.
- Waterfall matching: regex → reply → chain → LLM.
- 2-pass crawl for historical backfill.
