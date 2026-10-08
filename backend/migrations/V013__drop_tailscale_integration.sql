-- V013: integrasi API Tailscale di dashboard (panel ACL/device, kunci API di database) dihapus.
-- Tailscale tetap dipakai, tetapi dipasang di OS server dan host Proxmox, bukan dikelola dari dashboard.
-- Tabel dan kolom dari V004 dan V005 tidak dipakai lagi.
DROP TABLE IF EXISTS tailscale_config;
ALTER TABLE users DROP COLUMN IF EXISTS tailscale_login;
