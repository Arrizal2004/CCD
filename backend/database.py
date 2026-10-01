import os
import asyncpg
import asyncio
import logging
from datetime import datetime, timezone, timedelta

log = logging.getLogger("database")

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://hyperv:hyperv123@postgres:5432/hypervdb"
)

_pool: asyncpg.Pool = None

RETENTION_DAYS = 14  # auto-cleanup metrik VM/host setelah 14 hari

# Audit log sengaja diberi retensi lebih panjang dari metrik — dia jejak keamanan/kepatuhan
# (siapa login, siapa hapus VM, dst), bukan data performa yang cepat basi. Default 180 hari,
# bisa diubah lewat .env kalau kebijakan institusi butuh lebih lama/pendek.
AUDIT_RETENTION_DAYS = int(os.getenv("AUDIT_RETENTION_DAYS", "180"))


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
    return _pool


async def close_pool():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


async def init_db():
    """Buat semua tabel jika belum ada."""
    pool = await get_pool()
    async with pool.acquire() as conn:

        # Users table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id            SERIAL PRIMARY KEY,
                username      TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                full_name     TEXT NOT NULL DEFAULT '',
                role          TEXT NOT NULL DEFAULT 'student',
                email         TEXT,
                is_active     BOOLEAN NOT NULL DEFAULT true,
                last_login    TIMESTAMPTZ,
                created_at    TIMESTAMPTZ DEFAULT NOW(),
                updated_at    TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # VM assignments untuk student role
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS vm_assignments (
                id        SERIAL PRIMARY KEY,
                user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                vm_id     TEXT NOT NULL,
                host_name TEXT NOT NULL,
                assigned_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (user_id, vm_id, host_name)
            )
        """)

        # Buat superadmin awal kalau belum ada. Password dari INITIAL_ADMIN_PASSWORD (diisi
        # setup.sh); tanpa itu jatuh ke admin123 yang wajib langsung diganti.
        from auth import hash_password
        existing = await conn.fetchval("SELECT id FROM users WHERE username = 'admin'")
        if not existing:
            initial_pass = os.getenv("INITIAL_ADMIN_PASSWORD") or "admin123"
            await conn.execute("""
                INSERT INTO users (username, password_hash, full_name, role)
                VALUES ('admin', $1, 'Super Administrator', 'superadmin')
            """, hash_password(initial_pass))
            if initial_pass == "admin123":
                log.warning("Superadmin awal dibuat dengan password bawaan admin123 — segera ganti lewat menu Profil")
            else:
                log.info("Superadmin awal dibuat dengan password dari INITIAL_ADMIN_PASSWORD")

        # VM metadata — deskripsi, owner, masa pinjam
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS vm_metadata (
                vm_id        TEXT NOT NULL,
                host_name    TEXT NOT NULL,
                description  TEXT DEFAULT '',
                owner        TEXT DEFAULT '',
                borrow_until TIMESTAMPTZ,
                notes        TEXT DEFAULT '',
                updated_at   TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (vm_id, host_name)
            )
        """)

        # VM metrics history — time-series tiap 10 detik
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS vm_metrics_history (
                id           BIGSERIAL,
                vm_id        TEXT NOT NULL,
                vm_name      TEXT NOT NULL,
                host_name    TEXT NOT NULL,
                recorded_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                state        TEXT,
                cpu_pct      REAL,
                mem_assigned_mb  BIGINT,
                mem_max_mb       BIGINT,
                net_tx_bps   BIGINT DEFAULT 0,
                net_rx_bps   BIGINT DEFAULT 0,
                uptime_ms    BIGINT DEFAULT 0,
                PRIMARY KEY (id)
            )
        """)

        # Index untuk query cepat per VM + time range
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_vmh_vm_time
            ON vm_metrics_history (host_name, vm_id, recorded_at DESC)
        """)

        # Index untuk cleanup job
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_vmh_recorded_at
            ON vm_metrics_history (recorded_at)
        """)

        # Helpdesk / Ticketing
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS tickets (
                id            BIGSERIAL PRIMARY KEY,
                ticket_number TEXT UNIQUE,
                student_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                vm_id         TEXT,
                host_name     TEXT,
                title         TEXT NOT NULL,
                category      TEXT NOT NULL DEFAULT 'OTHERS',
                description   TEXT NOT NULL DEFAULT '',
                status        TEXT NOT NULL DEFAULT 'OPEN',
                vm_snapshot   JSONB,
                created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tickets_student ON tickets (student_id, created_at DESC)
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets (status, created_at DESC)
        """)
        # Waktu tiket ditutup (NULL bila belum/ dibuka kembali oleh superadmin)
        await conn.execute("""
            ALTER TABLE tickets ADD COLUMN IF NOT EXISTS closed_at TIMESTAMPTZ
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS ticket_messages (
                id          BIGSERIAL PRIMARY KEY,
                ticket_id   BIGINT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
                sender_id   INTEGER,
                sender_role TEXT,
                sender_name TEXT,
                message     TEXT NOT NULL,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tmsg_ticket ON ticket_messages (ticket_id, created_at ASC)
        """)
        # Attachment columns on ticket messages (idempotent migration)
        await conn.execute("""
            ALTER TABLE ticket_messages ADD COLUMN IF NOT EXISTS attachment_url  TEXT
        """)
        await conn.execute("""
            ALTER TABLE ticket_messages ADD COLUMN IF NOT EXISTS attachment_name TEXT
        """)

        # Binary attachment metadata — tracks every uploaded file per ticket
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS ticket_attachments (
                id            BIGSERIAL PRIMARY KEY,
                ticket_id     BIGINT  NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
                message_id    BIGINT  REFERENCES ticket_messages(id) ON DELETE SET NULL,
                stored_name   TEXT    NOT NULL,
                original_name TEXT    NOT NULL DEFAULT '',
                file_url      TEXT    NOT NULL,
                file_size     BIGINT  NOT NULL DEFAULT 0,
                mime_type     TEXT    NOT NULL DEFAULT '',
                uploader_id   INTEGER,
                created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tattach_ticket
            ON ticket_attachments (ticket_id, created_at DESC)
        """)

        # Audit Trail / Activity Log
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id              BIGSERIAL PRIMARY KEY,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                user_id         INTEGER,
                username        TEXT,
                user_role       TEXT,
                action_type     TEXT NOT NULL,
                severity_level  TEXT NOT NULL DEFAULT 'INFO',
                target_server_id   TEXT,
                target_server_name TEXT,
                client_ip       TEXT,
                detail_message  TEXT
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_logs (created_at DESC)
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_audit_sev ON audit_logs (severity_level, created_at DESC)
        """)

        # VM IOPS history — time-series IOPS dari node-exporter (poller :9100)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS vm_iops_history (
                id           BIGSERIAL,
                vm_id        TEXT NOT NULL,
                host_name    TEXT NOT NULL,
                recorded_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                read_iops    REAL DEFAULT 0,
                write_iops   REAL DEFAULT 0,
                PRIMARY KEY (id)
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_iops_vm_time
            ON vm_iops_history (host_name, vm_id, recorded_at DESC)
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_iops_recorded_at
            ON vm_iops_history (recorded_at)
        """)

        # Host metrics history
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS host_metrics_history (
                id           BIGSERIAL,
                host_name    TEXT NOT NULL,
                recorded_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                cpu_pct      REAL,
                mem_used_mb  BIGINT,
                mem_total_mb BIGINT,
                disk_read_iops  REAL DEFAULT 0,
                disk_write_iops REAL DEFAULT 0,
                net_rx_bps   BIGINT DEFAULT 0,
                net_tx_bps   BIGINT DEFAULT 0,
                PRIMARY KEY (id)
            )
        """)

        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_hosth_time
            ON host_metrics_history (host_name, recorded_at DESC)
        """)
        await conn.execute("""
            ALTER TABLE host_metrics_history
            ADD COLUMN IF NOT EXISTS disk_used_gb REAL DEFAULT NULL
        """)

        # Sesi Open Web (proxy ke IP privat) — diaudit & bisa dicabut admin di Audit & Remote
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS openweb_sessions (
                id          TEXT PRIMARY KEY,
                user_id     INTEGER,
                username    TEXT NOT NULL DEFAULT '',
                role        TEXT NOT NULL DEFAULT '',
                target_ip   TEXT NOT NULL,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                expires_at  TIMESTAMPTZ NOT NULL,
                revoked_at  TIMESTAMPTZ,
                revoked_by  TEXT,
                last_seen   TIMESTAMPTZ,
                hits        INTEGER NOT NULL DEFAULT 0,
                last_ip     TEXT NOT NULL DEFAULT '',
                client_ips  TEXT[] NOT NULL DEFAULT '{}'
            )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_openweb_sessions_created ON openweb_sessions (created_at DESC)")

        # OS Accounts per VM — beberapa user OS bisa diassign ke student berbeda
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS vm_os_accounts (
                id            SERIAL PRIMARY KEY,
                vm_id         TEXT NOT NULL,
                host_name     TEXT NOT NULL,
                label         TEXT NOT NULL DEFAULT '',
                os_type       TEXT NOT NULL DEFAULT 'linux',
                guac_protocol TEXT NOT NULL DEFAULT '',
                ssh_host      TEXT NOT NULL DEFAULT '',
                ssh_port      INTEGER NOT NULL DEFAULT 22,
                os_username   TEXT NOT NULL DEFAULT '',
                password_enc  TEXT,
                pkey_enc      TEXT,
                created_at    TIMESTAMPTZ DEFAULT NOW(),
                updated_at    TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (vm_id, host_name, os_username)
            )
        """)

        # Migrasi: tambah os_account_id ke vm_assignments
        await conn.execute("""
            ALTER TABLE vm_assignments
            ADD COLUMN IF NOT EXISTS os_account_id INTEGER REFERENCES vm_os_accounts(id) ON DELETE SET NULL
        """)

        # SSH credentials untuk host Hyper-V (fallback dari Redis)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS host_ssh_credentials (
                host_name    TEXT PRIMARY KEY,
                ssh_host     TEXT NOT NULL,
                ssh_port     INTEGER NOT NULL DEFAULT 22,
                ssh_user     TEXT NOT NULL DEFAULT 'Administrator',
                password_enc TEXT,
                pkey_enc     TEXT,
                created_at   TIMESTAMPTZ DEFAULT NOW(),
                updated_at   TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # Credentials untuk akses VM: SSH (Linux) atau PS Direct (Windows)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS vm_credentials (
                vm_id        TEXT NOT NULL,
                host_name    TEXT NOT NULL,
                os_type      TEXT NOT NULL DEFAULT 'linux',
                cred_type    TEXT NOT NULL DEFAULT 'ssh',
                ssh_host     TEXT NOT NULL DEFAULT '',
                ssh_port     INTEGER NOT NULL DEFAULT 22,
                username     TEXT NOT NULL DEFAULT 'root',
                password_enc TEXT,
                pkey_enc     TEXT,
                created_at   TIMESTAMPTZ DEFAULT NOW(),
                updated_at   TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (vm_id, host_name)
            )
        """)
        # Migrasi: tambah os_type jika kolom belum ada (untuk DB yang sudah ada)
        await conn.execute("""
            ALTER TABLE vm_credentials ADD COLUMN IF NOT EXISTS os_type TEXT NOT NULL DEFAULT 'linux'
        """)
        # Migrasi: status agent exporter (node-exporter / windows_exporter) di dalam VM
        await conn.execute("""
            ALTER TABLE vm_credentials ADD COLUMN IF NOT EXISTS agent_installed BOOLEAN NOT NULL DEFAULT FALSE
        """)
        await conn.execute("""
            ALTER TABLE vm_credentials ADD COLUMN IF NOT EXISTS agent_status TEXT NOT NULL DEFAULT ''
        """)
        await conn.execute("""
            ALTER TABLE vm_credentials ADD COLUMN IF NOT EXISTS agent_port INTEGER NOT NULL DEFAULT 9100
        """)
        # Flag eksplisit: exporter sudah terinstall DAN terverifikasi via health-check port.
        await conn.execute("""
            ALTER TABLE vm_credentials ADD COLUMN IF NOT EXISTS agent_configured BOOLEAN NOT NULL DEFAULT FALSE
        """)
        # Migrasi: protokol Guacamole pilihan user ('ssh' | 'rdp'); '' = auto dari os_type
        await conn.execute("""
            ALTER TABLE vm_credentials ADD COLUMN IF NOT EXISTS guac_protocol TEXT NOT NULL DEFAULT ''
        """)

        # Guest exporter usage — diisi oleh vm_agent_poller saat scrape berhasil
        await conn.execute("""
            ALTER TABLE vm_metrics_history ADD COLUMN IF NOT EXISTS mem_used_mb REAL
        """)

        # Kolom tags di vm_metadata (array teks, disimpan sebagai TEXT[])
        await conn.execute("""
            ALTER TABLE vm_metadata ADD COLUMN IF NOT EXISTS tags TEXT[] DEFAULT '{}'
        """)
        # Arsip kredensial login VM (nullable) — agar student bisa recover login
        await conn.execute("""
            ALTER TABLE vm_metadata ADD COLUMN IF NOT EXISTS vm_username TEXT
        """)
        await conn.execute("""
            ALTER TABLE vm_metadata ADD COLUMN IF NOT EXISTS vm_password_enc TEXT
        """)

        # VM Templates — konfigurasi reusable untuk buat VM baru
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS vm_templates (
                id          TEXT PRIMARY KEY,
                name        TEXT NOT NULL UNIQUE,
                description TEXT DEFAULT '',
                os_type     TEXT NOT NULL DEFAULT 'linux',
                cpu         INTEGER NOT NULL DEFAULT 2,
                ram_gb      INTEGER NOT NULL DEFAULT 4,
                disk_gb     INTEGER NOT NULL DEFAULT 40,
                generation  INTEGER NOT NULL DEFAULT 2,
                storage_dir TEXT NOT NULL DEFAULT 'C:\\HYPERV_VM',
                switch_name TEXT NOT NULL DEFAULT 'Default Switch',
                use_dynamic_mem BOOLEAN NOT NULL DEFAULT FALSE,
                checkpoint_type TEXT NOT NULL DEFAULT 'Disabled',
                master_path TEXT DEFAULT '',
                use_diff    BOOLEAN NOT NULL DEFAULT FALSE,
                created_by  TEXT NOT NULL DEFAULT 'admin',
                created_at  TIMESTAMPTZ DEFAULT NOW(),
                updated_at  TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        # Master Parent Cloning Template — kolom tambahan (idempotent).
        # Networking lanjutan + auto-config credentials (parent source & new guest).
        for _col, _type in [
            ("vlan_id",        "INTEGER"),
            ("subnet",         "TEXT DEFAULT ''"),
            ("parent_ssh_ip",  "TEXT DEFAULT ''"),
            ("parent_ssh_port","INTEGER NOT NULL DEFAULT 22"),
            ("parent_username","TEXT DEFAULT ''"),
            ("parent_password","TEXT DEFAULT ''"),
            ("dns_servers",    "TEXT DEFAULT ''"),
            ("new_username",   "TEXT DEFAULT ''"),
            ("new_password",   "TEXT DEFAULT ''"),
        ]:
            await conn.execute(
                f"ALTER TABLE vm_templates ADD COLUMN IF NOT EXISTS {_col} {_type}"
            )

        # Host vSwitches — cache persisten daftar virtual switch per host.
        # Diisi/di-refresh setiap kali agent membalas list-vswitches, sehingga
        # frontend bisa menampilkan daftar terakhir secara instan tanpa menunggu agent.
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS host_vswitches (
                host_name    TEXT NOT NULL,
                name         TEXT NOT NULL,
                switch_type  TEXT DEFAULT '',
                net_adapter  TEXT DEFAULT '',
                updated_at   TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (host_name, name)
            )
        """)

        # Infrastructure Request Pipeline — VPS / VPN provisioning requests from students.
        # Status flow: PENDING → APPROVED | REJECTED
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS infrastructure_requests (
                id           TEXT PRIMARY KEY,
                student_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                request_type TEXT NOT NULL,               -- 'VPS' | 'VPN'
                specs        JSONB,                       -- {cpu, ram_gb, storage_gb, os}
                notes        TEXT NOT NULL DEFAULT '',    -- student's description / purpose
                status       TEXT NOT NULL DEFAULT 'PENDING',
                admin_note   TEXT NOT NULL DEFAULT '',    -- admin response / reason
                reviewed_by  INTEGER REFERENCES users(id),
                reviewed_at  TIMESTAMPTZ,
                created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_infra_student
            ON infrastructure_requests (student_id, created_at DESC)
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_infra_status
            ON infrastructure_requests (status, created_at DESC)
        """)
        # Idempotent migration: document attachment support
        await conn.execute("""
            ALTER TABLE infrastructure_requests ADD COLUMN IF NOT EXISTS document_url TEXT
        """)

        # Phase-2 migration: extended status + credentials + chat
        await conn.execute("ALTER TABLE infrastructure_requests ADD COLUMN IF NOT EXISTS vpn_username    TEXT")
        await conn.execute("ALTER TABLE infrastructure_requests ADD COLUMN IF NOT EXISTS vpn_password    TEXT")
        await conn.execute("ALTER TABLE infrastructure_requests ADD COLUMN IF NOT EXISTS config_file_url TEXT")
        await conn.execute("ALTER TABLE infrastructure_requests ADD COLUMN IF NOT EXISTS linked_vm_id    TEXT")
        await conn.execute("ALTER TABLE infrastructure_requests ADD COLUMN IF NOT EXISTS linked_vm_name  TEXT")
        await conn.execute("ALTER TABLE infrastructure_requests ADD COLUMN IF NOT EXISTS linked_host_name TEXT")

        # Migrate old status values → new vocabulary
        await conn.execute("UPDATE infrastructure_requests SET status = 'DONE'    WHERE status = 'APPROVED'")
        await conn.execute("UPDATE infrastructure_requests SET status = 'DECLINE' WHERE status = 'REJECTED'")

        # Chat messages per infra request
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS infra_request_messages (
                id          SERIAL PRIMARY KEY,
                request_id  TEXT         NOT NULL REFERENCES infrastructure_requests(id) ON DELETE CASCADE,
                sender_id   INTEGER      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                sender_role TEXT         NOT NULL,
                sender_name TEXT         NOT NULL,
                message     TEXT         NOT NULL DEFAULT '',
                created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_infra_msg_req
            ON infra_request_messages (request_id, created_at ASC)
        """)

        # is_verified: false = student baru self-register (hanya bisa akses infra requests)
        #              true  = akses penuh (auto-upgrade saat infra request pertama di-approve)
        await conn.execute("""
            ALTER TABLE users ADD COLUMN IF NOT EXISTS is_verified BOOLEAN NOT NULL DEFAULT false
        """)
        # Semua student yang sudah ada sebelum fitur ini (dibuat admin) langsung verified
        await conn.execute("""
            UPDATE users SET is_verified = true WHERE role = 'student' AND is_verified = false
        """)

        # Role 'admin' dihapus — migrate ke 'sysadmin'
        await conn.execute("""
            UPDATE users SET role = 'sysadmin' WHERE role = 'admin'
        """)

        # Simpan vm_name langsung di assignment agar tidak bergantung metrics history
        await conn.execute("""
            ALTER TABLE vm_assignments ADD COLUMN IF NOT EXISTS vm_name TEXT
        """)
        # Backfill vm_name dari metrics history untuk assignment yang sudah ada
        await conn.execute("""
            UPDATE vm_assignments va
            SET vm_name = (
                SELECT vm_name FROM vm_metrics_history
                WHERE vm_id = va.vm_id ORDER BY recorded_at DESC LIMIT 1
            )
            WHERE va.vm_name IS NULL
        """)

        # Enkripsi vm_password → pindahkan ke vm_password_enc lalu drop kolom lama
        old_col = await conn.fetchval(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'vm_metadata' AND column_name = 'vm_password'"
        )
        if old_col:
            from services.ssh_client import encrypt_secret as _enc
            plain_rows = await conn.fetch(
                "SELECT vm_id, host_name, vm_password FROM vm_metadata "
                "WHERE vm_password IS NOT NULL AND vm_password != ''"
            )
            for r in plain_rows:
                await conn.execute(
                    "UPDATE vm_metadata SET vm_password_enc = $1 WHERE vm_id = $2 AND host_name = $3",
                    _enc(r["vm_password"]), r["vm_id"], r["host_name"]
                )
            await conn.execute("ALTER TABLE vm_metadata DROP COLUMN vm_password")
            log.info("Encrypted %d vm_metadata passwords, migrated to vm_password_enc", len(plain_rows))

        # ── ReBAC: Groups & VM access ──────────────────────────────────────────
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS groups (
                id          SERIAL PRIMARY KEY,
                name        TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                created_at  TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS group_members (
                group_id  INT NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
                user_id   INT NOT NULL REFERENCES users(id)  ON DELETE CASCADE,
                joined_at TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (group_id, user_id)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS group_vm_access (
                group_id    INT  NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
                vm_id       TEXT NOT NULL,
                host_name   TEXT NOT NULL,
                assigned_at TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (group_id, vm_id, host_name)
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_group_members_user
            ON group_members (user_id)
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_group_vm_group
            ON group_vm_access (group_id)
        """)
        # Kolom auth_mode + credentials untuk koneksi Guacamole per grup
        await conn.execute("""
            ALTER TABLE group_vm_access
                ADD COLUMN IF NOT EXISTS auth_mode       TEXT NOT NULL DEFAULT 'mandiri',
                ADD COLUMN IF NOT EXISTS os_username     TEXT,
                ADD COLUMN IF NOT EXISTS os_password_enc TEXT,
                ADD COLUMN IF NOT EXISTS os_type         TEXT NOT NULL DEFAULT 'linux',
                ADD COLUMN IF NOT EXISTS guac_protocol   TEXT NOT NULL DEFAULT ''
        """)

        # Versioned migrations (V001, V002, ...)
        from migrate import run_migrations
        await run_migrations(conn)

    log.info("Tables ready")


async def get_student_vm_ids(user_id: int, host_name: str = None) -> set:
    """
    Return set of (vm_id, host_name) yang boleh diakses student ini.
    Menggabungkan direct vm_assignments + akses via group_vm_access.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        if host_name:
            rows = await conn.fetch("""
                SELECT vm_id, host_name FROM vm_assignments
                WHERE user_id = $1 AND host_name = $2 AND deleted_at IS NULL
                UNION
                SELECT gva.vm_id, gva.host_name
                FROM group_members gm
                JOIN group_vm_access gva ON gva.group_id = gm.group_id
                WHERE gm.user_id = $1 AND gva.host_name = $2
            """, user_id, host_name)
        else:
            rows = await conn.fetch("""
                SELECT vm_id, host_name FROM vm_assignments
                WHERE user_id = $1 AND deleted_at IS NULL
                UNION
                SELECT gva.vm_id, gva.host_name
                FROM group_members gm
                JOIN group_vm_access gva ON gva.group_id = gm.group_id
                WHERE gm.user_id = $1
            """, user_id)
    return {(r["vm_id"], r["host_name"]) for r in rows}


async def cleanup_old_metrics():
    """
    Hapus data lebih dari RETENTION_DAYS hari.
    Dipanggil oleh background job tiap 6 jam.
    """
    pool = await get_pool()
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)

    async with pool.acquire() as conn:
        # PostgreSQL tidak mendukung aggregate di RETURNING — pakai CTE lalu COUNT.
        vm_deleted = await conn.fetchval(
            "WITH deleted AS (DELETE FROM vm_metrics_history WHERE recorded_at < $1 RETURNING 1) "
            "SELECT count(*) FROM deleted",
            cutoff
        )
        host_deleted = await conn.fetchval(
            "WITH deleted AS (DELETE FROM host_metrics_history WHERE recorded_at < $1 RETURNING 1) "
            "SELECT count(*) FROM deleted",
            cutoff
        )
        await conn.execute(
            "DELETE FROM vm_iops_history WHERE recorded_at < $1", cutoff
        )

    log.info("Cleanup done", extra={"vm_rows": vm_deleted, "host_rows": host_deleted, "retention_days": RETENTION_DAYS})
    return vm_deleted, host_deleted


async def purge_old_audit_logs() -> int:
    """Hapus audit log lebih tua dari AUDIT_RETENTION_DAYS hari. Dipanggil oleh background
    job yang sama dengan cleanup_old_metrics(), retensinya sengaja terpisah (lihat komentar
    di AUDIT_RETENTION_DAYS)."""
    pool = await get_pool()
    cutoff = datetime.now(timezone.utc) - timedelta(days=AUDIT_RETENTION_DAYS)

    async with pool.acquire() as conn:
        deleted = await conn.fetchval(
            "WITH deleted AS (DELETE FROM audit_logs WHERE created_at < $1 RETURNING 1) "
            "SELECT count(*) FROM deleted",
            cutoff
        )

        await conn.execute("DELETE FROM openweb_sessions WHERE created_at < $1", cutoff)

    log.info("Audit log cleanup done", extra={"rows": deleted, "retention_days": AUDIT_RETENTION_DAYS})
    return deleted or 0


async def run_cleanup_job():
    """Background job: cleanup tiap 6 jam. Jalan sekali di startup, lalu tiap 6 jam."""
    while True:
        try:
            await cleanup_old_metrics()
        except Exception as e:
            log.error("Cleanup error: %s", e)
        try:
            await purge_old_audit_logs()
        except Exception as e:
            log.error("Audit log cleanup error: %s", e)
        await asyncio.sleep(6 * 3600)
