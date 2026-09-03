"""Template rendering for Moban FU Case Tracker v1.2.

Each jenis_case has a specific layout of fields that get rendered
into a WhatsApp-friendly text format.
"""

# New case types (v1.2)
CASE_TYPES = ["non_order", "non_ao", "mobile"]

# Required fields per jenis_case (validated in backend)
REQUIRED_FIELDS: dict[str, set[str]] = {
    "non_order": {"ticket_remedy", "no_indihome"},
    "non_ao": {"ticket_remedy", "order_id", "no_indihome"},
    "mobile": {"ticket_remedy", "msisdn"},
}

# Field labels per jenis_case — order matters for rendering
CASE_FIELDS: dict[str, list[tuple[str, str]]] = {
    "non_order": [
        ("ticket_remedy", "Ticket Remedy"),
        ("no_indihome", "Nomer Indihome"),
        ("request_case", "Request Case"),
        ("detail_case", "Detail Case"),
        ("link_evidence", "Link Evidence"),
    ],
    "non_ao": [
        ("ticket_remedy", "Ticket Remedy"),
        ("order_id", "Order ID"),
        ("no_indihome", "Nomer Indihome"),
        ("last_milestone", "Last Milestone"),
        ("request_case", "Request Case"),
        ("detail_case", "Detail Case"),
        ("link_evidence", "Link Evidence"),
    ],
    "mobile": [
        ("ticket_remedy", "Ticket Remedy"),
        ("msisdn", "MSISDN"),
        ("request_case", "Request Case"),
        ("detail_case", "Detail Case"),
        ("link_evidence", "Link Evidence"),
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
Nomer Indihome : 0211234567
Request Case : <request case>
Detail Case : <detail case>
Link Evidence :
<link1>
<link2>""",
    "non_ao": """punten rekan @<nomor> mohon bantuannya untuk case Non AO ada 1 case lagi

Area : <nama area>
Regional : <nama regional>
Sumber Ticket : <STC/Grapari/Web IT>
Asal Grapari : <nama GraPARI> (jika sumber Grapari)
Jenis Case : Non AO

Ticket Remedy : INC000000000000
Order ID : <order id>
Nomer Indihome : 0211234567
Last Milestone : <last milestone>
Request Case : <request case>
Detail Case : <detail case>
Link Evidence :
<link1>
<link2>""",
    "mobile": """punten rekan @<nomor> mohon bantuannya untuk case Mobile ada 1 case lagi

Area : <nama area>
Regional : <nama regional>
Sumber Ticket : <STC/Grapari/Web IT>
Asal Grapari : <nama GraPARI> (jika sumber Grapari)
Jenis Case : Mobile

Ticket Remedy : INC000000000000
MSISDN : 08xxxxxxxxxx
Request Case : <request case>
Detail Case : <detail case>
Link Evidence :
<link1>
<link2>""",
}


def render_header(mentions: list[dict], case_type: str,
                  custom_header: str | None = None) -> str:
    """Render the greeting + mention line at the top of the message.

    WhatsApp mentions require @<phone_number> in text for the mention to work.
    WAHA/WhatsApp automatically renders the display name from the contact.

    If custom_header is provided, use it with @<phone> mentions inserted.
    Otherwise use default: punten rekan @<phone> mohon bantuannya untuk case <TYPE> ada 1 case lagi
    """
    # Collect phone numbers from mentions
    phones = [m.get("number", "") for m in mentions if m.get("number")]

    if custom_header:
        # Replace {phone} placeholder with @<phone> mentions
        # If no {phone} placeholder, append @mentions at the end
        if "{phone}" in custom_header:
            phone_mentions = " ".join(f"@{p}" for p in phones)
            return custom_header.replace("{phone}", phone_mentions)
        elif phones:
            phone_mentions = " ".join(f"@{p}" for p in phones)
            return f"{custom_header} {phone_mentions}"
        return custom_header

    # Default header
    label = TYPE_LABELS.get(case_type, case_type.upper())
    parts = ["punten rekan"]
    for p in phones:
        parts.append(f"@{p}")
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
        if val is not None:
            if key == "link_evidence" and isinstance(val, list):
                # Render array of evidence links, one per line
                links = [str(v).strip() for v in val if v and str(v).strip()]
                if links:
                    lines.append(f"Link Evidence :")
                    for link in links:
                        lines.append(link)
            else:
                s = str(val).strip()
                if s:
                    lines.append(f"{fld_label} : {s}")

    return "\n".join(lines)


def render_reminder_text(custom_message: str | None = None) -> str:
    """Render pesan reminder (nudge untuk case yang belum di-handle)."""
    if custom_message:
        return custom_message
    return "mohon di-follow up ya, case ini belum ada respon 🙏"
