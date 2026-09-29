"""Unit tests for solver-reply notifications to the default (test) group (v1.23)."""
import os
import sys
import pytest
from unittest.mock import patch, AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import importlib
main_module = importlib.import_module("main-v1-1")

GROUP_A = {"id": 1, "name": "Grup A", "chat_id": "120363001@g.us", "is_active": True}
DEFAULT_GROUP = {"id": 3, "name": "Test Development", "chat_id": "120363003@g.us",
                 "is_active": True, "is_default": True}

INC_CODE = "INC000023470"  # 9+ digit sesuai INC_RE = \bINC\d{9,}\b

NOTIF_CASE = {"id": 10, "case_code": INC_CODE, "group_id": 1,
              "fields": {"ticket_remedy": INC_CODE, "no_indihome": "0211234567"}}
NOTIF_CASE_INTERNAL = {"id": 11, "case_code": "1-SO9BLGS", "group_id": 1,
                       "fields": {"case_id": "1-SO9BLGS", "no_indihome": "141410121054"}}
NOTIF_CASE_BOTH = {"id": 12, "case_code": "1-SO9BLGS", "group_id": 1,
                   "fields": {"ticket_remedy": "INC012345678", "case_id": "1-SO9BLGS",
                              "no_indihome": "141410121054"}}
NOTIF_CASE_BARE = {"id": 13, "case_code": "INC999999999", "group_id": 1, "fields": {}}


def _make_mock_db(fetchone_sequence=None):
    from unittest.mock import MagicMock
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


def _payload(body=None, chat=None):
    body = body or f"done {INC_CODE}"
    return {
        "id": {"_serialized": "true_123@g.us_ABC"},
        "body": body,
        "from": chat or GROUP_A["chat_id"],
        "participant": "6281234567890@c.us",
    }


