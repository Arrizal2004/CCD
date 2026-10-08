"""
GuacamoleSync — sinkronisasi dua arah antara HyperPanel dan Guacamole web app.

Fitur:
  - sync_user(username, password, full_name, role)  → buat/update user di Guacamole
  - delete_user(username)                           → hapus user dari Guacamole
  - sync_vm_connection(host, vm_name, vm_id, creds) → buat/update koneksi SSH/RDP
  - delete_vm_connection(host, vm_id)               → hapus koneksi VM
  - grant_connection(username, conn_id)             → beri akses koneksi ke user
  - revoke_connection(username, conn_id)            → cabut akses
  - sync_vm_assignments(username, vm_ids, host)     → sync seluruh assignment sekaligus
"""
import os
import logging
import asyncio
import httpx
from typing import Any, Callable, Optional

log = logging.getLogger("guac_sync")

# ── Retry helper ──────────────────────────────────────────────────────────────

_RETRY_DELAYS = (5, 30, 90)  # seconds between attempts (3 retries after first try)


async def with_retry(
    fn: Callable,
    *args: Any,
    op_name: str = "",
    max_attempts: int = 4,
    **kwargs: Any,
) -> Any:
    """
    Execute a Guacamole sync function with exponential back-off.

    - max_attempts=4 → 1 immediate try + 3 retries at 5s / 30s / 90s
    - Returns False/raises after all attempts are exhausted.
    - False return from the wrapped function is treated as a soft failure and retried.
    - CancelledError propagates immediately (respects task cancellation).
    """
    label = op_name or getattr(fn, "__name__", repr(fn))
    last_exc: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            result = await fn(*args, **kwargs)
            if result is False:
                raise ValueError("returned False (Guacamole rejected or unreachable)")
            if attempt > 1:
                log.info("[retry] %s recovered on attempt %d", label, attempt)
            return result
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            last_exc = exc
            if attempt >= max_attempts:
                break
            delay = _RETRY_DELAYS[min(attempt - 1, len(_RETRY_DELAYS) - 1)]
            log.warning(
                "[retry] %s attempt %d/%d failed (%s), next in %ds",
                label, attempt, max_attempts, exc, delay,
            )
            await asyncio.sleep(delay)

    log.error("[retry] %s permanently failed after %d attempts: %s", label, max_attempts, last_exc)
    return False

GUAC_URL       = os.getenv("GUAC_URL",        "http://guacamole:8080/guacamole")
GUAC_ADMIN     = os.getenv("GUAC_ADMIN_USER", "guacadmin")
GUAC_ADMIN_PASS= os.getenv("GUAC_ADMIN_PASS", "guacadmin")
GUAC_DS        = os.getenv("GUAC_DS",         "postgresql")

# Cache token di memori (per-process)
_token_cache: dict = {"token": "", "expires": 0.0}


# ── Token Management ──────────────────────────────────────────────────────

async def _get_token() -> str:
    import time
    if _token_cache["token"] and time.time() < _token_cache["expires"]:
        return _token_cache["token"]
    return await _refresh_token()


_last_heal = 0.0  # kapan terakhir self-heal dicoba (rate limit)


async def _refresh_token() -> str:
    """Login admin Guacamole. Kalau gagal padahal GUAC_ADMIN_PASS bukan default, kemungkinan password
    admin Guacamole belum dirotasi (mis. backend start lebih dulu dari Guacamole) — coba rotasi lagi
    (maks 1x/30 detik) supaya Connect langsung pulih tanpa perlu restart backend."""
    global _last_heal
    import time
    token = await _login_admin()
    if token or GUAC_ADMIN_PASS == _DEFAULT_ADMIN_PASS or time.time() - _last_heal < 30:
        return token
    _last_heal = time.time()
    try:
        await ensure_admin_password()
    except Exception as e:
        log.warning("Self-heal password admin Guacamole gagal: %s", e)
        return ""
    return await _login_admin()


