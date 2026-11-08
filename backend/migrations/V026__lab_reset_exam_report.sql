-- V026: reset lab ke kondisi awal, mode ujian, dan (tanpa tabel) laporan pemakaian kelas.
-- Kondisi awal sebuah VM = snapshot ccd-baseline yang dibuat admin. Satu baris per VM mencatat bahwa baseline-nya ada.
CREATE TABLE IF NOT EXISTS vm_baselines (
    host_name  TEXT NOT NULL,                         -- <label>__<node>
    vm_id      TEXT NOT NULL,
    snapname   TEXT NOT NULL DEFAULT 'ccd-baseline',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (host_name, vm_id)
);

-- Satu eksekusi reset (satu VM, satu kelas, atau dari mode ujian). Hasil per VM disimpan di items.
CREATE TABLE IF NOT EXISTS lab_resets (
    id           SERIAL PRIMARY KEY,
    scope        TEXT NOT NULL CHECK (scope IN ('vm', 'group', 'exam')),
    group_id     INTEGER REFERENCES groups(id) ON DELETE SET NULL,
    exam_id      INTEGER,
    requested_by TEXT NOT NULL DEFAULT '',
    start_after  BOOLEAN NOT NULL DEFAULT TRUE,
    status       TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'done', 'failed')),
    items        JSONB NOT NULL DEFAULT '[]'::jsonb,  -- [{host_name, vm_id, vm_name, ok, started, reason}]
    started_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_lab_resets_started ON lab_resets (started_at DESC);

-- Ujian untuk satu kelas (grup). Selama berjalan, mahasiswa kelas itu hanya melihat dan membuka VM ujian
-- (VM yang di-assign ke anggota kelas dan VM milik grup), dan tidak bisa mengubah VM atau membuka Open Web.
CREATE TABLE IF NOT EXISTS exams (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    group_id        INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    starts_at       TIMESTAMPTZ NOT NULL,
    ends_at         TIMESTAMPTZ NOT NULL,
    reset_at_start  BOOLEAN NOT NULL DEFAULT TRUE,    -- kembalikan VM ke baseline saat ujian mulai
    start_vms       BOOLEAN NOT NULL DEFAULT TRUE,    -- nyalakan VM saat ujian mulai
    shutdown_at_end BOOLEAN NOT NULL DEFAULT TRUE,    -- matikan VM saat ujian selesai
    block_open_web  BOOLEAN NOT NULL DEFAULT TRUE,    -- tutup Open Web untuk kelas itu selama ujian
    status          TEXT NOT NULL DEFAULT 'scheduled' CHECK (status IN ('scheduled', 'running', 'finished', 'cancelled')),
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    start_result    JSONB NOT NULL DEFAULT '{}'::jsonb,
    end_result      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by      TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT exams_window CHECK (ends_at > starts_at)
);
CREATE INDEX IF NOT EXISTS idx_exams_group ON exams (group_id, starts_at DESC);
CREATE INDEX IF NOT EXISTS idx_exams_open ON exams (status, starts_at) WHERE status IN ('scheduled', 'running');
