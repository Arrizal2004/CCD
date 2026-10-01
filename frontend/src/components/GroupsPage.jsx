import { useState, useEffect, useCallback } from 'react';
import {
    fetchGroups, createGroup, updateGroup, deleteGroup,
    fetchGroupMembers, addGroupMember, removeGroupMember,
    fetchGroupVms, addGroupVm, updateGroupVm, removeGroupVm,
    fetchUsers, fetchAllProxmoxVmsFlat,
} from '../api';

// ── Helpers ───────────────────────────────────────────────────────────────────

const inputSt = {
    width: '100%', background: 'var(--bg)', border: '1px solid var(--border)',
    borderRadius: 6, padding: '7px 10px', color: 'var(--text)', fontSize: 12,
    boxSizing: 'border-box',
};
const btnSt = (color = 'var(--cyan)') => ({
    padding: '5px 12px', borderRadius: 6, fontSize: 11, fontWeight: 600,
    cursor: 'pointer', border: `1px solid ${color}44`,
    background: `${color}11`, color,
});
const dangerSt = { ...btnSt('var(--red)'), padding: '3px 8px', fontSize: 10 };

// ── Group Form (create / edit) ────────────────────────────────────────────────

function GroupForm({ initial, onSave, onCancel }) {
    const [name, setName] = useState(initial?.name || '');
    const [desc, setDesc] = useState(initial?.description || '');
    const [err, setErr] = useState('');
    const [saving, setSaving] = useState(false);

    const submit = async () => {
        if (!name.trim()) { setErr('Nama grup wajib diisi'); return; }
        setSaving(true); setErr('');
        try {
            await onSave({ name: name.trim(), description: desc.trim() });
        } catch (e) {
            setErr(e?.response?.data?.detail || e.message);
        } finally { setSaving(false); }
    };

    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div>
                <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 3 }}>Nama Grup *</div>
                <input style={inputSt} value={name} onChange={e => setName(e.target.value)}
                    placeholder="Contoh: Kelas XII-A" maxLength={80} />
            </div>
            <div>
                <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 3 }}>Deskripsi</div>
                <input style={inputSt} value={desc} onChange={e => setDesc(e.target.value)}
                    placeholder="Opsional" maxLength={200} />
            </div>
            {err && <div style={{ fontSize: 11, color: 'var(--red)' }}>{err}</div>}
            <div style={{ display: 'flex', gap: 8 }}>
                <button onClick={submit} disabled={saving} style={btnSt()}>
                    {saving ? 'Menyimpan...' : initial ? 'Simpan' : 'Buat Grup'}
                </button>
                <button onClick={onCancel} style={btnSt('var(--text3)')}>Batal</button>
            </div>
        </div>
    );
}

// ── Members Panel ─────────────────────────────────────────────────────────────