class TestCaseReplyNotification:
    @pytest.mark.asyncio
    async def test_notification_sent_to_default_group(self):
        """Balasan solver -> notif ke grup default dengan identifier lengkap."""
        mock_conn, _ = _make_mock_db(fetchone_sequence=[GROUP_A, DEFAULT_GROUP])
        with patch.object(main_module, "db", return_value=mock_conn), \
             patch.object(main_module, "resolve_contact_name", new_callable=AsyncMock, return_value=None), \
             patch.object(main_module, "store_message"), \
             patch.object(main_module, "find_case_by_code", return_value=NOTIF_CASE), \
             patch.object(main_module, "link_and_update"), \
             patch.object(main_module, "waha_send", new_callable=AsyncMock) as mock_send:
            result = await main_module.handle_message(_payload())
            assert result is True
            mock_send.assert_awaited_once()
            text = mock_send.call_args[0][0]
            kwargs = mock_send.call_args[1]
            assert kwargs["chat_id"] == DEFAULT_GROUP["chat_id"]
            assert f"Ticket Remedy : {INC_CODE}" in text
            assert "(IH 0211234567)" in text
            assert "Dibalas oleh Solver:" in text  # author_name None -> fallback

    @pytest.mark.asyncio
    async def test_notification_case_id_label(self):
        """Case kode internal -> label Case ID, bukan Ticket Remedy."""
        mock_conn, _ = _make_mock_db(fetchone_sequence=[GROUP_A, DEFAULT_GROUP])
        with patch.object(main_module, "db", return_value=mock_conn), \
             patch.object(main_module, "resolve_contact_name", new_callable=AsyncMock, return_value=None), \
             patch.object(main_module, "store_message"), \
             patch.object(main_module, "find_case_by_code", return_value=NOTIF_CASE_INTERNAL), \
             patch.object(main_module, "link_and_update"), \
             patch.object(main_module, "waha_send", new_callable=AsyncMock) as mock_send:
            await main_module.handle_message(_payload())
            text = mock_send.call_args[0][0]
            assert "Case ID : 1-SO9BLGS" in text
            assert "Ticket Remedy" not in text
            assert "(IH 141410121054)" in text

    @pytest.mark.asyncio
    async def test_notification_shows_both_identifiers(self):
        """Ticket Remedy DAN Case ID keduanya ada -> keduanya tampil dipisah ' | '."""
        mock_conn, _ = _make_mock_db(fetchone_sequence=[GROUP_A, DEFAULT_GROUP])
        with patch.object(main_module, "db", return_value=mock_conn), \
             patch.object(main_module, "resolve_contact_name", new_callable=AsyncMock, return_value=None), \
             patch.object(main_module, "store_message"), \
             patch.object(main_module, "find_case_by_code", return_value=NOTIF_CASE_BOTH), \
             patch.object(main_module, "link_and_update"), \
             patch.object(main_module, "waha_send", new_callable=AsyncMock) as mock_send:
            await main_module.handle_message(_payload())
            text = mock_send.call_args[0][0]
            assert "Ticket Remedy : INC012345678 | Case ID : 1-SO9BLGS" in text
            assert "Status: done" in text

    @pytest.mark.asyncio
    async def test_notification_fallback_case_code(self):
        """Tanpa ticket_remedy & case_id -> fallback 'Case : <case_code>' tanpa IH."""
        mock_conn, _ = _make_mock_db(fetchone_sequence=[GROUP_A, DEFAULT_GROUP])
        with patch.object(main_module, "db", return_value=mock_conn), \
             patch.object(main_module, "resolve_contact_name", new_callable=AsyncMock, return_value=None), \
             patch.object(main_module, "store_message"), \
             patch.object(main_module, "find_case_by_code", return_value=NOTIF_CASE_BARE), \
             patch.object(main_module, "link_and_update"), \
             patch.object(main_module, "waha_send", new_callable=AsyncMock) as mock_send:
            await main_module.handle_message(_payload())
            text = mock_send.call_args[0][0]
            assert "Case : INC999999999" in text
            assert "(IH" not in text

    @pytest.mark.asyncio
    async def test_notification_anti_loop_same_group(self):
        """Grup default == grup asal pesan -> TIDAK kirim notif (anti-loop)."""
        mock_conn, _ = _make_mock_db(fetchone_sequence=[DEFAULT_GROUP, DEFAULT_GROUP])
        case_in_test_group = {**NOTIF_CASE, "group_id": 3}  # milik grup test
        with patch.object(main_module, "db", return_value=mock_conn), \
             patch.object(main_module, "resolve_contact_name", new_callable=AsyncMock, return_value=None), \
             patch.object(main_module, "store_message"), \
             patch.object(main_module, "find_case_by_code", return_value=case_in_test_group), \
             patch.object(main_module, "link_and_update"), \
             patch.object(main_module, "waha_send", new_callable=AsyncMock) as mock_send:
            result = await main_module.handle_message(_payload(chat=DEFAULT_GROUP["chat_id"]))
            assert result is True
            mock_send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_notification_no_default_group(self):
        """Tidak ada grup default -> skip, tidak error."""
        mock_conn, _ = _make_mock_db(fetchone_sequence=[GROUP_A, None])
        with patch.object(main_module, "db", return_value=mock_conn), \
             patch.object(main_module, "resolve_contact_name", new_callable=AsyncMock, return_value=None), \
             patch.object(main_module, "store_message"), \
             patch.object(main_module, "find_case_by_code", return_value=NOTIF_CASE), \
             patch.object(main_module, "link_and_update"), \
             patch.object(main_module, "waha_send", new_callable=AsyncMock) as mock_send:
            result = await main_module.handle_message(_payload())
            assert result is True
            mock_send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_notification_waha_failure_non_fatal(self):
        """WAHA error -> notif gagal tapi update case tetap sukses."""
        mock_conn, _ = _make_mock_db(fetchone_sequence=[GROUP_A, DEFAULT_GROUP])
        with patch.object(main_module, "db", return_value=mock_conn), \
             patch.object(main_module, "resolve_contact_name", new_callable=AsyncMock, return_value=None), \
             patch.object(main_module, "store_message"), \
             patch.object(main_module, "find_case_by_code", return_value=NOTIF_CASE), \
             patch.object(main_module, "link_and_update") as mock_link, \
             patch.object(main_module, "waha_send", new_callable=AsyncMock,
                          side_effect=RuntimeError("waha down")):
            result = await main_module.handle_message(_payload())
            assert result is True
            mock_link.assert_called_once()

    @pytest.mark.asyncio
    async def test_notification_long_body_truncated(self):
        """Body > 300 char -> terpotong dengan ellipsis."""
        mock_conn, _ = _make_mock_db(fetchone_sequence=[GROUP_A, DEFAULT_GROUP])
        long_body = f"done {INC_CODE} " + "x" * 500
        with patch.object(main_module, "db", return_value=mock_conn), \
             patch.object(main_module, "resolve_contact_name", new_callable=AsyncMock, return_value=None), \
             patch.object(main_module, "store_message"), \
             patch.object(main_module, "find_case_by_code", return_value=NOTIF_CASE), \
             patch.object(main_module, "link_and_update"), \
             patch.object(main_module, "waha_send", new_callable=AsyncMock) as mock_send:
            await main_module.handle_message(_payload(body=long_body))
            text = mock_send.call_args[0][0]
            assert "…" in text
            assert "x" * 301 not in text

    @pytest.mark.asyncio
    async def test_notification_uses_author_name(self):
        """Nama solver dari resolve_contact_name dipakai di pesan notif."""
        mock_conn, _ = _make_mock_db(fetchone_sequence=[GROUP_A, DEFAULT_GROUP])
        with patch.object(main_module, "db", return_value=mock_conn), \
             patch.object(main_module, "resolve_contact_name", new_callable=AsyncMock,
                          return_value="Furqon Nugroho"), \
             patch.object(main_module, "store_message"), \
             patch.object(main_module, "find_case_by_code", return_value=NOTIF_CASE), \
             patch.object(main_module, "link_and_update"), \
             patch.object(main_module, "waha_send", new_callable=AsyncMock) as mock_send:
            await main_module.handle_message(_payload())
            text = mock_send.call_args[0][0]
            assert "Dibalas oleh Furqon Nugroho:" in text


