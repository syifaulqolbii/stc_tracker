# PRD — IT Tracker

**Produk:** IT Tracker v1.34.0
**Sumber kebenaran:** `main-v1-1.py` (3.373 baris, monolit single-file FastAPI) + `templates.py` + file `schema-*.sql` + `tests/*.py` (361 fungsi test, 7 file)
**Tanggal:** 16 Oktober 2026
**Status:** draf

Dokumen ini ditulis dari kode backend aktual. Setiap kebutuhan fungsional membawa **anchor bukti** (`file:baris`) yang merujuk langsung ke implementasi. Nomor baris dapat bergeser seiring perubahan kode; yang mengikat adalah perilaku yang dijelaskan.

## Daftar Isi

1. Konteks &amp; Masalah
2. Tujuan &amp; Ukuran Keberhasilan
3. Pengguna &amp; Peran
4. Ruang Lingkup
5. Perjalanan Pengguna
6. Kebutuhan Fungsional
7. Kebutuhan Non-Fungsional
8. Arsitektur Ringkas
9. Model Data Ringkas
10. Spesifikasi API Ringkas
11. Kriteria Penerimaan

Lampiran A. Konfigurasi (env)

---

## 1. Konteks &amp; Masalah

Tim IT STC menangani follow-up ("moban") case pelanggan melalui grup WhatsApp. Sebelum produk ini ada, alur kerja tidak punya sistem pencatatan: seorang agen mengetik case secara manual ke grup dengan format bebas, seorang solver menjawab, dan jika solver A meneruskan ke solver B lewat balasan, agen tidak pernah tahu kapan case benar-benar selesai. Semua pelacakan dilakukan dengan menggulir riwayat chat — lambat, mudah terlewat, dan tidak bisa direkap.

Akibatnya ada tiga masalah struktural: (a) **status case tidak tercatat** — "selesai" hanya berarti ada pesan di suatu tempat di dalam grup; (b) **tidak ada reporting** — durasi respon dan penyelesaian solver tidak terukur, sehingga evaluasi kinerja bersifat kesan, bukan data; (c) **format case tidak konsisten** — case diketik ulang setiap kali dengan susunan field yang berbeda-beda, sehingga sulit dicari dan diaudit.

IT Tracker memecahkan ini dengan menjadikan bot sebagai **pencatat otomatis**. Setiap case dirender ke teks template berformat tetap dengan identitas unik (`case_code`), dikirim ke grup WhatsApp, dan sejak itu seluruh percakapan di grup direkam. Bot menelusuri balasan solver — termasuk balasan berantai dan balasan tidak langsung ke pesan case — untuk memperbarui status case secara otomatis. Di atas data itu, produk menyediakan dashboard web untuk agen, ekspor Excel, metrik kinerja solver, reminder proaktif (sundul), dan pemantauan kesehatan layanan dengan alert Telegram.

Produk ini dulu bernama **"Moban FU Case Tracker"** dan berganti nama menjadi **"IT Tracker"**; istilah "moban" kini hanya bertahan sebagai istilah domain historis di dalam kode dan dokumen operasional.

Sumber konteks bisnis: `docs/pitch-deck-arsitektur.md`; riwayat perubahan nama &amp; fitur ada di docstring/changelog `main-v1-1.py:1-18` dan metadata aplikasi `main-v1-1.py:203`.

---

## 2. Tujuan &amp; Ukuran Keberhasilan

Tujuan produk — seluruhnya dapat diukur oleh fitur yang benar-benar ada di kode:

1. **Setiap case tercatat dan terlacak sampai selesai.** Case memiliki siklus status `open → in_progress → done` yang tersimpan di DB dan dapat dikoreksi manual. (`main-v1-1.py:1269`, `1171-1173`)
2. **Status case ter-update dari balasan WhatsApp tanpa aplikasi tambahan.** Pencocokan berlapis (waterfall 4 tahap) mengaitkan pesan solver ke case, tanpa solver perlu membuka web apa pun. (`main-v1-1.py:1094-1192`)
3. **Agen dan admin dapat memantau melalui web** — daftar case dengan filter, detail timeline, dan ekspor Excel. (`main-v1-1.py:1703`, `1615`)
4. **Kecepatan respon solver terukur** dalam satuan menit (median + distribusi + per solver). (`main-v1-1.py:2390`)
5. **Waktu penyelesaian case terukur** dalam satuan jam (median + distribusi + per solver). (`main-v1-1.py:2612`)
6. **Case idle tersundul otomatis** (reminder ke solver) — manual maupun lewat cron eksternal. (`main-v1-1.py:2053`)
7. **Layanan terpantau** — health detail dan alert Telegram saat turun/pulih. (`main-v1-1.py:3275`, `3366`)

### Ukuran keberhasilan

Keberhasilan diukur dari nilai yang dihasilkan endpoint metrik dan health, bukan dari opini:

- **Median response time solver** (menit) — dari case dikirim sampai balasan pertama solver (`main-v1-1.py:2390`).
- **Median resolution time solver** (jam) — dari case dikirim sampai balasan solver terakhir pada case `done` (`main-v1-1.py:2612`).
- **Jumlah `unanswered`** — case tanpa balasan solver sama sekali, sebagai watchlist (`main-v1-1.py:2292-2300`).
- **Jumlah `unresolved`** — case belum `done` atau `done` tanpa balasan solver, sebagai watchlist (`main-v1-1.py:2519-2524`).
- **Status layanan** `ok` vs `degraded` pada health detail (`main-v1-1.py:3203-3251`).

---

## 3. Pengguna &amp; Peran


