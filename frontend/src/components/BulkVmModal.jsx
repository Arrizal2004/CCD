import { useState, useEffect, useRef } from 'react';
import {
    fetchGroups, fetchProxmoxTemplates, fetchNetworks,
    previewVmBatch, createVmBatch, fetchVmBatches, fetchVmBatch, retryVmBatch, downloadVmBatchCsv,
} from '../api';
import { useSysConfig } from '../sysconfig';
import useIsMobile from '../useIsMobile';
import { locale, t as translate, tNodes, useT } from '../i18n';

// Buat VM massal untuk satu kelas (grup): satu VM per anggota dari template yang sama. Rencana ditampilkan
// dulu (nama VM, username OS, bentrok nama, kapasitas), lalu VM dibuat satu per satu di latar belakang dan
// langsung di-assign ke mahasiswanya. Jendela boleh ditutup; progres tetap bisa dibuka lagi.

const detail = (e) => e?.response?.data?.detail || e?.message || translate('common.failed');
const input = { width: '100%', boxSizing: 'border-box', background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 6, padding: '6px 10px', color: 'var(--text)', fontSize: 12 };
const label = { fontSize: 10, color: 'var(--text3)', marginBottom: 2, display: 'block' };
const section = { fontSize: 10, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '0.08em', fontWeight: 600, margin: '14px 0 8px' };
const small = { padding: '5px 12px', fontSize: 11, borderRadius: 6, cursor: 'pointer', background: 'transparent', border: '1px solid var(--border)', color: 'var(--text2)' };
const primary = { ...small, background: 'var(--cyan)', color: '#000', border: 'none', fontWeight: 600 };
const grid = { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 10 };
const STATUS = {
    pending:  { icon: '…', color: 'var(--text3)', text: 'bulk.stPending' },
    creating: { icon: '⚙', color: 'var(--cyan)', text: 'bulk.stCreating' },
    done:     { icon: '✓', color: 'var(--green)', text: 'bulk.stDone' },
    failed:   { icon: '✗', color: 'var(--red)', text: 'bulk.stFailed' },
};

function Field({ title, children }) {
    return <label style={{ display: 'block' }}><span style={label}>{title}</span>{children}</label>;
}

function Progress({ batch, onRetry, busy }) {
    const t = useT();
    const total = batch.items.length;
    const { done, failed } = batch.counts;
    const pct = total ? Math.round(((done + failed) / total) * 100) : 0;
    const download = async () => {
        const blob = await downloadVmBatchCsv(batch.id);
        const url = URL.createObjectURL(blob);
        const a = Object.assign(document.createElement('a'), { href: url, download: `vm-massal-${batch.id}.csv` });
        a.click();
        URL.revokeObjectURL(url);
    };
    return (
        <div>
            <div style={{ fontSize: 12, color: 'var(--text2)', margin: '10px 0 6px' }}>
                {tNodes('bulk.summary', { id: batch.id, group: <b>{batch.group_name}</b>, done, failed, total })}
                {batch.status === 'running' && t('bulk.running')}
                {batch.status === 'interrupted' && t('bulk.interrupted')}
            </div>
            <div style={{ height: 6, background: 'var(--bg-hover)', borderRadius: 3, overflow: 'hidden', marginBottom: 10 }}>
                <div style={{ width: `${pct}%`, height: '100%', background: failed ? 'var(--yellow)' : 'var(--green)', transition: 'width 0.3s' }} />
            </div>
            {batch.status === 'running' && (
                <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 8 }}>{t('bulk.canClose')}</div>
            )}
            <div style={{ border: '1px solid var(--border)', borderRadius: 8 }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                    <thead>
                        <tr style={{ color: 'var(--text3)', fontSize: 10, textAlign: 'left' }}>
                            <th style={{ padding: '6px 8px' }}>{t('bulk.colStudent')}</th><th style={{ padding: '6px 8px' }}>{t('bulk.colVm')}</th>
                            <th style={{ padding: '6px 8px' }}>{t('bulk.colIp')}</th><th style={{ padding: '6px 8px' }}>{t('bulk.colStatus')}</th>
                        </tr>
                    </thead>
                    <tbody>
                        {batch.items.map(i => {
                            const st = STATUS[i.status] || STATUS.pending;
                            return (
                                <tr key={i.id} style={{ borderTop: '1px solid var(--border)' }}>
                                    <td style={{ padding: '6px 8px' }}>{i.full_name || i.username}</td>
                                    <td style={{ padding: '6px 8px', fontFamily: 'var(--fmono)' }}>{i.vm_name}{i.vmid ? ` (${i.vmid})` : ''}</td>
                                    <td style={{ padding: '6px 8px', fontFamily: 'var(--fmono)' }}>{i.ip || '—'}</td>
                                    <td style={{ padding: '6px 8px', color: st.color }} title={i.error || ''}>
                                        {st.icon} {t(st.text)}{i.error ? `: ${i.error}` : ''}
                                    </td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 12 }}>
                <button onClick={download} style={small}>{t('bulk.downloadCsv')}</button>
                {batch.status !== 'running' && (failed > 0 || batch.counts.pending > 0) && (
                    <button onClick={onRetry} disabled={busy} style={{ ...small, borderColor: 'var(--yellow)', color: 'var(--yellow)' }}>
                        {busy ? t('bulk.retrying') : batch.status === 'interrupted' ? t('bulk.resume') : t('bulk.retryFailed', { n: failed })}
                    </button>
                )}
            </div>
            <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 8, lineHeight: 1.5 }}>
                {t('bulk.csvHint')}
            </div>
        </div>
    );
}

