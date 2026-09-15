# Reply Solver dari Web Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agen membalas pesan solver dari web (teks + image/file) sebagai reply ke pesan solver di grup WA.

**Architecture:** Satu endpoint JSON baru `POST /api/cases/{id}/replies` di `main-v1-1.py`. Attachments dikirim inline base64 (stdlib, tanpa dep baru). File disimpan ke `MEDIA_DIR`, dikirim ke WAHA via `sendImage`/`sendFile` dengan `file.url` publik, pesan keluar dicatat ke `wa_messages` (`from_me=true`, `quoted_id`=pesan solver).

**Tech Stack:** FastAPI, httpx, psycopg, pytest, WAHA 2026.8.1 (engine WEBJS, tier CORE)

## Global Constraints

- WAHA version: 2026.8.1, engine WEBJS, tier CORE — `sendImage`/`sendFile` supported with `file.url` or `file.data` base64, `reply_to` available on all message types.
- Attachment transport: inline base64 in JSON body via stdlib `base64` — no new dependency (`python-multipart` NOT added).
- File serving: reuse `GET /api/media/file/{filename}` (public, no auth) as WAHA `file.url`; `BACKEND_PUBLIC_URL` must be publicly reachable.
- Max attachment size: 5 MB per file (decoded), max 3 files per reply — enforced in code with `413`.
- Allowed types: `image/jpeg`, `image/png`, `image/webp`, `video/mp4`, `application/pdf` — others → `422`.
- `reply_to_wa_message_id` must be a `wa_messages` row with `case_id` = target case — else `422`.
- Outgoing messages stored to `wa_messages` with `from_me=true`, `quoted_id=<reply_to>` so reply-chain traversal keeps working.
- Error mapping: WAHA failure → `502` (same as `waha_send`).

---

### Task 1: POST /api/cases/{id}/replies (teks + base64 attachments, reply ke pesan solver)

**Files:**
- Modify: `main-v1-1.py` (helper `waha_send_media` + model `ReplyAttachment`/`ReplyIn` + endpoint `POST /api/cases/{id}/replies`)
- Test: `tests/test_api.py` (new class `TestCaseReplies`)

**Interfaces:**
- Consumes: `waha_send` error-handling pattern (`main-v1-1.py:311-333`), `store_message` (`main-v1-1.py:494-503`), `MEDIA_DIR` + `BACKEND_PUBLIC_URL` (`main-v1-1.py:66-67`), fixture `client` + `mock_waha` + `_make_mock_db` (`tests/test_api.py:20-69`)
- Produces: `POST /api/cases/{id}/replies` → `{ok, wa_message_ids[]}`; helper `waha_send_media(endpoint, payload)` used by endpoint

- [ ] **Step 1: Write the failing test**

```python
class TestCaseReplies:
    def test_reply_text_only(self, mock_waha):
        tc, cur = self._reply_client([{"id": 6, "case_code": "INC000023470570", "status": "open", "group_id": 1, "mentions": [], "wa_message_id": "root1", "deleted_at": None},
                                      {"id": 1, "name": "Grup A", "chat_id": "120363xxx@g.us"},
                                      {"wa_message_id": "solver1", "case_id": 6},
                                      None, None])
        r = tc.post("/api/cases/6/replies", json={"message": "siap, kami cek dulu", "reply_to_wa_message_id": "solver1"})
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert len(r.json()["wa_message_ids"]) == 1

    def _reply_client(self, seq):
        from unittest.mock import patch, AsyncMock
        mock_conn, mock_cursor = _make_mock_db(fetchone_sequence=seq)
        with patch.object(main_module, "db", return_value=mock_conn), \
             patch.object(main_module, "resolve_contact_name", new_callable=AsyncMock, return_value=None):
            return TestClient(main_module.app), mock_cursor
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_api.py::TestCaseReplies::test_reply_text_only -v`
Expected: FAIL with 404 (route not defined)

- [ ] **Step 3: Write minimal implementation**

