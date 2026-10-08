import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../api', () => ({
    fetchProxmoxInstances: vi.fn(),
    getProxmoxSshUrl: vi.fn(),
    appendGuacToken: vi.fn(url => url + '?token=t'),
    applyGuacTouchInputDefault: vi.fn(),
    createProxmoxInstance: vi.fn(),
    updateProxmoxInstance: vi.fn(),
    deleteProxmoxInstance: vi.fn(),
}));

import * as api from '../api';
import { setLanguage } from '../i18n';
import ProxmoxInstancesPanel from '../components/ProxmoxInstancesPanel';

beforeEach(() => {
    setLanguage('id');
    vi.mocked(api.fetchProxmoxInstances).mockResolvedValue([
        { label: 'kampus', host: '10.0.0.5:8006', token_id: 'root@pam!ccd', verify_ssl: false },
    ]);
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe('Instance Proxmox', () => {
    it('sysadmin hanya bisa menambah: tombol ubah dan hapus tidak tampil', async () => {
        render(<ProxmoxInstancesPanel />);
        await screen.findByText('kampus');
        expect(screen.getByRole('button', { name: '+ Tambah Instance' })).toBeTruthy();
        expect(screen.queryByRole('button', { name: 'Ubah' })).toBeNull();
        expect(screen.queryByRole('button', { name: 'Hapus' })).toBeNull();
        expect(document.body.textContent).toContain('Hanya superadmin yang dapat mengubah atau menghapus instance.');
    });

    it('superadmin melihat tombol ubah dan hapus', async () => {
        render(<ProxmoxInstancesPanel canModify />);
        await screen.findByText('kampus');
        expect(screen.getByRole('button', { name: 'Ubah' })).toBeTruthy();
        expect(screen.getByRole('button', { name: 'Hapus' })).toBeTruthy();
        expect(document.body.textContent).not.toContain('Hanya superadmin yang dapat mengubah');
    });
});

describe('SSH ke host Proxmox', () => {
    it('semua admin melihat tombol Terminal SSH, yang membuka Guacamole di tab baru dengan token', async () => {
        vi.mocked(api.getProxmoxSshUrl).mockResolvedValue({ url: '/guacamole/#/client/abc', hostname: '10.0.0.5', port: 22 });
        const open = vi.spyOn(window, 'open').mockReturnValue(null);
        render(<ProxmoxInstancesPanel />);                                     // sysadmin: tanpa canModify
        await screen.findByText('kampus');
        expect(document.body.textContent).toContain('Username dan password diminta di sana setiap kali dan tidak disimpan');
        fireEvent.click(screen.getByRole('button', { name: 'Terminal SSH' }));
        await waitFor(() => expect(open).toHaveBeenCalledWith('/guacamole/#/client/abc?token=t', '_blank'));
        expect(api.getProxmoxSshUrl).toHaveBeenCalledWith('kampus');
        open.mockRestore();
    });

    it('menampilkan alasan dari server kalau gagal dan tidak membuka tab', async () => {
        vi.mocked(api.getProxmoxSshUrl).mockRejectedValue({ response: { data: { detail: 'Gagal menyiapkan koneksi SSH di Guacamole' } } });
        const open = vi.spyOn(window, 'open').mockReturnValue(null);
        render(<ProxmoxInstancesPanel canModify />);
        await screen.findByText('kampus');
        fireEvent.click(screen.getByRole('button', { name: 'Terminal SSH' }));
        await screen.findByText(/Gagal menyiapkan koneksi SSH di Guacamole/);
        expect(open).not.toHaveBeenCalled();
        open.mockRestore();
    });
});