async def _login_admin() -> str:
    import time
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{GUAC_URL}/api/tokens",
                data={"username": GUAC_ADMIN, "password": GUAC_ADMIN_PASS},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if resp.status_code == 200:
            data = resp.json()
            token = data.get("authToken", "")
            # Token Guacamole valid ~30 menit, cache 25 menit
            _token_cache["token"]   = token
            _token_cache["expires"] = time.time() + 1500
            return token
        log.warning("Guacamole login failed: %s %s", resp.status_code, resp.text[:200])
    except Exception as e:
        log.warning("Cannot reach Guacamole: %s", e)
    return ""


_DEFAULT_ADMIN_PASS = "guacadmin"


async def ensure_admin_password() -> None:
    """A fresh Guacamole DB ships guacadmin/guacadmin. When GUAC_ADMIN_PASS (from .env) is set to
    something else and doesn't work yet while the default still does, rotate it — so no deployment
    keeps the publicly known default on an admin account reachable through /guacamole."""
    if GUAC_ADMIN_PASS == _DEFAULT_ADMIN_PASS:
        log.warning("GUAC_ADMIN_PASS is still Guacamole's default — set a strong value in .env")
        return
    if await _login_admin():
        return
    token = (await get_user_token(GUAC_ADMIN, _DEFAULT_ADMIN_PASS)).get("authToken")
    if not token:
        # Guacamole belum siap (masih start) atau kedua password salah. JANGAN return diam-diam:
        # sebelumnya ini dianggap sukses oleh retry di startup sehingga rotasi tidak pernah dicoba
        # lagi → password admin tetap default, login backend gagal, Connect rusak sampai restart.
        raise RuntimeError("Guacamole admin login failed with both GUAC_ADMIN_PASS and the default password")
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.put(
            f"{GUAC_URL}/api/session/data/{GUAC_DS}/users/{GUAC_ADMIN}/password?token={token}",
            json={"oldPassword": _DEFAULT_ADMIN_PASS, "newPassword": GUAC_ADMIN_PASS},
        )
    if resp.status_code >= 300:
        raise RuntimeError(f"Guacamole admin password rotation failed: HTTP {resp.status_code}")
    log.warning("Rotated Guacamole admin password from the default to GUAC_ADMIN_PASS")


async def get_user_token(username: str, password: str, client_ip: str = "") -> dict:
    """
    Authenticate user ke Guacamole, return full response dict.
    Keys: authToken, dataSource, username, availableDataSources.
    Return {} jika gagal.
    client_ip: IP pengguna yang login ke dashboard. Guacamole mencatat IP pembuat token sebagai IP
    klien setiap sesi Remote; tanpa header ini yang tercatat adalah IP container backend. Guacamole
    menerima X-Forwarded-For dari jaringan Docker lewat RemoteIpValve (docker-compose.yml).
    """
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if client_ip:
        headers["X-Forwarded-For"] = client_ip
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{GUAC_URL}/api/tokens",
                data={"username": username, "password": password},
                headers=headers,
            )
        if resp.status_code == 200:
            return resp.json()
        log.warning("Guacamole user login failed for '%s': %s", username, resp.status_code)
    except Exception as e:
        log.warning("Cannot get Guacamole token for '%s': %s", username, e)
    return {}


async def _fetch(method: str, path: str, json_data=None, _retry=True) -> tuple[dict, int]:
    token = await _get_token()
    if not token:
        return {"error": "Guacamole tidak tersedia"}, 503

    url = f"{GUAC_URL}/api{path}"
    sep = "&" if "?" in url else "?"
    url += f"{sep}token={token}"

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.request(
                method=method, url=url,
                json=json_data,
                headers={"Content-Type": "application/json"},
            )
        if resp.status_code in (401, 403) and _retry:
            _token_cache["expires"] = 0.0   # force refresh
            await _refresh_token()
            return await _fetch(method, path, json_data, _retry=False)
        if resp.status_code == 204:
            return {"ok": True}, 204
        try:
            return resp.json(), resp.status_code
        except Exception:
            return {"raw": resp.text}, resp.status_code
    except Exception as e:
        log.error("Guacamole API error %s %s: %s", method, path, e)
        return {"error": str(e)}, 503


# ── User Sync ─────────────────────────────────────────────────────────────

