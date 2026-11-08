import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../api', () => ({
    fetchVmSchedules: vi.fn(), createVmSchedule: vi.fn(), updateVmSchedule: vi.fn(), deleteVmSchedule: vi.fn(),
    setVmLock: vi.fn(), deleteProxmoxVm: vi.fn(),
}));

import * as api from '../api';
import VmSchedulePanel from '../components/VmSchedulePanel';
import VmLockPanel from '../components/VmLockPanel';
import VmDeletePanel from '../components/VmDeletePanel';

afterEach(() => { cleanup(); vi.clearAllMocks(); });

const ROW = { id: 5, action: 'start', days: [1, 2, 3, 4, 5], time: '07:30', enabled: true, note: '', keep: 3, last_run_at: null };

describe('VmSchedulePanel', () => {
    it('menampilkan jadwal dan zona waktu, lalu menambah jadwal baru dengan hari kerja bawaan', async () => {
        vi.mocked(api.fetchVmSchedules).mockResolvedValue({ timezone: 'Asia/Jakarta', schedules: [ROW] });
        vi.mocked(api.createVmSchedule).mockResolvedValue({});
        render(<VmSchedulePanel instance="lab" node="pve" vmid={101} />);
        expect(await screen.findByText('07:30')).toBeTruthy();
        expect(screen.getByText(/Asia\/Jakarta/)).toBeTruthy();

        fireEvent.change(screen.getByLabelText('Jam'), { target: { value: '17:00' } });
        fireEvent.change(screen.getByLabelText('Aksi'), { target: { value: 'shutdown' } });
        fireEvent.click(screen.getByText('Simpan jadwal'));
        await waitFor(() => expect(api.createVmSchedule).toHaveBeenCalledWith('lab', 'pve', 101,
            expect.objectContaining({ action: 'shutdown', time: '17:00', days: [1, 2, 3, 4, 5] })));
    });

    it('mematikan dan menghapus jadwal', async () => {
        vi.mocked(api.fetchVmSchedules).mockResolvedValue({ timezone: 'Asia/Jakarta', schedules: [ROW] });
        vi.mocked(api.updateVmSchedule).mockResolvedValue({});
        vi.mocked(api.deleteVmSchedule).mockResolvedValue({});
        render(<VmSchedulePanel instance="lab" node="pve" vmid={101} />);
        fireEvent.click(await screen.findByLabelText('Aktif'));
        await waitFor(() => expect(api.updateVmSchedule).toHaveBeenCalledWith('lab', 'pve', 101, 5, expect.objectContaining({ enabled: false })));
        fireEvent.click(screen.getByText('Hapus'));
        await waitFor(() => expect(api.deleteVmSchedule).toHaveBeenCalledWith('lab', 'pve', 101, 5));
    });

    it('galat dari server ditampilkan', async () => {
        vi.mocked(api.fetchVmSchedules).mockResolvedValue({ timezone: 'UTC', schedules: [] });
        vi.mocked(api.createVmSchedule).mockRejectedValue({ response: { data: { detail: 'Maksimal 20 jadwal per VM' } } });
        render(<VmSchedulePanel instance="lab" node="pve" vmid={101} />);
        fireEvent.click(await screen.findByText('Simpan jadwal'));
        expect(await screen.findByRole('alert')).toBeTruthy();
        expect(screen.getByText(/Maksimal 20 jadwal/)).toBeTruthy();
    });
});

describe('VmLockPanel dan VmDeletePanel', () => {
    it('mengunci VM', async () => {
        vi.mocked(api.setVmLock).mockResolvedValue({ vmid: 101, locked: true });
        const onChanged = vi.fn();
        render(<VmLockPanel instance="lab" node="pve" vmid={101} vmName="vm1" locked={false} onChanged={onChanged} />);
        fireEvent.click(screen.getAllByText('Kunci VM')[1]);
        await waitFor(() => expect(api.setVmLock).toHaveBeenCalledWith('lab', 'pve', 101, true));
        expect(onChanged).toHaveBeenCalledWith(true);
    });

    it('membuka kunci meminta konfirmasi dulu', async () => {
        vi.mocked(api.setVmLock).mockResolvedValue({ vmid: 101, locked: false });
        const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
        render(<VmLockPanel instance="lab" node="pve" vmid={101} vmName="vm1" locked onChanged={() => {}} />);
        fireEvent.click(screen.getByText('Buka kunci'));
        expect(confirm).toHaveBeenCalled();
        expect(api.setVmLock).not.toHaveBeenCalled();
        confirm.mockReturnValue(true);
        fireEvent.click(screen.getByText('Buka kunci'));
        await waitFor(() => expect(api.setVmLock).toHaveBeenCalledWith('lab', 'pve', 101, false));
        confirm.mockRestore();
    });

    it('VM terkunci tidak bisa dihapus dari panel hapus', () => {
        render(<VmDeletePanel instance="lab" node="pve" vmid={101} vmName="vm1" locked onDeleted={() => {}} />);
        expect(screen.getByTestId('delete-locked')).toBeTruthy();
        expect(screen.queryByPlaceholderText(/vm1/)).toBeNull();
    });
});
