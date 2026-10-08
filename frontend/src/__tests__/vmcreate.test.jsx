import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../api', () => ({
    fetchGroups: vi.fn(), fetchProxmoxTemplates: vi.fn(), fetchNetworks: vi.fn(),
    previewVmBatch: vi.fn(), createVmBatch: vi.fn(), fetchVmBatches: vi.fn(), fetchVmBatch: vi.fn(),
    retryVmBatch: vi.fn(), downloadVmBatchCsv: vi.fn(),
    fetchProxmoxInstances: vi.fn(), fetchProxmoxNodes: vi.fn(), createProxmoxVm: vi.fn(),
}));

import * as api from '../api';
import BulkVmModal from '../components/BulkVmModal';
import CreateVmModal from '../components/CreateVmModal';

const TEMPLATES = [
    { vmid: 102, name: 'Template-Opensuse', cores: 1, memory_mb: 1024, disk_gb: 10, cloudinit: true, bridge: 'vmbr0' },
    { vmid: 100, name: 'Template-Ubuntu', cores: 2, memory_mb: 2048, disk_gb: 20, cloudinit: true, bridge: 'vmbr0' },
];

// jsdom tidak punya matchMedia (dipakai useIsMobile): anggap layar desktop.
beforeAll(() => {
    window.matchMedia ||= () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe('BulkVmModal', () => {
    it('rencana tidak mencentang nama bentrok, lalu progres memuat ulang daftar VM sekali', async () => {
        vi.mocked(api.fetchGroups).mockResolvedValue([{ id: 5, name: 'TKJ 12A', member_count: 2 }]);
        vi.mocked(api.fetchProxmoxTemplates).mockResolvedValue(TEMPLATES);
        vi.mocked(api.fetchNetworks).mockResolvedValue({ instances: [] });
        vi.mocked(api.fetchVmBatches).mockResolvedValue([]);
        vi.mocked(api.previewVmBatch).mockResolvedValue({
            group: { id: 5, name: 'TKJ 12A' }, prefix: 'tkj-12a', count: 2, warnings: ['1 nama VM bentrok'],
            template: { vmid: 102, name: 'Template-Opensuse', memory_mb: 1024 }, node_free_mb: 8000, free_ips: null,
            items: [
                { user_id: 1, username: 'ani', full_name: 'Ani', vm_name: 'tkj-12a-ani', os_username: 'ani', conflict: null },
                { user_id: 2, username: 'budi', full_name: 'Budi', vm_name: 'tkj-12a-budi', os_username: 'budi', conflict: 'Nama VM sudah dipakai' },
            ],
        });
        const running = { id: 9, status: 'running', group_name: 'TKJ 12A', counts: { pending: 1, creating: 0, done: 0, failed: 0 },
            items: [{ id: 1, username: 'ani', full_name: 'Ani', vm_name: 'tkj-12a-ani', status: 'pending' }] };
        vi.mocked(api.createVmBatch).mockResolvedValue(running);
        vi.mocked(api.fetchVmBatch)
            .mockResolvedValueOnce(running)
            .mockResolvedValue({ ...running, status: 'done', counts: { pending: 0, creating: 0, done: 1, failed: 0 },
                items: [{ ...running.items[0], status: 'done', vmid: 120, ip: '192.168.111.2' }] });
        const onChanged = vi.fn();
        render(<BulkVmModal instance="pve1" node="n1" onClose={() => {}} onChanged={onChanged} />);

        fireEvent.change(await screen.findByDisplayValue('— pilih grup —'), { target: { value: '5' } });
        fireEvent.click(screen.getByRole('button', { name: 'Lihat rencana' }));
        expect(await screen.findByText('⚠ 1 nama VM bentrok')).toBeTruthy();
        expect(screen.getByLabelText('Pilih ani').checked).toBe(true);
        expect(screen.getByLabelText('Pilih budi').checked).toBe(false);          // bentrok: tidak dicentang

        fireEvent.click(screen.getByRole('button', { name: 'Buat 1 VM' }));
        await waitFor(() => expect(api.createVmBatch).toHaveBeenCalled());
        expect(vi.mocked(api.createVmBatch).mock.calls[0][0]).toMatchObject({ group_id: 5, template_vmid: 102, user_ids: [1], start: false });
        expect(await screen.findByText('192.168.111.2', {}, { timeout: 5000 })).toBeTruthy();
        await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    }, 10000);
});

describe('CreateVmModal dari request VPS', () => {
    it('memilih Proxmox, template sesuai OS, dan spek dari permintaan', async () => {
        vi.mocked(api.fetchProxmoxInstances).mockResolvedValue([{ label: 'pve1' }]);
        vi.mocked(api.fetchProxmoxNodes).mockResolvedValue([{ node: 'n1' }, { node: 'n2' }]);
        vi.mocked(api.fetchProxmoxTemplates).mockResolvedValue(TEMPLATES);
        vi.mocked(api.fetchNetworks).mockResolvedValue({ instances: [] });
        vi.mocked(api.createProxmoxVm).mockResolvedValue({ vmid: 130, name: 'vps-ani', clone: 'linked', static_ip: null, agent_ip: '172.16.111.40', connect_ready: true });
        const onCreated = vi.fn();
        render(<CreateVmModal initial={{ name: 'vps-ani', username: 'ani', password: 'Abcd-efgh-2345', cores: 4, memory_mb: 8192, disk_gb: 15, os: 'Ubuntu 24.04', note: 'Permintaan Ani' }}
            onClose={() => {}} onCreated={onCreated} />);

        expect(await screen.findByDisplayValue('pve1 / n1')).toBeTruthy();
        expect(await screen.findByDisplayValue('Template-Ubuntu (100)')).toBeTruthy();   // cocok dengan OS
        expect(screen.getByDisplayValue('4')).toBeTruthy();
        expect(screen.getByDisplayValue('8192')).toBeTruthy();
        expect(screen.getByDisplayValue('20')).toBeTruthy();                              // tidak lebih kecil dari template
        expect(screen.getByDisplayValue('Abcd-efgh-2345').type).toBe('text');
        expect(screen.getByText('Permintaan Ani')).toBeTruthy();

        fireEvent.change(screen.getByDisplayValue('pve1 / n1'), { target: { value: 'pve1|n2' } });
        await waitFor(() => expect(api.fetchProxmoxTemplates).toHaveBeenLastCalledWith('pve1', 'n2'));
        await screen.findByDisplayValue('Template-Ubuntu (100)');
        fireEvent.change(screen.getByPlaceholderText('192.168.1.50/24'), { target: { value: '172.16.111.40/24' } });
        fireEvent.change(screen.getByPlaceholderText('192.168.1.1'), { target: { value: '172.16.111.1' } });
        fireEvent.click(screen.getByRole('button', { name: 'Create VM' }));
        await waitFor(() => expect(onCreated).toHaveBeenCalled());
        expect(api.createProxmoxVm.mock.calls[0].slice(0, 2)).toEqual(['pve1', 'n2']);
        expect(onCreated.mock.calls[0][1]).toEqual({ instance: 'pve1', node: 'n2' });
    });
});
