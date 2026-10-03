from fastapi import FastAPI, Depends, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
import logging
import os
from datetime import datetime, timezone
from time import perf_counter
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
import redis.asyncio as aioredis
import asyncio
from starlette.types import Scope

from logging_config import setup_logging
setup_logging()

log = logging.getLogger("main")


class _SecureUploadsStaticFiles(StaticFiles):
    """StaticFiles subclass that:
    - BLOCKS direct HTTP access to the uploads/ subtree (returns 403).
      All upload access goes through the authenticated
      GET /api/tickets/{id}/files/{name} endpoint instead.
    - Passes everything else (e.g. static/agent/) through unchanged.
    """
    async def get_response(self, path: str, scope: Scope):
        if path.startswith("uploads/"):
            from starlette.responses import Response
            return Response(
                "Access denied — use the authenticated download endpoint.",
                status_code=403,
                media_type="text/plain",
            )
        return await super().get_response(path, scope)

from database import init_db, close_pool, run_cleanup_job
from auth import require_superadmin
from routers import vm_metadata, users
from routers import ssh_creds, linux_vm, terminal, metrics_ws, admin, tickets
from routers import infra_requests, groups, guac, proxmox, tailscale, openweb, ssh_keys

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
redis_client: aioredis.Redis = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_client
    redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
    from crypto import secure_from_url, ENABLED as ENC_ENABLED
    app.state.redis     = secure_from_url(redis_client)
    app.state.redis_raw = redis_client          # plain client untuk healthz ping
    import token_blocklist
    token_blocklist.set_redis(redis_client)
    from services import login_rate_limit
    login_rate_limit.set_redis(redis_client)
    log.info("Redis connected", extra={"redis_url": REDIS_URL, "payload_encryption": ENC_ENABLED})

    await init_db()

    # Start background cleanup job
    cleanup_task = asyncio.create_task(run_cleanup_job())
    app.state.cleanup_task = cleanup_task
    log.info("Cleanup job started (runs every 6 hours)")

    # Start VM agent poller — utamakan jalur exporter :9100 untuk VM Running
    from services.vm_agent_poller import run_agent_poller
    poller_task = asyncio.create_task(run_agent_poller())
    app.state.poller_task = poller_task
    log.info("VM agent poller started (port 9100)")

    from services.guac_sync import ensure_admin_password_on_startup
    admin_pw_task = asyncio.create_task(ensure_admin_password_on_startup())

    from services.proxmox_iops_poller import run_iops_poller
    iops_task = asyncio.create_task(run_iops_poller())

    yield

    cleanup_task.cancel()
    poller_task.cancel()
    admin_pw_task.cancel()
    iops_task.cancel()
    from services.ssh_client import close_all_pooled
    await close_all_pooled()   # tutup semua koneksi SSH pool (anti zombie)
    await redis_client.aclose()
    await close_pool()
    log.info("Connections closed")


app = FastAPI(title="Campus Cloud Dashboard API", version="1.0.0", lifespan=lifespan)

# Serve agent installer files
os.makedirs("static/agent", exist_ok=True)
app.mount("/agent", StaticFiles(directory="static/agent"), name="agent")

# Guarantee local storage paths for user media uploads
os.makedirs("static/uploads/tickets", exist_ok=True)
os.makedirs("static/uploads/chat", exist_ok=True)
os.makedirs("static/uploads/requests", exist_ok=True)

