"""Integration tests for Moban FU Tracker API endpoints.

These tests mock the database and WAHA to allow testing without running services.
"""
import os
import sys
import json
import pytest
from unittest.mock import patch, MagicMock, AsyncMock, call
from fastapi.testclient import TestClient

# Add parent directory to path and import main module
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import importlib
main_module = importlib.import_module("main-v1-1")


@pytest.fixture
def mock_waha():
    """Mock WAHA HTTP client."""
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


def _make_mock_db(fetchone_sequence=None):
    """Create a mock DB with configurable fetchone return sequence.
    
    Mimics psycopg context manager behavior:
    - db() returns conn, conn.__enter__() returns conn itself
    - conn.cursor() returns cur, cur.__enter__() returns cur itself
    """
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    
    # psycopg: with db() as conn → conn is the connection itself
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    
    # psycopg: conn.cursor() returns cursor
    mock_conn.cursor.return_value = mock_cursor
    
    # psycopg: with conn.cursor() as cur → cur is the cursor itself
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)

    if fetchone_sequence is not None:
        mock_cursor.fetchone.side_effect = fetchone_sequence

    return mock_conn, mock_cursor


@pytest.fixture
def client(mock_waha):
    """Create test client with mocked WAHA."""
    mock_conn, mock_cursor = _make_mock_db()
    with patch.object(main_module, "db", return_value=mock_conn):
        yield TestClient(main_module.app), mock_cursor


class TestCreateCase:
    def test_create_stc_case(self, client):
        tc, mock_cursor = client
        # Sequence: INSERT RETURNING -> INSERT INTO wa_messages
        mock_cursor.fetchone.side_effect = [
            {"id": 42, "case_code": "INC000023470570"},  # RETURNING id, case_code
            None,  # INSERT wa_messages (no return)
        ]

        response = tc.post("/api/cases", json={
            "case_type": "stc",
            "fields": {
                "ticket_remedy": "INC000023470570",
                "no_indihome": "142401135588",
                "detail_case": "Test moban case",
            },
            "mentions": [{"number": "6281234567890", "name": "Mas Budi"}],
        })
        assert response.status_code == 201
        data = response.json()
        assert data["case_code"] == "INC000023470570"
        assert data["wa_message_id"] == "test_msg_123"
        assert "punten rekan" in data["text"]
        assert "@Mas Budi" in data["text"]
        assert "#STC" in data["text"]

    def test_create_smooa_case(self, client):
        tc, mock_cursor = client
        mock_cursor.fetchone.side_effect = [
            {"id": 43, "case_code": "INC999"},
            None,
        ]

        response = tc.post("/api/cases", json={
            "case_type": "smooa",
            "fields": {
                "grapari": "GraPARI Bandung",
                "ticket_remedy": "INC999",
                "nama_pelanggan": "Budi Santoso",
            },
        })
        assert response.status_code == 201
        data = response.json()
        assert data["case_code"] == "INC999"
        assert "#SMOOA" in data["text"]
        assert "GraPARI : GraPARI Bandung" in data["text"]

    def test_create_mobile_case(self, client):
        tc, mock_cursor = client
        mock_cursor.fetchone.side_effect = [
            {"id": 44, "case_code": None},
            None,
        ]

        response = tc.post("/api/cases", json={
            "case_type": "mobile",
            "fields": {
                "grapari": "GraPARI Jakarta",
                "msisdn": "6281234567890",
            },
        })
        assert response.status_code == 201
        data = response.json()
        assert "#Case Mobile" in data["text"]

    def test_create_ufo_case(self, client):
        tc, mock_cursor = client
        mock_cursor.fetchone.side_effect = [
            {"id": 45, "case_code": None},
            None,
        ]

        response = tc.post("/api/cases", json={
            "case_type": "ufo",
            "fields": {
                "case_id": "UFO-123",
                "detail_case": "UFO test case",
            },
        })
        assert response.status_code == 201
        data = response.json()
        assert "#UFO" in data["text"]
        assert "Case ID : UFO-123" in data["text"]

    def test_create_case_invalid_type_downgrades(self, client):
        tc, mock_cursor = client
        mock_cursor.fetchone.side_effect = [
            {"id": 46, "case_code": None},
            None,
        ]

        response = tc.post("/api/cases", json={
            "case_type": "invalid_type",
            "fields": {"detail_case": "Test"},
        })
        assert response.status_code == 201
        data = response.json()
        assert "#Lainnya" in data["text"]

    def test_create_case_no_mentions(self, client):
        tc, mock_cursor = client
        mock_cursor.fetchone.side_effect = [
            {"id": 47, "case_code": None},
            None,
        ]

        response = tc.post("/api/cases", json={
            "case_type": "stc",
            "fields": {"ticket_remedy": "INC123"},
        })
        assert response.status_code == 201
        data = response.json()
        assert "punten mas" not in data["text"]
        assert "#STC" in data["text"]

    def test_create_case_empty_fields(self, client):
        tc, mock_cursor = client
        mock_cursor.fetchone.side_effect = [
            {"id": 48, "case_code": None},
            None,
        ]

        response = tc.post("/api/cases", json={
            "case_type": "stc",
            "fields": {},
        })
        assert response.status_code == 201
        data = response.json()
        assert "#STC" in data["text"]


