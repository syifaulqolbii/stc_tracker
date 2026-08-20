"""Tests for LLM fallback parsing and crawl 2-pass logic."""
import sys
import os
import json
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import importlib
main_module = importlib.import_module("main-v1-1")


class TestStripMarkdownJson:
    def test_plain_json(self):
        assert main_module._strip_markdown_json('{"a": 1}') == '{"a": 1}'

    def test_json_with_fences(self):
        raw = '```json\n{"a": 1}\n```'
        assert main_module._strip_markdown_json(raw) == '{"a": 1}'

    def test_json_with_fences_no_lang(self):
        raw = '```\n{"a": 1}\n```'
        assert main_module._strip_markdown_json(raw) == '{"a": 1}'

    def test_json_with_whitespace(self):
        raw = '  \n  {"a": 1}  \n  '
        assert main_module._strip_markdown_json(raw) == '{"a": 1}'


class TestValidateLlmResponse:
    def test_valid_response(self):
        data = {"case_code": "INC123", "status": "done", "note": "test", "confidence": 0.9}
        result = main_module._validate_llm_response(data)
        assert result["case_code"] == "INC123"
        assert result["status"] == "done"
        assert result["confidence"] == 0.9

    def test_null_case_code(self):
        data = {"case_code": None, "status": None, "confidence": 0.5}
        result = main_module._validate_llm_response(data)
        assert result["case_code"] is None

    def test_invalid_status_becomes_none(self):
        data = {"case_code": "INC123", "status": "invalid_status"}
        result = main_module._validate_llm_response(data)
        assert result["status"] is None

    def test_confidence_capped_at_1(self):
        data = {"confidence": 1.5}
        result = main_module._validate_llm_response(data)
        assert result["confidence"] == 1.0

    def test_confidence_floored_at_0(self):
        data = {"confidence": -0.5}
        result = main_module._validate_llm_response(data)
        assert result["confidence"] == 0.0

    def test_non_dict_returns_none(self):
        assert main_module._validate_llm_response("not a dict") is None
        assert main_module._validate_llm_response(None) is None

    def test_non_string_case_code_returns_none(self):
        data = {"case_code": 123}
        assert main_module._validate_llm_response(data) is None

    def test_note_must_be_string(self):
        data = {"note": 123}
        result = main_module._validate_llm_response(data)
        assert result["note"] is None

    def test_confidence_non_numeric_returns_none(self):
        data = {"confidence": "high"}
        result = main_module._validate_llm_response(data)
        assert result["confidence"] is None


