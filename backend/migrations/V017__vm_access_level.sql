-- V017: tingkat akses per penugasan VM.
--   full = Connect (SSH/RDP) dan Open Web, seperti sebelumnya
--   web  = hanya lihat status, Open Web, dan Helpdesk; tanpa Connect, kredensial, power, dan snapshot
-- Data lama otomatis 'full'.
ALTER TABLE vm_assignments  ADD COLUMN IF NOT EXISTS access TEXT NOT NULL DEFAULT 'full' CHECK (access IN ('full', 'web'));
ALTER TABLE group_vm_access ADD COLUMN IF NOT EXISTS access TEXT NOT NULL DEFAULT 'full' CHECK (access IN ('full', 'web'));
