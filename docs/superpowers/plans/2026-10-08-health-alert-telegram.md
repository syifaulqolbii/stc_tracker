# Health Alert → Telegram Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tambahkan monitoring proaktif: `GET /health` detail (menangkap status session WAHA) + endpoint `POST /api/health/alert` yang dipanggil cron tiap 5 menit dan mengirim alert Telegram saat service down, re-alert tiap 6 jam, pesan pemulihan saat normal lagi.

**Architecture:** Otak di backend (fungsi `collect_health()` dipakai bersama `/health` dan `POST /api/health/alert`); state transisi disimpan di tabel `alert_state`; kirim via Bot API Telegram (kanal terpisah dari WAHA). Wrapper shell tipis di VM jadi fallback saat backend sendiri down.

**Tech Stack:** Python 3.13, FastAPI, httpx, psycopg (PostgreSQL), pytest + pytest-asyncio, Bash + cron (VM).

## Global Constraints

- Semua teks log & pesan Telegram dalam Bahasa Indonesia (konsisten dengan kode).
- Helper Telegram tidak pernah raise — error di-log, alert fire-and-forget (pola `notify_case_reply`).
- `/health` tetap HTTP 200 walau `status: "degraded"` (Docker healthcheck `curl -f` untuk app, bukan WAHA).
- Env baru: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `HEALTH_ALERT_REMINDER_HOURS` (default 6). Tanpa token/chat → skip kirim, tetap jalan.
- `alert_state` dibuat via `CREATE TABLE IF NOT EXISTS` di lifespan (BUKAN mount initdb — volume DB produksi sudah ada).
- WAHA sessions dibaca dari `GET {WAHA_URL}/api/sessions` (sudah dipakai health lama); status session sehat hanya `"WORKING"`.
- Nama fungsi/kolom: `collect_health()`, `send_telegram(text)`, tabel `alert_state`, kolom `service/state/detail/first_seen_down_at/last_alert_at/last_ok_at/alert_count/updated_at`.

---

### Task 1: `/health` detail — fungsi `collect_health()` + endpoint

**Files:**
- Modify: `main-v1-1.py:2949-2966` (endpoint `health()`), tambah fungsi di dekatnya
- Test: `tests/test_api.py` (kelas `TestHealthCheck`, sekitar baris 361-380) — tambah test baru; buat file baru `tests/test_health_alert.py` untuk helper

**Interfaces:**
- Produces: `async def collect_health() -> dict` — bentuk:
  ```python
  {"status": "ok"|"degraded", "timestamp": iso, "uptime_seconds": int,
   "db": {"status": "ok"|"error", "error": None|str},
   "waha": {"http": {"status": "ok"|"error", "status_code": int|None},
            "sessions": [{"name": str, "status": str, "push_name": str|None, "engine_state": str|None}]}}
  ```
- Consumes: `db()` (sudah ada), `WAHA_URL`, `WAHA_HEADERS`, `WAHA_SESSION` (sudah ada)

- [ ] **Step 1: Tulis failing test** di `tests/test_health_alert.py` (file baru):

