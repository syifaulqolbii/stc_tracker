# API Contract — Moban FU Case Tracker (untuk Tim Frontend)

**Versi:** 1.12 · **Tanggal:** 7 September 2026 · **Backend:** FastAPI · **Base path:** `/api`
**Referensi:** PRD v1.5, schema-v1-2.sql, schema-multi-group.sql, schema-migration-group-default.sql

> Catatan: backend FastAPI juga mengekspos dokumentasi interaktif otomatis di `GET /docs` (Swagger UI) dan skema mesin di `GET /openapi.json` — bisa diimpor ke Postman. Dokumen ini adalah kontrak human-readable yang jadi acuan utama.

---

## 1. Gambaran Umum

| Item | Nilai |
|---|---| 
| Base URL (prod) | `https://api.stc.syfa.site` (via nginx + SSL) |
| Format | JSON, `Content-Type: application/json` |
| Auth | Header `X-API-Key: <key>` — **wajib** untuk semua endpoint kecuali `/health`, `/webhooks/waha`, dan `/api/auth/access-code` |
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

### 3.1 `POST /api/auth/access-code` — Login dengan kode akses

Gate akses untuk frontend. User masukkan kode akses → dapat JWT token.

**Tidak perlu API key.**

**Headers:**
```
Content-Type: application/json
```

**Request:**
```json
{
  "code": "YOUR_ACCESS_CODE"
}
```

**Response `200`:**
```json
{
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "expires_in": 86400
}
```

- `token` — JWT token, simpan di frontend (localStorage). Gunakan sebagai `Authorization: Bearer <token>` di request berikutnya.
- `expires_in` — waktu expired dalam detik (default 86400 = 24 jam).

**Error:**
| Status | Keterangan |
|---|---|
| `401` | Kode akses tidak valid |
| `503` | Access codes belum di-configure di server |

**Flow login:**
```
1. Frontend tampilkan form input kode akses
2. User input kode → POST /api/auth/access-code
3. Backend validasi kode → return JWT token
4. Frontend simpan token di localStorage
5. Semua request API: Authorization: Bearer <token>
```

---

### 3.1a `POST /api/cases/preview` — Preview teks case (tanpa kirim)

Render teks case **persis seperti yang akan dikirim** ke grup WA — tanpa mengirim apa pun ke WhatsApp dan **tanpa membuat case** di database. Untuk ditampilkan di UI sebelum user menekan tombol kirim/test.

- **Request body:** sama persis dengan `POST /api/cases` (semua field opsional, `group_id` diabaikan).
- **Response `200`:** `{ "text": "...", "mentions": [{"number": "628...", "name": "..."}] }`
- Teks yang di-preview **dijamin identik** dengan teks yang dikirim `POST /api/cases` untuk input yang sama.
- Read-only: hanya SELECT nama area/regional, tidak ada write.

### 3.1b `POST /api/cases/test-send` — Kirim case ke grup test (tanpa membuat case)

Kirim teks case ke **grup default** (`is_default=true`, biasanya grup test development) supaya user bisa melihat hasilnya di WhatsApp **sebelum** case dikirim ke grup asli.

- **Request body:** sama dengan `POST /api/cases`, plus field opsional:
  - `test_group_id` (int, opsional) — override grup tujuan test. Kosongkan → pakai grup default. ID grup default bisa dilihat via `GET /api/groups?include_default=true`.
- **Response `200`:** `{ "ok": true, "test_group_id": 1, "test_group_name": "Test Development", "wa_message_id": "...", "text": "..." }`
- **TIDAK membuat row case** di database — tidak muncul di dashboard, tidak di-reminder, tidak di-track.
- Error: grup test tidak ada → `422`; grup nonaktif → `409`; tidak ada grup default (dan `test_group_id` kosong) → `400`.
- **Alur FE yang disarankan:** tombol **"Kirim Test"** → `POST /api/cases/test-send` → user cek grup test di WA → kalau oke, tombol **"Kirim"** → `POST /api/cases` (endpoint lama, membuat case sungguhan di grup asli).

### 3.2 `POST /api/cases` — Buat & kirim case ke grup WA

> **Catatan (v1.10 — grup default):** `group_id` kini **opsional** (int) — ID grup WA tujuan dari `GET /api/groups`, dipakai **switcher grup** di frontend. Kalau user **tidak memilih** grup (field dikosongkan / tidak dikirim), case otomatis dikirim ke **grup default** (`wa_groups.is_default = true` — biasanya grup test development; **tidak muncul** di list switcher, lihat §6.1). Case tetap ter-track penuh di grup default itu. Kalau belum ada grup default → `400`.

**Headers:**
```
X-API-Key: <key>
Content-Type: application/json
```

**Request (contoh: STC + Non AO, field lengkap):**
```json
{
  "group_id": 1,
  "area_id": 1,
  "regional_id": 1,
  "sumber_ticket": "STC",
  "jenis_case": "Non AO",
  "asal_grapari": null,
  "mentions": [
    { "number": "6281113021236", "name": "Mas Habib" }
  ],
  "custom_header": null,
  "fields": {
    "ticket_remedy": "INC000023470570",
    "order_id": "MOk4260811023440131b25f60",
    "no_indihome": "0211234567",
    "last_milestone": "TSEL_ACTIVATION_FALLOUT",
    "request_case": "Mohon bantuannya follow up aktivasi",
    "detail_case": "Pelanggan kendala aktivasi, last milestone TSEL_ACTIVATION_FALLOUT. Mohon dicek di sisi TSEL.",
    "link_evidence": [
      { "label": "Evidence DSC", "url": ["https://prnt.sc/example1", "https://imgur.com/example1b"] },
      { "label": "Screenshot", "url": "https://drive.google.com/example2" }
    ]
  }
}
```

**Request (contoh: Grapari + Non Order):**
```json
{
  "group_id": 1,
  "area_id": 3,
  "regional_id": 7,
  "sumber_ticket": "Grapari",
  "jenis_case": "Non Order",
  "asal_grapari": "GraPARI Surabaya",
  "mentions": [
    { "number": "6281234567890", "name": "Budi" }
  ],
  "custom_header": null,
  "fields": {
    "ticket_remedy": "INC000098765432",
    "no_indihome": "0315678901",
    "request_case": "Mohon cek status aktivasi",
    "detail_case": "Pelanggan sudah bayar tapi layanan belum aktif.",
    "link_evidence": [
      { "url": "https://imgur.com/bukti_bayar" },
      { "label": "Screenshot", "url": "https://imgur.com/screenshot" }
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
  "custom_header": null,
  "fields": {
    "ticket_remedy": "INC000055555555",
    "msisdn": "6281299988877",
    "request_case": "Cek coverage area",
    "detail_case": "Pelanggan komplain sinyal lemah di area Jakarta Selatan.",
    "link_evidence": [
      { "label": "Sinyal", "url": "https://imgur.com/sinyal_screenshot" }
    ]
  }
}
```

