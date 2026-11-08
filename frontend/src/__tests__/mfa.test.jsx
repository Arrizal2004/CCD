import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../api', () => ({
    fetchMyMfa: vi.fn(), setupMyMfa: vi.fn(), enableMyMfa: vi.fn(), disableMyMfa: vi.fn(), newMfaRecoveryCodes: vi.fn(),
    fetchMfaPolicy: vi.fn(), saveMfaPolicy: vi.fn(),
}));

import * as api from '../api';
import MfaSection from '../components/MfaSection';
import MfaPolicyCard from '../components/MfaPolicyCard';

afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe('MfaSection (Profil)', () => {
    it('memasang 2FA: menampilkan QR, mengirim kode, lalu menampilkan kode pemulihan', async () => {
        vi.mocked(api.fetchMyMfa).mockResolvedValue({ enabled: false, recovery_codes_left: 0, required: false });
        vi.mocked(api.setupMyMfa).mockResolvedValue({ secret: 'JBSWY3DPEHPK3PXP', qr_svg: '<svg/>', otpauth_uri: 'otpauth://totp/x' });
        vi.mocked(api.enableMyMfa).mockResolvedValue({ recovery_codes: ['aaaaa-bbbbb', 'ccccc-ddddd'] });

        render(<MfaSection />);
        fireEvent.click(await screen.findByText('Aktifkan 2FA'));
        expect(await screen.findByText('JBSWY3DPEHPK3PXP')).toBeTruthy();
        // QR dipasang sebagai gambar (data URI), bukan HTML mentah
        expect(document.querySelector('img[alt="QR"]').src.startsWith('data:image/svg+xml')).toBe(true);

        fireEvent.change(screen.getByLabelText('Kode'), { target: { value: '123456' } });
        fireEvent.click(screen.getByText('Aktifkan'));
        await waitFor(() => expect(api.enableMyMfa).toHaveBeenCalledWith('123456'));
        expect(await screen.findByText('aaaaa-bbbbb')).toBeTruthy();
    });

    it('akun yang wajib 2FA tidak punya tombol nonaktifkan', async () => {
        vi.mocked(api.fetchMyMfa).mockResolvedValue({ enabled: true, recovery_codes_left: 7, required: true });
        render(<MfaSection />);
        expect(await screen.findByText('Aktif')).toBeTruthy();
        expect(screen.getByText('Buat kode pemulihan baru')).toBeTruthy();
        expect(screen.queryByText('Nonaktifkan 2FA')).toBeNull();
    });

    it('menonaktifkan butuh password dan kode', async () => {
        vi.mocked(api.fetchMyMfa).mockResolvedValue({ enabled: true, recovery_codes_left: 10, required: false });
        vi.mocked(api.disableMyMfa).mockResolvedValue({ ok: true });
        render(<MfaSection />);
        fireEvent.click(await screen.findByText('Nonaktifkan 2FA'));
        fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'rahasia' } });
        fireEvent.change(screen.getByLabelText('Kode'), { target: { value: '654321' } });
        fireEvent.click(screen.getAllByText('Nonaktifkan 2FA').pop());
        await waitFor(() => expect(api.disableMyMfa).toHaveBeenCalledWith('654321', 'rahasia'));
    });
});

describe('MfaPolicyCard', () => {
    it('memuat dan menyimpan kebijakan', async () => {
        vi.mocked(api.fetchMfaPolicy).mockResolvedValue({ required: 'off' });
        vi.mocked(api.saveMfaPolicy).mockResolvedValue({ required: 'admins' });
        render(<MfaPolicyCard />);
        const select = await screen.findByLabelText('Kebijakan 2FA');
        fireEvent.change(select, { target: { value: 'admins' } });
        fireEvent.click(screen.getByText('Simpan kebijakan'));
        await waitFor(() => expect(api.saveMfaPolicy).toHaveBeenCalledWith('admins'));
        expect(await screen.findByText(/Kebijakan 2FA disimpan/)).toBeTruthy();
    });
});