```python
"""Unit tests for collect_health() / GET /health detail (2026-10-08)."""
import os, sys
from unittest.mock import patch, MagicMock, AsyncMock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import importlib
main_module = importlib.import_module("main-v1-1")


def _mock_db_exception():
    conn, cur = MagicMock(), MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = cur
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)
    cur.execute.side_effect = RuntimeError("db down")
    return conn


def _waha_resp(status_code=200, sessions=None):
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = sessions if sessions is not None else [
        {"name": "default", "status": "WORKING",
         "me": {"pushName": "itsupportjateng"},
         "engine": {"state": "CONNECTED"}}]
    return r


class TestCollectHealth:
    @pytest.mark.asyncio
    async def test_all_ok(self):
        conn, cur = MagicMock(), MagicMock()
        conn.__enter__ = MagicMock(return_value=conn); conn.__exit__ = MagicMock(return_value=False)
        conn.cursor.return_value = cur
        cur.__enter__ = MagicMock(return_value=cur); cur.__exit__ = MagicMock(return_value=False)
        ac = AsyncMock(); ac.get.return_value = _waha_resp()
        with patch.object(main_module, "db", return_value=conn), \
             patch.object(main_module.httpx, "AsyncClient") as mh:
            mh.return_value.__aenter__ = AsyncMock(return_value=ac)
            mh.return_value.__aexit__ = AsyncMock(return_value=False)
            h = await main_module.collect_health()
        assert h["status"] == "ok"
        assert h["db"]["status"] == "ok"
        assert h["waha"]["http"]["status"] == "ok"
        assert h["waha"]["sessions"][0]["status"] == "WORKING"

    @pytest.mark.asyncio
    async def test_session_failed_degrades(self):
        conn, cur = MagicMock(), MagicMock()
        conn.__enter__ = MagicMock(return_value=conn); conn.__exit__ = MagicMock(return_value=False)
        conn.cursor.return_value = cur
        cur.__enter__ = MagicMock(return_value=cur); cur.__exit__ = MagicMock(return_value=False)
        ac = AsyncMock(); ac.get.return_value = _waha_resp(sessions=[
            {"name": "default", "status": "SCAN_QR_CODE", "me": None, "engine": None}])
        with patch.object(main_module, "db", return_value=conn), \
             patch.object(main_module.httpx, "AsyncClient") as mh:
            mh.return_value.__aenter__ = AsyncMock(return_value=ac)
            mh.return_value.__aexit__ = AsyncMock(return_value=False)
            h = await main_module.collect_health()
        assert h["status"] == "degraded"
        assert h["waha"]["sessions"][0]["status"] == "SCAN_QR_CODE"

    @pytest.mark.asyncio
    async def test_db_error_degrades(self):
        conn = _mock_db_exception()
        with patch.object(main_module, "db", return_value=conn):
            h = await main_module.collect_health()
        assert h["status"] == "degraded"
        assert h["db"]["status"] == "error"
```

Tambahkan 1 test di `tests/test_api.py::TestHealthCheck` (pola test lama baris 362-380):

```python
def test_health_degraded_when_session_failed(self, mock_waha):
    """Session WAHA bukan WORKING -> /health tetap 200 tapi status degraded."""
    mock_conn, mock_cursor = _make_mock_db()
    with patch.object(main_module, "db", return_value=mock_conn):
        mock_get_resp = MagicMock()
        mock_get_resp.status_code = 200
        mock_get_resp.json.return_value = [{"name": "default", "status": "FAILED",
                                            "me": None, "engine": None}]
        mock_async_client = AsyncMock()
        mock_async_client.get.return_value = mock_get_resp
        with patch.object(main_module.httpx, "AsyncClient") as mock_ac:
            mock_ac.return_value.__aenter__ = AsyncMock(return_value=mock_async_client)
            mock_ac.return_value.__aexit__ = AsyncMock(return_value=False)
            tc = TestClient(main_module.app)
            response = tc.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "degraded"
    assert data["waha"]["sessions"][0]["status"] == "FAILED"
```

- [ ] **Step 2: Jalankan test, pastikan FAIL** (gagal karena `collect_health` belum ada)

Run: `python -m pytest tests/test_health_alert.py -v`
Expected: `FAIL` — `AttributeError: module 'main-v1-1' has no attribute 'collect_health'`

- [ ] **Step 3: Implementasi `collect_health()` + ganti `health()`** di `main-v1-1.py` (ganti isi fungsi `health()` baris 2952-2966 dan tambah fungsi baru tepat di atasnya):