Aturan:
- `group_id` — **opsional**, int. ID grup WA tujuan (dari `GET /api/groups`). **Dikosongkan → grup default** (`is_default`, biasanya grup test development). Grup eksplisit tidak valid / `is_active=false` → `404`; tidak ada grup default terkonfigurasi → `400`.
- Field **required** per jenis case: `ticket_remedy` (semua), `no_indihome` (Non Order/Non AO), `order_id` (Non AO), `msisdn` (Mobile). Field lain **opsional**.
- `jenis_case` — nilai di luar enum di-downgrade ke `Non Order`.
- `sumber_ticket` — jika diisi `Grapari`, `asal_grapari` bisa diisi (free text, tidak ada tabel lookup).
- `area_id` / `regional_id` — ID dari tabel lookup. `regional_id` harus valid untuk `area_id` yang dipilih. ID tak dikenal → **422** (v1.16 — divalidasi **sebelum** pesan dikirim, supaya case tidak masuk grup tanpa tercatat di DB).
- `fields.link_evidence` — array of object `{"label": "...", "url": "..."}`. `url` bisa string tunggal ATAU array (banyak link untuk satu label). `label` opsional (kalau kosong, link dirender polos). Kosongkan array jika tidak ada evidence. Backward compatible: string URL lama tetap diterima.
- `mentions` opsional. `number` = nomor WA format internasional **tanpa `+`** (`628xxx`). `name` opsional, hanya untuk tampilan.
- **Auto-mention `@<nomor>` yang diketik manual** (v1.17; normalisasi format v1.18): token `@628xxx` di custom_header/detail_case OTOMATIS ikut dikirim sebagai mention WAHA — teks polos `@angka` tanpa `mentionedJid` tidak pernah ngetag di WhatsApp (root cause case INC000024096448). v1.18 juga terima format manusiawi: `@+62 811-9298-880`, `@081…`, spasi/strip/titik → selalu dinormalisasi ke `62…`. Nomor hasil extract disimpan ke `cases.mentions` (`name: null`) supaya reminder cron/manual ikut ngetag. Duplikat dengan mentions dropdown di-merge (satu entry). Token tanpa `@` (MSISDN/indihome/INC) dan `@nama` tidak disentuh.
- `custom_header` opsional. Custom header pesan. Gunakan `{phone}` sebagai placeholder nomor WA. **(v1.17) `{phone}` tapi `mentions` kosong → `422`** (`custom_header mengandung {phone} tapi mentions kosong — pilih solver dari dropdown atau hapus token {phone}`). Sebelumnya literal `{phone}` ikut terkirim ke grup. Jika kosong, pakai default: `punten rekan @<phone> mohon bantuannya untuk case <TYPE> ada 1 case lagi`. Mention `@<phone>` otomatis ditambahkan.
- `case_code` diturunkan backend dari `fields.ticket_remedy`. Bisa `null`.
- Mengirim ulang `case_code` yang sudah ada = **re-FU**: status kembali `open`, jangkar pesan diperbarui. Bukan error. Termasuk case yang sudah di-soft-delete (v1.16 — `deleted_at` di-clear, case muncul kembali di dashboard).

**Format pesan WhatsApp (otomatis) — compact:**
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
https://drive.google.com/example2
```

> **Catatan compact header:** Format `#SumberTicket_JenisCase_Area_Regional`. Beberapa bagian bisa kosong jika field tidak diisi (contoh: `#Non AO` jika hanya jenis case).

> **Catatan mention:** Backend menggunakan `@<nomor telepon>` di text, bukan `@<nama>`. WhatsApp otomatis render nama kontak dari phone book. Mention hanya work untuk kontak yang sudah save nomor bot.

**Contoh dengan custom_header:**
Request:
```json
{
  "mentions": [{ "number": "6281113021236" }],
  "custom_header": "Halo {phone}, mohon bantuannya ya 🙏",
  "jenis_case": "Non AO",
  "fields": { "ticket_remedy": "INC123", "order_id": "O123", "no_indihome": "021123" }
}
```
Pesan terkirim:
```
Halo @6281113021236, mohon bantuannya ya 🙏

#Non AO
...
```

> **Catatan custom_header:** Placeholder `{phone}` akan diganti dengan `@<nomor>` untuk setiap mention. Jika tidak ada `{phone}` di custom header, mention akan ditambahkan di akhir.

**Response `201`:**
```json
{
  "id": 42,
  "case_code": "INC000023470570",
  "wa_message_id": "true_120363xxx@g.us_3EB0A1B2C3",
  "group_id": 1,
  "group_name": "Grup A",
  "text": "punten rekan @6281113021236 mohon bantuannya untuk case Non AO ada 1 case lagi\n\n#Non AO\nArea : Area 1\nRegional : Sumbagut\nSumber Ticket : STC\nJenis Case : Non AO\nTicket Remedy : INC000023470570\nOrder ID : MOk4260811023440131b25f60\nNomer Indihome : 0211234567\n..."
}
```
`text` adalah pesan final persis yang terkirim ke grup — tampilkan di toast/modal sukses sebagai bukti.

**Error:** `401` API key tidak valid · `422` `group_id` <= 0 atau ID tidak dikenal (pesan error menyebut nilainya) · `409` grup ada tetapi **sedang dinonaktifkan** (pesan error menyebut nama grupnya) · `400` `group_id` dikosongkan tapi belum ada grup default · `502` WAHA tidak terjangkau / session tidak WORKING (case **tidak** tersimpan, suruh user retry).

> ⚠️ **Untuk frontend:** kalau user tidak memilih grup, **hilangkan field `group_id`** (atau kirim `null`) — jangan kirim `0`/`""`, keduanya ditolak. Untuk mengisi dropdown, panggil `GET /api/groups` **tanpa param**: hasilnya sudah aman (hanya grup aktif & bukan default), jadi opsi yang tampil dijamin bisa dipakai.

---

### 3.3 `GET /api/cases` — Daftar case (dashboard list)

**Headers:**
```
X-API-Key: <key>
```

**Query params (semua opsional, bisa dikombinasi):**

| Param | Contoh | Keterangan |
|---|---|---| 
| `include_deleted` | `true` | Sertakan case yang sudah di-delete (default: false) |
| `status` | `open` | filter enum status |
| `case_type` | `Non Order` | filter jenis case (nama dari tabel lookup) |
| `area_id` | `1` | filter berdasarkan Area ID |
| `regional_id` | `2` | filter berdasarkan Regional ID |
| `group_id` | `1` | filter berdasarkan grup WA (ID dari `GET /api/groups`) |
| `sumber_ticket` | `Grapari` | filter sumber ticket |
| `q` | `INC0000234` | pencarian substring di `case_code`, `title`, dan seluruh isi `fields` (no_indihome, order_id, case_id, msisdn, link evidence, dll) — case-insensitive (v1.24) |
| `date_from` | `2026-06-01` | **Opsional (v1.22)** — filter `created_at` mulai tanggal ini (inklusif). Format `YYYY-MM-DD`, bisa dikirim sendirian |
| `date_to` | `2026-08-31` | **Opsional (v1.22)** — filter `created_at` sampai tanggal ini (**inklusif** — case 31 Agu jam berapapun ikut). Format `YYYY-MM-DD` |
| `page` | `1` | **Opsional (v1.20)** — nomor halaman, mulai dari 1. Hanya dipakai jika `limit` dikirim |
| `limit` | — | **Opsional (v1.20)** — `1`–`100`. Diisi → response jadi **envelope `{data, pagination}`**. Tidak dikirim → response tetap **array polos** (legacy, kompatibel FE lama) |

**Error validasi tanggal (v1.22):** format salah / `date_from > date_to` → `422` dengan `detail` string, contoh: `"date_from harus format YYYY-MM-DD (contoh: 2026-06-01)"`.

