import { useState, useEffect, useCallback, useMemo } from 'react';
import { fetchUsers, createUser, updateUser, deleteUserApi, fetchUserAssignments, fetchGroups, bulkUsers, importUsers,
    resetUserPassword, fetchPasswordHelp, dismissPasswordHelp } from '../api';
import { leaseInfo } from '../format';
import { locale, tNodes, useT } from '../i18n';
import { parseCsv, CSV_TEMPLATE, credentialsCsv } from '../csv';
import UserActivityModal from '../components/UserActivityModal';

// Tanggal (YYYY-MM-DD) -> akhir hari itu di zona waktu browser, dan sebaliknya.
const endOfDay = (d) => (d ? new Date(`${d}T23:59:59`).toISOString() : null);
const toDateInput = (iso) => {
    if (!iso) return '';
    const d = new Date(iso);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};

function download(name, text) {
    const url = URL.createObjectURL(new Blob([text], { type: 'text/csv' }));
    const a = Object.assign(document.createElement('a'), { href: url, download: name });
    a.click();
    URL.revokeObjectURL(url);
}

// Label peran diterjemahkan saat ditampilkan: t(`role.${role}`).
const ROLE_CFG = {
    superadmin: { color: '#ff6b35' },
    sysadmin:   { color: 'var(--cyan)' },
    student:    { color: 'var(--purple)' },
};

const inp = {
    width: '100%', background: 'var(--bg-hover)', border: '1px solid var(--border-light)',
    borderRadius: 6, padding: '8px 10px', color: 'var(--text)', fontSize: 13,
    fontFamily: 'var(--fmono)', outline: 'none', boxSizing: 'border-box',
};
const inpDisabled = { ...inp, background: 'var(--bg)', color: 'var(--text3)', opacity: 0.6 };
const lbl = {
    fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase',
    letterSpacing: '0.08em', display: 'block', marginBottom: 4,
};

// ── User Modal (create / edit) ────────────────────────────────────────────────
function UserModal({ user, onClose, onSaved }) {
    const t = useT();
    const isEdit = !!user?.id;
    const [form, setForm] = useState({
        username:  user?.username  || '',
        full_name: user?.full_name || '',
        role:      user?.role      || 'student',
        email:     user?.email     || '',
        password:  '',
        expires:   toDateInput(user?.expires_at),
    });
    const [saving, setSaving] = useState(false);
    const [error, setError]   = useState('');

    const sf = (k, v) => setForm(p => ({ ...p, [k]: v }));

    const save = async () => {
        if (!form.username.trim() || !form.full_name.trim()) {
            setError(t('users.errRequired')); return;
        }
        if (!isEdit && !form.password) { setError(t('users.errPassword')); return; }
        setSaving(true); setError('');
        try {
            if (isEdit) {
                const body = { full_name: form.full_name, role: form.role, email: form.email || null };
                if (form.password) body.password = form.password;
                if (form.expires !== toDateInput(user.expires_at)) body.expires_at = endOfDay(form.expires);
                await updateUser(user.id, body);
            } else {
                const { expires, ...rest } = form;
                await createUser({ ...rest, expires_at: endOfDay(expires) });
            }
            onSaved(); onClose();
        } catch (e) { setError(e?.response?.data?.detail || e.message); }
        finally { setSaving(false); }
    };

    return (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.75)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={onClose}>
            <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border-light)', borderRadius: 14, padding: 28, width: 'min(440px,95vw)', maxHeight: '92vh', overflow: 'auto', boxSizing: 'border-box' }} onClick={e => e.stopPropagation()}>
                <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 20 }}>
                    {isEdit ? t('users.editTitle', { name: user.username }) : t('users.newTitle')}
                </div>

                {[
                    ['username',  t('users.colUsername'), 'text', !isEdit],
                    ['full_name', t('users.fullName'),    'text', true],
                ].map(([k, l, type, enabled]) => (
                    <div key={k} style={{ marginBottom: 12 }}>
                        <label style={lbl}>{l}</label>
                        <input type={type} value={form[k]} disabled={!enabled}
                            onChange={e => sf(k, e.target.value)}
                            style={enabled ? inp : inpDisabled} />
                    </div>
                ))}

                <div style={{ marginBottom: 12 }}>
                    <label style={lbl}>{t('users.emailOptional')}</label>
                    <input type="email" value={form.email}
                        onChange={e => sf('email', e.target.value)}
                        placeholder={t('users.emailPh')}
                        style={inp} />
                </div>

                {[
                    ['password',  isEdit ? t('users.newPassword') : t('users.password'), 'password', true],
                ].map(([k, l, type, enabled]) => (
                    <div key={k} style={{ marginBottom: 12 }}>
                        <label style={lbl}>{l}</label>
                        <input type={type} value={form[k]} disabled={!enabled}
                            onChange={e => sf(k, e.target.value)}
                            style={enabled ? inp : inpDisabled} />
                    </div>
                ))}

                <div style={{ marginBottom: 12 }}>
                    <label style={lbl}>{t('users.expiry')}</label>
                    <input type="date" value={form.expires} onChange={e => sf('expires', e.target.value)} style={{ ...inp, colorScheme: 'dark' }} />
                    <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 4 }}>{t('users.expiryHint')}</div>
                </div>

                <div style={{ marginBottom: 16 }}>
                    <label style={lbl}>{t('users.colRole')}</label>
                    <select value={form.role} onChange={e => sf('role', e.target.value)} style={{ ...inp, cursor: 'pointer' }}>
                        {Object.keys(ROLE_CFG).map(r => (
                            <option key={r} value={r}>{t(`role.${r}`)} ({r})</option>
                        ))}
                    </select>
                </div>

                {error && (
                    <div style={{ padding: '8px 10px', background: 'var(--red-glow)', border: '1px solid var(--red)44', borderRadius: 6, color: 'var(--red)', fontSize: 12, marginBottom: 12 }}>
                        {error}
                    </div>
                )}

                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                    <button onClick={onClose} style={{ padding: '7px 16px', borderRadius: 6, background: 'var(--bg-hover)', border: '1px solid var(--border)', color: 'var(--text2)', fontSize: 12, cursor: 'pointer' }}>{t('common.cancel')}</button>
                    <button onClick={save} disabled={saving} style={{ padding: '7px 16px', borderRadius: 6, background: 'var(--cyan)', color: '#000', fontSize: 12, fontWeight: 600, border: 'none', cursor: saving ? 'wait' : 'pointer', opacity: saving ? 0.7 : 1 }}>
                        {saving ? t('common.saving') : t('common.save')}
                    </button>
                </div>
            </div>
        </div>
    );
}

