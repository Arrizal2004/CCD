import { useEffect, useId, useState } from 'react';
import axios from 'axios';
import { registerStudent, requestPasswordHelp, changePassword, storeSession,
    fetchSelfResetAvailable, requestPasswordReset, verifyResetToken, confirmPasswordReset } from '../api';
import { useBranding } from '../branding';
import { useT } from '../i18n';
import BrandLogo from '../components/BrandLogo';
import LanguageToggle from '../components/LanguageToggle';
import ThemeToggle from '../components/ThemeToggle';
import AnnouncementBanner from '../components/AnnouncementBanner';
import Icon from '../components/Icons';
import { QrImage, RecoveryCodes } from '../components/MfaSection';

const BASE = import.meta.env.VITE_API_URL || '';

// ── Shared page chrome ──────────────────────────────────────────────────────
function PageShell({ children }) {
    const b = useBranding();
    const t = useT();
    return (
        <div style={{
            minHeight: '100vh', background: 'var(--bg)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontFamily: 'var(--font)', position: 'relative', overflow: 'hidden',
        }}>
            <div style={{ position: 'absolute', inset: 0, opacity: 0.03, backgroundImage: 'linear-gradient(var(--cyan) 1px, transparent 1px), linear-gradient(90deg, var(--cyan) 1px, transparent 1px)', backgroundSize: '40px 40px' }} />
            <div style={{ position: 'absolute', top: '20%', left: '15%', width: 400, height: 400, background: 'var(--cyan)', borderRadius: '50%', filter: 'blur(120px)', opacity: 0.04 }} />
            <div style={{ position: 'absolute', bottom: '20%', right: '15%', width: 300, height: 300, background: 'var(--purple)', borderRadius: '50%', filter: 'blur(100px)', opacity: 0.04 }} />

            {/* Bahasa dan tema bisa dipilih sebelum login, juga oleh calon pengguna yang belum punya akun. */}
            <div style={{ position: 'absolute', top: 16, right: 16, zIndex: 2, display: 'flex', alignItems: 'center', gap: 8 }}>
                <LanguageToggle />
                <ThemeToggle />
            </div>

            <div style={{ width: 'min(440px,95vw)', position: 'relative', zIndex: 1, padding: '40px 0' }}>
                {/* Branding */}
                <div style={{ textAlign: 'center', marginBottom: 28 }}>
                    <div style={{ display: 'flex', justifyContent: 'center', marginBottom: 16 }}>
                        <BrandLogo size={64} radius={16} shadow="0 8px 32px var(--cyan-glow)" />
                    </div>
                    <div style={{ fontSize: 22, fontWeight: 700, letterSpacing: '-0.02em' }}>{b.name}</div>
                    {b.tagline && <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 4, fontFamily: 'var(--fmono)' }}>{b.tagline}</div>}
                    {b.institution && <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 6 }}>{b.institution}</div>}
                </div>

                {b.announcement && <AnnouncementBanner announcement={b.announcement} style={{ marginBottom: 16 }} />}

                {children}

                <div style={{ textAlign: 'center', marginTop: 20, fontSize: 11, color: 'var(--text3)', lineHeight: 1.6 }}>
                    {b.name === 'Campus Cloud Dashboard' ? t('login.version') : t('login.basedOn')} · open source (MIT)<br />
                    <span style={{ fontFamily: 'var(--fmono)', fontSize: 10 }}>Proxmox VE + Apache Guacamole</span>
                </div>
            </div>
        </div>
    );
}

