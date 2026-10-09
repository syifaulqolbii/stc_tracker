# Panduan FE: Metrik Resolution Time Solver (v1.34)

Referensi kontrak lengkap: `API-contract-frontend.md` §12 Changelog v1.34. Dokumen ini fokus cara integrasinya di frontend.

Pasangan dari `docs/panduan-frontend-metrics-solver.md` (response time, v1.32). Bedanya satu kalimat: **response time = seberapa cepat solver pertama membalas; resolution time = berapa lama total sampai case selesai.**

## 1. Endpoint yang dipakai

```
GET /api/metrics/solver-resolution
```

Header: `X-API-Key` (sama dengan endpoint lain). Tanpa pagination — respons sudah berisi semua row yang lolos filter.

| Filter | Tipe | Contoh | Sumber pilihan |
|---|---|---|---|
| `regional_id` | int | `7` | `GET /api/areas` → `GET /api/areas/{id}/regionals` (dropdown bertingkat) |
| `solver` | string | `smops` | free text ATAU dari `GET /api/solver-contacts` (substring, case-insensitive — `smops` cocok `IT - SMOPS`) |
| `group_id` | int | `2` | `GET /api/groups` (switcher yang sudah ada) |
| `date_from` / `date_to` | YYYY-MM-DD | `2026-09-29` | date picker |

Semua opsional, digabung AND. Format tanggal salah / `date_from > date_to` → **422**, `detail` string — tampilkan pesannya, jangan crash.

## 2. Yang dihitung (penting untuk microcopy UI)

| Titik | Sumber | Arti |
|---|---|---|
| **t0** | `cases.created_at` | Ticket dikirim ke grup solver |
| **t1** | **Balasan solver TERAKHIR** | Apapun isinya — termasuk "bisa dicek lagi rekan" |
| **Syarat** | `cases.status = 'done'` | Done dari keyword solver **maupun** update manual admin di web |

`resolution_jam = (t1 - t0) / 3600` — **satuan jam**, bukan menit.

**Kenapa t1 = balasan terakhir, bukan timestamp admin klik done?** Alur umum: solver membalas *"bisa dicek lagi rekan"* (tanpa keyword done → status tetap `in_progress`), lalu admin menutup case manual di web. Yang diukur adalah durasi kerja solver sampai balasan terakhirnya, bukan waktu admin menekan tombol.

**Microcopy yang disarankan** di header/tooltip tabel: *"Dihitung dari ticket dikirim sampai balasan terakhir solver. Case yang ditutup manual tetap memakai waktu balasan terakhir solver, bukan waktu penutupan."*

## 3. Fetch data

```js
const API_KEY = import.meta.env.VITE_API_KEY; // jangan hardcode

function clean(obj) {
  return Object.fromEntries(Object.entries(obj).filter(([, v]) => v !== "" && v != null));
}

async function fetchResolutionMetrics(filters = {}) {
  const qs = new URLSearchParams(clean(filters)).toString();
  const resp = await fetch(`/api/metrics/solver-resolution${qs ? "?" + qs : ""}`, {
    headers: { "X-API-Key": API_KEY },
  });
  if (resp.status === 422) {
    const { detail } = await resp.json();
    throw new ValidationError(detail); // tampilkan di UI, mis. toast merah
  }
  if (!resp.ok) throw new Error(`Metrics gagal: ${resp.status}`);
  return resp.json();
}
```

## 4. Bentuk respons & cara render

```jsonc
{
  "filters": { "regional_id": 7, "solver": null, "...": "echo dari query" },
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
    // sort by median_jam ASC — paling cepat di atas
  ],
  "unresolved": {
    "count": 4,
    "cases": [ { "id": 33, "case_code": "INC000024135513", "status": "open",
                 "group_name": "Escalation OPERA - CX100", "regional_name": "Jateng DIY",
                 "created_at": "...", "menit_sejak_kirim": 27276.7 } ]
  },
  "cases": [ { "id": 89, "case_code": "INC000024304451", "status": "done",
               "group_name": "...", "regional_name": "...",
               "kirim_case_at": "...", "solver": "IT - SMOPS",
               "balasan_terakhir_at": "...", "resolution_jam": 2.76 } ]
}
```

**Mapping field → label UI (bucket adalah kode, jangan ditampilkan mentah):**

| Kode bucket | Label Indonesia | Warna badge |
|---|---|---|
| `lt_1h` | < 1 jam | hijau |
| `b_1_6h` | 1–6 jam | hijau muda |
| `b_6_24h` | 6–24 jam | kuning |
| `gt_24h` | > 24 jam | merah |

