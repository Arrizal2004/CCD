-- V006: CCDID, nomor VM yang unik di seluruh dashboard.
-- VMID Proxmox hanya unik di dalam satu Proxmox, jadi dua Proxmox bisa sama-sama punya VM 101.
-- Mahasiswa dan tiket memakai CCDID supaya satu nomor selalu menunjuk satu VM. Nomor tidak
-- dipakai ulang: VM yang dihapus lalu dibuat lagi dengan VMID sama mendapat CCDID baru.

ALTER TABLE vms ADD COLUMN IF NOT EXISTS ccd_id INTEGER;

-- VM yang sudah tercatat diberi nomor urut sesuai kapan pertama kali terlihat.
UPDATE vms v SET ccd_id = o.rn
FROM (SELECT vm_id, host_name, row_number() OVER (ORDER BY first_seen, host_name, vm_id) AS rn FROM vms) o
WHERE v.vm_id = o.vm_id AND v.host_name = o.host_name AND v.ccd_id IS NULL;

CREATE SEQUENCE IF NOT EXISTS vms_ccd_id_seq OWNED BY vms.ccd_id;
SELECT setval('vms_ccd_id_seq', COALESCE((SELECT max(ccd_id) FROM vms), 0) + 1, false);
ALTER TABLE vms ALTER COLUMN ccd_id SET DEFAULT nextval('vms_ccd_id_seq');
ALTER TABLE vms ALTER COLUMN ccd_id SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_vms_ccd_id ON vms (ccd_id);

-- Tiket menyimpan CCDID-nya sendiri, supaya tetap terbaca walaupun VM-nya sudah dihapus.
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS ccd_id INTEGER;
UPDATE tickets t SET ccd_id = v.ccd_id FROM vms v
WHERE t.ccd_id IS NULL AND t.vm_id = v.vm_id AND t.host_name = v.host_name;
