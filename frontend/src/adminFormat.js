// Format dan gaya bersama untuk halaman Audit & Remote (AdminPanel) dan tampilan aktivitas pengguna.
import { locale, t } from './i18n';

export const ROLE_COLOR = { superadmin: '#ff6b35', sysadmin: 'var(--cyan)', student: 'var(--purple)' };
export const SEV_COLOR = { CRITICAL: 'var(--red)', WARNING: 'var(--yellow)', INFO: 'var(--cyan)' };
export const PAGE = 50;

export function fmtTime(iso) {
    if (!iso) return '—';
    try {
        return new Date(iso).toLocaleString(locale(), { timeZone: 'Asia/Jakarta', day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' });
    } catch { return iso; }
}
export function fmtEpoch(ms) {
    if (!ms) return '—';
    try { return new Date(ms).toLocaleString(locale(), { timeZone: 'Asia/Jakarta', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' }); }
    catch { return '—'; }
}
export function fmtDur(s) {
    if (s == null) return '—';
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    return h > 0 ? `${h}h ${m}m` : m > 0 ? `${m}m ${sec}s` : `${sec}s`;
}
export function fmtBytes(n) {
    if (n == null) return '—';
    const u = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0, v = n;
    while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
    return `${v.toFixed(i && v < 10 ? 1 : 0)} ${u[i]}`;
}

// Label peran untuk lencana; peran yang tidak dikenal (mis. '-' pada login gagal) ditampilkan apa adanya.
export const roleLabel = (r) => (['superadmin', 'sysadmin', 'student', 'admin'].includes(r) ? t(`role.${r}`) : r);

// Status sesi → [kunci teks, warna]
export const WEB_STATUS = { active: ['admin.stActive', 'var(--green)'], expired: ['admin.stExpired', 'var(--text3)'], revoked: ['admin.stRevoked', 'var(--red)'] };
export const SSH_STATUS = {
    active: ['admin.stActive', 'var(--green)'], closed: ['admin.stClosed', 'var(--text3)'], timeout: ['admin.stTimeout', 'var(--yellow)'],
    error: ['admin.stDropped', 'var(--yellow)'], restart: ['admin.stRestart', 'var(--text3)'], lost: ['admin.stUnknown', 'var(--text3)'],
    killed: ['admin.stKilled', 'var(--red)'],
};
export const statusOf = (map, s) => {
    const [key, color] = map[s] || [null, 'var(--text3)'];
    return [key ? t(key) : s, color];
};

export const TH = { padding: '8px 12px', textAlign: 'left', fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.07em', fontWeight: 600, whiteSpace: 'nowrap', borderBottom: '1px solid var(--border)' };
export const TD = { padding: '8px 12px', fontSize: 12, color: 'var(--text2)', borderBottom: '1px solid var(--border)', verticalAlign: 'top' };
export const inp = { background: 'var(--bg-hover)', border: '1px solid var(--border)', borderRadius: 6, padding: '6px 10px', color: 'var(--text)', fontSize: 12, fontFamily: 'var(--fmono)', outline: 'none' };
export const btn = { background: 'var(--bg-hover)', border: '1px solid var(--border)', borderRadius: 6, padding: '6px 12px', color: 'var(--text2)', fontSize: 12, cursor: 'pointer', fontFamily: 'var(--fmono)' };
export const card = { background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 12, overflow: 'hidden' };
export const killBtn = { padding: '4px 12px', borderRadius: 6, fontSize: 11, fontFamily: 'var(--fmono)', cursor: 'pointer', background: 'var(--red-glow, #ff174422)', color: 'var(--red)', border: '1px solid var(--red)', whiteSpace: 'nowrap' };
export const killBtnMobile = { width: '100%', marginTop: 10, padding: '9px 12px', borderRadius: 8, fontSize: 13, fontFamily: 'var(--fmono)', cursor: 'pointer', background: 'var(--red-glow, #ff174422)', color: 'var(--red)', border: '1px solid var(--red)' };
