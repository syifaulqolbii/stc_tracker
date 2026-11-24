# Panduan manual — Deploy IT Tracker di VM intranet (PuTTY + WinSCP)

Acuan artefak: **hash SHA-256 di tabel bawah**, bukan commit — tarball dibangun dari isi repo (`git ls-files`) tanpa `docs/` & `.github/`, jadi commit boleh bergerak selama hash cocok. Kerjakan **berurutan**; setiap langkah punya "✅ harus terlihat". Kalau tidak cocok, **berhenti** dan lapor isi layar.

> **Urutan eksekusi:** **3A → 3B → 3C → 3D** (kode + venv) → **3E** (`.env`, sumber `DATABASE_URL`) → **Task 2** (butuh `venv/bin/python` + `DATABASE_URL`, karena VM tidak punya `psql`) → **4B–4D** → **5** → **6** → **7** (bila gate lolos) → **8** (opsional) → **9**.
> Kalau PC-mu punya `psql` dan bisa menjangkau `10.53.142.244`, Task 2 boleh dikerjakan lebih dulu (lihat 2 "Alternatif psql").

Artefak yang harus ada di PC (folder `offline/` repo). Tarball dibuat reproducible dengan `python scripts/build-src-tarball.py offline/it-tracker-src.tgz` (mtime/mode/uid dipatok, header gzip `mtime=0`, tanpa `docs/` & `.github/`) sehingga hash-nya stabil antar-build dan bisa diverifikasi ulang:

| File | Ukuran | SHA-256 |
|---|---|---|
| `it-tracker-src.tgz` | 427,859 B | `9c30bb49dda75aaa7745e3f401a36fc1268434039793ee0bb10b995fba5a872e` |
| `it-tracker-py310-wheels.tgz` | 17,079,114 B | `f7fb7d943c71ae0423c1d3c23a92ef0a58cf625decd422db4e1fb82cfc5257d1` |
| `db-bootstrap.sql` | 10,678 B | `143e029d841b58716d2dffb1d34edbb2ffa6157858526926f3a225c59f114bb1` |

---

## Langkah 0 — siapkan akses (sekali)

1. **PuTTY**: Host `10.59.129.89`, user `apps`, port 22 → login. (Password SSH; key belum terpasang.)
2. **WinSCP**: protokol SFTP, host sama, user `apps` → login. Root direktori akan jatuh di `/apps` atau home.
3. Kedua sesi harus masuk ke shell **tanpa** `sudo` sukses (kecuali `sudo vi` conf Apache di Task 5).

✅ `pwd` di PuTTY → `/apps` atau `/home/apps`; `id` → ada grup, **bukan** root.

Catatan: semua perintah di bawah dijalankan **di PuTTY** kecuali yang ditandai *(WinSCP)*.

---

## Task 2 — bootstrap schema `dc_schema`

Setelah Task 3 (venv) selesai, Task 2 dieksekusi **tanpa psql** memakai helper repo. Urutan riil: **Task 3 dulu, lalu 3E (`.env`), lalu Task 2**, karena kedua helper memakai `venv/bin/python` **dan** mengambil `DATABASE_URL` dari `.env`.

`check-db.sh` menerima DSN sebagai argumen pertama (`check-db.sh '<dsn>'`) dan juga membaca environment `DATABASE_URL`; `.env` hanya jalur default.

Kalau kamu sudah punya `psql` di PC sendiri dan bisa menjangkau `10.53.142.244`, Task 2 boleh dikerjakan sekarang dengan psql (lihat bagian "Alternatif psql" di bawah).

### 2A. Gate — cek schema & privilege

```bash
cd /apps/www/it-tracker
deploy/native/check-db.sh            # pakai DATABASE_URL dari .env
```

✅ Output: `user=usernamedb db=namadb search_path=...`, `can_use=True can_create=True`, `dc_schema tables (0): -`, `public leaks=0`, `gate: OK (siap bootstrap)`.

🛑 **STOP** bila skrip keluar non-zero. Sejak `bc122be` skrip itu sendiri yang menolak, jadi jangan lanjut ke 2B apa pun yang tercetak:

| Output | Arti | Tindakan |
|---|---|---|
| `FAIL: schema dc_schema belum ada` | schema belum dibuat | minta DBA: `CREATE SCHEMA dc_schema AUTHORIZATION usernamedb;` |
| `FAIL: role tidak punya USAGE pada dc_schema` | privilege kurang | minta DBA: `GRANT USAGE ON SCHEMA dc_schema TO usernamedb;` |
| `FAIL: role tidak punya CREATE pada dc_schema` | create hanya wajib sebelum bootstrap | minta DBA: `GRANT CREATE ON SCHEMA dc_schema TO usernamedb;` — setelah 11 tabel ada, USAGE saja cukup |
| `FAIL: dc_schema berisi tabel di luar milik app (...)` | schema dipakai aplikasi lain | **jangan** bootstrap; pakai schema/DB lain |
| `FAIL: N objek app bocor ke schema public` | sisa deploy lama di `public` | pindahkan/bersihkan dulu, kalau tidak akan ada duplikat |
| `gate: OK (bootstrap parsial — N/11 tabel, kurang: ...)` | 2B pernah gagal di tengah | ulangi 2B; semua DDL `IF NOT EXISTS`, aman |

