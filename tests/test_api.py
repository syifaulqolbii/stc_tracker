"""Integration tests for Moban FU Tracker API endpoints.

These tests mock the database and WAHA to allow testing without running services.
Updated for v1.2: new case types, new fields, new lookup endpoints.
"""
import os
import sys
import json
import httpx
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
    group_id=1,
):
    """Build the fetchone mock sequence for create_case endpoint.

    The endpoint makes these DB calls in order:
    1. _resolve_jenis_case(name) → SELECT id FROM jenis_cases WHERE name = %s
       (skipped if name is None — returns early without DB call)
    2. _resolve_sumber_ticket(name) → SELECT id FROM sumber_tickets WHERE name = %s
       (skipped if name is None — returns early without DB call)
    3. _get_group(group_id) → SELECT * FROM wa_groups WHERE id = %s AND is_active = true
       (always called — group_id is required)
    4. Resolve area_name (if area_id) → SELECT name FROM areas WHERE id = %s
    5. Resolve regional_name (if regional_id) → SELECT name FROM regionals WHERE id = %s
    6. INSERT RETURNING → {id, case_code}
    7. INSERT wa_messages → None
    """
    seq = []
    # 1. _resolve_jenis_case (only if name is not None)
    if jenis_case_name:
        seq.append({"id": 1})
    # 2. _resolve_sumber_ticket (only if name is not None)
    if sumber_ticket_name:
        seq.append({"id": 2})
    # 3. _get_group(group_id)
    seq.append({"id": group_id, "name": "Grup A", "chat_id": "120363xxx@g.us"})
    # 4. area name (only if area_name is provided, meaning area_id was set)
    if area_name:
        seq.append({"name": area_name})
    # 5. regional name (only if regional_name is provided)
    if regional_name:
        seq.append({"name": regional_name})
    # 6. INSERT RETURNING
    seq.append({"id": case_id, "case_code": case_code})
    # 7. INSERT wa_messages
    seq.append(None)
    return seq


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    """In-memory rate limiter (60 rpm/IP) dibagi antar test dalam satu proses —
    TestClient selalu pakai IP yang sama, jadi bucket-nya wajib dibersihkan
    supaya suite tidak mulai mengembalikan 429 hanya karena jumlah test bertambah."""
    main_module._rate_buckets.clear()
    yield
    main_module._rate_buckets.clear()


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
            "group_id": 1,
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
        # Compact header: #Grapari_Non Order_Area 1_Regional 2
        assert "#Grapari_Non Order_Area 1_Regional 2" in data["text"]
        assert "Asal Grapari : GraPARI Bandung" in data["text"]

    def test_create_mobile_case(self, client):
        tc, mock_cursor = client
        mock_cursor.fetchone.side_effect = _create_case_mock_sequence(
            jenis_case_name="Mobile",
            sumber_ticket_name=None,
            case_code=None,
        )

        response = tc.post("/api/cases", json={
            "group_id": 1,
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
            "group_id": 1,
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
            "group_id": 1,
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
            "group_id": 1,
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
            "group_id": 1,
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
            "group_id": 1,
            "case_type": "stc",  # legacy field
            "fields": {"ticket_remedy": "INC123"},
        })
        assert response.status_code == 201
        data = response.json()
        assert "#Non Order" in data["text"]

    def test_create_case_uses_default_group(self, client):
        """Tanpa group_id → case dikirim ke grup default (is_default)."""
        tc, mock_cursor = client
        mock_cursor.fetchone.side_effect = [
            {"id": 1},  # jenis_case lookup
            {"id": 5, "name": "Grup Test Dev", "chat_id": "120363999@g.us",
             "is_default": True},  # _get_default_group()
            {"id": 50, "case_code": None},  # INSERT RETURNING
            None,  # INSERT wa_messages
        ]
        response = tc.post("/api/cases", json={
            "jenis_case": "Non Order",
            "fields": {"detail_case": "tanpa grup"},
        })
        assert response.status_code == 201
        data = response.json()
        assert data["group_id"] == 5
        assert data["group_name"] == "Grup Test Dev"

    def test_create_case_no_group_and_no_default_returns_400(self, client):
        """Tanpa group_id DAN belum ada grup default → 400 dengan pesan jelas."""
        tc, mock_cursor = client
        mock_cursor.fetchone.side_effect = [
            {"id": 1},  # jenis_case lookup
            None,       # _get_default_group() → tidak ada default
        ]
        response = tc.post("/api/cases", json={
            "jenis_case": "Non Order",
            "fields": {"detail_case": "tanpa grup"},
        })
        assert response.status_code == 400
        assert "default" in response.json()["detail"]

    def test_create_case_unknown_group_returns_422(self, client):
        """group_id tidak dikenal → 422 yang menyebutkan nilainya (bukan 404 generik)."""
        tc, mock_cursor = client
        mock_cursor.fetchone.side_effect = [
            {"id": 1},  # jenis_case lookup
            None,       # _get_group(999) → tidak ada yang aktif
            None,       # _get_group_any(999) → memang tidak ada barisnya
        ]
        response = tc.post("/api/cases", json={
            "group_id": 999,
            "jenis_case": "Non Order",
            "fields": {"ticket_remedy": "INC123"},
        })
        assert response.status_code == 422
        detail = str(response.json()["detail"])
        assert "999" in detail
        assert "tidak dikenal" in detail

    def test_create_case_inactive_group_returns_409_with_name(self, client):
        """Grup ada tapi is_active=false → 409 dengan NAMA grup, bukan 404 tanpa konteks."""
        tc, mock_cursor = client
        mock_cursor.fetchone.side_effect = [
            {"id": 1},  # jenis_case lookup
            None,       # _get_group(2) → tidak aktif
            {"id": 2, "name": "Escalation OPERA - CX100",
             "chat_id": "120363410063803591@g.us", "is_active": False},  # _get_group_any(2)
        ]
        response = tc.post("/api/cases", json={
            "group_id": 2,
            "jenis_case": "Non Order",
            "fields": {"ticket_remedy": "INC123"},
        })
        assert response.status_code == 409
        detail = str(response.json()["detail"])
        assert "Escalation OPERA - CX100" in detail
        assert "dinonaktifkan" in detail

    @pytest.mark.parametrize("bad", [0, -1])
    def test_create_case_non_positive_group_rejected_by_validation(self, client, bad):
        """group_id <= 0 ditolak Pydantic (ge=1) — tanpa menyentuh DB."""
        tc, mock_cursor = client
        response = tc.post("/api/cases", json={
            "group_id": bad,
            "jenis_case": "Non Order",
            "fields": {"ticket_remedy": "INC123"},
        })
        assert response.status_code == 422
        assert "greater than or equal to 1" in str(response.json()["detail"])


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

    def test_webhook_never_requires_api_key_auth(self, mock_waha):
        """X-API-Key tetap tidak berlaku untuk webhook (auth webhook pakai secret sendiri).
        WAHA_WEBHOOK_SECRET kosong → mode lama, request diterima."""
        mock_conn, mock_cursor = _make_mock_db()
        with patch.object(main_module, "db", return_value=mock_conn), \
             patch.object(main_module, "BACKEND_API_KEY", "secret-key-123"), \
             patch.object(main_module, "WAHA_WEBHOOK_SECRET", ""):
            tc = TestClient(main_module.app)
            response = tc.post("/webhooks/waha", json={"event": "session.status"})
            assert response.status_code == 200
            assert response.json()["ok"] is True


