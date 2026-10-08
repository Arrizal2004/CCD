import { describe, expect, it } from 'vitest';
import { formatBytes, formatCcdId, leaseInfo } from '../format';

describe('formatCcdId', () => {
    it('memberi awalan CCD- dan nol di depan', () => {
        expect(formatCcdId(7)).toBe('CCD-0007');
        expect(formatCcdId(12345)).toBe('CCD-12345');
        expect(formatCcdId(null)).toBe('—');
    });
});

describe('formatBytes', () => {
    it('memilih satuan terbesar', () => {
        expect(formatBytes(512)).toBe('512 B');
        expect(formatBytes(1536)).toBe('1.5 KB');
        expect(formatBytes(15 * 1024 ** 3)).toBe('15 GB');
        expect(formatBytes(null)).toBe('—');
    });
});

describe('leaseInfo', () => {
    const now = Date.parse('2026-10-05T12:00:00Z');
    const inDays = (d) => new Date(now + d * 86400000).toISOString();

    it('tanpa batas', () => {
        expect(leaseInfo(null, now)).toMatchObject({ short: '—', expired: false, days: null });
    });
    it('sudah habis', () => {
        expect(leaseInfo(inDays(-1), now)).toMatchObject({ short: 'Habis', expired: true, color: 'var(--red)' });
    });
    it('tinggal sedikit berwarna kuning, masih lama berwarna hijau', () => {
        expect(leaseInfo(inDays(2), now)).toMatchObject({ days: 2, color: 'var(--yellow)', expired: false });
        expect(leaseInfo(inDays(30), now)).toMatchObject({ days: 30, color: 'var(--green)' });
    });
});