// ── Input helper ────────────────────────────────────────────────────────────
function Input({ label, value, onChange, type = 'text', placeholder, autoFocus, autoComplete, hasError }) {
    const [focused, setFocused] = useState(false);
    const id = useId();
    const borderColor = hasError ? 'var(--red)' : focused ? 'var(--cyan)' : 'var(--border-light)';
    return (
        <div style={{ marginBottom: 14 }}>
            <label htmlFor={id} style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.08em', display: 'block', marginBottom: 6 }}>{label}</label>
            <input id={id} value={value} onChange={e => onChange(e.target.value)} type={type}
                placeholder={placeholder} autoFocus={autoFocus} autoComplete={autoComplete}
                onFocus={() => setFocused(true)} onBlur={() => setFocused(false)}
                style={{ width: '100%', background: 'var(--bg-hover)', border: `1px solid ${borderColor}`, borderRadius: 8, padding: '10px 12px', color: 'var(--text)', fontSize: 13, fontFamily: 'var(--fmono)', outline: 'none', boxSizing: 'border-box' }} />
        </div>
    );
}

function ErrorBox({ msg }) {
    if (!msg) return null;
    return (
        <div style={{ padding: '8px 12px', background: 'var(--red-glow)', border: '1px solid var(--red)44', borderRadius: 6, color: 'var(--red)', fontSize: 12, marginBottom: 16, fontFamily: 'var(--fmono)' }}>
            ⚠ {msg}
        </div>
    );
}

// ── Login form ──────────────────────────────────────────────────────────────
const CARD = { background: 'var(--bg-card)', border: '1px solid var(--border-light)', borderRadius: 16, padding: 32, boxShadow: '0 16px 48px rgba(0,0,0,0.3)' };
const LINK = { background: 'none', border: 'none', color: 'var(--cyan)', cursor: 'pointer', fontSize: 12, fontWeight: 600, padding: 0 };
const submitStyle = (loading) => ({ width: '100%', padding: 11, borderRadius: 8, background: loading ? 'var(--bg-hover)' : 'var(--cyan)', color: loading ? 'var(--text3)' : '#000', fontSize: 13, fontWeight: 700, border: 'none', cursor: loading ? 'not-allowed' : 'pointer', transition: 'all 0.15s' });

