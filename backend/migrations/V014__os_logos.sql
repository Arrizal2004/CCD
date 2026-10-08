-- V014: logo untuk tiap pilihan OS di Infra Request. Kuncinya nama OS huruf kecil, supaya logo tetap
-- terpasang walau penulisan huruf besar/kecil namanya diubah. Dihapus kalau OS-nya dihapus dari daftar.
CREATE TABLE IF NOT EXISTS os_logos (
    key          TEXT PRIMARY KEY,
    content_type TEXT NOT NULL,
    data         BYTEA NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
