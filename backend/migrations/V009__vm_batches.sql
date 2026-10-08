-- V009: pembuatan VM massal per kelas (grup). Satu batch = satu permintaan admin; satu item = satu VM untuk
-- satu mahasiswa. Progres disimpan di database supaya tetap bisa dipantau walau halaman ditutup, dan bisa
-- dilanjutkan kalau backend sempat berhenti di tengah jalan.
CREATE TABLE IF NOT EXISTS vm_batches (
    id             SERIAL PRIMARY KEY,
    instance       TEXT NOT NULL,
    node           TEXT NOT NULL,
    group_id       INTEGER,
    group_name     TEXT NOT NULL,
    template_vmid  INTEGER NOT NULL,
    settings       JSONB NOT NULL,          -- cores, memory_mb, disk_gb, network_id, lease_days, start
    status         TEXT NOT NULL DEFAULT 'running',   -- running | done | interrupted
    created_by_id  INTEGER,
    created_by     TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at    TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS vm_batch_items (
    id            SERIAL PRIMARY KEY,
    batch_id      INTEGER NOT NULL REFERENCES vm_batches(id) ON DELETE CASCADE,
    user_id       INTEGER,
    username      TEXT NOT NULL,
    full_name     TEXT NOT NULL DEFAULT '',
    vm_name       TEXT NOT NULL,
    os_username   TEXT NOT NULL,
    password_enc  TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',    -- pending | creating | done | failed
    vmid          INTEGER,
    ip            TEXT,
    error         TEXT,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vm_batch_items_batch ON vm_batch_items (batch_id);