function MembersPanel({ group, allStudents }) {
    const [members, setMembers] = useState([]);
    const [loading, setLoading] = useState(true);
    const [addingId, setAddingId] = useState('');
    const [err, setErr] = useState('');

    const load = useCallback(async () => {
        setLoading(true);
        try { setMembers(await fetchGroupMembers(group.id)); }
        finally { setLoading(false); }
    }, [group.id]);

    useEffect(() => { load(); }, [load]);

    const memberIds = new Set(members.map(m => m.id));
    const available = allStudents.filter(s => !memberIds.has(s.id));

    const add = async () => {
        if (!addingId) return;
        setErr('');
        try {
            await addGroupMember(group.id, parseInt(addingId));
            setAddingId('');
            load();
        } catch (e) { setErr(e?.response?.data?.detail || e.message); }
    };

    const remove = async (userId) => {
        setErr('');
        try { await removeGroupMember(group.id, userId); load(); }
        catch (e) { setErr(e?.response?.data?.detail || e.message); }
    };

    return (
        <div>
            <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 10 }}>
                Anggota — {members.length} siswa
            </div>

            {/* Add member */}
            <div style={{ display: 'flex', gap: 6, marginBottom: 10 }}>
                <select value={addingId} onChange={e => setAddingId(e.target.value)}
                    style={{ ...inputSt, flex: 1 }}>
                    <option value="">— Pilih siswa —</option>
                    {available.map(s => (
                        <option key={s.id} value={s.id}>{s.full_name} (@{s.username})</option>
                    ))}
                </select>
                <button onClick={add} disabled={!addingId} style={btnSt()}>Tambah</button>
            </div>

            {err && <div style={{ fontSize: 11, color: 'var(--red)', marginBottom: 6 }}>{err}</div>}

            {loading ? (
                <div style={{ fontSize: 12, color: 'var(--text3)', padding: '12px 0' }}>Loading...</div>
            ) : members.length === 0 ? (
                <div style={{ fontSize: 12, color: 'var(--text3)', padding: '12px 0' }}>Belum ada anggota.</div>
            ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
                    {members.map(m => (
                        <div key={m.id} style={{
                            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                            padding: '7px 10px', background: 'var(--bg-card2)', borderRadius: 6,
                        }}>
                            <div>
                                <div style={{ fontSize: 12, fontWeight: 600 }}>{m.full_name}</div>
                                <div style={{ fontSize: 10, color: 'var(--text3)', fontFamily: 'var(--fmono)' }}>@{m.username}</div>
                            </div>
                            <button onClick={() => remove(m.id)} style={dangerSt}>Hapus</button>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}

// ── Auth Mode Form (dipakai di Add dan Edit) ──────────────────────────────────

const AUTH_MODES = [
    { value: 'mandiri', label: 'Login Mandiri', desc: 'Siswa isi sendiri username/password di Guacamole' },
    { value: 'credentials', label: 'Kredensial Grup', desc: 'Semua anggota grup pakai akun OS yang sama' },
];

function AuthModeForm({ value, onChange }) {
    const radioSt = (active) => ({
        padding: '7px 12px', borderRadius: 6, cursor: 'pointer', fontSize: 11,
        border: `1px solid ${active ? 'var(--cyan)' : 'var(--border)'}`,
        background: active ? 'var(--bg-hover)' : 'transparent',
        color: active ? 'var(--cyan)' : 'var(--text3)',
        textAlign: 'left',
    });

    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 2 }}>Mode Koneksi *</div>
            <div style={{ display: 'flex', gap: 6 }}>
                {AUTH_MODES.map(m => (
                    <button key={m.value} onClick={() => onChange({ ...value, auth_mode: m.value })}
                        style={radioSt(value.auth_mode === m.value)}>
                        <div style={{ fontWeight: 600 }}>{m.label}</div>
                        <div style={{ fontSize: 10, marginTop: 2, color: 'var(--text3)' }}>{m.desc}</div>
                    </button>
                ))}
            </div>

            {value.auth_mode === 'credentials' && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 4 }}>
                    <div style={{ display: 'flex', gap: 6 }}>
                        <div style={{ flex: 1 }}>
                            <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 3 }}>OS Type</div>
                            <select value={value.os_type || 'linux'}
                                onChange={e => onChange({ ...value, os_type: e.target.value })}
                                style={{ ...inputSt }}>
                                <option value="linux">Linux</option>
                                <option value="windows">Windows</option>
                            </select>
                        </div>
                        <div style={{ flex: 1 }}>
                            <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 3 }}>Protokol</div>
                            <select value={value.guac_protocol || ''}
                                onChange={e => onChange({ ...value, guac_protocol: e.target.value })}
                                style={{ ...inputSt }}>
                                <option value="">— Auto —</option>
                                <option value="ssh">SSH</option>
                                <option value="rdp">RDP</option>
                            </select>
                        </div>
                    </div>
                    <div>
                        <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 3 }}>Username OS *</div>
                        <input style={inputSt} value={value.os_username || ''}
                            onChange={e => onChange({ ...value, os_username: e.target.value })}
                            placeholder="Contoh: student" autoComplete="off" />
                    </div>
                    <div>
                        <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 3 }}>
                            Password OS * {value._editing && <span style={{ color: 'var(--yellow)' }}>(kosongkan = tidak diubah)</span>}
                        </div>
                        <input style={inputSt} type="password" value={value.os_password || ''}
                            onChange={e => onChange({ ...value, os_password: e.target.value })}
                            placeholder={value._editing ? '••••••••' : 'Password akun OS'}
                            autoComplete="new-password" />
                    </div>
                </div>
            )}
        </div>
    );
}

// ── VM Access Panel ───────────────────────────────────────────────────────────

const emptyAuthForm = { auth_mode: 'mandiri', os_type: 'linux', guac_protocol: '', os_username: '', os_password: '' };

