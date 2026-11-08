import { useEffect, useState } from 'react';
import { fetchEmailSettings, saveEmailSettings, sendTestEmail, fetchEmailOutbox } from '../api';
import { useT } from '../i18n';

const label = { display: 'block', fontSize: 11, color: 'var(--text3)', marginBottom: 5, textTransform: 'uppercase', letterSpacing: '0.06em' };
const input = { width: '100%', boxSizing: 'border-box', background: 'var(--bg-card2)', border: '1px solid var(--border)', borderRadius: 6, padding: '8px 10px', color: 'var(--text)', fontSize: 13, outline: 'none' };
const hint = { fontSize: 11, color: 'var(--text3)', marginTop: 4, lineHeight: 1.5 };

function Check({ checked, onChange, children, id }) {
    return (
        <label htmlFor={id} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: 'var(--text)', cursor: 'pointer', marginBottom: 6 }}>
            <input id={id} type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)} />{children}
        </label>
    );
}

/** Kartu Email (SMTP) di Pengaturan Sistem (superadmin). Disimpan sendiri, terpisah dari form utama. */
export default function EmailSettingsCard() {
    const t = useT();
    const [cfg, setCfg] = useState(null);
    const [password, setPassword] = useState('');
    const [testTo, setTestTo] = useState('');
    const [msg, setMsg] = useState(null);
    const [queue, setQueue] = useState(null);

    useEffect(() => {
        fetchEmailSettings().then(setCfg).catch(() => {});
        fetchEmailOutbox().then(o => setQueue(o.last_7_days)).catch(() => {});
    }, []);
    if (!cfg) return null;

    const smtp = (k, v) => setCfg(c => ({ ...c, smtp: { ...c.smtp, [k]: v } }));
    const notify = (k, v) => setCfg(c => ({ ...c, notify: { ...c.notify, [k]: v } }));
    const fail = (e) => setMsg({ ok: false, text: e?.response?.data?.detail || e.message });

    const save = async () => {
        const { has_password: _hp, ...rest } = cfg.smtp;
        try {
            const out = await saveEmailSettings({ smtp: { ...rest, ...(password ? { password } : {}) }, notify: cfg.notify });
            setCfg(out); setPassword(''); setMsg({ ok: true, text: t('email.saved') });
        } catch (e) { fail(e); }
    };
    const test = async () => {
        try { await sendTestEmail(testTo.trim()); setMsg({ ok: true, text: t('email.testOk') }); } catch (e) { fail(e); }
    };

    const q = queue || {};
    return (
        <div data-testid="email-card" style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', borderRadius: 10, padding: 18, marginBottom: 16 }}>
            <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text)', marginBottom: 6 }}>{t('email.title')}</div>
            <div style={{ ...hint, marginBottom: 14 }}>{t('email.hint')}</div>

            <Check id="em-enabled" checked={!!cfg.smtp.enabled} onChange={v => smtp('enabled', v)}>{t('email.enabled')}</Check>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(200px,1fr))', gap: 12, margin: '10px 0' }}>
                <div><label style={label} htmlFor="em-host">{t('email.host')}</label>
                    <input id="em-host" value={cfg.smtp.host} onChange={e => smtp('host', e.target.value)} placeholder="smtp.example.com" style={input} /></div>
                <div><label style={label} htmlFor="em-port">{t('email.port')}</label>
                    <input id="em-port" type="number" value={cfg.smtp.port} onChange={e => smtp('port', e.target.value)} style={input} /></div>
                <div><label style={label} htmlFor="em-sec">{t('email.security')}</label>
                    <select id="em-sec" value={cfg.smtp.security} onChange={e => smtp('security', e.target.value)} style={input}>
                        {['starttls', 'ssl', 'none'].map(v => <option key={v} value={v}>{t(`email.sec.${v}`)}</option>)}
                    </select></div>
                <div><label style={label} htmlFor="em-user">{t('email.username')}</label>
                    <input id="em-user" value={cfg.smtp.username} onChange={e => smtp('username', e.target.value)} autoComplete="off" style={input} /></div>
                <div><label style={label} htmlFor="em-pass">{t('email.password')}</label>
                    <input id="em-pass" type="password" value={password} onChange={e => setPassword(e.target.value)} autoComplete="new-password" style={input} />
                    {cfg.smtp.has_password && <div style={hint}>{t('email.passwordSet')}</div>}</div>
                <div><label style={label} htmlFor="em-from">{t('email.fromAddress')}</label>
                    <input id="em-from" value={cfg.smtp.from_address} onChange={e => smtp('from_address', e.target.value)} placeholder="no-reply@example.com" style={input} /></div>
                <div><label style={label} htmlFor="em-name">{t('email.fromName')}</label>
                    <input id="em-name" value={cfg.smtp.from_name} onChange={e => smtp('from_name', e.target.value)} style={input} /></div>
                <div><label style={label} htmlFor="em-reply">{t('email.replyTo')}</label>
                    <input id="em-reply" value={cfg.smtp.reply_to} onChange={e => smtp('reply_to', e.target.value)} style={input} /></div>
            </div>
            <div style={{ marginBottom: 12 }}>
                <label style={label} htmlFor="em-url">{t('email.dashboardUrl')}</label>
                <input id="em-url" value={cfg.smtp.dashboard_url} onChange={e => smtp('dashboard_url', e.target.value)} placeholder="https://ccd.example.com" style={input} />
                <div style={hint}>{t('email.dashboardUrlHint')}</div>
            </div>

            <Check id="em-selfreset" checked={!!cfg.notify.self_reset} onChange={v => notify('self_reset', v)}>{t('email.selfReset')}</Check>
            <div style={{ ...hint, marginBottom: 10 }}>{t('email.selfResetHint')}</div>
            <Check id="em-lease" checked={!!cfg.notify.lease} onChange={v => notify('lease', v)}>{t('email.lease')}</Check>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14, fontSize: 12, color: 'var(--text2)' }}>
                <label htmlFor="em-days">{t('email.leaseDays')}</label>
                <input id="em-days" type="number" min={1} max={30} value={cfg.notify.lease_days_before}
                    onChange={e => notify('lease_days_before', e.target.value)} style={{ ...input, width: 80 }} />
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 14 }}>
                <button type="button" onClick={save} style={{ padding: '7px 16px', borderRadius: 6, fontSize: 12, fontWeight: 600, background: 'var(--cyan)', color: '#000', border: 'none', cursor: 'pointer' }}>{t('email.save')}</button>
                {msg && <span style={{ fontSize: 12, color: msg.ok ? 'var(--green)' : 'var(--red)' }}>{msg.ok ? '✓' : '⚠'} {msg.text}</span>}
            </div>

            <div style={{ display: 'flex', gap: 8, alignItems: 'end', flexWrap: 'wrap' }}>
                <div style={{ flex: '1 1 220px' }}>
                    <label style={label} htmlFor="em-test">{t('email.testTo')}</label>
                    <input id="em-test" value={testTo} onChange={e => setTestTo(e.target.value)} placeholder="nama@example.com" style={input} />
                </div>
                <button type="button" onClick={test} disabled={!testTo.trim()} style={{ padding: '8px 14px', borderRadius: 6, fontSize: 12, background: 'transparent', border: '1px solid var(--border)', color: 'var(--text2)', cursor: 'pointer' }}>{t('email.testSend')}</button>
            </div>
            {queue && <div style={{ ...hint, marginTop: 10 }}>{t('email.queue', { sent: q.sent || 0, pending: q.pending || 0, failed: q.failed || 0 })}</div>}
        </div>
    );
}
