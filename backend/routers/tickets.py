"""
Helpdesk / Ticketing — terintegrasi dengan RBAC & audit log.

RBAC:
  student          : buat tiket, lihat & balas tiket MILIKNYA saja.
  admin/sysadmin/superadmin : lihat semua, ubah status, balas tiket mana pun.
"""
import io
import json
import logging
import mimetypes
import re
import unicodedata
import uuid
import zipfile
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Depends, File, Request, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

from database import get_pool, get_student_vm_ids
from auth import get_current_user, require_superadmin, verify_token, Role
from i18n import tr

# ── Flexible auth: Bearer header OR ?token= query param ──────────────────────
# <img src> / <a href> cannot set headers, so the download endpoint also
# accepts the JWT as a query parameter. Bearer header takes priority.
_bearer = HTTPBearer(auto_error=False)

async def _auth_flexible(
    token: Optional[str] = Query(None),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> dict:
    raw = credentials.credentials if credentials else token
    if not raw:
        raise HTTPException(401, tr("Belum login", "Not authenticated"),
                            headers={"WWW-Authenticate": "Bearer"})
    return await verify_token(raw)  # raises 401 on expired / invalid
from services.audit import both, log_activity

router = APIRouter()
log = logging.getLogger("tickets")

# ── File upload configuration ─────────────────────────────────────────────────
_ALLOWED_EXTS  = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".pdf", ".txt", ".log", ".zip"}
_ALLOWED_MIME_PREFIXES = ("image/", "application/pdf", "text/", "application/zip",
                           "application/x-zip")
_MAX_UPLOAD_BYTES = 10 * 1024 * 1024   # 10 MB
_UPLOAD_BASE = Path("static/uploads/tickets")

# Known file magic bytes — used to verify content matches declared extension.
# Key: normalised extension; value: list of valid leading byte sequences.
_MAGIC: dict[str, list[bytes]] = {
    ".jpg":  [b"\xff\xd8\xff"],
    ".jpeg": [b"\xff\xd8\xff"],
    ".png":  [b"\x89PNG\r\n\x1a\n"],
    ".gif":  [b"GIF87a", b"GIF89a"],
    ".pdf":  [b"%PDF"],
    # WebP checked separately (RIFF header + "WEBP" at offset 8)
}


def _verify_magic(content: bytes, ext: str) -> bool:
    """Return True if file content header matches the declared extension.

    Protects against polyglot / disguised-extension attacks
    (e.g. an HTML file uploaded as image.jpg to enable stored XSS).
    """
    if ext == ".webp":
        return (len(content) >= 12
                and content[:4] == b"RIFF"
                and content[8:12] == b"WEBP")
    candidates = _MAGIC.get(ext)
    if not candidates:
        # .txt / .log / .zip — no single definitive magic; skip
        return True
    return any(content[:len(m)] == m for m in candidates)


