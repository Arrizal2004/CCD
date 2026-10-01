"""
Infrastructure Request Pipeline — VPS / VPN provisioning requests for students.

Status flow:  PENDING  →  ON_PROGRESS  →  DONE | DECLINE

Endpoints:
  POST   /                    — student creates a new request
  GET    /                    — list requests (student: own; admin+: all)
  GET    /{id}                — get single request
  PATCH  /{id}/status         — admin updates status + credentials / VM link
  POST   /{id}/document       — student uploads supporting document (PDF/JPG/PNG)
  GET    /{id}/document       — authenticated document download
  POST   /{id}/config         — admin uploads VPN config file
  GET    /{id}/config         — authenticated config file download
  GET    /{id}/messages       — list chat messages
  WS     /{id}/ws             — realtime chat (send + broadcast)
"""
import io
import json
import logging
import mimetypes
import unicodedata
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

from auth import get_current_user, require_sysadmin, decode_token, Role
from database import get_pool

log = logging.getLogger(__name__)

router = APIRouter()


# ── WebSocket connection manager (one room per request_id) ────────────────────
class _ConnManager:
    def __init__(self):
        self.rooms: dict[str, set] = {}

    def register(self, req_id: str, ws: WebSocket):
        self.rooms.setdefault(req_id, set()).add(ws)

    def disconnect(self, req_id: str, ws: WebSocket):
        room = self.rooms.get(req_id)
        if room:
            room.discard(ws)
            if not room:
                self.rooms.pop(req_id, None)

    async def broadcast(self, req_id: str, payload: dict):
        room = self.rooms.get(req_id)
        if not room:
            return
        dead = []
        for ws in list(room):
            try:
                await ws.send_text(json.dumps(payload))
            except Exception:
                dead.append(ws)
        for ws in dead:
            room.discard(ws)


manager = _ConnManager()


# ── Flexible auth: Bearer OR ?token= (for <a href> / <img src>) ───────────────
_bearer = HTTPBearer(auto_error=False)

async def _auth_flexible(
    token:       Optional[str]                           = Query(None),
    credentials: Optional[HTTPAuthorizationCredentials]  = Depends(_bearer),
) -> dict:
    raw = credentials.credentials if credentials else token
    if not raw:
        raise HTTPException(401, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})
    return decode_token(raw)


# ── Upload configuration ───────────────────────────────────────────────────────
_UPLOAD_BASE       = Path("static/uploads/requests")
_DOC_ALLOWED_EXTS  = {".pdf", ".jpg", ".jpeg", ".png"}
_DOC_MAX_BYTES     = 5 * 1024 * 1024   # 5 MB
_CFG_ALLOWED_EXTS  = {".ovpn", ".conf", ".zip", ".pdf", ".txt", ".pem", ".crt", ".key"}
_CFG_MAX_BYTES     = 20 * 1024 * 1024  # 20 MB

_MAGIC: dict[str, list[bytes]] = {
    ".jpg":  [b"\xff\xd8\xff"],
    ".jpeg": [b"\xff\xd8\xff"],
    ".png":  [b"\x89PNG\r\n\x1a\n"],
    ".pdf":  [b"%PDF"],
    ".zip":  [b"PK\x03\x04"],
}

VALID_STATUSES = ("ON_PROGRESS", "DONE", "DECLINE")
_INFRA_ADMIN_ROLES = (Role.SUPERADMIN, Role.SYSADMIN)


def _verify_magic(content: bytes, ext: str) -> bool:
    candidates = _MAGIC.get(ext)
    if not candidates:
        return True
    return any(content[:len(m)] == m for m in candidates)


def _sanitize_filename(name: str) -> str:
    name = name.replace("\x00", "")
    name = unicodedata.normalize("NFKC", name)
    name = name.replace("..", "").replace("/", "").replace("\\", "")
    return name.strip() or "upload"


# ── Schemas ────────────────────────────────────────────────────────────────────
class CreateRequestBody(BaseModel):
    request_type: str
    specs:        Optional[dict] = None
    notes:        str = ""

class ReviewBody(BaseModel):
    status:           str               # ON_PROGRESS | DONE | DECLINE
    admin_note:       str = ""
    # VPN credentials (filled when type=VPN and status=DONE)
    vpn_username:     Optional[str] = None
    vpn_password:     Optional[str] = None
    # VPS linked VM (filled when type=VPS and status=DONE)
    linked_vm_id:     Optional[str] = None
    linked_vm_name:   Optional[str] = None
    linked_host_name: Optional[str] = None