function VmAccessPanel({ group, allVms }) {
    const [vms, setVms]           = useState([]);
    const [loading, setLoading]   = useState(true);
    const [selectedVm, setSelectedVm] = useState('');
    const [authForm, setAuthForm] = useState(emptyAuthForm);
    const [editTarget, setEditTarget] = useState(null); // vm object being edited
    const [editForm, setEditForm] = useState(null);
    const [err, setErr]           = useState('');
    const [saving, setSaving]     = useState(false);

    const load = useCallback(async () => {
        setLoading(true);
        try { setVms(await fetchGroupVms(group.id)); }
        finally { setLoading(false); }
    }, [group.id]);

    useEffect(() => { load(); setEditTarget(null); setEditForm(null); setSelectedVm(''); setAuthForm(emptyAuthForm); }, [load]);

    const accessKeys = new Set(vms.map(v => `${v.vm_id}|${v.host_name}`));
    const available = allVms.filter(v => !accessKeys.has(`${v.vm_id}|${v.host_name}`));

    const add = async () => {
        if (!selectedVm) return;
        const [vm_id, host_name] = selectedVm.split('||');
        setErr(''); setSaving(true);
        try {
            await addGroupVm(group.id, { vm_id, host_name, ...authForm });
            setSelectedVm(''); setAuthForm(emptyAuthForm);
            load();
        } catch (e) { setErr(e?.response?.data?.detail || e.message); }
        finally { setSaving(false); }
    };

    const startEdit = (v) => {
        setEditTarget(v);
        setEditForm({
            vm_id: v.vm_id, host_name: v.host_name,
            auth_mode: v.auth_mode || 'mandiri',
            os_type: v.os_type || 'linux',
            guac_protocol: v.guac_protocol || '',
            os_username: v.os_username || '',
            os_password: '',
            _editing: true,
        });
    };

    const saveEdit = async () => {
        setErr(''); setSaving(true);
        try {
            await updateGroupVm(group.id, editForm);
            setEditTarget(null); setEditForm(null);
            load();
        } catch (e) { setErr(e?.response?.data?.detail || e.message); }
        finally { setSaving(false); }
    };

    const remove = async (vm_id, host_name) => {
        setErr('');
        try { await removeGroupVm(group.id, vm_id, host_name); load(); }
        catch (e) { setErr(e?.response?.data?.detail || e.message); }
    };

    const authBadge = (v) => {
        if (v.auth_mode === 'credentials') {
            return (
                <span style={{ fontSize: 10, color: 'var(--purple)', fontFamily: 'var(--fmono)',
                    background: 'var(--purple)18', border: '1px solid var(--purple)33',
                    borderRadius: 4, padding: '1px 5px' }}>
                    creds: {v.os_username}
                </span>
            );
        }
        return (
            <span style={{ fontSize: 10, color: 'var(--yellow)', fontFamily: 'var(--fmono)',
                background: 'var(--yellow)18', border: '1px solid var(--yellow)33',
                borderRadius: 4, padding: '1px 5px' }}>
                mandiri
            </span>
        );
    };

    return (
        <div>
            <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 10 }}>
                Akses VM — {vms.length} VM
            </div>

            {/* Add VM form */}
            <div style={{ marginBottom: 12, padding: 10, background: 'var(--bg)', borderRadius: 8, border: '1px solid var(--border)' }}>
                <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
                    <select value={selectedVm} onChange={e => setSelectedVm(e.target.value)}
                        style={{ ...inputSt, flex: 1, fontFamily: 'var(--fmono)' }}>
                        <option value="">— Pilih VM untuk ditambah —</option>
                        {available.map(v => (
                            <option key={`${v.vm_id}|${v.host_name}`} value={`${v.vm_id}||${v.host_name}`}>
                                {v.vm_name} — {v.host_name}
                            </option>
                        ))}
                    </select>
                </div>
                {selectedVm && (
                    <div style={{ marginBottom: 8 }}>
                        <AuthModeForm value={authForm} onChange={setAuthForm} />
                    </div>
                )}
                {selectedVm && (
                    <button onClick={add} disabled={saving || !selectedVm} style={btnSt()}>
                        {saving ? 'Menyimpan...' : '+ Tambah VM'}
                    </button>
                )}
            </div>

            {err && <div style={{ fontSize: 11, color: 'var(--red)', marginBottom: 6 }}>{err}</div>}

            {loading ? (
                <div style={{ fontSize: 12, color: 'var(--text3)', padding: '12px 0' }}>Loading...</div>
            ) : vms.length === 0 ? (
                <div style={{ fontSize: 12, color: 'var(--text3)', padding: '12px 0' }}>Belum ada VM yang di-assign.</div>
            ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                    {vms.map(v => {
                        const meta = allVms.find(a => a.vm_id === v.vm_id && a.host_name === v.host_name);
                        const isEditing = editTarget?.vm_id === v.vm_id && editTarget?.host_name === v.host_name;
                        return (
                            <div key={`${v.vm_id}|${v.host_name}`} style={{
                                padding: '8px 10px', background: 'var(--bg-card2)', borderRadius: 6,
                                border: `1px solid ${isEditing ? 'var(--cyan)44' : 'transparent'}`,
                            }}>
                                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                    <div>
                                        <div style={{ fontSize: 12, fontWeight: 600, fontFamily: 'var(--fmono)' }}>
                                            {meta?.vm_name || v.vm_id}
                                        </div>
                                        <div style={{ display: 'flex', gap: 6, marginTop: 3, alignItems: 'center' }}>
                                            <span style={{ fontSize: 10, color: 'var(--text3)' }}>{v.host_name}</span>
                                            {authBadge(v)}
                                        </div>
                                    </div>
                                    <div style={{ display: 'flex', gap: 4 }}>
                                        <button onClick={() => isEditing ? (setEditTarget(null), setEditForm(null)) : startEdit(v)}
                                            style={{ ...btnSt('var(--text3)'), padding: '2px 7px', fontSize: 10 }}>
                                            {isEditing ? 'Tutup' : '✎'}
                                        </button>
                                        <button onClick={() => remove(v.vm_id, v.host_name)} style={dangerSt}>Hapus</button>
                                    </div>
                                </div>
                                {isEditing && editForm && (
                                    <div style={{ marginTop: 10, paddingTop: 10, borderTop: '1px solid var(--border)' }}>
                                        <AuthModeForm value={editForm} onChange={setEditForm} />
                                        <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
                                            <button onClick={saveEdit} disabled={saving} style={btnSt()}>
                                                {saving ? 'Menyimpan...' : 'Simpan'}
                                            </button>
                                            <button onClick={() => { setEditTarget(null); setEditForm(null); }}
                                                style={btnSt('var(--text3)')}>Batal</button>
                                        </div>
                                    </div>
                                )}
                            </div>
                        );
                    })}
                </div>
            )}
        </div>
    );
}

