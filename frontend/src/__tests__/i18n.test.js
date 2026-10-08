import { describe, expect, it } from 'vitest';
import { CATALOG, DUPLICATE_KEYS } from '../i18n';

// Semua kode sumber (kecuali test dan kamus) sebagai teks, untuk mencari kunci yang dipakai.
const SOURCES = import.meta.glob(['../**/*.{js,jsx}', '!../__tests__/**', '!../locales/**'], { query: '?raw', import: 'default', eager: true });
const placeholders = (text) => [...text.matchAll(/\{(\w+)\}/g)].map(m => m[1]).sort();

describe('kamus bahasa', () => {
    it('tidak ada kunci yang didefinisikan dua kali', () => {
        expect(DUPLICATE_KEYS).toEqual([]);
    });

    it('setiap kunci punya teks Indonesia dan Inggris yang tidak kosong', () => {
        const bad = Object.entries(CATALOG)
            .filter(([, pair]) => !Array.isArray(pair) || pair.length !== 2 || pair.some(x => typeof x !== 'string' || !x.trim()))
            .map(([k]) => k);
        expect(bad).toEqual([]);
    });

    it('placeholder {nama} sama di kedua bahasa', () => {
        const bad = Object.entries(CATALOG)
            .filter(([, [id, en]]) => placeholders(id).join() !== placeholders(en).join())
            .map(([k]) => k);
        expect(bad).toEqual([]);
    });

    it('setiap t("kunci") di kode ada di kamus', () => {
        const missing = [];
        for (const [file, text] of Object.entries(SOURCES)) {
            for (const m of text.matchAll(/\b(?:t|tr|tNodes)\(\s*'([A-Za-z][\w.-]*)'/g)) {
                if (!(m[1] in CATALOG)) missing.push(`${m[1]} (${file})`);
            }
        }
        expect(missing).toEqual([]);
    });
});