```python
class ReplyAttachment(BaseModel):
    filename: str = Field(..., description="Nama file asli (ekstensi menentukan tipe)")
    mimetype: str = Field(..., description="MIME: image/jpeg, image/png, image/webp, video/mp4, application/pdf")
    data_base64: str = Field(..., description="Isi file base64 (maks 5 MB decoded per file)")

class ReplyIn(BaseModel):
    message: str | None = Field(None, description="Teks balasan (opsional bila ada attachment)")
    reply_to_wa_message_id: str = Field(..., description="wa_message_id pesan solver yang dibalas")
    attachments: list[ReplyAttachment] = Field([], description="Maks 3 file, 5 MB per file")
    mentions: list[Mention] = Field([], description="Mention tambahan")


REPLY_MIMES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
               "video/mp4": ".mp4", "application/pdf": ".pdf"}

async def waha_send_media(endpoint: str, payload: dict) -> str | None:
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(f"{WAHA_URL}/api/{endpoint}", headers=WAHA_HEADERS, json=payload)
            r.raise_for_status()
            mid = r.json().get("id")
            return mid.get("_serialized") if isinstance(mid, dict) else mid
    except httpx.HTTPStatusError as e:
        log.error("WAHA media send failed: %s", e)
        raise HTTPException(status_code=502, detail=f"WAHA error: {e.response.status_code}") from e
    except Exception as e:
        log.error("WAHA unreachable: %s", e)
        raise HTTPException(status_code=502, detail="WAHA service unavailable") from e


@app.post("/api/cases/{case_id}/replies", tags=["Cases"],
          summary="Balas pesan solver dari web",
          description="Kirim teks + image/file sebagai reply ke pesan solver di grup case.")
async def reply_to_solver(case_id: int, inp: ReplyIn, request: Request,
                         _auth: str = Depends(verify_api_key),
                         _rate: None = Depends(check_rate_limit)):
    if not inp.message and not inp.attachments:
        raise HTTPException(status_code=422, detail="message atau attachments wajib diisi")
    if len(inp.attachments) > 3:
        raise HTTPException(status_code=422, detail="Maksimal 3 file per balasan")
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM cases WHERE id = %s AND deleted_at IS NULL", (case_id,))
        case = cur.fetchone()
        if not case:
            raise HTTPException(status_code=404, detail="Case not found")
        cur.execute("SELECT wa_message_id FROM wa_messages WHERE wa_message_id = %s AND case_id = %s",
                    (inp.reply_to_wa_message_id, case_id))
        if not cur.fetchone():
            raise HTTPException(status_code=422, detail="reply_to_wa_message_id bukan pesan case ini")
    group = _get_group(case["group_id"]) if case.get("group_id") else None
    if not group:
        raise HTTPException(status_code=400, detail="Case tidak punya grup aktif")
    sent: list[str] = []
    if inp.message:
        mid = await waha_send(inp.message,
                              mentions=[m.number for m in inp.mentions] or None,
                              reply_to=inp.reply_to_wa_message_id,
                              chat_id=group["chat_id"])
        if mid:
            store_message(mid, inp.reply_to_wa_message_id, None, inp.message, from_me=True)
            with db() as conn, conn.cursor() as cur:
                cur.execute("UPDATE wa_messages SET case_id = %s WHERE wa_message_id = %s", (case_id, mid))
                conn.commit()
            sent.append(mid)
    for att in inp.attachments:
        ext = REPLY_MIMES.get((att.mimetype or "").lower())
        if not ext:
            raise HTTPException(status_code=422, detail=f"mimetype {att.mimetype} tidak didukung")
        try:
            raw = base64.b64decode(att.data_base64, validate=True)
        except Exception:
            raise HTTPException(status_code=422, detail=f"data_base64 {att.filename} bukan base64 valid")
        if len(raw) > 5 * 1024 * 1024:
            raise HTTPException(status_code=413, detail=f"{att.filename} melebihi 5 MB")
        # ponytail: simpan apa adanya tanpa konversi; JPEG disarankan tapi tidak dipaksa
        fname = f"{uuid.uuid4().hex}{ext}"
        with open(os.path.join(MEDIA_DIR, fname), "wb") as f:
            f.write(raw)
        public_url = f"{BACKEND_PUBLIC_URL}/api/media/file/{fname}"
        endpoint = "sendImage" if att.mimetype.lower().startswith("image/") else "sendFile"
        payload = {"session": WAHA_SESSION, "chatId": group["chat_id"],
                   "file": {"mimetype": att.mimetype, "filename": att.filename, "url": public_url},
                   "reply_to": inp.reply_to_wa_message_id}
        if endpoint == "sendImage":
            payload["caption"] = inp.message or ""
        else:
            payload["caption"] = att.filename
        mid = await waha_send_media(endpoint, payload)
        if mid:
            store_message(mid, inp.reply_to_wa_message_id, None, inp.message or att.filename,
                          from_me=True, media_url=public_url, media_type=att.mimetype)
            with db() as conn, conn.cursor() as cur:
                cur.execute("UPDATE wa_messages SET case_id = %s WHERE wa_message_id = %s", (case_id, mid))
                conn.commit()
            sent.append(mid)
    return {"ok": True, "wa_message_ids": sent}
```

