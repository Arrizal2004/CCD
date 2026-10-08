import { t, locale } from './i18n';

// Bytes → unit terbesar yang masih >= 1, mis. "15.0 GB", "512 MB".
export function formatBytes(n) {
    if (n == null) return '—';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0, v = n;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return `${v.toFixed(v >= 10 ? 0 : 1)} ${units[i]}`;
}

// Detik → "Xd Yh" / "Xh Ym" / "Xm", dipakai untuk uptime VM/host.
export function formatUptime(sec) {
    if (!sec) return '—';
    const d = Math.floor(sec / 86400);
    const h = Math.floor((sec % 86400) / 3600);
    const m = Math.floor((sec % 3600) / 60);
    if (d > 0) return `${d}d ${h}h`;
    if (h > 0) return `${h}h ${m}m`;
    return `${m}m`;
}

// Milidetik → "m:ss" / "h:mm:ss", dipakai untuk durasi & posisi rekaman sesi.
export function formatDurationMs(ms) {
    if (ms == null) return '—';
    const s = Math.floor(ms / 1000);
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    const mm = String(m).padStart(2, '0'), ss = String(sec).padStart(2, '0');
    return h ? `${h}:${mm}:${ss}` : `${m}:${ss}`;
}

// CCDID: nomor VM yang unik di seluruh dashboard (VMID hanya unik per Proxmox). 7 → "CCD-0007".
export function formatCcdId(id) {
    return id == null ? '—' : `CCD-${String(id).padStart(4, '0')}`;
}

// Masa sewa VM atau masa berlaku akun -> teks singkat dan warna. null = tanpa batas.
export function leaseInfo(iso, now = Date.now()) {
    if (!iso) return { text: t('lease.none'), short: '—', color: 'var(--text3)', expired: false, days: null };
    const until = new Date(iso);
    const ms = until.getTime() - now;
    const date = until.toLocaleDateString(locale(), { day: '2-digit', month: 'short', year: 'numeric' });
    if (ms <= 0) return { text: t('lease.expiredSince', { date }), short: t('lease.expired'), color: 'var(--red)', expired: true, days: 0 };
    const days = Math.ceil(ms / 86400000);
    const color = days <= 3 ? 'var(--yellow)' : 'var(--green)';
    return { text: t('lease.until', { date, days }), short: t('lease.days', { days }), color, expired: false, days };
}