**Angka waktu:** tampilkan `resolution_jam` dengan aturan — `< 24` → "3,5 jam"; `≥ 24` → "2,0 hari" (bagi 24, 1 desimal) supaya tidak muncul "48,0 jam". Angka `null` → tampil "—".

```js
function fmtJam(j) {
  if (j == null) return "—";
  const n = v => v.toFixed(1).replace(".", ",");
  return j >= 24 ? `${n(j / 24)} hari` : `${n(j)} jam`;
}
```

## 5. Layout yang disarankan

```
┌─ Filter bar ────────────────────────────────────────────────┐
│ [Regional ▾] [Solver ▾/⌨] [Grup ▾] [Dari 📅] [Sampai 📅] [Terapkan] │
│ preset cepat: (7 hari) (30 hari) (bulan ini)                │
└─────────────────────────────────────────────────────────────┘
┌─ 4 kartu KPI ───────────────────────────────────────────────┐
│ Median 3,5 jam │ Avg 5,2 jam │ 56% <6 jam │ 4 belum selesai │
└─────────────────────────────────────────────────────────────┘
┌─ Distribusi (stacked bar / donut dari distribution[]) ──────┐
┌─ Tabel per solver: nama | n | median | avg | min | max ─────┐
│   → kolom Median ditonjolkan, Avg diberi ⓘ tooltip          │
└─────────────────────────────────────────────────────────────┘
┌─ Watchlist "Belum selesai" (unresolved.cases) ──────────────┐
│   → sort by menit_sejak_kirim DESC, link ke detail case      │
└─────────────────────────────────────────────────────────────┘
┌─ Tabel detail (cases[]) — collapsible, sort by resolution_jam ┐
└─────────────────────────────────────────────────────────────┘
```

## 6. Prinsip penting

1. **Kartu utama = Median, bukan Avg.** Sama seperti v1.32: avg terseret case outlier (data crawl). Kalau Avg ditampilkan, beri tooltip: *"Rata-rata sensitif terhadap case berdurasi sangat panjang; median lebih mewakili."*
2. **`per_solver` jangan dibandingkan via Avg.** Sort-nya sudah by median dari backend. Kolom `jumlah_case` wajib tampil — solver dengan 3 case tidak setara dengan yang pegang 25.
3. **`unresolved` = action item.** Berisi **dua** kelompok — perhatikan `status` di tiap row:
   - `open` / `in_progress` → case masih berjalan.
   - **`done`** → anomali: case ditutup tapi **tanpa satupun balasan solver**. Ini yang perlu perhatian admin; beri badge khusus, mis. *"ditutup tanpa balasan solver"*.

   Case di sini **tidak ikut** summary (by design), jadi jangan tampilkan "total 29" dari 25 + 4.

4. **Bedakan dari halaman response time.** Dua endpoint ini mirip; jangan tertukar satuan — response time **menit**, resolution **jam**. Kalau FE menaruh keduanya di satu halaman, beri tab terpisah + label satuan yang jelas.
5. **Preset periode** mengurangi salah input: `7 hari` / `30 hari` / `bulan ini` cukup dihitung di FE lalu set `date_from`/`date_to` (kode sama seperti panduan v1.32).

## 7. Realtime vs data crawl (penting untuk akurasi)

Balasan yang masuk lewat backfill `/api/crawl` tersimpan dengan timestamp **waktu crawl**, bukan waktu asli pesan WhatsApp — latensinya bisa palsu besar. Sampai ada flag crawl-vs-webhook di API:

- Jangan tampilkan `max_jam` mentah di KPI; pakai median + distribusi.
- Tooltip/microcopy di tabel detail: *"Case dengan durasi sangat panjang bisa berasal dari data historis yang di-backfill."*

## 8. Checklist integrasi

- [ ] Fetch + header `X-API-Key`, handling 422 menampilkan `detail`
- [ ] Filter: dropdown regional (bertingkat dari areas), grup dari switcher existing, date picker + preset
- [ ] KPI cards: Median (utama), Avg (dengan tooltip), % <6 jam, count unresolved
- [ ] Bucket dikonversi ke label Indonesia + warna (tabel §4)
- [ ] Format jam/hari otomatis; angka `null` → tampil "—"
- [ ] Tabel per solver: sort by median, kolom jumlah_case tampil
- [ ] Watchlist unresolved: badge merah, bedakan row `status: done` (anomali), sort by `menit_sejak_kirim` DESC, link ke `/cases/{id}`
- [ ] Microcopy: "dihitung sampai balasan terakhir solver, bukan waktu penutupan manual"
- [ ] Loading & empty state (data kosong → "Belum ada case done yang lolos filter")