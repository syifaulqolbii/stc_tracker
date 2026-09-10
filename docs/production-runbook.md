# Runbook Verifikasi Produksi — Moban FU Case Tracker (v1.10)

> **Mode:** Interaktif — user jalankan perintah di VPS, agent menilai output & melacak status.
> **Regla:** Backup dulu, semua perintah read-only atau aman, `POST /api/cases` mengirim WA asli (pakai grup default/test).
> **Tanggal:** 8 September 2026 · **Commit target:** `108129a` (HEAD main)

---

## Tabel Tracking

| # | Item | Status | Kesimpulan |
|---|---|---|---|
| 0 | Persiapan (SSH, git, docker, backup, .env) | ✅ | Commit `108129a` ter-deploy; semua container UP; backup 145K OK; semua env terisi |
| 1 | Kesehatan dasar (health, docs, TLS, log) | ✅ | health ok (db+waha), TLS ok, log bersih. ⚠️ nginx healthcheck salah config (kosmetik). ❌ port 8000 terbuka publik |
| 2 | Skema DB (tabel & kolom lengkap) | ✅ | 10/10 tabel, semua kolom ada. 9 cases, 194 msgs, 8 solvers. Grup: 1 aktif (default) + 1 nonaktif |
| 3a | Auth & access-code (key valid/401, brute-force) | ⚠️ | Auth API benar (200/401/401). ❌ Login access-code TIDAK di-rate-limit (5× salah → semua 401, tanpa 429) |
| 3b | Groups switcher + WAHA discovery | ❌ | Server jalan v1.9, bukan v1.10: switcher menyodorkan grup nonaktif; error group_id masih 404 generik (bukan 409+nama grup) |
| 3c | Create case → WA terkirim | ✅ | 201, group default benar, pesan WA masuk |
| 3d | Reply-chain update status | ✅ | status→done, source=reply, author_name (LID→nama) berfungsi |
| 3e | Mention + custom header | ✅ | `{phone}` ter-expand ke @nomor, header custom terpakai |
| 3f | Media gambar+caption → URL lokal | ✅ | media_url = /api/media/file/<hex>.jpg, 200 image/jpeg publik |
| 3g | Reminder manual + auto | ✅ | Manual ok (count naik); auto checked 11/reminded 10; case grup nonaktif dilewati benar. ⚠️ Cron auto-reminder TIDAK terpasang |
| 3h | Soft delete tidak bisa di-update | ❌ | BUG: reply WA ke case terhapus tetap memproses — progress_updates baru + updated_at berubah (deleted_at diabaikan webhook) |
| 3i | Crawl | ➖ | Tidak dijalankan (opsional, berisiko backfill massal) |
| 4.1 | Webhook butuh secret | ❌ | 200 ok tanpa auth — webhook terbuka; bisa inject update palsu dari internet |
| 4.2 | Media proxy tolak host asing | ❌ | 502 = fetch benar-benar terjadi ke host asing (waha.attacker.com) dengan header X-Api-Key — SSRF terkonfirmasi |
| 4.3 | BACKEND_API_KEY terisi | ✅ | 43 karakter, fail-closed terbukti (401 tanpa key via port langsung) |
| 4.4 | JWT_SECRET terisi | ✅ | 64 karakter |
| 5 | Ops: restart, disk, cron, backup | ⚠️ | Restart pulih 10s; disk 49% & media 1.5M sehat; cron backup ✅ harian. ❌ Cron auto-reminder tidak ada; nginx healthcheck salah config |

---

## Phase 0 — Persiapan

```bash
# SSH ke VPS
ssh user@vps-ip

# Lokasi proyek (mis. /opt/moban-tracker atau folder docker-compose)
cd /path/ke/proyek

# Catat commit yang ter-deploy
git log --oneline -1

# Container status
docker ps

# Backup DB DULU (aman)
bash scripts/backup-db.sh
# atau manual:
docker exec moban-db pg_dump -U postgres -d moban > backup_$(date +%F).sql

# Cek .env — hanya catat NAMA var yang terisi (jangan membagikan nilai):
grep -oE '^[A-Z_]+=' .env
```

