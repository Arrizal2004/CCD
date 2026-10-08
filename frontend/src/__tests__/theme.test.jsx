import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';

const attr = () => document.documentElement.getAttribute('data-theme');

// Modul tema menyimpan status di memori, jadi tiap test memuat salinan baru dengan localStorage tertentu.
async function freshTheme(storage = {}) {
    localStorage.clear();
    Object.entries(storage).forEach(([k, v]) => localStorage.setItem(k, v));
    vi.resetModules();
    return import('../theme');
}

afterEach(() => { cleanup(); localStorage.clear(); });

describe('theme', () => {
    it('tema bawaan sistem dipakai sampai pengguna memilih sendiri', async () => {
        const th = await freshTheme();
        expect(th.currentTheme()).toBe('dark');
        th.setDefaultTheme('light');
        expect(th.currentTheme()).toBe('light');
        expect(attr()).toBe('light');
        th.setTheme('dark');
        th.setDefaultTheme('system');
        expect(th.currentTheme()).toBe('dark');                // pilihan pengguna menang
        expect(localStorage.getItem('ccd_theme')).toBe('dark');
    });

    it('kunci lama: hanya "light" yang dianggap pilihan pengguna', async () => {
        let th = await freshTheme({ 'hv-theme': 'dark' });
        th.setDefaultTheme('light');
        expect(th.currentTheme()).toBe('light');               // 'dark' lama ditulis otomatis, bukan pilihan
        th = await freshTheme({ 'hv-theme': 'light' });
        expect(th.currentTheme()).toBe('light');
    });

    it('tombol mengganti tema dan ikonnya', async () => {
        const th = await freshTheme();
        const { default: ThemeToggle } = await import('../components/ThemeToggle');
        render(<ThemeToggle />);
        const btn = screen.getByRole('button', { name: 'Ganti tema' });
        const darkIcon = btn.innerHTML;
        expect(btn.querySelector('svg')).not.toBeNull();
        expect(btn.textContent).toBe('');          // ikon SVG, bukan emoji
        fireEvent.click(btn);
        expect(th.currentTheme()).toBe('light');
        expect(attr()).toBe('light');
        expect(btn.innerHTML).not.toBe(darkIcon);  // ikon berganti mengikuti tema
    });
});
