-- V011: satu Proxmox bisa punya beberapa blok alamat switch, misalnya 192.168.111.0/24 lalu ditambah
-- 192.168.112.0/24, supaya admin hanya mencadangkan rentang yang benar-benar dipakai. Setiap blok
-- diiklankan lewat Tailscale dan masuk aturan isolasi host oleh ccd-net-setup.sh.
-- Kolom net_pool (satu blok, V008) tidak dipakai lagi; isinya dipindahkan ke net_pools dan kolomnya
-- dibiarkan supaya versi sebelumnya masih bisa dijalankan kalau perlu kembali.
ALTER TABLE proxmox_instances ADD COLUMN IF NOT EXISTS net_pools TEXT[] NOT NULL DEFAULT '{}';
UPDATE proxmox_instances SET net_pools = ARRAY[net_pool]
WHERE net_pool IS NOT NULL AND cardinality(net_pools) = 0;
