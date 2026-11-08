import { useCallback, useState } from 'react';
import { deleteVmJob, fetchVmJobs, retryVmJob } from '../api';
import { useT } from '../i18n';
import useLoadEffect from '../useLoadEffect';

const ACTIVE = ['pending', 'running'];
const COLOR = { pending: 'var(--text3)', running: 'var(--cyan)', done: 'var(--green)', failed: 'var(--red)', interrupted: 'var(--yellow)' };
const small = (c = 'var(--border)') => ({ padding: '3px 10px', fontSize: 11, borderRadius: 5, cursor: 'pointer', background: 'transparent', border: `1px solid ${c}`, color: 'var(--text2)' });

/** Riwayat proses pembuatan VM di latar belakang (admin). Menyegarkan diri tiap 3 detik selama ada yang berjalan. */
export default function VmJobsPanel({ onClose }) {
    const t = useT();
    const [jobs, setJobs] = useState(null);
    const [error, setError] = useState('');
    const load = useCallback(async () => {
        try { setJobs(await fetchVmJobs()); setError(''); } catch (e) { setError(e?.response?.data?.detail || e.message); }
    }, []);
    useLoadEffect(load, jobs?.some(j => ACTIVE.includes(j.status)) ? 3000 : 0);
    const act = async (fn) => { try { await fn(); await load(); } catch (e) { setError(e?.response?.data?.detail || e.message); } };

    return (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 210 }} onClick={onClose}>
            <div role="dialog" aria-label={t('jobs.title')} onClick={e => e.stopPropagation()}
                style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', borderRadius: 10, width: 720, maxWidth: '94vw', maxHeight: '88vh', overflow: 'auto', padding: 20 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
                    <div style={{ fontSize: 15, fontWeight: 600 }}>{t('jobs.title')}</div>
                    <button type="button" onClick={onClose} aria-label={t('common.close')} style={{ background: 'none', border: 'none', color: 'var(--text3)', cursor: 'pointer', fontSize: 18 }}>✕</button>
                </div>
                {error && <div role="alert" style={{ color: 'var(--red)', fontSize: 12, marginBottom: 10 }}>⚠ {error}</div>}
                {jobs && !jobs.length && <div style={{ fontSize: 12, color: 'var(--text3)' }}>{t('jobs.empty')}</div>}
                {!jobs && <div style={{ fontSize: 12, color: 'var(--text3)' }}>{t('common.loading')}</div>}
                <div style={{ display: 'grid', gap: 8 }}>
                    {(jobs || []).map(j => (
                        <div key={j.id} data-testid="job-row" style={{ border: '1px solid var(--border)', borderRadius: 8, padding: '8px 10px', display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                            <span style={{ fontFamily: 'var(--fmono)', fontSize: 12, color: 'var(--text3)' }}>#{j.id}</span>
                            <div style={{ flex: 1, minWidth: 160 }}>
                                <div style={{ fontSize: 13, fontWeight: 600 }}>{j.name}{j.vmid ? <span style={{ fontWeight: 400, color: 'var(--text3)' }}> · {t('jobs.vmid', { vmid: j.vmid })}</span> : null}</div>
                                <div style={{ fontSize: 11, color: 'var(--text3)' }}>{j.instance}/{j.node} · {j.created_by}</div>
                                {j.status === 'running' && j.step && <div style={{ fontSize: 11, color: 'var(--cyan)' }}>{t(`jobs.step.${j.step}`)}</div>}
                                {j.error && <div style={{ fontSize: 11, color: 'var(--red)', marginTop: 2 }}>{j.error}</div>}
                            </div>
                            <span style={{ fontSize: 12, color: COLOR[j.status] }}>{t(`jobs.status.${j.status}`)}</span>
                            {(j.status === 'failed' || j.status === 'interrupted') && j.can_retry && <button type="button" style={small('var(--cyan)')} onClick={() => act(() => retryVmJob(j.id))}>{t('jobs.retry')}</button>}
                            {!ACTIVE.includes(j.status) && <button type="button" style={small()} onClick={() => act(() => deleteVmJob(j.id))} title={t('jobs.remove')} aria-label={t('jobs.remove')}>✕</button>}
                        </div>
                    ))}
                </div>
            </div>
        </div>
    );
}