class TestWebhookSecret:
    """v1.13 — webhook WAHA wajib membawa shared secret (anti spoofing)."""

    def test_reject_without_secret_when_configured(self, mock_waha):
        with patch.object(main_module, "WAHA_WEBHOOK_SECRET", "rahasia123"):
            tc = TestClient(main_module.app)
            r = tc.post("/webhooks/waha", json={"event": "message", "payload": {}})
            assert r.status_code == 401

    def test_reject_wrong_secret(self, mock_waha):
        with patch.object(main_module, "WAHA_WEBHOOK_SECRET", "rahasia123"):
            tc = TestClient(main_module.app)
            r = tc.post("/webhooks/waha", json={"event": "message", "payload": {}},
                        headers={"X-Webhook-Secret": "salah"})
            assert r.status_code == 401

    def test_accept_correct_header_secret(self, mock_waha):
        with patch.object(main_module, "WAHA_WEBHOOK_SECRET", "rahasia123"), \
             patch.object(main_module, "handle_message", new_callable=AsyncMock) as mock_handle:
            tc = TestClient(main_module.app)
            r = tc.post("/webhooks/waha", json={"event": "message", "payload": {}},
                        headers={"X-Webhook-Secret": "rahasia123"})
            assert r.status_code == 200
            mock_handle.assert_called_once()

    def test_accept_correct_token_query_param(self, mock_waha):
        with patch.object(main_module, "WAHA_WEBHOOK_SECRET", "rahasia123"), \
             patch.object(main_module, "handle_message", new_callable=AsyncMock) as mock_handle:
            tc = TestClient(main_module.app)
            r = tc.post("/webhooks/waha?token=rahasia123",
                        json={"event": "message", "payload": {}})
            assert r.status_code == 200
            mock_handle.assert_called_once()


class TestLoginRateLimit:
    """v1.13 — anti brute-force login access-code (maks 5/menit/IP)."""

    def test_sixth_attempt_returns_429(self, mock_waha):
        with patch.object(main_module, "ACCESS_CODES", ["CODE123"]), \
             patch.object(main_module, "LOGIN_RATE_LIMIT", 5):
            main_module._rate_buckets.clear()
            tc = TestClient(main_module.app)
            statuses = []
            for _ in range(6):
                r = tc.post("/api/auth/access-code", json={"code": "WRONG"})
                statuses.append(r.status_code)
            assert statuses[:5] == [401, 401, 401, 401, 401]
            assert statuses[5] == 429

    def test_limit_not_triggered_below_threshold(self, mock_waha):
        with patch.object(main_module, "ACCESS_CODES", ["CODE123"]), \
             patch.object(main_module, "LOGIN_RATE_LIMIT", 5):
            main_module._rate_buckets.clear()
            tc = TestClient(main_module.app)
            for _ in range(4):
                assert tc.post("/api/auth/access-code", json={"code": "WRONG"}).status_code == 401
            # lalu login benar tetap bisa
            r = tc.post("/api/auth/access-code", json={"code": "CODE123"})
            assert r.status_code == 200


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


