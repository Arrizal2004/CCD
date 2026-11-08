import { useCallback, useState } from 'react';
import {
    cancelExam, createExam, deleteExam, endExamNow, fetchExams, fetchGroupLabVms, fetchLabReset, fetchUsageReport,
    startExamNow, startGroupReset, fetchUsageReportCsv,
} from '../api';
import { locale, useT } from '../i18n';
import useLoadEffect from '../useLoadEffect';
import { appTimeZone } from '../sysconfig';

const input = { background: 'var(--bg-card2)', border: '1px solid var(--border)', borderRadius: 6, padding: '6px 9px', color: 'var(--text)', fontSize: 12 };
const btn = (c = 'var(--border)', fg = 'var(--text2)') => ({ padding: '5px 12px', fontSize: 11, borderRadius: 6, cursor: 'pointer', background: 'transparent', border: `1px solid ${c}`, color: fg });
const primary = { padding: '7px 16px', borderRadius: 6, fontSize: 12, fontWeight: 600, background: 'var(--cyan)', color: '#000', border: 'none', cursor: 'pointer' };
const errText = (e) => e?.response?.data?.detail || e?.message || '';

function fmt(iso) {
    if (!iso) return '—';
    try { return new Date(iso).toLocaleString(locale(), { timeZone: appTimeZone(), day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }); }
    catch { return iso; }
}

