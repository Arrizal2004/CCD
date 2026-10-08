import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

vi.mock('../api', () => ({
    fetchSshConfig: vi.fn(() => Promise.resolve({ enabled: true })),
    fetchAuditLogs: vi.fn(() => Promise.resolve({ total: 0, items: [] })),
    fetchAuditActions: vi.fn(() => Promise.resolve(['AUTH_LOGIN', 'USER_CREATE'])),
    fetchFailedLogins: vi.fn(),
    fetchRemoteSessions: vi.fn(() => Promise.resolve({ sessions: [] })),
    killRemoteSession: vi.fn(),
    fetchRemoteHistory: vi.fn(() => Promise.resolve({ total: 0, items: [] })),
    guacGrantAdmins: vi.fn(),
    fetchOpenWebSessions: vi.fn(() => Promise.resolve({ sessions: [] })),
    fetchOpenWebHistory: vi.fn(() => Promise.resolve({ total: 0, items: [] })),
    killOpenWebSession: vi.fn(),
    fetchSshSessions: vi.fn(() => Promise.resolve({ sessions: [] })),
    fetchSshHistory: vi.fn(() => Promise.resolve({ total: 0, items: [] })),
    killSshSession: vi.fn(),
    downloadAdminCsv: vi.fn(() => Promise.resolve('x.csv')),
}));

import * as api from '../api';
import { setLanguage } from '../i18n';
import AdminPanel from '../components/AdminPanel';

const SYSADMIN = { id: 1, username: 'sysadm', role: 'sysadmin' };
const LOG = { id: 7, timestamp: '2026-10-07T01:00:00Z', username: 'budi', user_role: 'student', action_type: 'AUTH_LOGIN',
    severity: 'INFO', detail: 'Login berhasil', target_name: '-', client_ip: '10.0.0.5' };

beforeEach(() => {
    setLanguage('id');
    // jsdom tidak punya matchMedia (dipakai useIsMobile): anggap layar desktop.
    window.matchMedia ||= () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });

const tab = (name) => fireEvent.click(screen.getByRole('button', { name }));

describe('Activity Log', () => {
    it('pencarian dikirim setelah berhenti mengetik, filter aksi dan akun ikut terkirim', async () => {
        render(<AdminPanel currentUser={SYSADMIN} />);
        await screen.findByRole('option', { name: 'USER_CREATE' });
        const box = screen.getByLabelText('🔍 Cari user / aksi / detail / server');
        for (const v of ['a', 'ad', 'adm']) fireEvent.change(box, { target: { value: v } });
        await waitFor(() => expect(api.fetchAuditLogs).toHaveBeenCalledWith(expect.objectContaining({ search: 'adm' })));
        expect(api.fetchAuditLogs).not.toHaveBeenCalledWith(expect.objectContaining({ search: 'a' }));

        fireEvent.change(screen.getByLabelText('Aksi'), { target: { value: 'USER_CREATE' } });
        fireEvent.change(screen.getByLabelText('Akun (persis)'), { target: { value: 'budi' } });
        await waitFor(() => expect(api.fetchAuditLogs).toHaveBeenLastCalledWith(
            expect.objectContaining({ search: 'adm', action: 'USER_CREATE', username: 'budi', page: 1 })));

        fireEvent.click(screen.getByRole('button', { name: '⬇ Ekspor CSV' }));
        await waitFor(() => expect(api.downloadAdminCsv).toHaveBeenCalledWith('audit-logs/export',
            { search: 'adm', action: 'USER_CREATE', username: 'budi' }));
    });

    it('nama pengguna membuka semua aktivitasnya', async () => {
        vi.mocked(api.fetchAuditLogs).mockResolvedValue({ total: 1, items: [LOG] });
        vi.mocked(api.fetchRemoteHistory).mockResolvedValue({ total: 2, items: [
            { id: 1, username: 'budi', vm: 'win-lab', os_account: 'siswa', host: 'pve1', protocol: 'RDP', remote_host: '100.64.1.2', start_date: 1, end_date: null, active: true, duration_s: 60 },
        ] });
        render(<AdminPanel currentUser={SYSADMIN} />);
        fireEvent.click(await screen.findByRole('button', { name: 'budi' }));

        const dialog = await screen.findByRole('dialog', { name: 'Aktivitas budi' });
        await waitFor(() => {
            expect(api.fetchRemoteHistory).toHaveBeenCalledWith(1, 20, { username: 'budi' });
            expect(api.fetchOpenWebHistory).toHaveBeenCalledWith(1, 20, { username: 'budi' });
            expect(api.fetchSshHistory).toHaveBeenCalledWith(1, 20, { username: 'budi' });
        });
        expect(api.fetchAuditLogs).toHaveBeenCalledWith({ username: 'budi', page: 1, page_size: 20 });
        fireEvent.click(await within(dialog).findByRole('button', { name: 'Remote (2)' }));
        expect(await within(dialog).findByText('100.64.1.2')).toBeTruthy();
        expect(within(dialog).getByText('RDP')).toBeTruthy();
        fireEvent.click(within(dialog).getByRole('button', { name: '⬇ Ekspor CSV' }));
        await waitFor(() => expect(api.downloadAdminCsv).toHaveBeenCalledWith('remote/history/export', { username: 'budi' }));
        fireEvent.keyDown(window, { key: 'Escape' });
        await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    });
});