# ============ Notifikasi bersmedia ke grup test (v1.28) ============

class TestCaseReplyNotificationMedia:
    """v1.28: balasan solver yang bermmedia → media ikut dikirim ke grup
    notif (sendImage/sendFile), teks notif jadi caption (anti double-send)."""

    IMG_TYPE = "image/jpeg"
    PDF_TYPE = "application/pdf"

    @pytest.fixture
    def media_file(self, tmp_path):
        """File media palsu di MEDIA_DIR (simulasi hasil _download_media)."""
        p = tmp_path / "abc123.jpg"
        p.write_bytes(b"\xff\xd8\xff\xe0fakejpeg")
        return str(p)

    def _media_payload(self, media_url, media_type=IMG_TYPE):
        p = _payload()
        p["hasMedia"] = True
        p["media"] = {"url": media_url or "http://waha:3000/api/files/abc.jpg",
                      "mimetype": media_type}
        return p

    @pytest.mark.asyncio
    async def test_image_replied_to_test_group_as_caption(self, media_file):
        """Balasan + image → HANYA sendImage (caption = teks notif), tanpa sendText."""
        mock_conn, _ = _make_mock_db(fetchone_sequence=[GROUP_A, DEFAULT_GROUP])
        with patch.object(main_module, "db", return_value=mock_conn), \
             patch.object(main_module, "resolve_contact_name", new_callable=AsyncMock, return_value=None), \
             patch.object(main_module, "store_message"), \
             patch.object(main_module, "find_case_by_code", return_value=NOTIF_CASE), \
             patch.object(main_module, "link_and_update"), \
             patch.object(main_module, "MEDIA_DIR", os.path.dirname(media_file)), \
             patch.object(main_module, "waha_send", new_callable=AsyncMock) as mock_send, \
             patch.object(main_module, "waha_send_media", new_callable=AsyncMock) as mock_media:
            # media_url hasil _download_and_rewrite_media = URL publik berisi nama file
            await main_module.handle_message(
                self._media_payload(f"http://x/api/media/file/{os.path.basename(media_file)}"))
            mock_send.assert_not_awaited()  # anti double-send
            mock_media.assert_awaited_once()
            endpoint, payload = mock_media.call_args[0]
            assert endpoint == "sendImage"
            assert payload["chatId"] == DEFAULT_GROUP["chat_id"]
            assert payload["file"]["mimetype"] == self.IMG_TYPE
            assert payload["file"]["data"]  # base64 terisi
            assert f"Ticket Remedy : {INC_CODE}" in payload["caption"]

    @pytest.mark.asyncio
    async def test_pdf_replied_uses_sendfile(self, media_file):
        """Media non-image → sendFile, caption tetap teks notif."""
        pdf = media_file.replace(".jpg", ".pdf")
        with open(pdf, "wb") as f:
            f.write(b"%PDF-1.4 fake")
        mock_conn, _ = _make_mock_db(fetchone_sequence=[GROUP_A, DEFAULT_GROUP])
        with patch.object(main_module, "db", return_value=mock_conn), \
             patch.object(main_module, "resolve_contact_name", new_callable=AsyncMock, return_value=None), \
             patch.object(main_module, "store_message"), \
             patch.object(main_module, "find_case_by_code", return_value=NOTIF_CASE), \
             patch.object(main_module, "link_and_update"), \
             patch.object(main_module, "MEDIA_DIR", os.path.dirname(pdf)), \
             patch.object(main_module, "waha_send", new_callable=AsyncMock) as mock_send, \
             patch.object(main_module, "waha_send_media", new_callable=AsyncMock) as mock_media:
            await main_module.handle_message(
                self._media_payload(f"http://x/api/media/file/{os.path.basename(pdf)}",
                                    media_type=self.PDF_TYPE))
            mock_send.assert_not_awaited()
            endpoint, payload = mock_media.call_args[0]
            assert endpoint == "sendFile"
            assert payload["file"]["mimetype"] == self.PDF_TYPE

    @pytest.mark.asyncio
    async def test_media_missing_falls_back_to_text(self, media_file):
        """File media hilang dari MEDIA_DIR → fallback kirim teks polos."""
        mock_conn, _ = _make_mock_db(fetchone_sequence=[GROUP_A, DEFAULT_GROUP])
        with patch.object(main_module, "db", return_value=mock_conn), \
             patch.object(main_module, "resolve_contact_name", new_callable=AsyncMock, return_value=None), \
             patch.object(main_module, "store_message"), \
             patch.object(main_module, "find_case_by_code", return_value=NOTIF_CASE), \
             patch.object(main_module, "link_and_update"), \
             patch.object(main_module, "MEDIA_DIR", os.path.dirname(media_file)), \
             patch.object(main_module, "waha_send", new_callable=AsyncMock) as mock_send, \
             patch.object(main_module, "waha_send_media", new_callable=AsyncMock) as mock_media:
            await main_module.handle_message(
                self._media_payload("http://x/api/media/file/tidak-ada-999.jpg"))
            mock_media.assert_not_awaited()
            mock_send.assert_awaited_once()  # fallback teks

    @pytest.mark.asyncio
    async def test_media_send_failure_falls_back_to_text(self, media_file):
        """sendImage error → tetap kirim teks (notif fire-and-forget, tidak raise)."""
        mock_conn, _ = _make_mock_db(fetchone_sequence=[GROUP_A, DEFAULT_GROUP])
        with patch.object(main_module, "db", return_value=mock_conn), \
             patch.object(main_module, "resolve_contact_name", new_callable=AsyncMock, return_value=None), \
             patch.object(main_module, "store_message"), \
             patch.object(main_module, "find_case_by_code", return_value=NOTIF_CASE), \
             patch.object(main_module, "link_and_update"), \
             patch.object(main_module, "MEDIA_DIR", os.path.dirname(media_file)), \
             patch.object(main_module, "waha_send", new_callable=AsyncMock) as mock_send, \
             patch.object(main_module, "waha_send_media", new_callable=AsyncMock,
                          side_effect=RuntimeError("waha down")) as mock_media:
            await main_module.handle_message(
                self._media_payload(f"http://x/api/media/file/{os.path.basename(media_file)}"))
            mock_media.assert_awaited_once()
            mock_send.assert_awaited_once()  # fallback teks tetap jalan

    @pytest.mark.asyncio
    async def test_no_media_still_sends_text_only(self):
        """Tanpa media → perilaku lama: sendText saja (regresi guard)."""
        mock_conn, _ = _make_mock_db(fetchone_sequence=[GROUP_A, DEFAULT_GROUP])
        with patch.object(main_module, "db", return_value=mock_conn), \
             patch.object(main_module, "resolve_contact_name", new_callable=AsyncMock, return_value=None), \
             patch.object(main_module, "store_message"), \
             patch.object(main_module, "find_case_by_code", return_value=NOTIF_CASE), \
             patch.object(main_module, "link_and_update"), \
             patch.object(main_module, "waha_send", new_callable=AsyncMock) as mock_send, \
             patch.object(main_module, "waha_send_media", new_callable=AsyncMock) as mock_media:
            await main_module.handle_message(_payload())
            mock_media.assert_not_awaited()
            mock_send.assert_awaited_once()