Note: tambah `import base64` di atas `main-v1-1.py` bila belum ada.

- [ ] **Step 4: Tambah test tepi (dalam class yang sama)**

```python
    def test_reply_wrong_case_message_rejected(self, mock_waha):
        tc, _ = self._reply_client([{"id": 6, "case_code": "INC1", "status": "open", "group_id": 1, "mentions": [], "wa_message_id": "root1", "deleted_at": None},
                                    {"id": 1, "name": "Grup A", "chat_id": "120363xxx@g.us"},
                                    None])
        r = tc.post("/api/cases/6/replies", json={"message": "halo", "reply_to_wa_message_id": "milik-case-lain"})
        assert r.status_code == 422

    def test_reply_with_image_attachment(self, mock_waha):
        import base64 as _b64
        tc, _ = self._reply_client([{"id": 6, "case_code": "INC1", "status": "open", "group_id": 1, "mentions": [], "wa_message_id": "root1", "deleted_at": None},
                                    {"id": 1, "name": "Grup A", "chat_id": "120363xxx@g.us"},
                                    {"wa_message_id": "solver1", "case_id": 6},
                                    None, None, None, None])
        r = tc.post("/api/cases/6/replies", json={"reply_to_wa_message_id": "solver1",
            "attachments": [{"filename": "bukti.jpg", "mimetype": "image/jpeg",
                             "data_base64": _b64.b64encode(b"fakejpeg").decode()}]})
        assert r.status_code == 200
        assert len(r.json()["wa_message_ids"]) == 1

    def test_reply_empty_rejected(self, mock_waha):
        tc, _ = self._reply_client([])
        r = tc.post("/api/cases/6/replies", json={"reply_to_wa_message_id": "solver1"})
        assert r.status_code == 422
```

- [ ] **Step 5: Run full suite**

Run: `python -m pytest tests/ -q`
Expected: all PASS (tidak ada regresi)

- [ ] **Step 6: Commit**

```bash
git add main-v1-1.py tests/test_api.py
git commit -m "feat: reply solver dari web + attachments (teks/image/file)"
```

### Task 2: Kontrak FE + batas nginx

**Files:**
- Modify: `API-contract-frontend.md` (seksi baru `POST /api/cases/{id}/replies`)
- Modify: `nginx.conf` (tambah `client_max_body_size 25m;` di server 443)

**Interfaces:**
- Consumes: Task 1 endpoint request/response
- Produces: docs + infra limit

- [ ] **Step 1: Tambah seksi kontrak (append sebelum `## 8. Format Error`)**

```markdown
### 3.6a `POST /api/cases/{id}/replies` — Balas pesan solver dari web

**Headers:** `X-API-Key: <key>`, `Content-Type: application/json`

**Request:**
```json
{
  "message": "siap, kami cek dulu ya",
  "reply_to_wa_message_id": "false_..._BBB",
  "attachments": [
    {"filename": "bukti.jpg", "mimetype": "image/jpeg", "data_base64": "<base64>"}
  ],
  "mentions": [{"number": "6281113021236", "name": "Mas Habib"}]
}
```

- `reply_to_wa_message_id` wajib — ambil dari `messages[].wa_message_id` di timeline `GET /api/cases/{id}` (bukan root). Milik case lain → `422`.
- `attachments` maks 3 file, 5 MB per file (decoded). MIME: `image/jpeg`, `image/png`, `image/webp`, `video/mp4`, `application/pdf`.
- Image terkirim via WAHA `sendImage` (caption = message), lainnya via `sendFile`.

**Response `200`:** `{ "ok": true, "wa_message_ids": ["..."] }`

**Error:** `404` case tidak ada · `422` reply_to bukan pesan case ini / MIME tak didukung / base64 invalid / kosong · `413` file > 5 MB · `502` WAHA gagal.

**Alur FE:** tombol **Balas** di tiap bubble solver → form (textarea + picker ≤3 file) → encode base64 → POST → refresh timeline.
```

- [ ] **Step 2: Tambah limit body nginx**

Di `nginx.conf`, dalam `server { listen 443 ... }`, tambah satu baris:
```
    client_max_body_size 25m;
```
(25m ≈ 3 file × 5 MB base64 +33% overhead + margin; tanpa ini default 1m menolak reply ber-attachment.)

- [ ] **Step 3: Commit**

```bash
git add API-contract-frontend.md nginx.conf
git commit -m "docs: kontrak POST /api/cases/{id}/replies + nginx client_max_body_size"
```
