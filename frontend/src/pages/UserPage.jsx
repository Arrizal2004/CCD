import { useState, useEffect, useCallback, useMemo } from 'react';
import { fetchUsers, createUser, updateUser, deleteUserApi, fetchUserAssignments } from '../api';

const ROLE_CFG = {
    superadmin: { color: '#ff6b35', label: 'Super Admin' },
    sysadmin:   { color: 'var(--cyan)', label: 'Sysadmin' },
    student:    { color: 'var(--purple)', label: 'Student' },
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
    const isEdit = !!user?.id;
    const [form, setForm] = useState({
        username:  user?.username  || '',
        full_name: user?.full_name || '',
        role:      user?.role      || 'student',
        email:     user?.email     || '',
        password:  '',
    });
    const [saving, setSaving] = useState(false);
    const [error, setError]   = useState('');

    const sf = (k, v) => setForm(p => ({ ...p, [k]: v }));

    const save = async () => {
        if (!form.username.trim() || !form.full_name.trim()) {
            setError('Username dan nama wajib diisi'); return;
        }
        if (!isEdit && !form.password) { setError('Password wajib diisi untuk user baru'); return; }
        setSaving(true); setError('');
        try {
            if (isEdit) {
                const body = { full_name: form.full_name, role: form.role, email: form.email || null };
                if (form.password) body.password = form.password;
                await updateUser(user.id, body);
            } else {
                await createUser(form);
            }
            onSaved(); onClose();
        } catch (e) { setError(e?.response?.data?.detail || e.message); }
        finally { setSaving(false); }
    };

    return (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.75)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={onClose}>
            <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border-light)', borderRadius: 14, padding: 28, width: 'min(440px,95vw)' }} onClick={e => e.stopPropagation()}>
                <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 20 }}>
                    {isEdit ? `Edit User — ${user.username}` : 'Tambah User Baru'}
                </div>

                {[
                    ['username',  'Username',   'text',     !isEdit],
                    ['full_name', 'Nama Lengkap','text',    true],
                ].map(([k, l, t, enabled]) => (
                    <div key={k} style={{ marginBottom: 12 }}>
                        <label style={lbl}>{l}</label>
                        <input type={t} value={form[k]} disabled={!enabled}
                            onChange={e => sf(k, e.target.value)}
                            style={enabled ? inp : inpDisabled} />
                    </div>
                ))}

                <div style={{ marginBottom: 12 }}>
                    <label style={lbl}>Email (opsional)</label>
                    <input type="email" value={form.email}
                        onChange={e => sf('email', e.target.value)}
                        placeholder="nama@contoh.com"
                        style={inp} />
                </div>

                {[
                    ['password',  isEdit ? 'Password Baru (kosongkan jika tidak diubah)' : 'Password', 'password', true],
                ].map(([k, l, t, enabled]) => (
                    <div key={k} style={{ marginBottom: 12 }}>
                        <label style={lbl}>{l}</label>
                        <input type={t} value={form[k]} disabled={!enabled}
                            onChange={e => sf(k, e.target.value)}
                            style={enabled ? inp : inpDisabled} />
                    </div>
                ))}

                <div style={{ marginBottom: 16 }}>
                    <label style={lbl}>Role</label>
                    <select value={form.role} onChange={e => sf('role', e.target.value)} style={{ ...inp, cursor: 'pointer' }}>
                        {Object.entries(ROLE_CFG).map(([r, c]) => (
                            <option key={r} value={r}>{c.label} ({r})</option>
                        ))}
                    </select>
                </div>

                {error && (
                    <div style={{ padding: '8px 10px', background: 'var(--red-glow)', border: '1px solid var(--red)44', borderRadius: 6, color: 'var(--red)', fontSize: 12, marginBottom: 12 }}>
                        {error}
                    </div>
                )}

                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                    <button onClick={onClose} style={{ padding: '7px 16px', borderRadius: 6, background: 'var(--bg-hover)', border: '1px solid var(--border)', color: 'var(--text2)', fontSize: 12, cursor: 'pointer' }}>Batal</button>
                    <button onClick={save} disabled={saving} style={{ padding: '7px 16px', borderRadius: 6, background: 'var(--cyan)', color: '#000', fontSize: 12, fontWeight: 600, border: 'none', cursor: saving ? 'wait' : 'pointer', opacity: saving ? 0.7 : 1 }}>
                        {saving ? 'Menyimpan...' : 'Simpan'}
                    </button>
                </div>
            </div>
        </div>
    );
}

