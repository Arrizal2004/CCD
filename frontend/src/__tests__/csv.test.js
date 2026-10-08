import { describe, expect, it } from 'vitest';
import { CSV_TEMPLATE, credentialsCsv, parseCsv } from '../csv';

describe('parseCsv', () => {
    it('membaca template bawaan', () => {
        const { rows, unknown } = parseCsv(CSV_TEMPLATE);
        expect(unknown).toEqual([]);
        expect(rows).toHaveLength(2);
        expect(rows[0]).toEqual({ username: 'siswa01', full_name: 'Budi Santoso', email: 'budi@sekolah.sch.id',
            role: 'student', expires_at: '2027-06-30', group: 'XII-TKJ-1' });
    });

    it('menerima titik koma dari Excel, BOM, tanda kutip, dan nama kolom bahasa Indonesia', () => {
        const text = '\uFEFFusername;Nama Lengkap;Kelas;Catatan\r\nani;"Ani; S.Kom";XI-RPL;abaikan\r\n\r\nbayu;"Bayu ""BJ"" Jaya";XI-RPL;\r\n';
        const { rows, unknown } = parseCsv(text);
        expect(unknown).toEqual(['catatan']);
        expect(rows).toEqual([
            { username: 'ani', full_name: 'Ani; S.Kom', group: 'XI-RPL' },
            { username: 'bayu', full_name: 'Bayu "BJ" Jaya', group: 'XI-RPL' },
        ]);
    });

    it('file kosong', () => {
        expect(parseCsv('\n  \n')).toEqual({ rows: [], unknown: [] });
    });
});

describe('credentialsCsv', () => {
    it('menulis header dan satu baris per akun', () => {
        expect(credentialsCsv([{ username: 'a', password: 'x1' }, { username: 'b', password: 'y2' }]))
            .toBe('username,password\na,x1\nb,y2\n');
    });
});