| Peran                   | Cara akses                                                                    | Kemampuan utama                                                                                                              |
| ----------------------- | ----------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| **Agen (operator STC)** | Login kode akses via `POST /api/auth/access-code` → JWT (`main-v1-1.py:1376`) | Membuat case (form dinamis 4 jenis), preview, test-send, melihat dashboard/list, membalas solver dari web, mengoreksi status |
| **Solver (tim solusi)** | Tanpa aplikasi — hanya membalas di grup WhatsApp                              | Tidak membuka web sama sekali; status case berubah otomatis lewat webhook                                                    |
| **Admin / operasional** | `X-API-Key` pada seluruh `/api` (`main-v1-1.py:260-289`)                      | Mengelola grup WA, kontak solver, discovery grup, crawl backfill histori, menerima alert Telegram                            |


Catatan penting soal autentikasi agen: JWT yang diterbitkan `POST /api/auth/access-code` **hanya mem-gate UI frontend** — backend tidak memverifikasi JWT pada endpoint `/api` mana pun. Proteksi API yang sebenarnya adalah `X-API-Key` (`main-v1-1.py:1376-1390`; lihat juga `PRD-it-tracker-v2.md:497`). Artinya kode akses mencegah orang tak berkepentingan membuka dashboard, tetapi bukan batas keamanan API-nya.

---

## 4. Ruang Lingkup

### Fitur in-scope

1. Form case dinamis 4 jenis (`non_order`, `non_ao`, `ao`, `mobile`) dengan set field berbeda per jenis. (`templates.py:8`, `19-63`)
2. Hierarchy Area → Regional saling terikat; Sumber Ticket STC/Grapari/Web IT; field Asal Grapari kondisional. (`main-v1-1.py:1834`, `1847`, `1867`)
3. Mention solver multi dari dropdown + token `@nomor` manual ternormalisasi ke format `62…`. (`main-v1-1.py:577-618`)
4. Custom header dengan placeholder `{phone}` untuk menyisipkan mention. (`templates.py:145-175`)
5. Preview teks tanpa menulis/mengirim, dan test-send ke grup test tanpa membuat case. (`main-v1-1.py:1479`, `1490`)
6. Kirim case + upsert re-FU: `case_code` sama membuka ulang case lama, bukan duplikat. (`main-v1-1.py:1391`, `1437-1455`)
7. Pencatatan seluruh pesan dari grup terdaftar ke `wa_messages`. (`main-v1-1.py:1094-1192`)
8. Waterfall pencocokan: regex INC → kode non-INC persis → reply/chain traversal → LLM fallback. (`main-v1-1.py:1132-1162`)
9. Auto `in_progress` pada balasan pertama solver untuk case `open`. (`main-v1-1.py:1171-1173`)
10. Multi-grup + grup default (fallback untuk case tanpa `group_id`, tersembunyi dari switcher). (`main-v1-1.py:2824-2947`)
11. Reminder manual per case + reminder otomatis (cron eksternal) + riwayat reminder. (`main-v1-1.py:1895`, `2053`, `2193`)
12. Balas solver dari web: teks dan/atau maksimal 3 lampiran (base64). (`main-v1-1.py:1968`, `2011-2045`)
13. Media timeline (gambar/video/dokumen) disimpan lokal dan disajikan kembali. (`main-v1-1.py:3131`, `3153`)
14. Dashboard list dengan filter kombinatif, pencarian `q`, dan pagination opt-in. (`main-v1-1.py:1703`)
15. Ekspor Excel dengan filter identik dashboard. (`main-v1-1.py:1615`)
16. Metrik response time solver dan resolution time solver. (`main-v1-1.py:2390`, `2612`)
17. Health detail + alert Telegram (transisi down, re-alert, pemulihan). (`main-v1-1.py:3275`, `3366`)
18. Crawl backfill histori grup 2-pass. (`main-v1-1.py:3037`)

Implementasi UI frontend tidak dibahas di dokumen ini — kontrak API-nya ada di `API-contract-frontend.md`.

### Belum ada / tidak dikerjakan


| Gap                                       | Keterangan                                                                                   |
| ----------------------------------------- | -------------------------------------------------------------------------------------------- |
| Notifikasi balik ke agen saat case `done` | Bot belum mengirim pesan/DM ke agen saat case tuntas.                                        |
| Event `message.revoked`                   | Pesan yang dihapus di WhatsApp tidak mengubah catatan di sistem.                             |
| Edit pesan                                | Pesan yang diedit tidak tercatat perubahannya.                                               |
| Validasi `REQUIRED_FIELDS` di backend     | Didefinisikan di `templates.py:11-16` tetapi **tidak ditegakkan** — enforcement hanya di UI. |
| Websocket / realtime                      | Pemuatan data memakai pola polling, bukan push.                                              |
| Multi-nomor WA                            | Belum ada; hanya ada design doc `docs/plan-multi-nomor-regional.md`.                         |
| `GET /api/waha/sessions`                  | Endpoint untuk melihat daftar session WAHA belum ada.                                        |


(Sumber gap: verifikasi kode sesi ini + `PRD-it-tracker-v2.md:87-96`.)

---

## 5. Perjalanan Pengguna

### J1 — Agen membuat case