```python
def _waha_session_healthy(s: dict) -> bool:
    """Session WAHA sehat hanya jika status == WORKING."""
    return (s.get("status") or "") == "WORKING"


async def collect_health() -> dict:
    """Kumpulkan status detail DB + WAHA (HTTP & tiap session).

    Dipakai bersama oleh GET /health dan POST /api/health/alert supaya kedua
    endpoint punya satu sumber kebenaran. Return dict (lihat test).
    """
    import time as _time
    from datetime import datetime, timezone as _tz
    out = {
        "status": "ok",
        "timestamp": datetime.now(_tz.utc).isoformat(),
        "uptime_seconds": int(_time.time() - _app_start_time),
        "db": {"status": "ok", "error": None},
        "waha": {"http": {"status": "ok", "status_code": None}, "sessions": []},
    }
    try:
        with db() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
    except Exception as e:
        out["db"] = {"status": "error", "error": str(e)[:200]}
        out["status"] = "degraded"
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{WAHA_URL}/api/sessions", headers=WAHA_HEADERS)
            out["waha"]["http"]["status_code"] = r.status_code
            if r.status_code == 200:
                for s in r.json():
                    out["waha"]["sessions"].append({
                        "name": s.get("name"),
                        "status": s.get("status"),
                        "push_name": (s.get("me") or {}).get("pushName"),
                        "engine_state": (s.get("engine") or {}).get("state"),
                    })
                if not any(_waha_session_healthy(s) for s in out["waha"]["sessions"]):
                    out["status"] = "degraded"
            else:
                out["waha"]["http"]["status"] = "error"
                out["status"] = "degraded"
    except Exception as e:
        out["waha"]["http"] = {"status": "error", "status_code": None}
        out["status"] = "degraded"
        out.setdefault("waha", {})["error"] = str(e)[:200]
    return out
```

- [ ] **Step 4: Jalankan test, pastikan PASS**

Run: `python -m pytest tests/test_health_alert.py -v`
Expected: PASS (3 test)

- [ ] **Step 5: Commit**

```bash
git add main-v1-1.py tests/test_health_alert.py tests/test_api.py
git commit -m "feat: health detail — collect_health() menangkap status session WAHA (v1.33)"
```

---

### Task 2: Env + helper `send_telegram()` + tabel `alert_state`

**Files:**
- Modify: `main-v1-1.py` (env di dekat baris 58-63; helper baru di dekat helper WAHA; inisialisasi tabel di `lifespan()` baris 185-189)
- Test: `tests/test_health_alert.py` (tambah kelas)

**Interfaces:**
- Consumes: env yang sudah ada (`WAHA_URL`, `WAHA_HEADERS`), `db()`
- Produces:
  - `TELEGRAM_BOT_TOKEN: str`, `TELEGRAM_CHAT_ID: str`, `HEALTH_ALERT_REMINDER_HOURS: int` (module-level, baca env dengan default)
  - `async def send_telegram(text: str) -> bool` — True jika terkirim/skip-karena-tak-terkonfigurasi, False jika gagal; **tidak pernah raise**
  - Tabel `alert_state` (created di lifespan)

- [ ] **Step 1: Tulis failing test** (tambah ke `tests/test_health_alert.py`):

```python
class TestSendTelegram:
    @pytest.mark.asyncio
    async def test_no_token_skips_without_raise(self):
        with patch.object(main_module, "TELEGRAM_BOT_TOKEN", ""), \
             patch.object(main_module, "TELEGRAM_CHAT_ID", "-5326937435"):
            ok = await main_module.send_telegram("test")
        assert ok is False

    @pytest.mark.asyncio
    async def test_sends_via_bot_api(self):
        resp = MagicMock(); resp.status_code = 200; resp.raise_for_status = MagicMock()
        ac = AsyncMock(); ac.post.return_value = resp
        with patch.object(main_module, "TELEGRAM_BOT_TOKEN", "123:ABC"), \
             patch.object(main_module, "TELEGRAM_CHAT_ID", "-5326937435"), \
             patch.object(main_module.httpx, "AsyncClient") as mh:
            mh.return_value.__aenter__ = AsyncMock(return_value=ac)
            mh.return_value.__aexit__ = AsyncMock(return_value=False)
            ok = await main_module.send_telegram("halo")
        assert ok is True
        url = ac.post.call_args[0][0]
        assert "api.telegram.org/bot123:ABC/sendMessage" in url

    @pytest.mark.asyncio
    async def test_http_error_returns_false(self):
        from unittest.mock import AsyncMock as _A
        class _Resp:
            status_code = 500
            def raise_for_status(self):
                raise RuntimeError("boom")
        ac = _A(); ac.post.return_value = _Resp()
        with patch.object(main_module, "TELEGRAM_BOT_TOKEN", "123:ABC"), \
             patch.object(main_module, "TELEGRAM_CHAT_ID", "-5326937435"), \
             patch.object(main_module.httpx, "AsyncClient") as mh:
            mh.return_value.__aenter__ = AsyncMock(return_value=ac)
            mh.return_value.__aexit__ = AsyncMock(return_value=False)
            ok = await main_module.send_telegram("halo")
        assert ok is False
```