class TestGroups:
    """CRUD untuk grup WhatsApp (switcher multi-grup)."""

    GROUP_ROW = {"id": 1, "name": "Grup A", "chat_id": "120363001@g.us",
                 "is_active": True, "created_at": "...", "updated_at": "..."}

    def test_create_group(self, mock_waha):
        mock_conn, mock_cursor = _make_mock_db(fetchone_sequence=[
            None,  # duplicate chat_id check (no existing)
            self.GROUP_ROW,
        ])
        with patch.object(main_module, "db", return_value=mock_conn):
            tc = TestClient(main_module.app)
            response = tc.post("/api/groups", json={
                "name": "Grup A",
                "chat_id": "120363001@g.us",
            })
            assert response.status_code == 201
            data = response.json()
            assert data["name"] == "Grup A"
            assert data["chat_id"] == "120363001@g.us"

    def test_create_group_duplicate_chat_id(self, mock_waha):
        mock_conn, mock_cursor = _make_mock_db(fetchone_sequence=[
            {"id": 1},  # duplicate found
        ])
        with patch.object(main_module, "db", return_value=mock_conn):
            tc = TestClient(main_module.app)
            response = tc.post("/api/groups", json={
                "name": "Grup B",
                "chat_id": "120363001@g.us",
            })
            assert response.status_code == 409

    def test_create_group_invalid_chat_id(self, mock_waha):
        mock_conn, mock_cursor = _make_mock_db()
        with patch.object(main_module, "db", return_value=mock_conn):
            tc = TestClient(main_module.app)
            response = tc.post("/api/groups", json={
                "name": "Grup X",
                "chat_id": "bisanya-nomor-aja",
            })
            assert response.status_code == 422

    def test_list_groups(self, mock_waha):
        mock_conn, mock_cursor = _make_mock_db()
        mock_cursor.fetchall.return_value = [
            {"id": 1, "name": "Grup A", "chat_id": "120363001@g.us", "is_active": True, "is_default": False},
            {"id": 2, "name": "Grup B", "chat_id": "120363002@g.us", "is_active": True, "is_default": False},
        ]
        with patch.object(main_module, "db", return_value=mock_conn):
            tc = TestClient(main_module.app)
            response = tc.get("/api/groups")
            assert response.status_code == 200
            data = response.json()
            assert len(data) == 2
            assert data[0]["name"] == "Grup A"

    def test_get_group(self, mock_waha):
        mock_conn, mock_cursor = _make_mock_db(fetchone_sequence=[self.GROUP_ROW])
        with patch.object(main_module, "db", return_value=mock_conn):
            tc = TestClient(main_module.app)
            response = tc.get("/api/groups/1")
            assert response.status_code == 200
            assert response.json()["name"] == "Grup A"

    def test_get_group_not_found(self, mock_waha):
        mock_conn, mock_cursor = _make_mock_db(fetchone_sequence=[None])
        with patch.object(main_module, "db", return_value=mock_conn):
            tc = TestClient(main_module.app)
            response = tc.get("/api/groups/999")
            assert response.status_code == 404

    def test_update_group(self, mock_waha):
        mock_conn, mock_cursor = _make_mock_db(fetchone_sequence=[
            {"id": 1},  # exists
            {**self.GROUP_ROW, "name": "Grup A Updated"},  # RETURNING row
        ])
        with patch.object(main_module, "db", return_value=mock_conn):
            tc = TestClient(main_module.app)
            response = tc.put("/api/groups/1", json={"name": "Grup A Updated"})
            assert response.status_code == 200
            assert response.json()["name"] == "Grup A Updated"

    def test_update_group_not_found(self, mock_waha):
        mock_conn, mock_cursor = _make_mock_db(fetchone_sequence=[None])
        with patch.object(main_module, "db", return_value=mock_conn):
            tc = TestClient(main_module.app)
            response = tc.put("/api/groups/999", json={"name": "Test"})
            assert response.status_code == 404

    def test_update_group_duplicate_chat_id(self, mock_waha):
        mock_conn, mock_cursor = _make_mock_db(fetchone_sequence=[
            {"id": 1},  # exists
            {"id": 2},  # duplicate chat_id found on another group
        ])
        with patch.object(main_module, "db", return_value=mock_conn):
            tc = TestClient(main_module.app)
            response = tc.put("/api/groups/1", json={"chat_id": "120363002@g.us"})
            assert response.status_code == 409

    def test_soft_delete_group(self, mock_waha):
        mock_conn, mock_cursor = _make_mock_db(fetchone_sequence=[
            {"id": 1},  # exists & active
            None,       # update
        ])
        with patch.object(main_module, "db", return_value=mock_conn):
            tc = TestClient(main_module.app)
            response = tc.delete("/api/groups/1")
            assert response.status_code == 200
            assert response.json()["ok"] is True

    def test_soft_delete_group_not_found(self, mock_waha):
        mock_conn, mock_cursor = _make_mock_db(fetchone_sequence=[
            None,  # not found or already inactive
        ])
        with patch.object(main_module, "db", return_value=mock_conn):
            tc = TestClient(main_module.app)
            response = tc.delete("/api/groups/999")
            assert response.status_code == 404

    # --- grup default (fallback, tersembunyi dari switcher) ---

    def test_list_groups_hides_default_by_default(self, mock_waha):
        """GET /api/groups menyaring keluar grup default (switcher bersih)."""
        mock_conn, mock_cursor = _make_mock_db()
        mock_cursor.fetchall.return_value = [
            {"id": 1, "name": "Grup A", "chat_id": "120363001@g.us",
             "is_active": True, "is_default": False},
        ]
        with patch.object(main_module, "db", return_value=mock_conn):
            tc = TestClient(main_module.app)
            response = tc.get("/api/groups")
            assert response.status_code == 200
            sql = mock_cursor.execute.call_args[0][0]
            assert "is_default = false" in sql

    def test_list_groups_excludes_inactive_by_default(self, mock_waha):
        """SAFE BY DEFAULT: tanpa param apa pun, grup nonaktif tidak disodorkan ke switcher.

        Regression: dulu endpoint ini mengembalikan grup nonaktif kecuali caller ingat
        mengirim ?is_active=true — frontend jadi memilih grup yang pasti ditolak.
        """
        mock_conn, mock_cursor = _make_mock_db()
        mock_cursor.fetchall.return_value = []
        with patch.object(main_module, "db", return_value=mock_conn):
            tc = TestClient(main_module.app)
            response = tc.get("/api/groups")
            assert response.status_code == 200
            sql = mock_cursor.execute.call_args[0][0]
            assert "is_active = true" in sql

    def test_list_groups_include_inactive_param(self, mock_waha):
        """?include_inactive=true → filter aktif dilepas (untuk admin)."""
        mock_conn, mock_cursor = _make_mock_db()
        mock_cursor.fetchall.return_value = []
        with patch.object(main_module, "db", return_value=mock_conn):
            tc = TestClient(main_module.app)
            response = tc.get("/api/groups?include_inactive=true")
            assert response.status_code == 200
            sql = mock_cursor.execute.call_args[0][0]
            assert "is_active = true" not in sql

    def test_list_groups_include_default_param(self, mock_waha):
        """?include_default=true → filter default dilepas (untuk admin)."""
        mock_conn, mock_cursor = _make_mock_db()
        mock_cursor.fetchall.return_value = []
        with patch.object(main_module, "db", return_value=mock_conn):
            tc = TestClient(main_module.app)
            response = tc.get("/api/groups?include_default=true")
            assert response.status_code == 200
            sql = mock_cursor.execute.call_args[0][0]
            assert "is_default = false" not in sql

    def test_create_group_with_is_default_clears_previous(self, mock_waha):
        """POST /api/groups is_default=true melepas default lama sebelum insert."""
        mock_conn, mock_cursor = _make_mock_db(fetchone_sequence=[
            None,  # duplicate chat_id check
            {"id": 3, "name": "Grup Test Dev", "chat_id": "120363003@g.us",
             "is_active": True, "is_default": True},
        ])
        with patch.object(main_module, "db", return_value=mock_conn):
            tc = TestClient(main_module.app)
            response = tc.post("/api/groups", json={
                "name": "Grup Test Dev",
                "chat_id": "120363003@g.us",
                "is_default": True,
            })
            assert response.status_code == 201
            assert response.json()["is_default"] is True
            sqls = [c[0][0] for c in mock_cursor.execute.call_args_list]
            assert any("SET is_default = false" in s for s in sqls)

    def test_create_group_without_is_default_keeps_false(self, mock_waha):
        """Tanpa is_default → tidak ada query pelepasan default."""
        mock_conn, mock_cursor = _make_mock_db(fetchone_sequence=[
            None,
            {**self.GROUP_ROW, "is_default": False},
        ])
        with patch.object(main_module, "db", return_value=mock_conn):
            tc = TestClient(main_module.app)
            response = tc.post("/api/groups", json={
                "name": "Grup A",
                "chat_id": "120363001@g.us",
            })
            assert response.status_code == 201
            sqls = [c[0][0] for c in mock_cursor.execute.call_args_list]
            assert not any("SET is_default = false" in s for s in sqls)

    def test_update_group_set_default(self, mock_waha):
        """PUT /api/groups/{id} {"is_default": true} menggeser default lama."""
        mock_conn, mock_cursor = _make_mock_db(fetchone_sequence=[
            {"id": 2},  # exists
            {"id": 2, "name": "Grup B", "chat_id": "120363002@g.us",
             "is_active": True, "is_default": True},  # RETURNING
        ])
        with patch.object(main_module, "db", return_value=mock_conn):
            tc = TestClient(main_module.app)
            response = tc.put("/api/groups/2", json={"is_default": True})
            assert response.status_code == 200
            assert response.json()["is_default"] is True
            sqls = [c[0][0] for c in mock_cursor.execute.call_args_list]
            assert any("SET is_default = false" in s and "id != %s" in s for s in sqls)


