# API Contract — Moban FU Case Tracker (untuk Tim Frontend)

**Versi:** 1.6 · **Tanggal:** 27 Agustus 2026 · **Backend:** FastAPI · **Base path:** `/api`
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
| Contact Names | Backend resolve `@lid` → nama kontak via WAHA API (prioritas pushname > phone book). `author_name` tersedia di messages & participants |
| Media | Image/video dari solver di-reply ke case → tersimpan di DB. `media_url` = proxy URL yang bisa diakses browser. `media_type` = MIME type. |

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

**Request (contoh: STC + Non AO, field lengkap):**
```json
{
  "area_id": 1,
  "regional_id": 1,
  "sumber_ticket": "STC",
  "jenis_case": "Non AO",
  "asal_grapari": null,
  "mentions": [
    { "number": "6281113021236", "name": "Mas Habib" }
  ],
  "fields": {
    "ticket_remedy": "INC000023470570",
    "order_id": "MOk4260811023440131b25f60",
    "no_indihome": "0211234567",
    "last_milestone": "TSEL_ACTIVATION_FALLOUT",
    "request_case": "Mohon bantuannya follow up aktivasi",
    "detail_case": "Pelanggan kendala aktivasi, last milestone TSEL_ACTIVATION_FALLOUT. Mohon dicek di sisi TSEL.",
    "link_evidence": [
      "https://prnt.sc/example1",
      "https://drive.google.com/example2"
    ]
  }
}
```

**Request (contoh: Grapari + Non Order):**
```json
{
  "area_id": 3,
  "regional_id": 7,
  "sumber_ticket": "Grapari",
  "jenis_case": "Non Order",
  "asal_grapari": "GraPARI Surabaya",
  "mentions": [
    { "number": "6281234567890", "name": "Budi" }
  ],
  "fields": {
    "ticket_remedy": "INC000098765432",
    "no_indihome": "0315678901",
    "request_case": "Mohon cek status aktivasi",
    "detail_case": "Pelanggan sudah bayar tapi layanan belum aktif.",
    "link_evidence": [
      "https://imgur.com/bukti_bayar",
      "https://imgur.com/screenshot"
    ]
  }
}
```

**Request (contoh: Web IT + Mobile):**
```json
{
  "area_id": 4,
  "regional_id": 10,
  "sumber_ticket": "Web IT",
  "jenis_case": "Mobile",
  "asal_grapari": null,
  "mentions": [
    { "number": "6289876543210" }
  ],
  "fields": {
    "ticket_remedy": "INC000055555555",
    "msisdn": "6281299988877",
    "request_case": "Cek coverage area",
    "detail_case": "Pelanggan komplain sinyal lemah di area Jakarta Selatan.",
    "link_evidence": [
      "https://imgur.com/sinyal_screenshot"
    ]
  }
}
```

Aturan:
- Field **required** per jenis case: `ticket_remedy` (semua), `no_indihome` (Non Order/Non AO), `order_id` (Non AO), `msisdn` (Mobile). Field lain **opsional**.
- `jenis_case` — nilai di luar enum di-downgrade ke `Non Order`.
- `sumber_ticket` — jika diisi `Grapari`, `asal_grapari` bisa diisi (free text, tidak ada tabel lookup).
- `area_id` / `regional_id` — ID dari tabel lookup. `regional_id` harus valid untuk `area_id` yang dipilih.
- `fields.link_evidence` — array of URL. Bisa multiple link. Kosongkan array jika tidak ada evidence.
- `mentions` opsional. `number` = nomor WA format internasional **tanpa `+`** (`628xxx`). `name` opsional, hanya untuk tampilan.
- `case_code` diturunkan backend dari `fields.ticket_remedy`. Bisa `null`.
- Mengirim ulang `case_code` yang sudah ada = **re-FU**: status kembali `open`, jangkar pesan diperbarui. Bukan error.

**Format pesan WhatsApp (otomatis):**
```
punten rekan @6281113021236 mohon bantuannya untuk case Non AO ada 1 case lagi

#Non AO
Area : Area 1
Regional : Sumbagut
Sumber Ticket : STC
Jenis Case : Non AO
Ticket Remedy : INC000023470570
Order ID : MOk4260811023440131b25f60
Nomer Indihome : 0211234567
Last Milestone : TSEL_ACTIVATION_FALLOUT
Request Case : Mohon bantuannya follow up aktivasi
Detail Case : Pelanggan kendala aktivasi...
Link Evidence :
https://prnt.sc/example1
https://drive.google.com/example2
```

