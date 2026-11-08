import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../api', () => ({
    fetchVmJobs: vi.fn(), retryVmJob: vi.fn(), deleteVmJob: vi.fn(), createVmJob: vi.fn(),
    fetchUserQuota: vi.fn(), saveUserQuota: vi.fn(), fetchGroupQuota: vi.fn(), saveGroupQuota: vi.fn(),
    fetchQuotaDefaults: vi.fn(), saveQuotaDefaults: vi.fn(),
    openVmConsole: vi.fn(), consoleWsUrl: vi.fn(() => 'ws://x/ws/console/s?token=t'),
}));

const rfbInstances = [];
vi.mock('@novnc/novnc', () => ({
    default: class RFB {
        constructor(el, url, opts) { this.el = el; this.url = url; this.opts = opts; this.handlers = {}; rfbInstances.push(this); }
        addEventListener(n, f) { this.handlers[n] = f; }
        disconnect() { this.disconnected = true; }
        focus() {}
        sendCtrlAltDel() { this.cad = true; }
    },
}));

import * as api from '../api';
import VmJobsPanel from '../components/VmJobsPanel';
import QuotaModal from '../components/QuotaModal';
import QuotaDefaultsCard from '../components/QuotaDefaultsCard';
import VmConsoleModal from '../components/VmConsoleModal';

afterEach(() => { cleanup(); vi.clearAllMocks(); rfbInstances.length = 0; });

const job = (o) => ({ id: 1, name: 'vm-a', instance: 'lab', node: 'pve', status: 'done', step: '', vmid: 105, error: '', created_by: 'dosen', can_retry: false, ...o });

describe('VmJobsPanel', () => {
    it('menampilkan proses beserta langkahnya, dan mengulang yang gagal', async () => {
        vi.mocked(api.fetchVmJobs).mockResolvedValue([
            job({ id: 3, name: 'vm-c', status: 'running', step: 'clone_wait', vmid: null }),
            job({ id: 2, name: 'vm-b', status: 'failed', error: 'Proxmox menolak', can_retry: true, vmid: null }),
            job({ id: 1 }),
        ]);
        vi.mocked(api.retryVmJob).mockResolvedValue({});
        render(<VmJobsPanel onClose={() => {}} />);
        expect(await screen.findByText('Menunggu salinan selesai')).toBeTruthy();
        expect(screen.getByText('Proxmox menolak')).toBeTruthy();
        expect(screen.getAllByTestId('job-row')).toHaveLength(3);
        fireEvent.click(screen.getByText('Ulangi'));
        await waitFor(() => expect(api.retryVmJob).toHaveBeenCalledWith(2));
    });

    it('menghapus proses selesai dari riwayat; proses berjalan tidak punya tombol hapus', async () => {
        vi.mocked(api.fetchVmJobs).mockResolvedValue([job({ id: 4, status: 'running', step: 'start', vmid: null }), job({ id: 1 })]);
        vi.mocked(api.deleteVmJob).mockResolvedValue({});
        render(<VmJobsPanel onClose={() => {}} />);
        await screen.findAllByTestId('job-row');
        const removes = screen.getAllByLabelText('Hapus dari riwayat');
        expect(removes).toHaveLength(1);
        fireEvent.click(removes[0]);
        await waitFor(() => expect(api.deleteVmJob).toHaveBeenCalledWith(1));
    });
});

