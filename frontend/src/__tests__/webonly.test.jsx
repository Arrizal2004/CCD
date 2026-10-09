import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

vi.mock('../api', () => ({
    fetchMyProxmoxVms: vi.fn(),
    fetchSshConfig: vi.fn(),
    fetchMyAssignedVmids: vi.fn(),
    fetchProxmoxInstances: vi.fn(),
    proxmoxVmAction: vi.fn(),
    getGuacUrl: vi.fn(),
    assignVm: vi.fn(),
    fetchUsers: vi.fn(),
    fetchUserAssignments: vi.fn(),
    fetchVmOsAccounts: vi.fn(),
    removeAssignment: vi.fn(),
    upsertVmOsAccount: vi.fn(),
}));

import * as api from '../api';
import { setLanguage } from '../i18n';
import ProxmoxPage from '../pages/ProxmoxPage';
import ProxmoxAssignmentsPanel from '../components/ProxmoxAssignmentsPanel';

const vm = (over) => ({
    instance: 'kampus', node: 'pve', status: 'running', cpus: 2, mem: 1e9, maxmem: 2e9, uptime: 60,
    lease_until: null, access: 'full', ...over,
});

function renderPage() {
    return render(
        <MemoryRouter initialEntries={['/servers']}>
            <Routes>
                <Route path="/servers" element={<ProxmoxPage currentUser={{ role: 'student' }} />} />
                <Route path="/openweb" element={<div>halaman-open-web</div>} />
            </Routes>
        </MemoryRouter>,
    );
}

beforeEach(() => {
    setLanguage('id');
    window.matchMedia = vi.fn().mockReturnValue({
        matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {},
    });
    vi.mocked(api.fetchSshConfig).mockResolvedValue({ enabled: false });
    vi.mocked(api.fetchMyAssignedVmids).mockResolvedValue({ vmids: [], all: false });
    vi.mocked(api.fetchMyProxmoxVms).mockResolvedValue([
        vm({ vmid: 101, ccd_id: 1, name: 'simulasi', access: 'web', manual_ip: '10.9.9.9' }),
        vm({ vmid: 102, ccd_id: 2, name: 'praktikum', access: 'full', manual_ip: '10.9.9.10' }),
    ]);
});
afterEach(() => { cleanup(); vi.clearAllMocks(); try { localStorage.clear(); } catch { /* tidak ada storage */ } });

describe('VM dengan akses Hanya Open Web (mahasiswa)', () => {
    it('hanya Open Web: tanpa Connect, power, dan snapshot; VM dengan akses penuh tetap lengkap', async () => {
        renderPage();
        await screen.findAllByText('simulasi');
        const rows = screen.getAllByRole('row');
        const web = rows.find(r => r.textContent.includes('simulasi'));
        const full = rows.find(r => r.textContent.includes('praktikum'));
        const names = (row) => [...row.querySelectorAll('button')].map(b => b.textContent.trim());
        expect(names(web)).toContain('Open Web');
        for (const label of ['Connect', 'Snapshots', 'Stop', 'Reboot']) expect(names(web)).not.toContain(label);
        expect(names(full)).toContain('Connect');
        expect(names(full)).not.toContain('Open Web');
        expect(names(full).length).toBeGreaterThan(names(web).length);
    });

    it('Open Web mengisi alamat VM dan membuka halaman Open Web', async () => {
        renderPage();
        await screen.findAllByText('simulasi');
        const web = screen.getAllByRole('row').find(r => r.textContent.includes('simulasi'));
        fireEvent.click([...web.querySelectorAll('button')].find(b => b.textContent.trim() === 'Open Web'));
        await screen.findByText('halaman-open-web');
        expect(localStorage.getItem('ccd-openweb-url')).toBe('http://10.9.9.9');
    });
});

describe('Penugasan VM oleh admin', () => {
    it('admin bisa menugaskan Hanya Open Web, dan penugasan itu ditandai', async () => {
        vi.mocked(api.fetchUsers).mockResolvedValue([{ id: 7, username: 'mhs1', full_name: 'Mahasiswa Satu', role: 'student' }]);
        vi.mocked(api.fetchVmOsAccounts).mockResolvedValue([]);
        vi.mocked(api.fetchUserAssignments).mockResolvedValue([]);
        vi.mocked(api.assignVm).mockResolvedValue({ status: 'assigned' });
        render(<ProxmoxAssignmentsPanel hostName="kampus__pve" vmid={101} vmName="simulasi" />);
        await screen.findByText('Mahasiswa Satu');
        fireEvent.click(screen.getByRole('button', { name: 'Assign' }));
        fireEvent.click(await screen.findByRole('button', { name: 'Hanya Open Web' }));
        await waitFor(() => expect(api.assignVm).toHaveBeenCalledWith(7, '101', 'kampus__pve', null, 'simulasi', 'web'));
        await screen.findByText('hanya Open Web');
    });
});
