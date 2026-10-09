-- V019: Proxmox yang boleh dikelola setiap sysadmin. Superadmin selalu boleh semuanya.
-- Sysadmin tanpa baris di sini tidak melihat Proxmox apa pun.
CREATE TABLE IF NOT EXISTS user_instances (
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    instance    TEXT    NOT NULL REFERENCES proxmox_instances(label) ON DELETE CASCADE,
    assigned_by TEXT    NOT NULL DEFAULT '',
    assigned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, instance)
);
CREATE INDEX IF NOT EXISTS idx_user_instances_instance ON user_instances (instance);

-- Sysadmin yang sudah ada memegang semua Proxmox yang ada sekarang, jadi tidak ada yang kehilangan akses
-- saat fitur ini terpasang. Superadmin yang menyempitkannya dari halaman Users.
INSERT INTO user_instances (user_id, instance, assigned_by)
SELECT u.id, i.label, 'migrasi V019'
FROM users u CROSS JOIN proxmox_instances i
WHERE u.role = 'sysadmin' AND u.deleted_at IS NULL
ON CONFLICT DO NOTHING;