function LoginForm({ onLogin, onMfa, notice, onGoRegister, onGoForgot }) {
    const b = useBranding();
    const t = useT();
    const [username, setUser] = useState('');
    const [password, setPass] = useState('');
    const [loading,  setLoad] = useState(false);
    const [error,    setErr]  = useState('');
    const [showPass, setShow] = useState(false);

    const submit = async (e) => {
        e.preventDefault();
        if (!username || !password) { setErr(t('login.required')); return; }
        setLoad(true); setErr('');
        try {
            const r = await axios.post(`${BASE}/api/v1/users/login`, { username, password });
            if (r.data.mfa_token) { onMfa({ ...r.data, username, password }); return; }   // langkah kedua: kode 2FA
            storeSession(r.data.access_token, r.data.guac_auth);
            localStorage.setItem('hv_user', JSON.stringify(r.data.user));
            // Password sementara diteruskan (hanya di memori) supaya tidak perlu diketik ulang di layar ganti password.
            onLogin(r.data.user, r.data.access_token, r.data.user.must_change_password ? password : undefined);
        } catch (e) {
            setErr(e?.response?.data?.detail || t('login.failed'));
        } finally { setLoad(false); }
    };

    return (
        <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border-light)', borderRadius: 16, padding: 32, boxShadow: '0 16px 48px rgba(0,0,0,0.3)' }}>
            <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 24 }}>{t('login.title')}</div>

            {notice && (
                <div style={{ padding: '8px 12px', background: 'var(--yellow-glow)', border: '1px solid var(--yellow)44', borderRadius: 6, color: 'var(--yellow)', fontSize: 12, marginBottom: 16, fontFamily: 'var(--fmono)' }}>
                    {notice}
                </div>
            )}

            <form onSubmit={submit}>
                <Input label="Username" value={username} onChange={setUser} placeholder="username" autoFocus autoComplete="username" hasError={!!error} />

                <div style={{ marginBottom: 20 }}>
                    <label style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.08em', display: 'block', marginBottom: 6 }}>Password</label>
                    <div style={{ position: 'relative' }}>
                        <input value={password} onChange={e => setPass(e.target.value)}
                            type={showPass ? 'text' : 'password'} placeholder="••••••••" autoComplete="current-password"
                            style={{ width: '100%', background: 'var(--bg-hover)', border: `1px solid ${error ? 'var(--red)' : 'var(--border-light)'}`, borderRadius: 8, padding: '10px 36px 10px 12px', color: 'var(--text)', fontSize: 13, fontFamily: 'var(--fmono)', outline: 'none', boxSizing: 'border-box' }}
                            onFocus={e => e.target.style.borderColor = 'var(--cyan)'}
                            onBlur={e => e.target.style.borderColor = error ? 'var(--red)' : 'var(--border-light)'} />
                        <button type="button" onClick={() => setShow(p => !p)} aria-label={t('profile.showPassword')} title={t('profile.showPassword')}
                            style={{ position: 'absolute', right: 10, top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', color: 'var(--text3)', cursor: 'pointer', fontSize: 14 }}>
                            <Icon name={showPass ? 'eyeOff' : 'eye'} />
                        </button>
                    </div>
                </div>

                <ErrorBox msg={error} />

                <button type="submit" disabled={loading} style={submitStyle(loading)}>
                    {loading ? t('login.verifying') : t('login.submit')}
                </button>
            </form>

            <div style={{ marginTop: 12, textAlign: 'center' }}>
                <button type="button" onClick={() => onGoForgot(username)} style={{ ...LINK, fontWeight: 500 }}>
                    {t('login.forgot')}
                </button>
            </div>

            {/* Register link (disembunyikan kalau superadmin menutup pendaftaran mandiri) */}
            <div style={{ marginTop: 20, paddingTop: 16, borderTop: '1px solid var(--border)', textAlign: 'center', fontSize: 12, color: 'var(--text3)' }}>
                {b.registration_open ? (
                    <>
                        {t('login.noAccount')}{' '}
                        <button onClick={onGoRegister}
                            style={{ background: 'none', border: 'none', color: 'var(--cyan)', cursor: 'pointer', fontSize: 12, fontWeight: 600, padding: 0 }}>
                            {t('login.registerNow')}
                        </button>
                    </>
                ) : t('login.askAdmin')}
            </div>
        </div>
    );
}

// ── Langkah kedua login: kode 2FA, atau pemasangan 2FA yang diwajibkan ──────
function MfaStep({ challenge, onLogin, onBack }) {
    const t = useT();
    const setup = !!challenge.mfa_setup_required;
    const [code, setCode] = useState('');
    const [enroll, setEnroll] = useState(null);
    const [error, setErr] = useState('');
    const [loading, setLoad] = useState(false);
    const [pending, setPending] = useState(null);     // hasil login yang menunggu kode pemulihan disimpan
    const mfaToken = challenge.mfa_token;

    useEffect(() => {
        if (!setup) return;
        axios.post(`${BASE}/api/v1/users/login/mfa/setup`, { mfa_token: mfaToken })
            .then(r => setEnroll(r.data)).catch(e => setErr(e?.response?.data?.detail || t('mfa.failed')));
    }, [setup, mfaToken]);                            // eslint-disable-line react-hooks/exhaustive-deps

    const finish = (data) => {
        storeSession(data.access_token, data.guac_auth);
        localStorage.setItem('hv_user', JSON.stringify(data.user));
        onLogin(data.user, data.access_token, data.user.must_change_password ? challenge.password : undefined);
    };

    const submit = async (e) => {
        e.preventDefault();
        if (!code.trim()) return;
        setLoad(true); setErr('');
        try {
            const r = await axios.post(`${BASE}/api/v1/users/login/mfa${setup ? '/enable' : ''}`, { mfa_token: mfaToken, code: code.trim() });
            if (r.data.recovery_codes) setPending(r.data);   // tampilkan kode pemulihan dulu, baru masuk
            else finish(r.data);
        } catch (e2) {
            setErr(e2?.response?.data?.detail || t('mfa.failed'));
        } finally { setLoad(false); }
    };

    if (pending) return <div style={CARD}><RecoveryCodes codes={pending.recovery_codes} onDone={() => finish(pending)} /></div>;

    return (
        <div style={CARD}>
            <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 6 }}>{setup ? t('mfa.setupRequiredTitle') : t('mfa.title')}</div>
            <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 18, lineHeight: 1.6 }}>
                {setup ? t('mfa.setupRequiredHint') : t('mfa.loginHint')}
            </div>
            {setup && enroll && (
                <div style={{ marginBottom: 16 }}>
                    <div style={{ display: 'flex', justifyContent: 'center', marginBottom: 10 }}><QrImage svg={enroll.qr_svg} /></div>
                    <div style={{ fontSize: 11, color: 'var(--text3)' }}>{t('mfa.orEnterKey')}</div>
                    <code style={{ display: 'block', fontSize: 12, wordBreak: 'break-all', marginTop: 4 }}>{enroll.secret}</code>
                </div>
            )}
            <form onSubmit={submit}>
                <Input label={t('mfa.code')} value={code} onChange={setCode} placeholder={setup ? '123456' : t('mfa.codePh')} autoFocus autoComplete="one-time-code" hasError={!!error} />
                <ErrorBox msg={error} />
                <button type="submit" disabled={loading || !code.trim() || (setup && !enroll)} style={submitStyle(loading)}>
                    {loading ? t('mfa.verifying') : t('mfa.verify')}
                </button>
            </form>
            <div style={{ marginTop: 14, textAlign: 'center' }}>
                <button type="button" onClick={onBack} style={LINK}>{t('mfa.back')}</button>
            </div>
        </div>
    );
}