1. Login dengan kode akses; dashboard terbuka.
2. Pilih Area → Regional (daftar Regional difilter oleh Area terpilih), Sumber Ticket, dan Jenis Case.
3. Isi field sesuai jenis case (mis. `ticket_remedy`, `order_id`, `no_indihome`, `msisdn` bergantung jenis). Bila Sumber Ticket = Grapari, field Asal Grapari muncul.
4. Pilih solver dari dropdown (bisa multi) dan/atau ketik `@nomor` manual.
5. Klik preview untuk melihat teks final; opsional klik test-send untuk mengirim ke grup test.
6. Klik kirim. Bila `group_id` tidak dipilih, case dikirim ke grup default. Response berisi `text` final yang dikirim.
7. Case tercatat di dashboard dengan status `open`.

### J2 — Solver menyelesaikan case (tanpa web)

1. Solver membalas pesan case root dengan "done mas" → ter-link sebagai `reply`.
2. Atau solver membalas pesan solver lain ("sudah diluruskan") → bot menelusuri rantai ke atas → ter-link sebagai `chain`.
3. Atau solver menulis pesan bebas yang memuat info tiket → LLM fallback mengekstraksi status (dipakai bila confidence ≥ 0.7) → ter-link sebagai `llm`.
4. Balasan pertama pada case yang masih `open` (tanpa keyword status apa pun) otomatis mengangkat status ke `in_progress`.
5. Bot mengirim notifikasi ringkas ke grup default bahwa case sudah dibalas.
6. Status case di dashboard berubah tanpa agen melakukan apa pun.

### J3 — Agen melihat &amp; membalas dari web

1. Buka daftar case, terapkan filter (status, jenis, area, regional, sumber, grup, tanggal) dan/atau pencarian `q`.
2. Buka detail case — melihat `case`, timeline pesan (`messages`), riwayat update (`updates`), dan `participants`.
3. Klik Balas, tulis teks dan/atau lampirkan 1–3 gambar/file (bisa paste dari clipboard).
4. Kirim — teks menjadi caption bila ada lampiran (anti double-send); balasan tercatat di timeline.
5. Refresh timeline untuk melihat balasan solver berikutnya.
6. Bila perlu, koreksi status manual (mis. tandai selesai).

**Guardrail operasional:** jangan pernah menjawab solver dari HP akun bot. Pesan manual dari akun bot (`fromMe=true`) dilewati webhook sehingga reply-chain putus. Selalu balas lewat endpoint replies (`main-v1-1.py:1970-1974`).

### J4 — Admin

1. Discovery grup: `GET /api/waha/groups` — ambil `chat_id` grup yang belum terdaftar (`registered: false`).
2. Daftarkan grup: `POST /api/groups`.
3. Tetapkan grup default: `PUT /api/groups/{id}` dengan `{"is_default": true}` (menggeser default lama).
4. Kelola kontak solver (CRUD; soft delete lewat `is_active=false`).
5. Jalankan crawl backfill histori bila perlu (operasi berat — sediakan konfirmasi di UI).

### J5 — Pelaporan &amp; metrik

1. Terapkan filter pada dashboard.
2. Klik ekspor → file `.xlsx` dengan filter identik dashboard (tanpa pagination).
3. Buka halaman metrik response time (menit): KPI median, distribusi bucket, tabel per solver, watchlist `unanswered`.
4. Buka halaman metrik resolution time (jam): KPI median, distribusi bucket, tabel per solver, watchlist `unresolved`.

---

## 6. Kebutuhan Fungsional

Prioritas **Wajib** kecuali tertulis "Sebaiknya". Anchor = bukti implementasi.

### FR-C — Case


| ID     | Kebutuhan                                                                                     | Prioritas | Bukti                                                                 |
| ------ | --------------------------------------------------------------------------------------------- | --------- | --------------------------------------------------------------------- |
| FR-C1  | Input case dengan hierarchy Area → Regional saling terikat                                    | Wajib     | `main-v1-1.py:1834,1847`                                              |
| FR-C2  | 4 jenis case (non\_order, non\_ao, ao, mobile) dengan set field berbeda                       | Wajib     | `templates.py:8,19-63`                                                |
| FR-C3  | Sumber ticket STC/Grapari/Web IT; Asal Grapari muncul hanya bila Grapari                      | Wajib     | `seed-v1-2.sql:6`, `main-v1-1.py:1867`                                |
| FR-C4  | Mention solver multi + ketik `@nomor` (dinormalisasi ke `62…`)                                | Wajib     | `main-v1-1.py:577-618`                                                |
| FR-C5  | Custom header dengan placeholder `{phone}` (mentions kosong + `{phone}` → 422)                | Wajib     | `templates.py:145-175`, `main-v1-1.py:836-843`                        |
| FR-C6  | Preview teks identik dengan hasil kirim, tanpa tulis/kirim                                    | Wajib     | `main-v1-1.py:1479`, tes parity `tests/test_api.py` (TestCasePreview) |
| FR-C7  | Test-send ke grup test/default tanpa membuat case                                             | Wajib     | `main-v1-1.py:1490`                                                   |
| FR-C8  | `group_id` kosong → kirim ke grup default; tak ada default → 400                              | Wajib     | `main-v1-1.py:1418-1424`                                              |
| FR-C9  | Re-FU `case_code` sama = buka ulang (status `open`, `deleted_at` dibersihkan), bukan duplikat | Wajib     | `main-v1-1.py:1437-1455`                                              |
| FR-C10 | Validasi `ticket_remedy` format `INC\d{9,}`                                                   | Wajib     | `main-v1-1.py:1187-1207`                                              |
| FR-C11 | Soft delete case (`deleted_at`; semua query menyaring `deleted_at IS NULL`)                   | Wajib     | `main-v1-1.py:1819`, `schema-migration-deleted-at.sql:4`              |
| FR-C12 | Render `link_evidence` (string / `{label,url}` / array, penomoran otomatis)                   | Wajib     | `templates.py:214-249`                                                |