- [ ] **Step 2: Jalankan test, pastikan FAIL** (belum ada `send_telegram`)

Run: `python -m pytest tests/test_health_alert.py::TestSendTelegram -v`
Expected: FAIL

- [ ] **Step 3: Implementasi** — env di dekat baris 58-63:

```python
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
HEALTH_ALERT_REMINDER_HOURS = int(os.getenv("HEALTH_ALERT_REMINDER_HOURS", "6"))
```

Helper (taruh di dekat `waha_send_media`, atau setelah `collect_health`):

```python
async def send_telegram(text: str) -> bool:
    """Kirim pesan ke grup Telegram (kanal alert terpisah dari WAHA).

    Tidak pernah raise: alert bersifat fire-and-forget. Return True jika
    terkirim atau dikonfigurasi kosong (skip), False jika gagal kirim.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID kosong — alert Telegram di-skip")
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            )
            r.raise_for_status()
            return True
    except Exception as e:
        log.warning("Telegram alert gagal: %s", e)
        return False
```

Inisialisasi tabel di `lifespan()` (setelah `_seed_default_group()`):

```python
    _ensure_alert_state_table()


def _ensure_alert_state_table():
    """Buat tabel state alert (idempotent). Dipanggil di lifespan."""
    try:
        with db() as conn, conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS alert_state (
                  service             TEXT PRIMARY KEY,
                  state               TEXT NOT NULL DEFAULT 'ok',
                  detail              TEXT,
                  first_seen_down_at  TIMESTAMPTZ,
                  last_alert_at       TIMESTAMPTZ,
                  last_ok_at          TIMESTAMPTZ,
                  alert_count         INT NOT NULL DEFAULT 0,
                  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
                )""")
            conn.commit()
    except Exception as e:
        log.warning("Failed to ensure alert_state table: %s", e)
```

- [ ] **Step 4: Jalankan test, pastikan PASS**

Run: `python -m pytest tests/test_health_alert.py::TestSendTelegram -v`
Expected: PASS (3 test)

- [ ] **Step 5: Commit**

```bash
git add main-v1-1.py tests/test_health_alert.py
git commit -m "feat: send_telegram helper + alert_state table (health alert, v1.33)"
```

---

### Task 3: Endpoint `POST /api/health/alert` + transisi state

**Files:**
- Modify: `main-v1-1.py` (tambah endpoint setelah `health()` atau di bagian System)
- Test: `tests/test_health_alert.py`

**Interfaces:**
- Consumes: `collect_health()` (Task 1), `send_telegram()` + env (Task 2), tabel `alert_state` (Task 2), `verify_api_key`, `check_rate_limit` (sudah ada)
- Produces: endpoint `POST /api/health/alert` → `{checked, alerted, recovered, realerted, noop}`

- [ ] **Step 1: Tulis failing test** (tambah kelas di `tests/test_health_alert.py`):