// ── Lupa password ───────────────────────────────────────────────────────────
// Tanpa email: permintaan masuk ke daftar admin di halaman Users. Jawaban server selalu sama, jadi
// form ini tidak bisa dipakai untuk menebak username mana yang terdaftar.
function ForgotForm({ initialUsername, onGoLogin }) {
    const t = useT();
    const [username, setUser] = useState(initialUsername || '');
    const [message,  setMsg]  = useState('');
    const [loading,  setLoad] = useState(false);
    const [error,    setErr]  = useState('');
    const [done,     setDone] = useState(false);

    const submit = async (e) => {
        e.preventDefault();
        if (!username.trim()) { setErr(t('forgot.required')); return; }
        setLoad(true); setErr('');
        try {
            await requestPasswordHelp(username.trim(), message.trim());
            setDone(true);
        } catch (e) {
            setErr(e?.response?.data?.detail || t('forgot.failed'));
        } finally { setLoad(false); }
    };

    if (done) {
        return (
            <div style={{ ...CARD, textAlign: 'center' }}>
                <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 8, color: 'var(--green)' }}>{t('forgot.doneTitle')}</div>
                <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 24, lineHeight: 1.7, padding: '10px 14px', background: 'var(--bg-hover)', borderRadius: 8 }}>
                    {t('forgot.doneHint')}
                </div>
                <button onClick={onGoLogin} style={LINK}>{t('forgot.back')}</button>
            </div>
        );
    }

    return (
        <div style={CARD}>
            <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 4 }}>{t('forgot.title')}</div>
            <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 24, lineHeight: 1.6 }}>{t('forgot.subtitle')}</div>
            <form onSubmit={submit}>
                <Input label="Username" value={username} onChange={setUser} placeholder="username" autoFocus={!initialUsername} autoComplete="username" hasError={!!error} />
                <div style={{ marginBottom: 20 }}>
                    <label htmlFor="forgot-message" style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.08em', display: 'block', marginBottom: 6 }}>{t('forgot.message')}</label>
                    <textarea id="forgot-message" value={message} onChange={e => setMsg(e.target.value)} maxLength={500} rows={3}
                        placeholder={t('forgot.messagePh')} autoFocus={!!initialUsername}
                        style={{ width: '100%', background: 'var(--bg-hover)', border: '1px solid var(--border-light)', borderRadius: 8, padding: '10px 12px', color: 'var(--text)', fontSize: 13, fontFamily: 'var(--font)', outline: 'none', boxSizing: 'border-box', resize: 'vertical' }} />
                </div>
                <ErrorBox msg={error} />
                <button type="submit" disabled={loading} style={{ ...submitStyle(loading), marginBottom: 12 }}>
                    {loading ? t('forgot.submitting') : t('forgot.submit')}
                </button>
            </form>
            <div style={{ textAlign: 'center' }}>
                <button onClick={onGoLogin} style={LINK}>{t('forgot.back')}</button>
            </div>
        </div>
    );
}

