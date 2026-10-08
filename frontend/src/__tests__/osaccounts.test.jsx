import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../api', () => ({
    fetchVmOsAccounts: vi.fn(),
    upsertVmOsAccount: vi.fn(),
    deleteVmOsAccount: vi.fn(),
    resetVmPassword: vi.fn(),
}));

import * as api from '../api';
import VmOsAccountsPanel from '../components/VmOsAccountsPanel';

const HOST = 'lab__pve';

beforeEach(() => {
    vi.mocked(api.fetchVmOsAccounts).mockResolvedValue([{ id: 7, os_username: 'budi', has_password: true }]);
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe('VmOsAccountsPanel', () => {
    it('membuat user di dalam VM secara bawaan dan menampilkan password acak sekali', async () => {
        vi.mocked(api.upsertVmOsAccount).mockResolvedValue({ id: 8, os_username: 'sinta', created_in_vm: true, password: 'Abcd-efgh-2345' });
        render(<VmOsAccountsPanel hostName={HOST} vmid={501} />);
        await screen.findByText('budi');

        fireEvent.change(screen.getByLabelText('Username OS'), { target: { value: ' sinta ' } });
        fireEvent.click(screen.getByRole('button', { name: 'Tambah' }));

        await screen.findByText('Abcd-efgh-2345');
        expect(api.upsertVmOsAccount).toHaveBeenCalledWith(HOST, '501', { os_username: 'sinta', password: null, create_in_vm: true });
    });

    it('reset password memakai username akun dan menampilkan tempat yang ikut diperbarui', async () => {
        vi.mocked(api.resetVmPassword).mockResolvedValue({ username: 'budi', password: 'Wxyz-2345-abcd', updated: ['Akun OS'] });
        render(<VmOsAccountsPanel hostName={HOST} vmid={501} />);
        fireEvent.click(await screen.findByRole('button', { name: 'Reset password' }));
        fireEvent.click(screen.getAllByRole('button', { name: 'Reset password' }).at(-1));

        await screen.findByText('Wxyz-2345-abcd');
        expect(api.resetVmPassword).toHaveBeenCalledWith(HOST, '501', { username: 'budi', password: undefined });
        expect(screen.getByRole('status').textContent).toContain('Akun OS');
    });

    it('hapus dari dalam VM hanya kalau dicentang, dan pesan galat ditampilkan', async () => {
        vi.mocked(api.deleteVmOsAccount).mockRejectedValue({ response: { data: { detail: "User 'budi' masih login di dalam VM" } } });
        render(<VmOsAccountsPanel hostName={HOST} vmid={501} />);
        fireEvent.click(await screen.findByRole('button', { name: 'Hapus' }));
        fireEvent.click(screen.getByRole('checkbox', { name: /Hapus juga user ini/ }));
        fireEvent.click(screen.getByRole('button', { name: 'Hapus akun' }));

        await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('masih login'));
        expect(api.deleteVmOsAccount).toHaveBeenCalledWith(HOST, '501', 7, true);
    });
});
