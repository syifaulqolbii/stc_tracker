"""Template rendering for Moban FU Case Tracker v1.2.

Each jenis_case has a specific layout of fields that get rendered
into a WhatsApp-friendly text format.
"""

# New case types (v1.2)
CASE_TYPES = ["non_order", "non_ao", "mobile"]

# Field labels per jenis_case — order matters for rendering
# These are the "old" fields that are still kept but all optional
CASE_FIELDS: dict[str, list[tuple[str, str]]] = {
    "non_order": [
        ("ticket_remedy", "Ticket Remedy"),
        ("no_indihome", "No Indihome"),
        ("order_id", "Order ID"),
        ("last_milestone", "Last Milestone"),
        ("milestone_info", "Milestone Info"),
        ("detail_case", "Detail Case"),
        ("evidence", "Evidence"),
        ("grapari", "GraPARI"),
        ("case_id", "Case ID"),
        ("email", "Email"),
        ("cp", "CP"),
        ("tgl_kejadian", "Tgl Kejadian"),
        ("status_case", "Status"),
        ("raw_text", "Pesan Lengkap"),
    ],
    "non_ao": [
        ("ticket_remedy", "Ticket Remedy"),
        ("no_indihome", "No Indihome"),
        ("order_id", "Order ID"),
        ("detail_case", "Detail Case"),
        ("evidence", "Evidence"),
        ("grapari", "GraPARI"),
        ("case_id", "Case ID"),
        ("email", "Email"),
        ("cp", "CP"),
        ("tgl_kejadian", "Tgl Kejadian"),
        ("status_case", "Status"),
        ("raw_text", "Pesan Lengkap"),
    ],
    "mobile": [
        ("ticket_remedy", "Ticket Remedy"),
        ("no_indihome", "No Indihome"),
        ("msisdn", "MSISDN"),
        ("tier", "Tier"),
        ("lokasi", "Lokasi"),
        ("detail_case", "Detail Case"),
        ("evidence", "Evidence"),
        ("grapari", "GraPARI"),
        ("tgl_kejadian", "Tgl Kejadian"),
        ("raw_text", "Pesan Lengkap"),
    ],
}

# Legacy case types mapping (v1.1 → v1.2) for backward compat
LEGACY_CASE_TYPE_MAP = {
    "stc": "non_order",
    "smooa": "non_order",
    "ufo": "non_order",
    "other": "non_order",
    "mobile": "mobile",
}

TYPE_LABELS = {
    "non_order": "Non Order",
    "non_ao": "Non AO",
    "mobile": "Mobile",
}

# Placeholder wording per jenis_case (untuk textarea mode)
PLACEHOLDER_TEXT = {
    "non_order": """punten rekan @<nomor> mohon bantuannya untuk case Non Order ada 1 case lagi

Area : <nama area>
Regional : <nama regional>
Sumber Ticket : <STC/Grapari/Web IT>
Asal Grapari : <nama GraPARI> (jika sumber Grapari)
Jenis Case : Non Order

Ticket Remedy : INC000000000000
No Indihome : 000000000000
Detail Case : <detail case>
Evidence : <link>""",
    "non_ao": """punten rekan @<nomor> mohon bantuannya untuk case Non AO ada 1 case lagi

Area : <nama area>
Regional : <nama regional>
Sumber Ticket : <STC/Grapari/Web IT>
Asal Grapari : <nama GraPARI> (jika sumber Grapari)
Jenis Case : Non AO

Ticket Remedy : INC000000000000
No Indihome : 000000000000
Detail Case : <detail case>
Evidence : <link>""",
    "mobile": """punten rekan @<nomor> mohon bantuannya untuk case Mobile ada 1 case lagi

Area : <nama area>
Regional : <nama regional>
Sumber Ticket : <STC/Grapari/Web IT>
Asal Grapari : <nama GraPARI> (jika sumber Grapari)
Jenis Case : Mobile

Ticket Remedy : INC000000000000
MSISDN : 08xxxxxxxxxx
Detail Case : <detail case>
Evidence : <link>""",
}


def render_header(mentions: list[dict], case_type: str) -> str:
    """Render the greeting + mention line at the top of the message.

    WhatsApp mentions require @<phone_number> in text for the mention to work.
    WAHA/WhatsApp automatically renders the display name from the contact.

    Format: punten rekan @<phone> mohon bantuannya untuk case <TYPE> ada 1 case lagi
    """
    label = TYPE_LABELS.get(case_type, case_type.upper())
    parts = ["punten rekan"]
    for m in mentions:
        number = m.get("number", "")
        if number:
            parts.append(f"@{number}")
    parts.append(f"mohon bantuannya untuk case {label} ada 1 case lagi")
    return " ".join(parts)


def render_case_text(case_type: str, fields: dict,
                     area_name: str | None = None,
                     regional_name: str | None = None,
                     sumber_ticket: str | None = None,
                     asal_grapari: str | None = None) -> str:
    """Render case fields into a formatted WhatsApp message body.

    New v1.2 fields (area, regional, sumber_ticket, asal_grapari) are rendered
    at the top. Old fields follow after.
    """
    label = TYPE_LABELS.get(case_type, case_type.upper())
    lines = [f"#{label}"]

    # Render new v1.2 fields first
    if area_name:
        lines.append(f"Area : {area_name}")
    if regional_name:
        lines.append(f"Regional : {regional_name}")
    if sumber_ticket:
        lines.append(f"Sumber Ticket : {sumber_ticket}")
    if asal_grapari:
        lines.append(f"Asal Grapari : {asal_grapari}")
    if label:
        lines.append(f"Jenis Case : {label}")

    # Render old fields (all optional)
    field_defs = CASE_FIELDS.get(case_type, CASE_FIELDS["non_order"])

    for key, fld_label in field_defs:
        val = fields.get(key)
        if val is not None and str(val).strip():
            lines.append(f"{fld_label} : {str(val).strip()}")

    return "\n".join(lines)
