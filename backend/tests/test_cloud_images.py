"""
Template otomatis dari cloud image (Ubuntu 24.04 dan openSUSE Leap 16.0): katalog, prasyarat node, unduh + impor +
template, pemulihan, dan hak akses. Proxmox tiruan; checksum tidak diambil dari internet.
"""
import time
import uuid

import asyncpg
import pytest

from services import cloud_images as ci
from tests.conftest import DATABASE_URL, _run, auth

I = "/api/v1/images"
OPTS = {"instance": "labImg", "node": "pve", "storage": "local", "disk_storage": "local-lvm", "pool": "lab"}


async def _sql(sql, *args):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


@pytest.fixture(autouse=True)
def _clean(client):
    yield
    _run(_sql("DELETE FROM image_jobs WHERE instance = 'labImg'"))


def _start(client, token, key="ubuntu-2404", **kw):
    return client.post(f"{I}/jobs", headers=auth(token), json={**OPTS, "image_key": key, **kw})


def _wait(client, token, job_id):
    for _ in range(200):
        j = client.get(f"{I}/jobs/{job_id}", headers=auth(token)).json()
        if j["status"] in ci.TERMINAL:
            return j
        time.sleep(0.05)
    raise AssertionError(f"job #{job_id} tidak selesai: {j}")


# ── unit ──────────────────────────────────────────────────────────────────────

def test_catalog_is_small_and_fixed():
    keys = [i["key"] for i in ci.CATALOG]
    assert keys == ["ubuntu-2404", "opensuse-leap-160", "opensuse-leap-161"]
    assert all(i["url"].startswith("https://") and i["checksum_url"].startswith("https://") for i in ci.CATALOG)


def test_parse_checksums_gnu_and_bsd():
    sums = ("aaaa" * 16 + " *noble-server-cloudimg-amd64.img\n" + "bbbb" * 16 + "  jammy-server-cloudimg-amd64.img\n")
    assert ci.parse_checksums(sums, "noble-server-cloudimg-amd64.img", "sha256") == "aaaa" * 16
    assert ci.parse_checksums(sums, "x/jammy-server-cloudimg-amd64.img", "sha256") == "bbbb" * 16
    bsd = "SHA256 (Leap-16.0.qcow2) = " + "c" * 64 + "\n"
    assert ci.parse_checksums(bsd, "Leap-16.0.qcow2", "sha256") == "c" * 64
    assert ci.parse_checksums(sums, "tidak-ada.img", "sha256") is None


def test_volume_filename_and_https_only():
    assert ci.volume_filename(ci.get_image("ubuntu-2404")) == "ccd-ubuntu-2404.qcow2"
    with pytest.raises(ci.ImageError):
        ci._https("http://cloud-images.ubuntu.com/x.img")
    with pytest.raises(ci.ImageError):
        ci._https("https://user:pw@cloud-images.ubuntu.com/x.img")


def test_validate_options():
    img = ci.get_image("ubuntu-2404")
    ok = ci.validate_options(img, {"storage": "local", "disk_storage": "local-lvm", "pool": "lab"})
    assert ok["template_name"] == "tpl-ubuntu-2404" and ok["cores"] == 2 and ok["disk_gb"] == 10 and ok["bridge"] == "vmbr0"
    base = {"storage": "local", "disk_storage": "local-lvm", "pool": "lab"}

    def bad(**kw):
        with pytest.raises(ci.ImageError):
            ci.validate_options(img, {**base, **kw})

    bad(storage="../etc")
    bad(template_name="nama salah!")
    bad(cores=0)
    bad(disk_gb=2)                                          # di bawah minimum image
    bad(bridge="vmbr0; rm -rf")


# ── API ───────────────────────────────────────────────────────────────────────