// ── Lupa password: tautan email kalau diaktifkan superadmin, kalau tidak lewat permintaan ke admin ──
function EmailResetForm({ initialUsername, onGoLogin, onUseAdmin }) {
    const t = useT();
    const [identifier, setIdentifier] = useState(initialUsername || '');
    const [loading, setLoad] = useState(false);
    const [error, setErr] = useState('');
    const [done, setDone] = useState('');
    const submit = async (e) => {
        e.preventDefault();
        if (!identifier.trim()) { setErr(t('reset.required')); return; }
        setLoad(true); setErr('');
        try { setDone((await requestPasswordReset(identifier.trim())).message); }
        catch (e2) { setErr(e2?.response?.data?.detail || t('forgot.failed')); }
        finally { setLoad(false); }
    };
    if (done) {
        return (
            <div style={{ ...CARD, textAlign: 'center' }}>
                <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 8, color: 'var(--green)' }}>{t('reset.sentTitle')}</div>
                <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 24, lineHeight: 1.7, padding: '10px 14px', background: 'var(--bg-hover)', borderRadius: 8 }}>{done}</div>
                <button onClick={onGoLogin} style={LINK}>{t('forgot.back')}</button>
            </div>
        );
    }
    return (
        <div style={CARD}>
            <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 4 }}>{t('forgot.title')}</div>
            <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 24, lineHeight: 1.6 }}>{t('reset.subtitle')}</div>
            <form onSubmit={submit}>
                <Input label={t('reset.identifier')} value={identifier} onChange={setIdentifier} placeholder={t('reset.identifierPh')}
                    autoFocus autoComplete="username" hasError={!!error} />
                <ErrorBox msg={error} />
                <button type="submit" disabled={loading} style={{ ...submitStyle(loading), marginBottom: 12 }}>
                    {loading ? t('forgot.submitting') : t('reset.send')}
                </button>
            </form>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
                <button onClick={onUseAdmin} style={{ ...LINK, fontWeight: 500, color: 'var(--text3)' }}>{t('reset.useAdmin')}</button>
                <button onClick={onGoLogin} style={LINK}>{t('forgot.back')}</button>
            </div>
        </div>
    );
}

function ForgotChooser({ initialUsername, onGoLogin }) {
    const [state, setState] = useState('loading');          // 'loading' | 'email' | 'admin'
    useEffect(() => {
        fetchSelfResetAvailable().then(r => setState(r.enabled ? 'email' : 'admin')).catch(() => setState('admin'));
    }, []);
    if (state === 'loading') return <div style={CARD} />;
    if (state === 'email') {
        return <EmailResetForm initialUsername={initialUsername} onGoLogin={onGoLogin} onUseAdmin={() => setState('admin')} />;
    }
    return <ForgotForm initialUsername={initialUsername} onGoLogin={onGoLogin} />;
}