```python
def _alert_client(fetchone_seq=None, fetchall_rows=None):
    """Buat TestClient dengan DB mock untuk POST /api/health/alert."""
    from fastapi.testclient import TestClient
    from unittest.mock import MagicMock, patch
    conn, cur = MagicMock(), MagicMock()
    conn.__enter__ = MagicMock(return_value=conn); conn.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = cur
    cur.__enter__ = MagicMock(return_value=cur); cur.__exit__ = MagicMock(return_value=False)
    if fetchone_seq is not None:
        cur.fetchone.side_effect = fetchone_seq
    if fetchall_rows is not None:
        cur.fetchall.return_value = fetchall_rows
    stack = patch.object(main_module, "db", return_value=conn)
    tc = TestClient(main_module.app)
    return tc, cur, stack


class TestHealthAlertEndpoint:
    def test_requires_api_key(self, mock_waha):
        """Saat BACKEND_API_KEY ter-set, tanpa key -> 401."""
        tc, _, st = _alert_client()
        with st, patch.object(main_module, "BACKEND_API_KEY", "secret-1"):
            r = tc.post("/api/health/alert")
        assert r.status_code == 401

    def test_ok_to_down_sends_alert(self, mock_waha):
        """Transisi ok->down: kirim Telegram (mock) + simpan state."""
        tc, cur, st = _alert_client(fetchone_seq=[None])  # tidak ada row state lama
        with st, \
             patch.object(main_module, "collect_health", new_callable=AsyncMock) as mh, \
             patch.object(main_module, "send_telegram", new_callable=AsyncMock) as ms:
            mh.return_value = {
                "status": "degraded", "timestamp": "2026-10-08T03:00:00Z",
                "uptime_seconds": 10,
                "db": {"status": "ok", "error": None},
                "waha": {"http": {"status": "ok", "status_code": 200},
                         "sessions": [{"name": "default", "status": "SCAN_QR_CODE",
                                       "push_name": None, "engine_state": None}]},
            }
            r = tc.post("/api/health/alert")
        assert r.status_code == 200
        body = r.json()
        assert body["alerted"] == ["waha.session:default"]
        ms.assert_awaited_once()
        text = ms.call_args[0][0]
        assert "SCAN_QR_CODE" in text

    def test_still_down_within_reminder_no_resend(self, mock_waha):
        """Tetap down < 6 jam -> tidak kirim ulang (noop)."""
        tc, cur, st = _alert_client(fetchone_seq=[{
            "service": "waha.session:default", "state": "down",
            "detail": "FAILED", "first_seen_down_at": None,
            "last_alert_at": None, "last_ok_at": None, "alert_count": 1}])
        with st, \
             patch.object(main_module, "collect_health", new_callable=AsyncMock) as mh, \
             patch.object(main_module, "send_telegram", new_callable=AsyncMock) as ms:
            mh.return_value = {
                "status": "degraded", "timestamp": "2026-10-08T03:00:00Z",
                "uptime_seconds": 10,
                "db": {"status": "ok", "error": None},
                "waha": {"http": {"status": "ok", "status_code": 200},
                         "sessions": [{"name": "default", "status": "FAILED",
                                       "push_name": None, "engine_state": None}]},
            }
            r = tc.post("/api/health/alert")
        assert r.status_code == 200
        assert r.json()["noop"] == ["waha.session:default"]
        ms.assert_not_awaited()
```

- [ ] **Step 2: Jalankan test, pastikan FAIL** (endpoint belum ada → 404/405)

Run: `python -m pytest tests/test_health_alert.py::TestHealthAlertEndpoint -v`
Expected: FAIL

- [ ] **Step 3: Implementasi endpoint** (tambah setelah fungsi `health()`):

