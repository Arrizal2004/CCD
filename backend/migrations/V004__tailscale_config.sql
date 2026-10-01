-- V004: Tailscale API credentials configurable via dashboard (per deployment) instead of only
-- TAILSCALE_TAILNET/TAILSCALE_API_KEY in .env. Single-row table (one tailnet per deployment).
-- api_key_enc is Fernet-encrypted at rest (same scheme as proxmox_instances.token_secret_enc).

CREATE TABLE IF NOT EXISTS tailscale_config (
    id          SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    tailnet     TEXT NOT NULL DEFAULT '-',   -- "-" = the tailnet that owns the API key
    api_key_enc TEXT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
