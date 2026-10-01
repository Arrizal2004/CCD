"""
Dashboard-managed slice of the Tailscale network policy — the network-layer half of RBAC.

Students reach VMs clientless (browser → Guacamole) and never join the tailnet, so what the
tailnet has to enforce is the perimeter around the gateway host (dashboard + Guacamole):
  - tag:ccd-gateway  — gateway device(s); the tag is owned by autogroup:admin
  - group:ccd-admins — generated from active dashboard superadmin/sysadmin users that have a
                       Tailscale login mapped → full access to the gateway
  - autogroup:member — any other tailnet member → dashboard web port(s) only
Anything referencing tag:ccd-*/group:ccd-* is owned by the dashboard and regenerated; the rest
of the policy is left untouched. Admins are matched by login instead of by tagging their
laptops, because tagging turns a device tag-owned and drops its user identity. Removing the
default allow-all rule (which otherwise makes all of this moot) is an explicit opt-in per apply.
"""
import copy
import json
import re

from database import get_pool
from services import tailscale_client as ts

GATEWAY_TAG = "tag:ccd-gateway"
ADMIN_GROUP = "group:ccd-admins"
_MANAGED_PREFIXES = ("tag:ccd-", "group:ccd-")
SELF_GRANT = {"src": ["autogroup:member"], "dst": ["autogroup:self"], "ip": ["*"]}
DEFAULT_MEMBER_PORTS = [80]
LOGIN_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+$")
_ADMIN_ROLES = ("superadmin", "sysadmin")


class PolicyChanged(Exception):
    """The tailnet policy was modified after the preview the admin approved."""


class PolicyNotInstalled(Exception):
    """tag:ccd-gateway has no tagOwners yet, so Tailscale would refuse to apply the tag."""


def _refs_managed(values) -> bool:
    return any(isinstance(v, str) and v.startswith(_MANAGED_PREFIXES) for v in values or [])


def _is_managed_grant(g) -> bool:
    return isinstance(g, dict) and (_refs_managed(g.get("src")) or _refs_managed(g.get("dst")))


def _is_allow_all_grant(g) -> bool:
    return (isinstance(g, dict) and set(g) <= {"src", "dst", "ip"}
            and g.get("src") == ["*"] and g.get("dst") == ["*"] and g.get("ip", ["*"]) == ["*"])


def _is_allow_all_acl(a) -> bool:
    return (isinstance(a, dict) and a.get("action") == "accept"
            and a.get("src") == ["*"] and a.get("dst") == ["*:*"])


def allow_all_active(policy: dict) -> bool:
    return (any(_is_allow_all_grant(g) for g in policy.get("grants", []))
            or any(_is_allow_all_acl(a) for a in policy.get("acls", [])))


def current_member_ports(policy: dict) -> list[int]:
    for g in policy.get("grants", []):
        if _is_managed_grant(g) and g.get("src") == ["autogroup:member"]:
            return sorted(int(p[4:]) for p in g.get("ip", []) if re.fullmatch(r"tcp:\d+", p))
    return []


def build_policy(current: dict, admin_logins: list[str], member_ports: list[int],
                 remove_allow_all: bool) -> tuple[dict, list[str]]:
    """Pure: returns (proposed policy, human-readable changes). Idempotent for the same inputs."""
    policy = copy.deepcopy(current)
    changes: list[str] = []

    owners = policy.setdefault("tagOwners", {})
    if owners.get(GATEWAY_TAG) != ["autogroup:admin"]:
        owners[GATEWAY_TAG] = ["autogroup:admin"]
        changes.append(f"tagOwners: {GATEWAY_TAG} boleh dipasang oleh admin tailnet")

    logins = sorted(set(admin_logins))
    groups = policy.setdefault("groups", {})
    if logins and groups.get(ADMIN_GROUP) != logins:
        groups[ADMIN_GROUP] = logins
        changes.append(f"{ADMIN_GROUP} = {', '.join(logins)}")
    elif not logins and ADMIN_GROUP in groups:
        del groups[ADMIN_GROUP]
        changes.append(f"{ADMIN_GROUP} dihapus (tidak ada admin dashboard yang dipetakan)")
    if not groups:
        del policy["groups"]

    old_grants = policy.get("grants", [])
    grants = [g for g in old_grants if not _is_managed_grant(g)]

    if remove_allow_all:
        before = len(grants)
        grants = [g for g in grants if not _is_allow_all_grant(g)]
        removed = before - len(grants)
        if "acls" in policy:
            acls = [a for a in policy["acls"] if not _is_allow_all_acl(a)]
            removed += len(policy["acls"]) - len(acls)
            policy["acls"] = acls
        if removed:
            changes.append(f"Aturan allow-all dihapus ({removed})")
        if SELF_GRANT not in grants:
            grants.append(SELF_GRANT)
            changes.append("Grant autogroup:member → autogroup:self ditambahkan (akses ke device milik sendiri)")

    managed = []
    if logins:
        managed.append({"src": [ADMIN_GROUP], "dst": [GATEWAY_TAG], "ip": ["*"]})
    if member_ports:
        managed.append({"src": ["autogroup:member"], "dst": [GATEWAY_TAG],
                        "ip": [f"tcp:{p}" for p in sorted(set(member_ports))]})
    if managed != [g for g in old_grants if _is_managed_grant(g)]:
        changes.extend(f"Grant: {', '.join(g['src'])} → {GATEWAY_TAG} ({', '.join(g['ip'])})" for g in managed)
        if not managed:
            changes.append("Grant terkelola dihapus")
    grants.extend(managed)
    policy["grants"] = grants
    return policy, changes