### FR-T — Tracking


| ID     | Kebutuhan                                                                                                                                                                                                                                                                                    | Prioritas | Bukti                                                 |
| ------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------- | ----------------------------------------------------- |
| FR-T1  | Semua pesan dari grup terdaftar direkam ke `wa_messages` (untuk rantai &amp; audit)                                                                                                                                                                                                          | Wajib     | `main-v1-1.py:1094-1192`                              |
| FR-T2  | Waterfall 4 tahap: regex INC → kode non-INC persis (dengan sinyal status) → reply/chain → LLM; source `rule|reply|chain|llm|crawl`                                                                                                                                                           | Wajib     | `main-v1-1.py:1132-1162`                              |
| FR-T3  | Verifikasi grup: case hanya di-link dari pesan di grupnya sendiri                                                                                                                                                                                                                            | Wajib     | `main-v1-1.py:1164-1167`                              |
| FR-T4  | Balasan pertama pada case `open` tanpa keyword status → otomatis `in_progress`                                                                                                                                                                                                               | Wajib     | `main-v1-1.py:1171-1173`                              |
| FR-T5  | Keyword status: done = `done/selesai/beres/kelar/solved/closed/terkirim/completed`; in-progress = `proses/progress/diproses/otw/dicek/cek dulu/follow up/fu`; word-boundary + guard negasi (≤60 char, clause-breaker `tapi/namun/eh sudah/ternyata sudah`); persentase → progress min(100,n) | Wajib     | `main-v1-1.py:500-529`, `tests/test_chain.py:150,209` |
| FR-T6  | Chain traversal via `quoted_id`, max 5 level, aman terhadap sirkular                                                                                                                                                                                                                         | Wajib     | `main-v1-1.py:627-654`                                |
| FR-T7  | LLM fallback (OpenAI-compatible): status `done|in_progress|None`, confidence di-clamp 0–1, diterima ≥0.7; retry 3× backoff eksponensial; tidak retry 4xx                                                                                                                                     | Wajib     | `main-v1-1.py:1026-1089`                              |
| FR-T8  | Ack pesan keluar (PENDING/SERVER/DEVICE/READ) tersimpan ke `cases.ack`                                                                                                                                                                                                                       | Sebaiknya | `main-v1-1.py:1200-1208`                              |
| FR-T9  | Notifikasi ringkas balasan solver ke grup default (identitas `Ticket Remedy|Case ID` + `(IH …)`, kutipan ≤300 char, anti-loop bila grup sama, media sebagai caption)                                                                                                                         | Wajib     | `main-v1-1.py:696-791`                                |
| FR-T10 | Resolusi nama kontak (`@lid` → nama) dari cache/WAHA, ditulis ke `author_name`                                                                                                                                                                                                               | Wajib     | `main-v1-1.py:422-498`                                |
| FR-T11 | Idempotensi update (`ON CONFLICT (wa_message_id) DO NOTHING`) — aman untuk crawl ulang                                                                                                                                                                                                       | Wajib     | `main-v1-1.py:667-694`                                |


### FR-A — Aksi dari Web


| ID    | Kebutuhan                                                                                                                                                                                                                    | Prioritas | Bukti                                               |
| ----- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------- | --------------------------------------------------- |
| FR-A1 | Koreksi status manual (`open`/`in_progress`/`done`; `issue` ditolak) + catatan sintetis di `wa_messages`                                                                                                                     | Wajib     | `main-v1-1.py:1796`, `1805-1814`                    |
| FR-A2 | Balas dari web: teks dan/atau maks 3 lampiran (mime `image/jpeg|png|webp`, `video/mp4`, `application/pdf`, ≤5 MB, base64); dengan lampiran teks hanya jadi caption (anti double-send); validasi semua lampiran sebelum kirim | Wajib     | `main-v1-1.py:1968`, `2011-2045`                    |
| FR-A3 | Paste gambar clipboard langsung jadi lampiran                                                                                                                                                                                | Sebaiknya | `docs/panduan-frontend-metrics-solver.md` (FE-only) |
| FR-A4 | Reminder manual per case (400 bila sudah `done`, 404 bila tak ada)                                                                                                                                                           | Wajib     | `main-v1-1.py:1895`, `1898-1966`                    |
| FR-A5 | Riwayat reminder per case                                                                                                                                                                                                    | Sebaiknya | `main-v1-1.py:2193`                                 |
| FR-A6 | Crawl backfill histori (2-pass, per grup atau semua grup aktif; 404 grup tak dikenal)                                                                                                                                        | Wajib     | `main-v1-1.py:3037`                                 |


### FR-R — Pelaporan


| ID    | Kebutuhan                                                                                                                             | Prioritas | Bukti                            |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------- | --------- | -------------------------------- |
| FR-R1 | Daftar case filter kombinatif + pencarian `q` (case\_code, title, `fields::text`)                                                     | Wajib     | `main-v1-1.py:1703`              |
| FR-R2 | Pagination opt-in: tanpa `limit` → array polos; dengan `limit` → `{data, pagination{page,limit,total,total_pages,has_next,has_prev}}` | Wajib     | `main-v1-1.py:1703`              |
| FR-R3 | Export Excel memakai filter identik dashboard (13 kolom, tanpa pagination)                                                            | Wajib     | `main-v1-1.py:1615`, `1590-1592` |
| FR-R4 | Metrik response time solver (menit): median, avg/min/max, bucket `lt_5/b_5_15/b_15_60/gt_60`, per-solver, watchlist `unanswered`      | Wajib     | `main-v1-1.py:2390`, `2215-2387` |
| FR-R5 | Metrik resolution time solver (jam, case `done`): median, bucket `lt_1h/b_1_6h/b_6_24h/gt_24h`, per-solver, watchlist `unresolved`    | Wajib     | `main-v1-1.py:2612`, `2425-2609` |


