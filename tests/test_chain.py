"""Tests for chain traversal logic in find_case_by_chain.

Tests the waterfall matching: regex INC → reply langsung → chain traversal.
Mocks the database to test all edge cases.
"""
import sys
import os
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import importlib
main_module = importlib.import_module("main-v1-1")


def _make_mock_db(query_responses):
    """Create mock DB that returns responses in order for execute() calls.
    
    query_responses: list of (sql_pattern, result) tuples.
    Each call to execute() checks if sql_pattern is in the SQL and returns the result.
    """
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)

    call_index = [0]
    results = [r for _, r in query_responses]
    patterns = [p for p, _ in query_responses]

    def execute_side_effect(sql, params=None):
        sql_lower = sql.lower()
        for i, pattern in enumerate(patterns):
            if pattern.lower() in sql_lower:
                mock_cursor.fetchone.return_value = results[i]
                return
        mock_cursor.fetchone.return_value = None

    mock_cursor.execute.side_effect = execute_side_effect
    return mock_conn, mock_cursor


def _make_fetchone_db(values):
    """Create mock DB where fetchone() returns values in sequence."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_cursor.fetchone.side_effect = values
    return mock_conn, mock_cursor


class TestFindByChain:
    """Test find_case_by_chain with various edge cases."""

    def test_direct_reply_to_root(self):
        """Quoted message IS the root case message → source='reply'."""
        root_case = {"id": 1, "case_code": "INC123", "wa_message_id": "root_001"}
        mock_conn, _ = _make_fetchone_db([root_case])

        with patch.object(main_module, "db", return_value=mock_conn):
            case, source = main_module.find_case_by_chain("root_001")
            assert case == root_case
            assert source == "reply"

    def test_chain_depth_one(self):
        """Reply to solver1's message which replies to root → source='chain'."""
        root_case = {"id": 1, "case_code": "INC123", "wa_message_id": "root_001"}
        solver_msg = {"quoted_id": "root_001", "case_id": None}

        # depth 0: root_001 not in cases → lookup in wa_messages → solver_msg
        # depth 1: root_001 IS in cases → found!
        mock_conn, _ = _make_fetchone_db([None, solver_msg, root_case])

        with patch.object(main_module, "db", return_value=mock_conn):
            case, source = main_module.find_case_by_chain("solver1_msg_002")
            assert case == root_case
            assert source == "chain"

    def test_chain_via_intermediate_case_id(self):
        """Intermediate message already has case_id set → shortcut to case."""
        root_case = {"id": 1, "case_code": "INC123", "wa_message_id": "root_001"}
        linked_msg = {"quoted_id": "root_001", "case_id": 1}

        # depth 0: not in cases → in wa_messages with case_id=1 → lookup case by id
        mock_conn, _ = _make_fetchone_db([None, linked_msg, root_case])

        with patch.object(main_module, "db", return_value=mock_conn):
            case, source = main_module.find_case_by_chain("linked_msg")
            assert case == root_case
            assert source == "chain"  # found via wa_messages.case_id, not direct root match

    def test_missing_message_returns_none(self):
        """Message not found in wa_messages → return None."""
        # depth 0: not in cases → not in wa_messages → return None
        mock_conn, _ = _make_fetchone_db([None, None])

        with patch.object(main_module, "db", return_value=mock_conn):
            case, source = main_module.find_case_by_chain("unknown_msg")
            assert case is None
            assert source == ""

    def test_max_depth_stops_traversal(self):
        """Chain longer than MAX_CHAIN_DEPTH → stops and returns None."""
        # 5 depths × 2 queries each = 10 fetchone calls, all returning None or chain
        values = []
        for i in range(5):  # depth 0..4
            values.append(None)  # not in cases
            values.append({"quoted_id": f"msg{i+2}", "case_id": None})  # wa_messages
        mock_conn, _ = _make_fetchone_db(values)

        with patch.object(main_module, "db", return_value=mock_conn):
            case, source = main_module.find_case_by_chain("msg1")
            assert case is None

    def test_circular_reference_stops(self):
        """A→B→A circular ref stopped by MAX_CHAIN_DEPTH."""
        values = [
            None, {"quoted_id": "msg_b", "case_id": None},  # depth 0: A→B
            None, {"quoted_id": "msg_a", "case_id": None},  # depth 1: B→A
            None, {"quoted_id": "msg_b", "case_id": None},  # depth 2: A→B
            None, {"quoted_id": "msg_a", "case_id": None},  # depth 3: B→A
            None, {"quoted_id": "msg_b", "case_id": None},  # depth 4: A→B
        ]
        mock_conn, _ = _make_fetchone_db(values)

        with patch.object(main_module, "db", return_value=mock_conn):
            case, source = main_module.find_case_by_chain("msg_a")
            assert case is None

    def test_null_quoted_id(self):
        """quoted_id is None → loop doesn't execute, returns None."""
        # db() is still called (cursor opened), but no execute() happens
        mock_conn, mock_cursor = _make_fetchone_db([])

        with patch.object(main_module, "db", return_value=mock_conn):
            case, source = main_module.find_case_by_chain(None)
            assert case is None
            assert source == ""
            # execute should NOT be called since cur is None
            mock_cursor.execute.assert_not_called()


