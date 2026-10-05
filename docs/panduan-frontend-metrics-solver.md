# Panduan FE: Metrik Response Time Solver (v1.32)

Referensi kontrak lengkap: `API-contract-frontend.md` §12 Changelog v1.32. Dokumen ini fokus cara integrasinya di frontend.

## 1. Endpoint yang dipakai

```
GET /api/metrics/solver-response
```

Header: `X-API-Key` (sama dengan endpoint lain). Tanpa pagination — respons sudah berisi semua row yang lolos filter.

| Filter | Tipe | Contoh | Sumber pilihan |
|---|---|---|---|
| `regional_id` | int | `7` | `GET /api/areas` → `GET /api/areas/{id}/regionals` (dropdown bertingkat) |
| `solver` | string | `smops` | free text ATAU dari `GET /api/solver-contacts` (substring, case-insensitive — `smops` cocok `IT - SMOPS`) |
| `group_id` | int | `2` | `GET /api/groups` (switcher yang sudah ada) |
| `date_from` / `date_to` | YYYY-MM-DD | `2026-09-29` | date picker |

Semua opsional, digabung AND. Format tanggal salah / `date_from > date_to` → **422**, `detail` string — tampilkan pesannya, jangan crash.

## 2. Fetch data

```js
const API_KEY = import.meta.env.VITE_API_KEY; // jangan hardcode

function clean(obj) {
  return Object.fromEntries(Object.entries(obj).filter(([, v]) => v !== "" && v != null));
}

async function fetchSolverMetrics(filters = {}) {
  const qs = new URLSearchParams(clean(filters)).toString();
  const resp = await fetch(`/api/metrics/solver-response${qs ? "?" + qs : ""}`, {
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

## 3. Bentuk respons & cara render

```jsonc
{
  "filters": { "regional_id": 7, "solver": null, "...": "echo dari query" },
  "summary": { "total_cases": 44, "avg_menit": 58.07, "min_menit": 0.29,
               "max_menit": 1499.5, "median_menit": 7.85 },
  "distribution": [
    { "bucket": "lt_5",    "jumlah_case": 13, "persen": 29.5 },
    { "bucket": "b_5_15",  "jumlah_case": 17, "persen": 38.6 },
    { "bucket": "b_15_60", "jumlah_case": 9,  "persen": 20.5 },
    { "bucket": "gt_60",   "jumlah_case": 5,  "persen": 11.4 }
  ],
  "per_solver": [
    { "solver": "Alif", "jumlah_case": 3, "avg_menit": 5.95, "min_menit": 1.15,
      "max_menit": 14.72, "median_menit": 1.97 }
    // sort by median ASC — paling cepat di atas
  ],
  "unanswered": {
    "count": 9,
    "cases": [ { "id": 33, "case_code": "INC000024135513", "status": "open",
                 "group_name": "Escalation OPERA - CX100", "regional_name": "Jateng DIY",
                 "created_at": "...", "menit_sejak_kirim": 27276.7 } ]
  },
  "cases": [ { "id": 89, "case_code": "INC000024304451", "status": "done",
               "group_name": "...", "regional_name": "...",
               "kirim_case_at": "...", "solver": "IT - SMOPS",
               "balasan_pertama_at": "...", "response_menit": 2.76 } ]
}
```

**Mapping field → label UI (bucket `lt_5` dsb adalah kode, jangan ditampilkan mentah):**

| Kode bucket | Label Indonesia | Warna badge |
|---|---|---|
| `lt_5` | < 5 menit | hijau |
| `b_5_15` | 5–15 menit | hijau muda |
| `b_15_60` | 15–60 menit | kuning |
| `gt_60` | > 60 menit | merah |

**Angka waktu:** tampilkan `response_menit` dengan aturan — `< 60` → "7,9 mnt"; `≥ 60` → "1,2 jam" (bagi 60, 1 desimal) supaya tidak muncul "1499,5 mnt".

## 4. Layout yang disarankan

```
┌─ Filter bar ────────────────────────────────────────────────┐
│ [Regional ▾] [Solver ▾/⌨] [Grup ▾] [Dari 📅] [Sampai 📅] [Terapkan] │
│ preset cepat: (7 hari) (30 hari) (bulan ini)                │
└─────────────────────────────────────────────────────────────┘
┌─ 4 kartu KPI ───────────────────────────────────────────────┐
│ Median 7,9 mnt │ Avg 58,1 mnt │ 68% <15 mnt │ 9 belum dibalas │
└─────────────────────────────────────────────────────────────┘
┌─ Distribusi (stacked bar / donut dari distribution[]) ──────┐
┌─ Tabel per solver: nama | n | median | avg | min | max ─────┐
│   → kolom Median ditonjolkan, Avg diberi ⓘ tooltip          │
└─────────────────────────────────────────────────────────────┘
┌─ Watchlist "Belum dibalas" (unanswered.cases) ──────────────┐
│   → sort by menit_sejak_kirim DESC, link ke detail case      │
└─────────────────────────────────────────────────────────────┘
┌─ Tabel detail (cases[]) — collapsible, sort by response_menit ─┐
└─────────────────────────────────────────────────────────────┘
```

## 5. Prinsip penting (hasil diskusi data nyata)

1. **Kartu utama = Median, bukan Avg.** Avg 58 mnt itu terseret 5 case lambat (84% dari total payload); median 7,9 mnt yang mewakili kerja harian. Kalau Avg ditampilkan, selalu beri tooltip: *"Rata-rata sensitif terhadap case respons sangat lambat; median lebih mewakili."*
2. **`per_solver` jangan dibandingkan via Avg.** Sort-nya sudah by median dari backend. Kolom `jumlah_case` wajib tampil — solver dengan 3 case tidak setara dengan yang pegang 44.
3. **`unanswered` = action item.** Badge merah dengan count; klik → expand watchlist. Case di sana **tidak ikut** dalam summary (by design), jadi jangan tampilkan "total 53" dari 44 + 9.
4. **Preset periode** mengurangi salah input: `7 hari` / `30 hari` / `bulan ini` cukup dihitung di FE lalu set `date_from`/`date_to`.

```js
// preset periode
const today = new Date();
const iso = d => d.toISOString().slice(0, 10);
const presets = {
  "7 hari":  { date_from: iso(new Date(Date.now() - 6 * 864e5)), date_to: iso(today) },
  "30 hari": { date_from: iso(new Date(Date.now() - 29 * 864e5)), date_to: iso(today) },
};
```

## 6. Realtime vs data crawl (penting untuk akurasi)

Balasan yang masuk lewat backfill `/api/crawl` tersimpan dengan timestamp **waktu crawl**, bukan waktu asli pesan WhatsApp — latensinya bisa palsu besar (contoh nyata: case "1-SWVQDR1" tercatat 25 jam). Sampai ada flag crawl-vs-webhook di API:

- Jangan tampilkan `max_menit` mentah di KPI; pakai median + distribusi.
- Tooltip/microcopy di tabel detail: *"Case dengan response sangat panjang bisa berasal dari data historis yang di-backfill."*

## 7. Checklist integrasi

- [ ] Fetch + header `X-API-Key`, handling 422 menampilkan `detail`
- [ ] Filter: dropdown regional (bertingkat dari areas), grup dari switcher existing, date picker + preset
- [ ] KPI cards: Median (utama), Avg (dengan tooltip), % <15 mnt, count unanswered
- [ ] Bucket dikonversi ke label Indonesia + warna (tabel §3)
- [ ] Format menit/jam otomatis; angka `null` (data kosong) → tampil "—"
- [ ] Tabel per solver: sort by median, kolom jumlah_case tampil
- [ ] Watchlist unanswered: badge merah, sort by `menit_sejak_kirim` DESC, link ke `/cases/{id}`
- [ ] Loading & empty state (data kosong → "Belum ada case yang lolos filter")
