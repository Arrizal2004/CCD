import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../api', () => ({
    fetchGroupLabVms: vi.fn(), startGroupReset: vi.fn(), fetchLabReset: vi.fn(),
    fetchExams: vi.fn(), createExam: vi.fn(), startExamNow: vi.fn(), endExamNow: vi.fn(), cancelExam: vi.fn(), deleteExam: vi.fn(),
    fetchUsageReport: vi.fn(), fetchUsageReportCsv: vi.fn(), fetchMyExam: vi.fn(),
    setVmBaseline: vi.fn(), removeVmBaseline: vi.fn(), resetVmLab: vi.fn(),
    fetchLabSettings: vi.fn(), saveLabSettings: vi.fn(),
}));

import * as api from '../api';
import { ExamsPanel, LabResetPanel, UsageReportPanel } from '../components/GroupLabPanel';
import VmLabPanel from '../components/VmLabPanel';
import ExamBanner from '../components/ExamBanner';
import LabSettingsCard from '../components/LabSettingsCard';

afterEach(() => { cleanup(); vi.clearAllMocks(); vi.restoreAllMocks(); });
const group = { id: 4, name: 'TI-2A' };

describe('LabResetPanel', () => {
    it('menampilkan status baseline, mereset kelas setelah konfirmasi, dan menampilkan hasil per VM', async () => {
        vi.spyOn(window, 'confirm').mockReturnValue(true);
        vi.mocked(api.fetchGroupLabVms).mockResolvedValue([
            { host_name: 'lab__pve', vm_id: '1', vm_name: 'lab-a', baseline: true },
            { host_name: 'lab__pve', vm_id: '2', vm_name: 'lab-b', baseline: false },
        ]);
        vi.mocked(api.startGroupReset).mockResolvedValue({ id: 9, total: 2 });
        vi.mocked(api.fetchLabReset).mockResolvedValue({
            id: 9, status: 'done', total: 2, ok: 1,
            items: [{ host_name: 'lab__pve', vm_id: '1', vm_name: 'lab-a', ok: true }, { host_name: 'lab__pve', vm_id: '2', vm_name: 'lab-b', ok: false, reason_text: 'belum punya baseline' }],
        });
        render(<LabResetPanel group={group} />);
        expect(await screen.findAllByTestId('lab-vm')).toHaveLength(2);
        expect(screen.getByText(/Baseline: Belum/)).toBeTruthy();
        fireEvent.click(screen.getByText('Reset semua VM kelas'));
        await waitFor(() => expect(api.startGroupReset).toHaveBeenCalledWith(4, true));
        expect(await screen.findByText('1 dari 2 VM berhasil di-reset')).toBeTruthy();
        expect(screen.getByText(/lab-b: belum punya baseline/)).toBeTruthy();
    });

    it('tidak mereset kalau konfirmasi ditolak', async () => {
        vi.spyOn(window, 'confirm').mockReturnValue(false);
        vi.mocked(api.fetchGroupLabVms).mockResolvedValue([{ host_name: 'lab__pve', vm_id: '1', vm_name: 'lab-a', baseline: true }]);
        render(<LabResetPanel group={group} />);
        fireEvent.click(await screen.findByText('Reset semua VM kelas'));
        expect(api.startGroupReset).not.toHaveBeenCalled();
    });
});

