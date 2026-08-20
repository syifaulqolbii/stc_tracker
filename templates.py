"""Template rendering for Moban FU Case Tracker.

Each case_type has a specific layout of fields that get rendered
into a WhatsApp-friendly text format.
"""

CASE_TYPES = ["stc", "smooa", "mobile", "ufo", "other"]

# Field labels per case_type — order matters for rendering
CASE_FIELDS: dict[str, list[tuple[str, str]]] = {
    "stc": [
        ("ticket_remedy", "Ticket Remedy"),
        ("no_indihome", "No Indihome"),
        ("order_id", "Order ID"),
        ("last_milestone", "Last Milestone"),
        ("milestone_info", "Milestone Info"),
        ("detail_case", "Detail Case"),
        ("evidence", "Evidence"),
    ],
    "smooa": [
        ("grapari", "GraPARI"),
        ("ticket_remedy", "Ticket Remedy"),
        ("no_indihome", "No IH"),
        ("nama_pelanggan", "Nama Pelanggan"),
        ("cp", "CP"),
        ("email", "Email"),
        ("tgl_kejadian", "Tgl Kejadian"),
        ("detail_case", "Detail Case"),
        ("evidence", "Capture Lightspeed"),
        ("smooa_parent", "No SMOOA Parent"),
        ("smooa_child", "No SMOOA Child"),
    ],
    "mobile": [
        ("grapari", "GraPARI"),
        ("ticket_remedy", "Ticket Remedy"),
        ("tier", "Tier"),
        ("msisdn", "MSISDN"),
        ("tgl_kejadian", "Tgl Kejadian"),
        ("lokasi", "Lokasi"),
        ("detail_case", "Detail Kejadian"),
        ("evidence", "Evidence"),
    ],
    "ufo": [
        ("grapari", "GraPARI"),
        ("case_id", "Case ID"),
        ("no_indihome", "Nomor Indihome"),
        ("email", "Email"),
        ("cp", "CP Pelanggan"),
        ("tgl_kejadian", "Tgl Kejadian"),
        ("order_id", "Order ID"),
        ("status_case", "Status"),
        ("detail_case", "Detail Keperluan"),
        ("evidence", "Evidence"),
    ],
    "other": [
        ("raw_text", "Pesan Lengkap"),
    ],
}

TYPE_LABELS = {
    "stc": "STC",
    "smooa": "SMOOA",
    "mobile": "Case Mobile",
    "ufo": "UFO",
    "other": "Lainnya",
}


def render_header(mentions: list[dict], case_type: str) -> str:
    """Render the greeting + mention line at the top of the message.

    Format: punten rekan @<name> mohon bantuannya untuk case <TYPE> ada 1 case lagi
    """
    label = TYPE_LABELS.get(case_type, case_type.upper())
    parts = ["punten rekan"]
    for m in mentions:
        name = m.get("name") or m.get("number", "")
        parts.append(f"@{name}")
    parts.append(f"mohon bantuannya untuk case {label} ada 1 case lagi")
    return " ".join(parts)


def render_case_text(case_type: str, fields: dict) -> str:
    """Render case fields into a formatted WhatsApp message body."""
    lines = [f"#{TYPE_LABELS.get(case_type, case_type.upper())}"]
    field_defs = CASE_FIELDS.get(case_type, CASE_FIELDS["other"])

    for key, label in field_defs:
        val = fields.get(key)
        if val is not None and str(val).strip():
            lines.append(f"{label} : {str(val).strip()}")

    return "\n".join(lines)
