-- V007: masa berlaku akun dan masa sewa VM.
-- users.expires_at: setelah lewat, akun tidak bisa login dan token lamanya ditolak.
-- users.expiry_enforced_at: kapan efek kedaluwarsa (putus sesi, nonaktif di Guacamole) dijalankan,
--   supaya hanya sekali; dikosongkan lagi saat masa berlakunya diubah.
-- vms.lease_until: batas masa sewa VM; setelah lewat, VM dimatikan sekali dan mahasiswa tidak bisa
--   menyalakannya lagi sampai admin memperpanjang. vms.lease_enforced_at mencatat kapan dimatikan.
ALTER TABLE users ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS expiry_enforced_at TIMESTAMPTZ;
ALTER TABLE vms ADD COLUMN IF NOT EXISTS lease_until TIMESTAMPTZ;
ALTER TABLE vms ADD COLUMN IF NOT EXISTS lease_enforced_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_users_expires ON users (expires_at) WHERE expires_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_vms_lease ON vms (lease_until) WHERE lease_until IS NOT NULL;