### 2B. Terapkan bootstrap

```bash
cd /apps/www/it-tracker
venv/bin/python scripts/apply-db-bootstrap.py db-bootstrap.sql
```

✅ `search_path: dc_schema` lalu `applied db-bootstrap.sql`. Skrip menolak file tanpa `SET search_path`, dan seluruh file jalan dalam satu transaksi — gagal di tengah = tidak ada yang tersisa.

### 2C. Verifikasi hasil

```bash
deploy/native/check-db.sh
```

✅ `dc_schema tables (11): alert_state, areas, cases, jenis_cases, progress_updates, regionals, reminder_log, solver_contacts, sumber_tickets, wa_groups, wa_messages`
`sumber_tickets: ['Grapari', 'STC', 'Web IT']`, `jenis_cases: ['Mobile', 'Non AO', 'Non Order']`, `alert_state rows: 0`, `public leaks=0`, `gate: OK (sudah ter-bootstrap)`.

### Alternatif psql (kalau PC-mu punya psql dan bisa ke 10.53.142.244)

```bash
psql "host=10.53.142.244 port=5432 dbname=namadb user=usernamedb sslmode=prefer" \
  -c "select has_schema_privilege(current_user,'dc_schema','USAGE'), has_schema_privilege(current_user,'dc_schema','CREATE')"
psql "host=10.53.142.244 port=5432 dbname=namadb user=usernamedb sslmode=prefer" \
  -c "select table_name from information_schema.tables where table_schema='dc_schema' order by 1"   # harus kosong
psql "host=10.53.142.244 port=5432 dbname=namadb user=usernamedb sslmode=prefer" \
  -v ON_ERROR_STOP=1 -f db-bootstrap.sql
```

---

## Task 3 — transfer kode + venv offline

### 3A. Upload *(WinSCP)*

Ke `/apps/www/` (folder ini harus writable oleh `apps`):

| Dari PC | Ke VM |
|---|---|
| `offline/it-tracker-src.tgz` | `/apps/www/it-tracker-src.tgz` |
| `offline/it-tracker-py310-wheels.tgz` | `/apps/www/it-tracker-py310-wheels.tgz` |
| `offline/db-bootstrap.sql` | `/apps/www/db-bootstrap.sql` |

✅ Ketiga file ada di `/apps/www/`. Kalau upload terputus, ulangi file itu saja (WinSCP aman menimpa).

### 3B. Cek integritas + ekstrak

```bash
cd /apps/www
sha256sum it-tracker-src.tgz it-tracker-py310-wheels.tgz db-bootstrap.sql
```

✅ Cocokkan dengan tabel di atas **persis**. Kalau beda → upload ulang, jangan ekstrak.

```bash
mkdir -p /apps/www/it-tracker
tar xzf /apps/www/it-tracker-src.tgz -C /apps/www/it-tracker
tar xzf /apps/www/it-tracker-py310-wheels.tgz -C /apps/www/it-tracker
cd /apps/www/it-tracker
mkdir -p logs run media
cp /apps/www/db-bootstrap.sql .
chmod +x deploy/native/*.sh scripts/*.sh
ls wheels/*.whl | wc -l          # 35
ls deploy/native scripts
```

✅ `35`; daftar berisi `apache-it-tracker.conf check-db.sh ctl.sh env.example preflight.sh verify.sh watchdog.sh` dan `apply-db-bootstrap.py backup-db.sh build-db-bootstrap.sh build-src-tarball.py clear-db.sh health-alert-cron.sh update-waha.sh`.

Catatan: bit `+x` sudah ikut di arsip (mode 755 pada `*.sh`/`*.py`, 644 sisanya). `chmod +x` di atas tetap dijalankan sebagai jaring pengaman.

### 3C. venv + install offline

```bash
/apps/Python3.10/bin/python3.10 -V
/apps/Python3.10/bin/python3.10 -c "import ssl, venv, ensurepip; print('ok')"
/apps/Python3.10/bin/python3.10 -m venv /apps/www/it-tracker/venv
/apps/www/it-tracker/venv/bin/pip install --no-index --find-links /apps/www/it-tracker/wheels -r /apps/www/it-tracker/requirements.txt
```

