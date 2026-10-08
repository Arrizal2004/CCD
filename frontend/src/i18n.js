import { Fragment, createElement, useSyncExternalStore } from 'react';

// Bahasa antarmuka: pilihan pengguna (disimpan di browser) atau bahasa bawaan dari Pengaturan Sistem.
// Semua teks ada di src/locales/*.js dengan format  kunci: [Indonesia, English]. Setiap berkas di folder itu
// dimuat otomatis. Test i18n memastikan setiap kunci punya kedua bahasa dan setiap kunci yang dipakai di kode ada.
const MODULES = import.meta.glob('./locales/*.js', { eager: true });

export const CATALOG = {};
export const DUPLICATE_KEYS = [];
for (const [file, mod] of Object.entries(MODULES)) {
    for (const [key, pair] of Object.entries(mod.default)) {
        if (key in CATALOG) DUPLICATE_KEYS.push(`${key} (${file})`);
        CATALOG[key] = pair;
    }
}
const DICT = { id: {}, en: {} };
for (const [key, [id, en]] of Object.entries(CATALOG)) {
    DICT.id[key] = id;
    DICT.en[key] = en;
}

export const LANGUAGES = [['id', 'ID'], ['en', 'EN']];
const KEY = 'ccd_lang';

let chosen = (() => { try { return localStorage.getItem(KEY); } catch { return null; } })();
let fallback = 'id';
const listeners = new Set();
const notify = () => listeners.forEach(fn => fn());

export const currentLang = () => (chosen && DICT[chosen] ? chosen : fallback);
export const locale = () => (currentLang() === 'en' ? 'en-GB' : 'id-ID');

export function setLanguage(lang) {
    chosen = lang;
    try { localStorage.setItem(KEY, lang); } catch { /* mode privat */ }
    document.documentElement.lang = lang;
    notify();
}

// Dipanggil saat pengaturan sistem dimuat; tidak menimpa pilihan pengguna.
export function setDefaultLanguage(lang) {
    if (!DICT[lang] || lang === fallback) return;
    fallback = lang;
    if (!chosen) document.documentElement.lang = lang;
    notify();
}

export function t(key, vars) {
    const text = DICT[currentLang()][key] ?? DICT.id[key] ?? key;
    return vars ? text.replace(/\{(\w+)\}/g, (_, k) => (vars[k] ?? '')) : text;
}

// Seperti t(), tetapi {nama} boleh diisi elemen React (mis. <strong>), supaya urutan kata tetap mengikuti
// bahasanya: tNodes('users.deleteMsg', { name: <strong>{u}</strong> }).
export function tNodes(key, nodes = {}) {
    return t(key).split(/\{(\w+)\}/).map((part, i) =>
        createElement(Fragment, { key: i }, i % 2 ? (nodes[part] ?? '') : part));
}

// Komponen yang memakai hook ini ikut digambar ulang saat bahasa berganti.
export function useT() {
    useSyncExternalStore(fn => { listeners.add(fn); return () => listeners.delete(fn); }, currentLang);
    return t;
}