> **Catatan mention:** Backend menggunakan `@<nomor telepon>` di text, bukan `@<nama>`. WhatsApp otomatis render nama kontak dari phone book. Mention hanya work untuk kontak yang sudah save nomor bot.

**Response `201`:**
```json
{
  "id": 42,
  "case_code": "INC000023470570",
  "wa_message_id": "true_120363xxx@g.us_3EB0A1B2C3",
  "text": "punten rekan @6281113021236 mohon bantuannya untuk case Non AO ada 1 case lagi\n\n#Non AO\nArea : Area 1\nRegional : Sumbagut\nSumber Ticket : STC\nJenis Case : Non AO\nTicket Remedy : INC000023470570\nOrder ID : MOk4260811023440131b25f60\nNomer Indihome : 0211234567\n..."
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
      "media_url": null,
      "media_type": null,
      "created_at": "..."
    },
    {
      "wa_message_id": "false_..._BBB",
      "quoted_id": "3EB0A1B2C3",
      "author": "6281113021236@lid",
      "author_name": "Mas Habib",
      "body": "dicek dulu mas",
      "from_me": false,
      "media_url": null,
      "media_type": null,
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
- `media_url` berisi proxy URL yang bisa diakses browser (via `GET /api/media/proxy`). Kalau `null`, tidak ada media. Render sebagai gambar/video inline di timeline.
- `media_type` = MIME type media (contoh: `image/jpeg`, `video/mp4`). Gunakan untuk menentukan render: `image/*` → `<img>`, `video/*` → `<video>`, lainnya → link download.
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


---

## 5. Solver Contacts CRUD

Tabel kontak solver yang bisa di-manage dari Swagger/API. Data ini bisa dipakai untuk populate dropdown mention di frontend.

### 5.1 `GET /api/solver-contacts` — Daftar kontak solver

**Headers:** `X-API-Key: <key>`

**Query params (semua opsional):**
| Param | Tipe | Keterangan |
|---|---|---|
| `is_active` | bool | Filter status aktif. Kosongkan untuk semua. |
| `q` | string | Pencarian substring di nama atau role |

**Response `200`:**


---

### 5.2 `POST /api/solver-contacts` — Tambah kontak baru

**Headers:** `X-API-Key: <key>`, `Content-Type: application/json`

**Request:**

- `name` wajib, string.
- `phone_number` wajib, string. Harus unik di antara kontak aktif. Format internasional tanpa `+` (`628xxx`).
- `role` opsional, string (posisi/jabatan).

**Response `201`:**


**Error:** `409` Nomor sudah terdaftar.

---

### 5.3 `GET /api/solver-contacts/{id\}` — Detail kontak

**Response `200`:** Object kontak lengkap.

**Error:** `404` Kontak tidak ditemukan.

---

### 5.4 `PUT /api/solver-contacts/{id\}json
{
  "name": "Mas Habib Updated",
  "role": "Solver Senior"
}
 — Soft delete kontak

Data tidak dihapus, hanya `is_active` di-set `false`.

**Response `200`:** `{ "ok": true }`

**Error:** `404` Tidak ditemukan.

---

## 6. Reminders (Sundul)

Fitur untuk mengingatkan solver agar follow up case yang belum ditangani. Bot akan reply ke pesan case asli di grup WA dengan mention solver.

### 6.1 `POST /api/cases/{id\}/reminder` — Manual reminder

**Headers:** `X-API-Key: <key>`

**Request (opsional):**

- `message` opsional. Default: "Halo, mohon bantuannya untuk follow up case ini. Terima kasih."

Bot akan reply ke `wa_message_id` case dengan pesan + mention solver.

**Response `200`:**


---

### 6.2 `GET /api/cases/{id\}/reminder` — Riwayat reminder case

**Response `200`:**
json
{
  "checked": 15,
  "reminded": 3,
  "cases": [
    { "id": 42, "case_code": "INC000023470570", "reminder_count": 1 },
    { "id": 43, "case_code": "INC000098765432", "reminder_count": 2 }
  ]
}
```

**Contoh crontab (reminder tiap 2 jam):**
```
0 */2 * * * curl -X POST "http://localhost:8000/api/reminders/run?hours=2" -H "X-API-Key: your-key"
```

---

### 6.4 `GET /api/reminders/pending` — Case yang perlu reminder

**Headers:** `X-API-Key: <key>`

**Query params:**
| Param | Default | Keterangan |
|---|---|---|
| `hours` | 2 | Jam idle minimum |
| `limit` | 50 | Max jumlah case |
| `area_id` | - | Filter Area ID |
| `regional_id` | - | Filter Regional ID |
| `sumber_ticket` | - | Filter sumber ticket |
| `jenis_case` | - | Filter jenis case |

**Response `200`:**


---

## 7. Media Proxy

Endpoint untuk proxy media (image/video/doc) dari WAHA supaya bisa diakses dari browser/frontend. WAHA mengembalikan URL internal Docker (`http://waha:3000/api/files/...`) yang tidak bisa diakses dari luar.