async def sync_user(username: str, password: str, full_name: str = "", disabled: bool = False) -> bool:
    """Buat atau update user di Guacamole."""
    payload = {
        "username": username,
        "password": password,
        "attributes": {
            "disabled": "true" if disabled else "",
            "expired": "",
            "access-window-start": "",
            "access-window-end": "",
            "valid-from": "",
            "valid-until": "",
            "timezone": None,
            "guac-full-name": full_name,
            "guac-email-address": "",
            "guac-organization": "HyperPanel",
        },
    }

    # Cek apakah sudah ada
    existing, status = await _fetch("GET", f"/session/data/{GUAC_DS}/users/{username}")
    if status == 200:
        # Update existing
        _, s = await _fetch("PUT", f"/session/data/{GUAC_DS}/users/{username}", payload)
        # Update password terpisah
        await _fetch("PUT", f"/session/data/{GUAC_DS}/users/{username}/password",
                     {"oldPassword": "", "newPassword": password})
        log.info("Guacamole user updated: %s", username)
        return s < 400
    else:
        # Buat baru
        data, s = await _fetch("POST", f"/session/data/{GUAC_DS}/users", payload)
        if s < 300:
            log.info("Guacamole user created: %s", username)
        else:
            log.warning("Guacamole create user failed: %s → %s", username, data)
        return s < 300


async def sync_user_disabled(username: str, disabled: bool) -> bool:
    """Update hanya status disabled Guacamole user — tanpa mengubah password.

    Dipanggil saat admin toggle is_active tanpa ganti password, sehingga
    password di Guacamole tidak ikut berubah.
    """
    existing, status = await _fetch("GET", f"/session/data/{GUAC_DS}/users/{username}")
    if status != 200:
        log.warning("Guacamole sync_user_disabled: user %s tidak ditemukan (status %s)", username, status)
        return False

    # Ambil attribute yang ada, update hanya 'disabled'
    attrs = existing.get("attributes", {})
    attrs["disabled"] = "true" if disabled else ""
    payload = {**existing, "attributes": attrs}

    _, s = await _fetch("PUT", f"/session/data/{GUAC_DS}/users/{username}", payload)
    log.info("Guacamole user %s disabled=%s → status %s", username, disabled, s)
    return s < 400


async def delete_user(username: str) -> bool:
    """Hapus user dari Guacamole."""
    _, s = await _fetch("DELETE", f"/session/data/{GUAC_DS}/users/{username}")
    log.info("Guacamole user deleted: %s (status %s)", username, s)
    return s in (200, 204, 404)


# ── Connection Sync ───────────────────────────────────────────────────────

def _conn_name(host_name: str, vm_name: str) -> str:
    return f"HV/{host_name}/{vm_name}"


async def _find_connection_id(name: str) -> Optional[str]:
    data, s = await _fetch("GET", f"/session/data/{GUAC_DS}/connections")
    if s != 200 or not isinstance(data, dict):
        return None
    for cid, c in data.items():
        if isinstance(c, dict) and c.get("name") == name:
            return c.get("identifier")
    return None


def _build_ssh_params(creds: dict) -> dict:
    # terminal-type 'xterm' (bukan xterm-256color) → terminfo ini hampir selalu ada
    # bahkan di Ubuntu minimal, mencegah 'Unable to associate shell with PTY'.
    # locale C.UTF-8 = fallback aman saat guest belum punya locale lengkap.
    return {
        "hostname":           creds.get("ssh_host", ""),
        "port":               str(creds.get("ssh_port") or 22),
        "username":           creds.get("username", ""),
        "password":           creds.get("password", ""),
        "private-key":        creds.get("pkey", ""),
        "color-scheme":       "green-black",
        "font-name":          "monospace",
        "font-size":          "14",
        "scrollback":         "5000",
        "terminal-type":      "xterm",
        "locale":             "C.UTF-8",
        "server-alive-interval": "15",
        "timezone":           "Asia/Jakarta",
    }


def _build_rdp_params(creds: dict) -> dict:
    return {
        "hostname":        creds.get("ssh_host", ""),
        "port":            str(creds.get("ssh_port") or 3389),
        "username":        creds.get("username", ""),
        "password":        creds.get("password", ""),
        "security":        "any",
        "ignore-cert":     "true",
        "resize-method":   "display-update",
        "color-depth":     "16",
        "enable-drive":    "false",
        "disable-audio":   "true",
        "enable-font-smoothing": "true",
    }