// ── Main GroupsPage ───────────────────────────────────────────────────────────

export default function GroupsPage() {
    const [groups, setGroups] = useState([]);
    const [allStudents, setAllStudents] = useState([]);
    const [allVms, setAllVms] = useState([]);
    const [loading, setLoading] = useState(true);
    const [selectedId, setSelectedId] = useState(null);
    const [activePanel, setActivePanel] = useState('members'); // 'members' | 'vms'
    const [showForm, setShowForm] = useState(false);
    const [editTarget, setEditTarget] = useState(null);
    const [err, setErr] = useState('');

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const [grps, users, vms] = await Promise.all([fetchGroups(), fetchUsers(), fetchAllProxmoxVmsFlat()]);
            setGroups(grps);
            setAllStudents(users.filter(u => u.role === 'student'));
            setAllVms(vms);
        } catch { setErr('Gagal memuat data grup'); }
        finally { setLoading(false); }
    }, []);

    useEffect(() => { load(); }, [load]);

    const selected = groups.find(g => g.id === selectedId);

    const handleCreate = async (body) => {
        await createGroup(body);
        setShowForm(false);
        load();
    };

    const handleUpdate = async (body) => {
        await updateGroup(editTarget.id, body);
        setEditTarget(null);
        load();
    };

    const handleDelete = async (g) => {
        if (!confirm(`Hapus grup "${g.name}"? Semua akses VM grup ini akan ikut terhapus.`)) return;
        try {
            await deleteGroup(g.id);
            if (selectedId === g.id) setSelectedId(null);
            load();
        } catch (e) { setErr(e?.response?.data?.detail || e.message); }
    };

    return (
        <div style={{ display: 'grid', gridTemplateColumns: '280px 1fr', gap: 16, height: '100%' }}>

            {/* Left: Group list */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>
                        Grup / Kelas ({groups.length})
                    </div>
                    <button onClick={() => { setShowForm(true); setEditTarget(null); }} style={btnSt()}>
                        + Buat
                    </button>
                </div>

                {showForm && !editTarget && (
                    <div style={{ padding: 12, background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 8 }}>
                        <GroupForm onSave={handleCreate} onCancel={() => setShowForm(false)} />
                    </div>
                )}

                {err && <div style={{ fontSize: 11, color: 'var(--red)' }}>{err}</div>}

                {loading ? (
                    <div style={{ fontSize: 12, color: 'var(--text3)', padding: 8 }}>Loading...</div>
                ) : groups.length === 0 ? (
                    <div style={{ fontSize: 12, color: 'var(--text3)', padding: 8 }}>Belum ada grup.</div>
                ) : (
                    groups.map(g => (
                        <div key={g.id}>
                            {editTarget?.id === g.id ? (
                                <div style={{ padding: 12, background: 'var(--bg-card)', border: '1px solid var(--cyan)44', borderRadius: 8 }}>
                                    <GroupForm initial={g} onSave={handleUpdate} onCancel={() => setEditTarget(null)} />
                                </div>
                            ) : (
                                <div
                                    onClick={() => setSelectedId(g.id)}
                                    style={{
                                        padding: '10px 12px', borderRadius: 8, cursor: 'pointer',
                                        background: selectedId === g.id ? 'var(--bg-hover)' : 'var(--bg-card)',
                                        border: `1px solid ${selectedId === g.id ? 'var(--cyan)44' : 'var(--border)'}`,
                                    }}
                                >
                                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                                        <div style={{ fontSize: 13, fontWeight: 600, color: selectedId === g.id ? 'var(--cyan)' : 'var(--text)' }}>
                                            {g.name}
                                        </div>
                                        <div style={{ display: 'flex', gap: 4 }}>
                                            <button onClick={e => { e.stopPropagation(); setEditTarget(g); setShowForm(false); }}
                                                style={{ ...btnSt('var(--text3)'), padding: '2px 7px', fontSize: 10 }}>✎</button>
                                            <button onClick={e => { e.stopPropagation(); handleDelete(g); }}
                                                style={{ ...dangerSt }}>✕</button>
                                        </div>
                                    </div>
                                    {g.description && (
                                        <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>{g.description}</div>
                                    )}
                                    <div style={{ display: 'flex', gap: 10, marginTop: 6, fontSize: 10, color: 'var(--text3)', fontFamily: 'var(--fmono)' }}>
                                        <span style={{ color: 'var(--purple)' }}>{g.member_count} siswa</span>
                                        <span style={{ color: 'var(--cyan)' }}>{g.vm_count} VM</span>
                                    </div>
                                </div>
                            )}
                        </div>
                    ))
                )}
            </div>

            {/* Right: Detail panel */}
            <div style={{ minHeight: 0 }}>
                {!selected ? (
                    <div style={{
                        height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center',
                        color: 'var(--text3)', fontSize: 13, border: '1px dashed var(--border)', borderRadius: 10,
                    }}>
                        Pilih grup untuk mengelola anggota dan akses VM
                    </div>
                ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                            <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--cyan)' }}>{selected.name}</div>
                            {selected.description && (
                                <div style={{ fontSize: 12, color: 'var(--text3)' }}>{selected.description}</div>
                            )}
                        </div>

                        {/* Panel tabs */}
                        <div style={{ display: 'flex', gap: 6 }}>
                            {[['members', 'Anggota'], ['vms', 'Akses VM']].map(([id, label]) => (
                                <button key={id} onClick={() => setActivePanel(id)} style={{
                                    padding: '5px 14px', borderRadius: 6, fontSize: 12, cursor: 'pointer',
                                    background: activePanel === id ? 'var(--bg-hover)' : 'transparent',
                                    color: activePanel === id ? 'var(--cyan)' : 'var(--text3)',
                                    border: `1px solid ${activePanel === id ? 'var(--cyan)44' : 'transparent'}`,
                                }}>{label}</button>
                            ))}
                        </div>

                        <div style={{ padding: 14, background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 8 }}>
                            {activePanel === 'members' ? (
                                <MembersPanel group={selected} allStudents={allStudents} />
                            ) : (
                                <VmAccessPanel group={selected} allVms={allVms} />
                            )}
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}