**Kesimpulan Phase 0:** commit ter-deploy, semua container UP, backup berhasil.

---

## Phase 1 — Kesehatan dasar

```bash
# Health check (db + waha status)
curl -s https://API_DOMAIN/health

# Docs Swagger
curl -s -o /dev/null -w "%{http_code}\n" https://API_DOMAIN/docs

# TLS/HSTS header
curl -sI https://API_DOMAIN/health | head -20

# Log backend — scan 5xx / traceback
docker logs moban-tracker --tail 100
```

**Kesimpulan Phase 1:** `{"status":"ok","db":"ok","waha":"ok"}`, docs 200, TLS valid, tidak ada traceback.

---

## Phase 2 — Skema DB (KRITIS — memvalidasi temuan #1)

```bash
# List semua tabel — HARUS ada: cases, wa_messages, progress_updates, areas,
# regionals, sumber_tickets, jenis_cases, wa_groups, solver_contacts, reminder_log
docker exec moban-db psql -U postgres -d moban -c '\dt'

# Kolom cases — HARUS ada: mentions, reminder_count, last_reminder_at, deleted_at, group_id
docker exec moban-db psql -U postgres -d moban -c '\d cases'

# Kolom wa_messages — HARUS ada: media_url, media_type, author_name
docker exec moban-db psql -U postgres -d moban -c '\d wa_messages'

# Grup terdaftar — HARUS ≥1, tandai mana default
docker exec moban-db psql -U postgres -d moban -c "SELECT id,name,chat_id,is_active,is_default FROM wa_groups;"
```

**Jika ada yang kurang (fix idempotent):**
```bash
docker exec -i moban-db psql -U postgres -d moban < schema-solver-contacts.sql
docker exec -i moban-db psql -U postgres -d moban < schema-reminder.sql
docker exec -i moban-db psql -U postgres -d moban < schema-migration-deleted-at.sql
docker exec -i moban-db psql -U postgres -d moban < schema-media-columns.sql
```

---

## Phase 3 — Fitur inti (pakai grup default/test)

> ⚠️ **`POST /api/cases` mengirim pesan WhatsApp asli.** Simpan INC palsu tes (mis. `INCTEST001`).

### 3a. Auth & access-code
```bash
# API key valid → 200
curl -s -H "X-API-Key: $BACKEND_API_KEY" https://API_DOMAIN/api/groups

# Tanpa key → 401
curl -s -o /dev/null -w "%{http_code}\n" https://API_DOMAIN/api/groups

# Login access-code → JWT
curl -s -X POST https://API_DOMAIN/api/auth/access-code \
  -H 'Content-Type: application/json' -d '{"code":"CODE123"}'

# Brute-force test (5× salah → harus diblokir 429)
for i in 1 2 3 4 5; do
  curl -s -o /dev/null -w "%{http_code} " \
    -X POST https://API_DOMAIN/api/auth/access-code \
    -H 'Content-Type: application/json' -d '{"code":"WRONG"}'
done; echo
```
**Sesuai:** key valid 200 / tanpa key 401, login benar dapat token, salah berkali-kali → 429.

### 3b. Groups switcher + discovery
```bash
# Switcher safe-by-default — grup default TIDAK boleh muncul
curl -s -H "X-API-Key: $BACKEND_API_KEY" "https://API_DOMAIN/api/groups"

# Admin view
curl -s -H "X-API-Key: $BACKEND_API_KEY" "https://API_DOMAIN/api/groups?include_inactive=true&include_default=true"

# WAHA discovery
curl -s -H "X-API-Key: $BACKEND_API_KEY" "https://API_DOMAIN/api/waha/groups"
```
**Sesuai:** switcher bersih (tanpa default), discovery menampilkan grup + `registered`/`group_id`/`is_active`/`is_default`.

### 3c. Create case
```bash
curl -s -X POST https://API_DOMAIN/api/cases \
  -H "X-API-Key: $BACKEND_API_KEY" -H 'Content-Type: application/json' \
  -d '{"jenis_case":"Non Order","sumber_ticket":"STC","fields":{"ticket_remedy":"INCTEST001","no_indihome":"0211234567","detail_case":"TEST RUNBOOK"}}'
```
**Sesuai:** 201, pesan muncul di grup WA, response berisi `group_id`/`group_name` grup default + `text`.