# Mount static route so uploads are publicly reachable via web URLs.
# Uses _SecureUploadsStaticFiles to inject X-Content-Type-Options / CSP headers
# on any path that contains "uploads", blocking browser MIME sniffing and JS execution.
app.mount("/static", _SecureUploadsStaticFiles(directory="static"), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.getenv("ALLOWED_ORIGINS", "http://localhost").split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

app.include_router(vm_metadata.router, prefix="/api/v1/vm-metadata", tags=["VM Metadata"])
app.include_router(users.router,         prefix="/api/v1/users",         tags=["Users"])
app.include_router(ssh_creds.router,     prefix="/api/v1/ssh-creds",     tags=["SSH Credentials"])
app.include_router(linux_vm.router,      prefix="/api/v1/linux-vm",      tags=["Linux VM Automation"])
app.include_router(terminal.router,      prefix="/ws/terminal",          tags=["Terminal"])
app.include_router(metrics_ws.router,    prefix="/ws/metrics",           tags=["Metrics WS"])
app.include_router(admin.router,         prefix="/api/admin",            tags=["Admin Panel"])
app.include_router(tickets.router,        prefix="/api/tickets",              tags=["Helpdesk"])
app.include_router(infra_requests.router, prefix="/api/v1/infra-requests",    tags=["Infrastructure Requests"])
app.include_router(groups.router,         prefix="/api/v1/groups",             tags=["Groups"])
app.include_router(guac.router,           prefix="/ws/guac",                   tags=["Guacamole WS"])
app.include_router(proxmox.router,        prefix="/api/v1/proxmox",            tags=["Proxmox"])
app.include_router(tailscale.router,       prefix="/api/v1/tailscale",           tags=["Tailscale"])
app.include_router(openweb.router,         prefix="/api/v1/openweb",             tags=["Open Web"])
app.include_router(ssh_keys.router,        prefix="/api/v1/ssh-keys",            tags=["SSH Keys"])


@app.get("/health")
async def health():
    """Backward-compat liveness probe (selalu 200)."""
    return {"status": "ok"}


def _is_admin_request(request: Request) -> bool:
    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        return False
    try:
        from auth import decode_token, Role
        payload = decode_token(auth_header.split(" ", 1)[1])
    except Exception:
        return False
    return payload.get("role") in (Role.SUPERADMIN, Role.SYSADMIN)


@app.get("/healthz")
async def healthz(request: Request):
    """
    Readiness probe — cek semua dependency kritis.
    HTTP 200 = semua OK, HTTP 503 = ada komponen bermasalah.
    """
    checks: dict = {}

    # ── Database ──────────────────────────────────────────────────
    t0 = perf_counter()
    try:
        from database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        checks["database"] = {
            "status":     "ok",
            "latency_ms": round((perf_counter() - t0) * 1000, 1),
        }
    except Exception as exc:
        checks["database"] = {"status": "error", "error": str(exc)}

    # ── Redis ────────────────────────────────────────────────────
    t0 = perf_counter()
    try:
        r = getattr(request.app.state, "redis_raw", None)
        if r is None:
            raise RuntimeError("Redis not initialised yet")
        await r.ping()
        checks["redis"] = {
            "status":     "ok",
            "latency_ms": round((perf_counter() - t0) * 1000, 1),
        }
    except Exception as exc:
        checks["redis"] = {"status": "error", "error": str(exc)}

    # ── Proxmox VE API (all configured instances) ─────────────────
    t0 = perf_counter()
    try:
        from services import proxmox_instances as pve_instances
        instances = await pve_instances.list_instances()
        nodes_online = nodes_total = 0
        errors = []
        for inst in instances:
            try:
                client = await pve_instances.get_client(inst["label"])
                nodes = await client.list_nodes()
                nodes_online += sum(1 for n in nodes if n.get("status") == "online")
                nodes_total += len(nodes)
            except Exception as exc:
                errors.append(f"{inst['label']}: {exc}")
        checks["proxmox"] = {
            "status":     "ok" if not errors else "error",
            "latency_ms": round((perf_counter() - t0) * 1000, 1),
            "instances_total": len(instances),
            "nodes_online": nodes_online,
            "nodes_total":  nodes_total,
            **({"error": "; ".join(errors)} if errors else {}),
        }
    except Exception as exc:
        checks["proxmox"] = {"status": "error", "error": str(exc)}

    # ── Guacamole ────────────────────────────────────────────────
    t0 = perf_counter()
    try:
        import os as _os
        import httpx
        guac_url = _os.getenv("GUAC_URL", "").rstrip("/")
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{guac_url}/api/languages")
        if resp.status_code >= 400:
            raise RuntimeError(f"HTTP {resp.status_code}")
        checks["guacamole"] = {
            "status":     "ok",
            "latency_ms": round((perf_counter() - t0) * 1000, 1),
        }
    except Exception as exc:
        checks["guacamole"] = {"status": "error", "error": str(exc)}

    all_ok = all(c.get("status") == "ok" for c in checks.values())

    # Endpoint ini terbuka tanpa login (dipakai probe/monitoring). Pesan error mentah dan
    # label instance Proxmox bisa membocorkan alamat internal, jadi hanya admin yang melihatnya.
    if not _is_admin_request(request):
        checks = {name: {k: v for k, v in c.items() if k in ("status", "latency_ms")}
                  for name, c in checks.items()}
    return JSONResponse(
        content={
            "status":    "ok" if all_ok else "degraded",
            "version":   app.version,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "checks":    checks,
        },
        status_code=200 if all_ok else 503,
    )


@app.post("/api/v1/admin/cleanup")
async def manual_cleanup(user: dict = Depends(require_superadmin)):
    """Trigger cleanup manual — hanya superadmin (operasi destruktif global)."""
    from database import cleanup_old_metrics
    vm_del, host_del = await cleanup_old_metrics()
    return {"vm_rows_deleted": vm_del, "host_rows_deleted": host_del}