describe('Kuota', () => {
    it('kuota pengguna: memuat penimpaan, menyimpan nilai, dan menampilkan batas yang berlaku', async () => {
        vi.mocked(api.fetchUserQuota).mockResolvedValue({
            override: { max_vms: 2, max_vcpu: null, max_ram_mb: null, max_disk_gb: null },
            limits: { max_vms: 2, max_vcpu: 0, max_ram_mb: 0, max_disk_gb: 0 }, source: { max_vms: 'user' },
            usage: { vms: 1, vcpu: 2, ram_mb: 2048, disk_gb: 20 },
        });
        vi.mocked(api.saveUserQuota).mockResolvedValue({});
        render(<QuotaModal kind="user" id={7} name="budi" onClose={() => {}} />);
        const vms = await screen.findByLabelText('Jumlah VM');
        expect(vms.value).toBe('2');
        expect(screen.getByText(/Dipakai: 1 VM · 2 vCPU/)).toBeTruthy();
        expect(screen.getByText(/dari pengguna/)).toBeTruthy();
        fireEvent.change(screen.getByLabelText('Total vCPU'), { target: { value: '4' } });
        fireEvent.click(screen.getByText('Simpan kuota'));
        await waitFor(() => expect(api.saveUserQuota).toHaveBeenCalledWith(7, expect.objectContaining({ max_vms: 2, max_vcpu: '4' })));
        expect(await screen.findByText(/Kuota disimpan/)).toBeTruthy();
    });

    it('kuota grup memakai endpoint grup', async () => {
        vi.mocked(api.fetchGroupQuota).mockResolvedValue({ max_vms: null, max_vcpu: null, max_ram_mb: null, max_disk_gb: null });
        vi.mocked(api.saveGroupQuota).mockResolvedValue({});
        render(<QuotaModal kind="group" id={3} name="TI-1A" onClose={() => {}} />);
        fireEvent.change(await screen.findByLabelText('Jumlah VM'), { target: { value: '3' } });
        fireEvent.click(screen.getByText('Simpan kuota'));
        await waitFor(() => expect(api.saveGroupQuota).toHaveBeenCalledWith(3, expect.objectContaining({ max_vms: '3' })));
    });

    it('galat server (mis. nilai tidak valid) ditampilkan', async () => {
        vi.mocked(api.fetchQuotaDefaults).mockResolvedValue({ max_vms: 0, max_vcpu: 0, max_ram_mb: 0, max_disk_gb: 0 });
        vi.mocked(api.saveQuotaDefaults).mockRejectedValue({ response: { data: { detail: "Nilai 'max_vms' harus bilangan bulat" } } });
        render(<QuotaDefaultsCard />);
        fireEvent.click(await screen.findByText('Simpan kuota'));
        expect(await screen.findByText(/harus bilangan bulat/)).toBeTruthy();
    });
});

describe('VmConsoleModal', () => {
    it('membuka sesi, menyambungkan noVNC dengan password sesi, dan memutus saat ditutup', async () => {
        vi.mocked(api.openVmConsole).mockResolvedValue({ session: 'sid', password: 'tiket', vm_name: 'vm1' });
        const onClose = vi.fn();
        const { unmount } = render(<VmConsoleModal instance="lab" node="pve" vmid={101} vmName="vm1" onClose={onClose} />);
        await waitFor(() => expect(rfbInstances).toHaveLength(1));
        expect(api.openVmConsole).toHaveBeenCalledWith('lab', 'pve', 101);
        expect(rfbInstances[0].opts.credentials.password).toBe('tiket');
        expect(rfbInstances[0].url).toContain('/ws/console/');
        expect(screen.getByTestId('console-state').textContent).toBe('Menyambung...');
        rfbInstances[0].handlers.connect();
        await waitFor(() => expect(screen.getByTestId('console-state').textContent).toBe('Tersambung'));
        unmount();
        expect(rfbInstances[0].disconnected).toBe(true);
    });

    it('galat dari server tampil dan bisa disambung ulang', async () => {
        vi.mocked(api.openVmConsole).mockRejectedValueOnce({ response: { data: { detail: 'Konsol hanya tersedia saat VM menyala' } } });
        vi.mocked(api.openVmConsole).mockResolvedValueOnce({ session: 's2', password: 'p2' });
        render(<VmConsoleModal instance="lab" node="pve" vmid={101} vmName="vm1" onClose={() => {}} />);
        expect(await screen.findByText(/hanya tersedia saat VM menyala/)).toBeTruthy();
        fireEvent.click(screen.getByText('Sambung ulang'));
        await waitFor(() => expect(rfbInstances).toHaveLength(1));
    });
});
