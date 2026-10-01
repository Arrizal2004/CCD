-- V002: Soft delete for users and vm_assignments
-- Hard DELETE loses audit history; soft delete sets deleted_at instead.

ALTER TABLE users          ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE vm_assignments ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;

-- Replace the hard UNIQUE constraint with a partial unique index so that
-- a soft-deleted assignment does not block re-assigning the same VM later.
ALTER TABLE vm_assignments
    DROP CONSTRAINT IF EXISTS vm_assignments_user_id_vm_id_host_name_key;

CREATE UNIQUE INDEX IF NOT EXISTS uq_vm_assignments_active
    ON vm_assignments (user_id, vm_id, host_name)
    WHERE deleted_at IS NULL;
