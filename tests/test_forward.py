"""Tests for POST /api/cases/{id}/forward (v1.37).

Forward = kirim ulang case yang sudah ada:
- group_id berbeda  → pindah grup (status open, reminder reset, anchor baru)
- group_id kosong/sama → kirim ulang di grup yang sama (edit mention, status tetap)
"""
import os
import sys
import importlib
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
main_module = importlib.import_module("main-v1-1")

GROUP_A = {"id": 1, "name": "Grup A", "chat_id": "120363001@g.us", "is_active": True}
GROUP_B = {"id": 2, "name": "Grup B", "chat_id": "120363002@g.us", "is_active": True}


def _make_mock_db(fetchone_sequence):
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_cursor.fetchone.side_effect = fetchone_sequence
    return mock_conn, mock_cursor


def _case(**over):
    base = {
        "id": 42,
        "case_code": "INC000023470570",
        "case_type": "non_ao",
        "title": "case",
        "status": "in_progress",
        "ack": "READ",
        "group_id": 1,
        "area_id": None,
        "regional_id": None,
        "sumber_ticket_id": 1,
        "jenis_case_id": 2,
        "asal_grapari": None,
        "fields": {
            "ticket_remedy": "INC000023470570",
            "order_id": "ORD-1",
            "no_indihome": "0211234567",
            "detail_case": "lama",
        },
        "mentions": [{"number": "628111111111", "name": "Mas Habib"}],
        "wa_message_id": "old_mid",
        "reminder_count": 3,
        "last_reminder_at": "2026-10-01T00:00:00+00:00",
    }
    base.update(over)
    return base


def _lookup(table, row_id):
    return {"jenis_cases": "Non AO", "sumber_tickets": "STC"}.get(table)


def _client(case_row):
    mock_conn, mock_cursor = _make_mock_db([case_row])
    patches = [
        patch.object(main_module, "db", return_value=mock_conn),
        patch.object(main_module, "_lookup_name", side_effect=_lookup),
        patch.object(main_module, "waha_send", new_callable=AsyncMock,
                     return_value="new_mid"),
    ]
    return TestClient(main_module.app), mock_cursor, patches


def _update_calls(mock_cursor):
    return [c for c in mock_cursor.execute.call_args_list
            if str(c.args[0]).strip().startswith("UPDATE cases")]


def _insert_message_calls(mock_cursor):
    return [c for c in mock_cursor.execute.call_args_list
            if "INSERT INTO wa_messages" in str(c.args[0])]


def _post(case_row, body, qs=""):
    tc, mock_cursor, patches = _client(case_row)
    with patches[0], patches[1], patches[2] as mock_send:
        resp = tc.post(f"/api/cases/42/forward{qs}", json=body)
    return resp, mock_cursor, mock_send


class TestForwardMove:
    def test_move_resets_status_and_reminder(self):
        """Pindah grup: status open, reminder di-reset, anchor & grup diperbarui."""
        with patch.object(main_module, "_get_group", return_value=GROUP_B):
            resp, mock_cursor, mock_send = _post(
                _case(), {"group_id": 2,
                          "mentions": [{"number": "628222222222", "name": "Budi"}]})

        assert resp.status_code == 200
        data = resp.json()
        assert data["is_move"] is True
        assert data["status"] == "open"
        assert data["status_reset"] is True
        assert data["reminder_reset"] is True
        assert data["wa_message_id"] == "new_mid"

        updates = _update_calls(mock_cursor)
        assert len(updates) == 1
        sql = str(updates[0].args[0])
        assert "status = 'open'" in sql
        assert "reminder_count = 0" in sql
        assert "last_reminder_at = NULL" in sql
        params = updates[0].args[1]
        assert params[0] == 2              # group_id baru
        assert params[1] == "new_mid"      # anchor baru
        assert params[6] == 42             # WHERE id

        mock_send.assert_awaited_once()
        kwargs = mock_send.await_args.kwargs
        assert kwargs["chat_id"] == GROUP_B["chat_id"]
        assert kwargs["mentions"] == ["628222222222"]

    def test_move_replaces_mentions(self):
        """Mentions yang dikirim REPLACE, bukan merge dengan yang lama."""
        with patch.object(main_module, "_get_group", return_value=GROUP_B):
            resp, _, _ = _post(
                _case(), {"group_id": 2,
                          "mentions": [{"number": "628222222222", "name": "Budi"}]})

        text = resp.json()["text"]
        assert "@628222222222" in text
        assert "@628111111111" not in text
        assert "punten rekan @628222222222" in text

    def test_move_keeps_fields_and_merges_override(self):
        """fields tanpa override dipertahankan; override partial menimpa satu field."""
        with patch.object(main_module, "_get_group", return_value=GROUP_B):
            resp, mock_cursor, _ = _post(
                _case(), {"group_id": 2, "fields": {"detail_case": "baru"}})

        text = resp.json()["text"]
        assert "Detail Case : baru" in text
        assert "Order ID : ORD-1" in text
        assert "Nomer Indihome : 0211234567" in text

        stored = __import__("json").loads(_update_calls(mock_cursor)[0].args[1][3])
        assert stored["detail_case"] == "baru"
        assert stored["order_id"] == "ORD-1"

    def test_move_records_anchor_message_for_chain(self):
        """Pesan forward disimpan sebagai wa_messages milik case → balasan ter-link."""
        with patch.object(main_module, "_get_group", return_value=GROUP_B):
            resp, mock_cursor, _ = _post(_case(), {"group_id": 2})

        inserts = _insert_message_calls(mock_cursor)
        assert len(inserts) == 1
        assert inserts[0].args[1][0] == "new_mid"
        assert inserts[0].args[1][1] == 42
        assert inserts[0].args[1][2] == resp.json()["text"]

    def test_stored_mentions_used_when_not_provided(self):
        with patch.object(main_module, "_get_group", return_value=GROUP_B):
            resp, _, mock_send = _post(_case(), {"group_id": 2})

        assert "@628111111111" in resp.json()["text"]
        assert mock_send.await_args.kwargs["mentions"] == ["628111111111"]