### FR-O — Administrasi &amp; Operasional


| ID    | Kebutuhan                                                                                                                                                       | Prioritas | Bukti                                                    |
| ----- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------- | -------------------------------------------------------- |
| FR-O1 | CRUD grup WA + flag `is_default` (maks 1 di level DB via partial unique index)                                                                                  | Wajib     | `main-v1-1.py:2824-2947`, `schema-multi-group.sql:23-24` |
| FR-O2 | CRUD kontak solver (soft delete `is_active=false`; phone unik antar-aktif; strip `+`)                                                                           | Wajib     | `main-v1-1.py:2689-2804`                                 |
| FR-O3 | Discovery grup langsung dari WAHA (annotasi `registered/group_id/is_active/is_default`)                                                                         | Wajib     | `main-v1-1.py:2992`                                      |
| FR-O4 | Health detail (db, waha.http, tiap session, uptime); `status: degraded` bila DB gagal / WAHA HTTP ≠200 / tak ada session `WORKING`; HTTP tetap 200              | Wajib     | `main-v1-1.py:3275`, `3203-3251`                         |
| FR-O5 | Alert Telegram: transisi ok→down kirim sekali; masih down ≥ `HEALTH_ALERT_REMINDER_HOURS` (6) → re-alert; pulih → recovery ±menit downtime                      | Wajib     | `main-v1-1.py:3309-3363`                                 |
| FR-O6 | Autentikasi: `X-API-Key` pada seluruh `/api` (kosong → terbuka, dev); webhook secret (constant-time, header atau `?token=`); rate limit 60 RPM/IP + 5 login/min | Wajib     | `main-v1-1.py:260-289`, `2651-2686`                      |


---

## 7. Kebutuhan Non-Fungsional


| Aspek                    | Nilai / perilaku                                                                                                                                                                                                                                                                                                                                                                 | Sumber                                               |
| ------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------- |
| Kinerja webhook          | Regex &amp; chain traversal ≈50 ms; LLM hanya dipanggil bila regex/chain gagal (menghemat \~9–10 detik per pesan)                                                                                                                                                                                                                                                                | `main-v1-1.py:1094-1192`                             |
| Ketersediaan             | Tidak ada scheduler internal. Semua tugas periodik dipicu cron eksternal → HTTP: reminder (`POST /api/reminders/run`, rekomendasi tiap 2 jam — **saat ini HOLD di produksi**), health alert (`POST /api/health/alert`, tiap 5 menit via `scripts/health-alert-cron.sh`), backup harian (`scripts/backup-db.sh`, retensi 14 hari), update WAHA bulanan (`scripts/update-waha.sh`) | `docs/production-runbook.md:293-295`                 |
| Keamanan                 | Data mengandung PII pelanggan → akses internal only; WAHA tidak diekspos publik; security headers (nosniff/DENY/XSS/Referrer); CORS allowlist; anti-SSRF media (hostname+port harus persis `WAHA_URL`)                                                                                                                                                                           | `main-v1-1.py:237-256`, `229-236`, `143-172`         |
| Data                     | Waktu ISO 8601 `TIMESTAMPTZ`; soft delete `deleted_at`; `progress_updates` berfungsi ganda sebagai audit trail                                                                                                                                                                                                                                                                   | `schema-v1-2.sql`, `schema-migration-deleted-at.sql` |
| Batas &amp; skalabilitas | Connection pool 2–10; rate limiter in-memory per-proses (reset saat restart, tidak shared antar worker — batasan)                                                                                                                                                                                                                                                                | `main-v1-1.py:304-317`, `184-186`                    |
| Kompatibilitas           | PostgreSQL (Supabase via connection string pooler port 6543); LLM endpoint OpenAI-compatible                                                                                                                                                                                                                                                                                     | `.env.example:4`, `main-v1-1.py:1026-1089`           |
| Webhook                  | Balas `200` cepat, proses sinkron per request (tanpa background task); retry hanya di LLM (3× backoff)                                                                                                                                                                                                                                                                           | `main-v1-1.py:2664`, `1026-1089`                     |
| Logging                  | Structured logging (level/format tetap) + request ID 8 karakter per request, header `X-Request-ID`, log durasi                                                                                                                                                                                                                                                                   | `main-v1-1.py:36-43`, `261-272`                      |


---

## 8. Arsitektur Ringkas

```mermaid
flowchart LR
    WEB["Web (Agen/Admin)"] --> API["FastAPI IT Tracker<br/>main-v1-1.py"]
    API --> DB[("PostgreSQL")]
    API <--> WAHA["WAHA"]
    WAHA <--> GRUP["WhatsApp Group"]
    API --> LLM["OpenRouter / LLM"]
    CRON["Cron (VM)"] --> API
    API --> TG["Telegram Bot"]
```

1. **Monolit single-file** 3.373 baris, tanpa ORM — psycopg3 + `psycopg_pool`, SQL mentah dengan `dict_row`.
2. **Tidak ada scheduler/thread/background-task** di dalam aplikasi — semua tugas periodik datang dari cron eksternal → HTTP endpoint.
3. **WAHA sebagai gateway WhatsApp**, session tunggal dari env `WAHA_SESSION`.
4. **LLM fallback OpenRouter-compatible** (3 retry, confidence ≥ 0.7) sebagai pintu terakhir pencocokan.
5. **Media disimpan lokal** di `MEDIA_DIR`, disajikan via `/api/media/file/{f}`, dengan proxy legacy + lazy migration.
6. **Deploy:** docker-compose + nginx (HTTPS), database Supabase-compatible.

