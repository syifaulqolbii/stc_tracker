# Spec Frontend: Mention Dropdown ala WhatsApp pada Custom Header

> **Konteks:** Backend **tidak ada perubahan sama sekali**. Semua yang dibutuhkan sudah tersedia di kontrak API (`API-contract-frontend.md`): field `mentions` + `custom_header` dengan placeholder `{phone}`. Tugas frontend adalah murni **UX**: membuat dropdown nama solver ketika user mengetik `@`, lalu menerjemahkannya menjadi payload yang sudah sesuai kontrak.

---

## 1. Perilaku yang diinginkan

User mengetik `@` di field *custom header* → muncul **dropdown daftar nama solver** (seperti mention di WhatsApp/Slack). User pilih nama → muncul "chip" mention. Tapi yang dikirim ke backend **bukan nama**, melainkan:

- `mentions`: array `{ number, name }` (nomor WA solver)
- `custom_header`: teks dengan placeholder `{phone}` di posisi mention

## 2. Sumber data dropdown

Frontend menyimpan daftar solver sendiri (nama + nomor WA format `628xxx` tanpa `+`). Bisa dari:

- file konfigurasi/JSON lokal di frontend, **atau**
- endpoint frontend yang sudah ada yang men-list solver/kontak.

❌ Tidak perlu endpoint backend baru.

## 3. UX Flow

1. **Textarea** untuk custom header.
2. Saat user mengetik `@` (di awal teks atau setelah spasi), buka **dropdown floating** berisi daftar solver, terfilter sesuai ketikan setelah `@` (mis. `@ju` → "Junaedi").
3. **Navigasi keyboard:** `↑`/`↓` pilih, `Enter`/klik untuk pilih, `Esc` tutup.
4. **Saat dipilih:**
   - Sisipkan token `{phone}` di posisi kursor, ditampilkan sebagai chip `@<Nama Solver>`.
   - Tambahkan `{ number, name }` ke state lokal `mentions` (skip jika nomor sudah ada — satu token `{phone}` akan di-expand jadi **semua** mention).
5. **Sinkronisasi:** jika semua token `{phone}` dihapus dari teks, kosongkan juga list `mentions` lokal.
6. **Jika user memilih mention tapi tidak menyisipkan `{phone}` di teks:** tidak apa-apa — backend otomatis menaruh mention di akhir pesan (perilaku yang sudah didokumentasikan).

### Visual contoh

Yang user lihat:
```
Halo @Junaedi, mohon bantuannya ya 🙏
     └─ chip mention (warna biru)
```

Yang ada di state/submit:
```json
{
  "mentions": [{ "number": "6281113021236", "name": "Junaedi" }],
  "custom_header": "Halo {phone}, mohon bantuannya ya 🙏"
}
```

## 4. Mapping ke payload (kontrak TIDAK berubah)

| UI | Payload |
|---|---|
| Chip mention yang dipilih | `mentions: [{ number: "628xxx", name: "Nama" }]` |
| Chip di dalam teks | token `{phone}` di `custom_header` |
| Header dikosongkan | kirim `custom_header: null` — backend pakai default, mention tetap jalan |
| `name` | display-only, backend abaikan untuk mekanisme mention |

> **Catatan penting (perilaku backend yang sudah ada):** jika ada **lebih dari satu** mention, **setiap** `{phone}` di-expand menjadi SEMUA nomor (`@a @b @c`). Artinya cukup pakai **satu** token `{phone}` di teks terlepas dari jumlah solver yang dipilih. Placeholder per-orang (`{phone:1}`) tidak didukung backend saat ini.

## 5. Validasi (frontend-only)

- Format nomor: `^62[0-9]+$` sebelum menambah ke `mentions`.
- Peringatkan user jika `custom_header` mengandung `{phone}` tapi list `mentions` kosong (backend akan membiarkan literal `{phone}` di pesan terkirim).
- Jangan kirim `group_id: 0` atau `""` — hilangkan field-nya jika user tidak memilih grup (sudah ada catatan soal ini di kontrak).

## 6. Komponen yang dibuat

- **`MentionTextarea`** — textarea + dropdown + filtering + keyboard nav + penyisipan token/chip.
- Sumber daftar solver (config lokal atau API existing).
- Integrasi ke form pembuatan case (field custom header).

## 7. Checklist testing

- [ ] Ketik `@` → dropdown muncul; filter `@ju` → hanya nama cocok.
- [ ] Pilih solver → chip `@Nama` muncul, `{phone}` tersimpan di state.
- [ ] Pilih 2+ solver → tetap satu token `{phone}`, `mentions` berisi semua nomor.
- [ ] Hapus semua token `{phone}` → `mentions` ikut ter-reset.
- [ ] Submit → payload sesuai kontrak; pesan WA yang diterima menampilkan `@628xxx` dan WhatsApp me-render nama kontak otomatis.
- [ ] Custom header kosong + ada mention → pesan terkirim pakai header default dengan mention di dalamnya.