class TestGetDefaultGroup:
    """Helper _get_default_group: hanya grup is_default yang aktif."""

    def test_sql_filters_is_default_and_active(self):
        mock_conn, mock_cursor = _make_mock_db(fetchone_sequence=[
            {"id": 5, "name": "Grup Test Dev", "chat_id": "120363003@g.us",
             "is_default": True, "is_active": True},
        ])
        with patch.object(main_module, "db", return_value=mock_conn):
            row = main_module._get_default_group()
            assert row["name"] == "Grup Test Dev"
            sql = mock_cursor.execute.call_args[0][0]
            assert "is_default = true" in sql
            assert "is_active = true" in sql

    def test_returns_none_when_no_default(self):
        mock_conn, mock_cursor = _make_mock_db(fetchone_sequence=[None])
        with patch.object(main_module, "db", return_value=mock_conn):
            assert main_module._get_default_group() is None


class TestWahaGroups:
    """GET /api/waha/groups — discovery grup dari WAHA + penanda sudah terdaftar."""

    CHATS = [
        {"id": {"_serialized": "120363001@g.us"}, "name": "Grup Produksi",
         "kind": "group", "isGroup": True},
        {"id": {"_serialized": "628111000@c.us"}, "name": "Budi Personal",
         "kind": "chat", "isGroup": False},
        {"id": {"_serialized": "120363002@g.us"}, "name": "Grup Test Dev",
         "kind": "group", "isGroup": True},
        {"id": {"_serialized": "status@broadcast"}, "name": None, "kind": "chat"},
    ]

    def _client(self, payload=None, get_side_effect=None):
        """Patch httpx.AsyncClient (GET) + db. Returns context manager + tc."""
        import contextlib

        mock_conn, mock_cursor = _make_mock_db()
        mock_cursor.fetchall.return_value = [
            {"id": 1, "chat_id": "120363001@g.us", "is_active": True, "is_default": False},
        ]
        stack = contextlib.ExitStack()
        stack.enter_context(patch.object(main_module, "db", return_value=mock_conn))
        mock_http = stack.enter_context(patch.object(main_module.httpx, "AsyncClient"))
        client = AsyncMock()
        if get_side_effect is not None:
            client.get.side_effect = get_side_effect
        else:
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = payload if payload is not None else self.CHATS
            resp.raise_for_status = MagicMock()
            client.get.return_value = resp
        mock_http.return_value.__aenter__ = AsyncMock(return_value=client)
        mock_http.return_value.__aexit__ = AsyncMock(return_value=False)
        return stack, TestClient(main_module.app)

    def test_only_groups_listed_and_unregistered_first(self):
        stack, tc = self._client()
        try:
            r = tc.get("/api/waha/groups")
            assert r.status_code == 200
            data = r.json()
            ids = [g["chat_id"] for g in data]
            # DM (628111000@c.us) + status@broadcast dibuang; yang belum terdaftar duluan
            assert ids == ["120363002@g.us", "120363001@g.us"]
            by_id = {g["chat_id"]: g for g in data}
            assert by_id["120363001@g.us"]["registered"] is True
            assert by_id["120363001@g.us"]["group_id"] == 1
            assert by_id["120363001@g.us"]["is_active"] is True
            assert by_id["120363002@g.us"]["registered"] is False
            assert by_id["120363002@g.us"]["group_id"] is None
        finally:
            stack.close()

    def test_handles_wrapped_waha_response(self):
        """Response {data: [...]} ikut dinormalisasi."""
        stack, tc = self._client(payload={"data": self.CHATS})
        try:
            r = tc.get("/api/waha/groups")
            assert r.status_code == 200
            assert len(r.json()) == 2
        finally:
            stack.close()

    def test_search_filters_by_name(self):
        stack, tc = self._client()
        try:
            r = tc.get("/api/waha/groups?search=test dev")
            assert r.status_code == 200
            data = r.json()
            assert [g["name"] for g in data] == ["Grup Test Dev"]
        finally:
            stack.close()

    def test_limit_caps_output(self):
        stack, tc = self._client()
        try:
            r = tc.get("/api/waha/groups?limit=1")
            assert r.status_code == 200
            assert len(r.json()) == 1
        finally:
            stack.close()

    def test_waha_http_error_returns_502(self):
        req = httpx.Request("GET", "http://waha/api/default/chats")
        err = httpx.HTTPStatusError("boom", request=req, response=httpx.Response(503, request=req))
        stack, tc = self._client(get_side_effect=err)
        try:
            r = tc.get("/api/waha/groups")
            assert r.status_code == 502
            assert "WAHA error" in r.json()["detail"]
        finally:
            stack.close()

    def test_waha_unreachable_returns_502(self):
        stack, tc = self._client(get_side_effect=httpx.ConnectError("down"))
        try:
            r = tc.get("/api/waha/groups")
            assert r.status_code == 502
            assert "unavailable" in r.json()["detail"]
        finally:
            stack.close()


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




