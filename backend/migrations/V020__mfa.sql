-- V020: 2FA TOTP + kode pemulihan, dan app_settings (pengaturan fitur bernilai JSON: kebijakan MFA, SMTP, kuota).
CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,
    data       JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_by TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- mfa_secret_enc: rahasia TOTP terenkripsi (NULL = MFA mati). mfa_last_step: langkah 30 detik terakhir yang
-- dipakai, supaya kode yang sama tidak bisa dipakai dua kali.
ALTER TABLE users ADD COLUMN IF NOT EXISTS mfa_secret_enc TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS mfa_enabled_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS mfa_last_step BIGINT;

CREATE TABLE IF NOT EXISTS mfa_recovery_codes (
    id        SERIAL PRIMARY KEY,
    user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code_hash TEXT NOT NULL,
    used_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_mfa_recovery_user ON mfa_recovery_codes (user_id);