// ── Impor akun dari CSV ───────────────────────────────────────────────────────
// Berkas diurai di browser, lalu dicek server (dry run) sebelum dibuat. Kalau satu baris saja salah,
// tidak ada akun yang dibuat. Password kosong dibuatkan acak dan ditampilkan sekali untuk diunduh.
function ImportModal({ onClose, onDone }) {
    const t = useT();
    const [rows, setRows] = useState(null);
    const [unknown, setUnknown] = useState([]);
    const [check, setCheck] = useState(null);     // hasil dry run
    const [result, setResult] = useState(null);   // hasil impor
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');

    const onFile = async (file) => {
        setError(''); setCheck(null); setResult(null);
        if (!file) return;
        const parsed = parseCsv(await file.text());
        setRows(parsed.rows); setUnknown(parsed.unknown);
        if (parsed.rows.length === 0) { setError(t('users.importEmpty')); return; }
        setBusy(true);
        try { setCheck(await importUsers(parsed.rows, true)); }
        catch (e) { setError(e?.response?.data?.detail || e.message); }
        finally { setBusy(false); }
    };

    const doImport = async () => {
        setBusy(true); setError('');
        try {
            const r = await importUsers(rows, false);
            if (r.errors) { setCheck(r); return; }
            setResult(r); onDone();
        } catch (e) { setError(e?.response?.data?.detail || e.message); }
        finally { setBusy(false); }
    };

    const th = { padding: '6px 8px', fontSize: 10, color: 'var(--text3)', textAlign: 'left', textTransform: 'uppercase', borderBottom: '1px solid var(--border)' };
    const td = { padding: '6px 8px', fontSize: 12, color: 'var(--text2)', borderBottom: '1px solid var(--border)', verticalAlign: 'top' };
    const btn = { padding: '7px 14px', borderRadius: 6, fontSize: 12, cursor: 'pointer', background: 'var(--bg-hover)', border: '1px solid var(--border)', color: 'var(--text2)' };

    return (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.75)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 12 }} onClick={onClose}>
            <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border-light)', borderRadius: 14, padding: 24, width: 'min(760px,96vw)', maxHeight: '92vh', overflow: 'auto', boxSizing: 'border-box' }} onClick={e => e.stopPropagation()}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                    <div style={{ fontWeight: 600, fontSize: 14 }}>{t('users.importTitle')}</div>
                    <button onClick={onClose} aria-label={t('common.close')} style={{ background: 'none', border: 'none', color: 'var(--text3)', fontSize: 18, cursor: 'pointer' }}>×</button>
                </div>

                {result ? (
                    <div>
                        <div style={{ padding: '10px 12px', borderRadius: 8, background: '#4ade8012', border: '1px solid #4ade8044', color: '#4ade80', fontSize: 13, margin: '10px 0' }}>
                            ✓ {t('users.importCreated', { n: result.created })}
                        </div>
                        {result.credentials?.length > 0 && (
                            <div style={{ fontSize: 12, color: 'var(--text2)', lineHeight: 1.6 }}>
                                {tNodes('users.importPasswords', { n: result.credentials.length, never: <b>{t('users.importNever')}</b> })}
                                <div style={{ marginTop: 10 }}>
                                    <button onClick={() => download('akun-baru.csv', credentialsCsv(result.credentials))} style={{ ...btn, borderColor: 'var(--cyan)', color: 'var(--cyan)' }}>{t('users.importDownload')}</button>
                                </div>
                            </div>
                        )}
                        <div style={{ marginTop: 16, textAlign: 'right' }}><button onClick={onClose} style={btn}>{t('common.close')}</button></div>
                    </div>
                ) : (
                    <>
                        <div style={{ fontSize: 12, color: 'var(--text3)', lineHeight: 1.6, marginBottom: 12 }}>
                            {tNodes('users.importHelp', {
                                cols: <code>username, full_name, email, password, role, expires_at, group</code>,
                                username: <b>username</b>, fullName: <b>full_name</b>, student: <code>student</code>,
                                expires: <code>expires_at</code>, fmt: <code>YYYY-MM-DD</code>, group: <code>group</code>,
                            })}
                            {' '}<button onClick={() => download('template-akun.csv', CSV_TEMPLATE)} style={{ background: 'none', border: 'none', padding: 0, color: 'var(--cyan)', cursor: 'pointer', fontSize: 12, textDecoration: 'underline' }}>{t('users.importTemplate')}</button>
                        </div>
                        <input type="file" accept=".csv,text/csv" onChange={e => onFile(e.target.files?.[0])} style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 12 }} />
                        {unknown.length > 0 && <div style={{ fontSize: 11, color: 'var(--yellow)', marginBottom: 8 }}>{t('users.importIgnored', { cols: unknown.join(', ') })}</div>}
                        {busy && <div style={{ fontSize: 12, color: 'var(--text3)' }}>{t('users.checking')}</div>}
                        {error && <div style={{ fontSize: 12, color: 'var(--red)', marginBottom: 8 }}>⚠ {error}</div>}

                        {check && (
                            <>
                                <div style={{ fontSize: 12, marginBottom: 8, color: check.errors ? 'var(--red)' : 'var(--green)' }}>
                                    {check.errors ? t('users.importErrors', { bad: check.errors, total: check.rows.length })
                                        : t('users.importReady', { n: check.rows.length })}
                                </div>
                                <div style={{ maxHeight: 320, overflow: 'auto', border: '1px solid var(--border)', borderRadius: 8 }}>
                                    <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                                        <thead><tr>{['users.colLine', 'users.colUsername', 'users.colName', 'users.colRole', 'users.colGroup', 'users.colExpires', 'users.colStatus'].map(h => <th key={h} style={th}>{t(h)}</th>)}</tr></thead>
                                        <tbody>
                                            {check.rows.map(r => (
                                                <tr key={r.line}>
                                                    <td style={{ ...td, fontFamily: 'var(--fmono)' }}>{r.line}</td>
                                                    <td style={{ ...td, fontFamily: 'var(--fmono)' }}>{r.username || '—'}</td>
                                                    <td style={td}>{r.full_name || '—'}</td>
                                                    <td style={td}>{r.role}</td>
                                                    <td style={td}>{r.group || '—'}</td>
                                                    <td style={{ ...td, fontFamily: 'var(--fmono)' }}>{r.expires_at ? toDateInput(r.expires_at) : '—'}</td>
                                                    <td style={{ ...td, color: r.errors.length ? 'var(--red)' : 'var(--green)' }}>{r.errors.length ? r.errors.join('; ') : 'OK'}</td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                                <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 14 }}>
                                    <button onClick={onClose} style={btn}>{t('common.cancel')}</button>
                                    <button onClick={doImport} disabled={busy || check.errors > 0}
                                        style={{ ...btn, background: check.errors ? 'var(--bg-hover)' : 'var(--cyan)', color: check.errors ? 'var(--text3)' : '#000', fontWeight: 600, border: 'none', cursor: check.errors ? 'not-allowed' : 'pointer' }}>
                                        {t('users.importDo', { n: check.rows.length })}
                                    </button>
                                </div>
                            </>
                        )}
                    </>
                )}
            </div>
        </div>
    );
}