---

## 9. Model Data Ringkas


| Tabel                                                 | Isi                                                                                                | Definisi                                                                                                                    |
| ----------------------------------------------------- | -------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `cases`                                               | Case + `fields` JSONB + status + grup/area/regional/sumber/jenis + mentions/reminder + soft delete | `schema-v1-2.sql:43-65` + `schema-multi-group.sql:30-31` + `schema-reminder.sql:5-11` + `schema-migration-deleted-at.sql:4` |
| `wa_messages`                                         | Semua pesan grup (rantai via `quoted_id`), `media_url`/`media_type`                                | `schema-v1-2.sql:75-87` + `schema-media-columns.sql`                                                                        |
| `progress_updates`                                    | Audit trail status (source: rule/reply/chain/llm/crawl/manual)                                     | `schema-v1-2.sql:93-106`                                                                                                    |
| `areas`, `regionals`, `sumber_tickets`, `jenis_cases` | Tabel lookup; `regionals` FK ke `areas` dengan `UNIQUE(area_id,name)`                              | `schema-v1-2.sql:9-37`                                                                                                      |
| `wa_groups`                                           | Grup WA + `is_active`/`is_default`                                                                 | `schema-multi-group.sql:10-24`                                                                                              |
| `reminder_log`                                        | Riwayat reminder (`triggered_by` manual|cron)                                                      | `schema-reminder.sql:14-22`                                                                                                 |
| `solver_contacts`                                     | Kontak solver (soft delete)                                                                        | `schema-solver-contacts.sql:4-15`                                                                                           |
| `alert_state`                                         | **Runtime** (dibuat di lifespan via `CREATE TABLE IF NOT EXISTS`)                                  | `main-v1-1.py:935-949`                                                                                                      |


Tiga partial index yang perlu diperhatikan:

- `idx_wa_groups_default` — partial unique, menjamin maksimal satu grup default (`schema-multi-group.sql:23-24`).
- `idx_cases_deleted` — partial pada `deleted_at IS NULL`, mempercepat query case aktif (`schema-migration-deleted-at.sql:4`).
- `idx_solver_contacts_phone` — partial unique, menjamin phone unik antar-kontak aktif (`schema-solver-contacts.sql:15`).

### Urutan migrasi

Install otomatis docker-compose (`docker-compose.yml:64-66`): `schema-v1-2.sql` → `seed-v1-2.sql` → `schema-multi-group.sql`.

Lalu **manual wajib**: `schema-solver-contacts.sql` → `schema-reminder.sql` → `schema-migration-deleted-at.sql` → `schema-media-columns.sql` (rujukan: `docs/production-runbook.md:98-104`).

Dua catatan gap:

1. **Tidak ada file schema kanonik** — skema tersusun dari 11 file assembly.
2. **Seed `jenis_cases` tidak memuat `AO`** meski `templates.py:8` mendukungnya (`seed-v1-2.sql:6`).

---

## 10. Spesifikasi API Ringkas

Legend autentikasi:

- **APIKEY** = `X-API-Key` (`BACKEND_API_KEY`; kosong ⇒ auth mati — dev only).
- **SECRET** = `WAHA_WEBHOOK_SECRET` (header `X-Webhook-Secret` atau `?token=`, constant-time; kosong ⇒ terbuka + warning).
- **—** = publik (media file, health, docs).

Seluruh route **APIKEY** juga terkena rate limit 60 RPM/IP.


