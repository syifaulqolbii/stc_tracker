"""Moban FU Case Tracker v1.2 — single group, reply-chain traversal.

Perubahan v1.2: tambah Area, Regional, Sumber Ticket, Jenis Case (tabel lookup).
Field lama tetap ada, semua opsional. Area → Regional hierarchy.
Sumber Ticket: STC / Grapari / Web IT. Jenis Case: Non Order / Non AO / Mobile.
Asal Grapari: text input (tidak disimpan di tabel terpisah).
"""

import asyncio
import json
import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager

import httpx
import psycopg
import psycopg_pool
from fastapi import FastAPI, Query, Request, Response, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field

from templates import (
    CASE_TYPES, LEGACY_CASE_TYPE_MAP,
    render_case_text, render_header,
)

# Structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("moban-tracker")

WAHA_URL = os.getenv("WAHA_URL", "http://localhost:3000")
WAHA_KEY = os.getenv("WAHA_API_KEY", "")
WAHA_SESSION = os.getenv("WAHA_SESSION", "default")
WA_GROUP_ID = os.getenv("WA_GROUP_ID", "")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/moban")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "WAHA")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://9router.tefambo.site/v1")

WAHA_HEADERS = {"X-Api-Key": WAHA_KEY}
BACKEND_API_KEY = os.getenv("BACKEND_API_KEY", "")
MAX_CHAIN_DEPTH = 5
LLM_MAX_RETRIES = 3
LLM_RETRY_DELAY = 1.0  # seconds base delay

# Rate limiting (simple in-memory)
RATE_LIMIT_RPM = int(os.getenv("RATE_LIMIT_RPM", "60"))  # requests per minute
_rate_buckets: dict[str, list[float]] = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    await _fetch_contacts()
    yield

app = FastAPI(
    title="Moban FU Tracker",
    description=(
        "Backend API untuk Moban FU Case Tracker v1.2. "
        "Mengelola case follow-up di grup WhatsApp dengan reply-chain traversal, "
        "Area/Regional hierarchy, dan Sumber Ticket/Jenis Case dari tabel lookup."
    ),
    version="1.2.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
    openapi_tags=[
        {"name": "Cases", "description": "CRUD dan manajemen case"},
        {"name": "Lookup", "description": "Data referensi: Area, Regional, Sumber Ticket, Jenis Case"},
        {"name": "Solver Contacts", "description": "CRUD kontak solver (whitelist mention)"},
        {"name": "Webhooks", "description": "Webhook receiver dari WAHA (WhatsApp HTTP API)"},
        {"name": "System", "description": "Health check dan operasi sistem"},
    ],
)

# CORS — allow frontend origins
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Security headers middleware
@app.middleware("http")
async def security_headers(request: Request, call_next):
    response: Response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


# Request ID + timing middleware
@app.middleware("http")
async def request_middleware(request: Request, call_next):
    request_id = str(uuid.uuid4())[:8]
    start = time.time()
    response: Response = await call_next(request)
    duration = time.time() - start
    response.headers["X-Request-ID"] = request_id
    log.info("%s %s %d %.3fs [%s]", request.method, request.url.path, response.status_code, duration, request_id)
    return response


# ---------------------------------------------------------------- Rate limiting
async def check_rate_limit(request: Request):
    """Simple sliding window rate limiter per client IP."""
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
    window = 60.0  # 1 minute
    # Get or create bucket for this IP
    bucket = _rate_buckets.setdefault(client_ip, [])
    # Remove old entries
    bucket[:] = [t for t in bucket if now - t < window]
    if len(bucket) >= RATE_LIMIT_RPM:
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again later.")
    bucket.append(now)


# ---------------------------------------------------------------- Auth
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(key: str | None = Depends(api_key_header)):
    """Validate X-API-Key header. Skip if BACKEND_API_KEY is not configured."""
    if not BACKEND_API_KEY:
        return  # no auth configured — allow all
    if not key or key != BACKEND_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


_pool: psycopg_pool.ConnectionPool | None = None


def _get_pool() -> psycopg_pool.ConnectionPool:
    global _pool
    if _pool is None:
        _pool = psycopg_pool.ConnectionPool(
            DATABASE_URL,
            min_size=2,
            max_size=10,
            kwargs={"row_factory": dict_row},
        )
    return _pool


def db():
    return _get_pool().connection()


# ---------------------------------------------------------------- WAHA client

async def waha_send(text: str, mentions: list[str] | None = None,
                    reply_to: str | None = None) -> str | None:
    payload = {"session": WAHA_SESSION, "chatId": WA_GROUP_ID, "text": text}
    if mentions:
        payload["mentions"] = mentions
    if reply_to:
        payload["reply_to"] = reply_to
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{WAHA_URL}/api/sendText", headers=WAHA_HEADERS, json=payload)
            r.raise_for_status()
            mid = r.json().get("id")
            return mid.get("_serialized") if isinstance(mid, dict) else mid
    except httpx.HTTPStatusError as e:
        log.error("WAHA send failed: %s", e)
        raise HTTPException(status_code=502, detail=f"WAHA error: {e.response.status_code}") from e
    except Exception as e:
        log.error("WAHA unreachable: %s", e)
        raise HTTPException(status_code=502, detail="WAHA service unavailable") from e


# ---------------------------------------------------------------- helpers

def norm_id(v) -> str | None:
    return v.get("_serialized") if isinstance(v, dict) else v


