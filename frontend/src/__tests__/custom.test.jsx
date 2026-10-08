import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { setLanguage, t, useT } from '../i18n';
import { accentVars } from '../branding';
import { leaseInfo } from '../format';
import { categoryLabel } from '../sysconfig';
import AnnouncementBanner from '../components/AnnouncementBanner';
import LanguageToggle from '../components/LanguageToggle';

afterEach(() => { cleanup(); setLanguage('id'); localStorage.clear(); });

describe('i18n', () => {
    it('menerjemahkan, mengisi variabel, dan jatuh ke Indonesia kalau terjemahan belum ada', () => {
        expect(t('login.submit')).toBe('Masuk →');
        setLanguage('en');
        expect(t('login.submit')).toBe('Sign in →');
        expect(t('servers.vms', { n: 3, running: 2 })).toBe('3 VMs · 2 running');
        expect(t('nav.servers')).toBe('Servers');                 // istilah yang sama di kedua bahasa
        expect(t('kunci.tidak.ada')).toBe('kunci.tidak.ada');
    });

    it('teks masa sewa ikut bahasa', () => {
        const past = new Date(Date.now() - 86400000).toISOString();
        expect(leaseInfo(past).short).toBe('Habis');
        setLanguage('en');
        expect(leaseInfo(past).short).toBe('Expired');
    });

    it('tombol ID / EN mengganti teks komponen', () => {
        function Title() { return <h1>{useT()('login.title')}</h1>; }
        render(<><LanguageToggle /><Title /></>);
        expect(screen.getByRole('heading').textContent).toBe('Masuk ke Dashboard');
        fireEvent.click(screen.getByRole('button', { name: 'EN' }));
        expect(screen.getByRole('heading').textContent).toBe('Sign in');
        expect(localStorage.getItem('ccd_lang')).toBe('en');
    });
});

describe('accentVars', () => {
    it('memakai warna asli di tema gelap dan versi lebih gelap di tema terang', () => {
        const v = accentVars('#F59E0B');
        expect(v.dark['--cyan']).toBe('#f59e0b');
        expect(v.light['--cyan']).toBe('#935f07');               // 60% kecerahan
        expect(v.dark['--cyan-glow']).toBe('rgba(245, 158, 11, 0.12)');
        expect(accentVars('')).toBeNull();
        expect(accentVars('red')).toBeNull();
    });
});

describe('categoryLabel', () => {
    const cats = [{ key: 'REMOTE_ISSUE', label: 'Tidak bisa Connect' }, { key: 'LEASE_EXTENSION', label: '' }, { key: 'LAB_JARINGAN', label: 'Lab Jaringan' }];
    it('label dari pengaturan, label bawaan yang diterjemahkan, atau kodenya', () => {
        expect(categoryLabel('REMOTE_ISSUE', cats)).toBe('Tidak bisa Connect');
        expect(categoryLabel('LEASE_EXTENSION', cats)).toBe('Perpanjang Sewa');
        setLanguage('en');
        expect(categoryLabel('LEASE_EXTENSION', cats)).toBe('Lease extension');
        expect(categoryLabel('KATEGORI_LAMA', cats)).toBe('KATEGORI_LAMA');
    });
});


describe('AnnouncementBanner', () => {
    it('bisa ditutup dan tetap tertutup untuk pengumuman yang sama', () => {
        const ann = { id: 'a1', text: 'Maintenance Sabtu', level: 'warning' };
        const { unmount } = render(<AnnouncementBanner announcement={ann} />);
        expect(screen.getByRole('status').textContent).toContain('Maintenance Sabtu');
        fireEvent.click(screen.getByRole('button', { name: 'Tutup' }));
        expect(screen.queryByRole('status')).toBeNull();
        unmount();
        render(<AnnouncementBanner announcement={ann} />);
        expect(screen.queryByRole('status')).toBeNull();
        cleanup();
        render(<AnnouncementBanner announcement={{ ...ann, id: 'a2', text: 'Pengumuman baru' }} />);
        expect(screen.getByRole('status').textContent).toContain('Pengumuman baru');
    });

    it('pengumuman penting tidak bisa ditutup', () => {
        render(<AnnouncementBanner announcement={{ id: 'c', text: 'Server mati', level: 'critical' }} />);
        expect(screen.queryByRole('button')).toBeNull();
    });
});
