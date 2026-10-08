import { useState, useEffect, useCallback } from 'react';
import { fetchVmOsAccounts, upsertVmOsAccount, deleteVmOsAccount, resetVmPassword } from '../api';
import { t as translate, tNodes, useT } from '../i18n';

// Akun OS untuk VM yang dipakai banyak orang. Di grup dengan Login Mandiri, tiap mahasiswa mengetik
// username dan password akunnya sendiri saat Connect; akun juga bisa dipasangkan ke mahasiswa di tab
// Assignments supaya Connect langsung masuk. User di dalam VM dibuat, direset, dan dihapus lewat
// QEMU Guest Agent, jadi tidak perlu SSH atau password lama.

const detail = (e) => e?.response?.data?.detail || e?.message || translate('common.failed');

const small = { padding: '3px 10px', fontSize: 11, borderRadius: 5, cursor: 'pointer', background: 'transparent', border: '1px solid var(--border)', color: 'var(--text2)' };
const input = { padding: '5px 8px', borderRadius: 5, border: '1px solid var(--border)', background: 'var(--bg)', color: 'var(--text)', fontSize: 12, minWidth: 0 };
const box = { background: 'var(--bg-card2)', border: '1px solid var(--border)', borderRadius: 6, padding: '8px 10px', margin: '6px 0 4px' };