### 3d. Reply-chain
Balas di WA ke pesan case: `done INCTEST001` → tunggu 3–5 detik →
```bash
# Cari case_code
curl -s -H "X-API-Key: $BACKEND_API_KEY" "https://API_DOMAIN/api/cases?q=INCTEST001"

# Detail + timeline
curl -s -H "X-API-Key: $BACKEND_API_KEY" https://API_DOMAIN/api/cases/<ID>
```
**Sesuai:** status → `done`, timeline berisi balasan, `author_name` terisi.

### 3e. Mention + custom header
```bash
curl -s -X POST https://API_DOMAIN/api/cases \
  -H "X-API-Key: $BACKEND_API_KEY" -H 'Content-Type: application/json' \
  -d '{"jenis_case":"Non Order","mentions":[{"number":"6281234567890","name":"Tes"}],"custom_header":"Halo {phone} mohon bantuannya 🙏","fields":{"ticket_remedy":"INCTEST002","no_indihome":"0211234567"}}'
```
**Sesuai:** WA mention nomor, header custom terpakai.

### 3f. Media gambar+caption
Kirim gambar dengan caption `proses INCTEST001` di grup → cek `case_detail`:
```bash
curl -s -H "X-API-Key: $BACKEND_API_KEY" https://API_DOMAIN/api/cases/<ID> | python -m json.tool
```
**Sesuai:** `media_url` berisi `/api/media/file/<uuid>.jpg` (bukan proxy), URL 200 di browser.

### 3g. Reminder
```bash
# Manual
curl -s -X POST -H "X-API-Key: $BACKEND_API_KEY" https://API_DOMAIN/api/cases/<ID>/reminder

# Auto (hours=0 untuk tes)
curl -s -X POST -H "X-API-Key: $BACKEND_API_KEY" "https://API_DOMAIN/api/reminders/run?hours=0"

# Pending
curl -s -H "X-API-Key: $BACKEND_API_KEY" "https://API_DOMAIN/api/reminders/pending?hours=0"
```
**Sesuai:** reply reminder terkirim, `reminder_count` naik, log terisi.

### 3h. Soft delete
```bash
curl -s -X DELETE -H "X-API-Key: $BACKEND_API_KEY" https://API_DOMAIN/api/cases/<ID>
# lalu balas di WA "done INCTEST001"
# → case terhapus TIDAK BOLEH berubah/update
```
**Sesuai:** balasan tidak menghidupkan/update case terhapus. Jika malah update → **bug #7 terbukti**.

### 3i. Crawl (opsional — backfill semua grup aktif)
```bash
curl -s -X POST -H "X-API-Key: $BACKEND_API_KEY" "https://API_DOMAIN/api/crawl?limit=50"
```
**Sesuai:** response `fetched/stored/updates_applied`, tidak ada error massal di log.

---

## Phase 4 — Tes keamanan

```bash
# 4.1 Webhook tanpa secret — HARUS tolak (401/403). 200 = webhook terbuka (bug #2)
curl -s -X POST https://API_DOMAIN/webhooks/waha \
  -H 'Content-Type: application/json' -d '{"event":"message","payload":{}}'

# 4.2 SSRF media proxy — host asing mengandung "waha" → HARUS 400. 200/bukan 400 = SSRF (bug #3)
curl -s -o /dev/null -w "%{http_code}\n" \
  "https://API_DOMAIN/api/media/proxy?url=https%3A%2F%2Fwaha.attacker.com%2Fx.jpg"

# 4.3 BACKEND_API_KEY terisi? (nilai tidak perlu dibagikan)
grep -c 'BACKEND_API_KEY=.\+' .env

# 4.4 JWT_SECRET terisi & bukan default?
grep -c 'JWT_SECRET=.\+' .env
```
**Sesuai:** 4.1 → 401/403; 4.2 → 400; 4.3 → ≥1; 4.4 → ≥1 dan bukan placeholder.

---

## Phase 5 — Ops & recovery

