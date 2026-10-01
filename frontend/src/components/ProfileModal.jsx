import { useState } from 'react';
import { createPortal } from 'react-dom';
import { changePassword } from '../api';

const ROLE_COLOR = {
    superadmin: 'var(--red)',
    admin:      'var(--yellow)',
    sysadmin:   'var(--cyan)',
    student:    'var(--green)',
};

function Field({ label, type = 'text', value, onChange, placeholder }) {
    const [show, setShow] = useState(false);
    const isPassword = type === 'password';
    return (
        <div style={{ marginBottom: 14 }}>
            <label style={{ display: 'block', fontSize: 11, color: 'var(--text3)', marginBottom: 5, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                {label}
            </label>
            <div style={{ position: 'relative' }}>
                <input
                    type={isPassword && !show ? 'password' : 'text'}
                    value={value}
                    onChange={e => onChange(e.target.value)}
                    placeholder={placeholder}
                    autoComplete="new-password"
                    style={{
                        width: '100%', boxSizing: 'border-box',
                        background: 'var(--bg-input, var(--bg-card2))',
                        border: '1px solid var(--border)', borderRadius: 6,
                        padding: isPassword ? '8px 36px 8px 10px' : '8px 10px',
                        color: 'var(--text1)', fontSize: 13, outline: 'none',
                    }}
                />
                {isPassword && (
                    <button
                        type="button"
                        onClick={() => setShow(s => !s)}
                        style={{
                            position: 'absolute', right: 8, top: '50%', transform: 'translateY(-50%)',
                            background: 'none', border: 'none', cursor: 'pointer',
                            color: 'var(--text3)', fontSize: 14, padding: 0,
                        }}
                        tabIndex={-1}
                    >
                        {show ? '🙈' : '👁'}
                    </button>
                )}
            </div>
        </div>
    );
}

export default function ProfileModal({ user, onClose }) {
    const [oldPw,  setOldPw]  = useState('');
    const [newPw,  setNewPw]  = useState('');
    const [confPw, setConfPw] = useState('');
    const [status, setStatus] = useState(null);  // { type: 'success'|'error', msg }
    const [loading, setLoading] = useState(false);

    const handleSubmit = async (e) => {
        e.preventDefault();
        setStatus(null);

        if (!oldPw)  return setStatus({ type: 'error', msg: 'Password lama wajib diisi' });
        if (newPw.length < 8) return setStatus({ type: 'error', msg: 'Password baru minimal 8 karakter' });
        if (newPw !== confPw) return setStatus({ type: 'error', msg: 'Konfirmasi password tidak cocok' });
        if (oldPw === newPw)  return setStatus({ type: 'error', msg: 'Password baru tidak boleh sama dengan password lama' });

        setLoading(true);
        try {
            await changePassword(oldPw, newPw);
            setStatus({ type: 'success', msg: 'Password berhasil diubah!' });
            setOldPw(''); setNewPw(''); setConfPw('');
        } catch (err) {
            const msg = err?.response?.data?.detail || err.message || 'Gagal mengganti password';
            setStatus({ type: 'error', msg });
        } finally {
            setLoading(false);
        }
    };

    const handleKey = (e) => { if (e.key === 'Escape') onClose(); };

    return createPortal(
        <div
            onKeyDown={handleKey}
            onClick={onClose}
            style={{
                position: 'fixed', inset: 0, zIndex: 9999,
                background: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(4px)',
                display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24,
            }}
        >
            <div
                onClick={e => e.stopPropagation()}
                style={{
                    background: 'var(--bg-card)', border: '1px solid var(--border)',
                    borderRadius: 12, width: '100%', maxWidth: 420,
                    boxShadow: '0 24px 60px rgba(0,0,0,0.5)',
                    overflow: 'hidden',
                }}
            >
                {/* Header */}
                <div style={{ padding: '18px 20px 14px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text1)' }}>Profil Saya</div>
                    <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text3)', fontSize: 18, lineHeight: 1 }}>✕</button>
                </div>

                {/* User info strip */}
                <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 12, background: 'var(--bg-card2)' }}>
                    <div style={{
                        width: 40, height: 40, borderRadius: '50%', flexShrink: 0,
                        background: (ROLE_COLOR[user?.role] || 'var(--text3)') + '22',
                        border: `1.5px solid ${ROLE_COLOR[user?.role] || 'var(--border)'}`,
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        fontSize: 16, color: ROLE_COLOR[user?.role] || 'var(--text3)',
                        fontWeight: 700,
                    }}>
                        {(user?.full_name || user?.username || '?')[0].toUpperCase()}
                    </div>
                    <div>
                        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text1)' }}>
                            {user?.full_name || user?.username}
                        </div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 3 }}>
                            <span style={{ fontSize: 11, color: 'var(--text3)', fontFamily: 'var(--fmono)' }}>@{user?.username}</span>
                            <span style={{
                                fontSize: 9, padding: '1px 6px', borderRadius: 4,
                                background: (ROLE_COLOR[user?.role] || 'var(--text3)') + '22',
                                color: ROLE_COLOR[user?.role] || 'var(--text3)',
                                textTransform: 'uppercase', letterSpacing: '0.06em',
                            }}>
                                {user?.role}
                            </span>
                        </div>
                    </div>
                </div>

                {/* Change password form */}
                <form onSubmit={handleSubmit} style={{ padding: '18px 20px 20px' }}>
                    <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', marginBottom: 14, textTransform: 'uppercase', letterSpacing: '0.08em' }}>
                        Ganti Password
                    </div>

                    <Field label="Password Lama" type="password" value={oldPw} onChange={setOldPw} placeholder="Masukkan password saat ini" />
                    <Field label="Password Baru" type="password" value={newPw} onChange={setNewPw} placeholder="Minimal 6 karakter" />
                    <Field label="Konfirmasi Password Baru" type="password" value={confPw} onChange={setConfPw} placeholder="Ulangi password baru" />

                    {/* Strength indicator */}
                    {newPw.length > 0 && (
                        <div style={{ marginBottom: 14, marginTop: -6 }}>
                            <div style={{ display: 'flex', gap: 4, marginBottom: 4 }}>
                                {[1,2,3,4].map(i => {
                                    const strength = newPw.length >= 12 ? 4 : newPw.length >= 10 ? 3 : newPw.length >= 8 ? 2 : 1;
                                    const colors = ['var(--red)', 'var(--yellow)', 'var(--cyan)', 'var(--green)'];
                                    return (
                                        <div key={i} style={{
                                            flex: 1, height: 3, borderRadius: 2,
                                            background: i <= strength ? colors[strength - 1] : 'var(--border)',
                                            transition: 'background 0.2s',
                                        }} />
                                    );
                                })}
                            </div>
                            <div style={{ fontSize: 10, color: 'var(--text3)' }}>
                                {newPw.length < 8 ? 'Lemah' : newPw.length < 10 ? 'Cukup' : newPw.length < 12 ? 'Kuat' : 'Sangat Kuat'}
                            </div>
                        </div>
                    )}

                    {status && (
                        <div style={{
                            padding: '8px 12px', borderRadius: 6, marginBottom: 14, fontSize: 12,
                            background: status.type === 'success' ? 'rgba(34,197,94,0.1)' : 'var(--red-glow, rgba(239,68,68,0.1))',
                            border: `1px solid ${status.type === 'success' ? 'var(--green)' : 'var(--red)'}`,
                            color: status.type === 'success' ? 'var(--green)' : 'var(--red)',
                        }}>
                            {status.type === 'success' ? '✓ ' : '✗ '}{status.msg}
                        </div>
                    )}

                    <button
                        type="submit"
                        disabled={loading}
                        style={{
                            width: '100%', padding: '9px 0', borderRadius: 6,
                            background: loading ? 'var(--bg-hover)' : 'var(--cyan)',
                            border: 'none', cursor: loading ? 'not-allowed' : 'pointer',
                            color: loading ? 'var(--text3)' : '#0b0f1a',
                            fontSize: 13, fontWeight: 600,
                        }}
                    >
                        {loading ? 'Menyimpan…' : 'Simpan Password Baru'}
                    </button>
                </form>
            </div>
        </div>,
        document.body
    );
}