async def sync_vm_connection(
    host_name: str,
    vm_name: str,
    vm_id: str,
    creds: dict,
) -> Optional[str]:
    """
    Buat atau update koneksi Guacamole untuk VM ini.
    Return connection_id jika berhasil, None jika gagal.
    creds dict: {os_type, guac_protocol, ssh_host, ssh_port, username, password, pkey}

    Protokol Guacamole dipilih eksplisit via creds['guac_protocol'] ('ssh'|'rdp').
    Jika kosong, fallback otomatis berdasarkan os_type (linux→ssh, windows→rdp).
    """
    protocol = (creds.get("guac_protocol") or "").lower()
    if protocol not in ("ssh", "rdp"):
        protocol = "ssh" if creds.get("os_type") == "linux" else "rdp"

    # Sesuaikan port default bila masih memakai default protokol lain
    port = creds.get("ssh_port")
    if protocol == "rdp" and (not port or int(port) == 22):
        creds = {**creds, "ssh_port": 3389}
    elif protocol == "ssh" and (not port or int(port) == 3389):
        creds = {**creds, "ssh_port": 22}

    params   = _build_ssh_params(creds) if protocol == "ssh" else _build_rdp_params(creds)
    name     = _conn_name(host_name, vm_name)

    body = {
        "name":             name,
        "parentIdentifier": "ROOT",
        "protocol":         protocol,
        "parameters":       params,
        "attributes": {
            "max-connections":          "10",
            "max-connections-per-user": "2",
            "guacd-hostname":           "",
            "guacd-port":               "",
            "guacd-encryption":         "",
        },
    }

    existing_id = await _find_connection_id(name)
    if existing_id:
        # Try PUT to preserve connection ID (keeps existing user permissions intact)
        _, s = await _fetch("PUT", f"/session/data/{GUAC_DS}/connections/{existing_id}", body)
        if s < 300:
            log.info("Guacamole connection updated: %s (id=%s protocol=%s)", name, existing_id, protocol)
            return existing_id
        log.warning("Guacamole PUT failed (s=%s), falling back to delete+create for %s", s, name)
        await _fetch("DELETE", f"/session/data/{GUAC_DS}/connections/{existing_id}")

    data, s = await _fetch("POST", f"/session/data/{GUAC_DS}/connections", body)
    if s < 300 and isinstance(data, dict):
        conn_id = data.get("identifier")
        log.info("Guacamole connection created: %s (id=%s protocol=%s)", name, conn_id, protocol)
        return conn_id
    log.warning("Guacamole connection sync failed: %s → %s", name, data)
    return None


async def delete_vm_connection(host_name: str, vm_name: str) -> bool:
    """Hapus koneksi Guacamole untuk VM ini."""
    name = _conn_name(host_name, vm_name)
    cid  = await _find_connection_id(name)
    if cid:
        _, s = await _fetch("DELETE", f"/session/data/{GUAC_DS}/connections/{cid}")
        log.info("Guacamole connection deleted: %s (id=%s)", name, cid)
        return s in (200, 204)
    return True  # sudah tidak ada


# ── Permission Management ─────────────────────────────────────────────────

async def grant_connection(username: str, conn_id: str) -> bool:
    """Beri akses READ ke koneksi untuk user tertentu."""
    patch = [{"op": "add", "path": f"/connectionPermissions/{conn_id}", "value": "READ"}]
    _, s = await _fetch("PATCH", f"/session/data/{GUAC_DS}/users/{username}/permissions", patch)
    return s < 300


async def revoke_connection(username: str, conn_id: str) -> bool:
    """Cabut akses koneksi dari user."""
    patch = [{"op": "remove", "path": f"/connectionPermissions/{conn_id}", "value": "READ"}]
    _, s = await _fetch("PATCH", f"/session/data/{GUAC_DS}/users/{username}/permissions", patch)
    return s < 300


async def get_user_connections(username: str) -> list[str]:
    """Return list conn_id yang bisa diakses user ini."""
    data, s = await _fetch("GET", f"/session/data/{GUAC_DS}/users/{username}/permissions")
    if s != 200 or not isinstance(data, dict):
        return []
    return list((data.get("connectionPermissions") or {}).keys())