```python
from datetime import datetime, timedelta, timezone as _tz


def _service_keys(h: dict) -> list[str]:
    keys = ["db", "waha.http"]
    for s in h.get("waha", {}).get("sessions", []):
        keys.append(f"waha.session:{s.get('name') or '?'}")
    return keys


def _service_state(h: dict, key: str) -> tuple[str, str]:
    """Return (state, detail) untuk satu service key."""
    if key == "db":
        ok = h["db"]["status"] == "ok"
        return ("ok", None) if ok else ("down", h["db"].get("error") or "db error")
    if key == "waha.http":
        ok = h["waha"]["http"]["status"] == "ok"
        return ("ok", None) if ok else ("down", f"HTTP {h['waha']['http'].get('status_code')}")
    # waha.session:<name>
    name = key.split(":", 1)[1]
    for s in h["waha"]["sessions"]:
        if (s.get("name") or "?") == name:
            if s.get("status") == "WORKING":
                return ("ok", None)
            if s.get("status") == "SCAN_QR_CODE":
                return ("down", "SCAN_QR_CODE — perlu scan QR ulang")
            return ("down", s.get("status") or "unknown")
    return ("down", "session tidak ada di response")


async def run_health_alert() -> dict:
    """Check kesehatan, bandingkan dengan alert_state, kirim Telegram sesuai transisi."""
    h = await collect_health()
    result = {"checked": _service_keys(h), "alerted": [], "recovered": [],
              "realerted": [], "noop": []}
    now = datetime.now(_tz.utc)
    with db() as conn, conn.cursor() as cur:
        for key in result["checked"]:
            state, detail = _service_state(h, key)
            cur.execute("SELECT * FROM alert_state WHERE service = %s", (key,))
            row = cur.fetchone()
            if state == "ok":
                if row and row["state"] == "down":
                    dur = ""
                    if row.get("first_seen_down_at"):
                        mins = int((now - row["first_seen_down_at"]).total_seconds() // 60)
                        dur = f" (down ±{mins} menit)"
                    await send_telegram(
                        f"✅ [STC Tracker] {key} pulih{dur} — service normal kembali.")
                    cur.execute(
                        """UPDATE alert_state SET state='ok', detail=NULL,
                           last_ok_at=now(), updated_at=now() WHERE service=%s""", (key,))
                    result["recovered"].append(key)
                continue
            # state == down
            if not row:
                await send_telegram(
                    f"🚨 [STC Tracker] {key} DOWN: {detail}")
                cur.execute(
                    """INSERT INTO alert_state (service, state, detail, first_seen_down_at,
                       last_alert_at, alert_count)
                       VALUES (%s,'down',%s,now(),now(),1)
                       ON CONFLICT (service) DO UPDATE
                         SET state='down', detail=EXCLUDED.detail,
                             first_seen_down_at=COALESCE(alert_state.first_seen_down_at, now()),
                             last_alert_at=now(), alert_count=alert_state.alert_count+1,
                             updated_at=now()""",
                    (key, detail))
                result["alerted"].append(key)
            elif row["state"] == "down":
                remind_h = HEALTH_ALERT_REMINDER_HOURS
                last_alert = row.get("last_alert_at")
                if last_alert is None or (now - last_alert) >= timedelta(hours=remind_h):
                    await send_telegram(
                        f"⚠️ [STC Tracker] {key} MASIH down sejak "
                        f"{row.get('first_seen_down_at') or '?'} — {detail}")
                    cur.execute(
                        """UPDATE alert_state SET detail=%s, last_alert_at=now(),
                           alert_count=alert_count+1, updated_at=now() WHERE service=%s""",
                        (detail, key))
                    result["realerted"].append(key)
                else:
                    result["noop"].append(key)
        conn.commit()
    return result


@app.post("/api/health/alert", tags=["System"],
          summary="Cek kesehatan service & kirim alert Telegram",
          description="Dipanggil cron tiap 5 menit. Kirim Telegram saat service turun, "
                      "re-alert tiap HEALTH_ALERT_REMINDER_HOURS, pesan pemulihan saat normal.")
async def health_alert_endpoint(request: Request,
                                _auth: str = Depends(verify_api_key),
                                _rate: None = Depends(check_rate_limit)):
    return await run_health_alert()
```

- [ ] **Step 4: Jalankan test, pastikan PASS**

Run: `python -m pytest tests/test_health_alert.py -v`
Expected: PASS semua test di file (collect_health + send_telegram + endpoint)

- [ ] **Step 5: Commit**

```bash
git add main-v1-1.py tests/test_health_alert.py
git commit -m "feat: POST /api/health/alert — alert/pemulihan/re-alert via Telegram (v1.33)"
```

---

### Task 4: Env di compose + `.env.example` + runbook

**Files:**
- Modify: `.env.example`, `docker-compose.yml:16-33`, `docker-compose.prod.yml:12-29`
- Modify: `docs/production-runbook.md` (tambah bagian monitoring)

- [ ] **Step 1: `.env.example`** — tambah setelah blok WAHA (baris ~16):

```bash
# Telegram alert (health monitoring) — dikirim saat service down
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=-5326937435
# Re-alert interval (jam) saat service masih down
HEALTH_ALERT_REMINDER_HOURS=6
```

