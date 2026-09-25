# Implementasi Fitur: Balas Pesan Solver dari Web (Reply + Attachment)

**Versi:** 1.0 · **Tanggal:** 16 September 2026 · **Target:** Tim Frontend
**Endpoint terkait:** `POST /api/cases/{id}/replies` (sudah live di backend `main`)
**Referensi:** `API-contract-frontend.md` §3.6a, langkah 5–6 §10

---

## 1. Gambaran Fitur

Saat ini solver meminta informasi tambahan lewat grup WA. Fitur ini memungkinkan **agen membalas dari web** — teks + image/file — dan pesannya masuk grup WA sebagai **reply ke pesan solver yang spesifik**.

```
Solver minta info di grup WA
        │  (pesan masuk via webhook → timeline GET /api/cases/{id})
        ▼
FE: tombol "Balas" di bubble solver → form (textarea + picker ≤3 file)
        ▼
FE: encode file → base64 → POST /api/cases/{id}/replies
        ▼
Backend: kirim ke grup via WAHA (reply ke pesan solver) + catat ke DB
        ▼
FE: refresh timeline → balasan tampil (from_me=true, quoted_id nyambung)
```

**Kenapa harus lewat web (bukan HP bot):** pesan yang diketik manual dari akun bot (`fromMe=true`) di-skip webhook sehingga tidak ter-record → reply-chain putus dan balasan solver berikutnya tidak ter-link ke case. Pesan via endpoint ini dicatat (`from_me=true`, `quoted_id`, `case_id`) sehingga rantai lanjut (`source=chain`).

---

## 2. Prasyarat & Konfigurasi (ops/infra — bukan tugas FE)

- `BACKEND_PUBLIC_URL` wajib URL publik (`https://api.stc.syfa.site`) — WAHA mengunduh attachment dari URL publik backend.
- Nginx wajib memuat `client_max_body_size 25m;` (sudah di repo) — tanpa ini reply ber-attachment ditolak nginx `413` sebelum sampai backend.
- UI FE mengerjakan tombol **Balas** + form + tombol **Send** mengikuti dokumen ini.

---

## 3. Spesifikasi Endpoint

| Item | Nilai |
|---|---|
| Method | `POST` |
| URL | `{BASE_URL}/api/cases/{case_id}/replies` |
| Base URL | `https://api.stc.syfa.site` |
| Auth | Header `X-API-Key: <key>` (wajib) |
| Content-Type | `application/json` |
| Rate limit | 60 request/menit per IP |
| Timeout anjuran FE | 30 dtk (attachment via WAHA butuh waktu beberapa detik) |

---

## 4. Menentukan `reply_to_wa_message_id`

Ambil dari timeline detail case: `GET /api/cases/{id}` → `messages[]`, pilih **pesan solver** (`from_me: false` — bukan root):

```json
{
  "messages": [
    { "wa_message_id": "true_120363xxx@g.us_AAA", "quoted_id": null, "author": null, "from_me": true, "body": "punten rekan ... (root case)" },
    { "wa_message_id": "false_6281113021236@lid_BBB", "quoted_id": "3EB0A1B2C3", "author": "6281113021236@lid", "author_name": "Mas Habib", "from_me": false, "body": "minta screenshot bukti pembayarannya dong" }
  ]
}
```

→ `reply_to_wa_message_id` = `"false_6281113021236@lid_BBB"`

---

## 5. Payload (Request)

```json
{
  "message": "ini bukti pembayarannya",
  "reply_to_wa_message_id": "false_6281113021236@lid_BBB",
  "attachments": [
    {"filename": "bukti-pembayaran.jpg", "mimetype": "image/jpeg", "data_base64": "/9j/4AAQSkZJRgABAQ..."}
  ],
  "mentions": [{"number": "6281113021236", "name": "Mas Habib"}]
}
```

### Field

| Field | Tipe | Wajib | Keterangan |
|---|---|---|---|
| `message` | string \| null | salah satu | Teks balasan. Kasih `null` / hilangkan untuk media-only. |
| `reply_to_wa_message_id` | string | ✅ | `wa_message_id` pesan solver dari timeline. **Harus milik case ini** — milik case lain → `422`. |
| `attachments` | array | salah satu | Maks **3** file, default `[]`. |
| `attachments[].filename` | string | ✅ | Nama asli (menentukan tampilan caption di WA). |
| `attachments[].mimetype` | string | ✅ | Salin persis dari `file.type` browser. |
| `attachments[].data_base64` | string | ✅ | Isi file base64 — **tanpa** prefix `data:`. |
| `mentions` | array `{number, name}` | ❌ | Opsional. `number` format `62xxx` tanpa `+`. |

### Constraint (divalidasi backend)

| Rule | Nilai | Response |
|---|---|---|
| Maks file | 3 per balasan | >3 → `422` |
| Maks ukuran | 5 MB per file (decoded) | >5 MB → `413` |
| MIME allowlist | `image/jpeg`, `image/png`, `image/webp`, `video/mp4`, `application/pdf` | lainnya → `422` |
| Wajib isi | minimal salah satu `message`/`attachments` | kosong dua-duanya → `422` |

