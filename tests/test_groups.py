"""Unit tests for multi-group behavior: handle_message group filtering,
group verification (cross-group false-positive prevention), and
group-scoped open_case_codes.
"""
import os
import sys
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import importlib
main_module = importlib.import_module("main-v1-1")

GROUP_A = {"id": 1, "name": "Grup A", "chat_id": "120363001@g.us", "is_active": True}
GROUP_B = {"id": 2, "name": "Grup B", "chat_id": "120363002@g.us", "is_active": True}


def _make_mock_db(fetchone_sequence=None):
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    if fetchone_sequence is not None:
        mock_cursor.fetchone.side_effect = fetchone_sequence
    return mock_conn, mock_cursor


INC_CODE = "INC000023470"  # 9+ digit sesuai INC_RE = \bINC\d{9,}\b


def _handle_message_payload(body="done INC000023470", chat="120363001@g.us"):
    return {
        "id": {"_serialized": "true_123@g.us_ABC"},
        "body": body,
        "from": chat,
        "participant": "6281234567890@c.us",
    }


class TestHandleMessageGroupFilter:
    @pytest.mark.asyncio
    async def test_unknown_group_skipped(self):
        """Message from an untracked group should be skipped."""
        mock_conn, mock_cursor = _make_mock_db(fetchone_sequence=[None])  # group lookup → None
        with patch.object(main_module, "db", return_value=mock_conn), \
             patch.object(main_module, "resolve_contact_name", new_callable=AsyncMock, return_value=None), \
             patch.object(main_module, "store_message") as mock_store, \
             patch.object(main_module, "parse_llm", new_callable=AsyncMock) as mock_llm:
            result = await main_module.handle_message(_handle_message_payload(chat="999999@g.us"))
            assert result is False
            mock_store.assert_not_called()
            mock_llm.assert_not_called()

    @pytest.mark.asyncio
    async def test_from_me_skipped(self):
        """Bot's own messages should be skipped."""
        with patch.object(main_module, "resolve_contact_name", new_callable=AsyncMock, return_value=None):
            result = await main_module.handle_message({**_handle_message_payload(), "fromMe": True})
            assert result is False


class TestHandleMessageGroupVerification:
    @pytest.mark.asyncio
    async def test_rule_match_same_group_linked(self):
        """Case in the same group as the message gets linked."""
        mock_conn, mock_cursor = _make_mock_db(fetchone_sequence=[GROUP_A])
        case = {"id": 10, "case_code": INC_CODE, "group_id": 1}
        with patch.object(main_module, "db", return_value=mock_conn), \
             patch.object(main_module, "resolve_contact_name", new_callable=AsyncMock, return_value=None), \
             patch.object(main_module, "store_message") as mock_store, \
             patch.object(main_module, "find_case_by_code", return_value=case) as mock_find, \
             patch.object(main_module, "link_and_update") as mock_link:
            result = await main_module.handle_message(_handle_message_payload())
            assert result is True
            mock_store.assert_called_once()
            mock_find.assert_called_once_with(INC_CODE)
            mock_link.assert_called_once()
            assert mock_link.call_args[0][0] == 10
            assert mock_link.call_args[0][6] == "rule"  # (case_id, wa_mid, author, body, status, note, source, conf)

    @pytest.mark.asyncio
    async def test_rule_match_other_group_rejected(self):
        """Case owned by another group must NOT be linked (anti false-positive)."""
        mock_conn, mock_cursor = _make_mock_db(fetchone_sequence=[GROUP_A])
        case = {"id": 10, "case_code": INC_CODE, "group_id": 2}  # case milik Grup B
        with patch.object(main_module, "db", return_value=mock_conn), \
             patch.object(main_module, "resolve_contact_name", new_callable=AsyncMock, return_value=None), \
             patch.object(main_module, "store_message"), \
             patch.object(main_module, "find_case_by_code", return_value=case), \
             patch.object(main_module, "link_and_update") as mock_link:
            result = await main_module.handle_message(_handle_message_payload())
            assert result is False
            mock_link.assert_not_called()

    @pytest.mark.asyncio
    async def test_chain_match_other_group_rejected(self):
        """Reply-chain resolving to a case of another group must not link."""
        mock_conn, mock_cursor = _make_mock_db(fetchone_sequence=[GROUP_A])
        case = {"id": 10, "case_code": INC_CODE, "group_id": 2}
        payload = {**_handle_message_payload(body="done mas"),
                   "replyTo": {"id": {"_serialized": "parent_123"}}}
        with patch.object(main_module, "db", return_value=mock_conn), \
             patch.object(main_module, "resolve_contact_name", new_callable=AsyncMock, return_value=None), \
             patch.object(main_module, "store_message"), \
             patch.object(main_module, "find_case_by_chain", return_value=(case, "chain")), \
             patch.object(main_module, "link_and_update") as mock_link:
            result = await main_module.handle_message(payload)
            assert result is False
            mock_link.assert_not_called()

    @pytest.mark.asyncio
    async def test_llm_fallback_scoped_to_group(self):
        """LLM fallback only sees open codes of the message's group."""
        mock_conn, mock_cursor = _make_mock_db(fetchone_sequence=[GROUP_A])
        case = {"id": 10, "case_code": INC_CODE, "group_id": 1}
        ai = {"case_code": INC_CODE, "status": "done", "note": "ok", "confidence": 0.9}
        payload = {**_handle_message_payload(body="sudah beres mas"), "from": "120363001@g.us"}
        with patch.object(main_module, "db", return_value=mock_conn), \
             patch.object(main_module, "resolve_contact_name", new_callable=AsyncMock, return_value=None), \
             patch.object(main_module, "store_message"), \
             patch.object(main_module, "open_case_codes", return_value=[INC_CODE]) as mock_codes, \
             patch.object(main_module, "parse_llm", new_callable=AsyncMock, return_value=ai), \
             patch.object(main_module, "find_case_by_code", return_value=case), \
             patch.object(main_module, "link_and_update"):
            result = await main_module.handle_message(payload)
            assert result is True
            # open_case_codes dipanggil dengan group_id dari chat asal pesan
            mock_codes.assert_called_once_with(1)


class TestOpenCaseCodesScoped:
    def test_open_case_codes_with_group_id(self):
        """open_case_codes(group_id) should add WHERE group_id filter."""
        mock_conn, mock_cursor = _make_mock_db()
        mock_cursor.fetchall.return_value = [{"case_code": "INC111"}, {"case_code": "INC222"}]
        with patch.object(main_module, "db", return_value=mock_conn):
            codes = main_module.open_case_codes(2)
            assert codes == ["INC111", "INC222"]
            sql = mock_cursor.execute.call_args[0][0]
            assert "group_id = %s" in sql
            assert mock_cursor.execute.call_args[0][1] == (2,)

    def test_open_case_codes_without_group_id(self):
        """open_case_codes() without arg should not filter by group."""
        mock_conn, mock_cursor = _make_mock_db()
        mock_cursor.fetchall.return_value = []
        with patch.object(main_module, "db", return_value=mock_conn):
            main_module.open_case_codes()
            sql = mock_cursor.execute.call_args[0][0]
            assert "group_id" not in sql