def extract_quoted_id(p: dict) -> str | None:
    rt = p.get("replyTo")
    if isinstance(rt, dict) and rt.get("id"):
        return norm_id(rt["id"])
    d = p.get("_data") or {}
    if d.get("quotedMsgId"):
        return d["quotedMsgId"]
    q = d.get("quotedMsg") or {}
    return norm_id(q.get("id"))


# ---------------------------------------------------------------- contact resolution

# Contact name cache (author_id -> display name)
_contact_cache: dict[str, str] = {}


async def _fetch_contacts():
    """Fetch all contacts + LID mappings from WAHA and build author_id→name cache.

    Populates cache with:
    - {number}@c.us → name (from contacts/all)
    - {lid}@lid → name (from lids mapping + contacts lookup)
    """
    global _contact_cache
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            # Step 1: Fetch all contacts
            r = await c.get(
                f"{WAHA_URL}/api/contacts/all",
                params={"session": WAHA_SESSION, "limit": 500},
                headers=WAHA_HEADERS,
            )
            r.raise_for_status()
            phone_to_name: dict[str, str] = {}
            for contact in r.json():
                cid = contact.get("id", "")
                name = contact.get("pushname") or contact.get("name") or contact.get("shortName")
                if name and cid:
                    _contact_cache[cid] = name
                    # Also cache by raw phone number
                    number = cid.split("@")[0] if "@" in cid else cid
                    _contact_cache[number] = name
                    phone_to_name[number] = name

            # Step 2: Fetch LID mappings to map @lid → phone → name
            try:
                r2 = await c.get(
                    f"{WAHA_URL}/api/{WAHA_SESSION}/lids",
                    params={"limit": 500},
                    headers=WAHA_HEADERS,
                )
                if r2.status_code == 200:
                    for mapping in r2.json():
                        lid = mapping.get("lid", "")
                        pn = mapping.get("pn", "")
                        if lid and pn:
                            pn_number = pn.split("@")[0] if "@" in pn else pn
                            if pn_number in phone_to_name:
                                _contact_cache[lid] = phone_to_name[pn_number]
                                log.debug("Cached LID %s → %s", lid, phone_to_name[pn_number])
            except Exception as e:
                log.debug("Failed to fetch LID mappings: %s", e)

            log.info("Loaded %d contacts into cache", len(_contact_cache))
    except Exception as e:
        log.warning("Failed to fetch contacts from WAHA: %s", e)


async def resolve_contact_name(author: str | None) -> str | None:
    """Resolve author (lid/phone) to contact name.

    Resolution flow:
    1. Check cache (keyed by original author ID)
    2. If @lid → resolve to phone via GET /api/{session}/lids/{lid}
    3. Lookup contact by phone or original ID via GET /api/contacts
    4. Cache both the original ID and the resolved phone → name
    """
    if not author:
        return None
    # Check cache first
    if author in _contact_cache:
        return _contact_cache[author]

    phone_number = None
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            # Step 1: If @lid, resolve to phone number via LID API
            if author.endswith("@lid"):
                lid_number = author.split("@")[0]
                r = await c.get(
                    f"{WAHA_URL}/api/{WAHA_SESSION}/lids/{lid_number}",
                    headers=WAHA_HEADERS,
                )
                if r.status_code == 200:
                    lid_data = r.json()
                    pn = lid_data.get("pn")
                    if pn:
                        # pn comes as "123456789@c.us" — extract the number
                        phone_number = pn.split("@")[0] if "@" in pn else pn
                        # Also cache the @c.us form
                        _contact_cache[pn] = None  # placeholder, resolved below

            # Step 2: Lookup contact by phone number (if resolved) or original ID
            lookup_id = phone_number if phone_number else author
            r = await c.get(
                f"{WAHA_URL}/api/contacts",
                params={"contactId": lookup_id, "session": WAHA_SESSION},
                headers=WAHA_HEADERS,
            )
            r.raise_for_status()
            data = r.json()
            name = data.get("pushname") or data.get("name") or data.get("shortName")
            if name:
                _contact_cache[author] = name
                # Also cache by phone number if we resolved from lid
                if phone_number:
                    _contact_cache[f"{phone_number}@c.us"] = name
                    _contact_cache[phone_number] = name
            return name
    except Exception as e:
        log.debug("Contact resolve failed for %s: %s", author, e)
        return None


INC_RE = re.compile(r"\bINC\d{9,}\b", re.I)
PCT_RE = re.compile(r"(\d{1,3})\s*%")
DONE_KW = ["done", "selesai", "beres", "kelar", "solved", "closed", "terkirim", "lurus"]
ISSUE_KW = ["kendala", "gagal", "error", "reject", "stuck", "belum bisa"]
PROG_KW = ["proses", "progress", "diproses", "otw", "dicek", "cek dulu", "follow up", "fu"]


def parse_rule(text: str) -> dict:
    out = {}
    m = INC_RE.search(text)
    if m:
        out["case_code"] = m.group(0).upper()
    m = PCT_RE.search(text)
    if m:
        out["progress"] = min(100, int(m.group(1)))
    low = text.lower()
    if any(k in low for k in DONE_KW):
        out["status"] = "done"
    elif any(k in low for k in ISSUE_KW):
        out["status"] = "issue"
    elif any(k in low for k in PROG_KW):
        out["status"] = "in_progress"
    return out


# ---------------------------------------------------------------- DB