---

## 6. Response

### Sukses `200`

```json
{ "ok": true, "wa_message_ids": ["true_120363xxx@g.us_DDD", "true_120363xxx@g.us_EEE"] }
```

- `wa_message_ids` — ID pesan WA yang terkirim, urutan sebanding dengan isi (`message` dulu, lalu tiap attachment).
- **Sehabis sukses: refresh timeline** (`GET /api/cases/{id}`) — balasan tampil sebagai pesan baru `from_me: true` dengan `quoted_id` = pesan solver yang dibalas, plus `media_url`/`media_type` untuk attachment.

### Gagal (semua bentuk `{"detail": "pesan"}`)

| Status | `detail` contoh | Maksud |
|---|---|---|
| `404` | `Case not found` | case id salah / case ter-soft-delete |
| `400` | `Case tidak punya grup aktif` | grup case dinonaktifkan admin |
| `422` | `message atau attachments wajib diisi` | body kosong |
| `422` | `Maksimal 3 file per balasan` | >3 file |
| `422` | `reply_to_wa_message_id bukan pesan case ini` | ID milik case lain / tidak ada di DB |
| `422` | `mimetype text/plain tidak didukung` | MIME di luar allowlist |
| `422` | `data_base64 bukti.jpg bukan base64 valid` | string base64 rusak |
| `413` | `bukti.jpg melebihi 5 MB` | file kebesaran |
| `502` | `WAHA error: ...` / `WAHA service unavailable` | WAHA gagal — tampilkan retry ke user |
| `401` | — | API key salah |
| `429` | — | rate limit 60/menit terlampau |

---

## 7. Use Case & Payload Contoh

Semua contoh memakai timeline di §4 (`reply_to = "false_6281113021236@lid_BBB"`).

### UC-1 · Balas teks saja (paling umum)

```json
{
  "message": "siap mas, kami cek dulu ya",
  "reply_to_wa_message_id": "false_6281113021236@lid_BBB"
}
```

### UC-2 · Teks + 1 screenshot

```json
{
  "message": "ini bukti pembayarannya",
  "reply_to_wa_message_id": "false_6281113021236@lid_BBB",
  "attachments": [
    {"filename": "bukti-pembayaran.jpg", "mimetype": "image/jpeg", "data_base64": "/9j/4AAQSkZJRgABAQ..."}
  ]
}
```

### UC-3 · PDF tanpa teks (media-only)

```json
{
  "reply_to_wa_message_id": "false_6281113021236@lid_BBB",
  "attachments": [
    {"filename": "rekap-tagihan-juni.pdf", "mimetype": "application/pdf", "data_base64": "JVBERi0xLjcK..."}
  ]
}
```

### UC-4 · 2 gambar sekaligus (maks 3)

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

### UC-5 · Mention/tag solver

Cukup ketik `@628xxx` di teks — backend auto-extract dan `ngetag` (format `@+62…`/`@081…` dengan spasi/strip/titik juga dinormalisasi):

```json
{
  "message": "@6281113021236 mohon cek ulang konfigurasinya ya mas",
  "reply_to_wa_message_id": "false_6281113021236@lid_BBB"
}
```

Kalau pilih dari dropdown: isi `mentions` + tetap tulis `@6281113021236` di teks. **Catatan:** mention hanya berjalan di pesan **teks** — media-only mengabaikan `mentions`.

---

## 8. Cara Kirim Media: Encode Base64

### JavaScript (FE)

```js
function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result.split(",")[1]); // buang prefix "data:...;base64,"
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}
```

### PowerShell (test manual)

```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("C:\bukti.jpg")) | Set-Clipboard
```

> Base64 menaikkan ukuran ~33% — file 5 MB → ~6.7 MB di JSON body (di-cover limit nginx 25 m).

---

## 9. Contoh Integrasi (vanilla `fetch`)

```js
const ALLOWED_MIME = ["image/jpeg", "image/png", "image/webp", "video/mp4", "application/pdf"];
const MAX_FILE_BYTES = 5 * 1024 * 1024;
const MAX_FILES = 3;

function validateClientSide(files) {
  if (files.length > MAX_FILES) throw new Error("Maksimal 3 file per balasan");
  for (const f of files) {
    if (!ALLOWED_MIME.includes(f.type)) throw new Error(`Tipe ${f.type} tidak didukung`);
    if (f.size > MAX_FILE_BYTES) throw new Error(`${f.name} melebihi 5 MB`);
  }
}

async function kirimBalasan(caseId, messageText, files, replyToWaMessageId) {
  const attachments = [];
  for (const f of files) {
    attachments.push({
      filename: f.name,
      mimetype: f.type,
      data_base64: await fileToBase64(f),
    });
  }
  const res = await fetch(`${API_BASE}/api/cases/${caseId}/replies`, {
    method: "POST",
    headers: { "X-API-Key": API_KEY, "Content-Type": "application/json" },
    body: JSON.stringify({
      message: messageText || null,           // null untuk media-only
      reply_to_wa_message_id: replyToWaMessageId,
      attachments,
      mentions: [],                            // opsional
    }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  const data = await res.json();
  // data.wa_message_ids — lalu re-fetch GET /api/cases/{id} untuk refresh timeline
  return data;
}
```