```bash
# Restart app
docker restart moban-tracker && sleep 10 && curl -s https://API_DOMAIN/health

# Disk
df -h && du -sh /path/media

# Cron reminder aktif?
crontab -l | grep -iE 'reminder|crawl|backup'

# Backup ter-Jadwal?
crontab -l | grep -i backup
```

---

## Aturan praktis

- **FAIL pada hitam (2, 4.1, 4.2, 4.3)** = perbaiki DULU sebelum dipakai serius.
- **FAIL pada kuning (3h, 4.4)** = bug real, prioritaskan segera.
- **FAIL pada fitur** = catat + fix atas persetujuan user (produksi berjalan).

---

# 📋 HASIL VERIFIKASI (8 September 2026) — SELESAI

Server: `api.stc.it-jaya.id` (VPS 43.157.212.98) · Commit ter-deploy: `108129a`

## ✅ Yang terbukti JALAN di produksi
- Health (db + waha), TLS via nginx, log bersih tanpa traceback
- Skema DB lengkap (10/10 tabel, semua kolom migration ada)
- Auth API (200/401/401) + API key melindungi endpoint walau port 8000 terekspos
- Create case → grup default → WA terkirim
- Reply-chain traversal + resolusi nama kontak @lid → nama (source=reply, author_name benar)
- Custom header + placeholder {phone} + mention
- Media: download lokal → /api/media/file/<hex>.jpg, 200 publik
- Reminder manual & auto (case grup nonaktif dilewati dengan benar)
- Soft delete endpoint (case hilang dari list)
- Backup harian via cron + certbot renew
- Recovery: restart pulih dalam 10 detik

## ❌ BUG TERKONFIRMASI (urutan prioritas perbaikan)
1. **Server jalan v1.9, bukan v1.10** — perubahan v1.10 masih uncommitted di lokal.
   Gejala: switcher menyodorkan grup nonaktif; error group_id 404 generik (bukan 409 + nama grup).
   → Fix: commit v1.10 → push → di VPS `git pull && docker compose build app && docker compose up -d app`.
2. **Webhook `/webhooks/waha` tanpa auth** (200 ok tanpa secret) — orang luar bisa inject
   update status palsu. Diperparah port 8000 terbuka.
   → Fix: verifikasi shared secret (env WAHA_WEBHOOK_SECRET) di handler webhook.
3. **SSRF di `/api/media/proxy`** — substring "waha" lolos; fetch nyata ke host asing
   sambil membawa X-Api-Key WAHA (terbukti: 502, bukan 400).
   → Fix: validasi host terhadap netloc WAHA_URL sebelum fetch.
4. **Soft-delete diabaikan webhook** — reply WA ke case terhapus tetap diproses
   (progress_updates id=28 ter-insert, updated_at berubah).
   → Fix: filter `deleted_at IS NULL` di find_case_by_code/find_case_by_chain/open_case_codes.
5. **Login access-code tanpa rate limit** (5× salah semua 401, tanpa 429) — brute-forceable.
   → Fix: rate limit khusus endpoint login.
6. **Port 8000 terbuka ke publik** (0.0.0.0:8000) — bypass nginx/TLS untuk
   webhook/media/health/login. API ber-auth tetap aman (401).
   → Fix: docker-compose `"8000:8000"` → `"127.0.0.1:8000:8000"`, lalu `up -d app`.
7. **Cron auto-reminder tidak terpasang** — fitur sundul otomatis tidak pernah jalan.
   → Fix: tambah crontab, mis. `*/30 * * * * curl -s -X POST -H "X-API-Key: $KEY" \
   https://api.stc.it-jaya.id/api/reminders/run?hours=2`.

## ⚠️ Kosmetik / low priority
- Nginx healthcheck salah target (`wget localhost:80` refused) — traffic nyata sehat.
- Crawl `/api/crawl` tidak diuji (opsional, berisiko backfill massal saat jam kerja).

# ✅ RE-VERIFY FINAL (8 September 2026, pasca-deploy d1dfdf7) — SEMUA FIX TUNTAS

