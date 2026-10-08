# Design: Health Alert → Telegram (2026-10-08)

## 1. Ringkasan

Tambahkan monitoring proaktif untuk service backend Moban FU Case Tracker: cron di VM
memanggil endpoint baru `POST /api/health/alert` setiap 5 menit; endpoint memeriksa
kesehatan detail seluruh service (DB, WAHA HTTP, dan **status tiap session WAHA**),
lalu mengirim alert ke **grup Telegram** saat ada service turun, mengirim ulang setiap
6 jam bila masih down, dan mengirim pesan pemulihan saat kembali normal.

Motivasi: kejadian 2026-10-08 — session WAHA `default` berstatus `FAILED`/`SCAN_QR_CODE`
(bot logout), semua `sendText`/`sendImage` ditolak WAHA 422 (`Session status is not as
expected`), sementara `GET /health` lama tetap menampilkan `"waha":"ok"` karena hanya
mengecek HTTP 200. Healthcheck lama **buta** terhadap kondisi ini.

## 2. Channel alert: Telegram (keputusan)

Alert dikirim ke **Telegram**, bukan grup WA:

- Telegram adalah kanal terpisah dari WAHA — alert tetap terkirim walau WAHA/session
  yang bermasalah (ironi: alert via WA tidak bisa terkirim saat WA-nya mati).
- Channel "pasif": tidak ada pesan uji/heartbeat berkala ke grup; tidak ada spam.
- Channel "pasif" = deteksi berbasis pembacaan status, bukan kirim pesan uji.

Konfigurasi (env, bukan hardcode):

| Env | Contoh | Keterangan |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | `1234567890:AAHf...xyz` | Token bot Telegram (BotFather) |
| `TELEGRAM_CHAT_ID` | `-5326937435` | Chat ID grup tujuan (negatif untuk grup) |
| `HEALTH_ALERT_REMINDER_HOURS` | `6` | Re-alert interval saat service masih down (default 6) |

Jika `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` kosong → `send_telegram` log warning dan
skip (tidak crash, tidak gagalkan endpoint).

## 3. Bagian 1 — `/health` detail

Ubah `GET /health` (main-v1-1.py:2949-2966). Saat ini hanya mengecek HTTP 200 WAHA,
tidak membaca body — penyebab `"waha":"ok"` saat session FAILED.

Respon baru:

```jsonc
{
  "status": "ok" | "degraded",          // "ok" hanya bila SEMUA service sehat
  "timestamp": "2026-10-08T02:30:00Z",
  "uptime_seconds": 1234,
  "db": { "status": "ok" | "error", "error": null | "msg" },
  "waha": {
    "http": { "status": "ok" | "error", "status_code": 200 },
    "sessions": [
      { "name": "default", "status": "WORKING" | "FAILED" | "SCAN_QR_CODE" | ...,
        "push_name": "itsupportjateng" | null,
        "engine_state": "CONNECTED" | null }
    ]
  }
}
```

Aturan:

- Endpoint **tetap HTTP 200** walau `status: degraded` → Docker healthcheck (`curl -f`)
  tetap hijau selama proses app hidup (healthcheck itu untuk app, bukan WAHA).
- `degraded` muncul saat: session WAHA ≠ WORKING, atau WAHA HTTP ≠ 200, atau DB
  `SELECT 1` gagal.
- Tetap tanpa auth (dipakai Docker + monitoring). Tidak menampilkan info sensitif
  (hanya nama bot / pushName, bukan token).
- Logika check dipakai bersama oleh `/health` dan `POST /api/health/alert` — satu
  fungsi `collect_health()` agar kedua endpoint konsisten.

## 4. Bagian 2 — State alert di DB + helper Telegram

**Tabel baru `alert_state`** (dibuat `CREATE TABLE IF NOT EXISTS` di lifespan app —
BUKAN via mount initdb, karena volume DB produksi sudah ada dan schema mount tidak
akan dieksekusi ulang):

```sql
CREATE TABLE IF NOT EXISTS alert_state (
  service             TEXT PRIMARY KEY,      -- 'db', 'waha.http', 'waha.session:default'
  state               TEXT NOT NULL DEFAULT 'ok',  -- 'ok' | 'down'
  detail              TEXT,
  first_seen_down_at  TIMESTAMPTZ,
  last_alert_at       TIMESTAMPTZ,
  last_ok_at          TIMESTAMPTZ,
  alert_count         INT NOT NULL DEFAULT 0,
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**Helper `send_telegram(text)`**:

- POST `https://api.telegram.org/bot<token>/sendMessage` dengan `chat_id` + `text`
  (pakai `httpx`, timeout pendek ~10s).
- Token/chat kosong → log warning + return (skip).
- Error HTTP/network → log warning, **tidak pernah raise** (alert fire-and-forget,
  pola sama dengan `notify_case_reply`).

## 5. Bagian 3 — Endpoint `POST /api/health/alert`

- Auth: `X-API-Key` (verify_api_key) + rate limit konsisten.
- Dipanggil cron setiap 5 menit.

Alur:

1. `collect_health()` → kondisi per service.
2. Untuk tiap service kunci, bandingkan state sekarang dengan `alert_state`:
   - `db`, `waha.http`, `waha.session:<name>` (satu baris per session).
