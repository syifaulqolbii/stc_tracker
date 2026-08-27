# API Contract — Moban FU Case Tracker (untuk Tim Frontend)

**Versi:** 1.4 · **Tanggal:** 27 Agustus 2026 · **Backend:** FastAPI · **Base path:** `/api`
**Referensi:** PRD v1.2, schema-v1-2.sql

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

### Jenis Case (tabel lookup `jenis_cases`)
| Nilai | Keterangan |
|---|---|
| `Non Order` | Case non-order (STC, SMOOA, UFO, dll) |
| `Non AO` | Case non-activation order |
| `Mobile` | Case mobile |

### Sumber Ticket (tabel lookup `sumber_tickets`)
| Nilai | Keterangan |
|---|---|
| `STC` | Sumber dari STC |
| `Grapari` | Sumber dari GraPARI (wajib input Asal Grapari) |
| `Web IT` | Sumber dari Web IT |

### Status Case
| Nilai | Badge |
|---|---|
| `open` | Abu |
| `in_progress` | Biru |
| `done` | Hijau |
| `issue` | Merah |

### Ack (pesan keluar)
| Nilai | Keterangan |
|---|---|
| `PENDING` | Belum terkirim |
| `SERVER` | Diterima server |
| `DEVICE` | Diterima device |
| `READ` | Sudah dibaca |

### Source Labels (progress_updates)
| Source | Keterangan | Contoh |
|---|---|---|
| `rule` | Pesan mengandung kode INC yang match dengan case yang sedang open | "INC000023470570 sudah done" |
| `reply` | Reply langsung ke pesan root bot di grup | Tekan lama → Reply → "done mas" |
| `chain` | Reply ke pesan orang lain (bukan root), atau reply ke reply | A reply ke bot → B reply ke A → source: chain |
| `llm` | Tidak match regex/chain, tapi LLM mendeteksi case + status | Pesan bebas yang mengandung info tiket |
| `crawl` | Diproses dari backfill histori grup | `POST /api/crawl` |
| `manual` | Diubah manual via API | `POST /api/cases/{id}/status` |

---

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
  "area_id": 1,
  "regional_id": 2,
  "sumber_ticket": "Grapari",
  "jenis_case": "Non Order",
  "asal_grapari": "GraPARI Bandung",
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
- Semua field **opsional**. Field baru (`area_id`, `regional_id`, `sumber_ticket`, `jenis_case`, `asal_grapari`) dan field lama (`ticket_remedy`, `no_indihome`, dll) semuanya tidak wajib diisi.
- `jenis_case` — nilai di luar enum di-downgrade ke `Non Order`.
- `sumber_ticket` — jika diisi `Grapari`, `asal_grapari` bisa diisi (free text, tidak ada tabel lookup).
- `area_id` / `regional_id` — ID dari tabel lookup. `regional_id` harus valid untuk `area_id` yang dipilih.
- `fields` — semua key opsional; yang kosong tidak muncul di teks WA.
- `mentions` opsional. `number` = nomor WA format internasional **tanpa `+`** (`628xxx`). `name` opsional, hanya untuk tampilan teks.
- `case_code` diturunkan backend dari `fields.ticket_remedy` atau `fields.case_id`. Bisa `null`.
- Mengirim ulang `case_code` yang sudah ada = **re-FU**: status kembali `open`, jangkar pesan diperbarui. Bukan error.