// ── Confirm Dialog ────────────────────────────────────────────────────────────
function ConfirmDialog({ title, message, confirmLabel, confirmColor = 'var(--red)', onConfirm, onClose }) {
    const t = useT();
    return (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.75)', zIndex: 1001, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <div style={{ background: 'var(--bg-card)', border: `1px solid ${confirmColor}44`, borderRadius: 12, padding: 24, width: 360 }}>
                <div style={{ fontWeight: 600, color: confirmColor, marginBottom: 8 }}>{title}</div>
                <div style={{ fontSize: 13, color: 'var(--text2)', marginBottom: 20 }}>{message}</div>
                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                    <button onClick={onClose} style={{ padding: '6px 14px', borderRadius: 6, background: 'var(--bg-hover)', border: '1px solid var(--border)', color: 'var(--text2)', fontSize: 12, cursor: 'pointer' }}>{t('common.cancel')}</button>
                    <button onClick={onConfirm} style={{ padding: '6px 14px', borderRadius: 6, background: confirmColor, color: '#fff', fontSize: 12, fontWeight: 600, border: 'none', cursor: 'pointer' }}>
                        {confirmLabel}
                    </button>
                </div>
            </div>
        </div>
    );
}

// ── Reset password akun CCD ───────────────────────────────────────────────────
// Password sementara hanya ditampilkan sekali di sini; backend tidak menyimpannya dalam bentuk yang bisa dibaca.
function ResetResultModal({ result, onClose }) {
    const t = useT();
    const [copied, setCopied] = useState(false);
    const copy = async () => {
        try { await navigator.clipboard.writeText(result.password); setCopied(true); } catch { /* clipboard ditolak browser */ }
    };
    return (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.75)', zIndex: 1001, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }}>
            <div role="dialog" aria-label={t('users.tempPassword')} style={{ background: 'var(--bg-card)', border: '1px solid var(--green)66', borderRadius: 12, padding: 24, width: 'min(420px, 100%)', boxSizing: 'border-box' }}>
                <div style={{ fontWeight: 600, color: 'var(--green)', marginBottom: 12 }}>{t('users.tempTitle', { name: result.username })}</div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 12px', borderRadius: 8, background: 'var(--bg-hover)', border: '1px solid var(--border)', marginBottom: 14 }}>
                    <code style={{ flex: 1, fontFamily: 'var(--fmono)', fontSize: 18, letterSpacing: '0.04em', color: 'var(--green)', userSelect: 'all', wordBreak: 'break-all' }}>{result.password}</code>
                    <button onClick={copy} style={{ padding: '5px 12px', borderRadius: 6, fontSize: 12, background: 'transparent', border: '1px solid var(--border)', color: 'var(--text2)', cursor: 'pointer', flexShrink: 0 }}>{copied ? t('common.copied') : t('common.copy')}</button>
                </div>
                <ul style={{ fontSize: 12, color: 'var(--text2)', lineHeight: 1.6, margin: '0 0 18px', paddingLeft: 18 }}>
                    <li>{t('users.tempOnce')}</li>
                    <li>{t('users.tempGive')}</li>
                    <li>{t('users.tempSessions')}</li>
                </ul>
                <div style={{ textAlign: 'right' }}>
                    <button onClick={onClose} style={{ padding: '6px 16px', borderRadius: 6, background: 'var(--cyan)', color: '#000', fontSize: 12, fontWeight: 600, border: 'none', cursor: 'pointer' }}>{t('users.tempNoted')}</button>
                </div>
            </div>
        </div>
    );
}