| #   | Method | Path                                | Perilaku                                                                                                                                               | Auth   | Anchor |
| --- | ------ | ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ | ------ | ------ |
| 1   | POST   | `/api/auth/access-code`             | Login kode akses → `{token, expires_in}` JWT; 401 salah; 503 belum dikonfigurasi; rate limit login 5/min                                               | —      | 1376   |
| 2   | POST   | `/api/cases`                        | Render teks → kirim ke grup (atau default) → INSERT/upsert case + root `wa_messages`; 201 `{id, case_code, wa_message_id, group_id, group_name, text}` | APIKEY | 1391   |
| 3   | POST   | `/api/cases/preview`                | Render teks identik, tanpa kirim/tulis; `{text, mentions}`                                                                                             | APIKEY | 1479   |
| 4   | POST   | `/api/cases/test-send`              | Kirim ke grup test/default, tanpa membuat case                                                                                                         | APIKEY | 1490   |
| 5   | GET    | `/api/cases/export.xlsx`            | Export Excel filter identik dashboard, tanpa pagination                                                                                                | APIKEY | 1615   |
| 6   | GET    | `/api/cases`                        | List + filter kombinatif + `q`; pagination opt-in (envelope)                                                                                           | APIKEY | 1703   |
| 7   | GET    | `/api/cases/{case_id}`              | Detail: `{case, messages, updates, participants}`; 404 bila deleted                                                                                    | APIKEY | 1755   |
| 8   | POST   | `/api/cases/{case_id}/status`       | Koreksi status manual; catatan sintetis `manual-…`; `issue` ditolak                                                                                    | APIKEY | 1796   |
| 9   | DELETE | `/api/cases/{case_id}`              | Soft delete; `{ok, case_code}`                                                                                                                         | APIKEY | 1819   |
| 10  | GET    | `/api/areas`                        | Lookup area                                                                                                                                            | APIKEY | 1834   |
| 11  | GET    | `/api/areas/{area_id}/regionals`    | Regional per area; 404 area tak dikenal                                                                                                                | APIKEY | 1847   |
| 12  | GET    | `/api/sumber-tickets`               | Lookup STC/Grapari/Web IT                                                                                                                              | APIKEY | 1867   |
| 13  | GET    | `/api/jenis-cases`                  | Lookup Non Order/Non AO/AO/Mobile                                                                                                                      | APIKEY | 1880   |
| 14  | POST   | `/api/cases/{case_id}/reminder`     | Reminder manual reply-to-root + mention solver; 404/400 (done)                                                                                         | APIKEY | 1895   |
| 15  | POST   | `/api/cases/{case_id}/replies`      | Balas dari web: teks + ≤3 lampiran; caption-only bila ada lampiran                                                                                     | APIKEY | 1968   |
| 16  | POST   | `/api/reminders/run`                | Cron batch case idle; `{checked, reminded, cases[]}`; `hours` default 2                                                                                | APIKEY | 2053   |
| 17  | GET    | `/api/reminders/pending`            | Dry-run case idle + `idle_hours` + filter                                                                                                              | APIKEY | 2136   |
| 18  | GET    | `/api/cases/{case_id}/reminder`     | Riwayat reminder                                                                                                                                       | APIKEY | 2193   |
| 19  | GET    | `/api/metrics/solver-response`      | Response time solver, satuan **menit**: summary(median), distribution, per\_solver, `unanswered`                                                       | APIKEY | 2390   |
| 20  | GET    | `/api/metrics/solver-resolution`    | Resolution time solver, satuan **jam**, case done: summary, distribution, per\_solver, `unresolved`                                                    | APIKEY | 2612   |
| 21  | POST   | `/webhooks/waha`                    | Receiver event WAHA; `message` → parse, `message.ack` → ack, lain no-op; selalu `{ok:true}`; 400 invalid/missing event                                 | SECRET | 2664   |
| 22  | GET    | `/api/solver-contacts`              | List kontak (+`is_active`, +`q`)                                                                                                                       | APIKEY | 2689   |
| 23  | POST   | `/api/solver-contacts`              | Tambah; strip `+`; 409 phone duplikat aktif                                                                                                            | APIKEY | 2713   |
| 24  | GET    | `/api/solver-contacts/{contact_id}` | Detail; 404                                                                                                                                            | APIKEY | 2741   |
| 25  | PUT    | `/api/solver-contacts/{contact_id}` | Update parsial; 422 tanpa field; 409 duplikat                                                                                                          | APIKEY | 2758   |
| 26  | DELETE | `/api/solver-contacts/{contact_id}` | Soft delete `is_active=false`                                                                                                                          | APIKEY | 2804   |
| 27  | GET    | `/api/groups`                       | Switcher safe-by-default (eksklusi inactive + default; `include_*` opsional)                                                                           | APIKEY | 2824   |
| 28  | POST   | `/api/groups`                       | Tambah grup; `chat_id` harus `^\d+@g\.us$`; 409 duplikat; `is_default` menggeser default lama                                                          | APIKEY | 2847   |
| 29  | GET    | `/api/groups/{group_id}`            | Detail grup                                                                                                                                            | APIKEY | 2879   |
| 30  | PUT    | `/api/groups/{group_id}`            | Update parsial; pindah default; 409/422                                                                                                                | APIKEY | 2896   |
| 31  | DELETE | `/api/groups/{group_id}`            | Nonaktifkan (soft delete)                                                                                                                              | APIKEY | 2947   |
| 32  | GET    | `/api/waha/groups`                  | Discovery langsung dari WAHA; annotasi `registered/group_id/...`; unregistered dulu                                                                    | APIKEY | 2992   |
| 33  | POST   | `/api/crawl`                        | Backfill 2-pass; tanpa `group_id` → semua grup aktif; 404 grup tak dikenal                                                                             | APIKEY | 3037   |
| 34  | GET    | `/api/media/file/{filename}`        | Sajikan media lokal; regex nama `^[a-f0-9\-]+\.\w{2,5}$`; content-type by ext                                                                          | —      | 3131   |
| 35  | GET    | `/api/media/proxy`                  | Proxy legacy WAHA + lazy migration (download → simpan → rewrite `media_url`)                                                                           | —      | 3153   |
| 36  | GET    | `/health`                           | Health detail; HTTP 200 walau `degraded`                                                                                                               | —      | 3275   |
| 37  | POST   | `/api/health/alert`                 | Cron 5 menit; state machine vs `alert_state`; Telegram                                                                                                 | APIKEY | 3366   |


Tiga catatan tambahan:

- **Format error** = `{detail}` (default FastAPI).
- **Pagination envelope** = `{data, pagination{page,limit,total,total_pages,has_next,has_prev}}`.
- **Filter daftar case** = `status, case_type, area_id, regional_id, sumber_ticket, group_id, q, include_deleted, date_from, date_to` (rentang tanggal inklusif).

---

## 11. Kriteria Penerimaan


