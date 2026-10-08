"""Unit tests for collect_health() / GET /health detail (2026-10-08)."""
import pytest
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