### 7.1 `GET /api/media/proxy` — Proxy media dari WAHA

**Tidak perlu auth** (agar bisa diakses langsung oleh `<img>` / `<video>` di browser).

**Query params:**
| Param | Required | Keterangan |
|---|---|---|
| `url` | ✅ | URL media dari WAHA (akan di-URL-encode) |

**Contoh:**
```
GET /api/media/proxy?url=http%3A%2F%2Fwaha%3A3000%2Fapi%2Ffiles%2Fabc.jpg
```

**Response `200`:** Streaming response dengan `Content-Type` sesuai media (contoh: `image/jpeg`, `video/mp4`). Body berisi data binary media.

**Response `400`:** URL tidak valid (bukan URL WAHA/internal).

**Response `502`:** Gagal fetch dari WAHA.

> **Catatan:** Frontend tidak perlu handle proxy URL secara manual. `media_url` di timeline response (`GET /api/cases/{id}`) sudah berisi proxy URL yang bisa langsung dipakai di `<img src="...">` atau `<video src="...">`.

---

## 8. Error
## 8. Format Error

FastAPI default: `{ "detail": "pesan error" }` dengan status code sesuai. Validasi body gagal → `422` dengan `detail` berisi array lokasi field. FE cukup menampilkan `detail` apa adanya.

| Status | Keterangan |
|---|---| 
| `401` | API key tidak valid atau tidak dikirim |
| `404` | Resource tidak ditemukan |
| `422` | Request body tidak valid (validasi gagal) |
| `429` | Rate limit terlampaui (default 60 req/menit per IP) |
| `502` | WAHA tidak terjangkau atau session tidak WORKING |

## 9. Spesifikasi Form Dinamis

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
| Key | Label UI | Tipe | Required |
|---|---|---|---|
| `ticket_remedy` | Ticket Remedy | text (pattern `INC\d+`) | ✅ |
| `no_indihome` | Nomer Indihome | text/tel | ✅ |
| `request_case` | Request Case | text | ❌ |
| `detail_case` | Detail Case | textarea (long text) | ❌ |
| `link_evidence` | Link Evidence | array of URL (multiple links) | ❌ |

#### `Non AO` — Case Non Activation Order
| Key | Label UI | Tipe | Required |
|---|---|---|---|
| `ticket_remedy` | Ticket Remedy | text | ✅ |
| `order_id` | Order ID | text | ✅ |
| `no_indihome` | Nomer Indihome | text/tel | ✅ |
| `last_milestone` | Last Milestone | text | ❌ |
| `request_case` | Request Case | text | ❌ |
| `detail_case` | Detail Case | textarea (long text) | ❌ |
| `link_evidence` | Link Evidence | array of URL (multiple links) | ❌ |

#### `Mobile` — Case Mobile
| Key | Label UI | Tipe | Required |
|---|---|---|---|
| `ticket_remedy` | Ticket Remedy | text | ✅ |
| `msisdn` | MSISDN | tel | ✅ |
| `request_case` | Request Case | text | ❌ |
| `detail_case` | Detail Case | textarea (long text) | ❌ |
| `link_evidence` | Link Evidence | array of URL (multiple links) | ❌ |

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

## 10. Alur Integrasi yang Disarankan

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

## 11. Contoh Lengkap: Semua Kombinasi Sumber Ticket × Jenis Case

Berikut 9 kombinasi lengkap dengan request body, field required, dan format pesan WhatsApp yang dihasilkan.

---

### 8.1 STC + Non Order

**Request:**
```json
{
  "area_id": 1,
  "regional_id": 1,
  "sumber_ticket": "STC",
  "jenis_case": "Non Order",
  "fields": {
    "ticket_remedy": "INC000011111111",
    "no_indihome": "0211111111",
    "request_case": "Mohon bantuannya cek status pasang baru",
    "detail_case": "Pelanggan request pasang baru INDIHOME 50Mbps.",
    "link_evidence": ["https://imgur.com/formorder"]
  }
}
```