> **Filter tanggal (v1.22):** berbasis `created_at` (tanggal case dibuat, UTC). Rentang inklusif di kedua ujung — `?date_from=2026-06-01&date_to=2026-08-31` = semua case dibuat 1 Juni s.d. 31 Agustus. Filter ini juga berlaku identik di **export Excel** (§3.3b) karena memakai SQL builder yang sama.

**Response `200` — LEGACY (tanpa param `limit`, identik perilaku lama):**
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
    "no_indihome": "0211234567",
    "group_id": 1,
    "group_name": "Grup A",
    "area_name": "Area 1",
    "regional_name": "Regional 2",
    "sumber_ticket_name": "Grapari",
    "jenis_case_name": "Non Order",
    "created_at": "2026-08-19T09:14:02+07:00",
    "updated_at": "2026-08-19T10:31:55+07:00"
  }
]
```

**Response `200` — PAGINATED (dengan `?page=1&limit=50`):**
```json
{
  "data": [ ...array rows yang sama persis dengan legacy di atas... ],
  "pagination": {
    "page": 1, "limit": 50, "total": 123,
    "total_pages": 3, "has_next": true, "has_prev": false
  }
}
```
Diurutkan `updated_at DESC` — case yang baru ada aktivitas selalu di atas. `ack` menunjukkan pesan case sudah dibaca grup atau belum (berguna untuk indikator "✓✓ biru"). Setiap row kini menyertakan `group_id` dan `group_name` (hasil join `wa_groups`) — pakai untuk badge/nama grup di dashboard (switcher). Sejak **v1.19**, setiap row juga menyertakan **`no_indihome`** (diambil dari `fields.no_indihome` case, `null` kalau tidak ada) — untuk kolom Nomor IH di list tanpa perlu fetch detail per case.

> **Pagination (v1.20) — OPT-IN, backwards compatible:** tanpa param `limit`, response **tetap array polos** — kode FE existing tidak perlu diubah. Saat FE siap pakai pagination, tambahkan `?page=N&limit=M` (M maks 100) dan baca `resp.data` (rows) + `resp.pagination` (untuk UI paging). Polling 30 dtk disarankan pindah ke pagination supaya payload tetap kecil saat data membesar. `limit=0` / `limit>100` / `page<1` → `422`.

---

### 3.3b `GET /api/cases/export.xlsx` — Export case ke Excel (v1.21)

**Headers:**
```
X-API-Key: <key>
```

**Query params:** **identik dengan §3.3** (`status`, `case_type`, `area_id`, `regional_id`, `sumber_ticket`, `group_id`, `q`, `include_deleted`, + `date_from`/`date_to` sejak v1.22) — **tanpa** `page`/`limit` (export selalu semua row yang lolos filter). Karena memakai SQL builder yang sama dengan list, hasil export **dijamin konsisten** dengan yang tampil di dashboard.

**Response `200`:** file binary `.xlsx`
```
Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet
Content-Disposition: attachment; filename="cases_export_20260921-0646.xlsx"
```

**Isi file:**

| Kolom | Sumber |
|---|---|
| ID, Case Code, Status, Nomor Indihome | kolom `cases` / `fields->>'no_indihome'` |
| Jenis Case, Area, Regional, Sumber Ticket, Grup WA | hasil join lookup (nama, bukan ID) |
| Judul | `cases.title` |
| Reminder Count | `cases.reminder_count` |
| Created At, Updated At | format `YYYY-MM-DD HH:MM` |

Header bold, lebar kolom disetel. Nilai `null` → sel kosong.

**Error:** `401` tanpa/da key salah · `422` filter tidak valid (termasuk tanggal) · file tetap valid (header saja) kalau 0 row lolos filter.

**Contoh integrasi FE (fetch → blob → download):**
```js
async function exportExcel(filters) {
  const qs = new URLSearchParams(filters).toString(); // filter aktif dashboard
  const resp = await fetch(`/api/cases/export.xlsx?${qs}`, {
    headers: { "X-API-Key": API_KEY },
  });
  if (!resp.ok) throw await getErrMsg(resp);
  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);
  const a = Object.assign(document.createElement("a"), {
    href: url, download: `cases_${new Date().toISOString().slice(0,10)}.xlsx`,
  });
  a.click();
  URL.revokeObjectURL(url);
}
// exportExcel({ status: "open", date_from: "2026-06-01", date_to: "2026-08-31" });
```
> Jangan pakai `<a href>` langsung ke endpoint — API key akan bocor di URL/history. Selalu fetch + blob.

---

### 3.4 `GET /api/cases/{id}` — Detail + timeline rantai

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
    "no_indihome": "0211234567",
    "group_id": 1,
    "group_name": "Grup A",
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
- `media_url` berisi URL media yang bisa diakses browser. Format: `https://api.stc.syfa.site/api/media/file/{uuid}.jpg`. Kalau `null`, tidak ada media. Render sebagai gambar/video inline di timeline. Untuk media lama (sebelum v1.6.1), masih pakai format proxy URL.
- `media_type` = MIME type media (contoh: `image/jpeg`, `video/mp4`). Gunakan untuk menentukan render: `image/*` → `<img>`, `video/*` → `<video>`, lainnya → link download.
- `author` berformat `xxx@lid` (WhatsApp LID). `author_name` adalah nama kontak yang di-resolve otomatis dari phone book via WAHA API.

**Performance:**
- Webhook diproses dalam ~50ms untuk message yang match via regex/chain
- LLM hanya dipanggil saat message TIDAK match regex/chain (fallback terakhir)
- Contact name di-cache di memory, resolve on-demand untuk kontak baru

**Error:** `401` API key tidak valid · `404 { "detail": "Case not found" }`.

---

### 3.5 `DELETE /api/cases/{id}` — Hapus case (soft delete)

Tandai case sebagai deleted. Case tidak benar-benar dihapus dari DB, hanya ditandai dengan `deleted_at` timestamp.

**Headers:**
```
X-API-Key: <key>
```

**Response `200`:**
```json
{
  "ok": true,
  "case_code": "INC000023470570"
}
```

**Error:** `404` Case tidak ditemukan atau sudah di-delete.

---

### 3.6 `POST /api/cases/{id}/status` — Koreksi status manual

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

### 3.6a `POST /api/cases/{id}/replies` — Balas pesan solver dari web

**Headers:** `X-API-Key: <key>`, `Content-Type: application/json`

**Request:**
```json
{
  "message": "siap, kami cek dulu ya",
  "reply_to_wa_message_id": "false_..._BBB",
  "attachments": [
    {"filename": "bukti.jpg", "mimetype": "image/jpeg", "data_base64": "<base64>"}
  ],
  "mentions": [{"number": "6281113021236", "name": "Mas Habib"}]
}
```

- `reply_to_wa_message_id` wajib — ambil dari `messages[].wa_message_id` di timeline `GET /api/cases/{id}` (pesan solver yang dibalas, bukan root). Milik case lain → `422`.
- `attachments` maks 3 file (>3 → `422`), 5 MB per file decoded (>5 MB → `413`). MIME: `image/jpeg`, `image/png`, `image/webp`, `video/mp4`, `application/pdf` (lainnya → `422`).
- Image terkirim via WAHA `sendImage` (caption = message), lainnya via `sendFile` (caption = nama file).
- Salah satu dari `message` / `attachments` wajib diisi (keduanya kosong → `422`).

