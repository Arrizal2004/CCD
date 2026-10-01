import { useState } from 'react';
import axios from 'axios';
import { registerStudent } from '../api';

const BASE = import.meta.env.VITE_API_URL || '';

// ── Shared page chrome ──────────────────────────────────────────────────────
function PageShell({ children }) {
    return (
        <div style={{
            minHeight: '100vh', background: 'var(--bg)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontFamily: 'var(--font)', position: 'relative', overflow: 'hidden',
        }}>
            <div style={{ position: 'absolute', inset: 0, opacity: 0.03, backgroundImage: 'linear-gradient(var(--cyan) 1px, transparent 1px), linear-gradient(90deg, var(--cyan) 1px, transparent 1px)', backgroundSize: '40px 40px' }} />
            <div style={{ position: 'absolute', top: '20%', left: '15%', width: 400, height: 400, background: 'var(--cyan)', borderRadius: '50%', filter: 'blur(120px)', opacity: 0.04 }} />
            <div style={{ position: 'absolute', bottom: '20%', right: '15%', width: 300, height: 300, background: 'var(--purple)', borderRadius: '50%', filter: 'blur(100px)', opacity: 0.04 }} />

            <div style={{ width: 'min(440px,95vw)', position: 'relative', zIndex: 1 }}>
                {/* Branding */}
                <div style={{ textAlign: 'center', marginBottom: 32 }}>
                    <div style={{ width: 64, height: 64, borderRadius: 16, margin: '0 auto 16px', background: 'linear-gradient(135deg,var(--cyan),var(--blue))', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 28, fontWeight: 800, color: '#000', boxShadow: '0 8px 32px rgba(0,229,255,0.3)' }}>C</div>
                    <div style={{ fontSize: 22, fontWeight: 700, letterSpacing: '-0.02em' }}>Campus Cloud Dashboard</div>
                    <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 4, fontFamily: 'var(--fmono)' }}>Clientless Campus Cloud</div>
                </div>

                {children}

                <div style={{ textAlign: 'center', marginTop: 20, fontSize: 11, color: 'var(--text3)', lineHeight: 1.6 }}>
                    Campus Cloud Dashboard v1.0 · Tugas Akhir 2026<br />
                    <span style={{ fontFamily: 'var(--fmono)', fontSize: 10 }}>Clientless Campus Cloud · Proxmox VE + Guacamole</span>
                </div>
            </div>
        </div>
    );
}

