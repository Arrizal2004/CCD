-- V022: jadwal VM (nyala, mati, dan snapshot otomatis per hari dan jam, zona waktu dashboard).
CREATE TABLE IF NOT EXISTS vm_schedules (
    id          SERIAL PRIMARY KEY,
    host_name   TEXT NOT NULL,                       -- <label>__<node>
    vm_id       TEXT NOT NULL,
    action      TEXT NOT NULL CHECK (action IN ('start', 'shutdown', 'snapshot')),
    days        SMALLINT[] NOT NULL,                 -- hari ISO: 1 = Senin ... 7 = Minggu
    at_time     TIME NOT NULL,                       -- jam lokal menurut zona waktu di Pengaturan Sistem
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    note        TEXT NOT NULL DEFAULT '',
    keep        SMALLINT NOT NULL DEFAULT 3,         -- snapshot: simpan sebanyak ini yang terbaru (ccd-auto-*)
    created_by  TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_run_at TIMESTAMPTZ,
    last_result TEXT
);
CREATE INDEX IF NOT EXISTS idx_vm_schedules_vm ON vm_schedules (host_name, vm_id);