**Pesan WA:**
```
punten rekan @628xxx mohon bantuannya untuk case Non Order ada 1 case lagi

#Non Order
Area : Area 1
Regional : Sumbagut
Sumber Ticket : STC
Jenis Case : Non Order
Ticket Remedy : INC000011111111
Nomer Indihome : 0211111111
Request Case : Mohon bantuannya cek status pasang baru
Detail Case : Pelanggan request pasang baru INDIHOME 50Mbps.
Link Evidence :
https://imgur.com/formorder
```

---

### 8.2 STC + Non AO

**Request:**
```json
{
  "area_id": 1,
  "regional_id": 1,
  "sumber_ticket": "STC",
  "jenis_case": "Non AO",
  "fields": {
    "ticket_remedy": "INC000022222222",
    "order_id": "MOk4260811023440131b25f60",
    "no_indihome": "0212222222",
    "last_milestone": "TSEL_ACTIVATION_FALLOUT",
    "request_case": "Follow up aktivasi macet",
    "detail_case": "Order ID sudah masuk tapi aktivasi stuck di TSEL.",
    "link_evidence": ["https://prnt.sc/screenshot1", "https://imgur.com/screenshot2"]
  }
}
```

**Pesan WA:**
```
punten rekan @628xxx mohon bantuannya untuk case Non AO ada 1 case lagi

#Non AO
Area : Area 1
Regional : Sumbagut
Sumber Ticket : STC
Jenis Case : Non AO
Ticket Remedy : INC000022222222
Order ID : MOk4260811023440131b25f60
Nomer Indihome : 0212222222
Last Milestone : TSEL_ACTIVATION_FALLOUT
Request Case : Follow up aktivasi macet
Detail Case : Order ID sudah masuk tapi aktivasi stuck di TSEL.
Link Evidence :
https://prnt.sc/screenshot1
https://imgur.com/screenshot2
```

---

### 8.3 STC + Mobile

**Request:**
```json
{
  "area_id": 2,
  "regional_id": 4,
  "sumber_ticket": "STC",
  "jenis_case": "Mobile",
  "fields": {
    "ticket_remedy": "INC000033333333",
    "msisdn": "6281233344455",
    "request_case": "Cek sinyal area Jakarta",
    "detail_case": "Pelanggan komplain sinyal hilang sejak kemarin.",
    "link_evidence": ["https://imgur.com/sinyal"]
  }
}
```

**Pesan WA:**
```
punten rekan @628xxx mohon bantuannya untuk case Mobile ada 1 case lagi

#Mobile
Area : Area 2
Regional : Jabo
Sumber Ticket : STC
Jenis Case : Mobile
Ticket Remedy : INC000033333333
MSISDN : 6281233344455
Request Case : Cek sinyal area Jakarta
Detail Case : Pelanggan komplain sinyal hilang sejak kemarin.
Link Evidence :
https://imgur.com/sinyal
```

---

### 8.4 Grapari + Non Order

**Request:**
```json
{
  "area_id": 3,
  "regional_id": 7,
  "sumber_ticket": "Grapari",
  "jenis_case": "Non Order",
  "asal_grapari": "GraPARI Surabaya",
  "fields": {
    "ticket_remedy": "INC000044444444",
    "no_indihome": "0314444444",
    "request_case": "Bantu cek billing overlap",
    "detail_case": "Pelanggan tagihan dobel bulan ini.",
    "link_evidence": ["https://imgur.com/billing_screenshot"]
  }
}
```

**Pesan WA:**
```
punten rekan @628xxx mohon bantuannya untuk case Non Order ada 1 case lagi

#Non Order
Area : Area 3
Regional : Jateng DIY
Sumber Ticket : Grapari
Asal Grapari : GraPARI Surabaya
Jenis Case : Non Order
Ticket Remedy : INC000044444444
Nomer Indihome : 0314444444
Request Case : Bantu cek billing overlap
Detail Case : Pelanggan tagihan dobel bulan ini.
Link Evidence :
https://imgur.com/billing_screenshot
```

---

### 8.5 Grapari + Non AO

