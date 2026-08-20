"""Moban FU Case Tracker v1.1 — single group, reply-chain traversal.

Perubahan dari v1.0: tidak ada lagi multi-group/case_hops. Semua pesan grup
direkam ke wa_messages beserta quoted_id (parent), lalu pencocokan pesan
masuk dilakukan via waterfall: regex INC -> reply langsung -> chain traversal
-> LLM fallback.
"""

import asyncio
import json
import logging
import os
import re
import time
import uuid

import httpx
import psycopg
import psycopg_pool
from fastapi import FastAPI, Request, Response, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict

from templates import CASE_TYPES, render_case_text, render_header

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

app = FastAPI(title="Moban FU Tracker", docs_url="/docs", redoc_url="/redoc")

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

def store_message(wa_mid, quoted_id, author, body, from_me=False):
    if not wa_mid:
        return
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO wa_messages (wa_message_id, quoted_id, author, body, from_me)
               VALUES (%s,%s,%s,%s,%s) ON CONFLICT (wa_message_id) DO NOTHING""",
            (wa_mid, quoted_id, author, body, from_me),
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
            cur_db.execute("SELECT * FROM cases WHERE wa_message_id = %s", (cur,))
            case = cur_db.fetchone()
            if case:
                return case, ("reply" if depth == 0 else "chain")
            cur_db.execute(
                "SELECT quoted_id, case_id FROM wa_messages WHERE wa_message_id = %s", (cur,)
            )
            row = cur_db.fetchone()
            if not row:
                return None, ""
            if row["case_id"]:
                cur_db.execute("SELECT * FROM cases WHERE id = %s", (row["case_id"],))
                case = cur_db.fetchone()
                if case:
                    return case, ("reply" if depth == 0 else "chain")
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


# ---------------------------------------------------------------- LLM fallback

def _strip_markdown_json(raw: str) -> str:
    """Strip markdown code fences from LLM JSON response."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*\n?", "", raw)
        raw = re.sub(r"\n?```\s*$", "", raw)
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
                          "response_format": {"type": "json_object"}},
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
            log.warning("LLM JSON parse failed (attempt %d/%d): %s", attempt + 1, LLM_MAX_RETRIES, e)
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
    if p.get("fromMe"):
        return False
    if WA_GROUP_ID and p.get("from") != WA_GROUP_ID:
        return False
    body = (p.get("body") or "").strip()
    if not body:
        return False

    wa_mid = norm_id(p.get("id"))
    quoted = extract_quoted_id(p)
    author = p.get("participant") or p.get("author") or p.get("from")

    store_message(wa_mid, quoted, author, body)

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
    # 4) LLM fallback (case tidak ketemu, atau ketemu tapi status tidak terbaca)
    if case is None or not parsed.get("status"):
        ai = await parse_llm(body, open_case_codes())
        if ai and (ai.get("confidence") or 0) >= 0.7:
            if case is None and ai.get("case_code"):
                case = find_case_by_code(ai["case_code"])
            if case:
                parsed["status"] = parsed.get("status") or ai.get("status")
                parsed["note"] = parsed.get("note") or ai.get("note")
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


# ---------------------------------------------------------------- API

class Mention(BaseModel):
    number: str
    name: str | None = None

class CaseIn(BaseModel):
    case_type: str = "other"
    fields: dict = {}
    mentions: list[Mention] = []

class StatusIn(BaseModel):
    status: str
    note: str | None = None


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


@app.post("/api/cases", status_code=201)
async def create_case(inp: CaseIn, request: Request, _auth: str = Depends(verify_api_key), _rate: None = Depends(check_rate_limit)):
    if inp.case_type not in CASE_TYPES:
        inp.case_type = "other"
    f = inp.fields
    case_code = (f.get("ticket_remedy") or f.get("case_id") or "").strip().upper() or None

    header = render_header([m.model_dump() for m in inp.mentions], inp.case_type) if inp.mentions else ""
    body_text = render_case_text(inp.case_type, f)
    text = f"{header}\n\n{body_text}" if header else body_text

    wa_mid = await waha_send(text, mentions=[m.number for m in inp.mentions] or None)

    with db() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO cases (case_code, case_type, title, fields, message_text, wa_message_id)
               VALUES (%s,%s,%s,%s,%s,%s)
               ON CONFLICT (case_code) DO UPDATE
                 SET wa_message_id = EXCLUDED.wa_message_id,
                     message_text  = EXCLUDED.message_text,
                     status        = 'open',
                     updated_at    = now()
               RETURNING id, case_code""",
            (case_code, inp.case_type, f.get("detail_case", "")[:120],
             json.dumps(f), text, wa_mid),
        )
        row = cur.fetchone()
        cur.execute(
            """INSERT INTO wa_messages (wa_message_id, case_id, body, from_me)
               VALUES (%s,%s,%s,true) ON CONFLICT (wa_message_id) DO NOTHING""",
            (wa_mid, row["id"], text),
        )
        conn.commit()
    return {"id": row["id"], "case_code": row["case_code"], "wa_message_id": wa_mid, "text": text}


@app.get("/api/cases")
def list_cases(request: Request, status: str | None = None, case_type: str | None = None, q: str | None = None, _auth: str = Depends(verify_api_key), _rate: None = Depends(check_rate_limit)):
    sql = "SELECT id, case_code, case_type, title, status, ack, created_at, updated_at FROM cases WHERE true"
    args = []
    if status:
        sql += " AND status = %s"; args.append(status)
    if case_type:
        sql += " AND case_type = %s"; args.append(case_type)
    if q:
        sql += " AND (case_code ILIKE %s OR title ILIKE %s)"; args += [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY updated_at DESC"
    with db() as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchall()


@app.get("/api/cases/{case_id}")
def case_detail(case_id: int, request: Request, _auth: str = Depends(verify_api_key), _rate: None = Depends(check_rate_limit)):
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM cases WHERE id = %s", (case_id,))
        case = cur.fetchone()
        if not case:
            raise HTTPException(status_code=404, detail="Case not found")
        cur.execute(
            """SELECT wa_message_id, quoted_id, author, body, from_me, created_at
               FROM wa_messages WHERE case_id = %s ORDER BY created_at""",
            (case_id,),
        )
        messages = cur.fetchall()
        cur.execute(
            "SELECT * FROM progress_updates WHERE case_id = %s ORDER BY created_at", (case_id,)
        )
        updates = cur.fetchall()
    participants = sorted({m["author"] for m in messages if m["author"] and not m["from_me"]})
    return {"case": case, "messages": messages, "updates": updates, "participants": participants}


@app.post("/api/cases/{case_id}/status")
def set_status(case_id: int, inp: StatusIn, request: Request, _auth: str = Depends(verify_api_key), _rate: None = Depends(check_rate_limit)):
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


@app.post("/webhooks/waha")
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


@app.post("/api/crawl")
async def crawl_group(limit: int = 200, request: Request = None, _auth: str = Depends(verify_api_key), _rate: None = Depends(check_rate_limit)):
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
            store_message(norm_id(m.get("id")), extract_quoted_id(m),
                          m.get("participant") or m.get("author") or m.get("from"),
                          (m.get("body") or "").strip(),
                          from_me=m.get("fromMe", False))
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


@app.get("/health")
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
