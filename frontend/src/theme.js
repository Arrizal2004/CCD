import { useSyncExternalStore } from 'react';

// Tema gelap/terang: pilihan pengguna (disimpan di browser) atau tema bawaan dari Pengaturan Sistem
// ("dark", "light", atau "system" = ikuti pengaturan perangkat). Dipakai di halaman login dan dashboard.
const KEY = 'ccd_theme';
const THEMES = ['dark', 'light', 'system'];

function readChoice() {
    try {
        const v = localStorage.getItem(KEY);
        if (v === 'dark' || v === 'light') return v;
        // Versi lama selalu menulis 'hv-theme' = 'dark' walau pengguna tidak memilih; hanya 'light'
        // yang pasti hasil pilihan pengguna.
        return localStorage.getItem('hv-theme') === 'light' ? 'light' : null;
    } catch { return null; }
}

let chosen = readChoice();
let fallback = 'dark';
const listeners = new Set();
const media = typeof window !== 'undefined' && window.matchMedia ? window.matchMedia('(prefers-color-scheme: light)') : null;

export function currentTheme() {
    const pref = chosen || fallback;
    if (pref === 'system') return media?.matches ? 'light' : 'dark';
    return pref;
}

function apply() {
    if (typeof document === 'undefined') return;
    const light = currentTheme() === 'light';
    document.documentElement.setAttribute('data-theme', light ? 'light' : '');
    document.documentElement.style.background = light ? '#f0f4f8' : '#0b0f1a';
    listeners.forEach(fn => fn());
}

export function setTheme(theme) {
    chosen = theme;
    try { localStorage.setItem(KEY, theme); } catch { /* mode privat */ }
    apply();
}

export const toggleTheme = () => setTheme(currentTheme() === 'dark' ? 'light' : 'dark');

// Dipanggil saat pengaturan sistem dimuat; tidak menimpa pilihan pengguna.
export function setDefaultTheme(theme) {
    if (!THEMES.includes(theme) || theme === fallback) return;
    fallback = theme;
    apply();
}

export function useTheme() {
    return useSyncExternalStore(fn => { listeners.add(fn); return () => listeners.delete(fn); }, currentTheme);
}

media?.addEventListener?.('change', apply);
apply();
