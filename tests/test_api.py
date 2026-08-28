"""Integration tests for Moban FU Tracker API endpoints.

These tests mock the database and WAHA to allow testing without running services.
Updated for v1.2: new case types, new fields, new lookup endpoints.
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
    """Create test client with mocked WAHA and contact resolution."""
    mock_conn, mock_cursor = _make_mock_db()
    with patch.object(main_module, "db", return_value=mock_conn), \
         patch.object(main_module, "resolve_contact_name", new_callable=AsyncMock, return_value=None):
        yield TestClient(main_module.app), mock_cursor


def _create_case_mock_sequence(
    jenis_case_name="Non Order",
    sumber_ticket_name=None,
    area_name=None,
    regional_name=None,
    case_id=42,
    case_code="INC000023470570",
):
    """Build the fetchone mock sequence for create_case endpoint.
    
    The endpoint makes these DB calls in order:
    1. _resolve_jenis_case(name) → SELECT id FROM jenis_cases WHERE name = %s
       (skipped if name is None — returns early without DB call)
    2. _resolve_sumber_ticket(name) → SELECT id FROM sumber_tickets WHERE name = %s
       (skipped if name is None — returns early without DB call)
    3. Resolve area_name (if area_id) → SELECT name FROM areas WHERE id = %s
    4. Resolve regional_name (if regional_id) → SELECT name FROM regionals WHERE id = %s
    5. INSERT RETURNING → {id, case_code}
    6. INSERT wa_messages → None
    """
    seq = []
    # 1. _resolve_jenis_case (only if name is not None)
    if jenis_case_name:
        seq.append({"id": 1})
    # 2. _resolve_sumber_ticket (only if name is not None)
    if sumber_ticket_name:
        seq.append({"id": 2})
    # 3. area name (only if area_name is provided, meaning area_id was set)
    if area_name:
        seq.append({"name": area_name})
    # 4. regional name (only if regional_name is provided)
    if regional_name:
        seq.append({"name": regional_name})
    # 5. INSERT RETURNING
    seq.append({"id": case_id, "case_code": case_code})
    # 6. INSERT wa_messages
    seq.append(None)
    return seq


class TestCreateCase:
    def test_create_non_order_case(self, client):
        tc, mock_cursor = client
        mock_cursor.fetchone.side_effect = _create_case_mock_sequence(
            jenis_case_name="Non Order",
            sumber_ticket_name="Grapari",
            area_name="Area 1",
            regional_name="Regional 2",
        )

        response = tc.post("/api/cases", json={
            "area_id": 1,
            "regional_id": 2,
            "sumber_ticket": "Grapari",
            "jenis_case": "Non Order",
            "asal_grapari": "GraPARI Bandung",
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
        assert "@6281234567890" in data["text"]
        assert "#Non Order" in data["text"]
        assert "Area : Area 1" in data["text"]
        assert "Regional : Regional 2" in data["text"]
        assert "Sumber Ticket : Grapari" in data["text"]
        assert "Asal Grapari : GraPARI Bandung" in data["text"]

    def test_create_mobile_case(self, client):
        tc, mock_cursor = client
        mock_cursor.fetchone.side_effect = _create_case_mock_sequence(
            jenis_case_name="Mobile",
            sumber_ticket_name=None,
            case_code=None,
        )

        response = tc.post("/api/cases", json={
            "jenis_case": "Mobile",
            "fields": {
                "msisdn": "6281234567890",
                "detail_case": "Mobile test case",
            },
        })
        assert response.status_code == 201
        data = response.json()
        assert "#Mobile" in data["text"]

    def test_create_non_ao_case(self, client):
        tc, mock_cursor = client
        mock_cursor.fetchone.side_effect = _create_case_mock_sequence(
            jenis_case_name="Non AO",
            sumber_ticket_name=None,
            case_code="INC999",
            case_id=45,
        )

        response = tc.post("/api/cases", json={
            "jenis_case": "Non AO",
            "fields": {
                "ticket_remedy": "INC999",
                "detail_case": "Non AO test",
            },
        })
        assert response.status_code == 201
        data = response.json()
        assert data["case_code"] == "INC999"
        assert "#Non AO" in data["text"]

    def test_create_case_invalid_type_downgrades(self, client):
        tc, mock_cursor = client
        # "invalid_type" is not None, so _resolve_jenis_case makes a DB call
        mock_cursor.fetchone.side_effect = _create_case_mock_sequence(
            jenis_case_name="invalid_type",  # truthy → DB call, but not in lookup
            case_code=None,
            case_id=46,
        )

        response = tc.post("/api/cases", json={
            "jenis_case": "invalid_type",
            "fields": {"detail_case": "Test"},
        })
        assert response.status_code == 201
        data = response.json()
        # Invalid type defaults to "non_order"
        assert "#Non Order" in data["text"]

    def test_create_case_no_mentions(self, client):
        tc, mock_cursor = client
        mock_cursor.fetchone.side_effect = _create_case_mock_sequence(
            jenis_case_name="Non Order",
            case_code=None,
            case_id=47,
        )

        response = tc.post("/api/cases", json={
            "jenis_case": "Non Order",
            "fields": {"ticket_remedy": "INC123"},
        })
        assert response.status_code == 201
        data = response.json()
        assert "punten mas" not in data["text"]
        assert "#Non Order" in data["text"]

    def test_create_case_empty_fields(self, client):
        tc, mock_cursor = client
        mock_cursor.fetchone.side_effect = _create_case_mock_sequence(
            jenis_case_name="Non Order",
            case_code=None,
            case_id=48,
        )

        response = tc.post("/api/cases", json={
            "jenis_case": "Non Order",
            "fields": {},
        })
        assert response.status_code == 201
        data = response.json()
        assert "#Non Order" in data["text"]

    def test_create_case_legacy_type_maps(self, client):
        """Legacy case_type 'stc' should map to 'non_order'."""
        tc, mock_cursor = client
        # When only case_type is provided (no jenis_case), _resolve_jenis_case gets None
        mock_cursor.fetchone.side_effect = _create_case_mock_sequence(
            jenis_case_name=None,  # None because jenis_case not provided
            case_code=None,
            case_id=49,
        )
        response = tc.post("/api/cases", json={
            "case_type": "stc",  # legacy field
            "fields": {"ticket_remedy": "INC123"},
        })
        assert response.status_code == 201
        data = response.json()
        assert "#Non Order" in data["text"]


class TestHealthCheck:
    def test_health_returns_ok(self, mock_waha):
        mock_conn, mock_cursor = _make_mock_db()

        with patch.object(main_module, "db", return_value=mock_conn):
            mock_get_resp = MagicMock()
            mock_get_resp.status_code = 200
            mock_async_client = AsyncMock()
            mock_async_client.get.return_value = mock_get_resp
            with patch.object(main_module.httpx, "AsyncClient") as mock_ac:
                mock_ac.return_value.__aenter__ = AsyncMock(return_value=mock_async_client)
                mock_ac.return_value.__aexit__ = AsyncMock(return_value=False)
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


class TestLookupEndpoints:
    def test_list_areas(self, mock_waha):
        mock_conn, mock_cursor = _make_mock_db(fetchone_sequence=[
            [{"id": 1, "name": "Area 1"}, {"id": 2, "name": "Area 2"}]
        ])
        with patch.object(main_module, "db", return_value=mock_conn):
            tc = TestClient(main_module.app)
            response = tc.get("/api/areas")
            assert response.status_code == 200

    def test_list_sumber_tickets(self, mock_waha):
        mock_conn, mock_cursor = _make_mock_db(fetchone_sequence=[
            [{"id": 1, "name": "STC"}, {"id": 2, "name": "Grapari"}, {"id": 3, "name": "Web IT"}]
        ])
        with patch.object(main_module, "db", return_value=mock_conn):
            tc = TestClient(main_module.app)
            response = tc.get("/api/sumber-tickets")
            assert response.status_code == 200

    def test_list_jenis_cases(self, mock_waha):
        mock_conn, mock_cursor = _make_mock_db(fetchone_sequence=[
            [{"id": 1, "name": "Non Order"}, {"id": 2, "name": "Non AO"}, {"id": 3, "name": "Mobile"}]
        ])
        with patch.object(main_module, "db", return_value=mock_conn):
            tc = TestClient(main_module.app)
            response = tc.get("/api/jenis-cases")
            assert response.status_code == 200

    def test_list_regionals_area_not_found(self, mock_waha):
        mock_conn, mock_cursor = _make_mock_db(fetchone_sequence=[None])
        with patch.object(main_module, "db", return_value=mock_conn):
            tc = TestClient(main_module.app)
            response = tc.get("/api/areas/999/regionals")
            assert response.status_code == 404

    def test_list_regionals_area_found(self, mock_waha):
        mock_conn, mock_cursor = _make_mock_db(fetchone_sequence=[
            {"id": 1, "name": "Area 1"},  # area found
            [{"id": 1, "name": "Regional 1"}, {"id": 2, "name": "Regional 2"}],  # regionals
        ])
        with patch.object(main_module, "db", return_value=mock_conn):
            tc = TestClient(main_module.app)
            response = tc.get("/api/areas/1/regionals")
            assert response.status_code == 200


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


class TestSolverContacts:
    def test_create_contact(self, mock_waha):
        mock_conn, mock_cursor = _make_mock_db(fetchone_sequence=[
            None,  # duplicate check (no existing)
            {"id": 1, "name": "Mas Budi", "phone_number": "6281234567890", "role": "Solusi 1", "is_active": True, "created_at": "...", "updated_at": "..."},
        ])
        with patch.object(main_module, "db", return_value=mock_conn):
            tc = TestClient(main_module.app)
            response = tc.post("/api/solver-contacts", json={
                "name": "Mas Budi",
                "phone_number": "6281234567890",
                "role": "Solusi 1",
            })
            assert response.status_code == 201
            data = response.json()
            assert data["name"] == "Mas Budi"
            assert data["phone_number"] == "6281234567890"
            assert data["role"] == "Solusi 1"

    def test_create_contact_duplicate_phone(self, mock_waha):
        mock_conn, mock_cursor = _make_mock_db(fetchone_sequence=[
            {"id": 1},  # duplicate found
        ])
        with patch.object(main_module, "db", return_value=mock_conn):
            tc = TestClient(main_module.app)
            response = tc.post("/api/solver-contacts", json={
                "name": "Mas Budi",
                "phone_number": "6281234567890",
            })
            assert response.status_code == 409

    def test_list_contacts(self, mock_waha):
        mock_conn, mock_cursor = _make_mock_db(fetchone_sequence=[
            [{"id": 1, "name": "Mas Budi", "phone_number": "6281234567890", "role": "Solusi 1", "is_active": True}],
        ])
        with patch.object(main_module, "db", return_value=mock_conn):
            tc = TestClient(main_module.app)
            response = tc.get("/api/solver-contacts")
            assert response.status_code == 200

    def test_get_contact_not_found(self, mock_waha):
        mock_conn, mock_cursor = _make_mock_db(fetchone_sequence=[None])
        with patch.object(main_module, "db", return_value=mock_conn):
            tc = TestClient(main_module.app)
            response = tc.get("/api/solver-contacts/999")
            assert response.status_code == 404

    def test_update_contact(self, mock_waha):
        mock_conn, mock_cursor = _make_mock_db(fetchone_sequence=[
            {"id": 1},  # contact exists
            None,  # duplicate check (no existing)
            {"id": 1, "name": "Mas Budi Updated", "phone_number": "6281234567890", "role": "Supervisor", "is_active": True},
        ])
        with patch.object(main_module, "db", return_value=mock_conn):
            tc = TestClient(main_module.app)
            response = tc.put("/api/solver-contacts/1", json={
                "name": "Mas Budi Updated",
                "role": "Supervisor",
            })
            assert response.status_code == 200

    def test_update_contact_not_found(self, mock_waha):
        mock_conn, mock_cursor = _make_mock_db(fetchone_sequence=[None])
        with patch.object(main_module, "db", return_value=mock_conn):
            tc = TestClient(main_module.app)
            response = tc.put("/api/solver-contacts/999", json={"name": "Test"})
            assert response.status_code == 404

    def test_soft_delete_contact(self, mock_waha):
        mock_conn, mock_cursor = _make_mock_db(fetchone_sequence=[
            {"id": 1},  # contact exists
            None,  # update
        ])
        with patch.object(main_module, "db", return_value=mock_conn):
            tc = TestClient(main_module.app)
            response = tc.delete("/api/solver-contacts/1")
            assert response.status_code == 200
            assert response.json()["ok"] is True

    def test_soft_delete_not_found(self, mock_waha):
        mock_conn, mock_cursor = _make_mock_db(fetchone_sequence=[None])
        with patch.object(main_module, "db", return_value=mock_conn):
            tc = TestClient(main_module.app)
            response = tc.delete("/api/solver-contacts/999")
            assert response.status_code == 404


class TestMediaHandling:
    """Test handling of image + caption and image-only replies from WAHA."""

    def test_webhook_image_caption_dispatches_handle_message(self, mock_waha):
        """Webhook with hasMedia + body should dispatch to handle_message."""
        mock_conn, mock_cursor = _make_mock_db()
        with patch.object(main_module, "db", return_value=mock_conn), \
             patch.object(main_module, "handle_message", new_callable=AsyncMock, return_value=True) as mock_handle:
            tc = TestClient(main_module.app)
            response = tc.post("/webhooks/waha", json={
                "event": "message",
                "payload": {
                    "id": {"_serialized": "true_123@g.us_ABC"},
                    "body": "done INC123",
                    "from": "120363xxx@g.us",
                    "hasMedia": True,
                    "media": {
                        "url": "http://waha:3000/api/files/abc.jpg",
                        "mimetype": "image/jpeg",
                    },
                },
            })
            assert response.status_code == 200
            mock_handle.assert_called_once()
            payload = mock_handle.call_args[0][0]
            assert payload["hasMedia"] is True
            assert payload["media"]["url"] == "http://waha:3000/api/files/abc.jpg"

    def test_store_message_saves_media_info(self):
        """store_message should save media_url and media_type to DB."""
        mock_conn, mock_cursor = _make_mock_db()
        with patch.object(main_module, "db", return_value=mock_conn):
            main_module.store_message(
                "test_mid", "quoted_123", "author_456", "done INC123",
                media_url="http://waha:3000/api/files/abc.jpg",
                media_type="image/jpeg",
            )
            args = mock_cursor.execute.call_args[0]
            # args[0] is SQL, args[1] is params tuple
            params = args[1]
            assert params[5] == "http://waha:3000/api/files/abc.jpg"  # media_url
            assert params[6] == "image/jpeg"  # media_type

    def test_store_message_without_media(self):
        """store_message without media should set media_url/media_type to None."""
        mock_conn, mock_cursor = _make_mock_db()
        with patch.object(main_module, "db", return_value=mock_conn):
            main_module.store_message(
                "test_mid", None, "author", "hello world"
            )
            args = mock_cursor.execute.call_args[0]
            params = args[1]
            assert params[5] is None  # media_url
            assert params[6] is None  # media_type
