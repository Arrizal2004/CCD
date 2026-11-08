-- V025: job pembuatan template dari cloud image resmi (Ubuntu dan openSUSE). Katalognya ada di kode
-- (services/cloud_images.CATALOG); tabel ini hanya menyimpan progres supaya tetap terlihat setelah halaman
-- ditutup atau backend restart.
CREATE TABLE IF NOT EXISTS image_jobs (
    id             SERIAL PRIMARY KEY,
    instance       TEXT NOT NULL,
    node           TEXT NOT NULL,
    image_key      TEXT NOT NULL,
    image_name     TEXT NOT NULL,
    options        JSONB NOT NULL,                       -- storage, disk_storage, pool, template_name, bridge, cores, memory_mb, disk_gb
    status         TEXT NOT NULL DEFAULT 'pending',      -- pending | running | done | failed | interrupted
    step           TEXT NOT NULL DEFAULT '',             -- preflight | download | create | template
    upid           TEXT NOT NULL DEFAULT '',             -- task Proxmox yang sedang ditunggu
    volid          TEXT NOT NULL DEFAULT '',             -- berkas image di storage
    vmid           INTEGER,                              -- template yang dihasilkan
    result         JSONB NOT NULL DEFAULT '{}'::jsonb,   -- checksum, progres, peringatan
    error          TEXT NOT NULL DEFAULT '',
    created_by_id  INTEGER,
    created_by     TEXT NOT NULL DEFAULT '',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_image_jobs_status ON image_jobs (status);
CREATE INDEX IF NOT EXISTS idx_image_jobs_instance ON image_jobs (instance, created_at DESC);
