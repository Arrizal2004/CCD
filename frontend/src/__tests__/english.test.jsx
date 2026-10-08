import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';

vi.mock('../api', () => ({
    fetchBranding: vi.fn(() => Promise.resolve({})),
    fetchSystemConfig: vi.fn(() => Promise.resolve({})),
    registerStudent: vi.fn(), requestPasswordHelp: vi.fn(), changePassword: vi.fn(), storeSession: vi.fn(),
    fetchUsers: vi.fn(), createUser: vi.fn(), updateUser: vi.fn(), deleteUserApi: vi.fn(), fetchUserAssignments: vi.fn(),
    fetchGroups: vi.fn(), bulkUsers: vi.fn(), importUsers: vi.fn(), resetUserPassword: vi.fn(),
    fetchPasswordHelp: vi.fn(), dismissPasswordHelp: vi.fn(),
    fetchNetworks: vi.fn(), checkNetworkSetup: vi.fn(), addNetworkPool: vi.fn(), removeNetworkPool: vi.fn(),
    createNetwork: vi.fn(), updateNetwork: vi.fn(), deleteNetwork: vi.fn(), fetchNetworkVms: vi.fn(),
    fetchSystemSettings: vi.fn(), saveSystemSettings: vi.fn(),
    fetchAuditStats: vi.fn(() => Promise.resolve({ rows: 5, oldest: '2026-09-30T01:00:00Z', bytes: 2048, env_default: 180, effective_days: 180 })), uploadSystemLogo: vi.fn(), deleteSystemLogo: vi.fn(),
}));

import * as api from '../api';
import { setLanguage } from '../i18n';
import LoginPage from '../pages/LoginPage';
import UsersPage from '../pages/UserPage';
import SwitchManager from '../components/SwitchManager';
import SystemSettingsPage from '../components/SystemSettingsPage';

// Kata Indonesia yang umum di antarmuka. Kalau salah satunya muncul di mode EN, ada teks yang lupa diterjemahkan.
const INDONESIAN = /\b(yang|dan|atau|untuk|dengan|belum|sudah|tidak|tambah|hapus|simpan|batal|gagal|memuat|pengguna|akun|alamat|blok|lewat|kosong|pilih|ubah|buat|masuk|keluar|bawaan|pengaturan)\b/i;

const indonesianIn = (el) => {
    const found = [];
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
    for (let n = walker.currentNode; n; n = walker.nextNode()) {
        const texts = n.nodeType === Node.TEXT_NODE ? [n.textContent]
            : ['placeholder', 'title', 'aria-label'].map(a => n.getAttribute?.(a)).filter(Boolean);
        for (const text of texts) {
            const m = text.match(INDONESIAN);
            if (m) found.push(`${m[0]} ← "${text.trim().slice(0, 80)}"`);
        }
    }
    return found;
};

beforeEach(() => setLanguage('en'));
afterEach(() => { cleanup(); vi.clearAllMocks(); setLanguage('id'); localStorage.clear(); });

describe('mode English tidak menampilkan teks Indonesia', () => {
    it('halaman login', () => {
        const { container } = render(<LoginPage onLogin={vi.fn()} />);
        expect(screen.getByRole('button', { name: 'Sign in →' })).toBeTruthy();
        expect(indonesianIn(container)).toEqual([]);
    });

    it('halaman Users', async () => {
        vi.mocked(api.fetchUsers).mockResolvedValue([
            { id: 1, username: 'admin', full_name: 'Admin', role: 'superadmin', is_active: true },
            { id: 2, username: 'budi', full_name: 'Budi', role: 'student', is_active: false, is_verified: false, must_change_password: true, expires_at: '2020-01-01T00:00:00Z' },
        ]);
        vi.mocked(api.fetchUserAssignments).mockResolvedValue([]);
        vi.mocked(api.fetchGroups).mockResolvedValue([{ id: 1, name: 'TKJ' }]);
        vi.mocked(api.fetchPasswordHelp).mockResolvedValue([{ id: 9, user_id: 2, username: 'budi', full_name: 'Budi', role: 'student', is_active: false, message: '', created_at: '2026-10-07T01:00:00Z' }]);
        const { container } = render(<UsersPage currentUser={{ id: 1, role: 'superadmin' }} />);
        await screen.findByText('User management');
        await screen.findAllByText('budi');
        expect(indonesianIn(container)).toEqual([]);
    });

    it('pengelola switch', async () => {
        vi.mocked(api.fetchNetworks).mockResolvedValue({ instances: [{
            label: 'pve1', token_id: 'root@pam!ccd', zone: 'ccd', suggested_cidr: null,
            pools: [{ cidr: '192.168.111.0/24', switches: 1, full: true }],
            networks: [{ id: 3, instance: 'pve1', vnet: 'ccdab12x', name: 'Class A', cidr: '192.168.111.0/24', gateway: '192.168.111.1', snat: true }],
        }] });
        vi.mocked(api.checkNetworkSetup).mockResolvedValue({ zone_ok: false, perm_zone: false, perm_apply: false, ready: false, reachable: { 3: false } });
        const { container } = render(<SwitchManager />);
        await screen.findByText('Class A');
        await screen.findByText(/does not exist yet/);
        expect(indonesianIn(container)).toEqual([]);
    });

    it('pengaturan sistem', async () => {
        vi.mocked(api.fetchSystemSettings).mockResolvedValue({
            name: 'CCD', short_name: 'CCD', institution: '', tagline: '', registration_open: true, allowed_emails: ['@kampus.ac.id'],
            accent_color: '', default_language: 'en', default_theme: 'dark',
            announcement: { text: 'x', level: 'info', starts_at: null, ends_at: null, show_on_login: false },
            default_vm_lease_days: null, default_account_days: null, ticket_categories: [{ key: 'OTHERS', label: '' }],
            vps_os_options: ['Ubuntu'], ssh_public_host: '', logo_version: null, ssh_env: { enabled: false, env_host_set: false, port: 2222 },
        });
        const { container } = render(<SystemSettingsPage />);
        await screen.findByText('System settings');
        // Contoh alamat email di placeholder memang memakai domain fiktif berbahasa Indonesia.
        expect(indonesianIn(container).filter(x => !x.includes('kampus.ac.id'))).toEqual([]);
    });
});
