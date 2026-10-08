import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../api', () => ({
    fetchSystemConfig: vi.fn(),
    saveTicketCategories: vi.fn(),
    saveOsOptions: vi.fn(),
    uploadOsLogo: vi.fn(),
    deleteOsLogo: vi.fn(),
}));

import * as api from '../api';
import { setLanguage } from '../i18n';
import { appTimeZone, loadSysConfig, osLogoUrl } from '../sysconfig';
import Clock from '../components/Clock';
import TicketCategoriesModal from '../components/TicketCategoriesModal';
import OsOptionsModal from '../components/OsOptionsModal';
import DeleteRecord from '../components/DeleteRecord';

const CONFIG = {
    announcement: null, default_vm_lease_days: null, timezone: 'Asia/Jakarta',
    ticket_categories: [{ key: 'REMOTE_ISSUE', label: '' }, { key: 'LEASE_EXTENSION', label: '' }, { key: 'OTHERS', label: '' }],
    vps_os_options: ['Windows', 'Ubuntu'], os_logos: { Ubuntu: 1700000000 },
};

beforeEach(async () => {
    setLanguage('id');
    vi.mocked(api.fetchSystemConfig).mockResolvedValue(CONFIG);
    await loadSysConfig();
});
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.useRealTimers(); });

describe('Jam di header', () => {
    it('menampilkan jam berjalan di zona waktu dari pengaturan, dan ikut berganti saat zona diubah', async () => {
        vi.useFakeTimers({ toFake: ['Date', 'setInterval', 'clearInterval'] });
        vi.setSystemTime(new Date('2026-10-07T05:00:00Z'));
        const { container } = render(<Clock />);
        const timer = container.querySelector('[role="timer"]');
        expect(timer.textContent).toMatch(/12[.:]00[.:]00/);                 // 05.00 UTC = 12.00 WIB
        expect(timer.textContent).toMatch(/WIB/);
        expect(timer.textContent).toMatch(/Rab/);                            // hari dalam bahasa Indonesia

        act(() => { vi.advanceTimersByTime(1000); });
        expect(timer.textContent).toMatch(/12[.:]00[.:]01/);

        vi.mocked(api.fetchSystemConfig).mockResolvedValue({ ...CONFIG, timezone: 'Asia/Jayapura' });
        await act(async () => { await loadSysConfig(); });
        expect(appTimeZone()).toBe('Asia/Jayapura');
        expect(timer.textContent).toMatch(/14[.:]00[.:]01/);                 // WIT = UTC+9
    });

    it('zona waktu dari isian yang belum disimpan dipakai untuk pratinjau, dan nama zona yang salah tidak merusak jam', () => {
        vi.useFakeTimers({ toFake: ['Date', 'setInterval', 'clearInterval'] });
        vi.setSystemTime(new Date('2026-10-07T05:00:00Z'));
        const { container, rerender } = render(<Clock timeZone="UTC" showDate={false} />);
        expect(container.querySelector('[role="timer"]').textContent).toMatch(/05[.:]00[.:]00/);
        rerender(<Clock timeZone="Mars/Olympus" showDate={false} />);
        expect(container.querySelector('[role="timer"]').textContent).toMatch(/\d{2}[.:]\d{2}[.:]\d{2}/);
    });
});

describe('Kategori Helpdesk', () => {
    it('menambah kategori baru tanpa kode, tidak bisa menghapus yang dipakai sistem, dan memuat ulang konfigurasi', async () => {
        vi.mocked(api.saveTicketCategories).mockResolvedValue({ ticket_categories: [] });
        const onClose = vi.fn();
        render(<TicketCategoriesModal onClose={onClose} />);

        const remove = screen.getAllByRole('button', { name: 'Hapus' });
        expect(remove.map(b => b.disabled)).toEqual([false, true, true]);    // LEASE_EXTENSION dan OTHERS terkunci

        fireEvent.click(screen.getByRole('button', { name: '+ Tambah kategori' }));
        fireEvent.change(screen.getByLabelText('Nama kategori 4'), { target: { value: 'Praktikum Jaringan' } });
        fireEvent.click(screen.getByRole('button', { name: '+ Tambah kategori' }));         // baris kosong diabaikan
        fireEvent.click(screen.getByRole('button', { name: 'Simpan' }));

        await waitFor(() => expect(onClose).toHaveBeenCalled());
        expect(api.saveTicketCategories).toHaveBeenCalledWith([
            { key: 'REMOTE_ISSUE', label: '' }, { key: 'LEASE_EXTENSION', label: '' }, { key: 'OTHERS', label: '' },
            { key: '', label: 'Praktikum Jaringan' },
        ]);
        expect(api.fetchSystemConfig).toHaveBeenCalledTimes(2);              // sekali di awal test, sekali setelah menyimpan
    });

    it('menampilkan pesan galat dari server dan tetap terbuka', async () => {
        vi.mocked(api.saveTicketCategories).mockRejectedValue({ response: { data: { detail: 'Kategori tidak valid' } } });
        const onClose = vi.fn();
        render(<TicketCategoriesModal onClose={onClose} />);
        fireEvent.click(screen.getByRole('button', { name: 'Simpan' }));
        expect((await screen.findByRole('alert')).textContent).toContain('Kategori tidak valid');
        expect(onClose).not.toHaveBeenCalled();
    });
});