def store_message(wa_mid, quoted_id, author, body, from_me=False, media_url=None, media_type=None):
    if not wa_mid:
        return
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO wa_messages (wa_message_id, quoted_id, author, body, from_me, media_url, media_type)
               VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (wa_message_id) DO NOTHING""",
            (wa_mid, quoted_id, author, body, from_me, media_url, media_type),
        )
        conn.commit()


def find_case_by_code(code: str):
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM cases WHERE case_code = %s", (code,))
        return cur.fetchone()


def find_case_by_chain(quoted_id: str | None) -> tuple[dict | None, str]:
    """Telusuri rantai parent ke atas sampai ketemu root case atau pesan yang
    sudah punya case_id. Return (case, source): 'reply' jika langsung, 'chain'
    jika lewat perantara."""
    cur, depth = quoted_id, 0
    with db() as conn, conn.cursor() as cur_db:
        while cur and depth < MAX_CHAIN_DEPTH:
            # WAHA may send short/partial IDs, use LIKE for matching
            cur_db.execute("SELECT * FROM cases WHERE wa_message_id = %s OR wa_message_id LIKE %s", (cur, f"%{cur}%"))
            case = cur_db.fetchone()
            if case:
                # Direct match to root message = reply; anything deeper = chain
                return case, ("reply" if depth == 0 else "chain")
            cur_db.execute(
                "SELECT quoted_id, case_id FROM wa_messages WHERE wa_message_id = %s OR wa_message_id LIKE %s", (cur, f"%{cur}%")
            )
            row = cur_db.fetchone()
            if not row:
                return None, ""
            if row["case_id"]:
                cur_db.execute("SELECT * FROM cases WHERE id = %s", (row["case_id"],))
                case = cur_db.fetchone()
                if case:
                    # Found via wa_messages.case_id = not a direct reply to root
                    return case, "chain"
            cur, depth = row["quoted_id"], depth + 1
    return None, ""


def open_case_codes() -> list[str]:
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT case_code FROM cases WHERE status != 'done' AND case_code IS NOT NULL")
        return [r["case_code"] for r in cur.fetchall()]


def link_and_update(case_id, wa_mid, author, body, status, note, source, confidence):
    with db() as conn, conn.cursor() as cur:
        cur.execute("UPDATE wa_messages SET case_id = %s WHERE wa_message_id = %s",
                    (case_id, wa_mid))
        cur.execute(
            """INSERT INTO progress_updates
               (case_id, wa_message_id, author, body, parsed_status, parsed_note, source, confidence)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (wa_message_id) DO NOTHING""",
            (case_id, wa_mid, author, body, status, note, source, confidence),
        )
        if status:
            cur.execute("UPDATE cases SET status = %s, updated_at = now() WHERE id = %s",
                        (status, case_id))
        conn.commit()


# ---------------------------------------------------------------- Lookup helpers

def _resolve_sumber_ticket(name: str | None) -> int | None:
    """Resolve sumber ticket name to ID. Returns None if not found."""
    if not name:
        return None
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM sumber_tickets WHERE name = %s", (name.strip(),))
        row = cur.fetchone()
        return row["id"] if row else None


def _resolve_jenis_case(name: str | None) -> int | None:
    """Resolve jenis case name to ID. Returns None if not found."""
    if not name:
        return None
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM jenis_cases WHERE name = %s", (name.strip(),))
        row = cur.fetchone()
        return row["id"] if row else None


def _resolve_jenis_case_name(name: str | None) -> str:
    """Normalize jenis case name. Maps legacy names to new ones."""
    if not name:
        return "non_order"
    name = name.strip().lower().replace(" ", "_")
    if name in CASE_TYPES:
        return name
    # Try legacy mapping
    return LEGACY_CASE_TYPE_MAP.get(name, "non_order")


# ---------------------------------------------------------------- LLM fallback

def _strip_markdown_json(raw: str) -> str:
    """Strip markdown code fences and extract JSON from LLM response.
    
    Handles: raw JSON, ```json fences, JSON embedded in prose, etc.
    """
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*\n?", "", raw)
        raw = re.sub(r"\n?```\s*$", "", raw)
    # If still not starting with {, try to find JSON substring
    if not raw.startswith("{"):
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end > start:
            raw = raw[start:end + 1]
    return raw.strip()


def _validate_llm_response(data: dict) -> dict | None:
    """Validate and normalize LLM response structure."""
    if not isinstance(data, dict):
        return None
    # Normalize: ensure case_code is str or None
    case_code = data.get("case_code")
    if case_code and not isinstance(case_code, str):
        return None
    # Normalize: ensure status is valid enum
    status = data.get("status")
    valid_statuses = {"done", "in_progress", "issue", None}
    if status and status not in valid_statuses:
        status = None
    # Normalize: confidence must be float 0-1
    confidence = data.get("confidence")
    if confidence is not None:
        try:
            confidence = max(0.0, min(1.0, float(confidence)))
        except (TypeError, ValueError):
            confidence = None
    return {
        "case_code": case_code,
        "status": status,
        "note": data.get("note") if isinstance(data.get("note"), str) else None,
        "confidence": confidence,
    }


