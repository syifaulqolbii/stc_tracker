"""Tests for templates.py — v1.2 (Non Order, Non AO, Mobile)"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from templates import CASE_TYPES, render_case_text, render_header, CASE_FIELDS, TYPE_LABELS


def test_case_types_list():
    assert "non_order" in CASE_TYPES
    assert "non_ao" in CASE_TYPES
    assert "mobile" in CASE_TYPES
    # Old types should NOT be in CASE_TYPES
    assert "stc" not in CASE_TYPES
    assert "smooa" not in CASE_TYPES
    assert "ufo" not in CASE_TYPES
    assert "other" not in CASE_TYPES


def test_type_labels():
    assert TYPE_LABELS["non_order"] == "Non Order"
    assert TYPE_LABELS["non_ao"] == "Non AO"
    assert TYPE_LABELS["mobile"] == "Mobile"


def test_render_header_with_mentions():
    result = render_header(
        [{"number": "6281234567890", "name": "Mas Budi"}], "non_order"
    )
    assert "@6281234567890" in result
    assert "mohon bantuannya untuk case Non Order" in result
    assert "punten rekan" in result


def test_render_header_multiple_mentions():
    result = render_header(
        [{"number": "628111", "name": "A"}, {"number": "628222", "name": "B"}],
        "non_ao",
    )
    assert "@628111" in result
    assert "@628222" in result
    assert "Non AO" in result


def test_render_header_no_name():
    result = render_header([{"number": "6281234567890"}], "mobile")
    assert "@6281234567890" in result
    assert "Mobile" in result


def test_render_header_empty_mentions():
    result = render_header([], "non_order")
    assert "mohon bantuannya untuk case Non Order" in result


def test_render_case_text_non_order():
    fields = {
        "ticket_remedy": "INC123456789",
        "no_indihome": "142401135588",
        "detail_case": "Test case detail",
    }
    result = render_case_text("non_order", fields)
    assert "#Non Order" in result
    assert "Ticket Remedy : INC123456789" in result
    assert "No Indihome : 142401135588" in result
    assert "Detail Case : Test case detail" in result
    # Empty fields should not appear
    assert "Order ID" not in result


def test_render_case_text_with_new_fields():
    """Test rendering with area, regional, sumber_ticket, asal_grapari."""
    fields = {
        "ticket_remedy": "INC123",
        "detail_case": "Test",
    }
    result = render_case_text(
        "non_order", fields,
        area_name="Area 1",
        regional_name="Regional 2",
        sumber_ticket="Grapari",
        asal_grapari="GraPARI Bandung",
    )
    assert "#Non Order" in result
    assert "Area : Area 1" in result
    assert "Regional : Regional 2" in result
    assert "Sumber Ticket : Grapari" in result
    assert "Asal Grapari : GraPARI Bandung" in result
    assert "Jenis Case : Non Order" in result
    assert "Ticket Remedy : INC123" in result


def test_render_case_text_without_new_fields():
    """Test rendering without optional new fields."""
    fields = {"detail_case": "Test"}
    result = render_case_text("non_order", fields)
    assert "Area :" not in result
    assert "Regional :" not in result
    assert "Sumber Ticket :" not in result
    assert "Asal Grapari :" not in result
    # But jenis_case is always rendered
    assert "Jenis Case : Non Order" in result


def test_render_case_text_skips_empty():
    result = render_case_text("non_order", {"ticket_remedy": "INC123"})
    assert "Ticket Remedy : INC123" in result
    lines = result.strip().split("\n")
    # Header (#Non Order) + Jenis Case + Ticket Remedy = 3 lines
    assert len(lines) == 3


def test_render_case_text_empty_fields():
    result = render_case_text("non_order", {})
    assert "#Non Order" in result
    # Only header + Jenis Case line
    assert "Jenis Case : Non Order" in result


def test_all_case_types_have_fields():
    for ct in CASE_TYPES:
        assert ct in CASE_FIELDS, f"Missing CASE_FIELDS for {ct}"


def test_render_case_text_non_ao():
    fields = {
        "ticket_remedy": "INC999",
        "detail_case": "Non AO test",
        "grapari": "GraPARI Bandung",
    }
    result = render_case_text("non_ao", fields)
    assert "#Non AO" in result
    assert "Ticket Remedy : INC999" in result
    assert "Detail Case : Non AO test" in result
    assert "GraPARI : GraPARI Bandung" in result


def test_render_case_text_mobile():
    fields = {
        "ticket_remedy": "INC888",
        "msisdn": "6281234567890",
        "detail_case": "Mobile case",
    }
    result = render_case_text("mobile", fields)
    assert "#Mobile" in result
    assert "Ticket Remedy : INC888" in result
    assert "MSISDN : 6281234567890" in result
    assert "Detail Case : Mobile case" in result


def test_render_case_text_whitespace_only_skipped():
    fields = {
        "ticket_remedy": "  ",
        "detail_case": "valid",
    }
    result = render_case_text("non_order", fields)
    assert "Ticket Remedy" not in result
    assert "Detail Case : valid" in result


def test_render_case_text_legacy_type_fallback():
    """Old case types like 'stc' should fallback to 'non_order' fields."""
    fields = {"ticket_remedy": "INC123"}
    result = render_case_text("stc", fields)
    # Should use non_order fields since stc is not in CASE_FIELDS
    assert "Ticket Remedy : INC123" in result
    # Header uses the raw key since it's not in TYPE_LABELS
    assert "#STC" in result