class TestHealthCheck:
    def test_health_returns_ok(self, mock_waha):
        mock_conn, mock_cursor = _make_mock_db()
        # No fetchone calls needed for health check (just SELECT 1)

        with patch.object(main_module, "db", return_value=mock_conn):
            with patch.object(main_module.httpx, "get") as mock_get:
                mock_get.return_value = MagicMock(status_code=200)
                tc = TestClient(main_module.app)

                response = tc.get("/health")
                assert response.status_code == 200
                data = response.json()
                assert data["status"] == "ok"
                assert data["db"] == "ok"
                assert data["waha"] == "ok"


class TestCaseDetail:
    def test_case_detail_returns_404(self, mock_waha):
        mock_conn, mock_cursor = _make_mock_db(fetchone_sequence=[None])

        with patch.object(main_module, "db", return_value=mock_conn):
            tc = TestClient(main_module.app)
            response = tc.get("/api/cases/99999")
            assert response.status_code == 404


class TestSetStatus:
    def test_set_status_returns_404_if_not_found(self, mock_waha):
        mock_conn, mock_cursor = _make_mock_db(fetchone_sequence=[None])

        with patch.object(main_module, "db", return_value=mock_conn):
            tc = TestClient(main_module.app)
            response = tc.post("/api/cases/99999/status", json={
                "status": "done",
                "note": "test",
            })
            assert response.status_code == 404