✅ `Python 3.10.14`, `ok`, lalu `Successfully installed ...` **tanpa** kalimat "Looking in indexes" (ada jaringan pun `--no-index` memaksa offline; yang penting tidak ada error).

Wajib muncul `exceptiongroup` di daftar `Successfully installed`. Wheel itu **tidak** diturunkan otomatis oleh pip versi lama pada Python 3.10, padahal `anyio` meng-import-nya saat runtime (`if sys.version_info < (3, 11): from exceptiongroup import BaseExceptionGroup`) — kalau tidak terpasang, app gagal start. Bundle di atas sudah memuatnya.

Kalau `exceptiongroup` **tidak** muncul di `Successfully installed` (pip lama mengevaluasi marker dengan versi lain), pasang eksplisit dari bundle:

```bash
/apps/www/it-tracker/venv/bin/pip install --no-index --find-links /apps/www/it-tracker/wheels exceptiongroup
/apps/www/it-tracker/venv/bin/python -c "import exceptiongroup; print('exceptiongroup ok')"
```

Kalau `venv` gagal `ensurepip`:

```bash
/apps/Python3.10/bin/python3.10 -m venv /apps/www/it-tracker/venv
/apps/www/it-tracker/venv/bin/python -m ensurepip --default-pip || \
  /apps/www/it-tracker/venv/bin/python -m pip install --no-index --find-links wheels pip
```

### 3D. Sanity import (wajib, sebelum lanjut)

```bash
cd /apps/www/it-tracker
venv/bin/python -c "import fastapi, uvicorn, httpx, psycopg, psycopg_pool, jwt, openpyxl, pydantic; print('imports ok')"
MEDIA_DIR=/apps/www/it-tracker/media venv/bin/python -c "import importlib.util as u; s=u.spec_from_file_location('m','main-v1-1.py'); m=u.module_from_spec(s); s.loader.exec_module(m); print('app import ok')"
```

✅ `imports ok`, `app import ok`.
🛑 Kalau muncul `FileNotFoundError: /app/media` → `MEDIA_DIR` tidak ter-set; ulangi perintah kedua apa adanya.

### 3E. Buat `.env` (prasyarat Task 2)

Kerjakan **4A** sekarang; isinya lengkap di sana. Tanpa `.env`, `check-db.sh` berhenti dengan `FAIL: DATABASE_URL tidak ditemukan di ... maupun environment`, dan `apply-db-bootstrap.py` menolak jalan. Alternatif tanpa `.env`: `deploy/native/check-db.sh '<dsn>'` (argumen pertama) dan `venv/bin/python scripts/apply-db-bootstrap.py db-bootstrap.sql --dsn '<dsn>'`.

Sekarang **kembali ke Task 2** (butuh venv + `.env`).

---

## Task 4 — jalankan di loopback

### 4A. Buat `.env` (sudah dikerjakan di 3E — lewati bila selesai)

```bash
cd /apps/www/it-tracker
cp deploy/native/env.example .env
chmod 600 .env
openssl rand -hex 32     # → JWT_SECRET
openssl rand -hex 32     # → BACKEND_API_KEY
openssl rand -hex 32     # → WAHA_WEBHOOK_SECRET
vi .env
```

Isi yang **wajib** diganti di `vi`:

| Baris | Nilai |
|---|---|
| `DATABASE_URL` | ganti `GANTI_PASSWORD` dengan password `usernamedb`. **Jangan hapus** `?options=-csearch_path%3Ddc_schema&connect_timeout=5&sslmode=prefer`. Nilai **tetap di dalam kutip satu** (`'...'`) |
| `JWT_SECRET` | hasil `openssl` #1 |
| `BACKEND_API_KEY` | hasil `openssl` #2 — **catat**, dipakai FE |
| `WAHA_WEBHOOK_SECRET` | hasil `openssl` #3 |
| `ACCESS_CODES` | kode akses FE (mis. `STC2026`) |
| `ALLOWED_ORIGINS` | origin FE VM, mis. `https://10.59.129.90` |
| `RATE_LIMIT_RPM` | batas request per IP per menit untuk endpoint API (biarkan `120`) |
| `LOGIN_RATE_LIMIT` | percobaan login per IP per menit (default app 5; `env.example` memakai 10) |
| `BACKEND_PUBLIC_URL` | **biarkan** `https://10.59.129.89/it-tracker` |
| `MEDIA_DIR` | **biarkan** `/apps/www/it-tracker/media` |
| `OPENROUTER_API_KEY`, `LLM_BASE_URL`, `TELEGRAM_BOT_TOKEN` | kosongkan (egress diblokir) |

Simpan: `Esc` `:wq` (vi). Verifikasi tanpa membocorkan isi:

```bash
grep -c '^' .env; stat -c '%a %n' .env     # 600
grep -E '^(BACKEND_PUBLIC_URL|MEDIA_DIR)=' .env
```

