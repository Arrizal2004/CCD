import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

vi.mock('../api', () => ({
    createOpenWebTicket: vi.fn(),
    fetchMyOpenWebHistory: vi.fn(),
    reopenOpenWeb: vi.fn(),
    extendOpenWeb: vi.fn(),
}));

import * as api from '../api';
import { setLanguage } from '../i18n';
import OpenWebPage from '../pages/OpenWebPage';

const item = (over) => ({
    id: 'a1', url: 'http://10.0.0.5:8080/app', status: 'active', created_at: '2026-10-09T01:00:00Z',
    expires_at: '2026-10-09T02:00:00Z', remaining: 3000, can_extend: false, extend_in: 1200, ...over,
});

beforeEach(() => {
    setLanguage('id');
    try { localStorage.clear(); } catch { /* tidak ada storage */ }
    vi.mocked(api.fetchMyOpenWebHistory).mockResolvedValue({ items: [item()] });
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });


describe('Riwayat Open Web', () => {
    it('menampilkan alamat, status, batas waktu, dan sisa waktu', async () => {
        render(<OpenWebPage />);
        await screen.findByText('http://10.0.0.5:8080/app');
        expect(document.body.textContent).toContain('Aktif');
        expect(document.body.textContent).toContain('berlaku sampai');
        expect(document.body.textContent).toContain('sisa 50 menit');
    });

    it('Buka memakai lagi sesi yang sama dan menampilkannya di iframe, tanpa membuat sesi baru', async () => {
        vi.mocked(api.reopenOpenWeb).mockResolvedValue({ proxy_path: '/openweb/tk/10.0.0.5:8080/app', session_id: 'a1', url: 'http://10.0.0.5:8080/app' });
        render(<OpenWebPage />);
        const line = (await screen.findByText('http://10.0.0.5:8080/app')).parentElement;   // tombol "Buka" di form dan di riwayat
        fireEvent.click(within(line).getByRole('button', { name: 'Buka' }));
        await waitFor(() => expect(document.querySelector('iframe')?.getAttribute('src')).toBe('/openweb/tk/10.0.0.5:8080/app'));
        expect(api.reopenOpenWeb).toHaveBeenCalledWith('a1');
        expect(api.createOpenWebTicket).not.toHaveBeenCalled();
    });

    it('+1 jam nonaktif saat sisa waktu masih 30 menit atau lebih, dengan petunjuk kapan bisa', async () => {
        render(<OpenWebPage />);
        await screen.findByText('http://10.0.0.5:8080/app');
        const btn = screen.getByRole('button', { name: '+1 jam' });
        expect(btn.disabled).toBe(true);
        expect(btn.getAttribute('title')).toContain('kurang dari 30 menit (sekitar 20 menit lagi)');
        fireEvent.click(btn);
        expect(api.extendOpenWeb).not.toHaveBeenCalled();
    });

    it('+1 jam aktif saat sisa kurang dari 30 menit, lalu riwayat dimuat ulang', async () => {
        vi.mocked(api.fetchMyOpenWebHistory).mockResolvedValue({ items: [item({ remaining: 600, can_extend: true, extend_in: 0 })] });
        vi.mocked(api.extendOpenWeb).mockResolvedValue({ expires_at: '2026-10-09T03:00:00Z', remaining: 4200 });
        render(<OpenWebPage />);
        await screen.findByText('http://10.0.0.5:8080/app');
        const before = vi.mocked(api.fetchMyOpenWebHistory).mock.calls.length;
        const btn = screen.getByRole('button', { name: '+1 jam' });
        expect(btn.disabled).toBe(false);
        fireEvent.click(btn);
        await waitFor(() => expect(api.extendOpenWeb).toHaveBeenCalledWith('a1'));
        await waitFor(() => expect(vi.mocked(api.fetchMyOpenWebHistory).mock.calls.length).toBeGreaterThan(before));
    });

    it('sesi yang sudah habis atau dicabut: tidak ada +1 jam, hanya Buka baru yang membuat sesi dengan alamat sama', async () => {
        vi.mocked(api.fetchMyOpenWebHistory).mockResolvedValue({ items: [
            item({ id: 'e1', url: 'http://10.0.0.5:8080/habis', status: 'expired', remaining: 0, can_extend: false, extend_in: 0 }),
            item({ id: 'r1', url: 'http://10.0.0.5:8080/cabut', status: 'revoked', remaining: 0, can_extend: false, extend_in: 0 }),
        ] });
        vi.mocked(api.createOpenWebTicket).mockResolvedValue({ proxy_path: '/openweb/new/10.0.0.5:8080/habis', session_id: 'n1' });
        render(<OpenWebPage />);
        await screen.findByText('http://10.0.0.5:8080/habis');
        expect(screen.queryByRole('button', { name: '+1 jam' })).toBeNull();
        expect(document.body.textContent).toContain('Habis');
        expect(document.body.textContent).toContain('Dicabut');
        fireEvent.click(screen.getAllByRole('button', { name: 'Buka baru' })[0]);
        await waitFor(() => expect(api.createOpenWebTicket).toHaveBeenCalledWith('http://10.0.0.5:8080/habis'));
    });

    it('pesan dari server ditampilkan kalau menambah waktu ditolak', async () => {
        vi.mocked(api.fetchMyOpenWebHistory).mockResolvedValue({ items: [item({ remaining: 600, can_extend: true, extend_in: 0 })] });
        vi.mocked(api.extendOpenWeb).mockRejectedValue({ response: { data: { detail: 'Sesi ini sudah mencapai batas 24 jam. Buka sesi baru' } } });
        render(<OpenWebPage />);
        await screen.findByText('http://10.0.0.5:8080/app');
        fireEvent.click(screen.getByRole('button', { name: '+1 jam' }));
        await screen.findByText('Sesi ini sudah mencapai batas 24 jam. Buka sesi baru');
    });

    it('tanpa riwayat: keterangan kosong', async () => {
        vi.mocked(api.fetchMyOpenWebHistory).mockResolvedValue({ items: [] });
        render(<OpenWebPage />);
        await screen.findByText(/Belum ada riwayat/);
    });
});
