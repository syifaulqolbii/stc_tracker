# Design: Metrik Resolution Time Solver (v1.34)

**Tanggal:** 2026-10-09 · **Status:** Disetujui untuk implementasi
**Endpoint baru:** `GET /api/metrics/solver-resolution`
**File terdampak:** `main-v1-1.py`, `tests/test_api.py`, `API-contract-frontend.md`,
`docs/panduan-frontend-metrics-solver-resolution.md`, `reports/resolution-time-solver.sql`

---

## 1. Tujuan

Pasangan dari `GET /api/metrics/solver-response` (v1.32). Kalau response time
mengukur "seberapa cepat solver **pertama kali** membalas", resolution time
mengukur "**berapa lama total** sampai case benar-benar selesai".

**Definisi:**

| Titik | Sumber | Keterangan |
|---|---|---|
| **t0** | `cases.created_at` | Ticket dikirim ke grup solver (pesan root bot) |
| **t1** | `MAX(wa_messages.created_at)` di mana `from_me = false` dan `case_id` ter-link | **Balasan solver TERAKHIR** — isi pesan diabaikan |
| **Syarat** | `cases.status = 'done'` | Hanya case berstatus done |

`resolution_jam = (t1 - t0) / 3600`. Satuan **jam** (bukan menit) karena
resolution time umumnya hitungan jam sampai hari.

**Filter case:** `cases.deleted_at IS NULL AND t1 >= t0` (buang latensi negatif —
pola sama dengan report response time).

---

## 2. Keputusan Desain (hasil klarifikasi dengan user)

| Pertanyaan | Keputusan |
|---|---|
| Momen "done" ditentukan dari mana? | **Semua case berstatus `done`** — baik yang di-set otomatis dari keyword solver maupun yang di-update manual admin di web |
| Titik akhir waktunya? | **Balasan solver terakhir**, BUKAN timestamp update manual admin |
| Bucket distribusi | **Jam**: `<1 jam`, `1–6 jam`, `6–24 jam`, `>24 jam` |
| Case `done` tanpa balasan solver sama sekali | **Tidak dihitung** di summary (tidak punya titik akhir) → masuk watchlist `unresolved` |

**Alasan keputusan "t1 = balasan terakhir":** alur nyata di lapangan — solver
membalas *"bisa dicek lagi rekan"* (tidak mengandung keyword done, jadi status
tetap `in_progress`), lalu admin menutup case manual di web. Yang dihitung
adalah waktu sampai *"bisa dicek lagi rekan"* (t1), bukan waktu admin klik
tombol done. Ini yang mencerminkan durasi kerja solver sebenarnya.

Konsekuensi: kasus **normal** (solver menulis "done mas" sebagai balasan
terakhir) tetap menghasilkan t1 = pesan itu sendiri — tidak ada perbedaan
perilaku. Yang berubah hanya case yang ditutup manual.

---

## 3. Kontrak Response

```jsonc
{
  "filters": { "regional_id": 7, "solver": null, "group_id": null,
               "date_from": null, "date_to": null },
  "summary": { "total_cases": 25, "avg_jam": 5.2, "min_jam": 0.3,
               "max_jam": 48.0, "median_jam": 3.5 },
  "distribution": [
    { "bucket": "lt_1h",   "jumlah_case": 4,  "persen": 16.0 },
    { "bucket": "b_1_6h",  "jumlah_case": 10, "persen": 40.0 },
    { "bucket": "b_6_24h", "jumlah_case": 8,  "persen": 32.0 },
    { "bucket": "gt_24h",  "jumlah_case": 3,  "persen": 12.0 }
  ],
  "per_solver": [
    { "solver": "Alif", "jumlah_case": 3, "avg_jam": 2.1,
      "min_jam": 0.5, "max_jam": 5.2, "median_jam": 1.9 }
    // sort by median_jam ASC
  ],
  "unresolved": {
    "count": 4,
    "cases": [ { "id": 33, "case_code": "INC000024135513", "status": "open",
                 "group_name": "Escalation OPERA - CX100",
                 "regional_name": "Jateng DIY",
                 "created_at": "...", "menit_sejak_kirim": 27276.7 } ]
  },
  "cases": [ { "id": 89, "case_code": "INC000024304451", "status": "done",
               "group_name": "...", "regional_name": "...",
               "kirim_case_at": "...", "solver": "IT - SMOPS",
               "balasan_terakhir_at": "...", "resolution_jam": 2.76 } ]
}
```

`summary` semua `null` bila tidak ada row (konsisten dengan solver-response).
`distribution` = `[]` bila kosong.

### Bucket

| Kode | Rentang |
|---|---|
| `lt_1h` | < 1 jam |
| `b_1_6h` | 1 – 6 jam |
| `b_6_24h` | 6 – 24 jam |
| `gt_24h` | > 24 jam |

### `unresolved` — isi watchlist

Dua kelompok, keduanya **tidak** ikut summary:

1. Case belum berstatus `done` sama sekali (open / in_progress).
2. Case berstatus `done` tetapi **tidak punya satupun balasan solver** → t1 tidak ada.

Bedakan di FE lewat field `status`: `"done"` di watchlist = kasus anomali
(ditutup tanpa jejak balasan solver) → perlu perhatian admin.

---

## 4. Query Inti

