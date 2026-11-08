import { useEffect, useState } from 'react';
import { fetchLabSettings, saveLabSettings } from '../api';
import { useT } from '../i18n';

/** Kartu di Pengaturan Sistem (superadmin): izinkan mahasiswa me-reset VM sendiri. */
export default function LabSettingsCard() {
    const t = useT();
    const [allow, setAllow] = useState(null);
    const [msg, setMsg] = useState(null);
    useEffect(() => { fetchLabSettings().then(s => setAllow(!!s.students_can_reset)).catch(() => {}); }, []);
    if (allow === null) return null;
    const toggle = async (v) => {
        setAllow(v);
        try { await saveLabSettings({ students_can_reset: v }); setMsg({ ok: true, text: t('lab.settingsSaved') }); }
        catch (e) { setAllow(!v); setMsg({ ok: false, text: e?.response?.data?.detail || e.message }); }
    };
    return (
        <div style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', borderRadius: 10, padding: 18, marginBottom: 16 }}>
            <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text)', marginBottom: 6 }}>{t('lab.settingsTitle')}</div>
            <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 10, lineHeight: 1.5 }}>{t('lab.settingsHint')}</div>
            <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13 }}>
                <input type="checkbox" checked={allow} onChange={e => toggle(e.target.checked)} /> {t('lab.settingsAllow')}
            </label>
            {msg && <div style={{ fontSize: 12, marginTop: 8, color: msg.ok ? 'var(--green)' : 'var(--red)' }}>{msg.ok ? '✓' : '⚠'} {msg.text}</div>}
        </div>
    );
}
