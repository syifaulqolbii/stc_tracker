# Panduan FE: Export Excel + Filter Tanggal (v1.21–v1.22)

Referensi kontrak lengkap: `API-contract-frontend.md` §3.3 (list) & §3.3b (export). Dokumen ini fokus cara integrasinya di frontend.

## 1. Endpoint yang dipakai

| Kebutuhan | Endpoint |
|---|---|
| List di dashboard (dengan/pagination) | `GET /api/cases` |
| Download Excel semua row yang lolos filter | `GET /api/cases/export.xlsx` |

Kedua endpoint menerima **filter identik** — `status`, `case_type`, `area_id`, `regional_id`, `sumber_ticket`, `group_id`, `q`, `include_deleted`, `date_from`, `date_to`. Export tidak punya `page`/`limit` (selalu semua row). Backend memakai SQL builder yang sama, jadi **yang terlihat di dashboard = yang ada di Excel**, dijamin.

## 2. Filter rentang tanggal (v1.22)

- Format `YYYY-MM-DD`, keduanya opsional, boleh satu saja.
- **Inklusif**: `date_to=2026-08-31` memuat case 31 Agustus jam berapapun.
- Berbasis `created_at` (tanggal case dibuat, UTC).
- Error: format salah / `date_from > date_to` → **422**, `detail` berupa string pesan.

## 3. Download blob (JANGAN `<a href>` langsung)

API key tidak boleh lewat URL (bocor di history/server log) — selalu fetch + blob:

```js
const API_KEY = import.meta.env.VITE_API_KEY; // jangan hardcode

async function exportExcel(filters = {}) {
  const qs = new URLSearchParams(
    Object.entries(filters).filter(([, v]) => v !== "" && v != null)
  ).toString();

  const resp = await fetch(`/api/cases/export.xlsx${qs ? "?" + qs : ""}`, {
    headers: { "X-API-Key": API_KEY },
  });
  if (resp.status === 422) {
    const { detail } = await resp.json();
    alert(detail); // pesan validasi dari backend (mis. format tanggal salah)
    return;
  }
  if (!resp.ok) throw new Error(`Export gagal: ${resp.status}`);

  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);
  const a = Object.assign(document.createElement("a"), {
    href: url,
    download: `cases_${new Date().toISOString().slice(0, 10)}.xlsx`,
  });
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
```

## 4. Meneruskan filter aktif dashboard

Simpan state filter dashboard dalam satu objek, lalu pakai untuk **list dan export sekaligus**:

```js
// state filter dashboard (contoh)
let activeFilters = { status: "open", group_id: "1", date_from: "", date_to: "" };

async function refreshList(page = 1) {
  const qs = new URLSearchParams({ ...clean(activeFilters), page, limit: 50 });
  const resp = await fetch(`/api/cases?${qs}`, { headers: { "X-API-Key": API_KEY } });
  const { data, pagination } = await resp.json();
  renderTable(data);        // rows
  renderPager(pagination);  // UI paging
}

btnExport.onclick = () => exportExcel(clean(activeFilters));
// → Excel berisi persis data yang sedang tampil (semua halaman, tanpa limit)
```

```js
function clean(obj) {
  return Object.fromEntries(Object.entries(obj).filter(([, v]) => v !== "" && v != null));
}
```

## 5. Datepicker

Input type `date` native sudah memenuhi (value-nya persis `YYYY-MM-DD`):

```html
<label>Dari <input type="date" id="f-date-from" /></label>
<label>Sampai <input type="date" id="f-date-to" /></label>
```

```js
activeFilters.date_from = document.getElementById("f-date-from").value; // "2026-06-01"
activeFilters.date_to   = document.getElementById("f-date-to").value;   // "2026-08-31"
```

Quick-pick opsional (bulan ini / bulan lalu / custom):

```js
const monthRange = (offset) => {
  const d = new Date();
  d.setMonth(d.getMonth() + offset, 1);
  const from = d.toISOString().slice(0, 10);
  const to = new Date(d.getFullYear(), d.getMonth() + 1, 0).toISOString().slice(0, 10);
  return { date_from: from, date_to: to };
};
// monthRange(0) → bulan berjalan; monthRange(-1) → bulan lalu
```

## 6. Checklist QA FE

- [ ] Export tanpa filter → file berisi semua case aktif
- [ ] Export dengan filter aktif → isi file = data dashboard (bandingkan jumlah row)
- [ ] `date_from` > `date_to` → alert berisi pesan 422 dari backend, tidak ada file ter-download
- [ ] Case tanpa Nomor IH / Area → sel kosong di Excel, bukan `null`/`undefined`
- [ ] Nama file mengandung tanggal hari ini
- [ ] Tidak ada `X-API-Key` di URL tab/address bar saat export