/* ── Lab: baseline dan reset kelas ───────────────────────────────────────── */
export function LabResetPanel({ group }) {
    const t = useT();
    const [vms, setVms] = useState(null);
    const [run, setRun] = useState(null);
    const [start, setStart] = useState(true);
    const [error, setError] = useState('');
    const loadVms = useCallback(async () => { try { setVms(await fetchGroupLabVms(group.id)); } catch (e) { setError(errText(e)); } }, [group.id]);
    useLoadEffect(loadVms);

    const poll = useCallback(async () => {
        if (!run) return;
        try { setRun(await fetchLabReset(run.id)); } catch (e) { setError(errText(e)); }
    }, [run?.id]);                                                                   // eslint-disable-line react-hooks/exhaustive-deps
    useLoadEffect(poll, run && run.status === 'running' ? 1500 : 0, !!run);

    const go = async () => {
        if (!window.confirm(t('lab.resetConfirm', { n: vms?.length || 0 }))) return;
        setError('');
        try { const r = await startGroupReset(group.id, start); setRun(await fetchLabReset(r.id)); } catch (e) { setError(errText(e)); }
    };
    return (
        <div>
            <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 12, lineHeight: 1.6 }}>{t('lab.hint')}</div>
            {error && <div role="alert" style={{ color: 'var(--red)', fontSize: 12, marginBottom: 10 }}>⚠ {error}</div>}
            {vms && !vms.length && <div style={{ fontSize: 12, color: 'var(--text3)' }}>{t('lab.noVms')}</div>}
            {vms?.length > 0 && (
                <div style={{ display: 'grid', gap: 4, marginBottom: 14 }}>
                    {vms.map(v => (
                        <div key={`${v.host_name}/${v.vm_id}`} data-testid="lab-vm" style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, padding: '4px 0', borderBottom: '1px solid var(--border)' }}>
                            <span>{v.vm_name}</span>
                            <span style={{ color: v.baseline ? 'var(--green)' : 'var(--yellow)' }}>{t('lab.baseline')}: {v.baseline ? t('lab.hasBaseline') : t('lab.noBaseline')}</span>
                        </div>
                    ))}
                </div>
            )}
            <label style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 12, color: 'var(--text2)', marginBottom: 10 }}>
                <input type="checkbox" checked={start} onChange={e => setStart(e.target.checked)} /> {t('lab.startAfter')}
            </label>
            <button type="button" style={primary} disabled={!vms?.length || run?.status === 'running'} onClick={go}>{t('lab.resetAll')}</button>
            {run && (
                <div data-testid="lab-run" style={{ marginTop: 14 }}>
                    <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6, color: run.status === 'running' ? 'var(--cyan)' : 'var(--text)' }}>
                        {run.status === 'running' ? t('lab.running') : t('lab.resultLine', { ok: run.ok, total: run.total })}
                    </div>
                    {run.items.map(i => (
                        <div key={`${i.host_name}/${i.vm_id}`} style={{ fontSize: 12, color: i.pending ? 'var(--text3)' : i.ok ? 'var(--green)' : 'var(--red)' }}>
                            {i.vm_name}: {i.pending ? t('lab.itemPending') : i.ok ? t('lab.itemOk') : i.reason_text}
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}

/* ── Ujian ───────────────────────────────────────────────────────────────── */
const toLocalInput = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);

export function ExamsPanel({ group }) {
    const t = useT();
    const [exams, setExams] = useState(null);
    const [error, setError] = useState('');
    const [form, setForm] = useState(() => {
        const s = new Date(Date.now() + 3600e3);
        return { name: '', starts: toLocalInput(s), ends: toLocalInput(new Date(s.getTime() + 2 * 3600e3)), reset_at_start: true, start_vms: true, shutdown_at_end: true, block_open_web: true };
    });
    const load = useCallback(async () => { try { setExams(await fetchExams(group.id)); } catch (e) { setError(errText(e)); } }, [group.id]);
    useLoadEffect(load, 20000);
    const set = (k) => (e) => setForm(f => ({ ...f, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value }));
    const act = async (fn, confirmText) => {
        if (confirmText && !window.confirm(confirmText)) return;
        setError('');
        try { await fn(); await load(); } catch (e) { setError(errText(e)); }
    };
    const create = (e) => {
        e.preventDefault();
        return act(() => createExam({
            name: form.name, group_id: group.id, starts_at: new Date(form.starts).toISOString(), ends_at: new Date(form.ends).toISOString(),
            reset_at_start: form.reset_at_start, start_vms: form.start_vms, shutdown_at_end: form.shutdown_at_end, block_open_web: form.block_open_web,
        }));
    };
    const color = { scheduled: 'var(--cyan)', running: 'var(--green)', finished: 'var(--text3)', cancelled: 'var(--yellow)' };
    const sr = (r, key) => (r && r.total != null ? t(key, { ok: r.ok, total: r.total }) : null);

    return (
        <div>
            <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 12, lineHeight: 1.6 }}>{t('exam.hint')}</div>
            {error && <div role="alert" style={{ color: 'var(--red)', fontSize: 12, marginBottom: 10 }}>⚠ {error}</div>}
            {exams && !exams.length && <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 12 }}>{t('exam.empty')}</div>}
            <div style={{ display: 'grid', gap: 8, marginBottom: 16 }}>
                {(exams || []).map(x => (
                    <div key={x.id} data-testid="exam-row" style={{ border: '1px solid var(--border)', borderRadius: 8, padding: '8px 10px' }}>
                        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                            <div style={{ flex: 1, minWidth: 160 }}>
                                <div style={{ fontSize: 13, fontWeight: 600 }}>{x.name} <span style={{ fontWeight: 400, color: 'var(--text3)', fontSize: 11 }}>· {t('exam.vmCount', { n: x.vm_count ?? '?' })}</span></div>
                                <div style={{ fontSize: 11, color: 'var(--text3)' }}>{fmt(x.starts_at)} → {fmt(x.ends_at)}</div>
                            </div>
                            <span style={{ fontSize: 12, color: color[x.status] }}>{t(`exam.status.${x.status}`)}</span>
                            {x.status === 'scheduled' && <button type="button" style={btn('var(--cyan)', 'var(--cyan)')} onClick={() => act(() => startExamNow(x.id), t('exam.startNowConfirm', { name: x.name }))}>{t('exam.startNow')}</button>}
                            {x.status === 'running' && <button type="button" style={btn('var(--yellow)', 'var(--yellow)')} onClick={() => act(() => endExamNow(x.id), t('exam.endNowConfirm', { name: x.name }))}>{t('exam.endNow')}</button>}
                            {(x.status === 'scheduled' || x.status === 'running') && <button type="button" style={btn()} onClick={() => act(() => cancelExam(x.id), t('exam.cancelConfirm', { name: x.name }))}>{t('exam.cancel')}</button>}
                            {x.status !== 'running' && <button type="button" style={btn('var(--red)', 'var(--red)')} onClick={() => act(() => deleteExam(x.id))}>{t('exam.delete')}</button>}
                        </div>
                        {sr(x.start_result?.reset || x.start_result?.start, 'exam.startResult') && <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 4 }}>{sr(x.start_result.reset || x.start_result.start, 'exam.startResult')}</div>}
                        {sr(x.end_result?.shutdown, 'exam.endResult') && <div style={{ fontSize: 11, color: 'var(--text3)' }}>{sr(x.end_result.shutdown, 'exam.endResult')}</div>}
                    </div>
                ))}
            </div>
            <form onSubmit={create} style={{ borderTop: '1px solid var(--border)', paddingTop: 14 }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', marginBottom: 10 }}>{t('exam.new')}</div>
                <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 10 }}>
                    <label style={{ flex: '1 1 200px', fontSize: 11, color: 'var(--text3)' }}>{t('exam.name')}
                        <input value={form.name} onChange={set('name')} maxLength={100} style={{ ...input, width: '100%', boxSizing: 'border-box', display: 'block' }} /></label>
                    <label style={{ fontSize: 11, color: 'var(--text3)' }}>{t('exam.starts')}
                        <input type="datetime-local" value={form.starts} onChange={set('starts')} style={{ ...input, display: 'block' }} /></label>
                    <label style={{ fontSize: 11, color: 'var(--text3)' }}>{t('exam.ends')}
                        <input type="datetime-local" value={form.ends} onChange={set('ends')} style={{ ...input, display: 'block' }} /></label>
                </div>
                {[['reset_at_start', 'exam.resetAtStart'], ['start_vms', 'exam.startVms'], ['shutdown_at_end', 'exam.shutdownAtEnd'], ['block_open_web', 'exam.blockOpenWeb']].map(([k, label]) => (
                    <label key={k} style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 12, color: 'var(--text2)', marginBottom: 4 }}>
                        <input type="checkbox" checked={form[k]} onChange={set(k)} /> {t(label)}
                    </label>
                ))}
                <button type="submit" disabled={!form.name.trim()} style={{ ...primary, marginTop: 10, opacity: form.name.trim() ? 1 : 0.6 }}>{t('exam.create')}</button>
            </form>
        </div>
    );
}