async def sync_vm_assignments(
    username: str,
    host_name: str,
    assigned_vm_names: list[str],
) -> None:
    """
    Sync seluruh assignment VM untuk satu user:
    - Grant koneksi yang ada di assigned_vm_names
    - Revoke koneksi yang tidak lagi di-assign

    Dipanggil saat user assignment berubah.
    """
    # Dapatkan semua koneksi
    all_conns, s = await _fetch("GET", f"/session/data/{GUAC_DS}/connections")
    if s != 200 or not isinstance(all_conns, dict):
        return

    # Map name → id untuk VM yang di-assign
    target_ids: set[str] = set()
    for cid, c in all_conns.items():
        if not isinstance(c, dict): continue
        conn_name = c.get("name", "")
        if not conn_name.startswith(f"HV/{host_name}/"): continue
        vm_nm = conn_name.split("/", 2)[-1]
        if vm_nm in assigned_vm_names:
            target_ids.add(c.get("identifier", cid))

    # Koneksi yang saat ini dimiliki user
    current_ids = set(await get_user_connections(username))

    # Grant yang baru
    for cid in target_ids - current_ids:
        await grant_connection(username, cid)

    # Revoke yang dihapus — scope ke host ini saja agar tidak cabut koneksi host lain
    hv_ids = {c.get("identifier", k) for k, c in all_conns.items()
               if isinstance(c, dict) and c.get("name", "").startswith(f"HV/{host_name}/")}
    for cid in (current_ids & hv_ids) - target_ids:
        await revoke_connection(username, cid)

    log.info("Guacamole assignment synced: %s → %d connections", username, len(target_ids))


async def _connection_info() -> dict:
    """Map connectionIdentifier → (name, protocol) untuk menampilkan VM tujuan sesi aktif."""
    data, s = await _fetch("GET", f"/session/data/{GUAC_DS}/connections")
    if s != 200 or not isinstance(data, dict):
        return {}
    return {cid: (c.get("name", ""), c.get("protocol", "")) for cid, c in data.items() if isinstance(c, dict)}


async def get_active_sessions() -> list[dict]:
    """Daftar sesi remote aktif saat ini di Guacamole."""
    data, s = await _fetch("GET", f"/session/data/{GUAC_DS}/activeConnections")
    if s != 200 or not isinstance(data, dict):
        return []
    from services.remote_history import split_name, _own_ips
    info = await _connection_info()
    out = []
    for active_id, a in data.items():
        if not isinstance(a, dict):
            continue
        conn_id = a.get("connectionIdentifier", "")
        name, protocol = info.get(conn_id, (conn_id, ""))
        # name: HV/{host}/{vm} atau HV/{host}/{vm}@{akun-os}
        host, vm, os_user = split_name(name)
        ip = a.get("remoteHost", "")
        out.append({
            "active_id":   active_id,
            "username":    a.get("username", ""),
            "remote_host": "" if ip in _own_ips() else ip,
            "start_date":  a.get("startDate"),
            "connection":  name,
            "host":        host,
            "vm":          vm,
            "os_account":  os_user,
            "protocol":    (protocol or "").upper(),
        })
    return out


async def kill_session(active_id: str) -> bool:
    """Putuskan paksa satu sesi aktif Guacamole berdasarkan activeConnection id."""
    patch = [{"op": "remove", "path": f"/{active_id}"}]
    _, s = await _fetch("PATCH", f"/session/data/{GUAC_DS}/activeConnections", patch)
    log.info("Guacamole kill session %s → %s", active_id, s)
    return s < 300


def _conn_name_os(host_name: str, vm_name: str, os_username: str) -> str:
    return f"HV/{host_name}/{vm_name}@{os_username}"


