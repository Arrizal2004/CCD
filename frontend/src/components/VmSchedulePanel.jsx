import { useCallback, useState } from 'react';
import { createVmSchedule, deleteVmSchedule, fetchVmSchedules, updateVmSchedule } from '../api';
import { locale, useT } from '../i18n';
import useLoadEffect from '../useLoadEffect';

// Jadwal nyala, mati, dan snapshot otomatis untuk satu VM (admin). Jam mengikuti zona waktu di Pengaturan Sistem.
const DAYS = [1, 2, 3, 4, 5, 6, 7];
const WEEKDAYS = [1, 2, 3, 4, 5];
const label = { display: 'block', fontSize: 12, color: 'var(--text2)', fontWeight: 500, marginBottom: 5 };
const field = { background: 'var(--bg-card2)', border: '1px solid var(--border)', borderRadius: 6, padding: '6px 8px', color: 'var(--text)', fontSize: 13 };
const small = (danger) => ({ padding: '3px 10px', fontSize: 11, borderRadius: 5, cursor: 'pointer', background: 'transparent', border: `1px solid ${danger ? 'var(--red)' : 'var(--border)'}`, color: danger ? 'var(--red)' : 'var(--text2)' });

function fmtTime(iso) {
    try { return new Date(iso).toLocaleString(locale(), { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }); }
    catch { return iso; }
}

export default function VmSchedulePanel({ instance, node, vmid }) {
    const t = useT();
    const [data, setData] = useState(null);
    const [form, setForm] = useState({ action: 'start', days: WEEKDAYS, time: '07:00', keep: 3 });
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);

    const load = useCallback(async () => {
        try { setData(await fetchVmSchedules(instance, node, vmid)); setError(''); }
        catch (e) { setError(e?.response?.data?.detail || e.message); }
    }, [instance, node, vmid]);
    useLoadEffect(load);

    const run = async (fn) => {
        setBusy(true); setError('');
        try { await fn(); await load(); } catch (e) { setError(e?.response?.data?.detail || e.message); } finally { setBusy(false); }
    };
    const toggleDay = (d) => setForm(f => ({ ...f, days: f.days.includes(d) ? f.days.filter(x => x !== d) : [...f.days, d].sort() }));
    const dayName = (d) => t(`sched.day${d}`);
    const daysText = (days) => (days.length === 7 ? t('sched.everyDay')
        : days.join(',') === WEEKDAYS.join(',') ? t('sched.weekdays') : days.map(dayName).join(', '));

    return (
        <div>
            <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 12, lineHeight: 1.6 }}>
                {t('sched.hint', { tz: data?.timezone || '' })}
            </div>
            {error && <div role="alert" style={{ fontSize: 12, color: 'var(--red)', marginBottom: 10 }}>⚠ {error}</div>}

            {data?.schedules?.length ? (
                <div style={{ display: 'grid', gap: 8, marginBottom: 16 }}>
                    {data.schedules.map(s => (
                        <div key={s.id} data-testid="schedule-row" style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 10px', border: '1px solid var(--border)', borderRadius: 6, opacity: s.enabled ? 1 : 0.55, flexWrap: 'wrap' }}>
                            <span style={{ fontFamily: 'var(--fmono)', fontSize: 14, fontWeight: 600 }}>{s.time}</span>
                            <span style={{ fontSize: 12, padding: '1px 8px', borderRadius: 10, background: 'var(--bg-hover)', color: s.action === 'start' ? 'var(--green)' : s.action === 'snapshot' ? 'var(--cyan)' : 'var(--yellow)' }}>
                                {s.action === 'snapshot' ? t('sched.snapshotKeep', { n: s.keep }) : t(s.action === 'start' ? 'sched.start' : 'sched.shutdown')}
                            </span>
                            <span style={{ fontSize: 12, color: 'var(--text2)', flex: 1, minWidth: 120 }}>
                                {daysText(s.days)}
                                {s.last_run_at && <span style={{ display: 'block', fontSize: 11, color: 'var(--text3)' }}>{t('sched.lastRun', { at: fmtTime(s.last_run_at), result: s.last_result_text })}</span>}
                            </span>
                            <label style={{ fontSize: 12, display: 'flex', gap: 5, alignItems: 'center' }}>
                                <input type="checkbox" checked={s.enabled} disabled={busy}
                                    onChange={e => run(() => updateVmSchedule(instance, node, vmid, s.id, { action: s.action, days: s.days, time: s.time, enabled: e.target.checked, note: s.note, keep: s.keep }))} />
                                {t('sched.enabled')}
                            </label>
                            <button type="button" disabled={busy} style={small(true)} onClick={() => run(() => deleteVmSchedule(instance, node, vmid, s.id))}>{t('common.delete')}</button>
                        </div>
                    ))}
                </div>
            ) : <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 16 }}>{data ? t('sched.empty') : t('common.loading')}</div>}

            <div style={{ borderTop: '1px solid var(--border)', paddingTop: 14 }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', marginBottom: 10 }}>{t('sched.add')}</div>
                <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end', marginBottom: 10 }}>
                    <label><span style={label}>{t('sched.action')}</span>
                        <select value={form.action} onChange={e => setForm(f => ({ ...f, action: e.target.value }))} style={field}>
                            <option value="start">{t('sched.start')}</option>
                            <option value="shutdown">{t('sched.shutdown')}</option>
                            <option value="snapshot">{t('sched.snapshot')}</option>
                        </select>
                    </label>
                    {form.action === 'snapshot' && (
                        <label><span style={label}>{t('sched.keep')}</span>
                            <input type="number" min="1" max="30" value={form.keep} style={{ ...field, width: 70 }}
                                onChange={e => setForm(f => ({ ...f, keep: Number(e.target.value) || 1 }))} />
                        </label>
                    )}
                    <label><span style={label}>{t('sched.time')}</span>
                        <input type="time" value={form.time} style={field} onChange={e => setForm(f => ({ ...f, time: e.target.value }))} />
                    </label>
                </div>
                <div style={label}>{t('sched.days')}</div>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 8 }}>
                    {DAYS.map(d => (
                        <button key={d} type="button" aria-pressed={form.days.includes(d)} onClick={() => toggleDay(d)}
                            style={{ minWidth: 44, height: 30, borderRadius: 6, fontSize: 12, cursor: 'pointer',
                                     border: `1px solid ${form.days.includes(d) ? 'var(--cyan)' : 'var(--border-light)'}`,
                                     background: form.days.includes(d) ? 'var(--cyan-glow)' : 'var(--bg-card)', color: form.days.includes(d) ? 'var(--cyan)' : 'var(--text2)' }}>
                            {dayName(d)}
                        </button>
                    ))}
                </div>
                <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
                    <button type="button" style={small(false)} onClick={() => setForm(f => ({ ...f, days: WEEKDAYS }))}>{t('sched.weekdays')}</button>
                    <button type="button" style={small(false)} onClick={() => setForm(f => ({ ...f, days: DAYS }))}>{t('sched.everyDay')}</button>
                </div>
                <button type="button" disabled={busy || !form.days.length || !form.time}
                    onClick={() => run(() => createVmSchedule(instance, node, vmid, form))}
                    style={{ padding: '7px 16px', borderRadius: 6, fontSize: 12, fontWeight: 600, background: 'var(--cyan)', color: '#000', border: 'none', cursor: 'pointer' }}>
                    {t('sched.save')}
                </button>
            </div>
        </div>
    );
}
