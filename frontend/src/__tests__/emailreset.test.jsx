import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../api', () => ({
    fetchEmailSettings: vi.fn(), saveEmailSettings: vi.fn(), sendTestEmail: vi.fn(), fetchEmailOutbox: vi.fn(),
    fetchSelfResetAvailable: vi.fn(), requestPasswordReset: vi.fn(), verifyResetToken: vi.fn(), confirmPasswordReset: vi.fn(),
    requestPasswordHelp: vi.fn(), registerStudent: vi.fn(), changePassword: vi.fn(), storeSession: vi.fn(),
}));

import * as api from '../api';
import EmailSettingsCard from '../components/EmailSettingsCard';
import LoginPage from '../pages/LoginPage';

afterEach(() => { cleanup(); vi.clearAllMocks(); window.history.replaceState(null, '', '/'); });

const CFG = {
    smtp: { enabled: false, host: '', port: 587, security: 'starttls', username: '', from_address: '', from_name: '', reply_to: '', dashboard_url: '', has_password: false },
    notify: { lease: true, lease_days_before: 3, self_reset: false }, events: ['lease'],
};

describe('EmailSettingsCard', () => {
    it('menyimpan pengaturan tanpa mengirim field has_password, dengan password hanya kalau diisi', async () => {
        vi.mocked(api.fetchEmailSettings).mockResolvedValue(CFG);
        vi.mocked(api.fetchEmailOutbox).mockResolvedValue({ items: [], last_7_days: { sent: 4, failed: 1 } });
        vi.mocked(api.saveEmailSettings).mockResolvedValue({ ...CFG, smtp: { ...CFG.smtp, has_password: true } });
        render(<EmailSettingsCard />);
        fireEvent.change(await screen.findByLabelText('Server SMTP'), { target: { value: 'smtp.kampus.ac.id' } });
        fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'rahasia' } });
        fireEvent.click(screen.getByLabelText('Izinkan reset password mandiri lewat email'));
        fireEvent.click(screen.getByText('Simpan email'));
        await waitFor(() => expect(api.saveEmailSettings).toHaveBeenCalled());
        const sent = vi.mocked(api.saveEmailSettings).mock.calls[0][0];
        expect(sent.smtp.host).toBe('smtp.kampus.ac.id');
        expect(sent.smtp.password).toBe('rahasia');
        expect('has_password' in sent.smtp).toBe(false);
        expect(sent.notify.self_reset).toBe(true);
        expect(await screen.findByText(/Pengaturan email disimpan/)).toBeTruthy();
        expect(screen.getByText(/4 terkirim/)).toBeTruthy();
    });

    it('kirim email uji menampilkan galat dari server', async () => {
        vi.mocked(api.fetchEmailSettings).mockResolvedValue(CFG);
        vi.mocked(api.fetchEmailOutbox).mockResolvedValue({ items: [], last_7_days: {} });
        vi.mocked(api.sendTestEmail).mockRejectedValue({ response: { data: { detail: 'Gagal mengirim: smtp mati' } } });
        render(<EmailSettingsCard />);
        fireEvent.change(await screen.findByLabelText('Kirim email uji ke'), { target: { value: 'a@b.id' } });
        fireEvent.click(screen.getByText('Kirim uji'));
        expect(await screen.findByText(/smtp mati/)).toBeTruthy();
    });
});

describe('Halaman login: reset password lewat email', () => {
    it('Lupa password menawarkan tautan email bila diaktifkan', async () => {
        vi.mocked(api.fetchSelfResetAvailable).mockResolvedValue({ enabled: true });
        vi.mocked(api.requestPasswordReset).mockResolvedValue({ message: 'Tautan sudah dikirim.' });
        render(<LoginPage onLogin={() => {}} />);
        fireEvent.click(screen.getByText(/Lupa password/));
        fireEvent.change(await screen.findByLabelText('Username atau email'), { target: { value: 'budi' } });
        fireEvent.click(screen.getByText('Kirim tautan reset'));
        await waitFor(() => expect(api.requestPasswordReset).toHaveBeenCalledWith('budi'));
        expect(await screen.findByText('Tautan sudah dikirim.')).toBeTruthy();
    });

    it('tanpa email, Lupa password tetap lewat permintaan ke admin', async () => {
        vi.mocked(api.fetchSelfResetAvailable).mockResolvedValue({ enabled: false });
        render(<LoginPage onLogin={() => {}} />);
        fireEvent.click(screen.getByText(/Lupa password/));
        await waitFor(() => expect(api.fetchSelfResetAvailable).toHaveBeenCalled());
        expect(screen.queryByText('Kirim tautan reset')).toBeNull();
    });

    it('tautan ?reset= membuka form password baru, lalu token dihapus dari alamat', async () => {
        window.history.replaceState(null, '', '/?reset=tok-123');
        vi.mocked(api.verifyResetToken).mockResolvedValue({ username: 'budi' });
        vi.mocked(api.confirmPasswordReset).mockResolvedValue({ username: 'budi' });
        render(<LoginPage onLogin={() => {}} />);
        expect(window.location.search).toBe('');
        fireEvent.change(await screen.findByLabelText('Password baru'), { target: { value: 'PasswordBaru#9' } });
        fireEvent.change(screen.getByLabelText('Ulangi password baru'), { target: { value: 'PasswordBaru#9' } });
        fireEvent.click(screen.getByText('Simpan password baru'));
        await waitFor(() => expect(api.confirmPasswordReset).toHaveBeenCalledWith('tok-123', 'PasswordBaru#9'));
        expect(await screen.findByText('Password sudah diganti')).toBeTruthy();
    });

    it('tautan yang tidak valid menampilkan alasannya', async () => {
        window.history.replaceState(null, '', '/?reset=lama');
        vi.mocked(api.verifyResetToken).mockRejectedValue({ response: { data: { detail: 'Tautan kedaluwarsa.' } } });
        render(<LoginPage onLogin={() => {}} />);
        expect(await screen.findByText('Tautan tidak bisa dipakai')).toBeTruthy();
        expect(screen.getByText('Tautan kedaluwarsa.')).toBeTruthy();
    });
});
