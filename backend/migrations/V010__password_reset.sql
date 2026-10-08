-- V010: reset password akun CCD oleh admin dan permintaan "Lupa password?" dari halaman login.
-- password_version naik setiap password diganti; token login yang dibuat dengan versi lama ditolak, jadi
-- semua sesi lama otomatis berakhir. must_change_password memaksa pengguna mengganti password sementara
-- dari admin sebelum bisa memakai dashboard.
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_version INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT FALSE;

-- Permintaan bantuan dari pengguna yang lupa password. Hanya dibuat untuk akun yang benar-benar ada;
-- respons ke pengirim selalu sama supaya tidak bisa dipakai menebak username.
CREATE TABLE IF NOT EXISTS password_help_requests (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    message     TEXT NOT NULL DEFAULT '',
    client_ip   TEXT,
    status      TEXT NOT NULL DEFAULT 'open',      -- open | done | dismissed
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    handled_by  TEXT,
    handled_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_password_help_open ON password_help_requests (status, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_password_help_open_user ON password_help_requests (user_id) WHERE status = 'open';