class TestMediaProxy:
    def test_rewrite_media_url_waha(self):
        """_rewrite_media_url should rewrite WAHA internal URL to proxy URL."""
        url = main_module._rewrite_media_url("http://waha:3000/api/files/abc.jpg")
        assert "/api/media/proxy?url=" in url
        assert url.startswith("http://localhost:8000/api/media/proxy")
        assert "abc.jpg" in url

    def test_rewrite_media_url_localhost(self):
        """_rewrite_media_url should rewrite localhost URL to proxy URL."""
        url = main_module._rewrite_media_url("http://localhost:3000/api/files/test.png")
        assert "/api/media/proxy?url=" in url

    def test_rewrite_media_url_none(self):
        """_rewrite_media_url should return None for None input."""
        assert main_module._rewrite_media_url(None) is None

    def test_rewrite_media_url_external(self):
        """_rewrite_media_url should NOT rewrite external URLs."""
        url = "https://imgur.com/something.jpg"
        assert main_module._rewrite_media_url(url) == url

    @pytest.mark.asyncio
    async def test_download_media_success(self):
        """_download_media should download from WAHA and save locally."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "image/jpeg"}
        mock_resp.content = b"fake image bytes"

        async_client = AsyncMock()
        async_client.get.return_value = mock_resp

        with patch.object(main_module, "WAHA_URL", "http://waha:3000"), \
             patch.object(main_module.httpx, "AsyncClient") as mock_client, \
             patch("builtins.open", MagicMock()):
            mock_client.return_value.__aenter__ = AsyncMock(return_value=async_client)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await main_module._download_media("http://waha:3000/api/files/abc.jpg")
            assert result is not None
            assert result.endswith(".jpg")

    @pytest.mark.asyncio
    async def test_download_media_failure(self):
        """_download_media should return None on failure."""
        mock_resp = MagicMock()
        mock_resp.status_code = 404

        async_client = AsyncMock()
        async_client.get.return_value = mock_resp

        with patch.object(main_module.httpx, "AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(return_value=async_client)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await main_module._download_media("http://waha:3000/api/files/missing.jpg")
            assert result is None

    @pytest.mark.asyncio
    async def test_download_and_rewrite_media_success(self):
        """_download_and_rewrite_media should return local URL on success."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "image/jpeg"}
        mock_resp.content = b"fake image bytes"

        async_client = AsyncMock()
        async_client.get.return_value = mock_resp

        with patch.object(main_module, "WAHA_URL", "http://waha:3000"), \
             patch.object(main_module.httpx, "AsyncClient") as mock_client, \
             patch("builtins.open", MagicMock()):
            mock_client.return_value.__aenter__ = AsyncMock(return_value=async_client)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await main_module._download_and_rewrite_media("http://waha:3000/api/files/abc.jpg")
            assert result is not None
            assert "/api/media/file/" in result

    def test_media_proxy_invalid_url(self):
        """media_proxy should reject non-WAHA URLs."""
        tc = TestClient(main_module.app)
        resp = tc.get("/api/media/proxy?url=https://evil.com/hack.jpg")
        assert resp.status_code == 400