// ── Input helper ────────────────────────────────────────────────────────────
function Input({ label, value, onChange, type = 'text', placeholder, autoFocus, autoComplete, hasError }) {
    const [focused, setFocused] = useState(false);
    const borderColor = hasError ? 'var(--red)' : focused ? 'var(--cyan)' : 'var(--border-light)';
    return (
        <div style={{ marginBottom: 14 }}>
            <label style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.08em', display: 'block', marginBottom: 6 }}>{label}</label>
            <input value={value} onChange={e => onChange(e.target.value)} type={type}
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
function LoginForm({ onLogin, notice, onGoRegister }) {
    const [username, setUser] = useState('');
    const [password, setPass] = useState('');
    const [loading,  setLoad] = useState(false);
    const [error,    setErr]  = useState('');
    const [showPass, setShow] = useState(false);

    const submit = async (e) => {
        e.preventDefault();
        if (!username || !password) { setErr('Username dan password wajib diisi'); return; }
        setLoad(true); setErr('');
        try {
            const r = await axios.post(`${BASE}/api/v1/users/login`, { username, password });
            localStorage.setItem('hv_token', r.data.access_token);
            localStorage.setItem('hv_user', JSON.stringify(r.data.user));
            const ga = r.data.guac_auth;
            if (ga?.authToken) {
                localStorage.setItem('hv_guac_token', ga.authToken);
                // Format GUAC_AUTH: localStorageService.setItem(key, authResultObj)
                // yang di-JSON.stringify langsung — bukan dibungkus { [dataSource]: ... }
                localStorage.setItem('GUAC_AUTH', JSON.stringify(ga));
            }
            onLogin(r.data.user, r.data.access_token);
        } catch (e) {
            setErr(e?.response?.data?.detail || 'Login gagal — periksa koneksi ke server');
        } finally { setLoad(false); }
    };

    return (
        <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border-light)', borderRadius: 16, padding: 32, boxShadow: '0 16px 48px rgba(0,0,0,0.3)' }}>
            <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 4 }}>Masuk ke Dashboard</div>
            <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 24 }}>Masukkan kredensial yang diberikan administrator</div>

            {notice && (
                <div style={{ padding: '8px 12px', background: 'var(--yellow-glow)', border: '1px solid var(--yellow)44', borderRadius: 6, color: 'var(--yellow)', fontSize: 12, marginBottom: 16, fontFamily: 'var(--fmono)' }}>
                    ⏱ {notice}
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
                        <button type="button" onClick={() => setShow(p => !p)}
                            style={{ position: 'absolute', right: 10, top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', color: 'var(--text3)', cursor: 'pointer', fontSize: 14 }}>
                            {showPass ? '🙈' : '👁'}
                        </button>
                    </div>
                </div>

                <ErrorBox msg={error} />

                <button type="submit" disabled={loading}
                    style={{ width: '100%', padding: 11, borderRadius: 8, background: loading ? 'var(--bg-hover)' : 'var(--cyan)', color: loading ? 'var(--text3)' : '#000', fontSize: 13, fontWeight: 700, border: 'none', cursor: loading ? 'not-allowed' : 'pointer', transition: 'all 0.15s' }}>
                    {loading ? '⏳ Memverifikasi...' : 'Masuk →'}
                </button>
            </form>

            {/* Register link */}
            <div style={{ marginTop: 20, paddingTop: 16, borderTop: '1px solid var(--border)', textAlign: 'center', fontSize: 12, color: 'var(--text3)' }}>
                Mahasiswa baru?{' '}
                <button onClick={onGoRegister}
                    style={{ background: 'none', border: 'none', color: 'var(--cyan)', cursor: 'pointer', fontSize: 12, fontWeight: 600, padding: 0 }}>
                    Daftar Sekarang →
                </button>
            </div>
        </div>
    );
}

// ── Register form ───────────────────────────────────────────────────────────
function RegisterForm({ onGoLogin }) {
    const [form,    setForm]    = useState({ username: '', full_name: '', email: '', password: '', confirm: '' });
    const [loading, setLoading] = useState(false);
    const [error,   setError]   = useState('');
    const [success, setSuccess] = useState('');

    const set = (k) => (v) => setForm(f => ({ ...f, [k]: v }));

    const submit = async (e) => {
        e.preventDefault();
        setError('');
        if (!form.username.trim() || !form.full_name.trim() || !form.password) {
            setError('Username, nama lengkap, dan password wajib diisi'); return;
        }
        if (form.password.length < 6) {
            setError('Password minimal 8 karakter'); return;
        }
        if (form.password !== form.confirm) {
            setError('Konfirmasi password tidak cocok'); return;
        }
        setLoading(true);
        try {
            const r = await registerStudent({
                username:  form.username.trim(),
                full_name: form.full_name.trim(),
                email:     form.email.trim() || null,
                password:  form.password,
            });
            setSuccess(r.message || 'Registrasi berhasil!');
        } catch (e) {
            setError(e?.response?.data?.detail || 'Registrasi gagal — periksa koneksi ke server');
        } finally { setLoading(false); }
    };

    if (success) {
        return (
            <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border-light)', borderRadius: 16, padding: 32, boxShadow: '0 16px 48px rgba(0,0,0,0.3)', textAlign: 'center' }}>
                <div style={{ fontSize: 40, marginBottom: 14 }}>✅</div>
                <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 8, color: 'var(--green)' }}>Registrasi Berhasil!</div>
                <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 8, lineHeight: 1.7, fontFamily: 'var(--font)' }}>
                    {success}
                </div>
                <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 24, lineHeight: 1.6, padding: '10px 14px', background: 'var(--bg-hover)', borderRadius: 8 }}>
                    💡 Akun langsung aktif. Kamu bisa login dan mengajukan Infrastructure Request (VPS/VPN).
                    Setelah request pertama disetujui admin, semua fitur student akan terbuka otomatis.
                </div>
                <button onClick={onGoLogin}
                    style={{ padding: '10px 28px', borderRadius: 8, background: 'var(--cyan)', color: '#000', fontSize: 13, fontWeight: 700, border: 'none', cursor: 'pointer' }}>
                    Kembali ke Login →
                </button>
            </div>
        );
    }

    return (
        <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border-light)', borderRadius: 16, padding: 32, boxShadow: '0 16px 48px rgba(0,0,0,0.3)' }}>
            <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 4 }}>Daftar Akun Mahasiswa</div>
            <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 24 }}>
                Akun akan aktif setelah diverifikasi oleh administrator
            </div>

            <form onSubmit={submit}>
                <Input label="Nama Lengkap" value={form.full_name} onChange={set('full_name')} placeholder="Nama sesuai KTM" autoFocus autoComplete="name" hasError={!!error} />
                <Input label="Username" value={form.username} onChange={set('username')} placeholder="huruf kecil, tanpa spasi" autoComplete="username" hasError={!!error} />
                <Input label="Email (opsional)" value={form.email} onChange={set('email')} type="email" placeholder="nama@contoh.com" autoComplete="email" />
                <Input label="Password" value={form.password} onChange={set('password')} type="password" placeholder="minimal 8 karakter" autoComplete="new-password" hasError={!!error} />
                <Input label="Konfirmasi Password" value={form.confirm} onChange={set('confirm')} type="password" placeholder="ulangi password" autoComplete="new-password" hasError={!!error} />

                <ErrorBox msg={error} />

                <button type="submit" disabled={loading}
                    style={{ width: '100%', padding: 11, borderRadius: 8, background: loading ? 'var(--bg-hover)' : 'var(--cyan)', color: loading ? 'var(--text3)' : '#000', fontSize: 13, fontWeight: 700, border: 'none', cursor: loading ? 'not-allowed' : 'pointer', transition: 'all 0.15s', marginBottom: 12 }}>
                    {loading ? '⏳ Mendaftar...' : 'Daftar Sekarang →'}
                </button>
            </form>

            <div style={{ textAlign: 'center', fontSize: 12, color: 'var(--text3)' }}>
                Sudah punya akun?{' '}
                <button onClick={onGoLogin}
                    style={{ background: 'none', border: 'none', color: 'var(--cyan)', cursor: 'pointer', fontSize: 12, fontWeight: 600, padding: 0 }}>
                    Masuk
                </button>
            </div>
        </div>
    );
}

// ── Root export ─────────────────────────────────────────────────────────────
export default function LoginPage({ onLogin, notice = '' }) {
    const [mode, setMode] = useState('login');   // 'login' | 'register'

    return (
        <PageShell>
            {mode === 'login'
                ? <LoginForm onLogin={onLogin} notice={notice} onGoRegister={() => setMode('register')} />
                : <RegisterForm onGoLogin={() => setMode('login')} />
            }
        </PageShell>
    );
}