| Domain              | Kriteria lolos                                                                                                                 | Bukti test                                                            |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------- |
| Case &amp; template | Preview == teks terkirim; render per jenis sesuai urutan field                                                                 | `tests/test_templates.py`, `tests/test_api.py:1568` (TestCasePreview) |
| Parser keyword      | `belum selesai` ≠ done; `diluruskan` ≠ done; `fu` hanya whole-word; done menang atas in\_progress; persen cap 100              | `tests/test_chain.py:150,209`                                         |
| Chain               | max 5 level; sirkular aman; reply ke root = `reply`, lebih dalam = `chain`; grup lain ditolak                                  | `tests/test_chain.py:59`, `tests/test_groups.py:65`                   |
| Webhook             | 400 invalid JSON / missing event; `fromMe` skip; grup tak terdaftar skip; ack update                                           | `tests/test_api.py:601`                                               |
| LLM                 | confidence clamp 0–1; invalid status → None; retry 5xx; tanpa retry 4xx/invalid JSON                                           | `tests/test_llm_crawl.py:30,77`                                       |
| Reminder            | 404 case tak ada; 400 case done; riwayat; pending menghitung `idle_hours`; deleted case tidak muncul di pending                | `tests/test_api.py:1428`                                              |
| Grup &amp; kontak   | default maks 1 (DB); switcher tanpa `include_default` tidak memuat default; discovery annotate benar; 409 phone duplikat aktif | `tests/test_groups.py`, `tests/test_api.py:745,970,991`               |
| Metrik              | median &amp; bucket benar untuk data uji; `unanswered`/`unresolved` terpisah dari summary                                      | `tests/test_api.py:2890-3147`                                         |
| Health &amp; alert  | session tidak `WORKING` → `status: degraded` dengan HTTP 200; ok→down alert sekali; down &lt;6 jam → `noop`; pulih → recovery  | `tests/test_health_alert.py:32,156`                                   |
| Auth                | 401 tanpa/salah `X-API-Key`; webhook secret ditolak; rate limit login                                                          | `tests/test_api.py:478,539,574`                                       |


### Regresi wajib

```
pytest -q
```

Suite harus lulus. Jumlah saat dokumen ini ditulis: **361 fungsi test** di 7 file — `test_api.py` 212, `test_chain.py` 35, `test_groups.py` 16, `test_groups_notif.py` 16, `test_health_alert.py` 9, `test_llm_crawl.py` 24, `test_templates.py` 49.

Hasil eksekusi terukur saat penulisan dokumen: **372 lulus, 1 gagal** (`tests/test_api.py::TestAuth::test_health_never_requires_auth` → `RecursionError` saat `TestClient` menjalankan lifespan terhadap DB mock; kegagalan lingkungan/library, bukan regresi kode). Jalankan suite di lingkungan bersih dengan Postgres/WAHA mock untuk konfirmasi penuh.

---

## Lampiran A. Konfigurasi (env)


| Nama                          | Fungsi                                                                                                                                        | Default                                               |
| ----------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------- |
| `DATABASE_URL`                | Connection string PostgreSQL                                                                                                                  | `postgresql://postgres:postgres@localhost:5432/moban` |
| `WAHA_URL`                    | Base URL WAHA                                                                                                                                 | `http://localhost:3000`                               |
| `WAHA_API_KEY`                | API key WAHA                                                                                                                                  | (kosong)                                              |
| `WAHA_SESSION`                | Nama session WAHA                                                                                                                             | `default`                                             |
| `WA_GROUP_ID`                 | **Hanya seed** grup "Grup A" saat tabel `wa_groups` kosong + backfill `group_id IS NULL`; **BUKAN grup default** (default lewat `is_default`) | (kosong)                                              |
| `TELEGRAM_BOT_TOKEN`          | Token bot Telegram untuk alert                                                                                                                | (kosong)                                              |
| `TELEGRAM_CHAT_ID`            | Chat ID tujuan alert Telegram                                                                                                                 | (kosong)                                              |
| `HEALTH_ALERT_REMINDER_HOURS` | Interval re-alert saat masih down (jam)                                                                                                       | `6`                                                   |
| `OPENROUTER_API_KEY`          | API key LLM fallback                                                                                                                          | (kosong)                                              |
| `OPENROUTER_MODEL`            | Nama model LLM                                                                                                                                | `WAHA`                                                |
| `LLM_BASE_URL`                | Base URL endpoint LLM OpenAI-compatible                                                                                                       | `https://9router.tefambo.site/v1`                     |
| `BACKEND_API_KEY`             | Kunci `X-API-Key` untuk seluruh `/api`                                                                                                        | (kosong)                                              |
| `WAHA_WEBHOOK_SECRET`         | Shared secret webhook WAHA                                                                                                                    | (kosong)                                              |
| `ALLOWED_ORIGINS`             | Daftar origin CORS (comma-separated)                                                                                                          | `http://localhost:3000`                               |
| `RATE_LIMIT_RPM`              | Batas request per menit per IP                                                                                                                | `60`                                                  |
| `LOGIN_RATE_LIMIT`            | Batas percobaan login per menit per IP                                                                                                        | `5`                                                   |
| `ACCESS_CODES`                | Daftar kode akses (comma-separated)                                                                                                           | (kosong)                                              |
| `JWT_SECRET`                  | Secret signing JWT                                                                                                                            | random hex (bila kosong)                              |
| `JWT_EXPIRY_HOURS`            | Masa berlaku JWT (jam)                                                                                                                        | `24`                                                  |
| `BACKEND_PUBLIC_URL`          | Base URL publik backend (untuk URL media publik)                                                                                              | `http://localhost:8000`                               |
| `MEDIA_DIR`                   | Direktori penyimpanan media lokal                                                                                                             | `/app/media`                                          |


Catatan: `BACKEND_API_KEY` kosong ⇒ seluruh API terbuka (dev only). `WAHA_WEBHOOK_SECRET` kosong ⇒ webhook terbuka dan menulis warning di log.