class TestParseLlm:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_api_key(self):
        with patch.object(main_module, "OPENROUTER_API_KEY", ""):
            result = await main_module.parse_llm("test", ["INC123"])
            assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_open_codes(self):
        with patch.object(main_module, "OPENROUTER_API_KEY", "key"):
            result = await main_module.parse_llm("test", [])
            assert result is None

    @pytest.mark.asyncio
    async def test_successful_parse(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": '{"case_code": "INC123", "status": "done", "confidence": 0.9}'}}]
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(main_module, "OPENROUTER_API_KEY", "key"), \
             patch.object(main_module.httpx, "AsyncClient") as mock_client:
            async_client = AsyncMock()
            async_client.post.return_value = mock_response
            mock_client.return_value.__aenter__ = AsyncMock(return_value=async_client)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await main_module.parse_llm("done INC123", ["INC123"])
            assert result["case_code"] == "INC123"
            assert result["status"] == "done"

    @pytest.mark.asyncio
    async def test_retries_on_500_error(self):
        """Should retry on server errors (5xx)."""
        error_response = MagicMock()
        error_response.status_code = 500

        success_response = MagicMock()
        success_response.status_code = 200
        success_response.json.return_value = {
            "choices": [{"message": {"content": '{"case_code": "INC123", "status": "done", "confidence": 0.9}'}}]
        }
        success_response.raise_for_status = MagicMock()

        call_count = [0]

        async def mock_post(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("Server error")
            return success_response

        with patch.object(main_module, "OPENROUTER_API_KEY", "key"), \
             patch.object(main_module, "LLM_MAX_RETRIES", 3), \
             patch.object(main_module, "LLM_RETRY_DELAY", 0.01), \
             patch.object(main_module.httpx, "AsyncClient") as mock_client:
            async_client = AsyncMock()
            async_client.post = mock_post
            mock_client.return_value.__aenter__ = AsyncMock(return_value=async_client)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await main_module.parse_llm("done INC123", ["INC123"])
            assert result["case_code"] == "INC123"
            assert call_count[0] == 2  # retried once

    @pytest.mark.asyncio
    async def test_no_retry_on_400_error(self):
        """Should NOT retry on client errors (4xx)."""
        error = Exception("Bad request")

        with patch.object(main_module, "OPENROUTER_API_KEY", "key"), \
             patch.object(main_module.httpx, "AsyncClient") as mock_client:
            async_client = AsyncMock()

            async def mock_post(*args, **kwargs):
                raise error

            async_client.post = mock_post
            mock_client.return_value.__aenter__ = AsyncMock(return_value=async_client)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await main_module.parse_llm("test", ["INC123"])
            assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_invalid_json(self):
        """Should return None on invalid JSON from LLM."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "this is not json"}}]
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(main_module, "OPENROUTER_API_KEY", "key"), \
             patch.object(main_module, "LLM_MAX_RETRIES", 1), \
             patch.object(main_module, "LLM_RETRY_DELAY", 0.01), \
             patch.object(main_module.httpx, "AsyncClient") as mock_client:
            async_client = AsyncMock()
            async_client.post.return_value = mock_response
            mock_client.return_value.__aenter__ = AsyncMock(return_value=async_client)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await main_module.parse_llm("test", ["INC123"])
            assert result is None


class TestCrawlGroup:
    @pytest.mark.asyncio
    async def test_crawl_2_pass(self):
        """Crawl stores all messages first, then processes."""
        msgs = [
            {"id": {"_serialized": "msg1"}, "body": "done INC123", "from": "group@g.us"},
            {"id": {"_serialized": "msg2"}, "body": "thanks", "from": "group@g.us"},
        ]

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)

        with patch.object(main_module, "db", return_value=mock_conn), \
             patch.object(main_module.httpx, "AsyncClient") as mock_http:
            async_client = AsyncMock()
            get_resp = MagicMock()
            get_resp.status_code = 200
            get_resp.json.return_value = msgs
            get_resp.raise_for_status = MagicMock()
            async_client.get.return_value = get_resp
            mock_http.return_value.__aenter__ = AsyncMock(return_value=async_client)
            mock_http.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await main_module.crawl_group(limit=2)
            assert result["fetched"] == 2
            assert result["stored"] == 2

    @pytest.mark.asyncio
    async def test_crawl_handles_store_error(self):
        """Crawl continues even if one message fails to store."""
        msgs = [
            {"id": {"_serialized": "msg1"}, "body": "test", "from": "group@g.us"},
            {"id": {"_serialized": "msg2"}, "body": "test2", "from": "group@g.us"},
        ]

        call_count = [0]

        def mock_store(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("DB error")

        with patch.object(main_module, "db") as mock_db, \
             patch.object(main_module, "store_message", side_effect=mock_store), \
             patch.object(main_module.httpx, "AsyncClient") as mock_http:
            async_client = AsyncMock()
            get_resp = MagicMock()
            get_resp.status_code = 200
            get_resp.json.return_value = msgs
            get_resp.raise_for_status = MagicMock()
            async_client.get.return_value = get_resp
            mock_http.return_value.__aenter__ = AsyncMock(return_value=async_client)
            mock_http.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await main_module.crawl_group(limit=2)
            assert result["fetched"] == 2
            assert result["stored"] == 1  # only 1 stored successfully
            assert result["store_errors"] == 1

    @pytest.mark.asyncio
    async def test_crawl_returns_error_counts(self):
        """Crawl response includes error counts."""
        with patch.object(main_module, "db") as mock_db, \
             patch.object(main_module.httpx, "AsyncClient") as mock_http:
            async_client = AsyncMock()
            get_resp = MagicMock()
            get_resp.status_code = 200
            get_resp.json.return_value = []
            get_resp.raise_for_status = MagicMock()
            async_client.get.return_value = get_resp
            mock_http.return_value.__aenter__ = AsyncMock(return_value=async_client)
            mock_http.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await main_module.crawl_group(limit=100)
            assert "store_errors" in result
            assert "process_errors" in result
            assert result["store_errors"] == 0
            assert result["process_errors"] == 0