# ── Helper ─────────────────────────────────────────────────────────────────────
def _row(r) -> dict:
    d = dict(r)
    if isinstance(d.get("specs"), str):
        try:
            d["specs"] = json.loads(d["specs"])
        except Exception:
            d["specs"] = None
    return d


async def _get_request_or_403(conn, req_id: str, user: dict) -> dict:
    row = await conn.fetchrow(
        """SELECT ir.*,
                  u.full_name  AS student_name,
                  u.username   AS student_username,
                  rv.full_name AS reviewer_name
           FROM infrastructure_requests ir
           JOIN users u  ON u.id  = ir.student_id
           LEFT JOIN users rv ON rv.id = ir.reviewed_by
           WHERE ir.id = $1""",
        req_id,
    )
    if not row:
        raise HTTPException(404, "Request tidak ditemukan")
    r = _row(row)
    is_admin = user["role"] in _INFRA_ADMIN_ROLES
    if not is_admin and r["student_id"] != int(user["sub"]):
        raise HTTPException(403, "Akses ditolak")
    return r


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("")
async def create_request(body: CreateRequestBody, user: dict = Depends(get_current_user)):
    if body.request_type not in ("VPS", "VPN"):
        raise HTTPException(400, "request_type harus 'VPS' atau 'VPN'")

    req_id     = str(uuid.uuid4())
    specs_json = json.dumps(body.specs) if body.specs else None

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO infrastructure_requests
                   (id, student_id, request_type, specs, notes)
               VALUES ($1, $2, $3, $4::jsonb, $5)
               RETURNING *""",
            req_id, int(user["sub"]), body.request_type, specs_json, body.notes,
        )
    return _row(row)


@router.get("")
async def list_requests(
    status:       Optional[str] = Query(None),
    request_type: Optional[str] = Query(None),
    page:         int = Query(1, ge=1),
    page_size:    int = Query(20, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    is_admin = user["role"] in _INFRA_ADMIN_ROLES

    conditions: list[str] = []
    params:     list      = []
    idx = 1

    if not is_admin:
        conditions.append(f"ir.student_id = ${idx}"); params.append(int(user["sub"])); idx += 1
    if status:
        conditions.append(f"ir.status = ${idx}"); params.append(status.upper()); idx += 1
    if request_type:
        conditions.append(f"ir.request_type = ${idx}"); params.append(request_type.upper()); idx += 1

    where  = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    offset = (page - 1) * page_size

    pool = await get_pool()
    async with pool.acquire() as conn:
        total = await conn.fetchval(
            f"""SELECT COUNT(*)
                FROM infrastructure_requests ir
                JOIN users u ON u.id = ir.student_id
                {where}""",
            *params,
        )
        rows = await conn.fetch(
            f"""SELECT ir.*,
                       u.full_name  AS student_name,
                       u.username   AS student_username,
                       rv.full_name AS reviewer_name
                FROM infrastructure_requests ir
                JOIN users u  ON u.id  = ir.student_id
                LEFT JOIN users rv ON rv.id = ir.reviewed_by
                {where}
                ORDER BY ir.created_at DESC
                LIMIT ${idx} OFFSET ${idx + 1}""",
            *params, page_size, offset,
        )

    return {"total": total, "items": [_row(r) for r in rows]}


@router.get("/{req_id}/document")
async def download_document(req_id: str, user: dict = Depends(_auth_flexible)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _get_request_or_403(conn, req_id, user)

    req_dir = _UPLOAD_BASE / req_id / "doc"
    if not req_dir.is_dir():
        raise HTTPException(404, "Dokumen belum diunggah")
    files = [f for f in req_dir.iterdir() if f.is_file()]
    if not files:
        raise HTTPException(404, "Dokumen belum diunggah")

    fp   = files[0]
    mime = mimetypes.guess_type(str(fp))[0] or "application/octet-stream"
    inline = fp.suffix.lower() in {".jpg", ".jpeg", ".png"}

    return FileResponse(
        path=fp, media_type=mime,
        headers={
            "Content-Disposition": f'{"inline" if inline else "attachment"}; filename="{fp.name}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=3600",
        },
    )


@router.get("/{req_id}/config")
async def download_config(req_id: str, user: dict = Depends(_auth_flexible)):
    """Download VPN config file uploaded by admin."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _get_request_or_403(conn, req_id, user)

    config_dir = _UPLOAD_BASE / req_id / "config"
    if not config_dir.is_dir():
        raise HTTPException(404, "File konfigurasi belum diunggah")
    files = [f for f in config_dir.iterdir() if f.is_file()]
    if not files:
        raise HTTPException(404, "File konfigurasi belum diunggah")

    fp   = files[0]
    mime = mimetypes.guess_type(str(fp))[0] or "application/octet-stream"

    return FileResponse(
        path=fp, media_type=mime,
        headers={
            "Content-Disposition": f'attachment; filename="{fp.name}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=3600",
        },
    )


