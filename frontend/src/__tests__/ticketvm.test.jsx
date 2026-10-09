import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../api', () => ({
    fetchTickets: vi.fn(),
    fetchTicket: vi.fn(),
    fetchSystemConfig: vi.fn(),
    updateTicketStatus: vi.fn(),
    replyTicket: vi.fn(),
    uploadTicketAttachment: vi.fn(),
    fetchMyProxmoxVms: vi.fn(),
    deleteTicket: vi.fn(),
}));
// Modal detail VM diganti tiruan: yang diuji adalah kapan ia dibuka dan dengan data apa.
vi.mock('../components/ProxmoxVmDetailModal', () => ({
    default: (p) => (
        <div data-testid="vm-modal">
            {`${p.instance}|${p.node}|${p.vmid}|${p.vmName}|${p.leaseUntil}|${p.zIndex}`}
            <button onClick={p.onLeaseChanged}>lease-berubah</button>
            <button onClick={p.onClose}>tutup-vm</button>
        </div>
    ),
}));

import * as api from '../api';
import { setLanguage } from '../i18n';
import TicketsPage from '../components/TicketsPage';

const ticket = (over = {}) => ({
    id: 5, ticket_number: 'TKT-0005', student_id: 9, student_name: 'Mahasiswa Satu', title: 'Minta tambah waktu VM',
    category: 'LEASE_EXTENSION', description: 'Mohon diperpanjang', status: 'OPEN', vm_id: '104', host_name: 'kampus__pve',
    ccd_id: 7, vm_snapshot: { vm_name: 'vm-simulasi', state: 'running' }, created_at: '2026-10-01T03:00:00Z',
    updated_at: '2026-10-01T03:00:00Z', closed_at: null, ...over,
});

beforeEach(() => {
    setLanguage('id');
    window.matchMedia = vi.fn().mockReturnValue({
        matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {},
    });
    vi.mocked(api.fetchSystemConfig).mockResolvedValue({});
    vi.mocked(api.fetchTickets).mockResolvedValue({ total: 1, items: [ticket()] });
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });

async function openThread(role, over) {
    vi.mocked(api.fetchTicket).mockResolvedValue({ ticket: ticket(over), messages: [] });
    render(<TicketsPage currentUser={{ id: 1, role }} />);
    fireEvent.click(await screen.findByText('Minta tambah waktu VM'));
    await screen.findByText('Mohon diperpanjang');
}

describe('Detail VM dari tiket', () => {
    it('admin membuka detail VM di atas tiket, dengan instance, node, VMID, nama, dan masa sewa dari tiket', async () => {
        await openThread('sysadmin', { vm_live: true, vm_lease_until: '2030-01-02T03:04:05+00:00' });
        expect(screen.queryByTestId('vm-modal')).toBeNull();
        fireEvent.click(screen.getByRole('button', { name: 'Buka detail VM' }));
        expect((await screen.findByTestId('vm-modal')).textContent).toContain('kampus|pve|104|vm-simulasi|2030-01-02T03:04:05+00:00|3100');
        expect(screen.getByText('Mohon diperpanjang')).toBeTruthy();                   // tiket tetap terbuka di bawahnya
        fireEvent.click(screen.getByRole('button', { name: 'tutup-vm' }));
        await waitFor(() => expect(screen.queryByTestId('vm-modal')).toBeNull());
        expect(screen.getByText('Mohon diperpanjang')).toBeTruthy();
    });

    it('setelah masa sewa diubah, tiket dimuat ulang', async () => {
        await openThread('superadmin', { vm_live: true, vm_lease_until: null });
        const before = vi.mocked(api.fetchTicket).mock.calls.length;
        fireEvent.click(screen.getByRole('button', { name: 'Buka detail VM' }));
        fireEvent.click(await screen.findByRole('button', { name: 'lease-berubah' }));
        await waitFor(() => expect(vi.mocked(api.fetchTicket).mock.calls.length).toBeGreaterThan(before));
    });

    it('VM yang sudah tidak ada: tombol diganti keterangan', async () => {
        await openThread('sysadmin', { vm_live: false, vm_lease_until: null });
        expect(screen.queryByRole('button', { name: 'Buka detail VM' })).toBeNull();
        expect(document.body.textContent).toContain('VM ini sudah tidak ada di dashboard');
    });

    it('mahasiswa tidak melihat tombol maupun keterangan itu', async () => {
        await openThread('student', {});
        expect(screen.queryByRole('button', { name: 'Buka detail VM' })).toBeNull();
        expect(document.body.textContent).not.toContain('VM ini sudah tidak ada di dashboard');
    });

    it('tiket tanpa VM tidak punya tombol', async () => {
        await openThread('sysadmin', { vm_id: null, host_name: null, ccd_id: null, vm_snapshot: null });
        expect(screen.queryByRole('button', { name: 'Buka detail VM' })).toBeNull();
    });
});