---

## 10. UI/UX Saran (pola detail case)

1. **Tombol "Balas"** di tiap bubble pesan solver (`from_me=false`) di timeline — simpan `bubble.wa_message_id` sebagai state `replyTo`.
2. **Form balasan** muncul dekat bubble / di bawah timeline: textarea + picker file (maks 3, `accept="image/jpeg,image/png,image/webp,video/mp4,application/pdf"`) + indikator ukuran + tombol **Send**.
3. **Validasi klien dulu** (§9 `validateClientSide`) sebelum POST — feedback instan, hemat request.
4. **Saat submit tampil loading** — kirim via WAHA bisa 2–10 detik per attachment.
5. **Sukses:** kosongkan form, re-fetch `GET /api/cases/{id}`, tampilkan balasan baru di timeline (bubble `from_me=true`).
6. **Gagal:** tampilkan `err.detail` apa adanya (bahasa Indonesia dari backend sudah informatif) + tombol retry.
7. **Sesudah case `done`** — tombol Balas tetap boleh aktif (konfirmasi ke solver masih wajar), tapi pertimbangkan UI singgah bahwa case sudah done.
8. Preview bubble di timeline: pesan dari web = `from_me=true`; render dengan ikon "🌐 dari web" agar beda dengan pesan dari HP bot — opsional.

---

## 11. Paste Image dari Clipboard (Ctrl+V)

Fitur ini **tidak butuh endpoint baru** — attachment base64 di endpoint reply 100% kompatibel dengan paste image dari clipboard.

### Alur

```
Ctrl+V di textarea
  → paste event: clipboardData.items berisi File (clipboard image selalu image/png atau image/jpeg)
  → FileReader.readAsDataURL(file)     // hasil: "data:image/png;base64,iVBORw..."
  → split(",")[1]                       // strip prefix → pure base64
  → push ke state `attachments` (sama seperti file yang dipilih via picker)
  → POST /api/cases/{id}/replies
```

### Kode handler

```js
el.addEventListener("paste", (e) => {
  const img = [...(e.clipboardData?.items || [])].find(i => i.type.startsWith("image/"));
  if (!img) return;                     // paste teks biasa → biarkan default
  e.preventDefault();
  const file = img.getAsFile();
  const reader = new FileReader();
  reader.onload = () => {
    attachments.push({
      filename: file.name || `paste-${Date.now()}.png`,
      mimetype: img.type,               // "image/png" / "image/jpeg"
      data_base64: reader.result.split(",")[1],
    });
  };
  reader.readAsDataURL(file);
});
```

### Catatan penting

- **MIME**: clipboard image selalu `image/png` (screenshot) atau `image/jpeg` (copy dari viewer) — keduanya sudah di whitelist backend, tidak akan kena 422.
- **Ukuran**: screenshot layar penuh biasanya 0.5–2 MB — aman di bawah batas 5 MB. Screenshot dari tool tertentu (Retina/4K, format asli) bisa lebih besar → siapkan alert untuk `413`.
- **Payload**: JSON base64 ±33% lebih besar dari binary — 2 MB image jadi ±2.7 MB request body. Wajar, tapi jangan set client timeout terlalu pendek.
- **Nama file**: clipboard `File.name` sering kosong → fallback `paste-<timestamp>.png` (backend hanya pakai ekstensi untuk penamaan storage, aman).
- **Preview sebelum kirim**: tampilkan thumbnail dari `URL.createObjectURL(file)` + tombol hapus per attachment — pola sama seperti file picker.

## 12. Checklist Testing FE

- [ ] Balas teks-only → pesan muncul di grup sebagai reply ke bubble solver; timeline ter-update setelah re-fetch.
- [ ] Balas teks + 1 image → grup menerima image dengan caption teks; timeline menampilkan image (`media_url`).
- [ ] Kirim PDF tanpa teks → file masuk grup; timeline menampilkan media_type `application/pdf`.
- [ ] 2 file sekaligus → 2 reply terpisah muncul; response 3 id.
- [ ] Mention `@628xxx` di teks → solver terngetag di WA; hanya pada pesan teks.
- [ ] Pilih file >5 MB / MIME `.txt` / >3 file → ditolak di sisi klien (tidak POST). Strip di sisi klien adalah UX; backend tetap meng-validasi.
- [ ] `reply_to` milik case lain → `422` tampil jelas.
- [ ] Idle WAHA/matikan service → `502` + retry.
- [ ] Extract base64 prefix `data:image/png;base64,` dibuang dengan benar (kalau tidak → `422 bukan base64 valid`).
- [ ] Paste image (Ctrl+V) di textarea → thumbnail muncul; kirim → image terkirim ke grup dengan caption; timeline ter-update.
- [ ] Paste teks biasa → TIDAK mencegah default (teks tetap masuk textarea, tidak ada attachment ghost).
- [ ] Paste image >5 MB → alert `413` (atau strip di klien sebelum POST).
- [ ] Hapus attachment hasil paste via tombol x → benar-benar keluar dari state.