**Response `200`:** `{ "ok": true, "wa_message_ids": ["..."] }`

**Error:** `404` case tidak ada · `400` case tidak punya grup aktif · `422` reply_to bukan pesan case ini / MIME tak didukung / base64 invalid / kosong / >3 file · `413` file > 5 MB · `502` WAHA gagal.

**Alur FE:** tombol **Balas** di tiap bubble solver di timeline → form (textarea + picker ≤3 file) → encode base64 → POST → refresh timeline.

> ⚠️ **Selalu balas lewat web, jangan dari HP bot.** Pesan yang diketik manual dari akun bot (`fromMe=true`) di-skip webhook sehingga tidak tercatat di `wa_messages` — reply-chain putus dan balasan solver berikutnya tidak ter-link ke case. Pesan via endpoint ini dicatat (`from_me=true`, `quoted_id` = pesan solver, `case_id` terisi) sehingga rantai lanjut (`source=chain`).

### Contoh payload per use case `POST /api/cases/{id}/replies`

Semua contoh memakai contoh case ini: `GET /api/cases/42` mengembalikan timeline berisi pesan solver:

```json
{
  "messages": [
    { "wa_message_id": "true_120363xxx@g.us_AAA", "quoted_id": null, "author": null, "from_me": true, "body": "punten rekan ... (root case)" },
    { "wa_message_id": "false_6281113021236@lid_BBB", "quoted_id": "3EB0A1B2C3", "author": "6281113021236@lid", "author_name": "Mas Habib", "from_me": false, "body": "minta screenshot bukti pembayarannya dong" }
  ]
}
```

`reply_to_wa_message_id` = `"false_6281113021236@lid_BBB"` (pesan solver, `from_me=false`).

---

#### UC-1 · Balas teks saja (paling umum)

**Request:**
```json
{
  "message": "siap mas, kami cek dulu ya",
  "reply_to_wa_message_id": "false_6281113021236@lid_BBB"
}
```

**Response `200`:**
```json
{ "ok": true, "wa_message_ids": ["true_120363xxx@g.us_DDD"] }
```

---

#### UC-2 · Balas teks + 1 screenshot

Encode file dulu — FE dari `<input type="file">` pakai `FileReader.readAsDataURL` (buang prefix `data:...;base64,`) atau `btoa`; PowerShell untuk test manual:
```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("C:\bukti.jpg")) | Set-Clipboard
```

**Request:**
```json
{
  "message": "ini bukti pembayarannya",
  "reply_to_wa_message_id": "false_6281113021236@lid_BBB",
  "attachments": [
    {"filename": "bukti-pembayaran.jpg", "mimetype": "image/jpeg", "data_base64": "/9j/4AAQSkZJRgABAQ..."}
  ]
}
```

Image -> WAHA `sendImage`, caption = `message`. Response sama (`wa_message_ids` berisi 2 id: 1 teks + 1 image).

---

#### UC-3 · Kirim dokumen PDF tanpa teks (media-only)

`message` boleh dihilangkan — yang wajib minimal salah satu dari `message`/`attachments` terisi.

**Request:**
```json
{
  "reply_to_wa_message_id": "false_6281113021236@lid_BBB",
  "attachments": [
    {"filename": "rekap-tagihan-juni.pdf", "mimetype": "application/pdf", "data_base64": "JVBERi0xLjcK..."}
  ]
}
```

PDF/file non-image -> WAHA `sendFile`, caption = nama file. Response: `wa_message_ids` berisi 1 id.

---

#### UC-4 · Balas 2 gambar sekaligus (maks 3 file)

**Request:**
```json
{
  "message": "berikut 2 screenshot dari sistem internal",
  "reply_to_wa_message_id": "false_6281113021236@lid_BBB",
  "attachments": [
    {"filename": "dashboard-1.png", "mimetype": "image/png", "data_base64": "iVBORw0KGgoAAAANS..."},
    {"filename": "dashboard-2.png", "mimetype": "image/png", "data_base64": "iVBORw0KGgoAAAANS..."}
  ]
}
```

Response: `wa_message_ids` berisi 3 id (1 teks + 2 image). Setiap file dikirim sebagai reply terpisah ke pesan solver yang sama.

---

#### UC-5 · Balas dengan mention/tag solver

WAHA ngetag kalau **nomor ada di teks** (`@628xxx`) — backend auto-extract semua token `@<nomor>` (`@628…`, `@+62…` dengan spasi/strip/titik dinormalisasi). Field `mentions` opsional (untuk merge), jadi cukup ketik `@628xxx` di `message`:

**Request:**
```json
{
  "message": "@6281113021236 mohon cek ulang konfigurasinya ya mas",
  "reply_to_wa_message_id": "false_6281113021236@lid_BBB"
}
```

Kalau mention dikirim via dropdown FE (bukan diketik), isi `mentions: [{"number": "6281113021236", "name": "Mas Habib"}]` + tulis `@6281113021236` di teks. **Catatan:** mention hanya berjalan di pesan **teks** — reply media-only mengabaikan `mentions`.

---

#### UC-6 · Contoh error yang akan ditemui FE

| Situation | Request (intinya) | Response |
|---|---|---|
| reply_to salah case | `"reply_to_wa_message_id": "false_..._XXX"` milik case 43, dikirim ke `/api/cases/42/replies` | `422 {"detail": "reply_to_wa_message_id bukan pesan case ini"}` |
| MIME tidak didukung | `{"filename": "catatan.txt", "mimetype": "text/plain", ...}` | `422 {"detail": "mimetype text/plain tidak didukung"}` |
| base64 rusak | `"data_base64": "!!!bukan-base64!!!"` | `422 {"detail": "data_base64 catatan.txt bukan base64 valid"}` |
| file kebesaran | 1 file 6 MB decoded | `413 {"detail": "bukti.jpg melebihi 5 MB"}` |
| 4 file | `attachments` berisi 4 entry | `422 {"detail": "Maksimal 3 file per balasan"}` |
| body kosong | `{}` atau `{}` tanpa message & attachments | `422 {"detail": "message atau attachments wajib diisi"}` |

---

### 3.7 `POST /api/crawl` — Backfill histori grup (admin)

**Headers:**
```
X-API-Key: <key>
```

**Query params:**
| Param | Default | Keterangan |
|---|---|---|
| `limit` | 200 | Jumlah pesan histori yang diambil **per grup** (maks mengikuti WAHA) |
| `group_id` | - | ID grup WA yang di-crawl (dari `GET /api/groups`). **Kosongkan → crawl SEMUA grup aktif** berurutan. |

**Response `200`:**
```json
{
  "fetched": 400,
  "stored": 370,
  "updates_applied": 24,
  "store_errors": 0,
  "process_errors": 2,
  "groups": [
    {
      "group_id": 1,
      "group_name": "Grup A",
      "fetched": 200,
      "stored": 185,
      "updates_applied": 12,
      "store_errors": 0,
      "process_errors": 1
    },
    {
      "group_id": 2,
      "group_name": "Grup B",
      "fetched": 200,
      "stored": 185,
      "updates_applied": 12,
      "store_errors": 0,
      "process_errors": 1
    }
  ]
}
```
- Field level atas (`fetched`, `stored`, dst) = total seluruh grup yang di-crawl.
- `groups[]` = rincian per grup — berguna untuk indikator progress per grup di UI admin.

