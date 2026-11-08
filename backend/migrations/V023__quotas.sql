-- V023: kuota per pengguna. Yang dibatasi adalah apa yang boleh di-assign ke satu mahasiswa:
-- jumlah VM, total vCPU, total RAM, dan total disk. 0 = tanpa batas; kosong (NULL) = ikut lapisan berikutnya.
-- Urutan: kuota pengguna -> kuota grup terbesar -> bawaan (app_settings 'quota_defaults').
ALTER TABLE vms ADD COLUMN IF NOT EXISTS cores INTEGER;
ALTER TABLE vms ADD COLUMN IF NOT EXISTS memory_mb INTEGER;
ALTER TABLE vms ADD COLUMN IF NOT EXISTS disk_gb INTEGER;

CREATE TABLE IF NOT EXISTS user_quotas (
    user_id     INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    max_vms     INTEGER CHECK (max_vms IS NULL OR max_vms >= 0),
    max_vcpu    INTEGER CHECK (max_vcpu IS NULL OR max_vcpu >= 0),
    max_ram_mb  INTEGER CHECK (max_ram_mb IS NULL OR max_ram_mb >= 0),
    max_disk_gb INTEGER CHECK (max_disk_gb IS NULL OR max_disk_gb >= 0),
    updated_by  TEXT NOT NULL DEFAULT '',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS group_quotas (
    group_id    INTEGER PRIMARY KEY REFERENCES groups(id) ON DELETE CASCADE,
    max_vms     INTEGER CHECK (max_vms IS NULL OR max_vms >= 0),
    max_vcpu    INTEGER CHECK (max_vcpu IS NULL OR max_vcpu >= 0),
    max_ram_mb  INTEGER CHECK (max_ram_mb IS NULL OR max_ram_mb >= 0),
    max_disk_gb INTEGER CHECK (max_disk_gb IS NULL OR max_disk_gb >= 0),
    updated_by  TEXT NOT NULL DEFAULT '',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