class TestParseRule:
    """Test regex-based message parsing."""

    def test_inc_code_extraction(self):
        result = main_module.parse_rule("done INC000023470570 sudah beres")
        assert result["case_code"] == "INC000023470570"
        assert result["status"] == "done"

    def test_inc_case_insensitive(self):
        result = main_module.parse_rule("inc12345678901 proses")
        assert result["case_code"] == "INC12345678901"
        assert result["status"] == "in_progress"

    def test_percentage_extraction(self):
        result = main_module.parse_rule("INC000023470570 progress 75%")
        assert result["case_code"] == "INC000023470570"
        assert result.get("progress") == 75

    def test_done_keywords(self):
        for kw in ["done", "selesai", "beres", "kelar", "solved", "closed", "terkirim", "lurus"]:
            result = main_module.parse_rule(f"case {kw}")
            assert result["status"] == "done", f"Keyword '{kw}' should map to done"

    def test_issue_keywords(self):
        for kw in ["kendala", "gagal", "error", "reject", "stuck", "belum bisa"]:
            result = main_module.parse_rule(f"case {kw}")
            assert result["status"] == "issue", f"Keyword '{kw}' should map to issue"

    def test_in_progress_keywords(self):
        for kw in ["proses", "progress", "diproses", "otw", "dicek", "cek dulu", "follow up", "fu"]:
            result = main_module.parse_rule(f"case {kw}")
            assert result["status"] == "in_progress", f"Keyword '{kw}' should map to in_progress"

    def test_empty_text(self):
        result = main_module.parse_rule("")
        assert result == {}

    def test_no_match(self):
        result = main_module.parse_rule("selamat pagi semua")
        assert result == {}

    def test_priority_done_over_issue(self):
        """done keyword takes priority over issue."""
        result = main_module.parse_rule("done ada kendala juga")
        assert result["status"] == "done"

    def test_priority_issue_over_progress(self):
        """issue keyword takes priority over in_progress."""
        result = main_module.parse_rule("gagal sedang proses")
        assert result["status"] == "issue"

    def test_percentage_cap_at_100(self):
        result = main_module.parse_rule("INC000023470570 150%")
        assert result["progress"] == 100


class TestExtractQuotedId:
    """Test extract_quoted_id helper."""

    def test_reply_to_dict(self):
        p = {"replyTo": {"id": {"_serialized": "msg_123"}}}
        assert main_module.extract_quoted_id(p) == "msg_123"

    def test_reply_to_string(self):
        p = {"replyTo": {"id": "msg_456"}}
        assert main_module.extract_quoted_id(p) == "msg_456"

    def test_quoted_msg_id_fallback(self):
        p = {"_data": {"quotedMsgId": "msg_789"}}
        assert main_module.extract_quoted_id(p) == "msg_789"

    def test_quoted_msg_fallback(self):
        p = {"_data": {"quotedMsg": {"id": {"_serialized": "msg_abc"}}}}
        assert main_module.extract_quoted_id(p) == "msg_abc"

    def test_no_reply(self):
        p = {"body": "hello"}
        assert main_module.extract_quoted_id(p) is None

    def test_empty_reply_to(self):
        p = {"replyTo": {}}
        assert main_module.extract_quoted_id(p) is None


class TestNormId:
    """Test norm_id helper."""

    def test_dict_with_serialized(self):
        assert main_module.norm_id({"_serialized": "abc"}) == "abc"

    def test_string_passthrough(self):
        assert main_module.norm_id("abc") == "abc"

    def test_none_passthrough(self):
        assert main_module.norm_id(None) is None

    def test_dict_without_serialized(self):
        assert main_module.norm_id({"other": "val"}) is None