# ============ Test bump updated_at tiap balasan solver (v1.25) ============

class TestLinkAndUpdateBumpsUpdatedAt:
    """v1.25: SEMUA balasan ter-link harus UPDATE cases.updated_at, bukan hanya
    yang mengubah status — supaya sort list "terbaru di atas" reflektif."""

    def _calls(self, status):
        mock_conn, mock_cursor = _make_mock_db()
        with patch.object(main_module, "db", return_value=mock_conn):
            main_module.link_and_update(56, "wm-1", "author@lid", "ijin up", status, "note", "reply", None)
        return [c.args[0] for c in mock_cursor.execute.call_args_list if c.args]

    def test_reply_without_status_bumps_updated_at(self):
        """Balasan TANPA keyword status → tetap ada UPDATE updated_at (inti bug)."""
        sqls = self._calls(status=None)
        bump = [s for s in sqls if "UPDATE cases SET updated_at = now()" in s]
        assert len(bump) == 1

    def test_reply_with_status_updates_both(self):
        """Balasan dengan keyword status → UPDATE status + updated_at sekaligus."""
        sqls = self._calls(status="done")
        assert any("UPDATE cases SET status = %s, updated_at = now()" in s for s in sqls)
        # tidak boleh ada bump ganda
        assert not any("UPDATE cases SET updated_at = now() WHERE" in s and "status" not in s
                       for s in sqls)