/* ── Laporan pemakaian ───────────────────────────────────────────────────── */
const today = () => new Date().toISOString().slice(0, 10);
const daysAgo = (n) => new Date(Date.now() - n * 864e5).toISOString().slice(0, 10);

export function UsageReportPanel({ group }) {
    const t = useT();
    const [range, setRange] = useState({ from: daysAgo(29), to: today() });
    const [rep, setRep] = useState(null);
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);
    const show = useCallback(async () => {
        setBusy(true); setError('');
        try { setRep(await fetchUsageReport(group.id, range.from, range.to)); } catch (e) { setError(errText(e)); } finally { setBusy(false); }
    }, [group.id, range.from, range.to]);

    const sum = rep?.summary;
    const card = (label, value) => (
        <div style={{ border: '1px solid var(--border)', borderRadius: 8, padding: '8px 12px', minWidth: 110 }}>
            <div style={{ fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{label}</div>
            <div style={{ fontSize: 18, fontWeight: 700 }}>{value}</div>
        </div>
    );
    return (
        <div>
            <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 12, lineHeight: 1.6 }}>{t('report.hint')}</div>
            <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end', flexWrap: 'wrap', marginBottom: 14 }}>
                <label style={{ fontSize: 11, color: 'var(--text3)' }}>{t('report.from')}
                    <input type="date" value={range.from} onChange={e => setRange(r => ({ ...r, from: e.target.value }))} style={{ ...input, display: 'block' }} /></label>
                <label style={{ fontSize: 11, color: 'var(--text3)' }}>{t('report.to')}
                    <input type="date" value={range.to} onChange={e => setRange(r => ({ ...r, to: e.target.value }))} style={{ ...input, display: 'block' }} /></label>
                <button type="button" style={primary} disabled={busy} onClick={show}>{t('report.show')}</button>
                {rep && <button type="button" onClick={() => downloadCsv(group.id, range.from, range.to, setError)} style={btn('var(--cyan)', 'var(--cyan)')}>{t('report.csv')}</button>}
            </div>
            {error && <div role="alert" style={{ color: 'var(--red)', fontSize: 12, marginBottom: 10 }}>⚠ {error}</div>}
            {rep && !rep.remote_available && <div style={{ color: 'var(--yellow)', fontSize: 12, marginBottom: 10 }}>⚠ {t('report.noRemote')}</div>}
            {rep && (
                <>
                    <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 14 }}>
                        {card(t('report.students'), sum.students)}
                        {card(t('report.participation'), `${sum.participation_pct}%`)}
                        {card(t('report.sessions'), sum.total_sessions)}
                        {card(t('report.totalMinutes'), sum.total_minutes)}
                        {card(t('report.avgMinutes'), sum.avg_minutes_active)}
                    </div>
                    {!rep.students.length ? <div style={{ fontSize: 12, color: 'var(--text3)' }}>{t('report.empty')}</div> : (
                        <div style={{ overflowX: 'auto' }}>
                            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                                <thead><tr style={{ textAlign: 'left', color: 'var(--text3)' }}>
                                    {['student', 'logins', 'remote', 'web', 'ssh', 'total', 'days', 'last', 'vms'].map(c => <th key={c} style={{ padding: '6px 8px', fontWeight: 500 }}>{t(`report.col.${c}`)}</th>)}
                                </tr></thead>
                                <tbody>
                                    {rep.students.map(s => (
                                        <tr key={s.user_id} data-testid="report-row" style={{ borderTop: '1px solid var(--border)', opacity: s.active ? 1 : 0.55 }}>
                                            <td style={{ padding: '6px 8px' }}>{s.full_name || s.username}<div style={{ fontSize: 10, color: 'var(--text3)', fontFamily: 'var(--fmono)' }}>{s.username}</div></td>
                                            <td style={{ padding: '6px 8px' }}>{s.logins}</td>
                                            <td style={{ padding: '6px 8px', whiteSpace: 'nowrap' }}>{s.remote_sessions} / {s.remote_minutes}</td>
                                            <td style={{ padding: '6px 8px', whiteSpace: 'nowrap' }}>{s.web_sessions} / {s.web_minutes}</td>
                                            <td style={{ padding: '6px 8px', whiteSpace: 'nowrap' }}>{s.ssh_sessions} / {s.ssh_minutes}</td>
                                            <td style={{ padding: '6px 8px', fontWeight: 600 }}>{s.total_minutes}</td>
                                            <td style={{ padding: '6px 8px' }}>{s.active_days}</td>
                                            <td style={{ padding: '6px 8px', whiteSpace: 'nowrap' }}>{s.last_active ? fmt(s.last_active) : t('report.never')}</td>
                                            <td style={{ padding: '6px 8px', color: 'var(--text2)' }}>{s.vms.join(', ') || '—'}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                </>
            )}
        </div>
    );
}

async function downloadCsv(groupId, from, to, onError) {
    try {
        const { blob, filename } = await fetchUsageReportCsv(groupId, from, to);
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url; a.download = filename; document.body.appendChild(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) { onError(errText(e)); }
}
