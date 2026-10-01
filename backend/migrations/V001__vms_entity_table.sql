-- V001: Dedicated entity table for VMs
-- Solves: vm_name previously only available in time-series vm_metrics_history,
-- causing UUID display when a VM had never been polled.

CREATE TABLE IF NOT EXISTS vms (
    vm_id      TEXT NOT NULL,
    host_name  TEXT NOT NULL,
    vm_name    TEXT NOT NULL,
    state      TEXT,
    first_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (vm_id, host_name)
);

-- Backfill from metrics history: take most-recent record per VM
INSERT INTO vms (vm_id, host_name, vm_name, state, first_seen, updated_at)
SELECT DISTINCT ON (vm_id, host_name)
    vm_id, host_name, vm_name, state,
    MIN(recorded_at) OVER (PARTITION BY vm_id, host_name) AS first_seen,
    recorded_at AS updated_at
FROM vm_metrics_history
ORDER BY vm_id, host_name, recorded_at DESC
ON CONFLICT (vm_id, host_name) DO UPDATE SET
    vm_name    = EXCLUDED.vm_name,
    state      = EXCLUDED.state,
    updated_at = EXCLUDED.updated_at;