// ── Confirm Dialog ────────────────────────────────────────────────────────────
function ConfirmDialog({ title, message, confirmLabel, confirmColor = 'var(--red)', onConfirm, onClose }) {
    return (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.75)', zIndex: 1001, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <div style={{ background: 'var(--bg-card)', border: `1px solid ${confirmColor}44`, borderRadius: 12, padding: 24, width: 360 }}>
                <div style={{ fontWeight: 600, color: confirmColor, marginBottom: 8 }}>{title}</div>
                <div style={{ fontSize: 13, color: 'var(--text2)', marginBottom: 20 }}>{message}</div>
                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                    <button onClick={onClose} style={{ padding: '6px 14px', borderRadius: 6, background: 'var(--bg-hover)', border: '1px solid var(--border)', color: 'var(--text2)', fontSize: 12, cursor: 'pointer' }}>Batal</button>
                    <button onClick={onConfirm} style={{ padding: '6px 14px', borderRadius: 6, background: confirmColor, color: '#fff', fontSize: 12, fontWeight: 600, border: 'none', cursor: 'pointer' }}>
                        {confirmLabel}
                    </button>
                </div>
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
    const [users, setUsers]               = useState([]);
    const [loading, setLoading]           = useState(true);
    const [assignments, setAssignments]   = useState({});
    const [search, setSearch]             = useState('');
    const [roleFilter, setRoleFilter]     = useState('');
    const [sort, setSort]                 = useState({ col: 'role', dir: 'asc' });
    const [editUser, setEditUser]         = useState(null);
    const [showNew, setShowNew]           = useState(false);
    const [confirmDlg, setConfirmDlg]     = useState(null); // { type, user }
    const [actionErr, setActionErr]       = useState('');

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

    const onSort = (col) => setSort(s => ({ col, dir: s.col === col && s.dir === 'asc' ? 'desc' : 'asc' }));

    const filtered = useMemo(() => {
        let list = users;
        if (roleFilter) list = list.filter(u => u.role === roleFilter);
        if (search.trim()) {
            const q = search.trim().toLowerCase();
            list = list.filter(u =>
                u.username.toLowerCase().includes(q) ||
                (u.full_name || '').toLowerCase().includes(q) ||
                (u.email || '').toLowerCase().includes(q)
            );
        }
        return sortUsers(list, sort);
    }, [users, search, roleFilter, sort]);

    const total = users.length;

    return (
        <div style={{ padding: '20px', maxWidth: 1300, margin: '0 auto' }}>

            {/* Header */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
                <div>
                    <div style={{ fontSize: 18, fontWeight: 700 }}>User Management</div>
                    <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 2 }}>Kelola akun dan hak akses pengguna dashboard</div>
                </div>
                {isSuperAdmin && (
                    <button onClick={() => setShowNew(true)} style={{ padding: '8px 16px', borderRadius: 8, background: 'var(--cyan)', color: '#000', fontSize: 13, fontWeight: 600, border: 'none', cursor: 'pointer' }}>
                        + Tambah User
                    </button>
                )}
            </div>

            {/* Stats */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 10, marginBottom: 20 }}>
                <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderLeft: '3px solid var(--text3)', borderRadius: 8, padding: '10px 14px' }}>
                    <div style={{ fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>Total</div>
                    <div style={{ fontFamily: 'var(--fmono)', fontSize: 24, fontWeight: 700, color: 'var(--text)', marginTop: 2 }}>{total}</div>
                </div>
                {Object.entries(ROLE_CFG).map(([role, cfg]) => (
                    <div key={role} onClick={() => setRoleFilter(r => r === role ? '' : role)}
                        style={{ background: 'var(--bg-card)', border: `1px solid ${roleFilter === role ? cfg.color + '66' : 'var(--border)'}`, borderLeft: `3px solid ${cfg.color}`, borderRadius: 8, padding: '10px 14px', cursor: 'pointer', transition: 'border-color 0.15s' }}>
                        <div style={{ fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>{cfg.label}</div>
                        <div style={{ fontFamily: 'var(--fmono)', fontSize: 24, fontWeight: 700, color: cfg.color, marginTop: 2 }}>{users.filter(u => u.role === role).length}</div>
                    </div>
                ))}
            </div>

            {/* Search & Filter bar */}
            <div style={{ display: 'flex', gap: 10, marginBottom: 14, alignItems: 'center' }}>
                <input
                    value={search} onChange={e => setSearch(e.target.value)}
                    placeholder="Cari username, nama, atau email..."
                    style={{ flex: 1, background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 8, padding: '8px 12px', color: 'var(--text)', fontSize: 13, outline: 'none' }}
                />
                {roleFilter && (
                    <button onClick={() => setRoleFilter('')} style={{ padding: '7px 12px', borderRadius: 8, background: 'var(--bg-hover)', border: '1px solid var(--border)', color: 'var(--cyan)', fontSize: 12, cursor: 'pointer' }}>
                        ✕ {ROLE_CFG[roleFilter]?.label}
                    </button>
                )}
                <div style={{ fontSize: 12, color: 'var(--text3)', flexShrink: 0 }}>
                    {filtered.length}/{total} user
                </div>
            </div>

            {actionErr && (
                <div style={{ padding: '8px 12px', background: 'var(--red-glow)', border: '1px solid var(--red)44', borderRadius: 6, color: 'var(--red)', fontSize: 12, marginBottom: 12 }}>
                    ⚠ {actionErr}
                </div>
            )}

            {/* Table */}
            <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 12, overflow: 'hidden' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                    <thead>
                        <tr style={{ borderBottom: '1px solid var(--border)', background: 'var(--bg-card2)' }}>
                            <SortTh col="username"   label="Username"    sort={sort} onSort={onSort} />
                            <SortTh col="full_name"  label="Nama"        sort={sort} onSort={onSort} />
                            <SortTh col="role"       label="Role"        sort={sort} onSort={onSort} />
                            <th style={{ padding: '10px 14px', textAlign: 'left', fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.08em', fontWeight: 600 }}>VM Assigned</th>
                            <SortTh col="email"      label="Email"       sort={sort} onSort={onSort} />
                            <SortTh col="created_at" label="Dibuat"      sort={sort} onSort={onSort} />
                            <SortTh col="last_login" label="Last Login"  sort={sort} onSort={onSort} />
                            <th style={{ padding: '10px 14px', textAlign: 'left', fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.08em', fontWeight: 600 }}>Status</th>
                            {isAdmin && <th style={{ padding: '10px 14px', textAlign: 'left', fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.08em', fontWeight: 600 }}>Aksi</th>}
                        </tr>
                    </thead>
                    <tbody>
                        {loading ? (
                            <tr><td colSpan={9} style={{ textAlign: 'center', padding: 40, color: 'var(--text3)', fontFamily: 'var(--fmono)', fontSize: 12 }}>Memuat...</td></tr>
                        ) : filtered.length === 0 ? (
                            <tr><td colSpan={9} style={{ textAlign: 'center', padding: 40, color: 'var(--text3)', fontSize: 12 }}>Tidak ada user yang cocok</td></tr>
                        ) : filtered.map((u, i) => {
                            const cfg = ROLE_CFG[u.role] || { color: 'var(--text3)', label: u.role };
                            const asgns = assignments[u.id];
                            return (
                                <tr key={u.id} style={{ borderBottom: '1px solid var(--border)', background: i % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.01)', opacity: u.is_active ? 1 : 0.5 }}>

                                    {/* Username */}
                                    <td style={{ padding: '10px 14px', fontFamily: 'var(--fmono)', fontSize: 12, color: u.id === currentUser?.id ? 'var(--cyan)' : 'var(--text)' }}>
                                        {u.username}
                                        {u.id === currentUser?.id && <span style={{ fontSize: 9, marginLeft: 5, color: 'var(--cyan)', background: 'var(--cyan-glow)', padding: '1px 5px', borderRadius: 4 }}>Anda</span>}
                                    </td>

                                    {/* Full name */}
                                    <td style={{ padding: '10px 14px', fontSize: 12 }}>{u.full_name}</td>

                                    {/* Role */}
                                    <td style={{ padding: '10px 14px' }}>
                                        <span style={{ padding: '2px 10px', borderRadius: 20, fontSize: 11, fontFamily: 'var(--fmono)', background: cfg.color + '22', color: cfg.color }}>
                                            {cfg.label}
                                        </span>
                                    </td>

                                    {/* VM Assigned */}
                                    <td style={{ padding: '10px 14px', maxWidth: 200 }}>
                                        {u.role === 'student' ? (
                                            !asgns ? <span style={{ fontSize: 11, color: 'var(--text3)' }}>—</span> :
                                            asgns.length === 0 ? <span style={{ fontSize: 11, color: 'var(--text3)' }}>Belum ada</span> : (
                                                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                                                    {asgns.map(a => (
                                                        <span key={a.id} style={{ fontSize: 10, fontFamily: 'var(--fmono)', padding: '1px 7px', borderRadius: 8, background: 'var(--purple-glow,#a78bfa22)', color: 'var(--purple)', border: '1px solid var(--purple)44', whiteSpace: 'nowrap' }}>
                                                            {a.vm_name || '—'}
                                                        </span>
                                                    ))}
                                                </div>
                                            )
                                        ) : (
                                            <span style={{ fontSize: 11, color: 'var(--text3)', fontStyle: 'italic' }}>Semua VM</span>
                                        )}
                                    </td>

                                    {/* Email */}
                                    <td style={{ padding: '10px 14px', fontSize: 11, color: 'var(--text3)', fontFamily: 'var(--fmono)' }}>{u.email || '—'}</td>

                                    {/* Created */}
                                    <td style={{ padding: '10px 14px', fontSize: 11, color: 'var(--text3)', whiteSpace: 'nowrap' }}>
                                        {u.created_at ? new Date(u.created_at).toLocaleDateString('id-ID') : '—'}
                                    </td>

                                    {/* Last Login */}
                                    <td style={{ padding: '10px 14px', fontSize: 11, color: 'var(--text3)', whiteSpace: 'nowrap' }}>
                                        {u.last_login ? new Date(u.last_login).toLocaleString('id-ID') : 'Belum pernah'}
                                    </td>

                                    {/* Status badges */}
                                    <td style={{ padding: '10px 14px' }}>
                                        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                                            <span style={{ padding: '2px 8px', borderRadius: 20, fontSize: 10, background: u.is_active ? 'var(--green-glow)' : 'var(--red-glow)', color: u.is_active ? 'var(--green)' : 'var(--red)', width: 'fit-content' }}>
                                                {u.is_active ? 'Aktif' : 'Nonaktif'}
                                            </span>
                                            {u.role === 'student' && (
                                                <span style={{ padding: '2px 8px', borderRadius: 20, fontSize: 10, background: u.is_verified ? '#4ade8012' : '#f0c04012', color: u.is_verified ? '#4ade80' : '#f0c040', border: `1px solid ${u.is_verified ? '#4ade8033' : '#f0c04033'}`, width: 'fit-content' }}>
                                                    {u.is_verified ? 'Verified' : 'Unverified'}
                                                </span>
                                            )}
                                        </div>
                                    </td>

                                    {/* Actions */}
                                    {isAdmin && (
                                        <td style={{ padding: '10px 14px' }}>
                                            {u.id !== currentUser?.id && isSuperAdmin && (
                                                <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
                                                    <button onClick={() => setEditUser(u)} style={{ padding: '3px 10px', borderRadius: 4, fontSize: 11, background: 'transparent', border: '1px solid var(--border)', color: 'var(--text2)', cursor: 'pointer' }}>✏ Edit</button>

                                                    {u.role === 'student' && !u.is_verified && (
                                                        <button onClick={() => setConfirmDlg({ type: 'verify', user: u })} style={{ padding: '3px 10px', borderRadius: 4, fontSize: 11, background: 'transparent', border: '1px solid #4ade8066', color: '#4ade80', cursor: 'pointer' }}>✓ Verify</button>
                                                    )}

                                                    <button onClick={() => setConfirmDlg({ type: 'toggle', user: u })} style={{ padding: '3px 10px', borderRadius: 4, fontSize: 11, background: 'transparent', border: `1px solid ${u.is_active ? 'var(--yellow)' : 'var(--green)'}`, color: u.is_active ? 'var(--yellow)' : 'var(--green)', cursor: 'pointer' }}>
                                                        {u.is_active ? 'Nonaktifkan' : 'Aktifkan'}
                                                    </button>

                                                    <button onClick={() => setConfirmDlg({ type: 'delete', user: u })} style={{ padding: '3px 8px', borderRadius: 4, fontSize: 11, background: 'transparent', border: '1px solid var(--red)44', color: 'var(--red)', cursor: 'pointer' }}>🗑</button>
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

            {confirmDlg?.type === 'delete' && (
                <ConfirmDialog
                    title="⚠ Hapus User"
                    message={<>Hapus user <strong>{confirmDlg.user.username}</strong>? Tindakan ini tidak bisa dibatalkan.</>}
                    confirmLabel="Ya, Hapus"
                    onConfirm={() => doDelete(confirmDlg.user)}
                    onClose={() => setConfirmDlg(null)} />
            )}

            {confirmDlg?.type === 'toggle' && (
                <ConfirmDialog
                    title={confirmDlg.user.is_active ? 'Nonaktifkan User?' : 'Aktifkan User?'}
                    message={<>{confirmDlg.user.is_active ? 'Nonaktifkan' : 'Aktifkan'} user <strong>{confirmDlg.user.username}</strong>?</>}
                    confirmLabel={confirmDlg.user.is_active ? 'Nonaktifkan' : 'Aktifkan'}
                    confirmColor={confirmDlg.user.is_active ? 'var(--yellow)' : 'var(--green)'}
                    onConfirm={() => doToggleActive(confirmDlg.user)}
                    onClose={() => setConfirmDlg(null)} />
            )}

            {confirmDlg?.type === 'verify' && (
                <ConfirmDialog
                    title="Verifikasi Student?"
                    message={<>Verifikasi student <strong>{confirmDlg.user.username}</strong>? Student akan mendapat akses penuh.</>}
                    confirmLabel="Verifikasi"
                    confirmColor="#4ade80"
                    onConfirm={() => doVerify(confirmDlg.user)}
                    onClose={() => setConfirmDlg(null)} />
            )}
        </div>
    );
}