describe('Login gagal', () => {
    it('rekap per akun dan per IP', async () => {
        vi.mocked(api.fetchFailedLogins).mockResolvedValue({
            days: 7, total: 14,
            by_user: [{ username: 'budi', attempts: 12, last_at: '2026-10-07T01:00:00Z', ips: ['203.0.113.9'], known: true, locked_for: 120 },
                      { username: 'admin', attempts: 2, last_at: '2026-10-07T01:00:00Z', ips: ['203.0.113.9'], known: false, locked_for: 0 }],
            by_ip: [{ ip: '203.0.113.9', attempts: 14, last_at: '2026-10-07T01:00:00Z', accounts: 2, usernames: ['admin', 'budi'] }],
        });
        render(<AdminPanel currentUser={SYSADMIN} />);
        tab('🚫 Login Gagal');
        expect(await screen.findByText('14 login gagal dalam 7 hari terakhir')).toBeTruthy();
        expect(screen.getByText('TERKUNCI 120 dtk')).toBeTruthy();
        expect(screen.getByText('tidak terdaftar')).toBeTruthy();
        expect(screen.getByRole('button', { name: 'budi' })).toBeTruthy();          // akun terdaftar bisa dibuka
        expect(screen.queryByRole('button', { name: 'admin' })).toBeNull();
        tab('30 hari');
        await waitFor(() => expect(api.fetchFailedLogins).toHaveBeenLastCalledWith(30));
    });
});