def _check_zip_bomb(content: bytes,
                    max_uncompressed: int = 500 * 1024 * 1024) -> bool:
    """Return True if the ZIP is safe (not a bomb).

    Reads only the central-directory headers (no extraction) to get the
    declared uncompressed sizes.  Rejects any ZIP whose total declared
    expansion exceeds max_uncompressed (default 500 MB).

    A compression-ratio check is intentionally omitted: legitimate log files
    can compress 1000x or more, so ratio thresholds produce false positives.
    The absolute ceiling is the reliable guard against resource exhaustion.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            total = sum(info.file_size for info in zf.infolist())
    except Exception:
        return False  # Unreadable or encrypted ZIP treated as unsafe
    return total <= max_uncompressed


def _sanitize_filename(name: str) -> str:
    """Harden the uploaded filename before storing in the database.

    Handles three distinct attack vectors:
      - Null-byte injection  (``evil.php\\x00.jpg``)
      - Unicode lookalike path traversal  (U+2025 ‥ normalises to ``..``)
      - Classic dot-dot / path-separator injection
    """
    name = name.replace("\x00", "")           # null byte injection
    name = unicodedata.normalize("NFKC", name)  # e.g. ‥ → .., ／ → /
    name = name.replace("..", "").replace("/", "").replace("\\", "")
    return name.strip() or "upload"


# ── Real-time chat: ConnectionManager (room per ticket_id) ────────────────────
class ConnectionManager:
    def __init__(self):
        self.rooms: dict[int, set] = {}

    async def connect(self, ticket_id: int, ws: WebSocket):
        await ws.accept()
        self.rooms.setdefault(ticket_id, set()).add(ws)

    def register(self, ticket_id: int, ws: WebSocket):
        """Daftarkan ws yang SUDAH di-accept (untuk kontrol kode close manual)."""
        self.rooms.setdefault(ticket_id, set()).add(ws)

    def disconnect(self, ticket_id: int, ws: WebSocket):
        room = self.rooms.get(ticket_id)
        if room:
            room.discard(ws)
            if not room:
                self.rooms.pop(ticket_id, None)

    async def broadcast(self, ticket_id: int, payload: dict):
        room = self.rooms.get(ticket_id)
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


manager = ConnectionManager()

_ADMIN_ROLES = (Role.SUPERADMIN, Role.SYSADMIN)
STATUSES   = ("OPEN", "IN_PROGRESS", "RESOLVED", "CLOSED")


def _is_admin(user: dict) -> bool:
    return user["role"] in _ADMIN_ROLES


class CreateTicket(BaseModel):
    title:       str
    category:    str = "OTHERS"
    description: str = ""
    vm_id:       Optional[str] = None
    host_name:   Optional[str] = None
    ccd_id:      Optional[str] = None   # mis. "CCD-0007" atau "7"; menggantikan vm_id + host_name
    vm_snapshot: Optional[dict] = None


def _parse_ccd_id(text: str) -> int | None:
    m = re.fullmatch(r"\s*(?:ccd-?)?0*(\d{1,9})\s*", text or "", re.IGNORECASE)
    return int(m[1]) if m else None


async def _resolve_vm(body: CreateTicket) -> tuple[Optional[str], Optional[str], Optional[int], Optional[dict]]:
    """VM tiket -> (vm_id, host_name, ccd_id, data VM). CCDID diterjemahkan ke host + VMID; tiket dari
    Detail VM (host + VMID) dilengkapi CCDID-nya."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        if body.ccd_id:
            ccd = _parse_ccd_id(body.ccd_id)
            row = await conn.fetchrow(
                "SELECT vm_id, host_name, ccd_id, vm_name, state FROM vms WHERE ccd_id = $1", ccd) if ccd else None
            if not row:
                raise HTTPException(400, tr(f"CCDID '{body.ccd_id[:20]}' tidak ditemukan",
                                            f"CCDID '{body.ccd_id[:20]}' not found"))
            return row["vm_id"], row["host_name"], row["ccd_id"], dict(row)
        if body.vm_id and body.host_name:
            row = await conn.fetchrow(
                "SELECT vm_id, host_name, ccd_id, vm_name, state FROM vms WHERE vm_id = $1 AND host_name = $2",
                body.vm_id, body.host_name)
            return body.vm_id, body.host_name, row["ccd_id"] if row else None, dict(row) if row else None
    return body.vm_id, body.host_name, None, None


class StatusUpdate(BaseModel):
    status: str


class ReplyBody(BaseModel):
    message:         str            = ""
    attachment_url:  Optional[str]  = None
    attachment_name: Optional[str]  = None