3. Transisi:
   - **ok → down**: kirim 🚨 alert (detail jelas, mis. `WAHA session "default":
     SCAN_QR_CODE — perlu scan QR ulang`), simpan state down.
   - **down → ok**: kirim ✅ pesan pemulihan (termasuk durasi down), reset state.
   - **tetap down** & `now - last_alert_at >= HEALTH_ALERT_REMINDER_HOURS`: kirim ⚠️
     re-alert ("masih down sejak ..."), update `last_alert_at`.
   - **tetap down** < reminder hours: no-op (tidak spam).
4. Return ringkasan:

```jsonc
{ "checked": ["db", "waha.http", "waha.session:default"],
  "alerted":   ["waha.session:default"],
  "recovered": [],
  "realerted": [],
  "noop":      [] }
```

**Kondisi down** (semua dicakup, WAHA paling diperhatikan):

| Service key | Sehat | Down |
|---|---|---|
| `db` | `SELECT 1` OK | query gagal/error |
| `waha.http` | HTTP 200 | HTTP ≠ 200 / timeout / connect error |
| `waha.session:<name>` | `status == "WORKING"` | lainnya: FAILED, SCAN_QR_CODE, STARTING, DISCONNECTED, dst. Label khusus untuk SCAN_QR_CODE ("perlu scan QR ulang") |

## 6. Bagian 4 — Cron wrapper VM + deploy

**`scripts/health-alert-cron.sh`** (tipis, fallback backend-down):

```bash
#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && export $(grep -v '^#' .env | xargs)

RESP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
  -X POST http://localhost:8000/api/health/alert -H "X-API-Key: ${BACKEND_API_KEY:-}")

if [ "$RESP" != "200" ]; then
  curl -s -o /dev/null \
    "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
    --data-urlencode "text=🚨 [STC Tracker] Backend DOWN — health alert tidak merespons (HTTP ${RESP:-timeout})"
fi
```

→ Backend mati pun alert tetap ke Telegram (token dibaca `.env` VM, kirim langsung ke
Bot API). Catatan: `export $(grep ... | xargs)` adalah pola yang SUDAH dipakai
`run.sh` di repo ini (konsisten, walau dikenal rapuh untuk nilai dengan spasi — nilai
env alert tidak mengandung spasi).

**Cron** (VM):

```cron
*/5 * * * * cd ~/stc_tracker && bash scripts/health-alert-cron.sh >> backups/health-alert.log 2>&1
```

**Deploy**: tambahkan env ke `.env.example`, `docker-compose.yml`, dan
`docker-compose.prod.yml`; update `docs/production-runbook.md`.

## 7. Bagian 5 — Testing

File baru `tests/test_health_alert.py` + tambahan di `tests/test_api.py` (pola mengikuti
`tests/test_groups_notif.py` dan `tests/test_api.py` yang ada):

- `/health` detail: session FAILED → `degraded` + `waha.sessions[].status` terisi;
  DB error → `degraded`; semua sehat → `ok`.
- ok→down: `POST /api/health/alert` memanggil Telegram (mock `httpx.AsyncClient.post`)
  + menulis baris `alert_state`.
- down→ok: kirim pesan pemulihan + durasi.
- tetap down < 6 jam → **tidak** kirim ulang; ≥ 6 jam → re-alert.
- `send_telegram` tanpa token → tidak raise.
- Endpoint `POST /api/health/alert` butuh `X-API-Key` (401 bila salah/kosong saat
  BACKEND_API_KEY ter-set).

## 8. Lingkup / Non-lingkup

**Masuk lingkup:**
- `/health` detail (db + waha.http + waha.sessions).
- Tabel `alert_state` + helper `send_telegram`.
- Endpoint `POST /api/health/alert` + transisi alert/pemulihan/re-alert.
- Script cron wrapper + cron line + env baru + dokumentasi.
- Testing lengkap.

**Tidak masuk lingkup (YAGNI / ditunda):**
- Monitor nginx/TLS/disk dari sisi VM (di luar backend; bisa jadi iterasi berikutnya).
- Heartbeat kirim pesan uji berkala ke grup (keputusan: channel pasif).
- Dashboard UI baru untuk status service (cukup `/health` JSON + alert Telegram).
- Auto-relog session WAHA (rekomendasi pemulihan manual via scan QR).

## 9. Keputusan yang sudah diambil

1. Channel: **Telegram** (bukan WA), alasan ironi channel WA.
2. Deteksi: **pasif** — tidak ada heartbeat kirim pesan uji.
3. Arsitektur: **Opsi A** — otak di backend (`POST /api/health/alert`) + cron tipis
   VM dengan fallback backend-down di wrapper shell.
4. Kadensi: cek tiap **5 menit**; re-alert tiap **6 jam** (env
   `HEALTH_ALERT_REMINDER_HOURS`); alert sekali per transisi ok→down, pesan pemulihan
   saat down→ok.
5. `/health` tetap HTTP 200 walau `degraded` (healthcheck Docker untuk app, bukan WAHA).
6. Kondisi down: db error, waha HTTP ≠ 200, session ≠ WORKING (label khusus
   SCAN_QR_CODE).