def test_catalog_and_options_endpoints(client, image_pve, sysadmin_token, student_token):
    assert client.get(f"{I}/catalog", headers=auth(student_token)).status_code == 403
    cat = client.get(f"{I}/catalog", headers=auth(sysadmin_token)).json()
    assert [c["key"] for c in cat] == ["ubuntu-2404", "opensuse-leap-160", "opensuse-leap-161"] and "checksum_url" not in cat[0]
    opt = client.get(f"{I}/options", params={"instance": "labImg", "node": "pve"}, headers=auth(sysadmin_token)).json()
    assert opt["version_ok"] and opt["version"] == "9.1" and opt["pools"] == ["lab"] and opt["bridges"] == ["vmbr0"]
    assert [s["storage"] for s in opt["import_storages"]] == ["local", "local-lvm"]
    image_pve.version_str = "8.1.0"
    old = client.get(f"{I}/options", params={"instance": "labImg", "node": "pve"}, headers=auth(sysadmin_token)).json()
    assert old["version_ok"] is False


def test_happy_path_builds_a_template(client, image_pve, sysadmin_token):
    r = _start(client, sysadmin_token, template_name="tpl-ubuntu", disk_gb=16)
    assert r.status_code == 201, r.text
    job = _wait(client, sysadmin_token, r.json()["id"])
    assert job["status"] == "done", job["error"]
    assert job["result"]["template_vmid"] == job["vmid"] and job["result"]["checksum"].startswith("sha256:")
    assert any("guest agent" in w for w in job["result"]["warnings"])               # Ubuntu tidak membawa agent
    dl = image_pve.downloads[0]
    assert dl["url"].endswith("noble-server-cloudimg-amd64.img") and dl["checksum"] == "a" * 64 and dl["filename"] == "ccd-ubuntu-2404.qcow2"
    vm = image_pve.vms[job["vmid"]]
    cfg = vm["config"]
    assert cfg["template"] == "1" and cfg["ide2"] == "local-lvm:cloudinit" and cfg["agent"] == "1" and cfg["pool"] == "lab"
    assert ("resize", job["vmid"], "16G") in image_pve.calls                          # disk diperbesar sesuai permintaan
    assert vm["name"] == "tpl-ubuntu"


def test_opensuse_161_uses_its_own_image_and_checksum(client, image_pve, sysadmin_token):
    job = _wait(client, sysadmin_token, _start(client, sysadmin_token, key="opensuse-leap-161").json()["id"])
    assert job["status"] == "done" and job["result"]["dhcp_expected"] is True
    dl = image_pve.downloads[0]
    assert "/leap/16.1/" in dl["url"] and dl["url"].endswith("Leap-16.1-Minimal-VM.x86_64-Cloud.qcow2")
    assert dl["filename"] == "ccd-opensuse-leap-161.qcow2"


def test_opensuse_has_no_agent_warning(client, image_pve, sysadmin_token):
    job = _wait(client, sysadmin_token, _start(client, sysadmin_token, key="opensuse-leap-160").json()["id"])
    assert job["status"] == "done" and not job["result"]["warnings"] and job["result"]["dhcp_expected"] is True
    assert image_pve.downloads[0]["url"].endswith("Leap-16.0-Minimal-VM.x86_64-Cloud.qcow2")


def test_preflight_errors_are_clear(client, image_pve, sysadmin_token):
    image_pve.version_str = "8.1.2"
    j = _wait(client, sysadmin_token, _start(client, sysadmin_token).json()["id"])
    assert j["status"] == "failed" and "terlalu lama" in j["error"] and not image_pve.downloads
    image_pve.version_str = "9.1"
    j = _wait(client, sysadmin_token, _start(client, sysadmin_token, storage="tidak-ada").json()["id"])
    assert j["status"] == "failed" and "Import" in j["error"]
    image_pve.privs["/pool/lab"] = {"VM.Allocate": 1}
    j = _wait(client, sysadmin_token, _start(client, sysadmin_token).json()["id"])
    assert j["status"] == "failed" and "VM.Config.Cloudinit" in j["error"] and not image_pve.downloads