**Format pesan WhatsApp (otomatis):**
```
punten rekan @6281113021236 mohon bantuannya untuk case Non Order ada 1 case lagi

#Non Order
Area : Area 1
Regional : Regional 2
Sumber Ticket : Grapari
Asal Grapari : GraPARI Bandung
Jenis Case : Non Order
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
  "text": "punten rekan @6281113021236 mohon bantuannya untuk case Non Order ada 1 case lagi\n\n#Non Order\nArea : Area 1\n..."
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
| `case_type` | `Non Order` | filter jenis case (nama dari tabel lookup) |
| `area_id` | `1` | filter berdasarkan Area ID |
| `regional_id` | `2` | filter berdasarkan Regional ID |
| `sumber_ticket` | `Grapari` | filter sumber ticket |
| `q` | `INC0000234` | pencarian substring di `case_code` dan `title` (case-insensitive) |

**Response `200`:**
```json
[
  {
    "id": 42,
    "case_code": "INC000023470570",
    "case_type": "non_order",
    "title": "Moban dibantu add subsnya di domain/realm telkom.net...",
    "status": "in_progress",
    "ack": "READ",
    "area_id": 1,
    "regional_id": 2,
    "sumber_ticket_id": 2,
    "jenis_case_id": 1,
    "asal_grapari": "GraPARI Bandung",
    "area_name": "Area 1",
    "regional_name": "Regional 2",
    "sumber_ticket_name": "Grapari",
    "jenis_case_name": "Non Order",
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
    "case_type": "non_order",
    "title": "...",
    "fields": { "ticket_remedy": "INC000023470570", "...": "..." },
    "message_text": "punten rekan ...",
    "wa_message_id": "true_120363xxx@g.us_3EB0A1B2C3",
    "status": "done",
    "ack": "READ",
    "area_id": 1,
    "regional_id": 2,
    "sumber_ticket_id": 2,
    "jenis_case_id": 1,
    "asal_grapari": "GraPARI Bandung",
    "area_name": "Area 1",
    "regional_name": "Regional 2",
    "sumber_ticket_name": "Grapari",
    "jenis_case_name": "Non Order",
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

## 4. Lookup Endpoints

### 4.1 `GET /api/areas` — Daftar semua Area

**Headers:**
```
X-API-Key: <key>
```

**Response `200`:**
```json
[
  { "id": 1, "name": "Area 1" },
  { "id": 2, "name": "Area 2" },
  { "id": 3, "name": "Area 3" }
]
```

---

### 4.2 `GET /api/areas/{area_id}/regionals` — Daftar Regional per Area

**Headers:**
```
X-API-Key: <key>
```

**Response `200`:**
```json
{
  "area": { "id": 1, "name": "Area 1" },
  "regionals": [
    { "id": 1, "name": "Regional 1" },
    { "id": 2, "name": "Regional 2" },
    { "id": 3, "name": "Regional 3" }
  ]
}
```

**Error:** `404 { "detail": "Area not found" }`.

---

### 4.3 `GET /api/sumber-tickets` — Daftar Sumber Ticket

**Headers:**
```
X-API-Key: <key>
```

**Response `200`:**
```json
[
  { "id": 1, "name": "STC" },
  { "id": 2, "name": "Grapari" },
  { "id": 3, "name": "Web IT" }
]
```

---

### 4.4 `GET /api/jenis-cases` — Daftar Jenis Case

**Headers:**
```
X-API-Key: <key>
```

**Response `200`:**
```json
[
  { "id": 1, "name": "Non Order" },
  { "id": 2, "name": "Non AO" },
  { "id": 3, "name": "Mobile" }
]
```

---

## 5. Format Error

FastAPI default: `{ "detail": "pesan error" }` dengan status code sesuai. Validasi body gagal → `422` dengan `detail` berisi array lokasi field. FE cukup menampilkan `detail` apa adanya.

| Status | Keterangan |
|---|---| 
| `401` | API key tidak valid atau tidak dikirim |
| `404` | Resource tidak ditemukan |
| `422` | Request body tidak valid (validasi gagal) |
| `429` | Rate limit terlampaui (default 60 req/menit per IP) |
| `502` | WAHA tidak terjangkau atau session tidak WORKING |

## 6. Spesifikasi Form Dinamis

### Alur Form Input
1. **Pilih Area** → dropdown `GET /api/areas`
2. **Pilih Regional** → dropdown `GET /api/areas/{area_id}/regionals` (muncul setelah Area dipilih)
3. **Pilih Sumber Ticket** → dropdown `GET /api/sumber-tickets` (STC / Grapari / Web IT)
4. **Asal Grapari** → text input (hanya muncul jika Sumber Ticket = Grapari)
5. **Pilih Jenis Case** → dropdown `GET /api/jenis-cases` (Non Order / Non AO / Mobile)
6. **Input fields** → dua mode:
   - **Mode Form:** Isi field-field per jenis case (semua opsional)
   - **Mode Textarea:** Copy-paste langsung wording case

### Mode Form: Field per Jenis Case

Semua field **opsional**. Kolom bertanda ★ disarankan diisi di UI demi kualitas tracking.

#### `Non Order` — Case STC/SMOOA/UFO/Other
| Key | Label UI | Tipe |
|---|---|---|
| `ticket_remedy` | Ticket Remedy | text (pattern `INC\d+`) |
| `no_indihome` | Nomer Indihome | text/tel |
| `request_case` | Request Case | text |
| `detail_case` | Detail Case | textarea (long text) |
| `link_evidence` | Link Evidence | array of URL (multiple links) |

#### `Non AO` — Case Non Activation Order
| Key | Label UI | Tipe |
|---|---|---|
| `ticket_remedy` | Ticket Remedy | text |
| `order_id` | Order ID | text |
| `no_indihome` | Nomer Indihome | text/tel |
| `last_milestone` | Last Milestone | text |
| `request_case` | Request Case | text |
| `detail_case` | Detail Case | textarea (long text) |
| `link_evidence` | Link Evidence | array of URL (multiple links) |

#### `Mobile` — Case Mobile
| Key | Label UI | Tipe |
|---|---|---|
| `ticket_remedy` | Ticket Remedy | text |
| `msisdn` | MSISDN | tel |
| `request_case` | Request Case | text |
| `detail_case` | Detail Case | textarea (long text) |
| `link_evidence` | Link Evidence | array of URL (multiple links) |

### Mode Textarea: Copy-Paste Wording

Untuk setiap jenis case, tersedia placeholder wording yang bisa dicopy-paste. User tinggal ganti data sesuai case.

**Placeholder Non Order:**
```
punten rekan @<nomor> mohon bantuannya untuk case Non Order ada 1 case lagi

Area : <nama area>
Regional : <nama regional>
Sumber Ticket : <STC/Grapari/Web IT>
Asal Grapari : <nama GraPARI> (jika sumber Grapari)
Jenis Case : Non Order

Ticket Remedy : INC000000000000
Nomer Indihome : 0211234567
Request Case : <request case>
Detail Case : <detail case>
Link Evidence :
<link1>
<link2>
```

**Placeholder Non AO:**
```
punten rekan @<nomor> mohon bantuannya untuk case Non AO ada 1 case lagi

Area : <nama area>
Regional : <nama regional>
Sumber Ticket : <STC/Grapari/Web IT>
Asal Grapari : <nama GraPARI> (jika sumber Grapari)
Jenis Case : Non AO

Ticket Remedy : INC000000000000
Order ID : <order id>
Nomer Indihome : 0211234567
Last Milestone : <last milestone>
Request Case : <request case>
Detail Case : <detail case>
Link Evidence :
<link1>
<link2>
```

**Placeholder Mobile:**
```
punten rekan @<nomor> mohon bantuannya untuk case Mobile ada 1 case lagi

Area : <nama area>
Regional : <nama regional>
Sumber Ticket : <STC/Grapari/Web IT>
Asal Grapari : <nama GraPARI> (jika sumber Grapari)
Jenis Case : Mobile

Ticket Remedy : INC000000000000
MSISDN : 08xxxxxxxxxx
Request Case : <request case>
Detail Case : <detail case>
Link Evidence :
<link1>
<link2>
```

### Komponen Mention (semua jenis case)
- Multi-select kontak → dikirim sebagai `mentions: [{number, name}]`.
- Saran: daftar kontak solver di-hardcode di FE dulu (atau tabel config nanti), user tinggal centang.

## 7. Alur Integrasi yang Disarankan

1. **Load lookup data saat init:**
   - `GET /api/areas` → populate dropdown Area
   - `GET /api/sumber-tickets` → populate dropdown Sumber Ticket
   - `GET /api/jenis-cases` → populate dropdown Jenis Case
2. **Dynamic dropdown:**
   - User pilih Area → `GET /api/areas/{id}/regionals` → populate dropdown Regional
   - User pilih Sumber Ticket = Grapari → tampilkan field Asal Grapari
3. **Form input:** render field per §6 → submit `POST /api/cases` → tampilkan `text` dari response sebagai konfirmasi "pesan terkirim ke grup".
4. **Dashboard:** `GET /api/cases` (+filter) → tabel dengan badge status & indikator ack. Polling 30 dtk.
5. **Detail:** klik baris → `GET /api/cases/{id}` → render thread `messages` + sidebar `updates` + chip `participants` dengan nama.
6. **Aksi:** tombol koreksi status → `POST /api/cases/{id}/status` → refresh detail.

### Contoh render participants
```
Ditangani oleh:
  [chip] Mas Habib (@6281113021236)
  [chip] Budi Santoso (@6281299887766)
```

### Contoh render timeline
```
🤖 Bot: punten rekan @6281113021236 mohon bantuannya untuk case Non Order ...
  ↳ Mas Habib: dicek dulu mas                    [reply · 10:30]
    ↳ Budi Santoso: done mas, sudah diluruskan    [chain · 10:32] ✅ done
```

## 8. Changelog

### v1.4 (27 Agustus 2026)
- **Field per jenis case disederhanakan**: Hanya field yang dibutuhkan per jenis case.
  - Non Order: ticket_remedy, no_indihome, request_case, detail_case, link_evidence
  - Non AO: ticket_remedy, order_id, no_indihome, last_milestone, request_case, detail_case, link_evidence
  - Mobile: ticket_remedy, msisdn, request_case, detail_case, link_evidence
- **`link_evidence`**: Array of URL (bukan single link). Bisa multiple evidence per case.
- **`request_case`**: Field baru untuk deskripsi request/keperluan case.
- **Field dihapus dari rendering**: email, cp, tgl_kejadian, status_case, raw_text, tier, lokasi, case_id, grapari, milestone_info tidak lagi ditampilkan per jenis case.

### v1.3 (27 Agustus 2026)
- **Area & Regional**: Tabel lookup baru dengan hierarchy Area → Regional. Endpoint `GET /api/areas` dan `GET /api/areas/{id}/regionals`.
- **Sumber Ticket**: Tabel lookup baru (STC, Grapari, Web IT). Endpoint `GET /api/sumber-tickets`.
- **Jenis Case**: Tabel lookup baru (Non Order, Non AO, Mobile). Menggantikan enum lama (stc/smooa/mobile/ufo/other). Endpoint `GET /api/jenis-cases`.
- **Asal Grapari**: Field free text, hanya muncul jika Sumber Ticket = Grapari.
- **Field lama opsional**: Semua field lama (ticket_remedy, no_indihome, dll) tetap ada tapi opsional.
- **Mode textarea**: User bisa copy-paste langsung wording case tanpa input field satu per satu.
- **Placeholder wording**: Tersedia per jenis case untuk mode textarea.
- **Swagger tags**: Endpoint dikelompokkan (Cases, Lookup, Webhooks, System) dengan summary & description.
- **Backward compatibility**: `case_type` lama di database tetap di-keep, data lama di-migrate ke `jenis_case_id`.

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
