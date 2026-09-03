"""Tests for templates.py — v1.2 (Non Order, Non AO, Mobile)"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from templates import CASE_TYPES, render_case_text, render_header, CASE_FIELDS, TYPE_LABELS, REQUIRED_FIELDS


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
    """Non Order: ticket_remedy, no_indihome, request_case, detail_case, link_evidence"""
    fields = {
        "ticket_remedy": "INC123456789",
        "no_indihome": "142401135588",
        "request_case": "Perlu pengecekan langsung",
        "detail_case": "Test case detail",
    }
    result = render_case_text("non_order", fields)
    assert "#Non Order" in result
    assert "Ticket Remedy : INC123456789" in result
    assert "Nomer Indihome : 142401135588" in result
    assert "Request Case : Perlu pengecekan langsung" in result
    assert "Detail Case : Test case detail" in result
    # Fields not in non_order should not appear
    assert "Order ID" not in result
    assert "MSISDN" not in result


def test_render_case_text_non_order_with_evidence():
    """Non Order with link_evidence array."""
    fields = {
        "ticket_remedy": "INC123",
        "link_evidence": ["https://imgur.com/a", "https://imgur.com/b"],
    }
    result = render_case_text("non_order", fields)
    assert "Ticket Remedy : INC123" in result
    assert "Link Evidence :" in result
    assert "https://imgur.com/a" in result
    assert "https://imgur.com/b" in result


def test_render_case_text_non_order_evidence_empty_list():
    """Empty link_evidence array should not render Link Evidence."""
    fields = {
        "ticket_remedy": "INC123",
        "link_evidence": [],
    }
    result = render_case_text("non_order", fields)
    assert "Ticket Remedy : INC123" in result
    assert "Link Evidence" not in result


def test_render_case_text_non_order_evidence_single():
    """Single item link_evidence array."""
    fields = {
        "ticket_remedy": "INC123",
        "link_evidence": ["https://imgur.com/single"],
    }
    result = render_case_text("non_order", fields)
    assert "Link Evidence :" in result
    assert "https://imgur.com/single" in result


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
    """Non AO: ticket_remedy, order_id, no_indihome, last_milestone, request_case, detail_case, link_evidence"""
    fields = {
        "ticket_remedy": "INC999",
        "order_id": "ORD-001",
        "no_indihome": "0219876543",
        "last_milestone": "Installed",
        "request_case": "Perlu follow up",
        "detail_case": "Non AO test",
    }
    result = render_case_text("non_ao", fields)
    assert "#Non AO" in result
    assert "Ticket Remedy : INC999" in result
    assert "Order ID : ORD-001" in result
    assert "Nomer Indihome : 0219876543" in result
    assert "Last Milestone : Installed" in result
    assert "Request Case : Perlu follow up" in result
    assert "Detail Case : Non AO test" in result
    # Fields not in non_ao should not appear
    assert "MSISDN" not in result


def test_render_case_text_non_ao_with_evidence():
    """Non AO with link_evidence array."""
    fields = {
        "ticket_remedy": "INC999",
        "link_evidence": ["https://drive.google.com/a", "https://drive.google.com/b"],
    }
    result = render_case_text("non_ao", fields)
    assert "Link Evidence :" in result
    assert "https://drive.google.com/a" in result
    assert "https://drive.google.com/b" in result


def test_render_case_text_mobile():
    """Mobile: ticket_remedy, msisdn, request_case, detail_case, link_evidence"""
    fields = {
        "ticket_remedy": "INC888",
        "msisdn": "6281234567890",
        "request_case": "Cek coverage area",
        "detail_case": "Mobile case",
    }
    result = render_case_text("mobile", fields)
    assert "#Mobile" in result
    assert "Ticket Remedy : INC888" in result
    assert "MSISDN : 6281234567890" in result
    assert "Request Case : Cek coverage area" in result
    assert "Detail Case : Mobile case" in result
    # Fields not in mobile should not appear
    assert "Nomer Indihome" not in result
    assert "Order ID" not in result


def test_render_case_text_mobile_with_evidence():
    """Mobile with link_evidence array."""
    fields = {
        "ticket_remedy": "INC888",
        "msisdn": "6281234567890",
        "link_evidence": ["https://imgur.com/c"],
    }
    result = render_case_text("mobile", fields)
    assert "Link Evidence :" in result
    assert "https://imgur.com/c" in result


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


def test_render_case_text_full_non_order():
    """Full Non Order with all fields filled."""
    fields = {
        "ticket_remedy": "INC111",
        "no_indihome": "0211111111",
        "request_case": "Setup new connection",
        "detail_case": "Customer requests new INDIHOME installation at...",
        "link_evidence": ["https://imgur.com/1", "https://imgur.com/2", "https://imgur.com/3"],
    }
    result = render_case_text(
        "non_order", fields,
        area_name="Area 2",
        regional_name="Jabo",
        sumber_ticket="STC",
    )
    assert "#Non Order" in result
    assert "Area : Area 2" in result
    assert "Regional : Jabo" in result
    assert "Sumber Ticket : STC" in result
    assert "Jenis Case : Non Order" in result
    assert "Ticket Remedy : INC111" in result
    assert "Nomer Indihome : 0211111111" in result
    assert "Request Case : Setup new connection" in result
    assert "Detail Case : Customer requests new INDIHOME" in result
    assert "Link Evidence :" in result
    assert "https://imgur.com/1" in result
    assert "https://imgur.com/2" in result
    assert "https://imgur.com/3" in result


def test_render_case_text_evidence_with_empty_strings():
    """link_evidence with some empty strings should filter them out."""
    fields = {
        "ticket_remedy": "INC123",
        "link_evidence": ["https://valid.com", "", "  ", "https://also-valid.com"],
    }
    result = render_case_text("non_order", fields)
    assert "https://valid.com" in result
    assert "https://also-valid.com" in result
    # Empty strings should not appear as blank lines


def test_case_fields_non_order_count():
    """Non Order should have exactly 5 fields."""
    assert len(CASE_FIELDS["non_order"]) == 5


def test_case_fields_non_ao_count():
    """Non AO should have exactly 7 fields."""
    assert len(CASE_FIELDS["non_ao"]) == 7


def test_case_fields_mobile_count():
    """Mobile should have exactly 5 fields."""
    assert len(CASE_FIELDS["mobile"]) == 5


def test_case_fields_field_keys_non_order():
    """Non Order field keys should match expected list."""
    keys = [k for k, _ in CASE_FIELDS["non_order"]]
    assert keys == ["ticket_remedy", "no_indihome", "request_case", "detail_case", "link_evidence"]


def test_case_fields_field_keys_non_ao():
    """Non AO field keys should match expected list."""
    keys = [k for k, _ in CASE_FIELDS["non_ao"]]
    assert keys == ["ticket_remedy", "order_id", "no_indihome", "last_milestone", "request_case", "detail_case", "link_evidence"]


def test_case_fields_field_keys_mobile():
    """Mobile field keys should match expected list."""
    keys = [k for k, _ in CASE_FIELDS["mobile"]]
    assert keys == ["ticket_remedy", "msisdn", "request_case", "detail_case", "link_evidence"]


def test_required_fields_non_order():
    """Non Order required: ticket_remedy, no_indihome."""
    assert REQUIRED_FIELDS["non_order"] == {"ticket_remedy", "no_indihome"}


def test_required_fields_non_ao():
    """Non AO required: ticket_remedy, order_id, no_indihome."""
    assert REQUIRED_FIELDS["non_ao"] == {"ticket_remedy", "order_id", "no_indihome"}


def test_required_fields_mobile():
    """Mobile required: ticket_remedy, msisdn."""
    assert REQUIRED_FIELDS["mobile"] == {"ticket_remedy", "msisdn"}


def test_required_fields_subset_of_case_fields():
    """All required fields must exist in CASE_FIELDS."""
    for case_type, req in REQUIRED_FIELDS.items():
        field_keys = {k for k, _ in CASE_FIELDS[case_type]}
        for rf in req:
            assert rf in field_keys, f"Required field '{rf}' not in CASE_FIELDS for {case_type}"


# ---------------------------------------------------------------- Reminder text tests

from templates import render_reminder_text


def test_render_reminder_text_default():
    """Default reminder message should contain follow-up text."""
    result = render_reminder_text(None)
    assert "follow up" in result
    assert "🙏" in result


def test_render_reminder_text_custom():
    """Custom message should be returned as-is."""
    custom = "tolong segera di-follow up ya!"
    result = render_reminder_text(custom)
    assert result == custom
