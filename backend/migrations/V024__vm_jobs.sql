-- V024: pembuatan VM dari template sebagai proses latar belakang (satu VM per proses, bisa beberapa sekaligus).
-- Riwayatnya tetap terlihat setelah halaman ditutup. Password cloud-init disimpan terenkripsi hanya selama proses
-- belum berhasil (untuk "Ulangi"), dan dikosongkan begitu VM jadi.
CREATE TABLE IF NOT EXISTS vm_jobs (
    id            SERIAL PRIMARY KEY,
    instance      TEXT NOT NULL,
    node          TEXT NOT NULL,
    name          TEXT NOT NULL,
    template_vmid INTEGER NOT NULL,
    request       JSONB NOT NULL,                      -- isian form tanpa password
    password_enc  TEXT,
    actor         JSONB NOT NULL,                      -- sub, username, role pembuat (untuk audit)
    status        TEXT NOT NULL DEFAULT 'pending',     -- pending | running | done | failed | interrupted
    step          TEXT NOT NULL DEFAULT '',            -- clone | clone_wait | configure | start | agent | credentials
    vmid          INTEGER,
    result        JSONB NOT NULL DEFAULT '{}'::jsonb,
    error         TEXT NOT NULL DEFAULT '',
    created_by_id INTEGER,
    created_by    TEXT NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_vm_jobs_status ON vm_jobs (status);
CREATE INDEX IF NOT EXISTS idx_vm_jobs_instance ON vm_jobs (instance, id DESC);