class TestForwardSameGroup:
    def test_same_group_does_not_reset_status_or_reminder(self):
        """Edit mention di grup yang sama: status & reminder tidak disentuh."""
        with patch.object(main_module, "_get_group", return_value=GROUP_A):
            resp, mock_cursor, _ = _post(
                _case(status="done"),
                {"mentions": [{"number": "628222222222", "name": "Budi"}]})

        assert resp.status_code == 200
        data = resp.json()
        assert data["is_move"] is False
        assert data["status"] == "done"
        assert data["status_reset"] is False
        assert data["reminder_reset"] is False

        sql = str(_update_calls(mock_cursor)[0].args[0])
        assert "status = 'open'" not in sql
        assert "reminder_count" not in sql

    def test_explicit_same_group_id_is_not_a_move(self):
        with patch.object(main_module, "_get_group", return_value=GROUP_A):
            resp, _, _ = _post(_case(), {"group_id": 1})

        assert resp.json()["is_move"] is False
        assert resp.json()["status"] == "in_progress"


class TestForwardMentions:
    def test_typed_mention_is_extracted(self):
        """Token @nomor yang diketik di teks ikut dikirim sebagai mention WAHA."""
        with patch.object(main_module, "_get_group", return_value=GROUP_B):
            resp, _, mock_send = _post(
                _case(), {"group_id": 2, "mentions": [],
                          "fields": {"detail_case": "hub @628333333333 ya"}})

        assert "@628333333333" in resp.json()["text"]
        assert "628333333333" in mock_send.await_args.kwargs["mentions"]


class TestForwardValidation:
    def test_deleted_case_404(self):
        resp, _, mock_send = _post(None, {"group_id": 2})
        assert resp.status_code == 404
        mock_send.assert_not_awaited()

    def test_unknown_group_422(self):
        with patch.object(main_module, "_get_group", return_value=None), \
             patch.object(main_module, "_get_group_any", return_value=None):
            resp, _, mock_send = _post(_case(), {"group_id": 99})
        assert resp.status_code == 422
        assert "99" in resp.json()["detail"]
        mock_send.assert_not_awaited()

    def test_inactive_group_409(self):
        with patch.object(main_module, "_get_group", return_value=None), \
             patch.object(main_module, "_get_group_any", return_value={"id": 9, "name": "Grup Mati"}):
            resp, _, mock_send = _post(_case(), {"group_id": 9})
        assert resp.status_code == 409
        assert "Grup Mati" in resp.json()["detail"]
        mock_send.assert_not_awaited()

    def test_no_active_group_400(self):
        with patch.object(main_module, "_get_group", return_value=None):
            resp, _, mock_send = _post(_case(), {})
        assert resp.status_code == 400
        mock_send.assert_not_awaited()

    def test_dry_run_renders_without_sending_or_writing(self):
        with patch.object(main_module, "_get_group", return_value=GROUP_B):
            resp, mock_cursor, mock_send = _post(_case(), {"group_id": 2}, "?dry_run=true")

        assert resp.status_code == 200
        data = resp.json()
        assert data["dry_run"] is True
        assert data["is_move"] is True
        assert "#STC_Non AO" in data["text"]
        mock_send.assert_not_awaited()
        assert _update_calls(mock_cursor) == []
        assert _insert_message_calls(mock_cursor) == []

    def test_waha_missing_message_id_502_without_db_write(self):
        """WAHA 200 tanpa id → 502, anchor case tidak diganti (tidak set NULL)."""
        mock_conn, mock_cursor = _make_mock_db([_case()])
        with patch.object(main_module, "db", return_value=mock_conn), \
             patch.object(main_module, "_lookup_name", side_effect=_lookup), \
             patch.object(main_module, "waha_send", new_callable=AsyncMock, return_value=None), \
             patch.object(main_module, "_get_group", return_value=GROUP_B):
            resp = TestClient(main_module.app).post(
                "/api/cases/42/forward", json={"group_id": 2})

        assert resp.status_code == 502
        assert _update_calls(mock_cursor) == []
        assert _insert_message_calls(mock_cursor) == []