def test_failed_import_discards_the_half_built_vm_and_retry_reuses_the_download(client, image_pve, sysadmin_token):
    image_pve.task_fail["import"] = "ERROR: import mislead"
    j = _wait(client, sysadmin_token, _start(client, sysadmin_token).json()["id"])
    assert j["status"] == "failed" and "Impor disk gagal" in j["error"]
    assert j["vmid"] not in image_pve.vms                                           # VM setengah jadi dihapus
    assert len(image_pve.downloads) == 1

    image_pve.task_fail.clear()
    assert client.post(f"{I}/jobs/{j['id']}/retry", headers=auth(sysadmin_token)).status_code == 200
    done = _wait(client, sysadmin_token, j["id"])
    assert done["status"] == "done" and len(image_pve.downloads) == 1               # berkas yang sudah diunduh dipakai ulang
    assert any("dipakai ulang" in w for w in done["result"]["warnings"])
    assert client.post(f"{I}/jobs/{j['id']}/retry", headers=auth(sysadmin_token)).status_code == 409


def test_download_failure_is_reported(client, image_pve, sysadmin_token):
    image_pve.task_fail["download"] = "ERROR: checksum mismatch"
    j = _wait(client, sysadmin_token, _start(client, sysadmin_token).json()["id"])
    assert j["status"] == "failed" and "checksum mismatch" in j["error"]


def test_insufficient_space_stops_before_downloading(client, image_pve, sysadmin_token, monkeypatch):
    async def small(url):
        return 500 << 30
    monkeypatch.setattr(ci, "_head_size", small)
    j = _wait(client, sysadmin_token, _start(client, sysadmin_token).json()["id"])
    assert j["status"] == "failed" and "tidak cukup" in j["error"] and not image_pve.downloads


def test_one_active_job_per_image_and_node(client, image_pve, sysadmin_token):
    _run(_sql("""INSERT INTO image_jobs (instance, node, image_key, image_name, options, status)
                 VALUES ('labImg', 'pve', 'ubuntu-2404', 'Ubuntu', '{}'::jsonb, 'running')"""))
    r = _start(client, sysadmin_token)
    assert r.status_code == 409 and "sedang membuat template" in r.json()["detail"]
    assert _start(client, sysadmin_token, key="opensuse-leap-160").status_code == 201      # image lain boleh


def test_validation_and_unknown_image(client, image_pve, sysadmin_token):
    assert _start(client, sysadmin_token, key="debian-12").status_code == 404
    assert _start(client, sysadmin_token, storage="a b").status_code == 400
    assert _start(client, sysadmin_token, cores=999).status_code == 400


def test_interrupted_job_can_be_retried_and_history_removed(client, image_pve, sysadmin_token):
    jid = _run(_sql("""INSERT INTO image_jobs (instance, node, image_key, image_name, options, status)
                       VALUES ('labImg', 'pve', 'ubuntu-2404', 'Ubuntu', $1::jsonb, 'running') RETURNING id""",
                    '{"storage":"local","disk_storage":"local-lvm","pool":"lab","bridge":"vmbr0","template_name":"tpl-x","cores":2,"memory_mb":2048,"disk_gb":10,"force_download":false}'))[0]["id"]
    client.portal.call(ci.recover_interrupted)
    j = client.get(f"{I}/jobs/{jid}", headers=auth(sysadmin_token)).json()
    assert j["status"] == "interrupted"
    client.post(f"{I}/jobs/{jid}/retry", headers=auth(sysadmin_token))
    assert _wait(client, sysadmin_token, jid)["status"] == "done"
    assert client.delete(f"{I}/jobs/{jid}", headers=auth(sysadmin_token)).status_code == 200
    assert client.get(f"{I}/jobs/{jid}", headers=auth(sysadmin_token)).status_code == 404


def test_only_admins(client, image_pve, student_token):
    assert client.get(f"{I}/options", params={"instance": "x", "node": "y"}, headers=auth(student_token)).status_code == 403
    assert _start(client, student_token).status_code == 403
    assert client.get(f"{I}/jobs", headers=auth(student_token)).status_code == 403
    assert client.get(f"{I}/jobs/1", headers=auth(student_token)).status_code == 403
