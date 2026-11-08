import { useEffect, useState } from 'react';
import { fetchMyMfa, setupMyMfa, enableMyMfa, disableMyMfa, newMfaRecoveryCodes } from '../api';
import { copyText } from '../clipboard';
import { useT } from '../i18n';

const input = { width: '100%', boxSizing: 'border-box', background: 'var(--bg-card2)', border: '1px solid var(--border)', borderRadius: 6, padding: '8px 10px', color: 'var(--text1)', fontSize: 13, outline: 'none', fontFamily: 'var(--fmono)', marginBottom: 8 };
const btn = (primary) => ({ padding: '7px 14px', borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer', background: primary ? 'var(--cyan)' : 'transparent', color: primary ? '#0b0f1a' : 'var(--text2)', border: primary ? 'none' : '1px solid var(--border)' });

/** Kode QR dari server dipasang sebagai gambar (bukan HTML), jadi isinya tidak bisa menjalankan skrip. */
export function QrImage({ svg, size = 168 }) {
    const src = `data:image/svg+xml;utf8,${encodeURIComponent(svg || '')}`;
    return <img src={src} alt="QR" width={size} height={size} style={{ background: '#fff', borderRadius: 6, display: 'block' }} />;
}

export function RecoveryCodes({ codes, onDone }) {
    const t = useT();
    const [copied, setCopied] = useState(false);
    const copy = async () => { if (await copyText(codes.join('\n'))) { setCopied(true); setTimeout(() => setCopied(false), 1500); } };
    return (
        <div>
            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>{t('mfa.recoveryTitle')}</div>
            <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 10, lineHeight: 1.5 }}>{t('mfa.recoveryHint')}</div>
            <div data-testid="recovery-codes" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, fontFamily: 'var(--fmono)', fontSize: 13, background: 'var(--bg-hover)', padding: 12, borderRadius: 8, marginBottom: 10 }}>
                {codes.map(c => <span key={c}>{c}</span>)}
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
                <button type="button" onClick={copy} style={btn(false)}>{copied ? t('mfa.copied') : t('mfa.copy')}</button>
                <button type="button" onClick={onDone} style={btn(true)}>{t('mfa.recoveryDone')}</button>
            </div>
        </div>
    );
}

