-- V008: switch (jaringan) yang dibuat dari dashboard.
-- proxmox_instances.net_pool: blok alamat untuk switch di Proxmox ini. Host Proxmox mengiklankan blok
--   ini sekali lewat Tailscale, jadi switch baru di dalamnya langsung terjangkau dari VPS.
-- proxmox_instances.sdn_zone: SDN zone tipe Simple tempat VNet switch dibuat (lihat ccd-net-setup.sh).
-- networks: satu baris per switch, yaitu satu VNet + subnet di Proxmox.
ALTER TABLE proxmox_instances ADD COLUMN IF NOT EXISTS net_pool TEXT;
ALTER TABLE proxmox_instances ADD COLUMN IF NOT EXISTS sdn_zone TEXT NOT NULL DEFAULT 'ccd';

CREATE TABLE IF NOT EXISTS networks (
    id          SERIAL PRIMARY KEY,
    instance    TEXT NOT NULL REFERENCES proxmox_instances(label) ON DELETE CASCADE,
    vnet        TEXT NOT NULL,                  -- ID VNet di Proxmox, sekaligus nama bridge-nya
    name        TEXT NOT NULL,
    cidr        TEXT NOT NULL,
    gateway     TEXT NOT NULL,                  -- alamat host Proxmox di switch ini
    snat        BOOLEAN NOT NULL DEFAULT TRUE,  -- internet lewat NAT host Proxmox
    created_by  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (instance, vnet),
    UNIQUE (instance, cidr),
    UNIQUE (instance, name)
);
