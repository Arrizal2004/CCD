-- V018: riwayat Open Web per pengguna. Alamat lengkap disimpan supaya sesi yang masih berlaku bisa dibuka
-- lagi dan sesi yang sudah habis bisa dibuka baru dengan alamat yang sama.
ALTER TABLE openweb_sessions ADD COLUMN IF NOT EXISTS url TEXT;
CREATE INDEX IF NOT EXISTS idx_openweb_sessions_uid ON openweb_sessions (user_id, created_at DESC);
