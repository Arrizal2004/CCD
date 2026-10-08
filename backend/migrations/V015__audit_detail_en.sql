-- V015: versi Inggris dari detail Activity Log. Catatan baru menyimpan dua bahasa (services/audit.both);
-- tampilan dan ekspor memilih sesuai bahasa admin. Catatan lama diterjemahkan di migrasi V016.
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS detail_en TEXT;
ALTER TABLE ticket_messages ADD COLUMN IF NOT EXISTS message_en TEXT;
