import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../api', () => ({
    fetchSystemSettings: vi.fn(),
    saveSystemSettings: vi.fn(),
    fetchAuditStats: vi.fn(() => Promise.resolve({ rows: 1234, oldest: '2026-09-30T01:00:00Z', bytes: 335872, env_default: 180, effective_days: 180 })),
    uploadSystemLogo: vi.fn(),
    deleteSystemLogo: vi.fn(),
    fetchBranding: vi.fn(() => Promise.resolve({})),
    fetchSystemConfig: vi.fn(() => Promise.resolve({})),
}));

import * as api from '../api';
import SystemSettingsPage from '../components/SystemSettingsPage';

const SETTINGS = {
    name: 'Campus Cloud Dashboard', short_name: 'CCD', institution: '', tagline: '', registration_open: true,
    allowed_emails: [], accent_color: '', default_language: 'id', default_theme: 'dark',
    announcement: { text: '', level: 'info', starts_at: null, ends_at: null, show_on_login: false },
    default_vm_lease_days: null, default_account_days: null, ticket_categories: [{ key: 'OTHERS', label: '' }],
    vps_os_options: ['Ubuntu'], ssh_public_host: '', audit_retention_days: null, timezone: 'Asia/Jakarta', logo_version: null,
    ssh_env: { enabled: true, env_host: 'ssh.lama.example', port: 2222 },
};

afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe('SystemSettingsPage: alamat SSH', () => {
    it('kosong memakai nilai .env, dan alamat baru langsung terlihat di contoh perintah lalu disimpan', async () => {
        vi.mocked(api.fetchSystemSettings).mockResolvedValue(SETTINGS);
        vi.mocked(api.saveSystemSettings).mockImplementation(async (body) => ({ ...body, ssh_public_host: body.ssh_public_host.trim().toLowerCase() }));
        render(<SystemSettingsPage />);
        const field = await screen.findByLabelText('Alamat SSH untuk pengguna');
        expect(field.getAttribute('placeholder')).toBe('ssh.lama.example');
        expect(screen.getByText(/ssh -J tunnel@ssh.lama.example:2222/)).toBeTruthy();

        fireEvent.change(field, { target: { value: 'SSH.Baru.Example' } });
        expect(screen.getByText(/ssh -J tunnel@SSH.Baru.Example:2222/)).toBeTruthy();
        fireEvent.click(screen.getByRole('button', { name: 'Simpan pengaturan' }));
        await waitFor(() => expect(api.saveSystemSettings).toHaveBeenCalled());
        expect(api.saveSystemSettings.mock.calls[0][0].ssh_public_host).toBe('SSH.Baru.Example');
        await screen.findByText(/Pengaturan tersimpan/);
    });

    it('memberi tahu kalau bastion belum aktif', async () => {
        vi.mocked(api.fetchSystemSettings).mockResolvedValue({ ...SETTINGS, ssh_env: { enabled: false, env_host: '', port: 2222 } });
        render(<SystemSettingsPage />);
        expect(await screen.findByText(/Bastion SSH belum diaktifkan/)).toBeTruthy();
    });
});

describe('SystemSettingsPage: penyimpanan log audit', () => {
    it('menampilkan isi dan ukuran log, kosong memakai nilai .env, dan angka baru ikut disimpan', async () => {
        vi.mocked(api.fetchSystemSettings).mockResolvedValue(SETTINGS);
        vi.mocked(api.saveSystemSettings).mockImplementation(async (body) => body);
        render(<SystemSettingsPage />);
        const field = await screen.findByLabelText('Simpan log selama (hari)');
        await waitFor(() => expect(field.getAttribute('placeholder')).toBe('kosong = 180 (dari .env)'));
        expect(screen.getByText(/Saat ini tersimpan 1\.234 entri log sejak 30 Sep 2026, memakai 328 KB/)).toBeTruthy();

        fireEvent.change(field, { target: { value: '90' } });
        fireEvent.click(screen.getByRole('button', { name: 'Simpan pengaturan' }));
        await waitFor(() => expect(api.saveSystemSettings).toHaveBeenCalled());
        expect(api.saveSystemSettings.mock.calls[0][0].audit_retention_days).toBe(90);

        fireEvent.change(field, { target: { value: '' } });
        fireEvent.click(screen.getByRole('button', { name: 'Simpan pengaturan' }));
        await waitFor(() => expect(api.saveSystemSettings).toHaveBeenCalledTimes(2));
        expect(api.saveSystemSettings.mock.calls[1][0].audit_retention_days).toBeNull();
    });
});

describe('SystemSettingsPage: zona waktu', () => {
    it('Indonesia di urutan pertama, pratinjau jam ikut pilihan, dan zona yang dipilih disimpan', async () => {
        vi.mocked(api.fetchSystemSettings).mockResolvedValue(SETTINGS);
        vi.mocked(api.saveSystemSettings).mockImplementation(async (body) => body);
        render(<SystemSettingsPage />);
        const select = await screen.findByLabelText('Zona waktu dashboard');
        expect(select.value).toBe('Asia/Jakarta');
        const groups = [...select.querySelectorAll('optgroup')];
        expect(groups[0].label).toBe('Indonesia');
        expect([...groups[0].querySelectorAll('option')].map(o => o.value)).toEqual(['Asia/Jakarta', 'Asia/Makassar', 'Asia/Jayapura']);

        fireEvent.change(select, { target: { value: 'Asia/Makassar' } });
        expect(screen.getAllByRole('timer')[0].textContent).toMatch(/WITA/);
        fireEvent.click(screen.getByRole('button', { name: 'Simpan pengaturan' }));
        await waitFor(() => expect(api.saveSystemSettings).toHaveBeenCalled());
        expect(api.saveSystemSettings.mock.calls[0][0].timezone).toBe('Asia/Makassar');
    });

    it('tidak lagi memuat kategori tiket dan pilihan OS (diatur dari Helpdesk dan Infra Requests)', async () => {
        vi.mocked(api.fetchSystemSettings).mockResolvedValue(SETTINGS);
        render(<SystemSettingsPage />);
        await screen.findByLabelText('Zona waktu dashboard');
        expect(screen.queryByText('Kategori tiket Helpdesk')).toBeNull();
        expect(screen.queryByText('Pilihan OS di Infra Request')).toBeNull();
    });
});