**Request:**
```json
{
  "area_id": 2,
  "regional_id": 5,
  "sumber_ticket": "Grapari",
  "jenis_case": "Non AO",
  "asal_grapari": "GraPARI Bandung",
  "fields": {
    "ticket_remedy": "INC000055555555",
    "order_id": "ORD-2026-0827-001",
    "no_indihome": "0225555555",
    "last_milestone": "INSTALL_PENDING",
    "request_case": "Urgent: pelanggan sudah tunggu 3 hari",
    "detail_case": "Order aktivasi sudah 3 hari belum diproses. Pelanggan sudah follow up berkali-kali.",
    "link_evidence": ["https://drive.google.com/lampiran1", "https://imgur.com/chat_screenshot"]
  }
}
```

**Pesan WA:**
```
punten rekan @628xxx mohon bantuannya untuk case Non AO ada 1 case lagi

#Non AO
Area : Area 2
Regional : Jabar
Sumber Ticket : Grapari
Asal Grapari : GraPARI Bandung
Jenis Case : Non AO
Ticket Remedy : INC000055555555
Order ID : ORD-2026-0827-001
Nomer Indihome : 0225555555
Last Milestone : INSTALL_PENDING
Request Case : Urgent: pelanggan sudah tunggu 3 hari
Detail Case : Order aktivasi sudah 3 hari belum diproses...
Link Evidence :
https://drive.google.com/lampiran1
https://imgur.com/chat_screenshot
```

---

### 8.6 Grapari + Mobile

**Request:**
```json
{
  "area_id": 4,
  "regional_id": 10,
  "sumber_ticket": "Grapari",
  "jenis_case": "Mobile",
  "asal_grapari": "GraPARI Makassar",
  "fields": {
    "ticket_remedy": "INC000066666666",
    "msisdn": "6281666677788",
    "request_case": "Cek kuota habis atau gangguan jaringan?",
    "detail_case": "Pelanggan bilang kuota masih ada tapi tidak bisa internetan.",
    "link_evidence": ["https://imgur.com/speedtest"]
  }
}
```

**Pesan WA:**
```
punten rekan @628xxx mohon bantuannya untuk case Mobile ada 1 case lagi

#Mobile
Area : Area 4
Regional : Sulawesi
Sumber Ticket : Grapari
Asal Grapari : GraPARI Makassar
Jenis Case : Mobile
Ticket Remedy : INC000066666666
MSISDN : 6281666677788
Request Case : Cek kuota habis atau gangguan jaringan?
Detail Case : Pelanggan bilang kuota masih ada tapi tidak bisa internetan.
Link Evidence :
https://imgur.com/speedtest
```

---

### 8.7 Web IT + Non Order

**Request:**
```json
{
  "area_id": 1,
  "regional_id": 2,
  "sumber_ticket": "Web IT",
  "jenis_case": "Non Order",
  "fields": {
    "ticket_remedy": "INC000077777777",
    "no_indihome": "0617777777",
    "request_case": "Reset password akun pelanggan",
    "detail_case": "Pelanggan tidak bisa login ke myIndiHOME. Sudah coba reset sendiri tapi gagal.",
    "link_evidence": ["https://imgur.com/error_page"]
  }
}
```

**Pesan WA:**
```
punten rekan @628xxx mohon bantuannya untuk case Non Order ada 1 case lagi

#Non Order
Area : Area 1
Regional : Sumbagsel
Sumber Ticket : Web IT
Jenis Case : Non Order
Ticket Remedy : INC000077777777
Nomer Indihome : 0617777777
Request Case : Reset password akun pelanggan
Detail Case : Pelanggan tidak bisa login ke myIndiHOME...
Link Evidence :
https://imgur.com/error_page
```

---

### 8.8 Web IT + Non AO

**Request:**
```json
{
  "area_id": 3,
  "regional_id": 8,
  "sumber_ticket": "Web IT",
  "jenis_case": "Non AO",
  "fields": {
    "ticket_remedy": "INC000088888888",
    "order_id": "WO-2026-0827-003",
    "no_indihome": "0358888888",
    "request_case": "Escalasi: order stuck 5 hari kerja",
    "detail_case": "Work order sudah 5 hari kerja belum ada progress. Mohon segera ditindaklanjuti.",
    "link_evidence": ["https://imgur.com/order_tracking", "https://drive.google.com/chat_log"]
  }
}
```

**Pesan WA:**
```
punten rekan @628xxx mohon bantuannya untuk case Non AO ada 1 case lagi

#Non AO
Area : Area 3
Regional : Jatim
Sumber Ticket : Web IT
Jenis Case : Non AO
Ticket Remedy : INC000088888888
Order ID : WO-2026-0827-003
Nomer Indihome : 0358888888
Request Case : Escalasi: order stuck 5 hari kerja
Detail Case : Work order sudah 5 hari kerja belum ada progress...
Link Evidence :
https://imgur.com/order_tracking
https://drive.google.com/chat_log
```