async def sync_os_account_connection(
    host_name: str, vm_name: str, os_username: str, creds: dict
) -> Optional[str]:
    """Buat atau update Guacamole connection untuk OS account spesifik di VM."""
    protocol = (creds.get("guac_protocol") or "").lower()
    if protocol not in ("ssh", "rdp"):
        protocol = "ssh" if creds.get("os_type") == "linux" else "rdp"

    port = creds.get("ssh_port")
    if protocol == "rdp" and (not port or int(port) == 22):
        creds = {**creds, "ssh_port": 3389}
    elif protocol == "ssh" and (not port or int(port) == 3389):
        creds = {**creds, "ssh_port": 22}

    params = _build_ssh_params(creds) if protocol == "ssh" else _build_rdp_params(creds)
    name = _conn_name_os(host_name, vm_name, os_username)

    body = {
        "name":             name,
        "parentIdentifier": "ROOT",
        "protocol":         protocol,
        "parameters":       params,
        "attributes": {
            "max-connections":          "5",
            "max-connections-per-user": "1",
            "guacd-hostname": "", "guacd-port": "", "guacd-encryption": "",
        },
    }

    existing_id = await _find_connection_id(name)
    if existing_id:
        _, s = await _fetch("PUT", f"/session/data/{GUAC_DS}/connections/{existing_id}", body)
        if s < 300:
            log.info("Guacamole OS account connection updated: %s (id=%s)", name, existing_id)
            return existing_id
        await _fetch("DELETE", f"/session/data/{GUAC_DS}/connections/{existing_id}")

    data, s = await _fetch("POST", f"/session/data/{GUAC_DS}/connections", body)
    if s < 300 and isinstance(data, dict):
        conn_id = data.get("identifier")
        log.info("Guacamole OS account connection created: %s (id=%s)", name, conn_id)
        return conn_id
    log.warning("Guacamole OS account connection sync failed: %s → %s", name, data)
    return None


async def delete_os_account_connection(
    host_name: str, vm_name: str, os_username: str
) -> bool:
    """Hapus Guacamole connection untuk OS account spesifik."""
    name = _conn_name_os(host_name, vm_name, os_username)
    cid = await _find_connection_id(name)
    if cid:
        _, s = await _fetch("DELETE", f"/session/data/{GUAC_DS}/connections/{cid}")
        log.info("Guacamole OS account connection deleted: %s (id=%s)", name, cid)
        return s in (200, 204)
    return True


async def get_connection_url_for_os_account(
    host_name: str, vm_name: str, os_username: str, guac_public_url: str = ""
) -> Optional[str]:
    """Return URL Guacamole untuk OS account spesifik (HV/host/vm@username)."""
    import base64
    name = _conn_name_os(host_name, vm_name, os_username)
    cid = await _find_connection_id(name)
    if not cid:
        return None
    base = guac_public_url or GUAC_URL
    client_id = base64.b64encode(f"{cid}\0c\0{GUAC_DS}".encode()).decode()
    return f"{base}/#/client/{client_id}"


# ── Group / Mandiri connections ───────────────────────────────────────────────

def _conn_name_mandiri(host_name: str, vm_name: str) -> str:
    return f"HV/{host_name}/{vm_name}@mandiri"


def _conn_name_group(host_name: str, vm_name: str, group_id: int) -> str:
    return f"HV/{host_name}/{vm_name}@grp_{group_id}"


async def _sync_named_connection(name: str, creds: dict) -> Optional[str]:
    """Buat atau update Guacamole connection dengan nama custom."""
    protocol = (creds.get("guac_protocol") or "").lower()
    if protocol not in ("ssh", "rdp"):
        protocol = "ssh" if creds.get("os_type") == "linux" else "rdp"

    port = creds.get("ssh_port")
    if protocol == "rdp" and (not port or int(port) == 22):
        creds = {**creds, "ssh_port": 3389}
    elif protocol == "ssh" and (not port or int(port) == 3389):
        creds = {**creds, "ssh_port": 22}

    params = _build_ssh_params(creds) if protocol == "ssh" else _build_rdp_params(creds)
    body = {
        "name":             name,
        "parentIdentifier": "ROOT",
        "protocol":         protocol,
        "parameters":       params,
        "attributes": {
            "max-connections":          "5",
            "max-connections-per-user": "1",
            "guacd-hostname": "", "guacd-port": "", "guacd-encryption": "",
        },
    }
    existing_id = await _find_connection_id(name)
    if existing_id:
        _, s = await _fetch("PUT", f"/session/data/{GUAC_DS}/connections/{existing_id}", body)
        if s < 300:
            return existing_id
        await _fetch("DELETE", f"/session/data/{GUAC_DS}/connections/{existing_id}")

    data, s = await _fetch("POST", f"/session/data/{GUAC_DS}/connections", body)
    if s < 300 and isinstance(data, dict):
        conn_id = data.get("identifier")
        log.info("Guacamole connection created: %s (id=%s)", name, conn_id)
        return conn_id
    log.warning("Guacamole connection sync failed: %s → %s", name, data)
    return None