// Halaman dari tautan email: ?reset=<token>.
function ResetForm({ token, onGoLogin }) {
    const t = useT();
    const [account, setAccount] = useState(null);           // null = memeriksa, '' = tautan tidak valid
    const [linkError, setLinkError] = useState('');
    const [pw, setPw] = useState('');
    const [confirm, setConfirm] = useState('');
    const [loading, setLoad] = useState(false);
    const [error, setErr] = useState('');
    const [done, setDone] = useState(false);
    useEffect(() => {
        verifyResetToken(token).then(r => setAccount(r.username))
            .catch(e => { setAccount(''); setLinkError(e?.response?.data?.detail || t('reset.invalid')); });
    }, [token]);   // eslint-disable-line react-hooks/exhaustive-deps
    const submit = async (e) => {
        e.preventDefault();
        setErr('');
        if (pw.length < 8) { setErr(t('reset.errShort')); return; }
        if (pw !== confirm) { setErr(t('reset.errMismatch')); return; }
        setLoad(true);
        try { await confirmPasswordReset(token, pw); setDone(true); }
        catch (e2) { setErr(e2?.response?.data?.detail || t('reset.failed')); }
        finally { setLoad(false); }
    };
    if (account === null) return <div style={{ ...CARD, fontSize: 13, color: 'var(--text2)', textAlign: 'center' }}>{t('reset.checking')}</div>;
    if (account === '' || done) {
        return (
            <div style={{ ...CARD, textAlign: 'center' }}>
                <div style={{ fontSize: 18, fontWeight: 700, marginBottom: 10, color: done ? 'var(--green)' : 'var(--red)' }}>
                    {done ? t('reset.doneTitle') : t('reset.invalidTitle')}
                </div>
                <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 24, lineHeight: 1.7 }}>{done ? t('reset.doneHint') : linkError}</div>
                <button onClick={onGoLogin} style={submitStyle(false)}>{t('reset.toLogin')}</button>
            </div>
        );
    }
    return (
        <div style={CARD}>
            <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 4 }}>{t('reset.title')}</div>
            <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 6, lineHeight: 1.6 }}>{t('reset.formHint')}</div>
            <div style={{ fontSize: 13, color: 'var(--text)', marginBottom: 18, fontFamily: 'var(--fmono)' }}>{account}</div>
            <form onSubmit={submit}>
                <input type="text" name="username" autoComplete="username" value={account} readOnly hidden />
                <Input label={t('reset.newPassword')} value={pw} onChange={setPw} type="password" placeholder="••••••••" autoFocus autoComplete="new-password" hasError={!!error} />
                <Input label={t('reset.confirmPassword')} value={confirm} onChange={setConfirm} type="password" placeholder="••••••••" autoComplete="new-password" hasError={!!error} />
                <ErrorBox msg={error} />
                <button type="submit" disabled={loading} style={submitStyle(loading)}>{loading ? t('reset.saving') : t('reset.submit')}</button>
            </form>
        </div>
    );
}

// Token reset dibaca sekali lalu dihapus dari alamat, supaya tidak tersimpan di riwayat atau terbagi lewat tangkapan layar.
function readResetToken() {
    try {
        const q = new URLSearchParams(window.location.search);
        const token = q.get('reset');
        if (token) {
            q.delete('reset');
            const rest = q.toString();
            window.history.replaceState(null, '', window.location.pathname + (rest ? `?${rest}` : ''));
        }
        return token || '';
    } catch { return ''; }
}