**Error:** `401` API key tidak valid · `404` `group_id` tidak ditemukan / grup tidak aktif · `400` tidak ada grup aktif untuk di-crawl.

Operasi ini berat (tarik histori WA + proses, dijalankan per grup). Jangan dipanggil otomatis dari UI utama — sediakan di halaman admin/pengaturan dengan konfirmasi.

---

### 3.8 `GET /health` — Health check

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

## 6. WhatsApp Groups (Multi-Grup) — CRUD

Tabel `wa_groups` = daftar grup WA tujuan case (label + `chat_id` @g.us). Dipakai **switcher grup** di form create case dan filter dashboard. Backend me-seed **satu grup awal ("Grup A")** dari env `WA_GROUP_ID` saat tabel masih kosong — tambah grup lain lewat endpoint di bawah ini.

Selain itu ada **grup default** (`is_default = true`, maks 1 baris) sebagai fallback ketika user tidak memilih grup di switcher — biasanya **grup test development**. Grup default **tidak muncul** di `GET /api/groups` (kecuali `?include_default=true`) supaya tidak terlihat user, tapi **tetap ter-track penuh** oleh webhook/crawl/reminder karena barisnya terdaftar aktif.

Bot (session WAHA) harus **di-add ke semua grup aktif** (termasuk grup default) agar webhook menerima pesan dari grup tersebut; pesan dari grup yang tidak terdaftar di-abaikan.

### 6.1 `GET /api/groups` — Daftar grup WA (untuk switcher)

**Headers:** `X-API-Key: <key>`

**Query params:**
| Param | Tipe | Keterangan |
|---|---|---|
| `include_inactive` | bool | Default `false` → **grup nonaktif disaring keluar**. `true` = sertakan (halaman admin). |
| `include_default` | bool | Default `false` → **grup default disaring keluar** (switcher bersih). `true` = sertakan (untuk halaman admin). |

**SAFE BY DEFAULT** — tanpa param apa pun endpoint ini sudah tepat untuk dropdown switcher: hanya grup **aktif** dan **bukan default** yang dikembalikan. (Sebelum v1.12: grup nonaktif ikut keluar kecuali caller mengirim `?is_active=true`, sehingga frontend bisa menyodorkan grup yang pasti ditolak saat create case. Param `is_active` sudah diganti `include_inactive`.)

**Response `200`:**
```json
[
  { "id": 1, "name": "Grup A", "chat_id": "120363001@g.us", "is_active": true, "is_default": false, "created_at": "...", "updated_at": "..." },
  { "id": 2, "name": "Grup B", "chat_id": "120363002@g.us", "is_active": true, "is_default": false, "created_at": "...", "updated_at": "..." }
]
```

---

### 6.2 `POST /api/groups` — Tambah grup WA

**Headers:** `X-API-Key: <key>`, `Content-Type: application/json`

**Request:**
```json
{ "name": "Grup B", "chat_id": "120363002@g.us" }
```
- `name` wajib, string (label unik).
- `chat_id` wajib, format `digits@g.us` (contoh: `120363002@g.us`).
- `is_default` opsional, bool (default `false`). `true` → jadikan grup fallback + **menggeser** default lama.

**Response `201`:** Object grup lengkap.

**Error:** `409` chat_id sudah terdaftar · `422` format chat_id salah.

---

### 6.3 `GET /api/groups/{id}` — Detail grup

**Response `200`:** Object grup lengkap. **Error:** `404` tidak ditemukan.

---

### 6.4 `PUT /api/groups/{id}` — Update grup (label / chat_id / aktif / default)

**Headers:** `X-API-Key: <key>`, `Content-Type: application/json`

**Request (partial — kirim hanya field yang diubah):**
```json
{ "name": "Grup B - Regional Jabar", "is_active": false }
```
```json
{ "is_default": true }
```
- `is_default: true` → grup ini jadi fallback; default lama otomatis dilepas (maks 1 di level DB, partial unique index).
- `is_default: false` → lepaskan status default.

**Error:** `404` tidak ditemukan · `409` chat_id sudah dipakai grup lain · `422` field tidak valid / tidak ada field yang diubah.

---

### 6.5 `DELETE /api/groups/{id}` — Nonaktifkan grup (soft delete)

Set `is_active = false`. Data tetap di DB karena case lama masih menunjuk grup ini. Setelah dinonaktifkan, case baru tidak bisa dikirim ke grup tsb dan webhook-nya tidak lagi di-track.

**Response `200`:** `{ "ok": true }` · **Error:** `404` tidak ditemukan / sudah nonaktif.

---

### 6.6 `GET /api/waha/groups` — Discovery grup dari WAHA (admin)

**Headers:** `X-API-Key: <key>`

Mengambil **daftar grup WhatsApp yang bot ikuti**, langsung dari WAHA — jadi admin tidak perlu membuka UI/CLI WAHA untuk menyalin `chat_id`. Hanya grup yang dikembalikan (chat personal & status dibuang oleh backend).

**Query params:**
| Param | Default | Keterangan |
|---|---|---|
| `limit` | 500 | Jumlah grup maksimal yang dikembalikan |
| `search` | - | Substring case-insensitive pada **nama** grup |

**Response `200`:**
```json
[
  {
    "chat_id": "120363002@g.us",
    "name": "Grup Baru Belum Didaftarkan",
    "registered": false,
    "group_id": null,
    "is_active": null,
    "is_default": null
  },
  {
    "chat_id": "120363001@g.us",
    "name": "Escalation OPERA - CX100",
    "registered": true,
    "group_id": 2,
    "is_active": true,
    "is_default": false
  }
]
```
- `registered` — `true` bila `chat_id` sudah ada di `wa_groups`. **Yang belum terdaftar diurutkan paling atas** supaya mudah dicari.
- `group_id` / `is_active` / `is_default` — isi baris lokal bila sudah terdaftar; `null` bila belum.
- Pencocokan memakai `chat_id` (stabil), **bukan nama grup** — nama grup WhatsApp bisa diganti kapan saja.

**Error:** `401` API key tidak valid · `502` WAHA error / tidak terjangkau.

**Alur pakai:** `GET /api/waha/groups` → salin `chat_id` yang `registered: false` → `POST /api/groups {"name":"<label>","chat_id":"<...@g.us>"}` → grup langsung muncul di switcher (`GET /api/groups`). Untuk menandainya sebagai fallback, tambahkan `"is_default": true` atau `PUT /api/groups/{id}`.

> Endpoint ini hanya **membaca** dari WAHA — tidak mendaftarkan apa pun otomatis. Butuh session WAHA aktif; panggilan pertama bisa beberapa detik karena WAHA memuat daftar chat.

---

## 7. Reminders (Sundul)

Fitur untuk mengingatkan solver agar follow up case yang belum ditangani. Bot akan reply ke pesan case asli di grup WA dengan mention solver.

### 7.1 `POST /api/cases/{id\}/reminder` — Manual reminder

**Headers:** `X-API-Key: <key>`

**Request (opsional):**

- `message` opsional. Default: "Halo, mohon bantuannya untuk follow up case ini. Terima kasih."

Bot akan reply ke `wa_message_id` case dengan pesan + mention solver.

**Response `200`:**


---

### 7.2 `GET /api/cases/{id\}/reminder` — Riwayat reminder case

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

