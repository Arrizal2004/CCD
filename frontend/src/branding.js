import { useSyncExternalStore } from 'react';
import { fetchBranding } from './api';
import { setDefaultLanguage } from './i18n';
import { setDefaultTheme } from './theme';

// Identitas sistem dari Pengaturan Sistem (superadmin): dipakai di halaman login, header, footer,
// jendela Tentang, dan judul tab. Nilai terakhir disimpan di browser supaya nama tidak berkedip dari
// nama bawaan ke nama kampus setiap kali halaman dibuka.
export const DEFAULT_BRANDING = {
    name: 'Campus Cloud Dashboard', short_name: 'CCD', institution: '', tagline: 'Clientless Campus Cloud',
    registration_open: true, email_required: false, email_domains: [],
    accent_color: '', default_language: 'id', default_theme: 'dark', logo_version: null, announcement: null,
};
const KEY = 'ccd_branding';

function stored() {
    try { return { ...DEFAULT_BRANDING, ...JSON.parse(localStorage.getItem(KEY) || '{}') }; } catch { return DEFAULT_BRANDING; }
}

// Warna aksen: versi asli untuk tema gelap, versi lebih gelap untuk tema terang (kontras di latar putih).
export function accentVars(hex) {
    const m = /^#([0-9a-f]{6})$/i.exec(hex || '');
    if (!m) return null;
    const n = parseInt(m[1], 16);
    const rgb = [n >> 16, (n >> 8) & 255, n & 255];
    const shade = (f) => '#' + rgb.map(c => Math.round(c * f).toString(16).padStart(2, '0')).join('');
    const glow = (a, f = 1) => `rgba(${rgb.map(c => Math.round(c * f)).join(', ')}, ${a})`;
    return {
        dark: { '--cyan': hex.toLowerCase(), '--cyan2': shade(0.8), '--cyan-glow': glow(0.12), '--blue': shade(0.6) },
        light: { '--cyan': shade(0.6), '--cyan2': shade(0.5), '--cyan-glow': glow(0.1, 0.6), '--blue': shade(0.45) },
    };
}

export const logoUrl = (b) => (b.logo_version ? `/api/v1/system/logo?v=${b.logo_version}` : null);

function applyTheme(b) {
    if (typeof document === 'undefined') return;
    document.title = b.name;
    let style = document.getElementById('ccd-accent');
    const vars = accentVars(b.accent_color);
    if (vars) {
        if (!style) { style = document.createElement('style'); style.id = 'ccd-accent'; document.head.appendChild(style); }
        const css = (o) => Object.entries(o).map(([k, v]) => `${k}:${v}`).join(';');
        style.textContent = `:root{${css(vars.dark)}}[data-theme="light"]{${css(vars.light)}}`;
    } else if (style) {
        style.remove();
    }
    const icon = document.querySelector('link[rel="icon"]');
    if (icon) {
        if (!icon.dataset.default) icon.dataset.default = icon.getAttribute('href') || '';
        icon.setAttribute('href', logoUrl(b) || icon.dataset.default);
    }
    setDefaultLanguage(b.default_language);
    setDefaultTheme(b.default_theme);
}

let state = stored();
const listeners = new Set();
applyTheme(state);

export function setBranding(b) {
    state = { ...DEFAULT_BRANDING, ...b };
    try { localStorage.setItem(KEY, JSON.stringify(state)); } catch { /* mode privat */ }
    applyTheme(state);
    listeners.forEach(fn => fn());
}

export function loadBranding() {
    return fetchBranding().then(setBranding).catch(() => {});
}

export function useBranding() {
    return useSyncExternalStore(fn => { listeners.add(fn); return () => listeners.delete(fn); }, () => state);
}

// Huruf di kotak logo: huruf pertama nama singkat.
export const logoLetter = (b) => (b.short_name || b.name || 'C').trim().charAt(0).toUpperCase();