async def parse_llm(text: str, open_codes: list[str]) -> dict | None:
    """Parse message using LLM with retry and validation.
    
    Returns normalized dict with keys: case_code, status, note, confidence.
    Returns None on failure after all retries.
    """
    if not OPENROUTER_API_KEY or not open_codes:
        return None
    prompt = (
        "Kamu parser update progres tiket di grup WhatsApp teknisi Telkom. "
        f"Tiket yang sedang terbuka: {', '.join(open_codes)}. "
        'Balas HANYA JSON: {"case_code": "INC..."|null, "status": "done|in_progress|issue"|null, '
        '"note": "ringkasan singkat"|null, "confidence": 0.0-1.0}. '
        "Kalau pesan bukan update tiket, balas {\"case_code\": null}. "
        f"Pesan: {text}"
    )
    last_error = None
    for attempt in range(LLM_MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.post(
                    f"{LLM_BASE_URL}/chat/completions",
                    headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
                    json={"model": OPENROUTER_MODEL,
                          "messages": [{"role": "user", "content": prompt}],
                          "response_format": {"type": "json_object"},
                          "stream": False},
                )
                r.raise_for_status()
                resp_json = r.json()
                raw = resp_json.get("choices", [{}])[0].get("message", {}).get("content", "")
                if not raw or not raw.strip():
                    log.warning("LLM returned empty content (attempt %d/%d)", attempt + 1, LLM_MAX_RETRIES)
                    return None  # empty response = no match, don't retry
                cleaned = _strip_markdown_json(raw)
                data = json.loads(cleaned)
                validated = _validate_llm_response(data)
                if validated:
                    return validated
                log.warning("LLM response invalid: %s", data)
                return None  # invalid structure, don't retry
        except json.JSONDecodeError as e:
            last_error = e
            log.warning("LLM JSON parse failed (attempt %d/%d): %s | raw: %s", attempt + 1, LLM_MAX_RETRIES, e, repr(raw[:200]))
            # If response has no JSON at all, don't retry
            if not raw or "{" not in raw:
                log.warning("LLM response has no JSON, skipping")
                return None
        except httpx.HTTPStatusError as e:
            last_error = e
            # Don't retry on 4xx errors (client errors)
            if 400 <= e.response.status_code < 500:
                log.warning("LLM HTTP %d (no retry): %s", e.response.status_code, e)
                return None
            log.warning("LLM HTTP error (attempt %d/%d): %s", attempt + 1, LLM_MAX_RETRIES, e)
        except Exception as e:
            last_error = e
            log.warning("LLM error (attempt %d/%d): %s", attempt + 1, LLM_MAX_RETRIES, e)
        # Exponential backoff before retry
        if attempt < LLM_MAX_RETRIES - 1:
            delay = LLM_RETRY_DELAY * (2 ** attempt)
            await asyncio.sleep(delay)
    log.warning("LLM parse failed after %d retries: %s", LLM_MAX_RETRIES, last_error)
    return None


# ---------------------------------------------------------------- handler utama

async def handle_message(p: dict, crawl: bool = False) -> bool:
    """Proses satu pesan grup. Return True jika berhasil dikaitkan ke case."""
    log.info("WEBHOOK payload keys: from=%s fromMe=%s participant=%s body=%s quoted=%s",
             p.get("from"), p.get("fromMe"), p.get("participant"),
             (p.get("body") or "")[:60], extract_quoted_id(p))
    if p.get("fromMe"):
        log.debug("SKIP: fromMe=true")
        return False
    if WA_GROUP_ID and p.get("from") != WA_GROUP_ID:
        log.warning("SKIP: from=%s != WA_GROUP_ID=%s", p.get("from"), WA_GROUP_ID)
        return False
    body = (p.get("body") or "").strip()
    has_media = p.get("hasMedia", False)
    media = p.get("media") or {}
    media_url = media.get("url")
    media_type = media.get("mimetype")

    # Skip if no body AND no media
    if not body and not has_media:
        return False

    wa_mid = norm_id(p.get("id"))
    quoted = extract_quoted_id(p)
    author = p.get("participant") or p.get("author") or p.get("from")

    store_message(wa_mid, quoted, author, body, media_url=media_url, media_type=media_type)

    # Resolve contact name in background (non-blocking)
    author_name = await resolve_contact_name(author)
    if author_name:
        try:
            with db() as conn, conn.cursor() as cur:
                cur.execute("UPDATE wa_messages SET author_name = %s WHERE wa_message_id = %s", (author_name, wa_mid))
                conn.commit()
        except Exception:
            pass  # non-critical

    case, source, conf = None, None, None
    parsed = parse_rule(body)

    # 1) regex INC
    if parsed.get("case_code"):
        case, source = find_case_by_code(parsed["case_code"]), "rule"
    # 2) reply langsung / 3) chain traversal
    if case is None and quoted:
        case, chain_src = find_case_by_chain(quoted)
        if case:
            source = chain_src
    # 4) LLM fallback — only when case NOT found via regex/chain
    # Skip LLM when case is already found: chain/reply already links the message,
    # no need for LLM to guess status (saves 9-10s per message)
    if case is None:
        ai = await parse_llm(body, open_case_codes())
        if ai and (ai.get("confidence") or 0) >= 0.7:
            if ai.get("case_code"):
                case = find_case_by_code(ai["case_code"])
            if case:
                parsed["status"] = ai.get("status")
                parsed["note"] = ai.get("note")
                source, conf = "llm", ai.get("confidence")

    if case is None:
        return False

    if crawl and source in ("reply", "chain", "rule"):
        source = "crawl"
    link_and_update(case["id"], wa_mid, author, body,
                    parsed.get("status"), parsed.get("note") or body[:200], source, conf)
    log.info("UPDATE %s <- %s (%s): %s", case["case_code"], author, source, parsed.get("status"))
    return True


def handle_ack(p: dict):
    mid, ack = norm_id(p.get("id")), p.get("ackName")
    if not mid or not ack:
        return
    with db() as conn, conn.cursor() as cur:
        cur.execute("UPDATE cases SET ack = %s WHERE wa_message_id = %s", (ack, mid))
        conn.commit()


# ---------------------------------------------------------------- API Models

class Mention(BaseModel):
    number: str = Field(..., description="Nomor WA format internasional tanpa + (contoh: 6281234567890)")
    name: str | None = Field(None, description="Nama kontak (opsional, hanya untuk tampilan)")

class CaseIn(BaseModel):
    area_id: int | None = Field(None, description="ID Area. Lihat GET /api/areas")
    regional_id: int | None = Field(None, description="ID Regional (tergantung Area). Lihat GET /api/areas/{area_id}/regionals")
    sumber_ticket: str | None = Field(None, description="Sumber Ticket: STC, Grapari, atau Web IT. Lihat GET /api/sumber-tickets")
    jenis_case: str | None = Field(None, description="Jenis Case: Non Order, Non AO, atau Mobile. Lihat GET /api/jenis-cases")
    asal_grapari: str | None = Field(None, description="Asal GraPARI (hanya jika Sumber Ticket = Grapari). Free text.")
    mentions: list[Mention] = Field([], description="Daftar kontak solver yang akan di-mention di grup WA")
    fields: dict = Field({}, description="Field case lama (semua opsional): ticket_remedy, no_indihome, detail_case, evidence, dll")

class StatusIn(BaseModel):
    status: str = Field(..., description="Status baru: open, in_progress, done, issue")
    note: str | None = Field(None, description="Catatan opsional untuk update status")


class SolverContactIn(BaseModel):
    name: str = Field(..., description="Nama kontak solver")
    phone_number: str = Field(..., description="Nomor WA format internasional tanpa + (contoh: 6281234567890)")
    role: str | None = Field(None, description="Posisi/role solver (contoh: Solusi 1, Solver IT, Supervisor)")


class SolverContactUpdate(BaseModel):
    name: str | None = Field(None, description="Nama kontak solver")
    phone_number: str | None = Field(None, description="Nomor WA format internasional tanpa +")
    role: str | None = Field(None, description="Posisi/role solver")
    is_active: bool | None = Field(None, description="Status aktif (false = soft delete)")


class WahaId(BaseModel):
    _serialized: str | None = None


class WahaReplyTo(BaseModel):
    id: WahaId | str | dict | None = None


class WahaPayload(BaseModel):
    id: WahaId | str | dict | None = None
    from_: str | None = None
    body: str | None = None
    participant: str | None = None
    author: str | None = None
    replyTo: WahaReplyTo | None = None
    fromMe: bool | None = None
    _data: dict | None = None

    model_config = ConfigDict(extra="allow")


class WahaWebhook(BaseModel):
    event: str
    payload: WahaPayload | dict | None = None


# ---------------------------------------------------------------- Cases API

@app.post("/api/cases", status_code=201, tags=["Cases"],
          summary="Buat & kirim case ke grup WA",
          description="Buat case baru dengan field Area, Regional, Sumber Ticket, Jenis Case. Field lama juga opsional.")
async def create_case(inp: CaseIn, request: Request,
                      _auth: str = Depends(verify_api_key),
                      _rate: None = Depends(check_rate_limit)):
    # Resolve jenis_case name → internal key
    jenis_key = _resolve_jenis_case_name(inp.jenis_case)
    jenis_case_id = _resolve_jenis_case(inp.jenis_case)
    sumber_ticket_id = _resolve_sumber_ticket(inp.sumber_ticket)

    f = inp.fields
    case_code = (f.get("ticket_remedy") or f.get("case_id") or "").strip().upper() or None

    # Resolve names for template rendering
    area_name = None
    regional_name = None
    with db() as conn, conn.cursor() as cur:
        if inp.area_id:
            cur.execute("SELECT name FROM areas WHERE id = %s", (inp.area_id,))
            row = cur.fetchone()
            if row:
                area_name = row["name"]
        if inp.regional_id:
            cur.execute("SELECT name FROM regionals WHERE id = %s", (inp.regional_id,))
            row = cur.fetchone()
            if row:
                regional_name = row["name"]

    header = render_header([m.model_dump() for m in inp.mentions], jenis_key) if inp.mentions else ""
    body_text = render_case_text(
        jenis_key, f,
        area_name=area_name,
        regional_name=regional_name,
        sumber_ticket=inp.sumber_ticket,
        asal_grapari=inp.asal_grapari,
    )
    text = f"{header}\n\n{body_text}" if header else body_text

    wa_mid = await waha_send(text, mentions=[m.number for m in inp.mentions] or None)

    with db() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO cases (case_code, case_type, title, fields, message_text, wa_message_id,
                                  area_id, regional_id, sumber_ticket_id, jenis_case_id, asal_grapari)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (case_code) DO UPDATE
                 SET wa_message_id = EXCLUDED.wa_message_id,
                     message_text  = EXCLUDED.message_text,
                     area_id       = EXCLUDED.area_id,
                     regional_id   = EXCLUDED.regional_id,
                     sumber_ticket_id = EXCLUDED.sumber_ticket_id,
                     jenis_case_id = EXCLUDED.jenis_case_id,
                     asal_grapari  = EXCLUDED.asal_grapari,
                     status        = 'open',
                     updated_at    = now()
               RETURNING id, case_code""",
            (case_code, jenis_key, f.get("detail_case", "")[:120],
             json.dumps(f), text, wa_mid,
             inp.area_id, inp.regional_id, sumber_ticket_id,
             jenis_case_id, inp.asal_grapari),
        )
        row = cur.fetchone()
        cur.execute(
            """INSERT INTO wa_messages (wa_message_id, case_id, body, from_me)
               VALUES (%s,%s,%s,true) ON CONFLICT (wa_message_id) DO NOTHING""",
            (wa_mid, row["id"], text),
        )
        conn.commit()
    return {"id": row["id"], "case_code": row["case_code"], "wa_message_id": wa_mid, "text": text}


@app.get("/api/cases", tags=["Cases"],
         summary="Daftar case (dashboard list)",
         description="List semua case dengan filter opsional. Diurutkan updated_at DESC.")
def list_cases(
    request: Request,
    status: str | None = Query(None, description="Filter status: open, in_progress, done, issue"),
    case_type: str | None = Query(None, description="Filter jenis case: Non Order, Non AO, Mobile"),
    area_id: int | None = Query(None, description="Filter berdasarkan Area ID"),
    regional_id: int | None = Query(None, description="Filter berdasarkan Regional ID"),
    sumber_ticket: str | None = Query(None, description="Filter sumber ticket: STC, Grapari, Web IT"),
    q: str | None = Query(None, description="Pencarian substring di case_code dan title"),
    _auth: str = Depends(verify_api_key),
    _rate: None = Depends(check_rate_limit),
):
    sql = """SELECT c.id, c.case_code, c.case_type, c.title, c.status, c.ack,
                    c.created_at, c.updated_at,
                    c.area_id, c.regional_id, c.sumber_ticket_id, c.jenis_case_id, c.asal_grapari,
                    a.name AS area_name, r.name AS regional_name,
                    st.name AS sumber_ticket_name, jc.name AS jenis_case_name
             FROM cases c
             LEFT JOIN areas a ON c.area_id = a.id
             LEFT JOIN regionals r ON c.regional_id = r.id
             LEFT JOIN sumber_tickets st ON c.sumber_ticket_id = st.id
             LEFT JOIN jenis_cases jc ON c.jenis_case_id = jc.id
             WHERE true"""
    args: list = []
    if status:
        sql += " AND c.status = %s"
        args.append(status)
    if case_type:
        sql += " AND jc.name = %s"
        args.append(case_type)
    if area_id:
        sql += " AND c.area_id = %s"
        args.append(area_id)
    if regional_id:
        sql += " AND c.regional_id = %s"
        args.append(regional_id)
    if sumber_ticket:
        sql += " AND st.name = %s"
        args.append(sumber_ticket)
    if q:
        sql += " AND (c.case_code ILIKE %s OR c.title ILIKE %s)"
        args += [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY c.updated_at DESC"
    with db() as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchall()


@app.get("/api/cases/{case_id}", tags=["Cases"],
         summary="Detail case + timeline rantai",
         description="Return detail case, semua pesan (timeline), progress updates, dan daftar peserta.")
def case_detail(case_id: int, request: Request,
                _auth: str = Depends(verify_api_key),
                _rate: None = Depends(check_rate_limit)):
    with db() as conn, conn.cursor() as cur:
        cur.execute("""SELECT c.*, a.name AS area_name, r.name AS regional_name,
                              st.name AS sumber_ticket_name, jc.name AS jenis_case_name
                       FROM cases c
                       LEFT JOIN areas a ON c.area_id = a.id
                       LEFT JOIN regionals r ON c.regional_id = r.id
                       LEFT JOIN sumber_tickets st ON c.sumber_ticket_id = st.id
                       LEFT JOIN jenis_cases jc ON c.jenis_case_id = jc.id
                       WHERE c.id = %s""", (case_id,))
        case = cur.fetchone()
        if not case:
            raise HTTPException(status_code=404, detail="Case not found")
        cur.execute(
            """SELECT wa_message_id, quoted_id, author, author_name, body, from_me,
                      media_url, media_type, created_at
               FROM wa_messages WHERE case_id = %s ORDER BY created_at""",
            (case_id,),
        )
        messages = cur.fetchall()
        cur.execute(
            "SELECT * FROM progress_updates WHERE case_id = %s ORDER BY created_at", (case_id,)
        )
        updates = cur.fetchall()
    # Build unique participants list with names
    seen = set()
    participants = []
    for m in messages:
        if m["author"] and not m["from_me"] and m["author"] not in seen:
            seen.add(m["author"])
            participants.append({"author": m["author"], "name": m.get("author_name")})
    return {"case": case, "messages": messages, "updates": updates, "participants": participants}


@app.post("/api/cases/{case_id}/status", tags=["Cases"],
          summary="Koreksi status case manual",
          description="Update status case secara manual. Berguna untuk tombol 'Tandai selesai' / 'Buka ulang' di UI.")
def set_status(case_id: int, inp: StatusIn, request: Request,
               _auth: str = Depends(verify_api_key),
               _rate: None = Depends(check_rate_limit)):
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM cases WHERE id = %s", (case_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Case not found")
        cur.execute(
            """INSERT INTO wa_messages (wa_message_id, case_id, author, body, from_me)
               VALUES (%s,%s,'manual',%s,true) ON CONFLICT DO NOTHING""",
            (f"manual-{case_id}-{inp.status}-{int(__import__('time').time())}", case_id, inp.note),
        )
        cur.execute("UPDATE cases SET status = %s, updated_at = now() WHERE id = %s",
                    (inp.status, case_id))
        conn.commit()
    return {"ok": True}


# ---------------------------------------------------------------- Lookup API

@app.get("/api/areas", tags=["Lookup"],
         summary="Daftar semua Area",
         description="Return list semua Area yang tersedia di database.")
def list_areas(
    request: Request,
    _auth: str = Depends(verify_api_key),
    _rate: None = Depends(check_rate_limit),
):
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, name FROM areas ORDER BY name")
        return cur.fetchall()


@app.get("/api/areas/{area_id}/regionals", tags=["Lookup"],
         summary="Daftar Regional berdasarkan Area",
         description="Return list Regional yang ada di bawah Area tertentu.")
def list_regionals(
    area_id: int,
    request: Request,
    _auth: str = Depends(verify_api_key),
    _rate: None = Depends(check_rate_limit),
):
    with db() as conn, conn.cursor() as cur:
        # Verify area exists
        cur.execute("SELECT id, name FROM areas WHERE id = %s", (area_id,))
        area = cur.fetchone()
        if not area:
            raise HTTPException(status_code=404, detail="Area not found")
        cur.execute("SELECT id, name FROM regionals WHERE area_id = %s ORDER BY name", (area_id,))
        regionals = cur.fetchall()
    return {"area": area, "regionals": regionals}


@app.get("/api/sumber-tickets", tags=["Lookup"],
         summary="Daftar Sumber Ticket",
         description="Return list sumber ticket: STC, Grapari, Web IT.")
def list_sumber_tickets(
    request: Request,
    _auth: str = Depends(verify_api_key),
    _rate: None = Depends(check_rate_limit),
):
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, name FROM sumber_tickets ORDER BY name")
        return cur.fetchall()


@app.get("/api/jenis-cases", tags=["Lookup"],
         summary="Daftar Jenis Case",
         description="Return list jenis case: Non Order, Non AO, Mobile.")
def list_jenis_cases(
    request: Request,
    _auth: str = Depends(verify_api_key),
    _rate: None = Depends(check_rate_limit),
):
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, name FROM jenis_cases ORDER BY name")
        return cur.fetchall()


# ---------------------------------------------------------------- Webhooks

@app.post("/webhooks/waha", tags=["Webhooks"],
          summary="Webhook receiver dari WAHA",
          description="Menerima event dari WAHA: message, message.ack, dll.")
async def waha_webhook(req: Request):
    try:
        data = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    event = data.get("event")
    if not event:
        raise HTTPException(status_code=400, detail="Missing 'event' field")
    payload = data.get("payload") or {}
    if event == "message":
        await handle_message(payload)
    elif event == "message.ack":
        handle_ack(payload)
    return {"ok": True}


# ---------------------------------------------------------------- Solver Contacts CRUD

@app.get("/api/solver-contacts", tags=["Solver Contacts"],
         summary="Daftar semua kontak solver",
         description="Return list kontak solver. Query: is_active=true untuk hanya yang aktif.")
def list_solver_contacts(
    request: Request,
    is_active: bool | None = Query(None, description="Filter status aktif. Kosongkan untuk semua."),
    q: str | None = Query(None, description="Pencarian substring di nama atau role"),
    _auth: str = Depends(verify_api_key),
    _rate: None = Depends(check_rate_limit),
):
    sql = "SELECT id, name, phone_number, role, is_active, created_at, updated_at FROM solver_contacts WHERE true"
    args: list = []
    if is_active is not None:
        sql += " AND is_active = %s"
        args.append(is_active)
    if q:
        sql += " AND (name ILIKE %s OR role ILIKE %s)"
        args += [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY name"
    with db() as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchall()


@app.post("/api/solver-contacts", status_code=201, tags=["Solver Contacts"],
           summary="Tambah kontak solver baru",
           description="Tambah kontak solver ke whitelist. Nomor WA harus unik di antara kontak aktif.")
def create_solver_contact(
    inp: SolverContactIn,
    request: Request,
    _auth: str = Depends(verify_api_key),
    _rate: None = Depends(check_rate_limit),
):
    phone = inp.phone_number.strip()
    if phone.startswith("+"):
        phone = phone[1:]
    with db() as conn, conn.cursor() as cur:
        # Check duplicate phone among active contacts
        cur.execute("SELECT id FROM solver_contacts WHERE phone_number = %s AND is_active = true", (phone,))
        if cur.fetchone():
            raise HTTPException(status_code=409, detail=f"Nomor {phone} sudah terdaftar")
        cur.execute(
            """INSERT INTO solver_contacts (name, phone_number, role)
               VALUES (%s, %s, %s)
               RETURNING id, name, phone_number, role, is_active, created_at, updated_at""",
            (inp.name.strip(), phone, inp.role.strip() if inp.role else None),
        )
        row = cur.fetchone()
        conn.commit()
    return row


@app.get("/api/solver-contacts/{contact_id}", tags=["Solver Contacts"],
          summary="Detail kontak solver",
          description="Return detail satu kontak solver berdasarkan ID.")
def get_solver_contact(
    contact_id: int,
    request: Request,
    _auth: str = Depends(verify_api_key),
    _rate: None = Depends(check_rate_limit),
):
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM solver_contacts WHERE id = %s", (contact_id,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Kontak tidak ditemukan")
    return row


@app.put("/api/solver-contacts/{contact_id}", tags=["Solver Contacts"],
          summary="Update kontak solver",
          description="Update field kontak solver. Kirim hanya field yang ingin diubah.")
def update_solver_contact(
    contact_id: int,
    inp: SolverContactUpdate,
    request: Request,
    _auth: str = Depends(verify_api_key),
    _rate: None = Depends(check_rate_limit),
):
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM solver_contacts WHERE id = %s", (contact_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Kontak tidak ditemukan")
        # Build dynamic update
        updates, args = [], []
        if inp.name is not None:
            updates.append("name = %s")
            args.append(inp.name.strip())
        if inp.phone_number is not None:
            phone = inp.phone_number.strip()
            if phone.startswith("+"):
                phone = phone[1:]
            # Check duplicate
            cur.execute("SELECT id FROM solver_contacts WHERE phone_number = %s AND is_active = true AND id != %s",
                        (phone, contact_id))
            if cur.fetchone():
                raise HTTPException(status_code=409, detail=f"Nomor {phone} sudah terdaftar")
            updates.append("phone_number = %s")
            args.append(phone)
        if inp.role is not None:
            updates.append("role = %s")
            args.append(inp.role.strip() if inp.role else None)
        if inp.is_active is not None:
            updates.append("is_active = %s")
            args.append(inp.is_active)
        if not updates:
            raise HTTPException(status_code=422, detail="Tidak ada field yang diubah")
        updates.append("updated_at = now()")
        args.append(contact_id)
        cur.execute(f"UPDATE solver_contacts SET {', '.join(updates)} WHERE id = %s RETURNING *", args)
        row = cur.fetchone()
        conn.commit()
    return row


@app.delete("/api/solver-contacts/{contact_id}", tags=["Solver Contacts"],
             summary="Hapus kontak solver (soft delete)",
             description="Set is_active = false. Data tetap ada di DB untuk referensi case lama.")
def delete_solver_contact(
    contact_id: int,
    request: Request,
    _auth: str = Depends(verify_api_key),
    _rate: None = Depends(check_rate_limit),
):
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM solver_contacts WHERE id = %s AND is_active = true", (contact_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Kontak tidak ditemukan atau sudah dihapus")
        cur.execute("UPDATE solver_contacts SET is_active = false, updated_at = now() WHERE id = %s", (contact_id,))
        conn.commit()
    return {"ok": True}


# ---------------------------------------------------------------- Crawl & System

@app.post("/api/crawl", tags=["System"],
          summary="Backfill histori grup WA",
          description="Dua pass: simpan semua pesan dulu (rantai lengkap), baru proses dengan waterfall matching.")
async def crawl_group(
    limit: int = Query(200, description="Jumlah pesan histori yang diambil"),
    request: Request = None,
    _auth: str = Depends(verify_api_key),
    _rate: None = Depends(check_rate_limit),
):
    """Dua pass: simpan semua pesan dulu (rantai lengkap), baru proses.
    
    Pass 1: Store all messages (for complete chain traversal).
    Pass 2: Process each message with waterfall matching.
    Per-message errors are caught to avoid failing the entire crawl.
    """
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.get(
            f"{WAHA_URL}/api/{WAHA_SESSION}/chats/{WA_GROUP_ID}/messages",
            params={"limit": limit, "download": "true"},
            headers=WAHA_HEADERS,
        )
        r.raise_for_status()
        msgs = r.json()

    # Pass 1: Store all messages for complete chain
    stored = 0
    store_errors = 0
    for m in msgs:
        try:
            media = m.get("media") or {}
            store_message(norm_id(m.get("id")), extract_quoted_id(m),
                          m.get("participant") or m.get("author") or m.get("from"),
                          (m.get("body") or "").strip(),
                          from_me=m.get("fromMe", False),
                          media_url=media.get("url"),
                          media_type=media.get("mimetype"))
            stored += 1
        except Exception as e:
            store_errors += 1
            log.warning("Crawl store failed for message: %s", e)

    # Pass 2: Process each message with waterfall
    applied = 0
    process_errors = 0
    for m in msgs:
        try:
            if await handle_message(m, crawl=True):
                applied += 1
        except Exception as e:
            process_errors += 1
            log.warning("Crawl process failed for message: %s", e)

    return {
        "fetched": len(msgs),
        "stored": stored,
        "updates_applied": applied,
        "store_errors": store_errors,
        "process_errors": process_errors,
    }


@app.get("/health", tags=["System"],
         summary="Health check",
         description="Cek status koneksi database dan WAHA service. Tidak perlu auth.")
async def health():
    out = {"status": "ok", "db": "unknown", "waha": "unknown"}
    try:
        with db() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            out["db"] = "ok"
    except Exception as e:
        out["db"] = f"error: {e}"
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get(f"{WAHA_URL}/api/sessions", headers=WAHA_HEADERS)
            out["waha"] = "ok" if r.status_code == 200 else f"http {r.status_code}"
    except Exception as e:
        out["waha"] = f"error: {e}"
    return out