/** Bagian 2FA di Profil: status, pasang, matikan, dan kode pemulihan baru. */
export default function MfaSection() {
    const t = useT();
    const [st, setSt] = useState(null);
    const [mode, setMode] = useState('');            // '' | 'enroll' | 'disable' | 'regen'
    const [enroll, setEnroll] = useState(null);
    const [code, setCode] = useState('');
    const [password, setPassword] = useState('');
    const [codes, setCodes] = useState(null);
    const [err, setErr] = useState('');
    const [busy, setBusy] = useState(false);

    const load = () => fetchMyMfa().then(setSt).catch(() => setSt(null));
    useEffect(() => { load(); }, []);

    const reset = () => { setMode(''); setCode(''); setPassword(''); setErr(''); setEnroll(null); };
    const run = async (fn) => {
        setBusy(true); setErr('');
        try { await fn(); } catch (e) { setErr(e?.response?.data?.detail || e.message); } finally { setBusy(false); }
    };

    const startEnroll = () => run(async () => { setEnroll(await setupMyMfa()); setMode('enroll'); });
    const confirmEnroll = (e) => { e.preventDefault(); run(async () => { const r = await enableMyMfa(code); reset(); setCodes(r.recovery_codes); await load(); }); };
    const confirmDisable = (e) => { e.preventDefault(); run(async () => { await disableMyMfa(code, password); reset(); await load(); }); };
    const confirmRegen = (e) => { e.preventDefault(); run(async () => { const r = await newMfaRecoveryCodes(code); reset(); setCodes(r.recovery_codes); await load(); }); };

    if (!st) return null;
    return (
        <div style={{ padding: '18px 20px 20px', borderTop: '1px solid var(--border)' }} data-testid="mfa-section">
            <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', marginBottom: 10, textTransform: 'uppercase', letterSpacing: '0.08em' }}>
                {t('mfa.section')}
            </div>

            {codes ? <RecoveryCodes codes={codes} onDone={() => setCodes(null)} /> : (
                <>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10, flexWrap: 'wrap' }}>
                        <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 4, background: st.enabled ? 'rgba(34,197,94,0.12)' : 'var(--bg-hover)', color: st.enabled ? 'var(--green)' : 'var(--text3)', fontWeight: 600 }}>
                            {st.enabled ? t('mfa.on') : t('mfa.off')}
                        </span>
                        {st.enabled && <span style={{ fontSize: 11, color: 'var(--text3)' }}>{t('mfa.codesLeft', { n: st.recovery_codes_left })}</span>}
                        {st.required && <span style={{ fontSize: 11, color: 'var(--yellow)' }}>{t('mfa.requiredByPolicy')}</span>}
                    </div>

                    {mode === '' && (
                        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                            {!st.enabled && <button type="button" disabled={busy} onClick={startEnroll} style={btn(true)}>{t('mfa.enable')}</button>}
                            {st.enabled && <button type="button" onClick={() => setMode('regen')} style={btn(false)}>{t('mfa.regenerate')}</button>}
                            {st.enabled && !st.required && <button type="button" onClick={() => setMode('disable')} style={btn(false)}>{t('mfa.disable')}</button>}
                        </div>
                    )}

                    {mode === 'enroll' && enroll && (
                        <form onSubmit={confirmEnroll}>
                            <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 10, lineHeight: 1.5 }}>{t('mfa.scan')}</div>
                            <div style={{ display: 'flex', justifyContent: 'center', marginBottom: 10 }}><QrImage svg={enroll.qr_svg} /></div>
                            <div style={{ fontSize: 11, color: 'var(--text3)' }}>{t('mfa.orEnterKey')}</div>
                            <code style={{ display: 'block', fontSize: 12, wordBreak: 'break-all', margin: '4px 0 10px' }}>{enroll.secret}</code>
                            <input value={code} onChange={e => setCode(e.target.value)} inputMode="numeric" autoComplete="one-time-code" maxLength={8} placeholder="123456" aria-label={t('mfa.code')} style={input} />
                            <div style={{ display: 'flex', gap: 8 }}>
                                <button type="submit" disabled={busy || !code} style={btn(true)}>{t('mfa.confirmEnable')}</button>
                                <button type="button" onClick={reset} style={btn(false)}>{t('mfa.cancel')}</button>
                            </div>
                        </form>
                    )}

                    {mode === 'disable' && (
                        <form onSubmit={confirmDisable}>
                            <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 8 }}>{t('mfa.disableHint')}</div>
                            <input type="password" value={password} onChange={e => setPassword(e.target.value)} autoComplete="current-password" placeholder={t('mfa.password')} aria-label={t('mfa.password')} style={input} />
                            <input value={code} onChange={e => setCode(e.target.value)} autoComplete="one-time-code" placeholder={t('mfa.codePh')} aria-label={t('mfa.code')} style={input} />
                            <div style={{ display: 'flex', gap: 8 }}>
                                <button type="submit" disabled={busy || !code || !password} style={btn(true)}>{t('mfa.disable')}</button>
                                <button type="button" onClick={reset} style={btn(false)}>{t('mfa.cancel')}</button>
                            </div>
                        </form>
                    )}

                    {mode === 'regen' && (
                        <form onSubmit={confirmRegen}>
                            <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 8 }}>{t('mfa.regenerateHint')}</div>
                            <input value={code} onChange={e => setCode(e.target.value)} inputMode="numeric" autoComplete="one-time-code" maxLength={8} placeholder="123456" aria-label={t('mfa.code')} style={input} />
                            <div style={{ display: 'flex', gap: 8 }}>
                                <button type="submit" disabled={busy || !code} style={btn(true)}>{t('mfa.regenerate')}</button>
                                <button type="button" onClick={reset} style={btn(false)}>{t('mfa.cancel')}</button>
                            </div>
                        </form>
                    )}
                    {err && <div role="alert" style={{ marginTop: 10, fontSize: 12, color: 'var(--red)' }}>⚠ {err}</div>}
                </>
            )}
        </div>
    );
}