describe('Memutus sesi', () => {
    it('Remote: pilih nonaktifkan akun, hasilnya ditampilkan', async () => {
        vi.mocked(api.fetchRemoteSessions).mockResolvedValue({ sessions: [
            { active_id: 'a1', username: 'budi', vm: 'tkj-12a', host: 'pve1', protocol: 'SSH', remote_host: '100.64.1.2', start_date: 1 },
        ] });
        vi.mocked(api.killRemoteSession).mockResolvedValue({ status: 'killed', block: 'account', sessions: { remote: 2, web: 1, ssh: 0 } });
        render(<AdminPanel currentUser={SYSADMIN} />);
        tab('🖥 Sesi Remote');
        fireEvent.click(await screen.findByRole('button', { name: '⛔ Putuskan Sesi' }));
        const dialog = screen.getByRole('dialog', { name: 'Putuskan sesi Remote' });
        fireEvent.click(within(dialog).getByLabelText(/Putuskan dan nonaktifkan akun/));
        fireEvent.click(within(dialog).getByRole('button', { name: '⛔ Putuskan Sesi' }));
        await within(dialog).findByText('Akun budi dinonaktifkan. Sesi yang ikut diputus: Remote 2, Web 1, SSH 0.');
        expect(api.killRemoteSession).toHaveBeenCalledWith('a1', 'account');
    });

    it('Remote: alasan penolakan dari server ditampilkan dan dialog tetap terbuka', async () => {
        vi.mocked(api.fetchRemoteSessions).mockResolvedValue({ sessions: [{ active_id: 'a2', username: 'budi', vm: 'x', host: 'h', start_date: 1 }] });
        vi.mocked(api.killRemoteSession).mockRejectedValue({ response: { data: { detail: 'Akses ke VM ini berasal dari grup TKJ.' } } });
        render(<AdminPanel currentUser={SYSADMIN} />);
        tab('🖥 Sesi Remote');
        fireEvent.click(await screen.findByRole('button', { name: '⛔ Putuskan Sesi' }));
        const dialog = screen.getByRole('dialog');
        fireEvent.click(within(dialog).getByLabelText(/cabut akses ke VM ini/));
        fireEvent.click(within(dialog).getByRole('button', { name: '⛔ Putuskan Sesi' }));
        expect((await within(dialog).findByRole('alert')).textContent).toContain('grup TKJ');
        expect(api.killRemoteSession).toHaveBeenCalledWith('a2', 'vm');
    });

    it('Web: sysadmin tidak bisa menonaktifkan akun sysadmin lain', async () => {
        vi.mocked(api.fetchOpenWebSessions).mockResolvedValue({ sessions: [
            { id: 's1', username: 'guru', role: 'sysadmin', target_ip: '10.0.0.7', status: 'active', hits: 3, client_ips: [] },
        ] });
        vi.mocked(api.killOpenWebSession).mockResolvedValue({ status: 'revoked', block: 'none' });
        render(<AdminPanel currentUser={SYSADMIN} />);
        tab('🌐 Sesi Web');
        fireEvent.click(await screen.findByRole('button', { name: '⛔ Cabut Link' }));
        const dialog = screen.getByRole('dialog', { name: 'Cabut link Open Web' });
        expect(within(dialog).getByLabelText(/nonaktifkan akun/).disabled).toBe(true);
        fireEvent.click(within(dialog).getByRole('button', { name: '⛔ Cabut Link' }));
        await within(dialog).findByText('Sesi sudah diputus.');
        expect(api.killOpenWebSession).toHaveBeenCalledWith('s1', 'none');
    });

    it('SSH: sesi aktif bisa diputus', async () => {
        vi.mocked(api.fetchSshSessions).mockResolvedValue({ sessions: [
            { id: 42, username: 'budi', role: 'student', key_name: 'laptop', fingerprint: 'SHA256:abc', client_ip: '203.0.113.7',
              targets: [], denied_targets: [], started_at: '2026-10-07T01:00:00Z', duration: 30, status: 'active' },
        ] });
        vi.mocked(api.killSshSession).mockResolvedValue({ status: 'killed', block: 'none' });
        render(<AdminPanel currentUser={SYSADMIN} />);
        fireEvent.click(await screen.findByRole('button', { name: '🔑 Sesi SSH' }));   // muncul setelah config SSH dimuat
        fireEvent.click(await screen.findByRole('button', { name: '⛔ Putuskan Sesi' }));
        const dialog = screen.getByRole('dialog', { name: 'Putuskan sesi SSH' });
        fireEvent.click(within(dialog).getByRole('button', { name: '⛔ Putuskan Sesi' }));
        await within(dialog).findByText('Sesi sudah diputus.');
        expect(api.killSshSession).toHaveBeenCalledWith(42, 'none');
    });

    it('akun pembaca saja (admin) tidak melihat tombol putuskan', async () => {
        vi.mocked(api.fetchRemoteSessions).mockResolvedValue({ sessions: [{ active_id: 'a3', username: 'budi', vm: 'x', host: 'h', start_date: 1 }] });
        render(<AdminPanel currentUser={{ id: 3, role: 'admin' }} />);
        tab('🖥 Sesi Remote');
        await screen.findByText('x');
        expect(screen.queryByRole('button', { name: '⛔ Putuskan Sesi' })).toBeNull();
    });
});

describe('Riwayat Remote', () => {
    it('menampilkan IP klien dan protokol, pencarian terkirim ke server', async () => {
        vi.mocked(api.fetchRemoteHistory).mockResolvedValue({ total: 1, items: [
            { id: 5, username: 'budi', vm: 'tkj-12a', os_account: '', host: 'pve1', protocol: 'SSH', remote_host: '100.64.1.2',
              start_date: 1, end_date: 2, active: false, duration_s: 3700 },
        ] });
        render(<AdminPanel currentUser={SYSADMIN} />);
        tab('🖥 Sesi Remote');
        tab('Riwayat Koneksi');
        expect(await screen.findByText('100.64.1.2')).toBeTruthy();
        expect(screen.getByText('1h 1m')).toBeTruthy();
        fireEvent.change(screen.getByLabelText('🔍 Cari user / VM'), { target: { value: 'tkj' } });
        await waitFor(() => expect(api.fetchRemoteHistory).toHaveBeenLastCalledWith(1, 50, { search: 'tkj' }));
    });
});
