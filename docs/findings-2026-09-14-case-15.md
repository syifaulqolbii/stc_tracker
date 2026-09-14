# 📋 Arsip Temuan — Tracing Case #15 (INC01239221)

Tanggal: 2026-09-14 · Status: **✅ KEDUANYA SUDAH DIFIX (v1.13.0)** · Sumber: investigasi manual via `GET /api/cases/{id}` di prod

> **Update 14 Sep 2026:** Kedua temuan sudah difix + 9 test baru (235 PASS).
> Fix #1: pesan reminder (manual & cron) di-INSERT ke `wa_messages` (quoted_id → root).
> Fix #2: `rewrite_mentions()` — token `@<lid>` di-rewrite jadi nama kontak untuk
> `progress_updates` (parsing & tampilan); `wa_messages.body` tetap menyimpan mentah.
> Checklist re-verify pasca-deploy: `docs/production-runbook.md` § FIX FINDINGS CASE-15.

Skenario yang ditrace:

1. Case dibuat via `POST /api/cases` (mobile, mention Furqon) → pesan root terkirim ke grup
2. Reminder manual `POST /api/cases/15/reminder` → bot reply ke pesan root: "mohon di-follow up ya, case ini belum ada respon 🙏" (`reminder_count` = 1)
3. Solver **reply ke pesan reminder** itu → tidak terekam sebagai update
4. Solver lain reply ke pesan root → terekam (source: reply) ✓
5. Reply solver berisi mention (`@71782207893754`) → tersimpan sebagai LID mentah, bukan nama

---

## Temuan 1 — Reply ke pesan REMINDER tidak terdeteksi reply-chain ❌

### Gejala

Solver yang me-reply pesan reminder ("mohon di-follow up ya...") tidak menghasilkan
`progress_updates`. Reply-nya hanya terdeteksi kalau me-reply **pesan root** case langsung.

### Akar masalah (terkonfirmasi dari kode)

`send_reminder()` (main-v1-1.py:1360) mengirim reminder via `waha_send(reply_to=case["wa_message_id"])`
dan hanya menulis ke `reminder_log` + update `cases.reminder_count`.
**Pesan reminder TIDAK pernah di-INSERT ke tabel `wa_messages`.**

Akibatnya, saat solver reply ke pesan reminder, alur `handle_message` →
`find_case_by_chain(quoted_id=<id pesan reminder>)` (main-v1-1.py:509) berjalan begini:

1. Cek `cases.wa_message_id = <id reminder>` → tidak cocok (root punya ID berbeda)
2. Cek `wa_messages.wa_message_id = <id reminder>` → **tidak ada row** (tidak pernah disimpan)
3. `return None, ""` → chain gagal di depth 0

Fallback parse_rule/LLM pada body juga tidak menolong kalau body tidak menyebut case_code
(contoh nyata: "siap rekan oncek" — tidak ada kode).

### Catatan tambahan

Efeknya meluas: reply ke pesan reminder yang ditulis cron auto-reminder (bukan hanya manual) juga akan hilang dengan pola yang sama.

### Rencana fix (saat di-recall)

- Saat `send_reminder()`, INSERT pesan reminder ke `wa_messages`:
  `wa_message_id = wa_mid`, `quoted_id = case["wa_message_id"]`, `from_me = true`
- Dengan begitu `find_case_by_chain` menemukan row (quoted_id → root) dan return `("chain")`
  → reply solver ke reminder otomatis ter-link ke case
- Tambah test: reply ke pesan reminder harus menghasilkan `progress_updates` dengan source `chain`
- Perlu dicek juga: apakah webhook WAHA `message` event dengan `fromMe=true` bisa dipakai
  untuk store otomatis semua pesan keluar bot (alternatif lebih umum, sekalian menutup
  pesan keluar lain)

---

## Temuan 2 — Mention solver tersimpan sebagai LID mentah, bukan nama ❌

### Gejala

Body tersimpan (dan tampil di FE/API):

```
"body": "Baik rekan, mohon dibantu @71782207893754"
```

Yang diharapkan: nama kontak yang di-mention (mis. `@Nama Kontak`) — di WhatsApp asli
nama memang tampil, tapi itu rendering sisi client; payload mentah WAHA berisi `@<lid>`.

### Akar masalah

`store_message()` menyimpan `body` mentah dari webhook WAHA tanpa post-processing.
Mention di payload = LID internal WhatsApp (`71782207893754@lid`), bukan nama.

### Yang sudah tersedia untuk fix

Backend sudah punya infrastruktur resolusi nama (main-v1-1.py:349):

- `_contact_cache` — map `author_id → nama` (termasuk mapping `@lid → nama` via `/api/{session}/lids`)
- `resolve_contact_name(author)` — dipakai untuk `author_name` (terbukti: kolom
  `author_name: "Furqon Nugroho"` di messages berhasil teresolve)

### Rencana fix (saat di-recall)

Tambahkan rewrite mention di `store_message()` (pola sama dengan `_rewrite_media_url`):
replace token `@<lid|number>` di body dengan nama dari `_contact_cache` /
`resolve_contact_name`, fallback ke token asli kalau kontak tidak dikenal.
Keputusan desain nanti: rewrite di waktu simpan (data historis tetap mentah) vs
rewrite saat baca di API (data lama ikut bagus). Bisa juga keduanya: simpan mentah +
tambah field `body_display`.

Catatan: pesan lama yang sudah tersimpan mentah tidak ikut beres otomatis kalau
fix-nya di waktu simpan.

---

## Bukti dari data case #15

- Reply ke root (syifaulqolbi + Furqon) → masuk `updates` ✓ (source: `reply`)
- Tidak ada `progress_updates` untuk reply ke pesan reminder ✗
- `body` mention mentah: `"Baik rekan, mohon dibantu @71782207893754"` ✗
- `author_name` sudah benar: `"Furqon Nugroho"` (kontak cache bekerja untuk author)
- Manual status update via API (author: "manual") muncul 4× di messages — pola
  `manual-15-<status>-<uuid>` (perilaku normal, hanya perlu dicatat agar tidak
  dikira anomali)

## Prioritas saat fix

1. **Temuan 1** — dampak tinggi: update dari solver hilang diam-diam (silent data loss),
   mempengaruhi juga auto-reminder cron
2. **Temuan 2** — dampak medium: kualitas data/UX dashboard, tidak ada data hilang