### 7.4 `GET /api/reminders/pending` — Case yang perlu reminder

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
| `group_id` | - | Filter grup WA (ID dari `GET /api/groups`) |

**Response `200`:**


---

## 8. Media Serving

Backend mendownload media (image/video/doc) dari WAHA saat webhook masuk, menyimpan ke Docker volume, dan serve langsung dari backend. Frontend bisa langsung pakai `media_url` di `<img>` atau `<video>`.

### 8.1 `GET /api/media/file/{filename}` — Serve media file lokal

**Tidak perlu auth** (agar bisa diakses langsung oleh `<img>` / `<video>` di browser).

**Path params:**
| Param | Keterangan |
|---|---|
| `filename` | Nama file media (format: `{uuid}.{ext}`, contoh: `a1b2c3d4e5f6.jpg`) |

**Contoh:**
```
GET /api/media/file/a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4.jpg
```

**Response `200`:** File binary dengan `Content-Type` sesuai extension (image/jpeg, image/png, video/mp4, dll).

**Response `400`:** Filename tidak valid (harus alphanumeric + dot + extension).

**Response `404:** File tidak ditemukan.

> **Catatan:** `media_url` di timeline response (`GET /api/cases/{id}`) sudah berisi URL lengkap yang bisa langsung dipakai di `<img src="...">` atau `<video src="...">`. Frontend tidak perlu handle apapun.

### 8.2 `GET /api/media/proxy` — Legacy proxy (fallback)

**Tidak perlu auth.**

**Query params:**
| Param | Required | Keterangan |
|---|---|---|
| `url` | ✅ | URL media dari WAHA |

> **Catatan:** Endpoint ini adalah fallback untuk media lama (sebelum fitur download diaktifkan). Gunakan `/api/media/file/{filename}` untuk media baru.

### Flow Media

```
1. Solver kirim image + caption di grup WA
2. WAHA kirim webhook → POST /webhooks/waha
3. Backend download dari WAHA → simpan ke /app/media/{uuid}.jpg
4. Simpan media_url: https://api.stc.syfa.site/api/media/file/{uuid}.jpg
5. Frontend render: <img src="{media_url}">
```

> **Docker volume:** File media tersimpan di Docker volume `media_data` yang persist meski container restart.

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
| `link_evidence` | Link Evidence | array of {label, url} | ❌ |

#### `Non AO` — Case Non Activation Order
| Key | Label UI | Tipe | Required |
|---|---|---|---|
| `ticket_remedy` | Ticket Remedy | text | ✅ |
| `order_id` | Order ID | text | ✅ |
| `no_indihome` | Nomer Indihome | text/tel | ✅ |
| `last_milestone` | Last Milestone | text | ❌ |
| `request_case` | Request Case | text | ❌ |
| `detail_case` | Detail Case | textarea (long text) | ❌ |
| `link_evidence` | Link Evidence | array of {label, url} | ❌ |

#### `Mobile` — Case Mobile
| Key | Label UI | Tipe | Required |
|---|---|---|---|
| `ticket_remedy` | Ticket Remedy | text | ✅ |
| `msisdn` | MSISDN | tel | ✅ |
| `request_case` | Request Case | text | ❌ |
| `detail_case` | Detail Case | textarea (long text) | ❌ |
| `link_evidence` | Link Evidence | array of {label, url} | ❌ |

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

### v1.24 (23 September 2026) — search `q` ikut menyisir fields
Peningkatan search di **`GET /api/cases`** dan **`GET /api/cases/export.xlsx`** (shared SQL builder): `q` sekarang mencari di `case_code`, `title`, **dan seluruh isi `fields`** (no_indihome, order_id, case_id, msisdn, link evidence, dll). Contoh: `?q=141410121054` menemukan case dengan Nomor IndiHome tersebut walau tidak ada di judul/kode. Tidak ada param/response baru — FE tidak perlu perubahan apa pun. Catatan: karena menyisir seluruh fields (termasuk URL evidence), match bisa lebih banyak dari sebelumnya (mis. `q=youtube` menemukan case dengan link evidence YouTube).
Fitur webhook internal (tanpa endpoint baru): setiap balasan solver yang ter-link ke case (via reply/chain/rule — semua jenis balasan, termasuk yang mengubah status jadi done) otomatis memicu notifikasi ke **grup default (test)**. Format pesan satu blok:

```
💬 Update Case
Ticket Remedy : INC01239221 | Case ID : 1-SO9BLGS (IH 141410121054)
Dibalas oleh IT - SMOPS:
"silahkan dilakukan pelurusan realm dari sisi upcf dengan radius terlebih dahulu rekan"
Status: in_progress   ← baris ini hanya muncul kalau balasan mengubah status
```

Perilaku: identifier menampilkan **semua yang ada** (`Ticket Remedy` dan/atau `Case ID`, dipisah ` | `) + `(IH <no_indihome>)` kalau ada; keduanya kosong → fallback `Case : <case_code>`. Isi balasan dipotong maks 300 char. Nama solver dari resolve kontak (fallback LID). Guard anti-loop: kalau grup default == grup asal balasan, notif tidak dikirim. Notif bersifat fire-and-forget — kegagalan WAHA tidak menggagalkan update case. Tidak ada perubahan endpoint/kontrak response.

### v1.22 (21 September 2026) — filter rentang tanggal `date_from` / `date_to`
Param baru di **`GET /api/cases`** dan **`GET /api/cases/export.xlsx`**: `date_from` & `date_to` (format `YYYY-MM-DD`, keduanya opsional & bisa satu saja). Filter pada **`created_at`** (tanggal case dibuat), **inklusif** di kedua ujung — `date_to=2026-08-31` memuat case yang dibuat 31 Agustus jam berapapun. Contoh: `?date_from=2026-06-01&date_to=2026-08-31` = 1 Juni s.d. 31 Agustus. Validasi: format salah / `date_from > date_to` → `422` dengan pesan jelas. Bisa dikombinasikan dengan semua filter lain (status, group_id, dll) dan pagination. Karena masuk ke shared SQL builder, list & export dijamin konsisten. Referensi detail + panduan integrasi FE: `docs/export-excel-frontend.md`.

### v1.21 (21 September 2026) — export Excel `/api/cases/export.xlsx`
Endpoint baru `GET /api/cases/export.xlsx`: download file **.xlsx** berisi SEMUA case yang lolos filter (tanpa pagination). **Filter identik 100% dengan `GET /api/cases`** (dipakai ulang SQL builder yang sama — dijamin tidak mungkin beda): `status`, `case_type`, `area_id`, `regional_id`, `sumber_ticket`, `group_id`, `q`, `include_deleted`. Response `Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`, `Content-Disposition: attachment; filename="cases_export_YYYYMMDD-HHMM.xlsx"`. Kolom: ID, Case Code, Jenis Case, Judul, Status, Nomor Indihome, Area, Regional, Sumber Ticket, Grup WA, Reminder Count, Created At, Updated At (datetime format `YYYY-MM-DD HH:MM`). Header bold, lebar kolom rapi. FE: pakai `<a href>` / `window.open` dengan header X-API-Key (atau fetch → blob → trigger download). Dependency baru: `openpyxl` (terpasang otomatis via requirements).

### v1.20.2 (21 September 2026) — fix 500 pagination (KeyError)
Hotfix produksi: `COUNT(*)` kini ber-alias `SELECT COUNT(*) AS total` dan diakses via `fetchone()["total"]` — pool psycopg aplikasi memakai `dict_row`, jadi akses index tuple (`fetchone()[0]`) melempar `KeyError: 0` → 500 di setiap request dengan `limit`. Tidak ada perubahan kontrak response.

### v1.20.1 (21 September 2026) — fix 500 pagination (COUNT malformed)
Hotfix produksi: query COUNT awalnya dibangun dengan `sql.replace()` yang hanya mengganti baris SELECT pertama, menyisakan baris kolom lain → SQL malformasi → syntax error Postgres → 500 di setiap request dengan `limit`. Kini COUNT dibangun dari potongan `FROM cases` ke belakang. Tidak ada perubahan kontrak response.

### v1.20 (21 September 2026) — pagination opt-in di GET /api/cases
Param baru `page` (default 1) & `limit` (1–100). **Backwards compatible**: tanpa `limit`, response tetap array polos (FE lama tidak perlu berubah). Dengan `limit`, response jadi envelope `{data, pagination}` (`page`, `limit`, `total`, `total_pages`, `has_next`, `has_prev`). COUNT dibangun dari SQL filter yang sama (tanpa ORDER BY), data via `LIMIT/OFFSET`. `limit=0`, `limit>100`, `page<1` → `422`. Motivasi: dashboard polling 30 dtk tidak lagi mengambil seluruh tabel saat data membesar.

### v1.19 (17 September 2026) — no_indihome di list `/api/cases`
Setiap row `GET /api/cases` kini menyertakan field **`no_indihome`** (diambil langsung dari `fields->>'no_indihome'` di DB — bukan full `fields`). Nilai `null` kalau case tidak punya nomor IH. Tujuan: FE bisa menampilkan kolom Nomor Indihome di dashboard list tanpa harus memanggil `GET /api/cases/{id}` per case. Tidak ada perubahan request/filter — murni tambahan field di response.

### v1.18 (16 September 2026) — normalisasi format mention manusiawi
Perluasan v1.17: token manual tidak hanya `@628xxx` polos — terima `@+62…`, `@081…`, separator spasi/strip/titik (`@+62 811-9298-880`), selalu dinormalisasi ke `62…` (`^62\d{7,14}$`). Token di bawah 7 digit / format tidak valid diabaikan. Validasi `422` `{phone}` tanpa mentions tetap berlaku.

### v1.17 (16 September 2026) — fix "mention tidak ngetag" (root cause: mentions kosong)
Akar masalah case INC000024096448: pesan berisi literal `@628119298880` (diketik manual di custom_header) tapi `cases.mentions = []` — tanpa `mentionedJid`, teks `@angka` tidak pernah ngetag di WhatsApp (bukan bug grup/LID — participants kedua grup `@c.us` semua, mention by nomor terbukti work di case INC2313132123).
- **Auto-extract `@<nomor>` dari teks** di `waha_send` (satu titik sentral — cover create/test-send/reminder manual/cron): union mentions dropdown + token manual, dedupe. `create_case` juga menyimpan hasil merge (`name: null`) ke `cases.mentions` supaya reminder ikut ngetag.
- **`custom_header` ber-token `{phone}` tapi `mentions` kosong → `422`** (berlaku di `/cases`, `/preview`, `/test-send`). Sebelumnya literal `{phone}` ikut terkirim.
- Kontrak request tidak berubah. Tidak ada migrasi schema (`mentions` JSONB sudah fleksibel).

### v1.16 (14 September 2026) — ticket_remedy wajib format INC + field case_id
⚠️ **BREAKING untuk FE** (hanya jika selama ini mengirim kode non-INC di `ticket_remedy`):
- **`fields.ticket_remedy` kini divalidasi wajib format `INC<9+ digit>`** (contoh: `INC012345678`). Kode lain → `422` dengan pesan: "fields.ticket_remedy harus format INC... kirim sebagai fields.case_id".
- **`fields.case_id` kini dirender ke pesan WA** dengan label sendiri: baris `Case ID : <kode>` (posisi tepat setelah Ticket Remedy). Sebelumnya key ini hanya jadi `case_code` di DB tanpa tampil di pesan.
- Konvensi yang benar: kode tiket Remedy → `ticket_remedy`; kode internal/non-INC → `case_id`. Keduanya bisa dikirim sekaligus (case_code prioritas ticket_remedy).
- `case_code` di DB tetap otomatis: dari `ticket_remedy` atau fallback `case_id` (di-uppercase).

### v1.16 (15 September 2026) — fix "case terkirim ke grup tapi tidak ter-record di DB"
Tiga perbaikan di `POST /api/cases` / `/preview` / `/test-send` (akar masalah: DB write terjadi SETELAH pesan dikirim ke grup, kegagalan DB = pesan sudah masuk grup tanpa row case):
- **`area_id` / `regional_id` tak dikenal → `422`** (sebelumnya lolos diam-diam → INSERT gagal FK *setelah* pesan masuk grup → 500, case hilang). Validasi terjadi SEBELUM kirim — berlaku di ketiga endpoint. Pesan: `area_id 999 tidak dikenal — pakai ID dari GET /api/areas`.
- **`fields.detail_case: null` diterima** (sebelumnya `None[:120]` TypeError setelah pesan terkirim → 500, case hilang). Kini title case = string kosong.
- **Re-FU case_code yang ter-soft-delete kini memunculkan case kembali**: `ON CONFLICT (case_code) DO UPDATE` ikut meng-clear `deleted_at` + reset `status='open'`. Sebelumnya upsert "sukses" 201 tapi row tetap `deleted_at != NULL` → case invisible di dashboard/webhook/reminder.

### v1.15 (14 September 2026) — fix reply-chain reminder + mention rewrite
Perbaikan dari temuan tracing case #15 (lihat `docs/findings-2026-09-14-case-15.md`). **Tidak ada perubahan kontrak endpoint untuk frontend**, tapi respons jadi lebih informatif:
- **Reply ke pesan reminder kini terdeteksi** (sebelumnya hilang diam-diam): pesan reminder (manual & cron) sekarang disimpan ke `wa_messages` dengan `quoted_id` → pesan root case, sehingga solver yang me-reply pesan reminder "mohon di-follow up ya..." tetap terekam sebagai `progress_updates` (source: `chain`). Sebelumnya hanya reply ke pesan root yang terdeteksi.
- **Mention solver tampil sebagai nama**: token mention mentah WAHA (`@71782207893754` — LID internal WhatsApp) di `progress_updates` kini di-rewrite menjadi nama kontak dari contact cache (mis. `@Furqon Nugroho`), fallback ke token asli kalau kontak tidak dikenal. `messages[].body` di `GET /api/cases/{id}` tetap menyimpan body mentah dari WAHA.

### v1.14 (10 September 2026) — fitur test-send case
- **Endpoint baru `POST /api/cases/preview`**: render teks case tanpa kirim & tanpa membuat case. Body sama dengan `POST /api/cases`. Response `{text, mentions}`.
- **Endpoint baru `POST /api/cases/test-send`**: kirim teks case ke grup default (atau `test_group_id` opsional) untuk dicek di WA sebelum kirim ke grup asli. **Tidak membuat row case** di DB. Response `{ok, test_group_id, test_group_name, wa_message_id, text}`.
- `POST /api/cases` tidak berubah kontraknya (refactor internal saja — teks sekarang dirender via helper bersama yang dipakai ketiga endpoint, jadi preview/test/kirim selalu identik).
- Alur FE: tombol test → `test-send`, tombol kirim → `POST /api/cases`.

### v1.13 (8 September 2026) — hardening keamanan pasca-audit produksi
Perubahan keamanan dari hasil verifikasi produksi 8 Sep 2026 (lihat `docs/production-runbook.md`). **Tidak ada perubahan kontrak endpoint untuk frontend.**
- **`POST /webhooks/waha` kini terproteksi**: jika env `WAHA_WEBHOOK_SECRET` ter-set, request wajib membawa secret via header `X-Webhook-Secret` atau query `?token=...`. Tanpa/salah → `401`. WAHA diarahkan ke URL webhook yang menyertakan token. (Kalau env kosong → perilaku lama, dengan log WARNING.)
- **`GET /api/media/proxy` anti-SSRF**: hanya URL yang menunjuk TEPAT ke host:port `WAHA_URL` yang diproxy. Host lain (termasuk `waha.attacker.com`) → `400`. Redirect keluar host tidak diikuti.
- **Soft-delete kini dihormati webhook**: reply WA ke case yang sudah di-delete tidak lagi memunculkan update/ubah status.
- **Login `/api/auth/access-code` di-rate-limit**: maks 5 percobaan/menit/IP (env `LOGIN_RATE_LIMIT`), lebih → `429`.
- Deploy: port app hanya bind `127.0.0.1` (akses publik eksklusif via nginx/TLS).

### v1.12 (7 September 2026) — perbaikan error switcher grup
Penyebab: frontend mengirim `group_id` grup yang sedang di-inaktifkan admin → dapat `404 "Group not found or inactive"` yang tidak menyebut apa-apa, sementara `GET /api/groups` justru menyodorkan grup nonaktif itu ke dropdown.
- **`GET /api/groups` kini SAFE BY DEFAULT**: hanya grup **aktif** dan **bukan default**. Param `is_active` **diganti** `include_inactive` (admin). Call lama `?is_active=true` tetap aman (param tak dikenal diabaikan → hasil tetap aktif-saja).
- **`POST /api/cases`, `group_id` divalidasi `>= 1`** → `0`/negatif → `422` dengan pesan Pydantic, tanpa menyentuh DB.
- ID tidak dikenal: `404` → **`422`** dengan nilai yang ditolak: `group_id 999 tidak dikenal — pakai ID dari GET /api/groups atau hilangkan field untuk mengirim ke grup default`.
- Grup ada tapi nonaktif: `404` → **`409`** dengan nama grupnya: `grup 'Escalation OPERA - CX100' sedang dinonaktifkan admin — pilih grup lain atau minta grup ini diaktifkan kembali`.
- Frontend: saat tidak memilih grup, **hilangkan `group_id`** (jangan kirim `0`).
- Backend versi 1.10.0. Tidak ada perubahan skema DB.

### v1.11 (7 September 2026)
- **Endpoint baru `GET /api/waha/groups`** (admin): daftar semua grup yang bot ikuti, diambil langsung dari WAHA — tidak perlu salin `chat_id` manual dari UI/CLI WAHA.
- Response berisi `chat_id`, `name`, `registered`, `group_id`, `is_active`, `is_default`; yang belum terdaftar diurutkan paling atas. Query: `limit` (default 500), `search` (substring nama).
- Read-only: tidak mendaftarkan grup apa pun. `502` bila WAHA error/tidak terjangkau.
- Backend versi 1.9.0. Tidak ada perubahan skema DB.

### v1.10 (7 September 2026)
- **Grup default (fallback):** kolom `wa_groups.is_default` (maks 1 baris, partial unique index di `schema-migration-group-default.sql`).
- `POST /api/cases`: `group_id` kini **opsional** — tidak dipilih → dikirim ke grup default (biasanya grup test development). Tanpa default terkonfigurasi → `400` (sebelumnya `422` karena wajib).
- `GET /api/groups`: **menyaring keluar** grup default secara otomatis; pakai `?include_default=true` untuk admin. Response menambah field `is_default`.
- `POST/PUT /api/groups`: dukung `is_default`; set `true` otomatis melepas default lama.
- Frontend: switcher tetap opsional — tidak perlu pre-select, dan grup test tidak terlihat oleh user.

### v1.9 (4 September 2026)
- **Multi-grup WA (switcher Grup A/B):** tabel `wa_groups` + CRUD admin (`GET/POST/PUT/DELETE /api/groups`).
- `POST /api/cases` kini **wajib** `group_id` (ID dari `GET /api/groups`); tanpa → `422`; grup tidak valid/nonaktif → `404`. Response menyertakan `group_id` & `group_name`.
- `GET /api/cases` & `GET /api/reminders/pending`: filter baru `group_id`; row menyertakan `group_name`.
- `POST /api/crawl`: param `group_id` (kosongkan → crawl semua grup aktif), response + breakdown `groups[]`.
- Tracking webhook per-grup: pesan dari grup tak terdaftar diabaikan; case hanya di-link dari pesan di grupnya sendiri (anti false-positive lintas grup); LLM fallback hanya melihat case open di grup tsb.
- Env `WA_GROUP_ID` = seed awal "Grup A" saat tabel kosong (bukan lagi satu-satunya target kirim).
- Migrasi schema: `schema-multi-group.sql` (tabel `wa_groups` + `cases.group_id`).

### v1.8 (2 September 2026)
- **Media download fix**: Webhook handler sekarang download media ke Docker volume (`/app/media/`) daripada cuma simpan proxy URL. File media persist meski container restart. Fix untuk error "media not found" yang muncul karena proxy URL ke WAHA expired/inaccessible.

### v1.7 (2 September 2026)
- **Access Code Auth**: Endpoint baru `POST /api/auth/access-code` untuk login dengan kode akses. Return JWT token untuk akses frontend. Access codes di-config via env var `ACCESS_CODES` (comma-separated). JWT expiry via `JWT_EXPIRY_HOURS` (default 24 jam).
- **Frontend gate**: Frontend harus login dulu sebelum bisa akses dashboard. API key auth tetap berjalan untuk backend API.
- **Nginx update**: Frontend dihapus dari VPS (sudah deploy di tempat lain). Semua request ke `api.stc.syfa.site` langsung ke backend.
- **Delete case (soft delete)**: Endpoint baru `DELETE /api/cases/{id}` untuk soft delete case. Case ditandai dengan `deleted_at` timestamp. Query `GET /api/cases` default hanya menampilkan case aktif (belum di-delete). Gunakan `?include_deleted=true` untuk melihat semua case.

### v1.6 (27 Agustus 2026)
- **Media download & storage**: Backend download media dari WAHA saat webhook masuk, simpan ke Docker volume (`/app/media/`). Serve via `GET /api/media/file/{filename}`. Media persist meski container restart. Legacy proxy endpoint masih ada sebagai fallback.
- **Media di timeline**: Setiap pesan di `GET /api/cases/{id}` sekarang include `media_url` (local file URL) dan `media_type` (MIME type). Mendukung image + caption dan image-only replies.
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
- **`link_evidence`**: Array of object `{label, url}` (sejak v1.15). Bisa multiple evidence per case. `url` bisa string atau array (banyak link satu label). `label` opsional — kosong render link polos. Render bernomor: `1. Label:` + link di bawahnya. Backward compatible: string URL lama tetap diterima.
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
