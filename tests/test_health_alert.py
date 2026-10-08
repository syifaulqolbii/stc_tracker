"""Unit tests for collect_health() / GET /health detail (2026-10-08)."""
import pytest
import os, sys
from datetime import datetime, timezone, timedelta
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


@pytest.fixture
def mock_waha():
    """Mock WAHA HTTP client (fixture lokal — tidak ada conftest.py)."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"id": {"_serialized": "test_msg_123"}}
    mock_response.raise_for_status = MagicMock()
    with patch.object(main_module.httpx, "AsyncClient") as mock_client:
        async_client = AsyncMock()
        async_client.post.return_value = mock_response
        mock_client.return_value.__aenter__ = AsyncMock(return_value=async_client)
        mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
        yield async_client


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    """Bersihkan rate limiter in-memory antar test (pola test_api.py)."""
    main_module._rate_buckets.clear()
    yield
    main_module._rate_buckets.clear()


def _alert_client(fetchone_seq=None, fetchall_rows=None):
    """Buat TestClient dengan DB mock untuk POST /api/health/alert."""
    from fastapi.testclient import TestClient
    from unittest.mock import MagicMock, patch
    conn, cur = MagicMock(), MagicMock()
    conn.__enter__ = MagicMock(return_value=conn); conn.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = cur
    cur.__enter__ = MagicMock(return_value=cur); cur.__exit__ = MagicMock(return_value=False)
    if fetchone_seq is not None:
        # JANGAN side_effect=[None] — dipanggil berulang (sekali per service key);
        # pad 40 slot None supaya tidak StopIteration di call ke-4 dst.
        cur.fetchone.side_effect = list(fetchone_seq) + [None] * 40
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
        last_alert_at = datetime.now(timezone.utc) - timedelta(hours=1)  # alert terakhir < 6 jam lalu
        tc, cur, st = _alert_client(fetchone_seq=[None, None, {
            "service": "waha.session:default", "state": "down",
            "detail": "FAILED", "first_seen_down_at": None,
            "last_alert_at": last_alert_at, "last_ok_at": None, "alert_count": 1}])
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