async def sync_mandiri_connection(host_name: str, vm_name: str, vm_creds: dict) -> Optional[str]:
    """Koneksi tanpa pre-fill credentials — student isi sendiri di Guacamole."""
    creds = {**vm_creds, "username": "", "password": "", "pkey": ""}
    return await _sync_named_connection(_conn_name_mandiri(host_name, vm_name), creds)


async def sync_group_connection(
    host_name: str, vm_name: str, group_id: int, creds: dict
) -> Optional[str]:
    """Koneksi dengan credentials level grup."""
    return await _sync_named_connection(_conn_name_group(host_name, vm_name, group_id), creds)


async def get_connection_url_by_name(name: str, guac_public_url: str = "") -> Optional[str]:
    """Return URL Guacamole untuk connection dengan nama tertentu."""
    import base64
    cid = await _find_connection_id(name)
    if not cid:
        return None
    base = guac_public_url or GUAC_URL
    client_id = base64.b64encode(f"{cid}\0c\0{GUAC_DS}".encode()).decode()
    return f"{base}/#/client/{client_id}"


def proxmox_host_connection_name(label: str) -> str:
    """Nama koneksi SSH ke host Proxmox. Segmen host-nya label instance (tanpa '__node'), jadi tidak
    pernah cocok dengan awalan 'HV/{instance}__{node}/' milik sinkronisasi VM mahasiswa."""
    return f"HV/{label}/PROXMOX-HOST"


def host_only(address: str) -> str:
    """'192.168.1.10:8006', 'https://pve.contoh.id:8006', atau '[fd00::1]:8006' -> alamat tanpa skema dan port."""
    addr = (address or "").strip()
    addr = addr.split("://", 1)[-1].split("/", 1)[0]
    if addr.startswith("["):
        return addr[1:].split("]", 1)[0]
    host, sep, port = addr.rpartition(":")
    return host if sep and port.isdigit() and ":" not in host else addr


def build_host_ssh_params(hostname: str, port: int = 22, timezone: str = "Asia/Jakarta") -> dict:
    """Parameter koneksi SSH ke host Proxmox. username, password, dan private-key SENGAJA tidak ada:
    Guacamole meminta kredensial itu ke pengguna setiap kali tersambung, jadi tidak ada yang tersimpan."""
    return {
        "hostname": hostname, "port": str(port),
        "color-scheme": "green-black", "font-name": "monospace", "font-size": "14", "scrollback": "5000",
        "terminal-type": "xterm", "locale": "C.UTF-8", "server-alive-interval": "15", "timezone": timezone,
    }


async def sync_proxmox_host_connection(label: str, hostname: str, port: int = 22, timezone: str = "Asia/Jakarta") -> Optional[str]:
    """Buat atau perbarui koneksi SSH ke host Proxmox `label` (tanpa kredensial tersimpan)."""
    name = proxmox_host_connection_name(label)
    body = {
        "name": name, "parentIdentifier": "ROOT", "protocol": "ssh",
        "parameters": build_host_ssh_params(hostname, port, timezone),
        "attributes": {"max-connections": "5", "max-connections-per-user": "2",
                       "guacd-hostname": "", "guacd-port": "", "guacd-encryption": ""},
    }
    existing_id = await _find_connection_id(name)
    if existing_id:
        _, s = await _fetch("PUT", f"/session/data/{GUAC_DS}/connections/{existing_id}", body)
        if s < 300:
            return existing_id
        await _fetch("DELETE", f"/session/data/{GUAC_DS}/connections/{existing_id}")
    data, s = await _fetch("POST", f"/session/data/{GUAC_DS}/connections", body)
    if s < 300 and isinstance(data, dict):
        return data.get("identifier")
    log.warning("Guacamole: koneksi SSH ke host Proxmox %s gagal dibuat: %s", label, data)
    return None