// ── Wajib ganti password (setelah direset admin) ────────────────────────────
export function ForcePasswordChange({ user, knownPassword, onDone, onLogout }) {
    const t = useT();
    const [current, setCurrent] = useState(knownPassword || '');
    const [newPw,   setNewPw]   = useState('');
    const [confirm, setConfirm] = useState('');
    const [loading, setLoad]    = useState(false);
    const [error,   setErr]     = useState('');

    const submit = async (e) => {
        e.preventDefault();
        setErr('');
        if (!current || !newPw) { setErr(t('register.errRequired')); return; }
        if (newPw.length < 8) { setErr(t('register.errShort')); return; }
        if (newPw !== confirm) { setErr(t('register.errMismatch')); return; }
        if (newPw === current) { setErr(t('mustChange.errSame')); return; }
        setLoad(true);
        try {
            const r = await changePassword(current, newPw);
            const updated = { ...user, must_change_password: false };
            localStorage.setItem('hv_user', JSON.stringify(updated));
            onDone(updated, r.access_token);
        } catch (e) {
            setErr(e?.response?.data?.detail || t('mustChange.failed'));
        } finally { setLoad(false); }
    };

    return (
        <PageShell>
            <div style={CARD}>
                <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 4 }}>{t('mustChange.title')}</div>
                <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 20, lineHeight: 1.6 }}>{t('mustChange.subtitle')}</div>
                <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 16, fontFamily: 'var(--fmono)' }}>{user?.username}</div>
                <form onSubmit={submit}>
                    {/* Kolom username tersembunyi membantu password manager menyimpan password baru untuk akun yang benar. */}
                    <input type="text" name="username" autoComplete="username" value={user?.username || ''} readOnly hidden />
                    {!knownPassword && (
                        <Input label={t('mustChange.current')} value={current} onChange={setCurrent} type="password" autoFocus autoComplete="current-password" hasError={!!error} />
                    )}
                    <Input label={t('mustChange.new')} value={newPw} onChange={setNewPw} type="password" placeholder={t('register.passwordPh')} autoFocus={!!knownPassword} autoComplete="new-password" hasError={!!error} />
                    <Input label={t('mustChange.confirm')} value={confirm} onChange={setConfirm} type="password" placeholder={t('register.confirmPh')} autoComplete="new-password" hasError={!!error} />
                    <ErrorBox msg={error} />
                    <button type="submit" disabled={loading} style={{ ...submitStyle(loading), marginBottom: 12 }}>
                        {loading ? t('mustChange.saving') : t('mustChange.submit')}
                    </button>
                </form>
                <div style={{ textAlign: 'center' }}>
                    <button onClick={onLogout} style={{ ...LINK, color: 'var(--text3)', fontWeight: 500 }}>{t('mustChange.logout')}</button>
                </div>
            </div>
        </PageShell>
    );
}

