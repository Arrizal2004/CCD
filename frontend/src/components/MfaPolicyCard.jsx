import { useEffect, useState } from 'react';
import { fetchMfaPolicy, saveMfaPolicy } from '../api';
import { useT } from '../i18n';

/** Kartu kebijakan 2FA di Pengaturan Sistem (superadmin). Disimpan sendiri, terpisah dari form utama. */
export default function MfaPolicyCard() {
    const t = useT();
    const [value, setValue] = useState('off');
    const [msg, setMsg] = useState(null);
    useEffect(() => { fetchMfaPolicy().then(p => setValue(p.required)).catch(() => {}); }, []);
    const save = async () => {
        try { await saveMfaPolicy(value); setMsg({ ok: true, text: t('mfa.policySaved') }); }
        catch (e) { setMsg({ ok: false, text: e?.response?.data?.detail || e.message }); }
    };
    return (
        <div style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', borderRadius: 10, padding: 18, marginBottom: 16 }}>
            <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text)', marginBottom: 6 }}>{t('mfa.policyTitle')}</div>
            <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 12, lineHeight: 1.5 }}>{t('mfa.policyHint')}</div>
            <select value={value} onChange={e => setValue(e.target.value)} aria-label={t('mfa.policyTitle')}
                style={{ width: '100%', boxSizing: 'border-box', background: 'var(--bg-card2)', border: '1px solid var(--border)', borderRadius: 6, padding: '8px 10px', color: 'var(--text)', fontSize: 13, marginBottom: 10 }}>
                {['off', 'admins', 'all'].map(v => <option key={v} value={v}>{t(`mfa.policy.${v}`)}</option>)}
            </select>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <button type="button" onClick={save} style={{ padding: '7px 16px', borderRadius: 6, fontSize: 12, fontWeight: 600, background: 'var(--cyan)', color: '#000', border: 'none', cursor: 'pointer' }}>{t('mfa.policySave')}</button>
                {msg && <span style={{ fontSize: 12, color: msg.ok ? 'var(--green)' : 'var(--red)' }}>{msg.ok ? '✓' : '⚠'} {msg.text}</span>}
            </div>
        </div>
    );
}