def _ticket_row(r) -> dict:
    return {
        "id": r["id"],
        "ticket_number": r["ticket_number"],
        "student_id": r["student_id"],
        "student_name": r.get("student_name") if isinstance(r, dict) else r["student_name"],
        "vm_id": r["vm_id"],
        "host_name": r["host_name"],
        "ccd_id": r.get("ccd_id") if isinstance(r, dict) else r["ccd_id"],
        "title": r["title"],
        "category": r["category"],
        "description": r["description"],
        "status": r["status"],
        "vm_snapshot": r["vm_snapshot"] if isinstance(r["vm_snapshot"], dict) else (json.loads(r["vm_snapshot"]) if r["vm_snapshot"] else None),
        "created_at": r["created_at"].isoformat(),
        "updated_at": r["updated_at"].isoformat(),
        "closed_at": r["closed_at"].isoformat() if r.get("closed_at") else None,
    }


def _status_changed(status: str, user: dict):
    return both(lambda: tr(f"Status diubah ke {status} oleh {user.get('username')} ({user.get('role')})",
                           f"Status changed to {status} by {user.get('username')} ({user.get('role')})"))


async def _system_msg(conn, ticket_id: int, text) -> dict:
    """Sisipkan pesan sistem (perubahan status, dll) ke thread chat & kembalikan payload broadcast.
    `text` dua bahasa (services.audit.both); pembaca memilih sesuai bahasanya lewat message_en."""
    row = await conn.fetchrow(
        """INSERT INTO ticket_messages (ticket_id, sender_id, sender_role, sender_name, message, message_en)
           VALUES ($1, NULL, 'system', 'System', $2, $3) RETURNING id, created_at""",
        ticket_id, str(text), getattr(text, "en", None))
    return {
        "type": "message", "id": row["id"], "sender_id": None,
        "sender_role": "system", "sender_name": "System",
        "message": str(text), "message_en": getattr(text, "en", None), "timestamp": row["created_at"].isoformat(),
    }