---

### 8.9 Web IT + Mobile

**Request:**
```json
{
  "area_id": 4,
  "regional_id": 11,
  "sumber_ticket": "Web IT",
  "jenis_case": "Mobile",
  "fields": {
    "ticket_remedy": "INC000099999999",
    "msisdn": "6281999900011",
    "request_case": "Ganti paket dari Basic ke Premium",
    "detail_case": "Pelanggan minta upgrade paket tapi tidak bisa dari aplikasi.",
    "link_evidence": ["https://imgur.com/app_error"]
  }
}
```

**Pesan WA:**
```
punten rekan @628xxx mohon bantuannya untuk case Mobile ada 1 case lagi

#Mobile
Area : Area 4
Regional : Kalimantan
Sumber Ticket : Web IT
Jenis Case : Mobile
Ticket Remedy : INC000099999999
MSISDN : 6281999900011
Request Case : Ganti paket dari Basic ke Premium
Detail Case : Pelanggan minta upgrade paket tapi tidak bisa dari aplikasi.
Link Evidence :
https://imgur.com/app_error
```

---

### Ringkasan Field per Kombinasi

| Sumber | Jenis Case | Required Fields | Optional Fields | Asal Grapari |
|---|---|---|---|---|
| STC | Non Order | ticket_remedy, no_indihome | request_case, detail_case, link_evidence | ❌ |
| STC | Non AO | ticket_remedy, order_id, no_indihome | last_milestone, request_case, detail_case, link_evidence | ❌ |
| STC | Mobile | ticket_remedy, msisdn | request_case, detail_case, link_evidence | ❌ |
| Grapari | Non Order | ticket_remedy, no_indihome | request_case, detail_case, link_evidence | ✅ Wajib input |
| Grapari | Non AO | ticket_remedy, order_id, no_indihome | last_milestone, request_case, detail_case, link_evidence | ✅ Wajib input |
| Grapari | Mobile | ticket_remedy, msisdn | request_case, detail_case, link_evidence | ✅ Wajib input |
| Web IT | Non Order | ticket_remedy, no_indihome | request_case, detail_case, link_evidence | ❌ |
| Web IT | Non AO | ticket_remedy, order_id, no_indihome | last_milestone, request_case, detail_case, link_evidence | ❌ |
| Web IT | Mobile | ticket_remedy, msisdn | request_case, detail_case, link_evidence | ❌ |

## 12. Changelog

### v1.6 (27 Agustus 2026)
- **Media proxy**: Endpoint `GET /api/media/proxy` untuk serve media dari WAHA yang sebelumnya hanya accessible dari dalam Docker. Frontend bisa langsung pakai `media_url` di `<img>` atau `<video>`.
- **Media di timeline**: Setiap pesan di `GET /api/cases/{id}` sekarang include `media_url` (proxy URL) dan `media_type` (MIME type). Mendukung image + caption dan image-only replies.
- **Pushname priority**: Contact name resolution sekarang prioritize `pushname` (nama yang user set di WA) daripada `name` (phone book).
- **Solver Contacts CRUD**: 5 endpoint baru (`GET/POST/GET/{id}/PUT/{id}/DELETE/{id}`) untuk manage kontak solver. Soft delete via `is_active` flag.
- **Reminder (Sundul)**: 4 endpoint baru untuk kirim reminder ke solver. Manual reminder, auto reminder batch (cron), dan riwayat reminder per case.
- **Required fields**: `ticket_remedy` (semua), `no_indihome` (Non Order/Non AO), `order_id` (Non AO), `msisdn` (Mobile). `REQUIRED_FIELDS` dict tersedia di backend untuk validasi frontend.

### v1.5 (27 Agustus 2026)
- **Contoh lengkap**: Semua 9 kombinasi Sumber Ticket × Jenis Case dengan request body, response, dan format pesan WA.
- **Required fields**: `ticket_remedy` (semua), `no_indihome` (Non Order/Non AO), `order_id` (Non AO), `msisdn` (Mobile).
- **Pushname优先**: Contact name resolution sekarang prioritize `pushname` (nama yang user set di WA) daripada `name` (phone book).
- **LID API**: `resolve_contact_name` sekarang handle 2-step: `@lid` → phone via WAHA LID API → name via Contacts API.

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
