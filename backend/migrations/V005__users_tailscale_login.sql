-- V005: map dashboard admins to their Tailscale login, so the network policy's admin group
-- (group:ccd-admins) can be generated from dashboard RBAC (services/tailscale_policy.py).
ALTER TABLE users ADD COLUMN IF NOT EXISTS tailscale_login TEXT;
