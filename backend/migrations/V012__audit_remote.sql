-- V012: Audit & Remote. Indeks untuk filter Activity Log per aksi dan per akun (termasuk rekap login
-- gagal dan tampilan aktivitas satu pengguna), serta pencatatan admin yang memutus sesi SSH.
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_logs (action_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_username ON audit_logs (lower(username), created_at DESC);
CREATE INDEX IF NOT EXISTS idx_openweb_sessions_user ON openweb_sessions (lower(username), created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ssh_sessions_user ON ssh_sessions (lower(username), started_at DESC);
ALTER TABLE ssh_sessions ADD COLUMN IF NOT EXISTS killed_by TEXT;