| Bug | Status | Bukti live |
|---|---|---|
| #1 Server v1.9 → v1.11 | ✅ PASS | switcher `[]` (grup nonaktif tersaring); group_id nonaktif → 409 + nama grup |
| #2 Webhook tanpa auth | ✅ PASS | tanpa secret → 401; dengan secret (header & query) → 200; URL webhook WAHA diperbaiki ke `http://moban-tracker:8000/webhooks/waha?token=...` (jalur lama `172.17.0.1:8000` terputus oleh fix #6 — ini efek samping yang diharapkan) |
| #3 SSRF media proxy | ✅ PASS | `waha.attacker.com` → 400 (dulu 502 = fetch nyata) |
| #4 Soft-delete dihormati | ✅ PASS | reply WA ke case terhapus: deleted_at/status/updated_at/count tetap (dulu bertambah) |
| #5 Rate limit login | ✅ PASS | via nginx `limit_req zone=auth_login rate=5r/m burst=2`: 7× login → `401×5 429×2`; endpoint lain tetap 200. (Limiter in-memory backend tidak efektif multi-worker — solusi final di nginx, config live di `/home/ubuntu/frontend/nginx/default.conf`) |
| #6 Port 8000 publik | ✅ PASS | dari luar → TERBLOKIR (000/timeout) |
| #7 Cron auto-reminder | ⏸️ HOLD | keputusan user — tidak dikerjakan |

**End-to-end terverifikasi:** create case → WA terkirim → reply `proses INCTEST003` → status `in_progress` (rantai WA → WAHA → backend hidup lewat jalur webhook ber-secret).

**Catatan ops (ditemukan saat verifikasi):**
- Nginx healthcheck "unhealthy" = healthcheck `wget localhost:80` mengikuti redirect 301 → TLS verify gagal dari dalam container. Traffic nyata selalu sehat. Kosmetik.
- Folder `/home/ubuntu/frontend/nginx/` JANGAN dihapus: masih jadi rumah config live nginx + sertifikat TLS + target cron certbot renew. Migrasi hanya jika cron certbot ikut diupdate.
- Secret webhook sempat tampil di log saat tes → disarankan rotasi: `openssl rand -hex 24`, update `.env`, `docker compose up -d app`, update `?token=` di URL webhook WAHA.

---

