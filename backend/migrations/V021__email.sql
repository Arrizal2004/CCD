-- V021: email keluar (SMTP) dan reset password lewat tautan email.
-- Antrean email keluar. Dikirim worker di latar belakang dengan percobaan ulang (services/mailer.py).
CREATE TABLE IF NOT EXISTS email_outbox (
    id              BIGSERIAL PRIMARY KEY,
    kind            TEXT NOT NULL,                 -- jenis email: lease, password_reset, test
    to_address      TEXT NOT NULL,
    subject         TEXT NOT NULL,
    body_text       TEXT NOT NULL,
    body_html       TEXT,
    status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sent', 'failed')),
    attempts        INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_error      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sent_at         TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_email_outbox_due ON email_outbox (next_attempt_at) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_email_outbox_created ON email_outbox (created_at DESC);

-- Peringatan masa sewa dikirim sekali per batas sewa: kolom ini menyimpan batas yang sudah diperingatkan,
-- jadi kalau masa sewa diperpanjang, peringatan berikutnya dikirim lagi.
ALTER TABLE vms ADD COLUMN IF NOT EXISTS lease_warned_for TIMESTAMPTZ;

-- Hanya hash token yang disimpan; token aslinya hanya ada di email.
CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id          BIGSERIAL PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash  TEXT NOT NULL UNIQUE,
    client_ip   TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at  TIMESTAMPTZ NOT NULL,
    used_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_password_reset_user ON password_reset_tokens (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_password_reset_ip ON password_reset_tokens (client_ip, created_at DESC);
