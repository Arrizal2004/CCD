import { useState, useEffect, useCallback } from 'react';
import { fetchUsers, fetchUserAssignments, assignVm, removeAssignment, fetchVmOsAccounts, upsertVmOsAccount } from '../api';
import { NewPasswordNotice } from './VmOsAccountsPanel';
import { tNodes, useT } from '../i18n';

// Assign VM ke user student, opsional dengan OS account (username/password) khusus per-student
// untuk connect Guacamole mandiri. Reuse infra ssh_creds/vm_assignments generik yang sudah ada
// (dibangun untuk Hyper-V, bekerja sama untuk Proxmox karena kunci vm_id/host_name generik).
// "Tambah Baru" bisa sekaligus membuat user-nya di dalam VM lewat QEMU Guest Agent.
const EMPTY_QUICK = { os_username: '', password: '', create_in_vm: true };

export default function ProxmoxAssignmentsPanel({ hostName, vmid, vmName }) {
    const t = useT();
    const vmIdStr = String(vmid);
    const [students, setStudents] = useState([]);
    const [assignments, setAssignments] = useState({}); // {student_id: {os_account_id, os_username} | null}
    const [osAccounts, setOsAccounts] = useState([]);
    const [loading, setLoading] = useState(true);
    const [pickingFor, setPickingFor] = useState(null);
    const [quickForm, setQuickForm] = useState(EMPTY_QUICK);
    const [notice, setNotice] = useState(null);
    const [quickSaving, setQuickSaving] = useState(false);
    const [quickErr, setQuickErr] = useState('');
    const [quickOpen, setQuickOpen] = useState(false);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const [users, osAccList] = await Promise.all([
                fetchUsers(),
                fetchVmOsAccounts(hostName, vmIdStr).catch(() => []),
            ]);
            const studs = users.filter(u => u.role === 'student');
            setStudents(studs);
            setOsAccounts(osAccList);

            const map = {};
            await Promise.all(studs.map(async s => {
                const as = await fetchUserAssignments(s.id);
                const match = as.find(a => a.vm_id === vmIdStr && a.host_name === hostName);
                map[s.id] = match ? { os_account_id: match.os_account_id || null, os_username: match.os_username || null } : null;
            }));
            setAssignments(map);
        } catch (e) {
            console.error(e);
        } finally {
            setLoading(false);
        }
    }, [hostName, vmIdStr]);

    useEffect(() => { load(); }, [load]);

    const doAssign = async (student, osAccountId = null) => {
        setPickingFor(null);
        try {
            await assignVm(student.id, vmIdStr, hostName, osAccountId, vmName);
            const acc = osAccounts.find(a => a.id === osAccountId);
            setAssignments(p => ({ ...p, [student.id]: { os_account_id: osAccountId, os_username: acc?.os_username || null } }));
        } catch (e) {
            alert(t('asg.assignFailed', { msg: e?.response?.data?.detail || e.message }));
        }
    };

    const doQuickAddAndAssign = async (student) => {
        if (!quickForm.os_username.trim()) return setQuickErr(t('asg.osUserRequired'));
        if (!quickForm.password && !quickForm.create_in_vm) return setQuickErr(t('asg.passwordRequired'));
        setQuickSaving(true); setQuickErr(''); setNotice(null);
        try {
            const res = await upsertVmOsAccount(hostName, vmIdStr, { ...quickForm, os_username: quickForm.os_username.trim(), password: quickForm.password || null });
            const newAcc = { id: res.id, os_username: res.os_username };
            setOsAccounts(p => [...p, newAcc]);
            if (res.password) setNotice({ username: res.os_username, password: res.password });
            await doAssign(student, res.id);
            setQuickOpen(false);
        } catch (e) {
            setQuickErr(e?.response?.data?.detail || e.message);
        } finally {
            setQuickSaving(false);
        }
    };

    const toggleAssign = async (student) => {
        if (assignments[student.id]) {
            try {
                await removeAssignment(student.id, vmIdStr);
                setAssignments(p => ({ ...p, [student.id]: null }));
            } catch (e) {
                alert(t('asg.unassignFailed', { msg: e?.response?.data?.detail || e.message }));
            }
        } else {
            setQuickForm(EMPTY_QUICK);
            setQuickErr('');
            setQuickOpen(false);
            setPickingFor(student.id);
        }
    };

    if (loading) return <div style={{ padding: 20, textAlign: 'center', color: 'var(--text3)', fontSize: 12 }}>{t('common.loading')}</div>;
    if (students.length === 0) return <div style={{ padding: 20, textAlign: 'center', color: 'var(--text3)', fontSize: 12 }}>{t('asg.noStudents')}</div>;

    return (
        <>
        {notice && <NewPasswordNotice {...notice} onClose={() => setNotice(null)} />}
        <div style={{ border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
            {students.map(s => {
                const asgn = assignments[s.id];
                const isPicking = pickingFor === s.id;
                return (
                    <div key={s.id} style={{ borderBottom: '1px solid var(--border)' }}>
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 14px' }}>
                            <div>
                                <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>{s.full_name}</div>
                                <div style={{ fontSize: 11, color: 'var(--text3)', display: 'flex', gap: 6, alignItems: 'center' }}>
                                    @{s.username}
                                    {asgn && asgn.os_username && (
                                        <span style={{ fontSize: 10, fontFamily: 'var(--fmono)', padding: '1px 6px', borderRadius: 8, background: 'var(--cyan-glow)', color: 'var(--cyan)', border: '1px solid var(--cyan)' }}>
                                            {asgn.os_username}
                                        </span>
                                    )}
                                    {asgn && !asgn.os_username && <span style={{ fontSize: 10, color: 'var(--text3)' }}>{t('asg.defaultCred')}</span>}
                                </div>
                            </div>
                            <button onClick={() => toggleAssign(s)}
                                style={{
                                    padding: '4px 10px', borderRadius: 4, fontSize: 11, fontWeight: 600, cursor: 'pointer',
                                    background: asgn ? 'var(--red-glow)' : 'var(--green-glow)',
                                    color: asgn ? 'var(--red)' : 'var(--green)',
                                    border: `1px solid ${asgn ? 'var(--red)' : 'var(--green)'}`,
                                }}>
                                {asgn ? t('asg.remove') : t('asg.assign')}
                            </button>
                        </div>

                        {isPicking && (
                            <div style={{ padding: '8px 14px 12px', background: 'var(--bg-card)', borderTop: '1px solid var(--border)' }}>
                                <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 6 }}>{tNodes('asg.assignTo', { name: <b>{s.username}</b> })}</div>
                                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: quickOpen ? 8 : 0 }}>
                                    <button onClick={() => doAssign(s, null)}
                                        style={{ padding: '4px 10px', borderRadius: 6, fontSize: 11, cursor: 'pointer', background: 'var(--bg-hover)', color: 'var(--text2)', border: '1px solid var(--border)' }}>
                                        {t('asg.defaultCredBtn')}
                                    </button>
                                    {osAccounts.map(acc => (
                                        <button key={acc.id} onClick={() => doAssign(s, acc.id)}
                                            style={{ padding: '4px 10px', borderRadius: 6, fontSize: 11, cursor: 'pointer', fontFamily: 'var(--fmono)', background: 'var(--cyan-glow)', color: 'var(--cyan)', border: '1px solid var(--cyan)' }}>
                                            {acc.os_username}
                                        </button>
                                    ))}
                                    <button onClick={() => { setQuickOpen(o => !o); setQuickForm(EMPTY_QUICK); setQuickErr(''); }}
                                        style={{ padding: '4px 10px', borderRadius: 6, fontSize: 11, cursor: 'pointer', background: quickOpen ? 'var(--green-glow)' : 'transparent', color: 'var(--green)', border: '1px solid var(--green)' }}>
                                        {t('asg.addNew')}
                                    </button>
                                    <button onClick={() => setPickingFor(null)}
                                        style={{ padding: '4px 10px', borderRadius: 6, fontSize: 11, cursor: 'pointer', background: 'transparent', color: 'var(--text3)', border: '1px solid var(--border)' }}>
                                        {t('common.cancel')}
                                    </button>
                                </div>
                                {quickOpen && (
                                    <>
                                        <div style={{ display: 'flex', gap: 6, alignItems: 'flex-end', flexWrap: 'wrap', marginBottom: 6 }}>
                                            <div>
                                                <div style={{ fontSize: 10, color: 'var(--text3)', marginBottom: 2 }}>{t('asg.osUser')}</div>
                                                <input value={quickForm.os_username} onChange={e => setQuickForm(f => ({ ...f, os_username: e.target.value }))}
                                                    placeholder="user_a"
                                                    style={{ padding: '4px 8px', borderRadius: 5, border: '1px solid var(--border)', background: 'var(--bg)', color: 'var(--text)', fontSize: 12, width: 110 }} />
                                            </div>
                                            <div>
                                                <div style={{ fontSize: 10, color: 'var(--text3)', marginBottom: 2 }}>{t('asg.password')}{quickForm.create_in_vm ? '' : ' *'}</div>
                                                <input type={quickForm.create_in_vm ? 'text' : 'password'} value={quickForm.password} onChange={e => setQuickForm(f => ({ ...f, password: e.target.value }))}
                                                    placeholder={quickForm.create_in_vm ? t('asg.randomPh') : '••••••'} autoComplete="new-password"
                                                    style={{ padding: '4px 8px', borderRadius: 5, border: '1px solid var(--border)', background: 'var(--bg)', color: 'var(--text)', fontSize: 12, width: 110 }} />
                                            </div>
                                            <button onClick={() => doQuickAddAndAssign(s)} disabled={quickSaving}
                                                style={{ padding: '4px 10px', borderRadius: 6, fontSize: 11, fontWeight: 600, cursor: 'pointer', background: 'var(--cyan)', color: '#000', border: 'none', opacity: quickSaving ? 0.6 : 1 }}>
                                                {quickSaving ? '…' : t('asg.addAssign')}
                                            </button>
                                        </div>
                                        <label style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 11, color: 'var(--text2)', marginBottom: 6 }}>
                                            <input type="checkbox" checked={quickForm.create_in_vm} onChange={e => setQuickForm(f => ({ ...f, create_in_vm: e.target.checked }))} />
                                            {t('asg.createInVm')}
                                        </label>
                                        {quickErr && <div style={{ fontSize: 11, color: 'var(--red)' }}>{quickErr}</div>}
                                    </>
                                )}
                            </div>
                        )}
                    </div>
                );
            })}
        </div>
        </>
    );
}