async def delete_named_connection(name: str) -> bool:
    cid = await _find_connection_id(name)
    if cid:
        _, s = await _fetch("DELETE", f"/session/data/{GUAC_DS}/connections/{cid}")
        return s in (200, 204)
    return True


ADMIN_ROLES = {"superadmin", "sysadmin"}


async def grant_all_connections_to_admin(username: str) -> None:
    """Grant READ akses ke semua koneksi Guacamole untuk user admin/sysadmin/superadmin."""
    all_conns, s = await _fetch("GET", f"/session/data/{GUAC_DS}/connections")
    if s != 200 or not isinstance(all_conns, dict):
        log.warning("grant_all_connections_to_admin: gagal fetch connections untuk %s", username)
        return
    current_ids = set(await get_user_connections(username))
    granted = 0
    for cid, c in all_conns.items():
        if not isinstance(c, dict):
            continue
        conn_id = c.get("identifier", cid)
        if conn_id not in current_ids:
            if await grant_connection(username, conn_id):
                granted += 1
    log.info("Guacamole: granted %d connections to admin user %s", granted, username)


async def grant_vm_to_all_admins(conn_id: str) -> None:
    """Saat VM connection baru dibuat, grant ke semua user admin/sysadmin/superadmin."""
    try:
        from database import get_pool
        pool = await get_pool()
        async with pool.acquire() as db:
            rows = await db.fetch(
                "SELECT username FROM users WHERE role = ANY($1) AND is_active = true",
                list(ADMIN_ROLES),
            )
    except Exception as e:
        log.warning("grant_vm_to_all_admins: DB error: %s", e)
        return
    for row in rows:
        await grant_connection(row["username"], conn_id)
    log.info("Guacamole: granted connection %s to %d admin users", conn_id, len(rows))


async def get_connection_history(limit: int = 100) -> list[dict]:
    """History koneksi remote (lampau) — username, protokol, VM, durasi."""
    data, s = await _fetch(
        "GET",
        f"/session/data/{GUAC_DS}/history/connections?order=-startDate&limit={limit}",
    )
    if s != 200 or not isinstance(data, list):
        return []
    out = []
    for h in data:
        if not isinstance(h, dict):
            continue
        name = h.get("connectionName", "")
        parts = name.split("/", 2) if name else []
        start = h.get("startDate")
        end = h.get("endDate")
        duration = None
        if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end >= start:
            duration = round((end - start) / 1000)  # detik
        # Protokol: tebak dari nama/prefix; Guacamole history tidak selalu menyimpan protocol
        proto = ""
        out.append({
            "username":   h.get("username", ""),
            "connection": name,
            "host":       parts[1] if len(parts) >= 3 else "",
            "vm":         parts[2] if len(parts) >= 3 else name,
            "protocol":   proto,
            "start_date": start,
            "end_date":   end,
            "active":     bool(h.get("active")),
            "duration_s": duration,
        })
    return out


async def get_connection_url(host_name: str, vm_name: str, guac_public_url: str = "") -> Optional[str]:
    """Return URL Guacamole untuk buka koneksi VM langsung di browser."""
    name = _conn_name(host_name, vm_name)
    cid  = await _find_connection_id(name)
    if not cid:
        return None
    base = guac_public_url or GUAC_URL
    # Guacamole client URL format: /#/client/<base64(id\0c\0postgresql)>
    import base64
    client_id = base64.b64encode(f"{cid}\0c\0{GUAC_DS}".encode()).decode()
    return f"{base}/#/client/{client_id}"


# ── Startup housekeeping ───────────────────────────────────────────────────────

async def ensure_admin_password_on_startup():
    """Retry ensure_admin_password() with back-off — Guacamole may still be starting up when this
    backend does. Runs once at boot as a background task (see main.py lifespan). Coba sampai ~10 menit
    (mesin baru: pull image + init DB bisa lambat); setelah itu _refresh_token() masih self-heal."""
    attempts = 40
    for attempt in range(1, attempts + 1):
        try:
            await ensure_admin_password()
            return
        except Exception as e:
            log.warning("Admin password rotation attempt %d/%d failed: %s", attempt, attempts, e)
            await asyncio.sleep(15)
    log.error("Admin password rotation gave up after %d attempts — Guacamole admin login will self-heal on demand", attempts)