@router.post("")
async def create_ticket(body: CreateTicket, request: Request, user: dict = Depends(get_current_user)):
    if not body.title.strip():
        raise HTTPException(400, tr("Judul wajib diisi", "A title is required"))
    # Unverified students (self-registered, no approved infra request yet) cannot open tickets
    if user.get("role") == Role.STUDENT:
        pool = await get_pool()
        async with pool.acquire() as conn:
            verified = await conn.fetchval(
                "SELECT is_verified FROM users WHERE id = $1", int(user["sub"])
            )
        if not verified:
            raise HTTPException(
                403,
                tr("Akses terbatas. Ajukan Infrastructure Request dan tunggu persetujuan admin "
                   "untuk mendapatkan akses penuh ke fitur Helpdesk.",
                   "Limited access. Submit an infrastructure request and wait for admin approval "
                   "to get full access to the Helpdesk.")
            )
    vm_id, host_name, ccd_id, vm = await _resolve_vm(body)
    # VMID hanya unik per Proxmox, jadi yang dicek pasangan host + VMID: mahasiswa hanya boleh
    # melaporkan VM miliknya.
    if user.get("role") == Role.STUDENT and host_name and \
            (vm_id, host_name) not in await get_student_vm_ids(int(user["sub"])):
        if body.ccd_id:   # pesan sama dengan CCDID yang tidak ada, supaya CCDID VM lain tidak bisa ditebak
            raise HTTPException(400, tr(f"CCDID '{body.ccd_id[:20]}' tidak ditemukan",
                                        f"CCDID '{body.ccd_id[:20]}' not found"))
        raise HTTPException(403, tr("VM ini tidak ditugaskan kepada Anda", "This VM is not assigned to you"))
    from services.system_settings import category_keys, get_settings
    category = body.category if body.category in category_keys(await get_settings()) else "OTHERS"
    snapshot = body.vm_snapshot or ({"vm_name": vm["vm_name"], "state": vm["state"]} if vm else None)
    snap = json.dumps(snapshot) if snapshot else None
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO tickets (student_id, vm_id, host_name, ccd_id, title, category, description, vm_snapshot)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8) RETURNING id""",
            int(user["sub"]), vm_id, host_name, ccd_id, body.title.strip(),
            category, body.description, snap)
        tid = row["id"]
        number = f"TKT-{tid:04d}"
        await conn.execute("UPDATE tickets SET ticket_number = $2 WHERE id = $1", tid, number)
    await log_activity(user, "TICKET_CREATE", "INFO",
                       {"id": vm_id, "name": number},
                       both(lambda: tr(f"Tiket {number} dibuat: {body.title.strip()}", f"Ticket {number} created: {body.title.strip()}")), request)
    return {"id": tid, "ticket_number": number, "status": "OPEN"}


@router.get("")
async def list_tickets(
    status: str = "", category: str = "", search: str = "",
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
    user: dict = Depends(get_current_user),
):
    where, params, i = [], [], 1
    if not _is_admin(user):
        where.append(f"t.student_id = ${i}"); params.append(int(user["sub"])); i += 1
    if status in STATUSES:
        where.append(f"t.status = ${i}"); params.append(status); i += 1
    if category and len(category) <= 30 and category.replace("_", "").isalnum():   # termasuk kategori lama
        where.append(f"t.category = ${i}"); params.append(category); i += 1
    if search:
        where.append(f"(t.title ILIKE ${i} OR t.ticket_number ILIKE ${i} OR u.username ILIKE ${i} "
                     f"OR 'CCD-' || lpad(t.ccd_id::text, 4, '0') ILIKE ${i})")
        params.append(f"%{search}%"); i += 1
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    pool = await get_pool()
    async with pool.acquire() as conn:
        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM tickets t JOIN users u ON u.id = t.student_id {clause}", *params)
        rows = await conn.fetch(
            f"""SELECT t.*, u.username AS student_name FROM tickets t
                JOIN users u ON u.id = t.student_id {clause}
                ORDER BY t.updated_at DESC LIMIT ${i} OFFSET ${i+1}""",
            *params, page_size, (page - 1) * page_size)
    return {"total": total or 0, "items": [_ticket_row(dict(r)) for r in rows]}


async def _get_ticket_or_403(conn, ticket_id: int, user: dict) -> dict:
    r = await conn.fetchrow(
        """SELECT t.*, u.username AS student_name FROM tickets t
           JOIN users u ON u.id = t.student_id WHERE t.id = $1""", ticket_id)
    if not r:
        raise HTTPException(404, tr("Tiket tidak ditemukan", "Ticket not found"))
    if not _is_admin(user) and r["student_id"] != int(user["sub"]):
        raise HTTPException(403, tr("Anda tidak punya akses ke tiket ini",
                                    "You do not have access to this ticket"))
    return dict(r)


@router.get("/{ticket_id}")
async def get_ticket(ticket_id: int, user: dict = Depends(get_current_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        t = await _get_ticket_or_403(conn, ticket_id, user)
        msgs = await conn.fetch(
            "SELECT * FROM ticket_messages WHERE ticket_id = $1 ORDER BY created_at ASC", ticket_id)
        ticket = _ticket_row(t)
        if _is_admin(user) and t["vm_id"] and t["host_name"]:
            # Admin: apakah VM-nya masih ada dan kapan masa sewanya habis, untuk tombol "Buka detail VM".
            vm = await conn.fetchrow("SELECT lease_until FROM vms WHERE vm_id = $1 AND host_name = $2",
                                     t["vm_id"], t["host_name"])
            ticket["vm_live"] = vm is not None
            ticket["vm_lease_until"] = vm["lease_until"].isoformat() if vm and vm["lease_until"] else None
    return {
        "ticket": ticket,
        "messages": [
            {
                "id": m["id"], "sender_id": m["sender_id"], "sender_role": m["sender_role"],
                "sender_name": m["sender_name"], "message": m["message"], "message_en": m["message_en"],
                "timestamp": m["created_at"].isoformat(),
                "attachment_url":  m["attachment_url"],
                "attachment_name": m["attachment_name"],
            } for m in msgs
        ],
    }


@router.post("/{ticket_id}/upload")
async def upload_ticket_file(
    ticket_id: int,
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    """Upload an image or document attachment for a ticket. Returns the public URL."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        t = await _get_ticket_or_403(conn, ticket_id, user)
    if t["status"] == "CLOSED":
        raise HTTPException(403, tr("Tiket sudah ditutup — tidak bisa mengunggah lampiran.",
                                    "The ticket is closed — attachments can no longer be uploaded."))

    # 1 — Sanitise original filename (null bytes, unicode tricks, path separators)
    original_name = _sanitize_filename(file.filename or "upload")
    ext = Path(original_name).suffix.lower()
    if ext not in _ALLOWED_EXTS:
        raise HTTPException(400, tr(f"Ekstensi tidak diizinkan: {ext or '(tidak ada)'}. "
                                    f"Diizinkan: {', '.join(sorted(_ALLOWED_EXTS))}",
                                    f"Extension not allowed: {ext or '(none)'}. "
                                    f"Allowed: {', '.join(sorted(_ALLOWED_EXTS))}"))

    content = await file.read()

    # 2 — Size gate
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(413, tr("File terlalu besar (maks 10 MB)", "The file is too large (max 10 MB)"))

    # 3 — MIME check (Content-Type header, can be spoofed — magic bytes below is the real guard)
    mime = file.content_type or mimetypes.guess_type(original_name)[0] or "application/octet-stream"
    if not any(mime.startswith(p) for p in _ALLOWED_MIME_PREFIXES):
        raise HTTPException(400, tr(f"MIME type tidak diizinkan: {mime}", f"MIME type not allowed: {mime}"))

    # 4 — Magic bytes validation: file content MUST match declared extension.
    #     Blocks polyglot files (e.g. HTML disguised as .jpg for stored XSS).
    if not _verify_magic(content, ext):
        raise HTTPException(400,
            tr(f"Isi file tidak sesuai dengan ekstensi {ext}. "
               "File kemungkinan dimanipulasi atau disamarkan.",
               f"The file contents do not match the {ext} extension. "
               "The file may have been tampered with or disguised."))

    # 5 — ZIP bomb detection: check declared-uncompressed ratio without extraction.
    if ext == ".zip" and not _check_zip_bomb(content):
        raise HTTPException(400,
            tr("File ZIP ditolak: rasio kompresi mencurigakan atau ukuran ekstraksi "
               "melampaui batas (ZIP bomb).",
               "ZIP file refused: suspicious compression ratio or extracted size "
               "over the limit (ZIP bomb)."))

    ticket_dir = _UPLOAD_BASE / str(ticket_id)
    ticket_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}{ext}"
    (ticket_dir / stored_name).write_bytes(content)

    # URL points to the authenticated endpoint, not the public static mount.
    file_url = f"/api/tickets/{ticket_id}/files/{stored_name}"

    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO ticket_attachments
               (ticket_id, stored_name, original_name, file_url, file_size, mime_type, uploader_id)
               VALUES ($1, $2, $3, $4, $5, $6, $7)""",
            ticket_id, stored_name, original_name, file_url,
            len(content), mime, int(user["sub"]),
        )

    return {"url": file_url, "filename": original_name, "size": len(content), "mime": mime}


@router.get("/{ticket_id}/files/{stored_name}")
async def download_ticket_file(
    ticket_id: int,
    stored_name: str,
    user: dict = Depends(_auth_flexible),
):
    """Serve a ticket attachment.

    Access control:
      • Student   — only their own tickets
      • Admin / Sysadmin / Superadmin — any ticket

    Accepts JWT via:
      • Authorization: Bearer <token>  (fetch / axios)
      • ?token=<jwt>                   (<img src>, <a href> — browsers cannot set headers)
    """
    # Strip directory components — must be a bare filename
    safe_name = Path(stored_name).name
    if not safe_name or safe_name != stored_name:
        raise HTTPException(400, tr("Nama file tidak valid", "Invalid file name"))

    file_path = _UPLOAD_BASE / str(ticket_id) / safe_name
    if not file_path.is_file():
        raise HTTPException(404, tr("File tidak ditemukan", "File not found"))

    # RBAC: ensure user may access this ticket (raises 403/404 otherwise)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _get_ticket_or_403(conn, ticket_id, user)

    ext  = file_path.suffix.lower()
    mime = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    is_image = ext in {".jpg", ".jpeg", ".png", ".gif", ".webp"}

    return FileResponse(
        path=file_path,
        media_type=mime,
        headers={
            # Inline for images (displayed in <img>); attachment for everything else
            "Content-Disposition": (
                f'inline; filename="{safe_name}"'
                if is_image else
                f'attachment; filename="{safe_name}"'
            ),
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
            "Cache-Control":           "private, max-age=3600",
        },
    )


@router.patch("/{ticket_id}/status")
async def update_status(ticket_id: int, body: StatusUpdate, request: Request, user: dict = Depends(get_current_user)):
    if not _is_admin(user):
        raise HTTPException(403, tr("Hanya admin/sysadmin yang dapat mengubah status",
                                    "Only admins/sysadmins can change the status"))
    if body.status not in STATUSES:
        raise HTTPException(400, tr(f"Status tidak valid. Pilihan: {STATUSES}",
                                    f"Invalid status. Options: {STATUSES}"))
    pool = await get_pool()
    async with pool.acquire() as conn:
        cur = await conn.fetchrow(
            "SELECT status, ticket_number FROM tickets WHERE id = $1", ticket_id)
        if not cur:
            raise HTTPException(404, tr("Tiket tidak ditemukan", "Ticket not found"))
        number = cur["ticket_number"]
        # Tiket yang sudah CLOSED terkunci: hanya superadmin yang boleh membuka/mengubah lagi.
        if cur["status"] == "CLOSED" and user["role"] != Role.SUPERADMIN:
            raise HTTPException(403, tr("Tiket sudah ditutup. Hanya superadmin yang dapat membukanya kembali.",
                                        "The ticket is closed. Only a superadmin can reopen it."))
        if cur["status"] == body.status:
            return {"status": body.status, "ticket_number": number}
        # Set closed_at saat menutup; bersihkan saat dibuka kembali.
        if body.status == "CLOSED":
            await conn.execute(
                "UPDATE tickets SET status = $2, closed_at = NOW(), updated_at = NOW() WHERE id = $1",
                ticket_id, body.status)
        else:
            await conn.execute(
                "UPDATE tickets SET status = $2, closed_at = NULL, updated_at = NOW() WHERE id = $1",
                ticket_id, body.status)
        sysmsg = await _system_msg(conn, ticket_id, _status_changed(body.status, user))
    sev = "WARNING" if body.status in ("RESOLVED", "CLOSED") else "INFO"
    await log_activity(
        user, "TICKET_STATUS", sev, {"id": str(ticket_id), "name": number},
        both(lambda: tr(f"{user.get('role').capitalize()} {user.get('username')} mengubah {number} → {body.status}",
                        f"{user.get('role').capitalize()} {user.get('username')} changed {number} → {body.status}")),
        request)
    await manager.broadcast(ticket_id, sysmsg)  # pesan sistem muncul di thread chat
    await manager.broadcast(ticket_id, {"type": "status", "status": body.status})
    return {"status": body.status, "ticket_number": number}


@router.delete("/{ticket_id}")
async def delete_ticket(ticket_id: int, request: Request, user: dict = Depends(require_superadmin)):
    """Hapus tiket beserta pesan dan lampirannya (superadmin). Ringkasannya dicatat di Audit Trail
    sebelum dihapus; isi percakapan tidak disalin."""
    from services.record_purge import remove_dir, short, stamp
    pool = await get_pool()
    async with pool.acquire() as conn:
        t = await conn.fetchrow(
            """SELECT t.ticket_number, t.title, t.category, t.status, t.created_at, t.closed_at, u.username AS student,
                      (SELECT count(*) FROM ticket_messages m WHERE m.ticket_id = t.id AND m.sender_id IS NOT NULL) AS messages,
                      (SELECT count(*) FROM ticket_attachments a WHERE a.ticket_id = t.id) AS attachments
               FROM tickets t JOIN users u ON u.id = t.student_id WHERE t.id = $1""", ticket_id)
        if not t:
            raise HTTPException(404, tr("Tiket tidak ditemukan", "Ticket not found"))
        # Ringkasan dicatat sebelum tiketnya dihapus.
        await log_activity(
            user, "TICKET_DELETE", "WARNING", {"id": str(ticket_id), "name": t["ticket_number"]},
            both(lambda: tr(
                f"{user.get('username')} menghapus tiket {t['ticket_number']} '{short(t['title'])}' milik {t['student']} "
                f"(kategori {t['category']}, status {t['status']}, dibuat {stamp(t['created_at'])}, "
                f"ditutup {stamp(t['closed_at'])}, {t['messages']} pesan, {t['attachments']} lampiran)",
                f"{user.get('username')} deleted ticket {t['ticket_number']} '{short(t['title'])}' of {t['student']} "
                f"(category {t['category']}, status {t['status']}, created {stamp(t['created_at'])}, "
                f"closed {stamp(t['closed_at'])}, {t['messages']} messages, {t['attachments']} attachments)")), request)
        await conn.execute("DELETE FROM tickets WHERE id = $1", ticket_id)
    remove_dir(_UPLOAD_BASE, str(ticket_id))
    return {"status": "deleted", "ticket_number": t["ticket_number"]}


@router.post("/{ticket_id}/messages")
async def reply_ticket(ticket_id: int, body: ReplyBody, request: Request, user: dict = Depends(get_current_user)):
    msg      = body.message.strip()
    att_url  = (body.attachment_url or "").strip()
    att_name = (body.attachment_name or "").strip()
    if not msg and not att_url:
        raise HTTPException(400, tr("Pesan atau lampiran diperlukan", "A message or attachment is required"))
    # Guard: attachment URL must belong to this ticket's upload folder
    if att_url and not att_url.startswith(f"/api/tickets/{ticket_id}/files/"):
        raise HTTPException(400, tr("URL lampiran tidak valid", "Invalid attachment URL"))
    pool = await get_pool()
    async with pool.acquire() as conn:
        t = await _get_ticket_or_403(conn, ticket_id, user)  # enforces ownership/RBAC
        if t["status"] == "CLOSED":
            raise HTTPException(403, tr("Tiket sudah ditutup — tidak bisa menambahkan pesan.",
                                        "The ticket is closed — no more messages can be added."))
        row = await conn.fetchrow(
            """INSERT INTO ticket_messages
               (ticket_id, sender_id, sender_role, sender_name, message, attachment_url, attachment_name)
               VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING id, created_at""",
            ticket_id, int(user["sub"]), user.get("role"), user.get("username"),
            msg, att_url or None, att_name or None)
        # IN_PROGRESS otomatis saat admin pertama membalas tiket OPEN
        sysmsg = None
        if _is_admin(user) and t["status"] == "OPEN":
            await conn.execute(
                "UPDATE tickets SET updated_at = NOW(), status = 'IN_PROGRESS' WHERE id = $1", ticket_id)
            sysmsg = await _system_msg(conn, ticket_id, _status_changed("IN_PROGRESS", user))
        else:
            await conn.execute("UPDATE tickets SET updated_at = NOW() WHERE id = $1", ticket_id)
    payload = {
        "type": "message", "id": row["id"], "sender_id": int(user["sub"]),
        "sender_role": user.get("role"), "sender_name": user.get("username"),
        "message": msg, "timestamp": row["created_at"].isoformat(),
        "attachment_url":  att_url  or None,
        "attachment_name": att_name or None,
    }
    await manager.broadcast(ticket_id, payload)  # realtime ke semua peserta room
    if sysmsg:
        await manager.broadcast(ticket_id, sysmsg)
        await manager.broadcast(ticket_id, {"type": "status", "status": "IN_PROGRESS"})
    return {"id": row["id"], "timestamp": row["created_at"].isoformat()}


# ── WebSocket real-time chat room per ticket ──────────────────────────────────
@router.websocket("/{ticket_id}/ws")
async def ticket_ws(websocket: WebSocket, ticket_id: int, token: str = Query(...)):
    await websocket.accept()
    # Auth: validasi JWT pada handshake
    try:
        user = await verify_token(token)
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

    # RBAC: student hanya boleh join room tiket miliknya; admin/sysadmin/superadmin bebas
    pool = await get_pool()
    async with pool.acquire() as conn:
        t = await conn.fetchrow("SELECT student_id FROM tickets WHERE id = $1", ticket_id)
    if not t:
        await websocket.close(code=4404)
        return
    if not _is_admin(user) and t["student_id"] != int(user["sub"]):
        await websocket.close(code=1008)  # policy violation → 1008 close frame
        return

    manager.register(ticket_id, websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            msg      = (data.get("message") or "").strip()
            att_url  = (data.get("attachment_url") or "").strip()
            att_name = (data.get("attachment_name") or "").strip()
            # Validate attachment URL to prevent URL injection from untrusted clients
            if att_url and not att_url.startswith(f"/api/tickets/{ticket_id}/files/"):
                att_url = ""
                att_name = ""
            if not msg and not att_url:
                continue
            # Tiket yang sudah CLOSED terkunci — tolak pesan baru.
            async with pool.acquire() as conn:
                cur = await conn.fetchrow("SELECT status FROM tickets WHERE id = $1", ticket_id)
            if cur and cur["status"] == "CLOSED":
                try:
                    await websocket.send_text(json.dumps(
                        {"type": "error", "message": tr("Tiket sudah ditutup — tidak bisa menambahkan pesan.",
                                                        "The ticket is closed — no more messages can be added.")}))
                except Exception:
                    pass
                continue
            # Simpan ke DB lalu broadcast ke seluruh room (termasuk pengirim)
            try:
                sysmsg = None
                async with pool.acquire() as conn:
                    row = await conn.fetchrow(
                        """INSERT INTO ticket_messages
                           (ticket_id, sender_id, sender_role, sender_name, message,
                            attachment_url, attachment_name)
                           VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING id, created_at""",
                        ticket_id, int(user["sub"]), user.get("role"), user.get("username"),
                        msg, att_url or None, att_name or None)
                    if _is_admin(user) and cur and cur["status"] == "OPEN":
                        await conn.execute(
                            "UPDATE tickets SET updated_at = NOW(), status = 'IN_PROGRESS' WHERE id = $1", ticket_id)
                        sysmsg = await _system_msg(conn, ticket_id, _status_changed("IN_PROGRESS", user))
                    else:
                        await conn.execute("UPDATE tickets SET updated_at = NOW() WHERE id = $1", ticket_id)
                await manager.broadcast(ticket_id, {
                    "type": "message", "id": row["id"], "sender_id": int(user["sub"]),
                    "sender_role": user.get("role"), "sender_name": user.get("username"),
                    "message": msg, "timestamp": row["created_at"].isoformat(),
                    "attachment_url":  att_url  or None,
                    "attachment_name": att_name or None,
                })
                if sysmsg:
                    await manager.broadcast(ticket_id, sysmsg)
                    await manager.broadcast(ticket_id, {"type": "status", "status": "IN_PROGRESS"})
            except Exception as e:
                log.warning("ticket ws save gagal: %s", e)
    except WebSocketDisconnect:
        manager.disconnect(ticket_id, websocket)
    except Exception as e:
        log.debug("ticket ws closed: %s", e)
        manager.disconnect(ticket_id, websocket)
