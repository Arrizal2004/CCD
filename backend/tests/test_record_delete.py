"""
Hapus tiket Helpdesk dan Infra Request (superadmin): hanya ringkasannya yang tersisa di Audit Trail,
pesan dan lampiran ikut terhapus, dan pencatatan Infra Request yang sebelumnya kosong kini lengkap.
"""
from pathlib import Path

import asyncpg

from tests.conftest import DATABASE_URL, _run, auth

SECRET = "isi-percakapan-rahasia-12345"


async def _sql(sql, *args):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


def _audit(client, token, action, search):
    r = client.get("/api/admin/audit-logs", params={"action": action, "search": search, "page_size": 50}, headers=auth(token))
    return [i["detail"] for i in r.json()["items"]]


def test_delete_ticket_keeps_only_a_summary(client, student_token, sysadmin_token, superadmin_token):
    t = client.post("/api/tickets", headers=auth(student_token),
                    json={"title": "Judul uji hapus tiket", "category": "OTHERS", "description": SECRET}).json()
    tid, number = t["id"], t["ticket_number"]
    assert client.post(f"/api/tickets/{tid}/messages", headers=auth(student_token), json={"message": SECRET}).status_code == 200
    up = client.post(f"/api/tickets/{tid}/upload", headers=auth(student_token),
                     files={"file": ("catatan.txt", b"lampiran uji", "text/plain")})
    assert up.status_code == 200, up.text
    folder = Path("static/uploads/tickets") / str(tid)
    assert folder.is_dir()

    assert client.delete(f"/api/tickets/{tid}", headers=auth(student_token)).status_code == 403
    assert client.delete(f"/api/tickets/{tid}", headers=auth(sysadmin_token)).status_code == 403     # hanya superadmin
    assert client.delete("/api/tickets/99999999", headers=auth(superadmin_token)).status_code == 404

    r = client.delete(f"/api/tickets/{tid}", headers=auth(superadmin_token))
    assert r.status_code == 200 and r.json()["ticket_number"] == number
    assert client.get(f"/api/tickets/{tid}", headers=auth(student_token)).status_code == 404
    assert not folder.exists()                                                                   # lampiran ikut terhapus
    assert not _run(_sql("SELECT 1 FROM ticket_messages WHERE ticket_id = $1", tid))

    # Jejaknya tetap terbaca di Audit Trail: pembuatan, dan ringkasan penghapusan tanpa isi percakapan.
    assert any(number in d for d in _audit(client, superadmin_token, "TICKET_CREATE", number))
    gone = _audit(client, superadmin_token, "TICKET_DELETE", number)
    assert len(gone) == 1
    d = gone[0]
    assert "tst_superadmin menghapus tiket" in d and "'Judul uji hapus tiket'" in d and "tst_student" in d
    assert "status OPEN" in d and "1 pesan" in d and "1 lampiran" in d
    assert SECRET not in d and SECRET not in " ".join(_audit(client, superadmin_token, "", number))


def test_delete_infra_request_is_audited_end_to_end(client, student_token, sysadmin_token, superadmin_token):
    r = client.post("/api/v1/infra-requests", headers=auth(student_token), json={
        "request_type": "VPS", "notes": SECRET, "specs": {"cpu": 2, "ram_gb": 4, "storage_gb": 40, "os": "Ubuntu"}})
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    short = rid[:8]
    _run(_sql("""INSERT INTO infra_request_messages (request_id, sender_id, sender_role, sender_name, message)
                 SELECT $1, id, 'student', username, $2 FROM users WHERE username = 'tst_student'""", rid, SECRET))
    folder = Path("static/uploads/requests") / rid
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "x.txt").write_text("berkas uji")

    # Pembuatan dan perubahan status sekarang tercatat.
    create = _audit(client, superadmin_token, "INFRA_REQUEST_CREATE", short)
    assert create and "tst_student mengajukan" in create[0] and "2 vCPU, 4 GB RAM, 40 GB disk, Ubuntu" in create[0]
    s = client.patch(f"/api/v1/infra-requests/{rid}/status", headers=auth(sysadmin_token),
                     json={"status": "DECLINE", "admin_note": SECRET})
    assert s.status_code == 200, s.text
    assert "→ DECLINE" in _audit(client, superadmin_token, "INFRA_REQUEST_STATUS", short)[0]

    assert client.delete(f"/api/v1/infra-requests/{rid}", headers=auth(student_token)).status_code == 403
    assert client.delete(f"/api/v1/infra-requests/{rid}", headers=auth(sysadmin_token)).status_code == 403
    assert client.delete("/api/v1/infra-requests/bukan-uuid", headers=auth(superadmin_token)).status_code == 404
    assert client.delete(f"/api/v1/infra-requests/{rid}", headers=auth(superadmin_token)).status_code == 200
    assert client.delete(f"/api/v1/infra-requests/{rid}", headers=auth(superadmin_token)).status_code == 404

    assert not _run(_sql("SELECT 1 FROM infrastructure_requests WHERE id = $1", rid))
    assert not _run(_sql("SELECT 1 FROM infra_request_messages WHERE request_id = $1", rid))
    assert not folder.exists()
    d = _audit(client, superadmin_token, "INFRA_REQUEST_DELETE", short)[0]
    assert "tst_superadmin menghapus Infra Request" in d and "milik tst_student" in d and "status DECLINE" in d
    assert "1 pesan" in d and "2 vCPU" in d
    assert SECRET not in " ".join(_audit(client, superadmin_token, "", short))      # tidak ada isi pesan atau catatan admin


def test_remove_dir_refuses_paths_outside_the_base(tmp_path):
    from services.record_purge import remove_dir
    base = tmp_path / "uploads"
    (base / "5").mkdir(parents=True)
    (tmp_path / "rahasia").mkdir()
    assert remove_dir(base, "..") is False and remove_dir(base, "../rahasia") is False
    assert (tmp_path / "rahasia").is_dir() and (base / "5").is_dir()
    assert remove_dir(base, "5") is True and not (base / "5").exists()
    assert remove_dir(base, "5") is False                                            # sudah tidak ada