describe('ExamsPanel', () => {
    const exam = (o) => ({ id: 1, name: 'UTS Jaringan', status: 'scheduled', starts_at: '2026-12-01T01:00:00Z', ends_at: '2026-12-01T03:00:00Z', vm_count: 12, start_result: {}, end_result: {}, ...o });

    it('menjadwalkan ujian dengan pilihan yang dicentang', async () => {
        vi.mocked(api.fetchExams).mockResolvedValue([]);
        vi.mocked(api.createExam).mockResolvedValue({});
        render(<ExamsPanel group={group} />);
        fireEvent.change(await screen.findByLabelText('Nama ujian'), { target: { value: 'UAS' } });
        fireEvent.click(screen.getByLabelText('Tutup Open Web selama ujian'));            // dimatikan
        fireEvent.click(screen.getByText('Jadwalkan'));
        await waitFor(() => expect(api.createExam).toHaveBeenCalledWith(expect.objectContaining({
            name: 'UAS', group_id: 4, reset_at_start: true, start_vms: true, shutdown_at_end: true, block_open_web: false,
        })));
        const body = vi.mocked(api.createExam).mock.calls[0][0];
        expect(new Date(body.ends_at) > new Date(body.starts_at)).toBe(true);
    });

    it('tombol mengikuti status ujian, dan aksi berbahaya meminta konfirmasi', async () => {
        vi.spyOn(window, 'confirm').mockReturnValue(true);
        vi.mocked(api.fetchExams).mockResolvedValue([exam(), exam({ id: 2, name: 'Quiz', status: 'running', start_result: { reset: { total: 12, ok: 11 } } })]);
        vi.mocked(api.startExamNow).mockResolvedValue({});
        vi.mocked(api.endExamNow).mockResolvedValue({});
        render(<ExamsPanel group={group} />);
        const rows = await screen.findAllByTestId('exam-row');
        expect(rows).toHaveLength(2);
        expect(screen.getByText('Saat mulai: 11 dari 12 VM berhasil')).toBeTruthy();
        expect(screen.getAllByText('Mulai sekarang')).toHaveLength(1);                    // hanya untuk yang terjadwal
        expect(screen.getAllByText('Akhiri sekarang')).toHaveLength(1);                   // hanya untuk yang berjalan
        expect(screen.getAllByText('Hapus')).toHaveLength(1);                             // ujian berjalan tidak bisa dihapus
        fireEvent.click(screen.getByText('Mulai sekarang'));
        await waitFor(() => expect(api.startExamNow).toHaveBeenCalledWith(1));
        fireEvent.click(screen.getByText('Akhiri sekarang'));
        await waitFor(() => expect(api.endExamNow).toHaveBeenCalledWith(2));
    });

    it('galat dari server (mis. bentrok jadwal) ditampilkan', async () => {
        vi.mocked(api.fetchExams).mockResolvedValue([]);
        vi.mocked(api.createExam).mockRejectedValue({ response: { data: { detail: "Bentrok dengan ujian 'UTS'" } } });
        render(<ExamsPanel group={group} />);
        fireEvent.change(await screen.findByLabelText('Nama ujian'), { target: { value: 'X' } });
        fireEvent.click(screen.getByText('Jadwalkan'));
        expect(await screen.findByRole('alert')).toBeTruthy();
        expect(screen.getByText(/Bentrok/)).toBeTruthy();
    });
});

describe('UsageReportPanel', () => {
    const REPORT = {
        remote_available: true,
        summary: { students: 2, active_students: 1, participation_pct: 50, total_minutes: 90, avg_minutes_active: 90, total_sessions: 4 },
        students: [
            { user_id: 1, username: 'budi', full_name: 'Budi', logins: 3, remote_sessions: 2, remote_minutes: 60, web_sessions: 1, web_minutes: 20, ssh_sessions: 1, ssh_minutes: 10,
              total_minutes: 90, total_sessions: 4, active_days: 2, last_active: '2026-11-03T02:00:00Z', vms: ['lab-a'], active: true },
            { user_id: 2, username: 'sari', full_name: 'Sari', logins: 0, remote_sessions: 0, remote_minutes: 0, web_sessions: 0, web_minutes: 0, ssh_sessions: 0, ssh_minutes: 0,
              total_minutes: 0, total_sessions: 0, active_days: 0, last_active: null, vms: [], active: false },
        ],
    };

    it('menampilkan ringkasan dan tabel, dan mengunduh CSV', async () => {
        vi.mocked(api.fetchUsageReport).mockResolvedValue(REPORT);
        vi.mocked(api.fetchUsageReportCsv).mockResolvedValue({ blob: new Blob(['a,b']), filename: 'ccd-pemakaian.csv' });
        window.URL.createObjectURL = vi.fn(() => 'blob:x');
        window.URL.revokeObjectURL = vi.fn();
        render(<UsageReportPanel group={group} />);
        fireEvent.click(screen.getByText('Tampilkan'));
        expect(await screen.findAllByTestId('report-row')).toHaveLength(2);
        expect(screen.getByText('50%')).toBeTruthy();
        expect(screen.getByText('tidak pernah')).toBeTruthy();                            // Sari belum pernah aktif
        expect(screen.getByText('2 / 60')).toBeTruthy();
        fireEvent.click(screen.getByText('Unduh CSV'));
        await waitFor(() => expect(api.fetchUsageReportCsv).toHaveBeenCalledWith(4, expect.any(String), expect.any(String)));
    });

    it('memperingatkan kalau data Remote tidak tersedia', async () => {
        vi.mocked(api.fetchUsageReport).mockResolvedValue({ ...REPORT, remote_available: false });
        render(<UsageReportPanel group={group} />);
        fireEvent.click(screen.getByText('Tampilkan'));
        expect(await screen.findByText(/Data sesi Remote tidak tersedia/)).toBeTruthy();
    });
});

