-- V003: Multi-Proxmox support — instances configurable via dashboard instead of a single
-- fixed PROXMOX_HOST/TOKEN_ID/TOKEN_SECRET in .env.
-- token_secret_enc is Fernet-encrypted at rest (same scheme as vm_credentials.password_enc).

CREATE TABLE IF NOT EXISTS proxmox_instances (
    id               SERIAL PRIMARY KEY,
    label            TEXT NOT NULL UNIQUE,   -- short slug, e.g. "lab" — used as host_name prefix for VMs
    host             TEXT NOT NULL,          -- "ip:port", e.g. "192.168.1.10:8006"
    token_id         TEXT NOT NULL,          -- e.g. "root@pam!dashboard"
    token_secret_enc TEXT NOT NULL,
    verify_ssl       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