✅ `600 .env`, dua baris bernilai tepat seperti tabel.

**Kalau password DB mengandung karakter khusus** (`@ : / ? # [ ] % $` lalu backtick, `"`, `'`, `\`, atau spasi), percent-encode dulu — password mentah akan merusak parsing URI **tanpa error**:

```bash
read -rp 'password: ' P; python3 -c 'import sys,urllib.parse;print(urllib.parse.quote(sys.argv[1],safe=""))' "$P"
# @ -> %40  $ -> %24  ` -> %60  " -> %22  \ -> %5C  ' -> %27  spasi -> %20  : -> %3A  / -> %2F  # -> %23
```

Tempel hasilnya sebagai `GANTI_PASSWORD`. Uji hasil akhirnya tanpa membocorkan password:

```bash
set -a; . ./.env; set +a
/apps/www/it-tracker/venv/bin/python -c "
from psycopg.conninfo import conninfo_to_dict as d; i=d(__import__('os').environ['DATABASE_URL'])
print('host=',i['host'],'db=',i['dbname'],'user=',i['user'],'search_path=',i['options'])"
```

✅ `host= 10.53.142.244 db= namadb user= usernamedb search_path= -csearch_path=dc_schema`. Kalau `host` bukan IP itu, password belum di-encode dengan benar.

### 4A-bis. Preflight satu perintah (opsional, disarankan)

```bash
cd /apps/www/it-tracker
APP_DIR=/apps/www/it-tracker deploy/native/preflight.sh
```

Memeriksa sekaligus: Python 3.10, isi `APP_DIR`, 35 wheel + `exceptiongroup`, bit `+x`, venv & semua import, mode `.env`, **nilai wajib yang kosong / masih `GANTI`**, **akhiran baris CRLF**, serta hasil parse DSN (host harus `10.53.142.244`, `options` harus memuat `dc_schema`). ✅ baris terakhir `semua cek inti lulus`. Paste output-nya kalau ada yang `FAIL`.

`BACKEND_API_KEY` kosong **mematikan seluruh auth** (`main-v1-1.py:305`: `if not BACKEND_API_KEY: return`), jadi baris `FAIL … kosong` di sini bukan peringatan gaya-gayaan — start akan membuat semua endpoint API terbuka.

### 4B. Start

```bash
cd /apps/www/it-tracker
deploy/native/ctl.sh start
deploy/native/ctl.sh status
```

✅ `started (pid …)` lalu `running (pid …)`.
🛑 `GAGAL start` → baca `tail -40 logs/app.log`; penyebab umum: `DATABASE_URL` salah kutip, atau `MEDIA_DIR` dihapus.

`ctl.sh` menjalankan **1 worker** (bisa di-override lewat `WORKERS=` di env atau `.env`). Alasannya bukan selera: `_rate_buckets` dan `_contact_cache` di `main-v1-1.py` adalah state in-memory per proses, jadi `RATE_LIMIT_RPM` dan `LOGIN_RATE_LIMIT` berlaku **kali jumlah worker**. Dengan 2 worker, pembatas brute-force kode akses diam-diam jadi 2× tanpa satu pun pesan error. Handler sinkron tetap konkuren (threadpool anyio), jadi menaikkan `WORKERS` hanya menambah kuota rate limit, bukan throughput nyata.

### 4C. Health

```bash
curl -s http://127.0.0.1:8000/health | python -m json.tool
```

✅ `"db": {"status": "ok", ...}`, `"status": "degraded"`, `"waha": {"http": {"status": "error", ...}}`. `degraded` **normal** sebelum WAHA ada.
🛑 `db.status: "error"` → lihat `logs/app.log`: `password authentication failed` (salah password) atau `relation ... does not exist` (search_path hilang dari DSN).

### 4D. Auth & docs

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/api/cases
curl -s -o /dev/null -w '%{http_code}\n' -H "X-API-Key: $(grep ^BACKEND_API_KEY= .env | cut -d= -f2-)" http://127.0.0.1:8000/api/cases
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/docs
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/it-tracker/health   # 404 — memang begitu
```

✅ `401`, `200`, `200`, `404`. Baris terakhir membuktikan uvicorn **tidak** menerima prefix.

---

## Task 5 — publikasi `/it-tracker` lewat Apache 443

### 5A. Gate modul

```bash
grep -nE '#?LoadModule (proxy|proxy_http)_module' /apps/httpd2462-php8336/apache/conf/httpd.conf
/apps/httpd2462-php8336/apache/bin/httpd -M 2>/dev/null | grep -E 'proxy_module|proxy_http_module'
```

✅ Dua baris `LoadModule` **berawalan `#`**, dan `-M` belum menampilkan `proxy_module`.

### 5B. Backup (wajib)

```bash
cd /apps/httpd2462-php8336/apache/conf
cp -p httpd.conf httpd.conf.bak-$(date +%F)
cp -p extra/httpd-ssl.conf extra/httpd-ssl.conf.bak-$(date +%F)
ls -l httpd.conf.bak-* extra/httpd-ssl.conf.bak-*
```

✅ Dua file `.bak-2026-11-20` (atau tanggal hari ini) muncul.

### 5C. Aktifkan modul

```bash
sudo vi /apps/httpd2462-php8336/apache/conf/httpd.conf
```

Cari ( `/proxy_module` lalu `n`): hapus `#` di depan **tepat dua baris** ini, urutan `proxy_module` lalu `proxy_http_module`:

```apache
LoadModule proxy_module      modules/mod_proxy.so
LoadModule proxy_http_module modules/mod_proxy_http.so
```

✅ `grep -nE '^LoadModule (proxy|proxy_http)_module' .../httpd.conf` → dua baris **tanpa** `#`.

### 5D. Sisipkan snippet di vhost SSL

```bash
grep -n '<VirtualHost\|</VirtualHost' /apps/httpd2462-php8336/apache/conf/extra/httpd-ssl.conf
cat /apps/www/it-tracker/deploy/native/apache-it-tracker.conf
sudo vi /apps/httpd2462-php8336/apache/conf/extra/httpd-ssl.conf
```

Tempel **isi** `apache-it-tracker.conf` (7 baris konfigurasi, bukan komentar header-nya kalau mau rapi) **di dalam** blok `<VirtualHost _default_:443>`, sebelum `</VirtualHost>`:

```apache
ProxyRequests Off
ProxyPreserveHost On
ProxyTimeout 120
ProxyAddHeaders On

ProxyPass        /it-tracker/ http://127.0.0.1:8000/
ProxyPassReverse /it-tracker/ http://127.0.0.1:8000/

RedirectMatch ^/it-tracker$ /it-tracker/docs
```

### 5E. Reload graceful + verifikasi modul

```bash
sudo /apps/httpd2462-php8336/apache/bin/httpd -k graceful
sleep 2
/apps/httpd2462-php8336/apache/bin/httpd -M | grep -E 'proxy_module|proxy_http_module'
```

✅ Kedua modul tampil.
🛑 Kalau `httpd` menolak (syntax error), pulihkan:

```bash
cd /apps/httpd2462-php8336/apache/conf
cp -p httpd.conf.bak-$(date +%F) httpd.conf
cp -p extra/httpd-ssl.conf.bak-$(date +%F) extra/httpd-ssl.conf
sudo /apps/httpd2462-php8336/apache/bin/httpd -k graceful
```

### 5F. Uji end-to-end

```bash
curl -sk -o /dev/null -w '%{http_code}\n' https://127.0.0.1/it-tracker/health
curl -sk -o /dev/null -w '%{http_code}\n' https://127.0.0.1/it-tracker/docs
curl -sk -o /dev/null -w '%{http_code}\n' https://127.0.0.1/it-tracker/openapi.json
curl -skI https://127.0.0.1/it-tracker | head -3
```

✅ `200`, `200`, `200`; baris pertama header `/it-tracker` → `302` + `Location: /it-tracker/docs`.

Cek X-Forwarded-For sampai ke app (penting untuk rate limit per IP — tanpa ini semua klien terlihat `127.0.0.1`):

```bash
curl -sk -H 'X-Forwarded-For: 10.99.99.99' -o /dev/null -w '%{http_code}\n' https://127.0.0.1/it-tracker/api/cases
sleep 1
grep '10.99.99.99' /apps/www/it-tracker/logs/app.log | tail -1
```

✅ `401` (tanpa API key, wajar) lalu muncul baris akses uvicorn `INFO: 10.99.99.99:0 - "GET /api/cases ..."`. Kalau tidak muncul, `ProxyAddHeaders` tidak aktif.

Catatan: baris log milik app (`GET /it-tracker/... 401 0.00s [id]`) **tidak** memuat IP klien — yang memuat IP adalah baris akses uvicorn (`INFO: <ip>:<port> - "..."`). Jangan pakai baris app untuk cek ini.
`503` = proxy jalan tapi app mati (`ctl.sh status`). `404` = snippet tidak di vhost yang benar.

### 5G. Uji dari PC kantor (browser)

Buka `https://10.59.129.89/it-tracker/health`.

✅ JSON `degraded` dari browser.
🛑 Chrome/Firefox menampilkan `NET::ERR_CERT_COMMON_NAME_INVALID` → **B3**: sertifikat `CN=10.59.129.89` tanpa SAN (sudah saya konfirmasi). Minta Tim PKI menerbitkan ulang dengan `subjectAltName=IP:10.59.129.89`. Curl/`-k` tetap jalan; FE tidak akan jalan sampai SAN ada.

---

## Task 6 — autostart + watchdog

### 6A. Crontab

```bash
crontab -l > /apps/www/it-tracker/cron.bak 2>/dev/null || true
cp /apps/www/it-tracker/cron.bak /tmp/cron.new 2>/dev/null || : > /tmp/cron.new
cat >> /tmp/cron.new <<'CRON'
@reboot /apps/www/it-tracker/deploy/native/ctl.sh start >> /apps/www/it-tracker/logs/ctl.log 2>&1
*/5 * * * * /apps/www/it-tracker/deploy/native/watchdog.sh >> /apps/www/it-tracker/logs/watchdog.log 2>&1
CRON
crontab /tmp/cron.new
crontab -l
```

✅ Dua baris baru (plus entri lama kalau ada). Backup di `cron.bak`.

### 6B. Uji watchdog (butuh 2 kegagalan berturut)

```bash
pkill -f 'uvicorn main-v1-1:app'; sleep 1
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/health   # 000
deploy/native/watchdog.sh; echo "1st rc=$? fail=$(cat run/watchdog.fail)"
deploy/native/watchdog.sh; echo "2nd rc=$?"
sleep 5
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/health   # 200
tail -5 logs/watchdog.log
```

✅ `000` → `1` → (kedua) restart → `200`; log `health=… gagal 2 kali — restart`.

Catatan: di Windows `curl` mengembalikan `000000`; di RHEL `000` — keduanya dianggap gagal oleh skrip.

### 6C. Restart bersih

```bash
deploy/native/ctl.sh restart && deploy/native/ctl.sh status
```

✅ `stopped`, `started (pid …)`, `running (pid …)`.

---

## Task 7 — WAHA (tunggu B1 + B2)

Gate dulu; **jangan** jalankan container bila gate gagal.

```bash
podman --version
podman info 2>/dev/null | grep -i cgroup
for u in https://web.whatsapp.com https://api.telegram.org; do
  printf '%-32s ' "$u"; curl -s -o /dev/null -m 8 -w '%{http_code}\n' "$u"
done
```

✅ `podman 4.x` (atau docker-ce) **dan** kedua URL bukan `000`.
🛑 `000` = egress diblokir (B1). `podman 1.6.4` = runtime tidak layak untuk Chromium (B2). Lapor ke tim jaringan/infra; Task 8–9 tetap bisa jalan tanpa WAHA.

Kalau gate lolos:

```bash
set -a; . /apps/www/it-tracker/.env; set +a
podman run -d --name waha --restart unless-stopped \
  --network host -v waha_sessions:/app/.sessions --shm-size=1g \
  -e WAHA_API_KEY="$WAHA_API_KEY" \
  -e WHATSAPP_HOOK_URL="http://127.0.0.1:8000/webhooks/waha?token=$WAHA_WEBHOOK_SECRET" \
  -e WHATSAPP_HOOK_EVENTS=message -e TZ=Asia/Jakarta \
  devlikeapro/waha:chrome
```

`--network host` **wajib**: dengan bridge, `127.0.0.1` di dalam container bukan host, webhook tidak akan pernah sampai ke app.

Scan QR: dari PC, buat tunnel `ssh -N -L 3000:127.0.0.1:3000 apps@10.59.129.89` (atau PuTTY: Connection → SSH → Tunnels, source `3000`, dest `127.0.0.1:3000`, Add), lalu buka `http://127.0.0.1:3000/dashboard`.

```bash
ss -ltn | grep -E ':3000\b'
curl -s -H "X-Api-Key: $WAHA_API_KEY" http://127.0.0.1:3000/api/sessions | python -m json.tool
curl -s http://127.0.0.1:8000/health | python -m json.tool
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8000/webhooks/waha \
  -H 'Content-Type: application/json' -d '{"event":"message","payload":{}}'
```

✅ `LISTEN 127.0.0.1:3000`, session `WORKING`, `/health` `"status": "ok"`, webhook tanpa token `401`.
🛑 `LISTEN 0.0.0.0:3000` → WAHA terbuka ke intranet; catat sebagai temuan keamanan.

---

## Task 8 — migrasi data lama (opsional)

Hanya bila ada data produksi VPS lama yang harus dibawa.

```bash
# di mesin yang bisa ke VPS lama DAN ke 10.53.142.244
pg_dump "$OLD_DATABASE_URL" --no-owner --no-privileges -Fc -f old.dump
pg_restore --no-owner --no-privileges --schema=public \
  -d "host=10.53.142.244 dbname=namadb user=usernamedb" old.dump
```

Lalu pindahkan 11 tabel ke `dc_schema` (satu transaksi):

```sql
BEGIN;
SET search_path TO dc_schema;
ALTER TABLE public.areas SET SCHEMA dc_schema;
ALTER TABLE public.regionals SET SCHEMA dc_schema;
ALTER TABLE public.sumber_tickets SET SCHEMA dc_schema;
ALTER TABLE public.jenis_cases SET SCHEMA dc_schema;
ALTER TABLE public.cases SET SCHEMA dc_schema;
ALTER TABLE public.wa_messages SET SCHEMA dc_schema;
ALTER TABLE public.progress_updates SET SCHEMA dc_schema;
ALTER TABLE public.wa_groups SET SCHEMA dc_schema;
ALTER TABLE public.solver_contacts SET SCHEMA dc_schema;
ALTER TABLE public.reminder_log SET SCHEMA dc_schema;
ALTER TABLE public.alert_state SET SCHEMA dc_schema;
COMMIT;
```

Reset sequence:

```sql
DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT c.relname AS seq, t.relname AS tbl
           FROM pg_class c JOIN pg_depend d ON d.objid=c.oid
           JOIN pg_class t ON t.oid=d.refobjid
           WHERE c.relkind='S' AND t.relnamespace='dc_schema'::regnamespace
  LOOP
    EXECUTE format('SELECT setval(%L, COALESCE((SELECT max(id) FROM dc_schema.%I),1))', r.seq, r.tbl);
  END LOOP;
END $$;
```

Media: `rsync -av --progress user@old-vps:/path/media/ /apps/www/it-tracker/media/` (atau WinSCP).

Verifikasi: `deploy/native/check-db.sh` (harus tanpa `search_path` error) + bandingkan jumlah baris `cases`, `wa_messages`, `progress_updates`, `wa_groups` dengan sumber.

---

## Task 9 — acceptance & rollback drill

### 9A. verify.sh dua sisi

```bash
cd /apps/www/it-tracker
deploy/native/verify.sh http://127.0.0.1:8000
deploy/native/verify.sh https://127.0.0.1/it-tracker
```

✅ Dua-duanya `OK: …` (diselingi `WARN: status degraded` bila WAHA belum ada).

### 9B. Rollback drill (di luar jam kerja)

```bash
cd /apps/httpd2462-php8336/apache/conf
cp httpd.conf.bak-YYYY-MM-DD httpd.conf
cp extra/httpd-ssl.conf.bak-YYYY-MM-DD extra/httpd-ssl.conf
sudo /apps/httpd2462-php8336/apache/bin/httpd -k graceful
curl -sk -o /dev/null -w '%{http_code}\n' https://127.0.0.1/it-tracker/health   # 404
```

Lalu pasang ulang Task 5C–5E. Rollback app: `deploy/native/ctl.sh stop`.

### 9C. Checklist penerimaan

| Kriteria | Cara cek | Target |
|---|---|---|
| Autostart setelah reboot | reboot VM (window terencana), tunggu 2 menit | `/health` 200 |
| Watchdog | `kill -9 $(cat run/uvicorn.pid)`, tunggu ≤10 menit | `/health` 200 |
| Auth | `verify.sh` | 401 tanpa key |
| DB di `dc_schema` | `deploy/native/check-db.sh` | `public leaks=0` |
| Media URL publik | 1 case uji | `media_url` diawali `https://10.59.129.89/it-tracker` |
| Port terekspos | `ss -ltn` | hanya 8000 (dan 3000 bila WAHA) di `127.0.0.1` |
| TLS browser | buka dari PC kantor | butuh B3 (SAN) |

---

## Fakta terukur (diverifikasi di workstation, 2026-11-20)

Dijalankan dengan `main-v1-1.py` asli + uvicorn lokal (`--root-path /it-tracker`), DB diarahkan ke port tertutup:

| Yang diuji | Hasil terukur | Implikasi |
|---|---|---|
| `GET /health`, `/docs`, `/openapi.json` tanpa prefix | `200`, `200`, `200` | uvicorn **tidak** men-strip prefix; Apache wajib membuang `/it-tracker` lewat `ProxyPass` |
| `GET /it-tracker/health` langsung ke uvicorn | `404` | konfirmasi tabel prefix di plan; jangan pernah menembak uvicorn dengan prefix |
| `url:` di dalam `/docs` | `/it-tracker/openapi.json` | `--root-path` memulihkan prefix untuk Swagger |
| `GET /api/cases` tanpa `X-API-Key` | `401` (walau DB mati) | cek auth di `verify.sh` valid bahkan saat DB down |
| `POST /webhooks/waha` tanpa/`?token=salah` | `401`, `401` | `WAHA_WEBHOOK_SECRET` benar-benar digate |
| `GET /health` saat DB tak terjangkau | `200` setelah **~32 s**, `db.status=error`, `status=degraded` | `curl -m 8` di `watchdog.sh` menganggapnya gagal → lihat catatan di bawah |
| App start saat DB mati | port tetap listen, `/docs` 200 | app tidak crash-loop karena DB |

**Catatan watchdog:** dengan DB lambat/mati, `/health` bisa >8 s sehingga watchdog menghitung "gagal". Dua kegagalan berturut = restart. Restart **tidak** memperbaiki DB, tapi tidak merusak apa pun; saat DB pulih, siklus berhenti sendiri. Kalau ingin menghindari restart sia-sia, naikkan `-m 8` → `-m 45` di `watchdog.sh` **setelah** deploy stabil. `verify.sh` memakai `-m 10`, jadi saat DB mati ia akan `FAIL: health tidak reachable` — itu perilaku yang diinginkan (deploy belum sehat).

### Bootstrap SQL diuji pada PostgreSQL sungguhan

`db-bootstrap.sql` dijalankan pada cluster PostgreSQL lokal (fresh database, role `usernamedb` non-superuser, schema `dc_schema` kosong):

| Uji | Hasil |
|---|---|
| `psql -v ON_ERROR_STOP=1 -f db-bootstrap.sql` sebagai `usernamedb` | exit 0 |
| Dijalankan **dua kali** (idempotensi) | exit 0, hanya `NOTICE ... already exists` |
| `information_schema.tables` di `dc_schema` | 11 (daftar sesuai Task 2 Step 4) |
| `sumber_tickets` / `jenis_cases` | `Grapari, STC, Web IT` / `Mobile, Non AO, Non Order` |
| `dc_schema.alert_state` | 0 baris |
| Objek bocor ke `public` | 0 |
| `scripts/apply-db-bootstrap.py` (via `.env` dan via `--dsn`) | `search_path: dc_schema`, 11 tabel |
| `deploy/native/check-db.sh` | gate lulus pada schema kosong (`tables (0)`) dan setelah bootstrap (`tables (11)`) |

### Rehearsal lengkap dari tarball final

`it-tracker-src.tgz` + `it-tracker-py310-wheels.tgz` diekstrak ke layout VM (bukan dari working tree), lalu seluruh Task 3–4 dijalankan terhadap PostgreSQL sungguhan:

| Langkah | Hasil |
|---|---|
| Ekstrak 2 tarball | 35 wheel, 7 skrip `deploy/native/` dengan bit `+x` |
| `preflight.sh` | semua cek inti lulus (kecuali `exceptiongroup` — hanya karena host uji Python 3.13, marker `<3.11`) |
| `check-db.sh` sebelum bootstrap | `tables (0)`, `can_use=True`, `can_create=True` |
| `apply-db-bootstrap.py` | `search_path: dc_schema`, `applied` |
| `check-db.sh` sesudahnya | `tables (11)`, seed benar, `public leaks=0` |
| `ctl.sh start` | `started (pid …)` setelah `/health` 200 |
| `verify.sh` | `OK: http://127.0.0.1:8185` (rc=0) |
| Endpoint | `/api/cases`, `/api/areas`, `/api/sumber-tickets` → 401 tanpa key, 200 dengan key |
| Prefix | `/it-tracker/health` di uvicorn → 404 (sesuai tabel prefix) |
| `watchdog.sh` 2× | kegagalan 1 → `run/watchdog.fail=1`; kegagalan 2 → restart → `/health` 200 |
| `ctl.sh restart` / `stop` | `stopped`→`started`→`running`; setelah stop `/health` 000 |

Sesudah bootstrap, app asli (`main-v1-1.py`) dijalankan terhadap database itu: `/health` → `db.status=ok`, `/api/cases`, `/api/areas`, `/api/sumber-tickets`, `/api/jenis-cases`, `/api/groups`, `/api/solver-contacts`, `/api/reminders/pending` → **200** dengan `X-API-Key` dan **401** tanpa key; `POST /api/solver-contacts` → 201; `alert_state` tetap di `dc_schema`, `public` bersih.

## Lampiran — kalau ada yang gagal

| Gejala | Cek |
|---|---|
| `ctl.sh start` → `GAGAL start` | `tail -40 /apps/www/it-tracker/logs/app.log` |
| `ImportError` saat start | `venv/bin/pip list` vs `requirements.txt`; ulangi 3C dengan `--no-index` |
| `db.status: error` | password DSN, dan `search_path%3Ddc_schema` masih ada di `DATABASE_URL` |
| `/it-tracker/health` 404 | snippet belum di `<VirtualHost _default_:443>` / `-k graceful` belum jalan |
| `/it-tracker/health` 503 | app mati: `deploy/native/ctl.sh status` |
| Semua 200 tapi browser error TLS | B3 — minta sertifikat ber-SAN |
| Watchdog tidak restart | `crontab -l`; `logs/watchdog.log`; `run/watchdog.fail` |