describe('VmLabPanel, ExamBanner, LabSettingsCard', () => {
    it('baseline dinonaktifkan saat VM menyala, dan reset meminta konfirmasi', async () => {
        vi.spyOn(window, 'confirm').mockReturnValue(true);
        vi.mocked(api.resetVmLab).mockResolvedValue({});
        render(<VmLabPanel instance="lab" node="pve" vmid={101} vmName="vm1" running />);
        expect(screen.getByText('Jadikan kondisi awal').disabled).toBe(true);
        fireEvent.click(screen.getByText('Reset ke kondisi awal'));
        await waitFor(() => expect(api.resetVmLab).toHaveBeenCalledWith('lab', 'pve', 101));
        expect(await screen.findByText(/dikembalikan ke kondisi awal/)).toBeTruthy();
    });

    it('spanduk ujian: berlangsung vs akan datang vs tidak ada', async () => {
        vi.mocked(api.fetchMyExam).mockResolvedValue({ exam: { name: 'UTS', starts_at: '2026-12-01T01:00:00Z', ends_at: '2026-12-01T03:00:00Z', live: true } });
        const onLive = vi.fn();
        const { unmount } = render(<ExamBanner onLiveChange={onLive} />);
        expect(await screen.findByTestId('exam-banner')).toBeTruthy();
        expect(screen.getByText(/sedang berlangsung/)).toBeTruthy();
        await waitFor(() => expect(onLive).toHaveBeenCalledWith(true));
        unmount();
        vi.mocked(api.fetchMyExam).mockResolvedValue({ exam: { name: 'UTS', starts_at: '2026-12-01T01:00:00Z', ends_at: '2026-12-01T03:00:00Z', live: false } });
        const again = render(<ExamBanner />);
        expect(await screen.findByText(/dimulai/)).toBeTruthy();
        again.unmount();
        vi.mocked(api.fetchMyExam).mockResolvedValue({ exam: null });
        render(<ExamBanner />);
        await waitFor(() => expect(api.fetchMyExam).toHaveBeenCalledTimes(3));
        expect(screen.queryByTestId('exam-banner')).toBeNull();
    });

    it('pengaturan reset oleh mahasiswa disimpan, dan dikembalikan kalau server menolak', async () => {
        vi.mocked(api.fetchLabSettings).mockResolvedValue({ students_can_reset: false });
        vi.mocked(api.saveLabSettings).mockResolvedValueOnce({}).mockRejectedValueOnce({ response: { data: { detail: 'Forbidden' } } });
        render(<LabSettingsCard />);
        const box = await screen.findByLabelText('Izinkan mahasiswa me-reset VM sendiri');
        fireEvent.click(box);
        await waitFor(() => expect(api.saveLabSettings).toHaveBeenCalledWith({ students_can_reset: true }));
        expect(await screen.findByText(/Pengaturan disimpan/)).toBeTruthy();
        fireEvent.click(box);
        expect(await screen.findByText(/Forbidden/)).toBeTruthy();
        expect(box.checked).toBe(true);                                                   // dikembalikan ke nilai sebelumnya
    });
});