class TestAuth:
    def test_no_api_key_rejected_when_configured(self, mock_waha):
        """When BACKEND_API_KEY is set, requests without key should be 401."""
        mock_conn, mock_cursor = _make_mock_db()
        with patch.object(main_module, "db", return_value=mock_conn), \
             patch.object(main_module, "BACKEND_API_KEY", "secret-key-123"):
            tc = TestClient(main_module.app)
            response = tc.get("/api/cases")
            assert response.status_code == 401
            assert "API key" in response.json()["detail"]

    def test_wrong_api_key_rejected(self, mock_waha):
        mock_conn, mock_cursor = _make_mock_db()
        with patch.object(main_module, "db", return_value=mock_conn), \
             patch.object(main_module, "BACKEND_API_KEY", "secret-key-123"):
            tc = TestClient(main_module.app)
            response = tc.get("/api/cases", headers={"X-API-Key": "wrong-key"})
            assert response.status_code == 401

    def test_correct_api_key_accepted(self, mock_waha):
        mock_conn, mock_cursor = _make_mock_db(fetchone_sequence=[[]])
        with patch.object(main_module, "db", return_value=mock_conn), \
             patch.object(main_module, "BACKEND_API_KEY", "secret-key-123"):
            tc = TestClient(main_module.app)
            response = tc.get("/api/cases", headers={"X-API-Key": "secret-key-123"})
            assert response.status_code == 200

    def test_no_auth_required_when_key_not_configured(self, mock_waha):
        """When BACKEND_API_KEY is empty, all requests should pass."""
        mock_conn, mock_cursor = _make_mock_db(fetchone_sequence=[[]])
        with patch.object(main_module, "db", return_value=mock_conn), \
             patch.object(main_module, "BACKEND_API_KEY", ""):
            tc = TestClient(main_module.app)
            response = tc.get("/api/cases")
            assert response.status_code == 200

    def test_health_never_requires_auth(self, mock_waha):
        mock_conn, mock_cursor = _make_mock_db()
        with patch.object(main_module, "db", return_value=mock_conn), \
             patch.object(main_module.httpx, "get") as mock_get, \
             patch.object(main_module, "BACKEND_API_KEY", "secret-key-123"):
            mock_get.return_value = MagicMock(status_code=200)
            tc = TestClient(main_module.app)
            response = tc.get("/health")
            assert response.status_code == 200

    def test_webhook_never_requires_auth(self, mock_waha):
        mock_conn, mock_cursor = _make_mock_db()
        with patch.object(main_module, "db", return_value=mock_conn), \
             patch.object(main_module, "BACKEND_API_KEY", "secret-key-123"):
            tc = TestClient(main_module.app)
            response = tc.post("/webhooks/waha", json={"event": "session.status"})
            assert response.status_code == 200
            assert response.json()["ok"] is True


class TestWebhook:
    def test_webhook_message_event(self, mock_waha):
        mock_conn, mock_cursor = _make_mock_db()
        with patch.object(main_module, "db", return_value=mock_conn), \
             patch.object(main_module, "handle_message", new_callable=AsyncMock) as mock_handle:
            tc = TestClient(main_module.app)
            response = tc.post("/webhooks/waha", json={
                "event": "message",
                "payload": {
                    "id": {"_serialized": "true_123@g.us_ABC"},
                    "body": "done INC123",
                    "from": "120363xxx@g.us",
                },
            })
            assert response.status_code == 200
            mock_handle.assert_called_once()

    def test_webhook_ack_event(self, mock_waha):
        mock_conn, mock_cursor = _make_mock_db()
        with patch.object(main_module, "db", return_value=mock_conn), \
             patch.object(main_module, "handle_ack") as mock_ack:
            tc = TestClient(main_module.app)
            response = tc.post("/webhooks/waha", json={
                "event": "message.ack",
                "payload": {
                    "id": {"_serialized": "true_123@g.us_ABC"},
                    "ackName": "READ",
                },
            })
            assert response.status_code == 200
            mock_ack.assert_called_once()

    def test_webhook_unknown_event_ignored(self, mock_waha):
        mock_conn, mock_cursor = _make_mock_db()
        with patch.object(main_module, "db", return_value=mock_conn):
            tc = TestClient(main_module.app)
            response = tc.post("/webhooks/waha", json={
                "event": "session.status",
                "payload": {"status": "WORKING"},
            })
            assert response.status_code == 200

    def test_webhook_missing_event_returns_400(self, mock_waha):
        mock_conn, mock_cursor = _make_mock_db()
        with patch.object(main_module, "db", return_value=mock_conn):
            tc = TestClient(main_module.app)
            response = tc.post("/webhooks/waha", json={"payload": {}})
            assert response.status_code == 400

    def test_webhook_invalid_json_returns_400(self, mock_waha):
        mock_conn, mock_cursor = _make_mock_db()
        with patch.object(main_module, "db", return_value=mock_conn):
            tc = TestClient(main_module.app)
            response = tc.post("/webhooks/waha", content="not json", headers={"Content-Type": "application/json"})
            assert response.status_code == 400