// ── Register form ───────────────────────────────────────────────────────────
function RegisterForm({ onGoLogin }) {
    const b = useBranding();
    const t = useT();
    const domains = b.email_domains.join(t('register.or'));
    const [form,    setForm]    = useState({ username: '', full_name: '', email: '', password: '', confirm: '' });
    const [loading, setLoading] = useState(false);
    const [error,   setError]   = useState('');
    const [success, setSuccess] = useState('');

    const set = (k) => (v) => setForm(f => ({ ...f, [k]: v }));

    const submit = async (e) => {
        e.preventDefault();
        setError('');
        if (!form.username.trim() || !form.full_name.trim() || !form.password) {
            setError(t('register.errRequired')); return;
        }
        if (b.email_required && !form.email.trim()) {
            setError(b.email_domains.length ? `${t('register.errEmail')}. ${t('register.useEmail', { domains })}` : t('register.errEmail')); return;
        }
        if (form.password.length < 8) {
            setError(t('register.errShort')); return;
        }
        if (form.password !== form.confirm) {
            setError(t('register.errMismatch')); return;
        }
        setLoading(true);
        try {
            await registerStudent({
                username:  form.username.trim(),
                full_name: form.full_name.trim(),
                email:     form.email.trim() || null,
                password:  form.password,
            });
            setSuccess(t('register.successMsg'));
        } catch (e) {
            setError(e?.response?.data?.detail || t('register.errFailed'));
        } finally { setLoading(false); }
    };

    if (success) {
        return (
            <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border-light)', borderRadius: 16, padding: 32, boxShadow: '0 16px 48px rgba(0,0,0,0.3)', textAlign: 'center' }}>
                <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 8, color: 'var(--green)' }}>{t('register.successTitle')}</div>
                <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 8, lineHeight: 1.7, fontFamily: 'var(--font)' }}>
                    {success}
                </div>
                <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 24, lineHeight: 1.6, padding: '10px 14px', background: 'var(--bg-hover)', borderRadius: 8 }}>
                    {t('register.successHint')}
                </div>
                <button onClick={onGoLogin}
                    style={{ padding: '10px 28px', borderRadius: 8, background: 'var(--cyan)', color: '#000', fontSize: 13, fontWeight: 700, border: 'none', cursor: 'pointer' }}>
                    {t('register.back')}
                </button>
            </div>
        );
    }

    return (
        <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border-light)', borderRadius: 16, padding: 32, boxShadow: '0 16px 48px rgba(0,0,0,0.3)' }}>
            <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 4 }}>{t('register.title')}</div>
            <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 24 }}>{t('register.subtitle')}</div>

            <form onSubmit={submit}>
                <Input label={t('register.fullName')} value={form.full_name} onChange={set('full_name')} placeholder={t('register.fullNamePh')} autoFocus autoComplete="name" hasError={!!error} />
                <Input label="Username" value={form.username} onChange={set('username')} placeholder={t('register.usernamePh')} autoComplete="username" hasError={!!error} />
                <Input label={b.email_required ? t('register.email') : t('register.emailOptional')} value={form.email} onChange={set('email')} type="email"
                    placeholder={b.email_domains.length ? `nama${b.email_domains[0]}` : 'nama@contoh.com'} autoComplete="email" />
                {b.email_domains.length > 0 && (
                    <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: -8, marginBottom: 14 }}>
                        {t('register.useEmail', { domains })}
                    </div>
                )}
                <Input label="Password" value={form.password} onChange={set('password')} type="password" placeholder={t('register.passwordPh')} autoComplete="new-password" hasError={!!error} />
                <Input label={t('register.confirm')} value={form.confirm} onChange={set('confirm')} type="password" placeholder={t('register.confirmPh')} autoComplete="new-password" hasError={!!error} />

                <ErrorBox msg={error} />

                <button type="submit" disabled={loading}
                    style={{ width: '100%', padding: 11, borderRadius: 8, background: loading ? 'var(--bg-hover)' : 'var(--cyan)', color: loading ? 'var(--text3)' : '#000', fontSize: 13, fontWeight: 700, border: 'none', cursor: loading ? 'not-allowed' : 'pointer', transition: 'all 0.15s', marginBottom: 12 }}>
                    {loading ? t('register.submitting') : t('register.submit')}
                </button>
            </form>

            <div style={{ textAlign: 'center', fontSize: 12, color: 'var(--text3)' }}>
                {t('register.haveAccount')}{' '}
                <button onClick={onGoLogin}
                    style={{ background: 'none', border: 'none', color: 'var(--cyan)', cursor: 'pointer', fontSize: 12, fontWeight: 600, padding: 0 }}>
                    {t('register.signIn')}
                </button>
            </div>
        </div>
    );
}

// ── Root export ─────────────────────────────────────────────────────────────
export default function LoginPage({ onLogin, notice = '' }) {
    const [resetToken] = useState(readResetToken);
    const [mode, setMode] = useState(resetToken ? 'reset' : 'login');   // 'login' | 'register' | 'forgot' | 'mfa' | 'reset'
    const [forgotUser, setForgotUser] = useState('');
    const [challenge, setChallenge] = useState(null);

    return (
        <PageShell>
            {mode === 'login' && (
                <LoginForm onLogin={onLogin} onMfa={(c) => { setChallenge(c); setMode('mfa'); }} notice={notice} onGoRegister={() => setMode('register')}
                    onGoForgot={(u) => { setForgotUser(u); setMode('forgot'); }} />
            )}
            {mode === 'mfa' && challenge && <MfaStep challenge={challenge} onLogin={onLogin} onBack={() => { setChallenge(null); setMode('login'); }} />}
            {mode === 'register' && <RegisterForm onGoLogin={() => setMode('login')} />}
            {mode === 'forgot' && <ForgotChooser initialUsername={forgotUser} onGoLogin={() => setMode('login')} />}
            {mode === 'reset' && <ResetForm token={resetToken} onGoLogin={() => setMode('login')} />}
        </PageShell>
    );
}
