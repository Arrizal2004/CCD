import { useState } from 'react';
import { useT } from '../i18n';

// Pengumuman dari Pengaturan Sistem. Pengguna bisa menutupnya; pengumuman baru (teks atau tanggal
// mulai berbeda) muncul lagi. Status "ditutup" hanya disimpan di browser ini.
const COLORS = { info: 'var(--cyan)', warning: 'var(--yellow)', critical: 'var(--red)' };
const ICONS = { info: 'ℹ️', warning: '⚠️', critical: '⛔' };
const KEY = 'ccd_dismissed_announcement';

function dismissedId() {
    try { return localStorage.getItem(KEY); } catch { return null; }
}

export default function AnnouncementBanner({ announcement, style }) {
    const t = useT();
    const [closed, setClosed] = useState(dismissedId);
    if (!announcement?.text || closed === announcement.id) return null;
    const color = COLORS[announcement.level] || COLORS.info;
    const dismiss = () => {
        try { localStorage.setItem(KEY, announcement.id); } catch { /* mode privat */ }
        setClosed(announcement.id);
    };
    return (
        <div role="status" style={{ display: 'flex', alignItems: 'flex-start', gap: 10, padding: '10px 14px', borderRadius: 8, background: 'var(--bg-card2)', border: `1px solid ${color}`, borderLeft: `4px solid ${color}`, ...style }}>
            <span aria-hidden="true">{ICONS[announcement.level] || ICONS.info}</span>
            <div style={{ flex: 1, minWidth: 0, fontSize: 13, color: 'var(--text)', lineHeight: 1.5, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{announcement.text}</div>
            {announcement.level !== 'critical' && (
                <button onClick={dismiss} title={t('announcement.dismiss')} aria-label={t('announcement.dismiss')}
                    style={{ background: 'none', border: 'none', color: 'var(--text3)', cursor: 'pointer', fontSize: 15, lineHeight: 1, padding: 0 }}>×</button>
            )}
        </div>
    );
}