export function NewPasswordNotice({ username, password, updated, onClose }) {
    const t = useT();
    const [copied, setCopied] = useState(false);
    const copy = async () => {
        try { await navigator.clipboard.writeText(password); setCopied(true); } catch { /* clipboard ditolak browser */ }
    };
    return (
        <div role="status" style={{ background: 'var(--green-glow)', border: '1px solid var(--green)', borderRadius: 6, padding: '8px 10px', fontSize: 11, color: 'var(--text)', margin: '6px 0 10px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <span>{tNodes('osacc.newPassword', { name: <b>{username}</b> })}</span>
                <code style={{ fontFamily: 'var(--fmono)', fontSize: 13, color: 'var(--green)', userSelect: 'all' }}>{password}</code>
                <button onClick={copy} style={small}>{copied ? t('common.copied') : t('common.copy')}</button>
                <button onClick={onClose} aria-label={t('common.close')} style={{ ...small, border: 'none', marginLeft: 'auto', fontSize: 14 }}>×</button>
            </div>
            <div style={{ color: 'var(--text3)', marginTop: 4, lineHeight: 1.5 }}>
                {t('osacc.giveIt')}
                {updated?.length > 0 && t('osacc.updated', { list: updated.join(', ') })}
            </div>
        </div>
    );
}

// Ganti password user di dalam VM. username kosong = user Login Connect VM ini.
export function ResetPasswordForm({ hostName, vmid, username, onDone, onCancel }) {
    const t = useT();
    const [password, setPassword] = useState('');
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState('');
    const submit = async () => {
        setBusy(true); setErr('');
        try {
            onDone(await resetVmPassword(hostName, String(vmid), { username: username || undefined, password: password || undefined }));
        } catch (e) {
            setErr(detail(e));
        } finally {
            setBusy(false);
        }
    };
    return (
        <div style={box}>
            <div style={{ fontSize: 11, color: 'var(--text2)', lineHeight: 1.5, marginBottom: 6 }}>
                {tNodes('osacc.resetIntro', { name: <b>{username || t('osacc.connectUser')}</b> })}
            </div>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                <input value={password} onChange={e => setPassword(e.target.value)} placeholder={t('osacc.newPasswordPh')}
                    autoComplete="off" spellCheck={false} style={{ ...input, flex: '1 1 180px' }} />
                <button onClick={submit} disabled={busy} style={{ ...small, borderColor: 'var(--yellow)', color: 'var(--yellow)', cursor: busy ? 'wait' : 'pointer' }}>
                    {busy ? t('osacc.resetting') : t('osacc.resetPassword')}
                </button>
                <button onClick={onCancel} disabled={busy} style={small}>{t('common.cancel')}</button>
            </div>
            {err && <div style={{ fontSize: 11, color: 'var(--red)', marginTop: 6 }}>{err}</div>}
        </div>
    );
}

const EMPTY = { os_username: '', password: '', create_in_vm: true };

export default function VmOsAccountsPanel({ hostName, vmid }) {
    const t = useT();
    const vmId = String(vmid);
    const [accounts, setAccounts] = useState(null);
    const [form, setForm] = useState(EMPTY);
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState('');
    const [done, setDone] = useState('');
    const [notice, setNotice] = useState(null);     // {username, password, updated}
    const [action, setAction] = useState(null);     // {id, kind: 'reset' | 'delete', removeInVm}

    const load = useCallback(() => fetchVmOsAccounts(hostName, vmId)
        .then(setAccounts)
        .catch(e => { setAccounts([]); setErr(detail(e)); }), [hostName, vmId]);

    useEffect(() => { load(); }, [load]);

    const reset = () => { setErr(''); setDone(''); setNotice(null); };

    const add = async () => {
        reset();
        setBusy(true);
        try {
            const r = await upsertVmOsAccount(hostName, vmId, {
                os_username: form.os_username.trim(), password: form.password || null, create_in_vm: form.create_in_vm,
            });
            if (r.password) setNotice({ username: r.os_username, password: r.password });
            else setDone(t(r.created_in_vm ? 'osacc.createdInVm' : 'osacc.recorded', { name: r.os_username }));
            setForm(EMPTY);
            load();
        } catch (e) {
            setErr(detail(e));
        } finally {
            setBusy(false);
        }
    };

    const remove = async (acc) => {
        reset();
        setBusy(true);
        try {
            const r = await deleteVmOsAccount(hostName, vmId, acc.id, action.removeInVm);
            setDone(t(r.removed_in_vm ? 'osacc.removedBoth' : 'osacc.removedDashboard', { name: acc.os_username }));
            setAction(null);
            load();
        } catch (e) {
            setErr(detail(e));
        } finally {
            setBusy(false);
        }
    };

    return (
        <div>
            <div style={{ fontSize: 11, color: 'var(--text3)', lineHeight: 1.6, marginBottom: 10 }}>
                {tNodes('osacc.intro', { mode: <b>{t('osacc.mandiri')}</b> })}
            </div>

            {notice && <NewPasswordNotice {...notice} onClose={() => setNotice(null)} />}
            {done && <div role="status" style={{ fontSize: 11, color: 'var(--green)', margin: '0 0 8px' }}>✓ {done}</div>}
            {err && <div role="alert" style={{ fontSize: 11, color: 'var(--red)', margin: '0 0 8px' }}>⚠ {err}</div>}

            <div style={{ border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden', marginBottom: 12 }}>
                {accounts === null && <div style={{ padding: 14, fontSize: 12, color: 'var(--text3)' }}>{t('common.loading')}</div>}
                {accounts?.length === 0 && <div style={{ padding: 14, fontSize: 12, color: 'var(--text3)' }}>{t('osacc.none')}</div>}
                {accounts?.map(a => (
                    <div key={a.id} style={{ padding: '8px 12px', borderBottom: '1px solid var(--border)' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                            <span style={{ fontFamily: 'var(--fmono)', fontSize: 12, color: 'var(--text)' }}>{a.os_username}</span>
                            <span style={{ fontSize: 10, color: 'var(--text3)' }}>{a.has_password ? t('osacc.hasPassword') : a.has_pkey ? t('osacc.privateKey') : '—'}</span>
                            <span style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
                                <button onClick={() => { reset(); setAction({ id: a.id, kind: 'reset' }); }} style={small}>{t('osacc.resetPassword')}</button>
                                <button onClick={() => { reset(); setAction({ id: a.id, kind: 'delete', removeInVm: false }); }}
                                    style={{ ...small, borderColor: 'var(--red)', color: 'var(--red)' }}>{t('common.delete')}</button>
                            </span>
                        </div>
                        {action?.id === a.id && action.kind === 'reset' && (
                            <ResetPasswordForm hostName={hostName} vmid={vmId} username={a.os_username}
                                onDone={r => { setAction(null); setNotice(r); load(); }} onCancel={() => setAction(null)} />
                        )}
                        {action?.id === a.id && action.kind === 'delete' && (
                            <div style={box}>
                                <label style={{ display: 'flex', gap: 6, alignItems: 'flex-start', fontSize: 11, color: 'var(--text2)', lineHeight: 1.5, marginBottom: 6 }}>
                                    <input type="checkbox" checked={action.removeInVm} onChange={e => setAction({ ...action, removeInVm: e.target.checked })} />
                                    {t('osacc.alsoRemove')}
                                </label>
                                <div style={{ display: 'flex', gap: 6 }}>
                                    <button onClick={() => remove(a)} disabled={busy} style={{ ...small, borderColor: 'var(--red)', color: 'var(--red)', cursor: busy ? 'wait' : 'pointer' }}>
                                        {busy ? t('common.deleting') : t('osacc.deleteAccount')}
                                    </button>
                                    <button onClick={() => setAction(null)} disabled={busy} style={small}>{t('common.cancel')}</button>
                                </div>
                            </div>
                        )}
                    </div>
                ))}
            </div>

            <div style={{ fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6 }}>{t('osacc.add')}</div>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 6 }}>
                <input value={form.os_username} onChange={e => setForm({ ...form, os_username: e.target.value })} placeholder={t('osacc.usernamePh')}
                    autoComplete="off" spellCheck={false} aria-label={t('osacc.username')} style={{ ...input, flex: '1 1 180px', fontFamily: 'var(--fmono)' }} />
                <input value={form.password} onChange={e => setForm({ ...form, password: e.target.value })}
                    placeholder={form.create_in_vm ? t('osacc.passwordRandomPh') : t('osacc.passwordPh')}
                    type={form.create_in_vm ? 'text' : 'password'} autoComplete="new-password" spellCheck={false} aria-label={t('osacc.password')}
                    style={{ ...input, flex: '1 1 200px' }} />
                <button onClick={add} disabled={busy || !form.os_username.trim()}
                    style={{ ...small, background: 'var(--cyan)', color: '#000', border: 'none', fontWeight: 600, opacity: busy || !form.os_username.trim() ? 0.6 : 1 }}>
                    {busy ? '…' : t('common.add')}
                </button>
            </div>
            <label style={{ display: 'flex', gap: 6, alignItems: 'flex-start', fontSize: 11, color: 'var(--text2)', lineHeight: 1.5 }}>
                <input type="checkbox" checked={form.create_in_vm} onChange={e => setForm({ ...form, create_in_vm: e.target.checked })} />
                <span>
                    {t('osacc.createInVm')}
                    <span style={{ display: 'block', color: 'var(--text3)', fontSize: 10 }}>
                        {form.create_in_vm ? t('osacc.createInVmOn') : t('osacc.createInVmOff')}
                    </span>
                </span>
            </label>
        </div>
    );
}