class TestAntiSsrf:
    """v1.13 — validasi host TEPAT WAHA_URL (anti bypass substring)."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad_url", [
        "http://waha.attacker.com/x.jpg",      # mengandung "waha" tapi host lain
        "http://localhost.attacker.com/x.jpg",  # mengandung "localhost" tapi host lain
        "http://127.0.0.1.evil.com/x.jpg",
        "https://evil.com/x.jpg",
        "http://waha@evil.com/x.jpg",           # userinfo trick
        "file:///etc/passwd",
        "",
        None,
    ])
    async def test_is_waha_url_rejects_foreign_hosts(self, bad_url):
        with patch.object(main_module, "WAHA_URL", "http://waha:3000"):
            assert main_module._is_waha_url(bad_url) is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize("good_url", [
        "http://waha:3000/api/files/abc.jpg",
        "http://waha:3000/api/files/abc.jpg?token=x",
    ])
    async def test_is_waha_url_accepts_exact_host(self, good_url):
        with patch.object(main_module, "WAHA_URL", "http://waha:3000"):
            assert main_module._is_waha_url(good_url) is True

    @pytest.mark.asyncio
    async def test_download_media_rewrites_localhost_then_accepts(self):
        """URL localhost:3000 di-rewrite ke netloc WAHA sebelum cek → lolos."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "image/jpeg"}
        mock_resp.content = b"x"
        async_client = AsyncMock()
        async_client.get.return_value = mock_resp
        with patch.object(main_module, "WAHA_URL", "http://waha:3000"), \
             patch.object(main_module.httpx, "AsyncClient") as mock_client, \
             patch("builtins.open", MagicMock()):
            mock_client.return_value.__aenter__ = AsyncMock(return_value=async_client)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await main_module._download_media("http://localhost:3000/api/files/abc.jpg")
            assert result is not None

    @pytest.mark.asyncio
    async def test_is_waha_url_rejects_wrong_port(self):
        """Host sama tapi port beda → ditolak."""
        with patch.object(main_module, "WAHA_URL", "http://waha:3000"):
            assert main_module._is_waha_url("http://waha:9999/x.jpg") is False

    @pytest.mark.asyncio
    async def test_download_media_blocks_foreign_host(self):
        """_download_media menolak (return None) untuk host asing — tanpa fetch."""
        with patch.object(main_module, "WAHA_URL", "http://waha:3000"), \
             patch.object(main_module.httpx, "AsyncClient") as mock_client:
            result = await main_module._download_media("http://waha.attacker.com/x.jpg")
            assert result is None
            mock_client.assert_not_called()  # TIDAK ada request keluar

    def test_media_proxy_blocks_substring_bypass(self):
        """Regression dari audit produksi: waha.attacker.com dulu 502 (fetch nyata),
        kini harus 400 tanpa fetch."""
        with patch.object(main_module, "WAHA_URL", "http://waha:3000"), \
             patch.object(main_module.httpx, "AsyncClient") as mock_client:
            tc = TestClient(main_module.app)
            resp = tc.get("/api/media/proxy?url=http%3A%2F%2Fwaha.attacker.com%2Fx.jpg")
            assert resp.status_code == 400
            mock_client.assert_not_called()

    def test_serve_media_file_invalid_filename(self):
        """serve_media_file should reject invalid filenames."""
        tc = TestClient(main_module.app)
        # Filename with special chars should be rejected by regex
        resp = tc.get("/api/media/file/abc!@#.jpg")
        assert resp.status_code == 400

    def test_serve_media_file_not_found(self):
        """serve_media_file should return 404 for missing files."""
        tc = TestClient(main_module.app)
        # Valid filename pattern but file doesn't exist
        resp = tc.get("/api/media/file/abcdef1234567890abcdef1234567890.jpg")
        assert resp.status_code == 404

    def test_media_proxy_success(self, mock_waha):
        """media_proxy should proxy WAHA media with streaming response."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "image/jpeg"}
        mock_resp.aread = AsyncMock(return_value=b"fake image data")

        async def fake_aiter_bytes(chunk_size):
            yield b"fake image data"

        mock_resp.aiter_bytes = fake_aiter_bytes

        async_client = AsyncMock()
        async_client.get.return_value = mock_resp

        with patch.object(main_module, "WAHA_URL", "http://waha:3000"), \
             patch.object(main_module.httpx, "AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(return_value=async_client)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            tc = TestClient(main_module.app)
            resp = tc.get("/api/media/proxy?url=http://waha:3000/api/files/abc.jpg")
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "image/jpeg"


class TestSoftDeleteWebhook:
    """v1.13 — case terhapus (deleted_at) tidak boleh di-update via webhook/reminder."""

    def test_find_case_by_code_excludes_deleted(self):
        mock_conn, mock_cursor = _make_mock_db()
        with patch.object(main_module, "db", return_value=mock_conn):
            main_module.find_case_by_code("INC123")
            sql = mock_cursor.execute.call_args[0][0]
            assert "deleted_at IS NULL" in sql

    def test_find_case_by_chain_excludes_deleted(self):
        mock_conn, mock_cursor = _make_mock_db()
        mock_cursor.fetchone.return_value = None
        with patch.object(main_module, "db", return_value=mock_conn):
            main_module.find_case_by_chain("msg_123")
            sqls = [c[0][0] for c in mock_cursor.execute.call_args_list]
            assert all("deleted_at IS NULL" in s for s in sqls if "FROM cases" in s)

    def test_open_case_codes_excludes_deleted(self):
        mock_conn, mock_cursor = _make_mock_db()
        mock_cursor.fetchall.return_value = []
        with patch.object(main_module, "db", return_value=mock_conn):
            main_module.open_case_codes()
            sql = mock_cursor.execute.call_args[0][0]
            assert "deleted_at IS NULL" in sql

    def test_auto_reminder_query_excludes_deleted(self):
        mock_conn, mock_cursor = _make_mock_db()
        mock_cursor.fetchall.return_value = []
        with patch.object(main_module, "db", return_value=mock_conn):
            import asyncio
            asyncio.get_event_loop().run_until_complete(
                main_module.run_auto_reminders(request=None))

    def test_pending_reminders_excludes_deleted(self, mock_waha):
        mock_conn, mock_cursor = _make_mock_db()
        mock_cursor.fetchall.return_value = []
        with patch.object(main_module, "db", return_value=mock_conn):
            tc = TestClient(main_module.app)
            r = tc.get("/api/reminders/pending?hours=0")
            assert r.status_code == 200
            sql = mock_cursor.execute.call_args[0][0]
            assert "deleted_at IS NULL" in sql


class TestSoftDeleteWebhook:
    """v1.13 — case terhapus (deleted_at) tidak boleh di-update via webhook/reminder."""

    def test_find_case_by_code_excludes_deleted(self):
        mock_conn, mock_cursor = _make_mock_db()
        with patch.object(main_module, "db", return_value=mock_conn):
            main_module.find_case_by_code("INC123")
            sql = mock_cursor.execute.call_args[0][0]
            assert "deleted_at IS NULL" in sql

    def test_find_case_by_chain_excludes_deleted(self):
        mock_conn, mock_cursor = _make_mock_db()
        mock_cursor.fetchone.return_value = None
        with patch.object(main_module, "db", return_value=mock_conn):
            main_module.find_case_by_chain("msg_123")
            sqls = [c[0][0] for c in mock_cursor.execute.call_args_list]
            assert all("deleted_at IS NULL" in s for s in sqls if "FROM cases" in s)

    def test_open_case_codes_excludes_deleted(self):
        mock_conn, mock_cursor = _make_mock_db()
        mock_cursor.fetchall.return_value = []
        with patch.object(main_module, "db", return_value=mock_conn):
            main_module.open_case_codes()
            sql = mock_cursor.execute.call_args[0][0]
            assert "deleted_at IS NULL" in sql

    def test_pending_reminders_excludes_deleted(self, mock_waha):
        mock_conn, mock_cursor = _make_mock_db()
        mock_cursor.fetchall.return_value = []
        with patch.object(main_module, "db", return_value=mock_conn):
            tc = TestClient(main_module.app)
            r = tc.get("/api/reminders/pending?hours=0")
            assert r.status_code == 200
            sql = mock_cursor.execute.call_args[0][0]
            assert "deleted_at IS NULL" in sql


class TestReminder:
    def test_reminder_manual_success(self, mock_waha):
        """Manual reminder should send reply and update DB."""
        mock_conn, mock_cursor = _make_mock_db(fetchone_sequence=[
            {"id": 6, "status": "open", "group_id": 1, "wa_message_id": "case_mid_123",
             "mentions": [{"number": "6281234567890", "name": "Budi"}],
             "reminder_count": 0},
            {"id": 1, "name": "Grup A", "chat_id": "120363xxx@g.us"},  # group resolution
            None,  # UPDATE reminder_count
            None,  # INSERT reminder_log
        ])
        with patch.object(main_module, "db", return_value=mock_conn), \
             patch.object(main_module, "resolve_contact_name", new_callable=AsyncMock, return_value=None):
            tc = TestClient(main_module.app)
            response = tc.post("/api/cases/6/reminder", json={})
            assert response.status_code == 200
            data = response.json()
            assert data["ok"] is True
            assert data["reminder_count"] == 1
            assert data["wa_message_id"] == "test_msg_123"
            assert "follow up" in data["message"]

    def test_reminder_case_not_found(self, mock_waha):
        mock_conn, mock_cursor = _make_mock_db(fetchone_sequence=[None])
        with patch.object(main_module, "db", return_value=mock_conn):
            tc = TestClient(main_module.app)
            response = tc.post("/api/cases/99999/reminder", json={})
            assert response.status_code == 404

    def test_reminder_case_done(self, mock_waha):
        """Cannot send reminder to a done case."""
        mock_conn, mock_cursor = _make_mock_db(fetchone_sequence=[
            {"id": 6, "status": "done", "group_id": 1, "wa_message_id": "case_mid_123",
             "mentions": [], "reminder_count": 0},
        ])
        with patch.object(main_module, "db", return_value=mock_conn):
            tc = TestClient(main_module.app)
            response = tc.post("/api/cases/6/reminder", json={})
            assert response.status_code == 400
            assert "done" in response.json()["detail"]

    def test_reminder_custom_message(self, mock_waha):
        mock_conn, mock_cursor = _make_mock_db(fetchone_sequence=[
            {"id": 6, "status": "open", "group_id": 1, "wa_message_id": "case_mid_123",
             "mentions": [{"number": "6281234567890", "name": "Budi"}],
             "reminder_count": 0},
            {"id": 1, "name": "Grup A", "chat_id": "120363xxx@g.us"},  # group resolution
            None,  # UPDATE
            None,  # INSERT log
        ])
        with patch.object(main_module, "db", return_value=mock_conn), \
             patch.object(main_module, "resolve_contact_name", new_callable=AsyncMock, return_value=None):
            tc = TestClient(main_module.app)
            response = tc.post("/api/cases/6/reminder", json={
                "message": "tolong segera di-follow up ya!",
            })
            assert response.status_code == 200
            data = response.json()
            assert data["message"] == "tolong segera di-follow up ya!"

    def test_reminder_history(self, mock_waha):
        mock_conn, mock_cursor = _make_mock_db(fetchone_sequence=[
            {"id": 6},  # case exists
            [{"id": 1, "triggered_by": "manual", "message": "follow up"},
             {"id": 2, "triggered_by": "cron", "message": "follow up"}],
        ])
        with patch.object(main_module, "db", return_value=mock_conn):
            tc = TestClient(main_module.app)
            response = tc.get("/api/cases/6/reminder")
            assert response.status_code == 200

    def test_reminder_history_case_not_found(self, mock_waha):
        mock_conn, mock_cursor = _make_mock_db(fetchone_sequence=[None])
        with patch.object(main_module, "db", return_value=mock_conn):
            tc = TestClient(main_module.app)
            response = tc.get("/api/cases/99999/reminder")
            assert response.status_code == 404

    def test_run_auto_reminders(self, mock_waha):
        mock_conn, mock_cursor = _make_mock_db(fetchone_sequence=[
            {"id": 1, "name": "Grup A", "chat_id": "120363xxx@g.us"},  # group resolution
            None,  # UPDATE
            None,  # INSERT log
        ])
        mock_cursor.fetchall.return_value = [
            {"id": 6, "status": "open", "group_id": 1, "wa_message_id": "case_mid_123",
             "mentions": [{"number": "6281234567890", "name": "Budi"}],
             "reminder_count": 0},
        ]
        with patch.object(main_module, "db", return_value=mock_conn), \
             patch.object(main_module, "resolve_contact_name", new_callable=AsyncMock, return_value=None):
            tc = TestClient(main_module.app)
            response = tc.post("/api/reminders/run?hours=2")
            assert response.status_code == 200
            data = response.json()
            assert data["checked"] == 1
            assert data["reminded"] == 1

    def test_run_auto_reminders_no_cases(self, mock_waha):
        mock_conn, mock_cursor = _make_mock_db()
        mock_cursor.fetchall.return_value = []
        with patch.object(main_module, "db", return_value=mock_conn):
            tc = TestClient(main_module.app)
            response = tc.post("/api/reminders/run?hours=2")
            assert response.status_code == 200
            data = response.json()
            assert data["checked"] == 0
            assert data["reminded"] == 0

    def test_list_pending_reminders(self, mock_waha):
        mock_conn, mock_cursor = _make_mock_db(fetchone_sequence=[
            [{"id": 6, "case_code": "INC123", "idle_hours": 5.2,
              "reminder_count": 1}],
        ])
        with patch.object(main_module, "db", return_value=mock_conn):
            tc = TestClient(main_module.app)
            response = tc.get("/api/reminders/pending?hours=2")
            assert response.status_code == 200

    def test_reminder_no_mentions(self, mock_waha):
        """Reminder should work even when case has no stored mentions."""
        mock_conn, mock_cursor = _make_mock_db(fetchone_sequence=[
            {"id": 6, "status": "open", "group_id": 1, "wa_message_id": "case_mid_123",
             "mentions": [], "reminder_count": 0},
            {"id": 1, "name": "Grup A", "chat_id": "120363xxx@g.us"},  # group resolution
            None,  # UPDATE
            None,  # INSERT log
        ])
        with patch.object(main_module, "db", return_value=mock_conn), \
             patch.object(main_module, "resolve_contact_name", new_callable=AsyncMock, return_value=None):
            tc = TestClient(main_module.app)
            response = tc.post("/api/cases/6/reminder", json={})
            assert response.status_code == 200
            data = response.json()
            assert data["ok"] is True


class TestCasePreview:
    """POST /api/cases/preview — render teks tanpa kirim & tanpa DB write."""

    BODY = {
        "jenis_case": "Non Order",
        "fields": {"ticket_remedy": "INCPREV01", "detail_case": "uji preview"},
    }

    def test_preview_renders_text(self, client):
        tc, mock_cursor = client
        mock_cursor.fetchone.side_effect = [{"id": 1}]  # jenis_case lookup di _render_case_payload
        response = tc.post("/api/cases/preview", json=self.BODY)
        assert response.status_code == 200
        data = response.json()
        assert "#Non Order" in data["text"]
        assert "INCPREV01" in data["text"]
        assert data["mentions"] == []

    def test_preview_no_waha_call(self, client, mock_waha):
        """Preview TIDAK boleh memanggil WAHA sama sekali."""
        tc, mock_cursor = client
        mock_cursor.fetchone.side_effect = [{"id": 1}]
        response = tc.post("/api/cases/preview", json=self.BODY)
        assert response.status_code == 200
        mock_waha.post.assert_not_called()

    def test_preview_no_insert(self, client):
        """Preview hanya boleh SELECT — tidak ada INSERT/UPDATE ke mana pun."""
        tc, mock_cursor = client
        mock_cursor.fetchone.side_effect = [{"id": 1}]
        tc.post("/api/cases/preview", json=self.BODY)
        for call_args in mock_cursor.execute.call_args_list:
            sql = call_args[0][0]
            assert "INSERT INTO" not in sql.upper(), f"Preview melakukan write: {sql}"
            assert "UPDATE " not in sql.upper()

    def test_preview_with_custom_header_and_mentions(self, client):
        tc, mock_cursor = client
        mock_cursor.fetchone.side_effect = [{"id": 1}]
        response = tc.post("/api/cases/preview", json={
            "jenis_case": "Non Order",
            "mentions": [{"number": "6281234567890", "name": "Budi"}],
            "custom_header": "Halo {phone} mohon bantuan",
            "fields": {"ticket_remedy": "INCPREV02"},
        })
        assert response.status_code == 200
        data = response.json()
        assert "Halo @6281234567890 mohon bantuan" in data["text"]
        assert data["mentions"] == [{"number": "6281234567890", "name": "Budi"}]

    def test_preview_parity_with_create_case(self, client):
        """Teks preview HARUS identik dengan teks create_case untuk input yang sama."""
        tc, mock_cursor = client
        seq = [{"id": 1}]  # jenis lookup (dipakai preview & create)
        seq += [{"id": 1, "name": "Grup A", "chat_id": "120363xxx@g.us"}]  # _get_group
        seq += [{"id": 42, "case_code": "INCPREV03"}, None]  # INSERT RETURNING + wa_messages
        mock_cursor.fetchone.side_effect = seq

        body = {
            "group_id": 1,
            "jenis_case": "Non Order",
            "fields": {"ticket_remedy": "INCPREV03", "detail_case": "paritas"},
        }
        prev_resp = tc.post("/api/cases/preview", json=body)
        assert prev_resp.status_code == 200
        # create_case memakai sequence yang sama (jenis lookup dipanggil lagi dari awal)
        mock_cursor.fetchone.side_effect = seq
        create_resp = tc.post("/api/cases", json=body)
        assert create_resp.status_code == 201
        assert create_resp.json()["text"] == prev_resp.json()["text"]


class TestCaseTestSend:
    """POST /api/cases/test-send — kirim ke grup test tanpa membuat case."""

    BODY = {
        "jenis_case": "Non Order",
        "fields": {"ticket_remedy": "INCTESTSEND", "detail_case": "uji test-send"},
    }

    def test_test_send_to_default_group(self, client, mock_waha):
        tc, mock_cursor = client
        mock_cursor.fetchone.side_effect = [
            {"id": 5, "name": "Test Development", "chat_id": "120363999@g.us",
             "is_default": True},  # _get_default_group (dipanggil sebelum render)
            {"id": 1},  # jenis lookup di _render_case_payload
        ]
        response = tc.post("/api/cases/test-send", json=self.BODY)
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["test_group_id"] == 5
        assert data["test_group_name"] == "Test Development"
        assert "INCTESTSEND" in data["text"]
        # WAHA dipanggil dengan chat_id grup test
        payload = mock_waha.post.call_args[1]["json"]
        assert payload["chatId"] == "120363999@g.us"

    def test_test_send_with_test_group_override(self, client, mock_waha):
        tc, mock_cursor = client
        mock_cursor.fetchone.side_effect = [
            {"id": 3, "name": "Grup Lain", "chat_id": "120363777@g.us"},  # _get_group(3)
            {"id": 1},  # jenis lookup di _render_case_payload
        ]
        response = tc.post("/api/cases/test-send", json={**self.BODY, "test_group_id": 3})
        assert response.status_code == 200
        data = response.json()
        assert data["test_group_id"] == 3
        payload = mock_waha.post.call_args[1]["json"]
        assert payload["chatId"] == "120363777@g.us"

    def test_test_send_no_case_insert(self, client):
        """Test-send TIDAK boleh menulis ke cases/wa_messages."""
        tc, mock_cursor = client
        mock_cursor.fetchone.side_effect = [
            {"id": 5, "name": "Test Development", "chat_id": "120363999@g.us", "is_default": True},
            {"id": 1},
        ]
        tc.post("/api/cases/test-send", json=self.BODY)
        for call_args in mock_cursor.execute.call_args_list:
            sql = call_args[0][0]
            assert "INSERT INTO" not in sql.upper(), f"Test-send melakukan write: {sql}"

    def test_test_send_no_default_group_returns_400(self, client):
        tc, mock_cursor = client
        mock_cursor.fetchone.side_effect = [
            None,       # _get_default_group → tidak ada (validasi sebelum render)
        ]
        response = tc.post("/api/cases/test-send", json=self.BODY)
        assert response.status_code == 400
        assert "default" in response.json()["detail"]

    def test_test_send_unknown_group_returns_422(self, client):
        tc, mock_cursor = client
        mock_cursor.fetchone.side_effect = [
            None,       # _get_group(999) → tidak aktif
            None,       # _get_group_any(999) → tidak ada barisnya
        ]
        response = tc.post("/api/cases/test-send", json={**self.BODY, "test_group_id": 999})
        assert response.status_code == 422
        assert "999" in response.json()["detail"]

    def test_test_send_inactive_group_returns_409(self, client):
        tc, mock_cursor = client
        mock_cursor.fetchone.side_effect = [
            None,       # _get_group(2) → tidak aktif
            {"id": 2, "name": "Grup Nonaktif", "chat_id": "120363002@g.us"},  # _get_group_any
        ]
        response = tc.post("/api/cases/test-send", json={**self.BODY, "test_group_id": 2})
        assert response.status_code == 409
        assert "Grup Nonaktif" in response.json()["detail"]

    def test_test_send_route_not_swallowed_by_case_id(self, client):
        """Route ordering: /api/cases/test-send tidak boleh tertelan GET /api/cases/{case_id}.

        (POST vs GET beda method, tapi guard ini memastikan route terdaftar benar.)
        """
        tc, _ = client
        # Body kosong diterima (semua field opsional di CaseIn) → handler jalan;
        # gagal di _get_default_group karena mock tanpa fetchone → kalau route
        # tersamar ke /{case_id}, POST /api/cases/test-send tidak akan 200 di sini.
        resp = tc.post("/api/cases/test-send", json={})
        assert resp.status_code in (200, 400, 500)

    def test_preview_route_not_swallowed(self, client):
        """Route ordering: /api/cases/preview harus match endpoint preview, bukan /{case_id}."""
        tc, mock_cursor = client
        mock_cursor.fetchone.side_effect = [{"id": 1}]
        resp = tc.post("/api/cases/preview", json=self.BODY)
        assert resp.status_code == 200
        assert "text" in resp.json()
