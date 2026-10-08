# Design: Reply Multi-Image sebagai Gallery Batch (v1.33)

**Tanggal:** 2026-10-08 · **Status:** Disetujui untuk implementasi
**Endpoint terkait:** `POST /api/cases/{case_id}/replies`
**File terdampak:** `main-v1-1.py`, `tests/test_api.py`, `API-contract-frontend.md`

---

## 1. Tujuan & Batasan

**Tujuan:** Saat agen mengirim 2+ image dalam satu balasan web, image-image itu dikirim ke grup WA secara **beruntun cepat** sehingga WhatsApp menyatukannya menjadi tampilan **gallery/batch** (beberapa bubble image berdampingan) — bukan lagi pesan-pesan terpisah yang berjauhan.

**Batasan yang sudah terverifikasi (3 sumber, 2026-10-08):**

- WAHA **belum punya endpoint kirim album native** — issue [devlikeapro/waha#2144](https://github.com/devlikeapro/waha/issues/2144) *"Add the ability to send an album"* masih open (dibuka 2026-07-05, hanya 1 komentar +1, tanpa jawaban maintainer).
- OpenAPI resmi WAHA (`waha.devlike.pro/swagger/openapi.json`) hanya mencantumkan `sendText`, `sendImage`, `sendFile`, `sendVideo`, `sendSticker`, `sendVoice`, dst. — **tidak ada** `sendAlbum`/`sendMediaGroup`/`sendCollection`, dan field `file` pada semua endpoint media hanya menerima **1 file**.
- Dokumentasi WAHA yang diindeks Context7 (`chatting.controller.md`, `endpoints.md`) juga tidak mencantumkan endpoint kirim album.

Karena itu pendekatan yang dipilih: **batch beruntun** — kirim N × `sendImage` berurutan dengan jeda kecil. WhatsApp Web sering menyatukan kiriman image beruntun menjadi satu batch, tapi **tidak dijamin** muncul badge "1/2" + swipe; itu perilaku sisi WhatsApp, di luar kendali backend kita.

**API request tidak berubah** (`attachments: list[ReplyAttachment]` tetap). Hanya perilaku pengiriman di backend yang diubah. FE tidak perlu perubahan.

---

## 2. Perilaku Baru (Keputusan Desain)

| Keputusan | Pilihan |
|---|---|
| Jumlah image per batch | **Semua image berturut-turut** dalam request (maks 3 sesuai limit attachment sekarang) |
| Caption | **Teks balasan jadi caption media PERTAMA saja** (image maupun file); media berikutnya: image → `""`, file → `att.filename` |
| File non-image (PDF/video) di tengah | Tetap `sendFile` — **memisahkan batch** (lihat §4) |
| Batas & validasi | Tidak berubah (maks 3 file, 5 MB/file, mimetype whitelist `REPLY_MIMES`) |

Konstanta baru: `REPLY_BATCH_DELAY_SEC = 0.7` (env-overridable, default 0.7 detik). Jeda ini memberi WhatsApp kesempatan menyatukan image menjadi batch.

---

## 3. Algoritma Pengiriman

Di endpoint `POST /api/cases/{case_id}/replies` (`main-v1-1.py` ± baris 1995), ganti loop attachment tunggal menjadi loop **per-batch**:

```python
decoded = [validate semua attachment]        # sudah ada, TIDAK berubah
batches = split_into_batches(decoded)
   # aturan: image berturut-turut dikumpulkan jadi 1 batch;
   #         file non-image (sendFile) menjadi pemisah batch
   #         (file non-image = batch berisi 1 item sendiri).

for batch in batches:
    endpoint = "sendImage" if batch[0] adalah image else "sendFile"
    for i, (att, (ext, raw)) in enumerate(batch):
        payload = {..., "caption": caption_for(att, is_first_in_request)}
        mid = await waha_send_media(endpoint, payload)
        if endpoint == "sendImage" and i < len(batch) - 1:
            await asyncio.sleep(REPLY_BATCH_DELAY_SEC)   # jeda antar image dalam batch
        if mid:
            store_message(...)                           # tidak berubah
```

**Aturan caption (prinsip anti-duplikasi v1.27 dilanjutkan):**

- `caption_for(att, is_first_in_request)`:
  - Attachment **pertama** dalam request: caption = `inp.message` (bila ada). Untuk file, fallback v1.27 tetap berlaku: tanpa teks → `att.filename`.
  - Attachment **bukan pertama**: image → `""`; file → `att.filename` (tanpa teks, menghindari duplikasi caption).
- Jadi teks balasan hanya muncul **sekali** per request, pada media pertama. Ini generalisasi dari keputusan "caption di image pertama saja" agar konsisten juga untuk file.

Catatan: `REPLY_MIMES` saat ini membatasi `image/jpeg|png|webp`, `video/mp4`, `application/pdf`. Semua yang `mimetype.startswith("image/")` dikirim via `sendImage` (konsisten dengan kode sekarang).

---

## 4. Perilaku Detail per Skenario

| Input attachments | Kirim | Hasil di WA |
|---|---|---|
| `[img, img]` | `sendImage`(1, caption=teks) → jeda → `sendImage`(2, caption="") | Batch 2 image (target) |
| `[img, img, img]` | `sendImage`(1, caption=teks) → jeda → `sendImage`(2,"") → jeda → `sendImage`(3,"") | Batch 3 image |
| `[img, pdf]` | `sendImage`(1, caption=teks) → `sendFile`(pdf, filename) | Image + file terpisah |
| `[img, pdf, img]` | `sendImage`(1, caption=teks) → `sendFile`(pdf, filename) → `sendImage`(3,"") | Image → file → image (terpisah) |
| `[pdf, img, img]` | `sendFile`(pdf, caption=teks) → `sendImage`(2,"") → jeda → `sendImage`(3,"") | File (dengan teks) → batch 2 image |
| `[img]` (1 saja) | `sendImage`(1, caption=teks) | Tetap 1 image, caption seperti biasa |
| teks saja | `sendText` | Tidak berubah |

**Konsekuensi yang diterima:** file non-image (PDF/video) **selalu memisahkan batch** — tidak bisa dijadikan satu album bersama image. Ini trade-off natural protokol WAHA (1 endpoint = 1 file).

---

## 5. Perubahan File & Data

| File | Perubahan |
|---|---|
| `main-v1-1.py` | Endpoint replies: refactor loop → batching + jeda antar image + caption hanya image pertama; konstanta `REPLY_BATCH_DELAY_SEC` |
| `tests/test_api.py` | Test baru: 2 image → 2 `sendImage` + 1 sleep; 3 image → 3 sendImage + 2 sleep; `[img, pdf]` → terpisah tanpa sleep; 1 image → tanpa sleep; caption hanya di image pertama; regression teks-only & validasi |
| `API-contract-frontend.md` | Catatan perilaku batch + delay + caption image pertama (dokumentasi) |

**Tidak ada perubahan schema DB.** Setiap image tetap `wa_messages` terpisah (`media_url`, `media_type` sendiri) — konsisten dengan timeline detail case yang menampilkan tiap media.

---

## 6. Error Handling & Partial Send

Pertahankan pola validation-first (semua attachment divalidasi + decode SEBELUM send apa pun — kode sekarang sudah benar).

- `sendImage`/`sendFile` gagal (WAHA down, 5xx): `waha_send_media` melempar HTTPException 502 → request gagal; image yang **sudah** terkirim tetap tercatat di DB (perilaku status quo hari ini, tidak berubah).
- Gagal pada image ke-2+ dalam batch → tidak ada rollback penghapusan; cukup error dilaporkan + log. Konsisten dengan perilaku sekarang.
- `asyncio.sleep` di antara image tidak mengubah status HTTP: request hanya selesai sedikit lebih lama (N image → +N×0.7s, maks 3 → +1.4s). Timeout FE 30s tetap aman.

---

## 7. Testing

**Unit (mock WAHA, `tests/test_api.py`):**

1. 2 image → tepat 2 panggilan `sendImage`, `asyncio.sleep` dipanggil di antaranya; caption payload pertama = teks, payload kedua `""`.
2. 3 image → 3 sendImage, 2 sleep.
3. `[img, pdf]` → 1 sendImage + 1 sendFile, **tanpa** sleep di antara; caption di image (pertama), pdf pakai filename.
4. `[pdf, img, img]` → 1 sendFile (caption=teks) + 2 sendImage, sleep hanya antar 2 image terakhir.
5. 1 image → tanpa sleep, caption tetap teks.
6. Regression: teks-only tetap `sendText`; validasi tetap (4 attachment → 422, oversize → 413, mimetype tak didukung → 422, base64 invalid → 422).

**Manual/staging:** kirim 2-3 image dari FE → amati di grup WA apakah menyatu jadi batch; kalau belum, tune `REPLY_BATCH_DELAY_SEC`.

---

## 8. Risiko

| Risiko | Dampak | Mitigasi |
|---|---|---|
| WhatsApp tidak menyatukan batch | Hasil tetap beberapa image terpisah (≈ status quo hari ini) | Terima; delay di-tune via env; didokumentasikan di kontrak |
| Jeda terlalu lama/cepat | Batch pecah / kecepatan kirim turun | Konstanta env mudah di-tune |
| WAHA update mengubah perilaku batching | Album mungkin tidak terbentuk lagi | Perilaku tetap "beruntun cepat" — tidak ada yang rusak, hanya efektivitas batching |
| Caption hanya di media pertama | Beberapa image tanpa caption (beda dari v1.27 yang caption di semua) | Keputusan desain eksplisit, disetujui user; didokumentasikan |