**Kesimpulan akhir:** Fitur inti bisnis (case → WA → reply-chain → media → reminder)
sehat di produksi. Sebelum dipakai serius: naikkan v1.10 (#1), tutup webhook (#2),
patch SSRF (#3), dan filter soft-delete (#4). Item #5–#7 menyusul.

---

# 🔧 HASIL FIX v1.11 (8 September 2026)

Fix #2, #3, #4, #5, #6 sudah dikerjakan di kode (v1.11). **#7 (cron auto-reminder) di-HOLD** atas keputusan user.

## Perubahan
- **#2 Webhook secret** — env `WAHA_WEBHOOK_SECRET`; header `X-Webhook-Secret` atau `?token=`; salah → 401. Env kosong → perilaku lama + log WARNING.
- **#3 Anti-SSRF** — `_is_waha_url()`: host+port harus identik dengan `WAHA_URL`; bypass substring (`waha.attacker.com`) kini 400; redirect tidak diikuti.
- **#4 Soft-delete** — `deleted_at IS NULL` di `find_case_by_code`, `find_case_by_chain`, `open_case_codes`, auto-reminder, pending-reminder, manual reminder.
- **#5 Rate limit login** — maks 5 percobaan/menit/IP (`LOGIN_RATE_LIMIT`), lebih → 429.
- **#6 Port** — docker-compose: `127.0.0.1:8000:8000`.
- Bonus: UUID untuk manual message-id, `datetime.now(timezone.utc)`.
- Test: **213 PASS** (189 lama + 24 baru).

## Langkah deploy ke VPS
```bash
# 1. Di laptop: commit + push (v1.10 uncommitted + fix v1.11)
# 2. Di VPS:
cd ~/stc_tracker
git pull
docker compose build app && docker compose up -d app

# 3. Set webhook secret di .env lalu restart:
echo "WAHA_WEBHOOK_SECRET=$(openssl rand -hex 24)" >> .env
docker compose up -d app   # setelah env di-load ulang
# 4. Di dashboard/konfig WAHA: set webhook URL menjadi
#    https://api.stc.it-jaya.id/webhooks/waha?token=<ISI_SECRET>
# 5. (opsional, cron auto-reminder — ON HOLD)
```

## Checklist re-verify pasca-deploy
```bash
API=https://api.stc.it-jaya.id
KEY=$(grep '^BACKEND_API_KEY=' .env | cut -d= -f2-)

# #1 v1.11 naik: switcher tidak lagi menampilkan grup nonaktif
curl -s -H "X-API-Key: $KEY" $API/api/groups          # → [] (grup nonaktif tersaring)

# #2 webhook: tanpa secret → 401 (bukan 200)
curl -s -o /dev/null -w "%{http_code}\n" -X POST $API/webhooks/waha \
  -H 'Content-Type: application/json' -d '{"event":"message","payload":{}}'

# #3 SSRF: host asing → 400 (bukan 502)
curl -s -o /dev/null -w "%{http_code}\n" \
  "$API/api/media/proxy?url=https%3A%2F%2Fwaha.attacker.com%2Fx.jpg"

# #4 soft-delete: reply WA ke case terhapus → progress_updates TIDAK bertambah
#    (cek: docker exec moban-db psql -U postgres -d moban -c \
#     "SELECT count(*) FROM progress_updates WHERE case_id = <ID_TERHAPUS>;" — angka tetap)

# #5 login: 6x salah → 429
for i in 1 2 3 4 5 6; do curl -s -o /dev/null -w "%{http_code} " \
  -X POST $API/api/auth/access-code -H 'Content-Type: application/json' -d '{"code":"X"}'; done; echo

# #6 port 8000 dari luar → timeout/refused (bukan 200)
curl -s -o /dev/null -w "%{http_code}\n" --max-time 5 http://43.157.212.98:8000/health

# WA masih masuk? (uji end-to-end setelah secret terpasang di WAHA)
# kirim pesan di grup test, pastikan webhook tetap terproses → cek /api/cases ter-update
```
---

# 🧪 FITUR TEST-SEND CASE (v1.12, 10 September 2026)

Flow baru: **preview → kirim ke grup test → kirim ke grup asli**, untuk menghindari case salah format masuk grup operasional.

## Endpoint baru (ephemeral — TIDAK membuat row case di DB)

| Endpoint | Fungsi |
|---|---|
| `POST /api/cases/preview` | Render teks case tanpa kirim & tanpa write DB. Body = sama dengan `POST /api/cases`. Response `{text, mentions}` |
| `POST /api/cases/test-send` | Kirim teks case ke grup default (`is_default`, grup Test Development). Body = sama + `test_group_id` opsional. Response `{ok, test_group_id, test_group_name, wa_message_id, text}` |

- Refactor: teks dirender via helper bersama `_render_case_payload()` — preview/test/kirim **dijamin identik**.
- Tidak ada migrasi DB. Test-send tidak memicu reminder/webhook tracking.
- Deploy biasa saja: `git pull && docker compose build app && docker compose up -d app`.

## Checklist re-verify pasca-deploy

```bash
API=https://api.stc.it-jaya.id
KEY=$(grep '^BACKEND_API_KEY=' .env | cut -d= -f2-)

# 1. Preview — render saja, tanpa kirim:
curl -s -X POST $API/api/cases/preview -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"jenis_case":"Non Order","fields":{"ticket_remedy":"INCTESTPREV","no_indihome":"0211234567"}}'
# → {"text":"#Non Order\nTicket Remedy : INCTESTPREV\n...","mentions":[]}

# 2. Test-send — kirim ke grup test (Test Development), CEK WA GRUP TEST:
curl -s -X POST $API/api/cases/test-send -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"jenis_case":"Non Order","fields":{"ticket_remedy":"INCTESTSEND01","no_indihome":"0211234567"}}'
# → {"ok":true,"test_group_id":1,"test_group_name":"Test Development",...}

# 3. Pastikan TIDAK ada case baru terbentuk:
curl -s -H "X-API-Key: $KEY" "$API/api/cases?q=INCTESTSEND01"
# → []   (ephemeral — benar)

# 4. Kirim case asli via POST /api/cases seperti biasa — teks harus identik dengan test-send.
```