# ── Dashboard RBAC → Tailscale identity mapping ───────────────────────────────

async def admin_users() -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, username, role, tailscale_login FROM users "
            "WHERE role = ANY($1::text[]) AND is_active ORDER BY id", list(_ADMIN_ROLES))
    return [dict(r) for r in rows]


async def set_admin_login(user_id: int, login: str | None) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE users SET tailscale_login = $2, updated_at = NOW() "
            "WHERE id = $1 AND role = ANY($3::text[]) "
            "RETURNING id, username, role, tailscale_login", user_id, login, list(_ADMIN_ROLES))
    return dict(row) if row else None


async def _mapped_logins() -> list[str]:
    return [u["tailscale_login"] for u in await admin_users() if u["tailscale_login"]]


# ── Orchestration against the live tailnet ────────────────────────────────────

async def status() -> dict:
    policy, _ = await ts.get_acl_json()
    devices = await ts.list_devices()
    admins = await admin_users()
    logins = [a["tailscale_login"] for a in admins if a["tailscale_login"]]
    installed = GATEWAY_TAG in policy.get("tagOwners", {})
    ports = current_member_ports(policy)
    return {
        "allow_all_active":  allow_all_active(policy),
        "managed_installed": installed,
        "in_sync":           installed and not build_policy(policy, logins, ports, False)[1],
        "member_ports":      ports or DEFAULT_MEMBER_PORTS,
        "gateway_devices":   [d.get("hostname") for d in devices if GATEWAY_TAG in (d.get("tags") or [])],
        "admins":            admins,
    }


async def preview(member_ports: list[int], remove_allow_all: bool) -> dict:
    policy, etag = await ts.get_acl_json()
    logins = await _mapped_logins()
    proposed, changes = build_policy(policy, logins, member_ports, remove_allow_all)
    error = await ts.validate_acl(proposed) if changes else None
    devices = await ts.list_devices()

    warnings = []
    if allow_all_active(proposed):
        warnings.append("Aturan allow-all masih aktif, jadi semua device tetap bisa menjangkau gateway "
                        "— pembatasan di atas belum berlaku. Centang \"Hapus allow-all\" untuk menegakkannya.")
    elif remove_allow_all and allow_all_active(policy):
        warnings.append("Menghapus allow-all memutus semua akses tailnet yang tidak tercakup aturan lain, "
                        "termasuk device atau layanan lain yang memakai tailnet ini.")
    if not logins:
        warnings.append("Belum ada admin dashboard yang dipetakan ke login Tailscale — grant admin tidak dibuat.")
    if not any(GATEWAY_TAG in (d.get("tags") or []) for d in devices):
        warnings.append(f"Belum ada device bertag {GATEWAY_TAG} — aturan ini belum mengenai device mana pun.")
    hujson = await ts.get_acl()
    if isinstance(hujson, str) and re.search(r"^\s*//|/\*", hujson, re.M) and changes:
        warnings.append("Komentar di policy HuJSON akan hilang saat Apply, karena policy dikirim ulang sebagai JSON.")

    return {
        "changes":          changes,
        "warnings":         warnings,
        "valid":            error is None,
        "validation_error": error,
        "etag":             etag,
        "proposed":         json.dumps(proposed, indent=2),
    }


async def apply(member_ports: list[int], remove_allow_all: bool, etag: str) -> list[str]:
    """Rebuilds from the live policy (never trusts a client-supplied policy) and writes it with
    If-Match, so an edit made in the Tailscale console after the preview is never overwritten."""
    policy, current_etag = await ts.get_acl_json()
    if etag != current_etag:
        raise PolicyChanged()
    proposed, changes = build_policy(policy, await _mapped_logins(), member_ports, remove_allow_all)
    if not changes:
        return []
    error = await ts.validate_acl(proposed)
    if error:
        raise ValueError(error)
    try:
        await ts.set_acl(proposed, current_etag)
    except ts.TailscaleError as e:
        if e.status_code == 412:
            raise PolicyChanged()
        raise
    return changes


async def set_gateway(device_id: str, enabled: bool) -> list[str]:
    policy, _ = await ts.get_acl_json()
    if GATEWAY_TAG not in policy.get("tagOwners", {}):
        raise PolicyNotInstalled()
    device = await ts.get_device(device_id)
    tags = [t for t in (device.get("tags") or []) if t != GATEWAY_TAG]
    if enabled:
        tags.append(GATEWAY_TAG)
    await ts.set_device_tags(device_id, tags)
    return tags