export default function BulkVmModal({ instance, node, onClose, onChanged }) {
    const t = useT();
    const { default_vm_lease_days: defaultLease } = useSysConfig();
    const isMobile = useIsMobile();
    const [groups, setGroups] = useState(null);
    const [templates, setTemplates] = useState(null);
    const [switches, setSwitches] = useState([]);
    const [recent, setRecent] = useState([]);
    const [form, setForm] = useState({
        group_id: '', template_vmid: '', prefix: '', network_id: '', os_mode: 'student', os_username: 'siswa',
        cores: '', memory_mb: '', disk_gb: '', lease_days: defaultLease ? String(defaultLease) : '', start: false,
    });
    const [plan, setPlan] = useState(null);
    const [selected, setSelected] = useState(new Set());
    const [batchId, setBatchId] = useState(null);
    const [batch, setBatch] = useState(null);
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState('');
    // onChanged dari halaman Servers bisa berganti setiap render; simpan di ref supaya polling tidak diulang.
    const onChangedRef = useRef(onChanged);
    useEffect(() => { onChangedRef.current = onChanged; }, [onChanged]);
    const set = (k) => (e) => { setPlan(null); setForm(f => ({ ...f, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value })); };

    useEffect(() => {
        let alive = true;
        fetchGroups().then(g => { if (alive) setGroups(g); }).catch(e => { if (alive) { setGroups([]); setErr(detail(e)); } });
        fetchProxmoxTemplates(instance, node).then(list => {
            if (!alive) return;
            setTemplates(list);
            if (list[0]) setForm(f => ({ ...f, template_vmid: String(list[0].vmid), cores: String(list[0].cores), memory_mb: String(list[0].memory_mb), disk_gb: list[0].disk_gb ? String(list[0].disk_gb) : '' }));
        }).catch(e => { if (alive) { setTemplates([]); setErr(detail(e)); } });
        fetchNetworks().then(d => { if (alive) setSwitches(d.instances.find(i => i.label === instance)?.networks || []); }).catch(() => {});
        fetchVmBatches().then(list => {
            if (!alive) return;
            const mine = list.filter(b => b.instance === instance && b.node === node);
            setRecent(mine.slice(0, 5));
            const running = mine.find(b => b.status === 'running');
            if (running) setBatchId(running.id);           // buka lagi progres yang masih berjalan
        }).catch(() => {});
        return () => { alive = false; };
    }, [instance, node]);

    // Progres diperbarui tiap 2,5 detik selama batch berjalan; daftar VM dimuat ulang sekali saat selesai.
    useEffect(() => {
        if (!batchId) return undefined;
        let alive = true;
        let timer;
        let wasRunning = false;
        const tick = () => fetchVmBatch(batchId).then(b => {
            if (!alive) return;
            setBatch(b);
            if (b.status === 'running') {
                wasRunning = true;
                timer = setTimeout(tick, 2500);
            } else if (wasRunning) {
                onChangedRef.current?.();
            }
        }).catch(e => { if (alive) setErr(detail(e)); });
        tick();
        return () => { alive = false; clearTimeout(timer); };
    }, [batchId]);

    const tpl = templates?.find(tp => String(tp.vmid) === form.template_vmid);
    const num = (v) => (v === '' ? null : Number(v));
    const body = () => ({
        instance, node, group_id: Number(form.group_id), template_vmid: Number(form.template_vmid),
        prefix: form.prefix.trim() || null, network_id: form.network_id ? Number(form.network_id) : null,
        os_username: form.os_mode === 'fixed' ? form.os_username.trim() : null,
        cores: num(form.cores), memory_mb: num(form.memory_mb), disk_gb: num(form.disk_gb),
        lease_days: num(form.lease_days), start: form.start,
    });

    const showPlan = async (e) => {
        e.preventDefault();
        setBusy(true); setErr('');
        try {
            const p = await previewVmBatch(body());
            setPlan(p);
            setSelected(new Set(p.items.filter(i => !i.conflict && !i.inactive).map(i => i.user_id)));
        } catch (e2) {
            setErr(detail(e2));
        } finally {
            setBusy(false);
        }
    };

    const startBatch = async () => {
        setBusy(true); setErr('');
        try {
            const b = await createVmBatch({ ...body(), user_ids: [...selected] });
            setBatch(b);
            setBatchId(b.id);
        } catch (e) {
            setErr(detail(e));
        } finally {
            setBusy(false);
        }
    };

    const retry = async () => {
        setBusy(true); setErr('');
        try {
            const b = await retryVmBatch(batch.id);
            setBatch(b);
            setBatchId(null);
            setTimeout(() => setBatchId(b.id), 0);     // mulai lagi pemantauan progres
        } catch (e) {
            setErr(detail(e));
        } finally {
            setBusy(false);
        }
    };

    const toggle = (id) => setSelected(s => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
    const chosen = plan ? plan.items.filter(i => selected.has(i.user_id)) : [];
    const chosenConflicts = chosen.filter(i => i.conflict).length;

    return (
        <div onClick={busy ? undefined : onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 210, padding: isMobile ? 0 : 16 }}>
            <div onClick={e => e.stopPropagation()} role="dialog" aria-label={t('bulk.dialog')}
                style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', borderRadius: isMobile ? 0 : 10, width: isMobile ? '100vw' : 'min(760px, 96vw)', height: isMobile ? '100dvh' : 'auto', maxHeight: isMobile ? '100dvh' : '90vh', overflow: 'auto', padding: 20, boxSizing: 'border-box' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <div>
                        <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text)' }}>{t('bulk.title')}</div>
                        <div style={{ fontSize: 11, color: 'var(--text3)' }}>{t('bulk.subtitle', { target: `${instance}/${node}` })}</div>
                    </div>
                    <button onClick={onClose} aria-label={t('common.close')} style={{ background: 'transparent', border: 'none', color: 'var(--text3)', fontSize: 18, cursor: 'pointer' }}>×</button>
                </div>

                {err && <div role="alert" style={{ background: 'var(--red-glow)', border: '1px solid var(--red)', borderRadius: 6, padding: '6px 10px', color: 'var(--red)', fontSize: 11, marginTop: 12 }}>⚠ {err}</div>}

                {batch ? (
                    <>
                        <Progress batch={batch} onRetry={retry} busy={busy} />
                        {batch.status !== 'running' && (
                            <button onClick={() => { setBatch(null); setBatchId(null); setPlan(null); }} style={{ ...small, marginTop: 10 }}>{t('bulk.newBatch')}</button>
                        )}
                    </>
                ) : groups === null || templates === null ? (
                    <div style={{ color: 'var(--text3)', fontSize: 12, padding: '14px 0' }}>{t('common.loading')}</div>
                ) : templates.length === 0 ? (
                    <div style={{ color: 'var(--text3)', fontSize: 12, padding: '14px 0' }}>{t('bulk.noTemplates')}</div>
                ) : (
                    <form onSubmit={showPlan}>
                        <div style={section}>{t('bulk.sectionClass')}</div>
                        <div style={grid}>
                            <Field title={t('bulk.group')}>
                                <select value={form.group_id} onChange={set('group_id')} required style={input}>
                                    <option value="">{t('bulk.pickGroup')}</option>
                                    {groups.map(g => <option key={g.id} value={g.id}>{t('bulk.groupOption', { name: g.name, n: g.member_count })}</option>)}
                                </select>
                            </Field>
                            <Field title={t('create.template')}>
                                <select value={form.template_vmid} style={input} onChange={e => {
                                    const tp = templates.find(x => String(x.vmid) === e.target.value);
                                    setPlan(null);
                                    setForm(f => ({ ...f, template_vmid: e.target.value, cores: String(tp?.cores || ''), memory_mb: String(tp?.memory_mb || ''), disk_gb: tp?.disk_gb ? String(tp.disk_gb) : '' }));
                                }}>
                                    {templates.map(tp => <option key={tp.vmid} value={tp.vmid}>{tp.name} ({tp.vmid})</option>)}
                                </select>
                            </Field>
                            <Field title={t('bulk.prefix')}>
                                <input value={form.prefix} onChange={set('prefix')} placeholder={t('bulk.prefixPh')} style={input} />
                            </Field>
                        </div>

                        <div style={section}>{t('bulk.sectionSpecs')}</div>
                        <div style={grid}>
                            <Field title={t('create.cpu')}><input type="number" min="1" max="64" value={form.cores} onChange={set('cores')} style={input} /></Field>
                            <Field title={t('create.ram')}><input type="number" min="256" step="256" value={form.memory_mb} onChange={set('memory_mb')} style={input} /></Field>
                            <Field title={t('create.disk', { min: tpl?.disk_gb ?? '—' })}><input type="number" min={tpl?.disk_gb || 1} value={form.disk_gb} onChange={set('disk_gb')} style={input} /></Field>
                        </div>

                        <div style={section}>{t('bulk.sectionNetwork')}</div>
                        <div style={grid}>
                            <Field title={t('create.connectTo')}>
                                <select value={form.network_id} onChange={set('network_id')} style={input}>
                                    <option value="">{t('bulk.bridgeDhcp')}</option>
                                    {switches.map(n => <option key={n.id} value={n.id}>{t('create.switchOption', { name: n.name, cidr: n.cidr })}</option>)}
                                </select>
                            </Field>
                            <Field title={t('bulk.osUser')}>
                                <select value={form.os_mode} onChange={set('os_mode')} style={input}>
                                    <option value="student">{t('bulk.osStudent')}</option>
                                    <option value="fixed">{t('bulk.osFixed')}</option>
                                </select>
                            </Field>
                            {form.os_mode === 'fixed' && (
                                <Field title={t('bulk.osFixedName')}><input value={form.os_username} onChange={set('os_username')} required style={input} /></Field>
                            )}
                            <Field title={t('create.lease')}>
                                <input type="number" min="1" max="3650" value={form.lease_days} onChange={set('lease_days')} style={input} />
                            </Field>
                        </div>
                        <label style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 12, color: 'var(--text2)', marginTop: 10 }}>
                            <input type="checkbox" checked={form.start} onChange={set('start')} /> {t('create.start')}
                        </label>
                        <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 4 }}>{t('bulk.passwordHint')}</div>

                        {!plan && (
                            <button type="submit" disabled={busy || !form.group_id} style={{ ...primary, marginTop: 14, opacity: busy || !form.group_id ? 0.6 : 1 }}>
                                {busy ? t('bulk.planning') : t('bulk.showPlan')}
                            </button>
                        )}

                        {plan && (
                            <div>
                                <div style={section}>{t('bulk.plan', { n: chosen.length, total: plan.count })}</div>
                                <div style={{ fontSize: 12, color: 'var(--text2)', lineHeight: 1.6, marginBottom: 8 }}>
                                    {t('bulk.planInfo', { name: plan.template.name, ram: (Number(form.memory_mb) || plan.template.memory_mb) * chosen.length })}
                                    {plan.node_free_mb != null && t('bulk.planFree', { mb: plan.node_free_mb })}
                                    {plan.free_ips != null && t('bulk.planIps', { n: plan.free_ips })}
                                </div>
                                {plan.warnings.map(w => <div key={w} style={{ fontSize: 11, color: 'var(--yellow)', marginBottom: 4 }}>⚠ {w}</div>)}
                                <div style={{ border: '1px solid var(--border)', borderRadius: 8, marginTop: 6 }}>
                                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                                        <thead>
                                            <tr style={{ color: 'var(--text3)', fontSize: 10, textAlign: 'left' }}>
                                                <th style={{ padding: '6px 8px' }} /><th style={{ padding: '6px 8px' }}>{t('bulk.colStudent')}</th>
                                                <th style={{ padding: '6px 8px' }}>{t('bulk.colVmName')}</th><th style={{ padding: '6px 8px' }}>{t('bulk.colOsUser')}</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {plan.items.map(i => (
                                                <tr key={i.user_id} style={{ borderTop: '1px solid var(--border)', opacity: selected.has(i.user_id) ? 1 : 0.5 }}>
                                                    <td style={{ padding: '6px 8px' }}>
                                                        <input type="checkbox" aria-label={t('bulk.pick', { name: i.username })} checked={selected.has(i.user_id)} onChange={() => toggle(i.user_id)} />
                                                    </td>
                                                    <td style={{ padding: '6px 8px' }}>
                                                        {i.full_name || i.username}
                                                        {i.inactive && <div style={{ color: 'var(--yellow)', fontSize: 11 }}>{t('bulk.inactive')}</div>}
                                                    </td>
                                                    <td style={{ padding: '6px 8px', fontFamily: 'var(--fmono)' }}>
                                                        {i.vm_name}{i.conflict && <div style={{ color: 'var(--red)', fontFamily: 'var(--font)', fontSize: 11 }}>{i.conflict}</div>}
                                                    </td>
                                                    <td style={{ padding: '6px 8px', fontFamily: 'var(--fmono)' }}>{i.os_username}</td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 12 }}>
                                    <button type="button" onClick={() => setPlan(null)} style={small}>{t('bulk.back')}</button>
                                    <button type="button" onClick={startBatch} disabled={busy || chosen.length === 0 || chosenConflicts > 0}
                                        style={{ ...primary, opacity: busy || chosen.length === 0 || chosenConflicts > 0 ? 0.6 : 1 }}>
                                        {busy ? t('bulk.starting') : t('bulk.create', { n: chosen.length })}
                                    </button>
                                    {chosenConflicts > 0 && <span style={{ fontSize: 11, color: 'var(--red)', alignSelf: 'center' }}>{t('bulk.uncheckConflicts')}</span>}
                                </div>
                            </div>
                        )}

                        {recent.length > 0 && (
                            <div style={{ marginTop: 18 }}>
                                <div style={section}>{t('bulk.previous')}</div>
                                {recent.map(b => (
                                    <button key={b.id} type="button" onClick={() => setBatchId(b.id)}
                                        style={{ ...small, display: 'block', width: '100%', textAlign: 'left', marginBottom: 6 }}>
                                        {t('bulk.recent', { id: b.id, group: b.group_name, done: b.done, total: b.total, failed: b.failed ? t('bulk.recentFailed', { n: b.failed }) : '', date: new Date(b.created_at).toLocaleString(locale()) })}
                                    </button>
                                ))}
                            </div>
                        )}
                    </form>
                )}
            </div>
        </div>
    );
}