@router.get("/{req_id}/messages")
async def get_messages(req_id: str, user: dict = Depends(get_current_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _get_request_or_403(conn, req_id, user)
        rows = await conn.fetch(
            """SELECT id, sender_id, sender_role, sender_name, message, created_at
               FROM infra_request_messages
               WHERE request_id = $1
               ORDER BY created_at ASC""",
            req_id,
        )
    return [
        {
            "id":          r["id"],
            "sender_id":   r["sender_id"],
            "sender_role": r["sender_role"],
            "sender_name": r["sender_name"],
            "message":     r["message"],
            "created_at":  r["created_at"].isoformat(),
        }
        for r in rows
    ]


@router.get("/{req_id}")
async def get_request(req_id: str, user: dict = Depends(get_current_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await _get_request_or_403(conn, req_id, user)


@router.post("/{req_id}/document")
async def upload_document(
    req_id: str,
    file:   UploadFile = File(...),
    user:   dict = Depends(get_current_user),
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        req = await _get_request_or_403(conn, req_id, user)

    if req["student_id"] != int(user["sub"]):
        raise HTTPException(403, "Hanya pemilik request yang bisa mengunggah dokumen")

    original_name = _sanitize_filename(file.filename or "upload")
    ext = Path(original_name).suffix.lower()
    if ext not in _DOC_ALLOWED_EXTS:
        raise HTTPException(400, f"Format tidak didukung '{ext}'. Diperbolehkan: PDF, JPG, JPEG, PNG")

    content = await file.read()
    if len(content) > _DOC_MAX_BYTES:
        raise HTTPException(413, f"File terlalu besar ({len(content) // 1024} KB). Maksimum 5 MB")

    declared_mime = file.content_type or ""
    if not any(declared_mime.startswith(p) for p in ("image/", "application/pdf")):
        raise HTTPException(415, f"MIME type tidak diizinkan: '{declared_mime}'")

    if not _verify_magic(content, ext):
        raise HTTPException(422, "Isi file tidak sesuai ekstensinya")

    doc_dir = _UPLOAD_BASE / req_id / "doc"
    doc_dir.mkdir(parents=True, exist_ok=True)
    for old in doc_dir.iterdir():
        if old.is_file():
            old.unlink(missing_ok=True)

    stored_name = f"{uuid.uuid4().hex}{ext}"
    (doc_dir / stored_name).write_bytes(content)

    doc_url = f"/api/v1/infra-requests/{req_id}/document"
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE infrastructure_requests SET document_url = $1, updated_at = NOW() WHERE id = $2",
            doc_url, req_id,
        )

    return {"document_url": doc_url, "original_name": original_name, "size": len(content)}


@router.post("/{req_id}/config")
async def upload_config(
    req_id: str,
    file:   UploadFile = File(...),
    user:   dict = Depends(require_sysadmin),
):
    """Admin uploads VPN config / certificate file for the student."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _get_request_or_403(conn, req_id, user)

    original_name = _sanitize_filename(file.filename or "config")
    ext = Path(original_name).suffix.lower()
    if ext not in _CFG_ALLOWED_EXTS:
        raise HTTPException(400, f"Format tidak didukung. Diperbolehkan: {', '.join(sorted(_CFG_ALLOWED_EXTS))}")

    content = await file.read()
    if len(content) > _CFG_MAX_BYTES:
        raise HTTPException(413, "File terlalu besar (maks 20 MB)")

    if not _verify_magic(content, ext):
        raise HTTPException(422, "Magic bytes tidak sesuai ekstensi")

    config_dir = _UPLOAD_BASE / req_id / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    for old in config_dir.iterdir():
        if old.is_file():
            old.unlink(missing_ok=True)

    stored_name = f"{uuid.uuid4().hex}{ext}"
    (config_dir / stored_name).write_bytes(content)

    config_url = f"/api/v1/infra-requests/{req_id}/config"
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE infrastructure_requests SET config_file_url = $1, updated_at = NOW() WHERE id = $2",
            config_url, req_id,
        )

    return {"config_file_url": config_url, "original_name": original_name, "size": len(content)}


@router.websocket("/{req_id}/ws")
async def infra_ws(websocket: WebSocket, req_id: str, token: str = Query(...)):
    await websocket.accept()
    try:
        user = decode_token(token)
    except Exception:
        await websocket.close(code=4401)
        return

    # Cek blocklist
    jti = user.get("jti")
    if jti:
        import token_blocklist
        if await token_blocklist.is_revoked(jti):
            await websocket.close(code=4401)
            return

    pool = await get_pool()
    async with pool.acquire() as conn:
        req = await conn.fetchrow(
            "SELECT student_id, status FROM infrastructure_requests WHERE id = $1", req_id
        )
    if not req:
        await websocket.close(code=4404)
        return
    if user["role"] not in _INFRA_ADMIN_ROLES and req["student_id"] != int(user["sub"]):
        await websocket.close(code=1008)
        return

    manager.register(req_id, websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            msg = (data.get("message") or "").strip()
            if not msg:
                continue

            async with pool.acquire() as conn:
                cur_status = await conn.fetchval(
                    "SELECT status FROM infrastructure_requests WHERE id = $1", req_id
                )
            if cur_status == "DONE":
                try:
                    await websocket.send_text(json.dumps(
                        {"type": "error", "message": "Request sudah selesai — chat sudah ditutup"}
                    ))
                except Exception:
                    pass
                continue

            try:
                async with pool.acquire() as conn:
                    row = await conn.fetchrow(
                        """INSERT INTO infra_request_messages
                               (request_id, sender_id, sender_role, sender_name, message)
                           VALUES ($1, $2, $3, $4, $5)
                           RETURNING id, created_at""",
                        req_id, int(user["sub"]), user.get("role"), user.get("username"), msg,
                    )
                await manager.broadcast(req_id, {
                    "type":        "message",
                    "id":          row["id"],
                    "sender_id":   int(user["sub"]),
                    "sender_role": user.get("role"),
                    "sender_name": user.get("username"),
                    "message":     msg,
                    "created_at":  row["created_at"].isoformat(),
                })
            except Exception as e:
                log.warning("infra ws save gagal: %s", e)
    except WebSocketDisconnect:
        manager.disconnect(req_id, websocket)
    except Exception as e:
        log.debug("infra ws closed: %s", e)
        manager.disconnect(req_id, websocket)


@router.patch("/{req_id}/status")
async def review_request(
    req_id: str,
    body:   ReviewBody,
    user:   dict = Depends(require_sysadmin),
):
    if body.status not in VALID_STATUSES:
        raise HTTPException(400, f"status harus salah satu dari: {', '.join(VALID_STATUSES)}")

    pool = await get_pool()
    async with pool.acquire() as conn:
        req = await _get_request_or_403(conn, req_id, user)

        row = await conn.fetchrow(
            """UPDATE infrastructure_requests
               SET status            = $1,
                   admin_note        = $2,
                   reviewed_by       = $3,
                   reviewed_at       = NOW(),
                   updated_at        = NOW(),
                   vpn_username      = COALESCE($4,  vpn_username),
                   vpn_password      = COALESCE($5,  vpn_password),
                   linked_vm_id      = COALESCE($6,  linked_vm_id),
                   linked_vm_name    = COALESCE($7,  linked_vm_name),
                   linked_host_name  = COALESCE($8,  linked_host_name)
               WHERE id = $9
               RETURNING *""",
            body.status,
            body.admin_note,
            int(user["sub"]),
            body.vpn_username   or None,
            body.vpn_password   or None,
            body.linked_vm_id   or None,
            body.linked_vm_name or None,
            body.linked_host_name or None,
            req_id,
        )
        if not row:
            raise HTTPException(404, "Request tidak ditemukan")

        # Auto-upgrade student ke akses penuh saat pertama kali DONE
        if body.status == "DONE":
            student_id  = row["student_id"]
            is_verified = await conn.fetchval(
                "SELECT is_verified FROM users WHERE id = $1", student_id
            )
            if not is_verified:
                await conn.execute(
                    "UPDATE users SET is_verified = true, updated_at = NOW() WHERE id = $1",
                    student_id,
                )

    return _row(row)