// Permintaan "Lupa password?" dari halaman login yang belum ditangani.
function PasswordHelpPanel({ requests, onReset, onDismiss }) {
    const t = useT();
    if (!requests.length) return null;
    return (
        <div style={{ background: 'var(--bg-card)', border: '1px solid var(--yellow)55', borderRadius: 10, padding: '12px 14px', marginBottom: 16 }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--yellow)', marginBottom: 4 }}>{t('users.helpTitle', { n: requests.length })}</div>
            <div style={{ fontSize: 11, color: 'var(--text3)', lineHeight: 1.5, marginBottom: 10 }}>
                {t('users.helpWarn')}
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {requests.map(r => (
                    <div key={r.id} style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', padding: '8px 10px', borderRadius: 8, background: 'var(--bg-hover)', border: '1px solid var(--border)' }}>
                        <div style={{ flex: '1 1 220px', minWidth: 0 }}>
                            <div style={{ fontSize: 12 }}>
                                <span style={{ fontFamily: 'var(--fmono)', fontWeight: 600 }}>{r.username}</span>
                                <span style={{ color: 'var(--text3)' }}> · {r.full_name} · {t(`role.${r.role}`)}</span>
                                {!r.is_active && <span style={{ marginLeft: 6, fontSize: 10, color: 'var(--red)' }}>{t('users.inactiveTag')}</span>}
                            </div>
                            {r.message && <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 3, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>“{r.message}”</div>}
                            <div style={{ fontSize: 10, color: 'var(--text3)', marginTop: 3, fontFamily: 'var(--fmono)' }}>
                                {new Date(r.created_at).toLocaleString(locale())}{r.client_ip ? ` · ${r.client_ip}` : ''}
                            </div>
                        </div>
                        <div style={{ display: 'flex', gap: 6 }}>
                            <button onClick={() => onReset(r)} style={{ padding: '5px 12px', borderRadius: 6, fontSize: 12, background: 'transparent', border: '1px solid var(--cyan)', color: 'var(--cyan)', cursor: 'pointer' }}>{t('users.resetPassword')}</button>
                            <button onClick={() => onDismiss(r)} style={{ padding: '5px 12px', borderRadius: 6, fontSize: 12, background: 'transparent', border: '1px solid var(--border)', color: 'var(--text3)', cursor: 'pointer' }}>{t('users.dismiss')}</button>
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
}

// ── Sort helpers ──────────────────────────────────────────────────────────────
function SortTh({ col, label, sort, onSort, style }) {
    const active = sort.col === col;
    return (
        <th onClick={() => onSort(col)} style={{
            padding: '10px 14px', textAlign: 'left', fontSize: 10,
            color: active ? 'var(--cyan)' : 'var(--text3)',
            textTransform: 'uppercase', letterSpacing: '0.08em', fontWeight: 600,
            cursor: 'pointer', userSelect: 'none', whiteSpace: 'nowrap', ...style,
        }}>
            {label} {active ? (sort.dir === 'asc' ? '▲' : '▼') : ''}
        </th>
    );
}

function sortUsers(users, { col, dir }) {
    return [...users].sort((a, b) => {
        let va = a[col] ?? '', vb = b[col] ?? '';
        if (col === 'last_login' || col === 'created_at') {
            va = va ? new Date(va).getTime() : 0;
            vb = vb ? new Date(vb).getTime() : 0;
        } else {
            va = String(va).toLowerCase();
            vb = String(vb).toLowerCase();
        }
        return dir === 'asc' ? (va < vb ? -1 : va > vb ? 1 : 0) : (va > vb ? -1 : va < vb ? 1 : 0);
    });
}

// ── Main Page ────────────────────────────────────────────────────────────────
export default function UsersPage({ currentUser }) {
    const t = useT();
    const [users, setUsers]               = useState([]);
    const [loading, setLoading]           = useState(true);
    const [assignments, setAssignments]   = useState({});
    const [search, setSearch]             = useState('');
    const [roleFilter, setRoleFilter]     = useState('');
    const [sort, setSort]                 = useState({ col: 'role', dir: 'asc' });
    const [editUser, setEditUser]         = useState(null);
    const [activityOf, setActivityOf]     = useState('');
    const [showNew, setShowNew]           = useState(false);
    const [confirmDlg, setConfirmDlg]     = useState(null); // { type, user }
    const [actionErr, setActionErr]       = useState('');
    const [groups, setGroups]             = useState([]);
    const [groupFilter, setGroupFilter]   = useState('');
    const [selected, setSelected]         = useState(() => new Set());
    const [bulkDate, setBulkDate]         = useState('');
    const [bulkBusy, setBulkBusy]         = useState(false);
    const [notice, setNotice]             = useState('');
    const [showImport, setShowImport]     = useState(false);
    const [helpRequests, setHelpRequests] = useState([]);
    const [resetResult, setResetResult]   = useState(null);   // { username, password }

    const isSuperAdmin = currentUser?.role === 'superadmin';
    const isAdmin      = currentUser?.role === 'sysadmin' || isSuperAdmin;

    const loadUsers = useCallback(async () => {
        setLoading(true); setActionErr('');
        try {
            const all = await fetchUsers();
            setUsers(all);
            const students = all.filter(u => u.role === 'student');
            const pairs = await Promise.all(
                students.map(u => fetchUserAssignments(u.id).then(a => [u.id, a]).catch(() => [u.id, []]))
            );
            setAssignments(Object.fromEntries(pairs));
        } catch (e) { setActionErr(e?.response?.data?.detail || e.message); }
        finally { setLoading(false); }
    }, []);

    useEffect(() => { loadUsers(); }, [loadUsers]);

    const loadHelp = useCallback(() => {
        fetchPasswordHelp().then(setHelpRequests).catch(() => {});
    }, []);
    useEffect(() => { if (isAdmin) loadHelp(); }, [isAdmin, loadHelp]);
    const helpChanged = () => {
        loadHelp();
        window.dispatchEvent(new CustomEvent('hv:password-help-changed'));
    };
    useEffect(() => {
        let alive = true;
        fetchGroups().then(g => { if (alive) setGroups(g); }).catch(() => {});
        return () => { alive = false; };
    }, []);

    // Aksi massal: hanya superadmin; akun sendiri tidak ikut (dilewati juga oleh backend).
    const toggleOne = (id) => setSelected(prev => { const n = new Set(prev); if (n.has(id)) n.delete(id); else n.add(id); return n; });
    const runBulk = async (action, extra = {}) => {
        const ids = [...selected];
        const label = t({ activate: 'users.bulkActivate', deactivate: 'users.bulkDeactivate', set_expiry: 'users.bulkSetExpiry', clear_expiry: 'users.bulkClearExpiry' }[action]);
        if (!confirm(t('users.bulkConfirm', { action: label, n: ids.length }) + (action === 'deactivate' ? t('users.bulkConfirmDeactivate') : ''))) return;
        setBulkBusy(true); setActionErr(''); setNotice('');
        try {
            const r = await bulkUsers({ user_ids: ids, action, ...extra });
            setNotice(t('users.bulkDone', { action: label, n: r.updated }));
            setSelected(new Set()); setBulkDate('');
            await loadUsers();
        } catch (e) { setActionErr(e?.response?.data?.detail || e.message); }
        finally { setBulkBusy(false); }
    };

    const doToggleActive = async (u) => {
        try {
            await updateUser(u.id, { is_active: !u.is_active });
            await loadUsers();
        } catch (e) { setActionErr(e?.response?.data?.detail || e.message); }
        setConfirmDlg(null);
    };

    const doVerify = async (u) => {
        try {
            await updateUser(u.id, { is_verified: true });
            await loadUsers();
        } catch (e) { setActionErr(e?.response?.data?.detail || e.message); }
        setConfirmDlg(null);
    };

    const doDelete = async (u) => {
        try {
            await deleteUserApi(u.id);
            await loadUsers();
        } catch (e) { setActionErr(e?.response?.data?.detail || e.message); }
        setConfirmDlg(null);
    };

    const doResetPassword = async (u) => {
        setConfirmDlg(null); setActionErr('');
        try {
            const r = await resetUserPassword(u.id);
            setResetResult(r);
            helpChanged();
            await loadUsers();
        } catch (e) { setActionErr(e?.response?.data?.detail || e.message); }
    };

    const doDismissHelp = async (r) => {
        if (!confirm(t('users.dismissConfirm', { name: r.username }))) return;
        try { await dismissPasswordHelp(r.id); helpChanged(); }
        catch (e) { setActionErr(e?.response?.data?.detail || e.message); }
    };

    // Superadmin bisa mereset semua akun lain; sysadmin hanya akun mahasiswa.
    const canReset = (u) => u.id !== currentUser?.id && (isSuperAdmin || (isAdmin && u.role === 'student'));

    const onSort = (col) => setSort(s => ({ col, dir: s.col === col && s.dir === 'asc' ? 'desc' : 'asc' }));

    const filtered = useMemo(() => {
        let list = users;
        if (roleFilter) list = list.filter(u => u.role === roleFilter);
        if (groupFilter) list = list.filter(u => (u.group_ids || []).includes(Number(groupFilter)));
        if (search.trim()) {
            const q = search.trim().toLowerCase();
            list = list.filter(u =>
                u.username.toLowerCase().includes(q) ||
                (u.full_name || '').toLowerCase().includes(q) ||
                (u.email || '').toLowerCase().includes(q)
            );
        }
        return sortUsers(list, sort);
    }, [users, search, roleFilter, groupFilter, sort]);

    const total = users.length;
    const selectable = filtered.filter(u => u.id !== currentUser?.id);
    const allSelected = selectable.length > 0 && selectable.every(u => selected.has(u.id));
    const toggleAll = () => setSelected(allSelected ? new Set() : new Set(selectable.map(u => u.id)));
    const colCount = 10 + (isSuperAdmin ? 1 : 0);

    return (
        <div style={{ padding: '20px', maxWidth: 1300, margin: '0 auto' }}>

            {/* Header */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20, gap: 10, flexWrap: 'wrap' }}>
                <div>
                    <div style={{ fontSize: 18, fontWeight: 700 }}>{t('users.title')}</div>
                    <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 2 }}>{t('users.subtitle')}</div>
                </div>
                {isSuperAdmin && (
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                        <button onClick={() => setShowImport(true)} style={{ padding: '8px 14px', borderRadius: 8, background: 'transparent', color: 'var(--cyan)', fontSize: 13, border: '1px solid var(--cyan)', cursor: 'pointer' }}>
                            {t('users.importCsv')}
                        </button>
                        <button onClick={() => setShowNew(true)} style={{ padding: '8px 16px', borderRadius: 8, background: 'var(--cyan)', color: '#000', fontSize: 13, fontWeight: 600, border: 'none', cursor: 'pointer' }}>
                            {t('users.add')}
                        </button>
                    </div>
                )}
            </div>

            {/* Stats */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 10, marginBottom: 20 }}>
                <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderLeft: '3px solid var(--text3)', borderRadius: 8, padding: '10px 14px' }}>
                    <div style={{ fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>{t('users.total')}</div>
                    <div style={{ fontFamily: 'var(--fmono)', fontSize: 24, fontWeight: 700, color: 'var(--text)', marginTop: 2 }}>{total}</div>
                </div>
                {Object.entries(ROLE_CFG).map(([role, cfg]) => (
                    <div key={role} onClick={() => setRoleFilter(r => r === role ? '' : role)}
                        style={{ background: 'var(--bg-card)', border: `1px solid ${roleFilter === role ? cfg.color + '66' : 'var(--border)'}`, borderLeft: `3px solid ${cfg.color}`, borderRadius: 8, padding: '10px 14px', cursor: 'pointer', transition: 'border-color 0.15s' }}>
                        <div style={{ fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>{t(`role.${role}`)}</div>
                        <div style={{ fontFamily: 'var(--fmono)', fontSize: 24, fontWeight: 700, color: cfg.color, marginTop: 2 }}>{users.filter(u => u.role === role).length}</div>
                    </div>
                ))}
            </div>

            {/* Search & Filter bar */}
            <div style={{ display: 'flex', gap: 10, marginBottom: 14, alignItems: 'center', flexWrap: 'wrap' }}>
                <input
                    value={search} onChange={e => setSearch(e.target.value)}
                    placeholder={t('users.search')}
                    style={{ flex: 1, minWidth: 200, background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 8, padding: '8px 12px', color: 'var(--text)', fontSize: 13, outline: 'none' }}
                />
                {groups.length > 0 && (
                    <select value={groupFilter} onChange={e => setGroupFilter(e.target.value)}
                        style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 8, padding: '8px 10px', color: 'var(--text)', fontSize: 13 }}>
                        <option value="">{t('users.allGroups')}</option>
                        {groups.map(g => <option key={g.id} value={g.id}>{g.name}</option>)}
                    </select>
                )}
                {roleFilter && (
                    <button onClick={() => setRoleFilter('')} style={{ padding: '7px 12px', borderRadius: 8, background: 'var(--bg-hover)', border: '1px solid var(--border)', color: 'var(--cyan)', fontSize: 12, cursor: 'pointer' }}>
                        ✕ {t(`role.${roleFilter}`)}
                    </button>
                )}
                <div style={{ fontSize: 12, color: 'var(--text3)', flexShrink: 0 }}>
                    {t('users.count', { shown: filtered.length, total })}
                </div>
            </div>

            {isAdmin && (
                <PasswordHelpPanel requests={helpRequests}
                    onReset={(r) => setConfirmDlg({ type: 'reset', user: { id: r.user_id, username: r.username } })}
                    onDismiss={doDismissHelp} />
            )}

            {actionErr && (
                <div style={{ padding: '8px 12px', background: 'var(--red-glow)', border: '1px solid var(--red)44', borderRadius: 6, color: 'var(--red)', fontSize: 12, marginBottom: 12 }}>
                    ⚠ {actionErr}
                </div>
            )}

            {notice && (
                <div style={{ padding: '8px 12px', background: '#4ade8012', border: '1px solid #4ade8044', borderRadius: 6, color: '#4ade80', fontSize: 12, marginBottom: 12 }}>✓ {notice}</div>
            )}

            {isSuperAdmin && selected.size > 0 && (
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', padding: '10px 12px', marginBottom: 12, borderRadius: 8, background: 'var(--bg-card2)', border: '1px solid var(--cyan)44' }}>
                    <span style={{ fontSize: 12, color: 'var(--cyan)', fontWeight: 600, marginRight: 4 }}>{t('users.selected', { n: selected.size })}</span>
                    {[['activate', 'users.bulkActivate', 'var(--green)'], ['deactivate', 'users.bulkDeactivate', 'var(--yellow)']].map(([a, l, c]) => (
                        <button key={a} disabled={bulkBusy} onClick={() => runBulk(a)} style={{ padding: '5px 12px', borderRadius: 6, fontSize: 12, cursor: 'pointer', background: 'transparent', border: `1px solid ${c}`, color: c }}>{t(l)}</button>
                    ))}
                    <input type="date" value={bulkDate} onChange={e => setBulkDate(e.target.value)}
                        style={{ padding: '4px 8px', borderRadius: 6, fontSize: 12, background: 'var(--bg-hover)', border: '1px solid var(--border)', color: 'var(--text)', colorScheme: 'dark' }} />
                    <button disabled={bulkBusy || !bulkDate} onClick={() => runBulk('set_expiry', { expires_at: endOfDay(bulkDate) })}
                        style={{ padding: '5px 12px', borderRadius: 6, fontSize: 12, cursor: bulkDate ? 'pointer' : 'not-allowed', background: 'transparent', border: '1px solid var(--cyan)', color: 'var(--cyan)', opacity: bulkDate ? 1 : 0.5 }}>{t('users.bulkSetExpiry')}</button>
                    <button disabled={bulkBusy} onClick={() => runBulk('clear_expiry')} style={{ padding: '5px 12px', borderRadius: 6, fontSize: 12, cursor: 'pointer', background: 'transparent', border: '1px solid var(--border)', color: 'var(--text2)' }}>{t('lease.none')}</button>
                    <button onClick={() => setSelected(new Set())} style={{ marginLeft: 'auto', padding: '5px 10px', borderRadius: 6, fontSize: 12, cursor: 'pointer', background: 'none', border: 'none', color: 'var(--text3)' }}>{t('users.unselect')}</button>
                </div>
            )}

            {/* Table */}
            <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 12, overflow: 'hidden' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                    <thead>
                        <tr style={{ borderBottom: '1px solid var(--border)', background: 'var(--bg-card2)' }}>
                            {isSuperAdmin && (
                                <th style={{ padding: '10px 6px 10px 14px', width: 20 }}>
                                    <input type="checkbox" checked={allSelected} onChange={toggleAll} title={t('users.selectAll')} aria-label={t('users.selectAll')} />
                                </th>
                            )}
                            <SortTh col="username"   label={t('users.colUsername')} sort={sort} onSort={onSort} />
                            <SortTh col="full_name"  label={t('users.colName')}     sort={sort} onSort={onSort} />
                            <SortTh col="role"       label={t('users.colRole')}     sort={sort} onSort={onSort} />
                            <th style={{ padding: '10px 14px', textAlign: 'left', fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.08em', fontWeight: 600 }}>{t('users.colVms')}</th>
                            <SortTh col="email"      label={t('users.colEmail')}     sort={sort} onSort={onSort} />
                            <SortTh col="created_at" label={t('users.colCreated')}   sort={sort} onSort={onSort} />
                            <SortTh col="last_login" label={t('users.colLastLogin')} sort={sort} onSort={onSort} />
                            <SortTh col="expires_at" label={t('users.colExpires')}   sort={sort} onSort={onSort} />
                            <th style={{ padding: '10px 14px', textAlign: 'left', fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.08em', fontWeight: 600 }}>{t('users.colStatus')}</th>
                            {isAdmin && <th style={{ padding: '10px 14px', textAlign: 'left', fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.08em', fontWeight: 600 }}>{t('users.colActions')}</th>}
                        </tr>
                    </thead>
                    <tbody>
                        {loading ? (
                            <tr><td colSpan={colCount} style={{ textAlign: 'center', padding: 40, color: 'var(--text3)', fontFamily: 'var(--fmono)', fontSize: 12 }}>{t('common.loading')}</td></tr>
                        ) : filtered.length === 0 ? (
                            <tr><td colSpan={colCount} style={{ textAlign: 'center', padding: 40, color: 'var(--text3)', fontSize: 12 }}>{t('users.noMatch')}</td></tr>
                        ) : filtered.map((u, i) => {
                            const cfg = ROLE_CFG[u.role] || { color: 'var(--text3)' };
                            const asgns = assignments[u.id];
                            const exp = leaseInfo(u.expires_at);
                            return (
                                <tr key={u.id} style={{ borderBottom: '1px solid var(--border)', background: selected.has(u.id) ? 'var(--cyan-glow, #00e5ff14)' : i % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.01)', opacity: u.is_active && !exp.expired ? 1 : 0.5 }}>
                                    {isSuperAdmin && (
                                        <td style={{ padding: '10px 6px 10px 14px' }}>
                                            {u.id !== currentUser?.id && <input type="checkbox" checked={selected.has(u.id)} onChange={() => toggleOne(u.id)} />}
                                        </td>
                                    )}

                                    {/* Username */}
                                    <td style={{ padding: '10px 14px', fontFamily: 'var(--fmono)', fontSize: 12, color: u.id === currentUser?.id ? 'var(--cyan)' : 'var(--text)' }}>
                                        {u.username}
                                        {u.id === currentUser?.id && <span style={{ fontSize: 9, marginLeft: 5, color: 'var(--cyan)', background: 'var(--cyan-glow)', padding: '1px 5px', borderRadius: 4 }}>{t('users.you')}</span>}
                                    </td>

                                    {/* Full name */}
                                    <td style={{ padding: '10px 14px', fontSize: 12 }}>{u.full_name}</td>

                                    {/* Role */}
                                    <td style={{ padding: '10px 14px' }}>
                                        <span style={{ padding: '2px 10px', borderRadius: 20, fontSize: 11, fontFamily: 'var(--fmono)', background: cfg.color + '22', color: cfg.color }}>
                                            {t(`role.${u.role}`)}
                                        </span>
                                    </td>

                                    {/* VM Assigned */}
                                    <td style={{ padding: '10px 14px', maxWidth: 200 }}>
                                        {u.role === 'student' ? (
                                            !asgns ? <span style={{ fontSize: 11, color: 'var(--text3)' }}>—</span> :
                                            asgns.length === 0 ? <span style={{ fontSize: 11, color: 'var(--text3)' }}>{t('users.noVm')}</span> : (
                                                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                                                    {asgns.map(a => (
                                                        <span key={a.id} style={{ fontSize: 10, fontFamily: 'var(--fmono)', padding: '1px 7px', borderRadius: 8, background: 'var(--purple-glow,#a78bfa22)', color: 'var(--purple)', border: '1px solid var(--purple)44', whiteSpace: 'nowrap' }}>
                                                            {a.vm_name || '—'}
                                                        </span>
                                                    ))}
                                                </div>
                                            )
                                        ) : (
                                            <span style={{ fontSize: 11, color: 'var(--text3)', fontStyle: 'italic' }}>{t('users.allVms')}</span>
                                        )}
                                    </td>

                                    {/* Email */}
                                    <td style={{ padding: '10px 14px', fontSize: 11, color: 'var(--text3)', fontFamily: 'var(--fmono)' }}>{u.email || '—'}</td>

                                    {/* Created */}
                                    <td style={{ padding: '10px 14px', fontSize: 11, color: 'var(--text3)', whiteSpace: 'nowrap' }}>
                                        {u.created_at ? new Date(u.created_at).toLocaleDateString(locale()) : '—'}
                                    </td>

                                    {/* Last Login */}
                                    <td style={{ padding: '10px 14px', fontSize: 11, color: 'var(--text3)', whiteSpace: 'nowrap' }}>
                                        {u.last_login ? new Date(u.last_login).toLocaleString(locale()) : t('common.never')}
                                    </td>

                                    {/* Masa berlaku */}
                                    <td style={{ padding: '10px 14px', fontSize: 11, whiteSpace: 'nowrap', color: u.expires_at ? exp.color : 'var(--text3)' }} title={exp.text}>
                                        {u.expires_at ? toDateInput(u.expires_at) : '—'}
                                    </td>

                                    {/* Status badges */}
                                    <td style={{ padding: '10px 14px' }}>
                                        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                                            <span style={{ padding: '2px 8px', borderRadius: 20, fontSize: 10, background: u.is_active ? 'var(--green-glow)' : 'var(--red-glow)', color: u.is_active ? 'var(--green)' : 'var(--red)', width: 'fit-content' }}>
                                                {u.is_active ? t('common.active') : t('common.inactive')}
                                            </span>
                                            {exp.expired && (
                                                <span style={{ padding: '2px 8px', borderRadius: 20, fontSize: 10, background: 'var(--red-glow)', color: 'var(--red)', width: 'fit-content' }}>{t('users.expired')}</span>
                                            )}
                                            {u.must_change_password && (
                                                <span title={t('users.mustChangeHint')} style={{ padding: '2px 8px', borderRadius: 20, fontSize: 10, background: 'var(--yellow-glow)', color: 'var(--yellow)', width: 'fit-content', whiteSpace: 'nowrap' }}>{t('users.mustChange')}</span>
                                            )}
                                            {u.role === 'student' && (
                                                <span style={{ padding: '2px 8px', borderRadius: 20, fontSize: 10, background: u.is_verified ? '#4ade8012' : '#f0c04012', color: u.is_verified ? '#4ade80' : '#f0c040', border: `1px solid ${u.is_verified ? '#4ade8033' : '#f0c04033'}`, width: 'fit-content' }}>
                                                    {u.is_verified ? t('users.verified') : t('users.unverified')}
                                                </span>
                                            )}
                                        </div>
                                    </td>

                                    {/* Actions */}
                                    {isAdmin && (
                                        <td style={{ padding: '10px 14px' }}>
                                            <button onClick={() => setActivityOf(u.username)} title={t('uact.title', { user: u.username })}
                                                style={{ padding: '3px 10px', borderRadius: 4, fontSize: 11, background: 'transparent', border: '1px solid var(--border)', color: 'var(--text2)', cursor: 'pointer', marginBottom: 5, whiteSpace: 'nowrap' }}>
                                                📋 {t('uact.button')}
                                            </button>
                                            {canReset(u) && !isSuperAdmin && (
                                                <button onClick={() => setConfirmDlg({ type: 'reset', user: u })} style={{ padding: '3px 10px', borderRadius: 4, fontSize: 11, background: 'transparent', border: '1px solid var(--cyan)66', color: 'var(--cyan)', cursor: 'pointer', whiteSpace: 'nowrap' }}>{t('users.resetBtn')}</button>
                                            )}
                                            {u.id !== currentUser?.id && isSuperAdmin && (
                                                <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
                                                    <button onClick={() => setEditUser(u)} style={{ padding: '3px 10px', borderRadius: 4, fontSize: 11, background: 'transparent', border: '1px solid var(--border)', color: 'var(--text2)', cursor: 'pointer' }}>{t('users.editBtn')}</button>
                                                    <button onClick={() => setConfirmDlg({ type: 'reset', user: u })} title={t('users.resetPassword')} style={{ padding: '3px 10px', borderRadius: 4, fontSize: 11, background: 'transparent', border: '1px solid var(--cyan)66', color: 'var(--cyan)', cursor: 'pointer' }}>{t('users.resetShort')}</button>

                                                    {u.role === 'student' && !u.is_verified && (
                                                        <button onClick={() => setConfirmDlg({ type: 'verify', user: u })} style={{ padding: '3px 10px', borderRadius: 4, fontSize: 11, background: 'transparent', border: '1px solid #4ade8066', color: '#4ade80', cursor: 'pointer' }}>{t('users.verifyBtn')}</button>
                                                    )}

                                                    <button onClick={() => setConfirmDlg({ type: 'toggle', user: u })} style={{ padding: '3px 10px', borderRadius: 4, fontSize: 11, background: 'transparent', border: `1px solid ${u.is_active ? 'var(--yellow)' : 'var(--green)'}`, color: u.is_active ? 'var(--yellow)' : 'var(--green)', cursor: 'pointer' }}>
                                                        {u.is_active ? t('users.bulkDeactivate') : t('users.bulkActivate')}
                                                    </button>

                                                    <button onClick={() => setConfirmDlg({ type: 'delete', user: u })} style={{ padding: '3px 8px', borderRadius: 4, fontSize: 11, background: 'transparent', border: '1px solid var(--red)44', color: 'var(--red)', cursor: 'pointer' }} title={t('common.delete')} aria-label={t('common.delete')}>🗑</button>
                                                </div>
                                            )}
                                        </td>
                                    )}
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>

            {/* Modals */}
            {(showNew || editUser) && (
                <UserModal user={editUser}
                    onClose={() => { setShowNew(false); setEditUser(null); }}
                    onSaved={() => { loadUsers(); setShowNew(false); setEditUser(null); }} />
            )}

            {showImport && <ImportModal onClose={() => setShowImport(false)} onDone={loadUsers} />}

            {confirmDlg?.type === 'delete' && (
                <ConfirmDialog
                    title={t('users.deleteTitle')}
                    message={tNodes('users.deleteMsg', { name: <strong>{confirmDlg.user.username}</strong> })}
                    confirmLabel={t('users.deleteConfirm')}
                    onConfirm={() => doDelete(confirmDlg.user)}
                    onClose={() => setConfirmDlg(null)} />
            )}

            {confirmDlg?.type === 'toggle' && (
                <ConfirmDialog
                    title={confirmDlg.user.is_active ? t('users.deactivateTitle') : t('users.activateTitle')}
                    message={tNodes(confirmDlg.user.is_active ? 'users.deactivateMsg' : 'users.activateMsg', { name: <strong>{confirmDlg.user.username}</strong> })}
                    confirmLabel={confirmDlg.user.is_active ? t('users.bulkDeactivate') : t('users.bulkActivate')}
                    confirmColor={confirmDlg.user.is_active ? 'var(--yellow)' : 'var(--green)'}
                    onConfirm={() => doToggleActive(confirmDlg.user)}
                    onClose={() => setConfirmDlg(null)} />
            )}

            {confirmDlg?.type === 'reset' && (
                <ConfirmDialog
                    title={t('users.resetTitle')}
                    message={tNodes('users.resetMsg', { name: <strong>{confirmDlg.user.username}</strong> })}
                    confirmLabel={t('users.resetPassword')}
                    confirmColor="var(--cyan)"
                    onConfirm={() => doResetPassword(confirmDlg.user)}
                    onClose={() => setConfirmDlg(null)} />
            )}

            {resetResult && <ResetResultModal result={resetResult} onClose={() => setResetResult(null)} />}
            {activityOf && <UserActivityModal username={activityOf} onClose={() => setActivityOf('')} />}

            {confirmDlg?.type === 'verify' && (
                <ConfirmDialog
                    title={t('users.verifyTitle')}
                    message={tNodes('users.verifyMsg', { name: <strong>{confirmDlg.user.username}</strong> })}
                    confirmLabel={t('users.verifyConfirm')}
                    confirmColor="#4ade80"
                    onConfirm={() => doVerify(confirmDlg.user)}
                    onClose={() => setConfirmDlg(null)} />
            )}
        </div>
    );
}
