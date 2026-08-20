"""Tests for templates.py"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from templates import CASE_TYPES, render_case_text, render_header, CASE_FIELDS


def test_case_types_list():
    assert "stc" in CASE_TYPES
    assert "smooa" in CASE_TYPES
    assert "mobile" in CASE_TYPES
    assert "ufo" in CASE_TYPES
    assert "other" in CASE_TYPES


def test_render_header_with_mentions():
    result = render_header(
        [{"number": "6281234567890", "name": "Mas Budi"}], "stc"
    )
    assert "@6281234567890" in result
    assert "mohon bantuannya untuk case STC" in result
    assert "punten rekan" in result


def test_render_header_multiple_mentions():
    result = render_header(
        [{"number": "628111", "name": "A"}, {"number": "628222", "name": "B"}],
        "smooa",
    )
    assert "@628111" in result
    assert "@628222" in result


def test_render_header_no_name():
    result = render_header([{"number": "6281234567890"}], "mobile")
    assert "@6281234567890" in result


def test_render_header_empty_mentions():
    result = render_header([], "stc")
    assert "mohon bantuannya untuk case STC" in result

def test_render_case_text_stc():
    fields = {
        "ticket_remedy": "INC123456789",
        "no_indihome": "142401135588",
        "detail_case": "Test case detail",
    }
    result = render_case_text("stc", fields)
    assert "#STC" in result
    assert "Ticket Remedy : INC123456789" in result
    assert "No Indihome : 142401135588" in result
    assert "Detail Case : Test case detail" in result
    # Empty fields should not appear
    assert "Order ID" not in result


def test_render_case_text_skips_empty():
    result = render_case_text("stc", {"ticket_remedy": "INC123"})
    assert "Ticket Remedy : INC123" in result
    lines = result.strip().split("\n")
    # Only header + one field
    assert len(lines) == 2


def test_render_case_text_other():
    result = render_case_text("other", {"raw_text": "Hello world"})
    assert "#Lainnya" in result
    assert "Pesan Lengkap : Hello world" in result


def test_render_case_text_empty_fields():
    result = render_case_text("stc", {})
    assert "#STC" in result
    assert result.strip() == "#STC"


def test_all_case_types_have_fields():
    for ct in CASE_TYPES:
        assert ct in CASE_FIELDS, f"Missing CASE_FIELDS for {ct}"


def test_render_case_text_smooa():
    fields = {
        "grapari": "GraPARI Bandung",
        "ticket_remedy": "INC999",
        "nama_pelanggan": "Budi Santoso",
        "detail_case": "SMOOA test",
    }
    result = render_case_text("smooa", fields)
    assert "#SMOOA" in result
    assert "GraPARI : GraPARI Bandung" in result
    assert "Ticket Remedy : INC999" in result
    assert "Nama Pelanggan : Budi Santoso" in result


def test_render_case_text_mobile():
    fields = {
        "grapari": "GraPARI Jakarta",
        "ticket_remedy": "INC888",
        "msisdn": "6281234567890",
        "detail_case": "Mobile case",
    }
    result = render_case_text("mobile", fields)
    assert "#Case Mobile" in result
    assert "GraPARI : GraPARI Jakarta" in result
    assert "Ticket Remedy : INC888" in result
    assert "MSISDN : 6281234567890" in result


def test_render_case_text_ufo():
    fields = {
        "grapari": "GraPARI Surabaya",
        "case_id": "UFO-123",
        "detail_case": "UFO test",
    }
    result = render_case_text("ufo", fields)
    assert "#UFO" in result
    assert "GraPARI : GraPARI Surabaya" in result
    assert "Case ID : UFO-123" in result


def test_render_case_text_whitespace_only_skipped():
    fields = {
        "ticket_remedy": "  ",
        "detail_case": "valid",
    }
    result = render_case_text("stc", fields)
    assert "Ticket Remedy" not in result
    assert "Detail Case : valid" in result
