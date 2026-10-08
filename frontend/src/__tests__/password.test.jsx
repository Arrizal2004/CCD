import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../api', () => ({
    fetchBranding: vi.fn(() => Promise.resolve({})),
    registerStudent: vi.fn(),
    requestPasswordHelp: vi.fn(),
    changePassword: vi.fn(),
    storeSession: vi.fn(),
    fetchUsers: vi.fn(),
    createUser: vi.fn(),
    updateUser: vi.fn(),
    deleteUserApi: vi.fn(),
    fetchUserAssignments: vi.fn(),
    fetchGroups: vi.fn(),
    bulkUsers: vi.fn(),
    importUsers: vi.fn(),
    resetUserPassword: vi.fn(),
    fetchPasswordHelp: vi.fn(),
    dismissPasswordHelp: vi.fn(),
}));

import * as api from '../api';
import LoginPage, { ForcePasswordChange } from '../pages/LoginPage';
import UsersPage from '../pages/UserPage';

afterEach(() => { cleanup(); vi.clearAllMocks(); localStorage.clear(); });

const fill = (label, value) => fireEvent.change(screen.getByLabelText(label), { target: { value } });

describe('Lupa password di halaman login', () => {
    it('mengirim permintaan dengan username yang sudah diketik dan menampilkan konfirmasi', async () => {
        vi.mocked(api.requestPasswordHelp).mockResolvedValue({ message: 'ok' });
        render(<LoginPage onLogin={vi.fn()} />);
        fireEvent.change(screen.getByPlaceholderText('username'), { target: { value: 'budi' } });
        fireEvent.click(screen.getByRole('button', { name: 'Lupa password?' }));

        expect(screen.getByPlaceholderText('username').value).toBe('budi');
        fill('Pesan untuk admin (opsional)', ' Kelas TKJ 12A ');
        fireEvent.click(screen.getByRole('button', { name: 'Kirim Permintaan →' }));

        await screen.findByText('Permintaan terkirim');
        expect(api.requestPasswordHelp).toHaveBeenCalledWith('budi', 'Kelas TKJ 12A');
        fireEvent.click(screen.getByRole('button', { name: '← Kembali ke login' }));
        expect(screen.getByRole('button', { name: 'Masuk →' })).toBeTruthy();
    });

    it('username wajib diisi', () => {
        render(<LoginPage onLogin={vi.fn()} />);
        fireEvent.click(screen.getByRole('button', { name: 'Lupa password?' }));
        fireEvent.click(screen.getByRole('button', { name: 'Kirim Permintaan →' }));
        expect(screen.getByText(/Username wajib diisi/)).toBeTruthy();
        expect(api.requestPasswordHelp).not.toHaveBeenCalled();
    });
});

describe('ForcePasswordChange', () => {
    const user = { id: 5, username: 'budi', role: 'student', must_change_password: true };

    it('memakai password sementara dari login tanpa meminta ulang', async () => {
        vi.mocked(api.changePassword).mockResolvedValue({ access_token: 'tok-baru' });
        const onDone = vi.fn();
        render(<ForcePasswordChange user={user} knownPassword="Abcd-efgh-2345" onDone={onDone} onLogout={vi.fn()} />);
        expect(screen.queryByLabelText('Password sementara')).toBeNull();

        fill('Password baru', 'Abcd-efgh-2345');
        fill('Ulangi password baru', 'Abcd-efgh-2345');
        fireEvent.click(screen.getByRole('button', { name: 'Simpan & Lanjutkan →' }));
        expect(screen.getByText(/harus berbeda/)).toBeTruthy();

        fill('Password baru', 'BaruSekali456!');
        fill('Ulangi password baru', 'BaruSekali456!');
        fireEvent.click(screen.getByRole('button', { name: 'Simpan & Lanjutkan →' }));
        await waitFor(() => expect(onDone).toHaveBeenCalledWith({ ...user, must_change_password: false }, 'tok-baru'));
        expect(api.changePassword).toHaveBeenCalledWith('Abcd-efgh-2345', 'BaruSekali456!');
        expect(JSON.parse(localStorage.getItem('hv_user')).must_change_password).toBe(false);
    });

    it('meminta password sementara kalau halaman dimuat ulang', () => {
        render(<ForcePasswordChange user={user} onDone={vi.fn()} onLogout={vi.fn()} />);
        expect(screen.getByLabelText('Password sementara')).toBeTruthy();
    });
});

describe('Users: reset password dan permintaan lupa password', () => {
    const users = [
        { id: 1, username: 'sysadm', full_name: 'Sys', role: 'sysadmin', is_active: true },
        { id: 2, username: 'budi', full_name: 'Budi', role: 'student', is_active: true, is_verified: true },
        { id: 3, username: 'guru', full_name: 'Guru', role: 'sysadmin', is_active: true },
    ];
    const setup = () => {
        vi.mocked(api.fetchUsers).mockResolvedValue(users);
        vi.mocked(api.fetchUserAssignments).mockResolvedValue([]);
        vi.mocked(api.fetchGroups).mockResolvedValue([]);
        vi.mocked(api.fetchPasswordHelp).mockResolvedValue([
            { id: 9, user_id: 2, username: 'budi', full_name: 'Budi', role: 'student', is_active: true,
              message: 'Kelas 12A', client_ip: '10.0.0.5', created_at: '2026-10-07T01:00:00Z' },
        ]);
    };

    it('sysadmin hanya bisa mereset mahasiswa, dan password sementara ditampilkan sekali', async () => {
        setup();
        vi.mocked(api.resetUserPassword).mockResolvedValue({ username: 'budi', password: 'Wxyz-2345-abcd', must_change_password: true });
        render(<UsersPage currentUser={{ id: 1, role: 'sysadmin' }} />);

        await screen.findByText('“Kelas 12A”');
        // Satu tombol di panel permintaan + satu di baris budi; tidak ada untuk akun sysadmin lain.
        expect(screen.getAllByRole('button', { name: /Reset password/ })).toHaveLength(2);

        // Tombol pertama ada di panel permintaan, yang kedua di baris budi.
        fireEvent.click(screen.getAllByRole('button', { name: 'Reset password' })[1]);
        fireEvent.click(screen.getAllByRole('button', { name: 'Reset password' }).at(-1));

        const dialog = await screen.findByRole('dialog', { name: 'Password sementara' });
        expect(dialog.textContent).toContain('Wxyz-2345-abcd');
        expect(api.resetUserPassword).toHaveBeenCalledWith(2);
        fireEvent.click(screen.getByRole('button', { name: 'Sudah dicatat' }));
        expect(screen.queryByText('Wxyz-2345-abcd')).toBeNull();
    });

    it('permintaan bisa diabaikan', async () => {
        setup();
        vi.mocked(api.dismissPasswordHelp).mockResolvedValue({ ok: true });
        vi.spyOn(window, 'confirm').mockReturnValue(true);
        render(<UsersPage currentUser={{ id: 1, role: 'sysadmin' }} />);
        fireEvent.click(await screen.findByRole('button', { name: 'Abaikan' }));
        await waitFor(() => expect(api.dismissPasswordHelp).toHaveBeenCalledWith(9));
    });
});