describe('Pilihan OS dan logo', () => {
    const file = (name, type, size = 10) => new File([new Uint8Array(size)], name, { type });

    it('menyimpan daftar lalu mengunggah logo yang dipilih dan menghapus yang dibuang', async () => {
        vi.mocked(api.saveOsOptions).mockResolvedValue({ vps_os_options: ['Windows 11', 'Ubuntu', 'Rocky 9'], os_logos: {} });
        vi.mocked(api.uploadOsLogo).mockResolvedValue({});
        vi.mocked(api.deleteOsLogo).mockResolvedValue({});
        globalThis.URL.createObjectURL = vi.fn(() => 'blob:uji');
        globalThis.URL.revokeObjectURL = vi.fn();
        const onClose = vi.fn();
        render(<OsOptionsModal onClose={onClose} />);

        expect(screen.getByAltText('Logo Ubuntu').getAttribute('src')).toBe(osLogoUrl('Ubuntu'));
        expect(screen.queryByAltText('Logo Windows')).toBeNull();

        fireEvent.change(screen.getByLabelText('Pilihan OS 1'), { target: { value: 'Windows 11' } });      // ganti nama
        fireEvent.change(screen.getByLabelText('Pilih logo 1'), { target: { files: [file('w.png', 'image/png')] } });
        fireEvent.click(screen.getAllByRole('button', { name: 'Hapus logo' })[1]);                         // logo Ubuntu dibuang (yang kedua)
        fireEvent.click(screen.getByRole('button', { name: '+ Tambah OS' }));
        fireEvent.change(screen.getByLabelText('Pilihan OS 3'), { target: { value: 'Rocky 9' } });
        fireEvent.click(screen.getByRole('button', { name: 'Simpan' }));

        await waitFor(() => expect(onClose).toHaveBeenCalled());
        expect(api.saveOsOptions).toHaveBeenCalledWith([
            { name: 'Windows 11', from: 'Windows' }, { name: 'Ubuntu', from: 'Ubuntu' }, { name: 'Rocky 9', from: null },
        ]);
        expect(api.uploadOsLogo).toHaveBeenCalledTimes(1);
        expect(api.uploadOsLogo.mock.calls[0][0]).toBe('Windows 11');
        expect(api.deleteOsLogo).toHaveBeenCalledWith('Ubuntu');
    });

    it('menolak berkas yang bukan gambar atau terlalu besar sebelum dikirim', () => {
        render(<OsOptionsModal onClose={vi.fn()} />);
        fireEvent.change(screen.getByLabelText('Pilih logo 1'), { target: { files: [file('x.svg', 'image/svg+xml')] } });
        expect(screen.getByRole('alert').textContent).toContain('PNG, JPG, atau WebP');
        fireEvent.change(screen.getByLabelText('Pilih logo 1'), { target: { files: [file('x.png', 'image/png', 300 * 1024)] } });
        expect(screen.getByRole('alert').textContent).toContain('maksimal 256 KB');
        expect(api.uploadOsLogo).not.toHaveBeenCalled();
    });
});

describe('Hapus tiket dan Infra Request', () => {
    it('meminta konfirmasi dulu, menjelaskan bahwa hanya ringkasan yang tersisa, lalu memanggil hapus', async () => {
        const onDelete = vi.fn().mockResolvedValue({});
        const onDeleted = vi.fn();
        render(<DeleteRecord title="Hapus tiket TKT-0007?" summary="VM mati · budi · Terbuka" onDelete={onDelete} onDeleted={onDeleted} />);

        fireEvent.click(screen.getByRole('button', { name: 'Hapus' }));
        const dialog = screen.getByRole('dialog', { name: 'Hapus tiket TKT-0007?' });
        expect(dialog.textContent).toContain('VM mati · budi · Terbuka');
        expect(dialog.textContent).toContain('Di Audit Trail hanya tersisa ringkasannya');
        expect(onDelete).not.toHaveBeenCalled();                                   // belum ada yang terhapus sebelum dikonfirmasi

        fireEvent.click(screen.getByRole('button', { name: 'Batal' }));
        expect(screen.queryByRole('dialog')).toBeNull();
        expect(onDelete).not.toHaveBeenCalled();

        fireEvent.click(screen.getByRole('button', { name: 'Hapus' }));
        fireEvent.click(screen.getByRole('button', { name: 'Hapus permanen' }));
        await waitFor(() => expect(onDeleted).toHaveBeenCalled());
        expect(onDelete).toHaveBeenCalledTimes(1);
        expect(screen.queryByRole('dialog')).toBeNull();
    });

    it('menampilkan alasan penolakan dan tidak menutup tampilan saat gagal', async () => {
        const onDelete = vi.fn().mockRejectedValue({ response: { data: { detail: 'Forbidden: fitur ini khusus superadmin.' } } });
        const onDeleted = vi.fn();
        render(<DeleteRecord title="Hapus?" summary="x" onDelete={onDelete} onDeleted={onDeleted} />);
        fireEvent.click(screen.getByRole('button', { name: 'Hapus' }));
        fireEvent.click(screen.getByRole('button', { name: 'Hapus permanen' }));
        expect((await screen.findByRole('alert')).textContent).toContain('khusus superadmin');
        expect(onDeleted).not.toHaveBeenCalled();
        expect(screen.getByRole('dialog')).toBeTruthy();
    });
});