```sql
WITH last_reply AS (
    SELECT DISTINCT ON (case_id)
           case_id, author, author_name, created_at AS last_reply_at
    FROM wa_messages
    WHERE from_me = false AND case_id IS NOT NULL
    ORDER BY case_id, created_at DESC      -- TERAKHIR, bukan pertama
)
SELECT c.id, c.case_code, c.status, g.name AS group_name,
       r.name AS regional_name,
       c.created_at AS kirim_case_at,
       COALESCE(lr.author_name, lr.author) AS solver,
       lr.last_reply_at AS balasan_terakhir_at,
       EXTRACT(EPOCH FROM (lr.last_reply_at - c.created_at)) / 3600.0
           AS resolution_jam
FROM cases c
JOIN last_reply lr ON lr.case_id = c.id
LEFT JOIN wa_groups g ON g.id = c.group_id
LEFT JOIN regionals r ON c.regional_id = r.id
WHERE c.deleted_at IS NULL
  AND c.status = 'done'
  AND lr.last_reply_at >= c.created_at
ORDER BY c.created_at ASC;
```

Query watchlist:

```sql
SELECT c.id, c.case_code, c.status, g.name, r.name, c.created_at,
       EXTRACT(EPOCH FROM (now() - c.created_at)) / 60.0 AS menit_sejak_kirim
FROM cases c
LEFT JOIN wa_groups g ON g.id = c.group_id
LEFT JOIN regionals r ON c.regional_id = r.id
WHERE c.deleted_at IS NULL
  AND ( c.status != 'done'
        OR NOT EXISTS (SELECT 1 FROM wa_messages w
                       WHERE w.case_id = c.id AND w.from_me = false) )
ORDER BY c.created_at ASC;
```

Agregat (avg / median / distribusi / per-solver) dihitung **di Python** —
pola sama `_summarize_response_rows`, supaya median akurat & mudah dites tanpa DB.

---

## 5. Filter

Identik dengan `/api/metrics/solver-response`, digabung AND:

| Param | Tipe | Perilaku |
|---|---|---|
| `regional_id` | int | `c.regional_id = %s` |
| `solver` | string | `COALESCE(lr.author_name, lr.author) ILIKE '%x%'` |
| `group_id` | int | `c.group_id = %s` |
| `date_from` | YYYY-MM-DD | `c.created_at >= %s` |
| `date_to` | YYYY-MM-DD | `c.created_at < date_to + 1 hari` (inklusif) |

- Format tanggal salah / `date_from > date_to` → **422** `{detail: string}`.
- Filter `solver` **di-skip** untuk query `unresolved` (case tanpa balasan
  solver tidak punya nama solver) — konsisten dengan perilaku `unanswered`.
- Filter diterapkan pada `cases`, bukan pada `wa_messages`.

---

## 6. Unit & Boundary

| Unit | Tanggung jawab | Interface |
|---|---|---|
| `_solver_resolution_metrics(cur, ...)` | Validasi tanggal, bangun & jalankan 2 query, return `(rows, unresolved)` | `cur` psycopg cursor + filter |
| `_summarize_resolution_rows(rows, unresolved)` | Agregat murni Python: summary, bucket, per-solver | `list[dict]` → `dict` |
| `solver_resolution_metrics(...)` | Route FastAPI: echo filter + rakit response | HTTP |

Dipisah begini supaya agregat bisa dites tanpa DB (mock cursor hanya untuk
query) dan ikut pola yang sudah terbukti di v1.32.

---

## 7. Testing (tests/test_api.py → `TestSolverResolutionMetrics`)

| Test | Yang diverifikasi |
|---|---|
| `test_summary_and_distribution` | 3 case done (2.5j, 8j, 100j) → avg/median/bucket benar + 1 unresolved |
| `test_per_solver_median` | Median per solver + sort by median ASC |
| `test_filters_passed_to_sql` | Argumen query 1 & 2 persis, `date_to+1d`, solver di-skip di query 2 |
| `test_bad_date_format_422` | `date_from=01-09-2026` → 422, pesan menyebut `YYYY-MM-DD` |
| `test_date_from_after_date_to_422` | 422 dengan pesan tepat |
| `test_empty_result` | Tanpa data: summary nol, `median_jam` null, `distribution`/`per_solver` kosong |
| `test_resolution_uses_last_reply` | Guard regresi: helper memakai `ORDER BY ... created_at DESC` (bukan ASC) |

Plus: seluruh suite existing harus tetap lulus (endpoint lama tidak disentuh).

---

## 8. Pranala Luar & Dokumentasi

- `API-contract-frontend.md`: entri changelog **v1.34** + versi app `1.33.0` → `1.34.0`.

  > Catatan penomoran: commit health-alert Telegram sudah memakai label v1.33
  > di runbook dan pesan commit, tetapi `version=` di kode dan changelog belum
  > dinaikkan. Fitur ini memakai **v1.34** agar tidak menabrak label tersebut.

- `docs/panduan-frontend-metrics-solver-resolution.md`: panduan FE — fetch,
  mapping bucket → label Indonesia, format jam/menit, layout KPI + tabel +
  watchlist, dan catatan akurasi data crawl (timestamp = waktu crawl).

---

## 9. Risiko & Mitigasi

| Risiko | Mitigasi |
|---|---|
| Data crawl punya timestamp waktu-crawl → resolution time palsu besar | Dokumentasikan di panduan FE; sama seperti keterbatasan response time v1.32 |
| Balasan terakhir bukan pesan "penyelesaian" (mis. "terimakasih") pada case yang ditutup manual | Diterima sebagai trade-off sesuai keputusan user; `max_jam` disembunyikan dari KPI, median jadi acuan |
| Case `done` tanpa balasan solver → tidak terhitung | Muncul di `unresolved` dengan `status: done` sebagai action item admin |
| Nomor versi bentrok dengan fitur lain | Pakai v1.34, catat alasannya di changelog |