- [ ] **Step 2: `docker-compose.yml`** — tambah 3 baris di blok environment app (baris 16-33):

```yaml
      - TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN:-}
      - TELEGRAM_CHAT_ID=${TELEGRAM_CHAT_ID:-}
      - HEALTH_ALERT_REMINDER_HOURS=${HEALTH_ALERT_REMINDER_HOURS:-6}
```

- [ ] **Step 3: `docker-compose.prod.yml`** — tambah 3 baris sama di environment app (baris 12-29).

- [ ] **Step 4: `docs/production-runbook.md`** — tambah sub-bab "Monitoring health → Telegram" di dekat bagian health check (baris ~697): jelaskan endpoint baru, cron line, env, contoh output `/health` degraded.

- [ ] **Step 5: Commit**

```bash
git add .env.example docker-compose.yml docker-compose.prod.yml docs/production-runbook.md
git commit -m "docs: env + compose + runbook untuk health alert Telegram (v1.33)"
```

---

### Task 5: Script cron wrapper + cron line

**Files:**
- Create: `scripts/health-alert-cron.sh`
- Modify: `docs/production-runbook.md` (cron line, sudah ada di Task 4 — finalisasi di sini)

- [ ] **Step 1: Buat script** `scripts/health-alert-cron.sh`:

```bash
#!/usr/bin/env bash
# =============================================================================
# health-alert-cron.sh — panggil POST /api/health/alert tiap 5 menit (cron).
# Kalau backend tidak merespons, kirim alert "Backend DOWN" langsung ke
# Telegram (fallback saat backend sendiri mati).
# Usage:
#   */5 * * * * cd ~/stc_tracker && bash scripts/health-alert-cron.sh >> backups/health-alert.log 2>&1
# =============================================================================
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

- [ ] **Step 2: Jadikan executable + verifikasi syntax**

```bash
chmod +x scripts/health-alert-cron.sh
bash -n scripts/health-alert-cron.sh && echo "syntax OK"
```

- [ ] **Step 3: (Di VM) pasang cron** — instruksi di runbook & manual:

```cron
*/5 * * * * cd ~/stc_tracker && bash scripts/health-alert-cron.sh >> backups/health-alert.log 2>&1
```

Verifikasi di VM: `crontab -l | grep health-alert`

- [ ] **Step 4: Test manual (dry)** — jalankan script sekali, pastikan endpoint 200 & tidak ada pesan Telegram palsu (harus noop karena service sehat).

- [ ] **Step 5: Commit**

```bash
git add scripts/health-alert-cron.sh docs/production-runbook.md
git commit -m "ops: health-alert cron wrapper + fallback backend down (v1.33)"
```

---

### Task 6: Full test suite + verifikasi akhir

**Files:**
- Test: seluruh `tests/`

- [ ] **Step 1: Jalankan seluruh test suite**

Run: `python -m pytest tests/ -q`
Expected: semua PASS (kecuali error lingkungan Windows yang sudah dikenal: `.pytest_cache`/temp permission — abaikan, bukan regresi)

- [ ] **Step 2: Verifikasi `/health` di dev** (opsional, kalau backend lokal jalan)

```bash
curl -s http://localhost:8000/health | python3 -m json.tool
# status ok, waha.sessions[0].status WORKING
```

- [ ] **Step 3: Verifikasi endpoint alert dengan curl (backend jalan, service sehat)**

```bash
curl -s -X POST http://localhost:8000/api/health/alert \
  -H "X-API-Key: $BACKEND_API_KEY" | python3 -m json.tool
# → {"checked": [...], "alerted": [], "recovered": [], "realerted": [], "noop": [...]}
```

- [ ] **Step 4: Simulasi down** (opsional, hati-hati): stop sementara session WAHA `docker restart waha`, jalankan alert → harus `alerted: ["waha.http"...]`/`waha.session` dan pesan 🚨 masuk Telegram; start lagi → run ulang → `recovered`.

- [ ] **Step 5: Commit akhir bila ada perubahan kecil**

```bash
git add -A
git commit -m "test: full suite pass untuk health alert (v1.33)"
```
