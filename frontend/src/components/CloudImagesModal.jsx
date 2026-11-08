import { useCallback, useEffect, useState } from 'react';
import { deleteImageJob, fetchImageCatalog, fetchImageJobs, fetchImageOptions, retryImageJob, startImageJob } from '../api';
import { useT } from '../i18n';
import useLoadEffect from '../useLoadEffect';

const ACTIVE = ['pending', 'running'];
const COLOR = { pending: 'var(--text3)', running: 'var(--cyan)', done: 'var(--green)', failed: 'var(--red)', interrupted: 'var(--yellow)' };
const label = { display: 'block', fontSize: 11, color: 'var(--text3)', marginBottom: 4 };
const input = { width: '100%', boxSizing: 'border-box', background: 'var(--bg-card2)', border: '1px solid var(--border)', borderRadius: 6, padding: '7px 9px', color: 'var(--text)', fontSize: 13 };
const small = (c = 'var(--border)') => ({ padding: '3px 10px', fontSize: 11, borderRadius: 5, cursor: 'pointer', background: 'transparent', border: `1px solid ${c}`, color: 'var(--text2)' });

/** Template otomatis dari cloud image (admin): pilih image + pilihan node, lalu pantau proses di latar belakang. */
export default function CloudImagesModal({ instance, node, onClose, onDone }) {
    const t = useT();
    const [catalog, setCatalog] = useState([]);
    const [opts, setOpts] = useState(null);
    const [form, setForm] = useState({ image_key: '', storage: '', disk_storage: '', pool: '', bridge: 'vmbr0', template_name: '', cores: 2, memory_mb: 2048, disk_gb: 10 });
    const [jobs, setJobs] = useState([]);
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);
    const set = (k) => (e) => setForm(f => ({ ...f, [k]: e.target.value }));

    useEffect(() => {
        fetchImageCatalog().then(c => { setCatalog(c); setForm(f => ({ ...f, image_key: f.image_key || c[0]?.key || '' })); }).catch(e => setError(e?.response?.data?.detail || e.message));
        fetchImageOptions(instance, node).then(o => {
            setOpts(o);
            setForm(f => ({
                ...f, storage: f.storage || o.import_storages[0]?.storage || '', disk_storage: f.disk_storage || o.disk_storages[0]?.storage || '',
                pool: f.pool || o.pools[0] || '', bridge: o.bridges.includes(f.bridge) ? f.bridge : (o.bridges[0] || f.bridge),
            }));
        }).catch(e => setError(e?.response?.data?.detail || e.message));
    }, [instance, node]);

    const image = catalog.find(c => c.key === form.image_key);
    const loadJobs = useCallback(async () => {
        try { setJobs((await fetchImageJobs()).filter(j => j.instance === instance && j.node === node)); } catch { /* daftar proses opsional */ }
    }, [instance, node]);
    useLoadEffect(loadJobs, jobs.some(j => ACTIVE.includes(j.status)) ? 3000 : 0);

    const finishedIds = jobs.filter(j => j.status === 'done').map(j => j.id).join(',');
    useEffect(() => { if (finishedIds) onDone?.(); }, [finishedIds]);   // eslint-disable-line react-hooks/exhaustive-deps

    const problem = !opts ? null
        : !opts.version_ok ? t('images.versionOld', { version: opts.version })
        : !opts.import_storages.length ? t('images.noImportStorage')
        : !opts.node_network ? t('images.noNetwork') : null;

    const start = async (e) => {
        e.preventDefault();
        setBusy(true); setError('');
        try {
            await startImageJob({ instance, node, ...form, template_name: form.template_name || null, disk_gb: Number(form.disk_gb) || null,
                cores: Number(form.cores) || null, memory_mb: Number(form.memory_mb) || null });
            await loadJobs();
        } catch (err) { setError(err?.response?.data?.detail || err.message); } finally { setBusy(false); }
    };
    const act = async (fn) => { try { await fn(); await loadJobs(); } catch (e) { setError(e?.response?.data?.detail || e.message); } };

    return (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 210 }} onClick={onClose}>
            <div role="dialog" aria-label={t('images.title')} onClick={e => e.stopPropagation()}
                style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', borderRadius: 10, width: 640, maxWidth: '94vw', maxHeight: '90vh', overflow: 'auto', padding: 20 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                    <div style={{ fontSize: 15, fontWeight: 600 }}>{t('images.title')}</div>
                    <button type="button" onClick={onClose} aria-label={t('common.close')} style={{ background: 'none', border: 'none', color: 'var(--text3)', cursor: 'pointer', fontSize: 18 }}>✕</button>
                </div>
                <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 6, lineHeight: 1.5 }}>{t('images.hint')}</div>
                <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 12, fontFamily: 'var(--fmono)' }}>{instance} / {node}</div>
                {error && <div role="alert" style={{ color: 'var(--red)', fontSize: 12, marginBottom: 10 }}>⚠ {error}</div>}
                {problem && <div role="alert" style={{ color: 'var(--yellow)', fontSize: 12, marginBottom: 10 }}>⚠ {problem}</div>}
                {!opts && !error && <div style={{ fontSize: 12, color: 'var(--text3)' }}>{t('images.loadingOptions')}</div>}

                {opts && (
                    <form onSubmit={start}>
                        <label style={label} htmlFor="img-key">{t('images.image')}</label>
                        <select id="img-key" value={form.image_key} onChange={set('image_key')} style={{ ...input, marginBottom: 4 }}>
                            {catalog.map(c => <option key={c.key} value={c.key}>{c.name}</option>)}
                        </select>
                        {image && <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 10 }}>
                            {image.agent_included ? t('images.agentYes') : t('images.agentNo')}{image.default_user ? ` · ${t('images.defaultUser', { user: image.default_user })}` : ''}
                        </div>}
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(180px,1fr))', gap: 10, marginBottom: 12 }}>
                            <div><label style={label} htmlFor="img-storage">{t('images.storage')}</label>
                                <select id="img-storage" value={form.storage} onChange={set('storage')} style={input}>{opts.import_storages.map(s => <option key={s.storage} value={s.storage}>{s.storage} ({s.avail_gb} GB)</option>)}</select></div>
                            <div><label style={label} htmlFor="img-disk">{t('images.diskStorage')}</label>
                                <select id="img-disk" value={form.disk_storage} onChange={set('disk_storage')} style={input}>{opts.disk_storages.map(s => <option key={s.storage} value={s.storage}>{s.storage} ({s.avail_gb} GB)</option>)}</select></div>
                            <div><label style={label} htmlFor="img-pool">{t('images.pool')}</label>
                                <select id="img-pool" value={form.pool} onChange={set('pool')} style={input}>{opts.pools.map(p => <option key={p} value={p}>{p}</option>)}</select></div>
                            <div><label style={label} htmlFor="img-bridge">{t('images.bridge')}</label>
                                <select id="img-bridge" value={form.bridge} onChange={set('bridge')} style={input}>{opts.bridges.map(b => <option key={b} value={b}>{b}</option>)}</select></div>
                            <div><label style={label} htmlFor="img-name">{t('images.templateName')}</label>
                                <input id="img-name" value={form.template_name} onChange={set('template_name')} placeholder={image ? `tpl-${image.key}` : ''} style={input} /></div>
                            <div><label style={label} htmlFor="img-cores">{t('images.cores')}</label><input id="img-cores" type="number" min="1" value={form.cores} onChange={set('cores')} style={input} /></div>
                            <div><label style={label} htmlFor="img-mem">{t('images.memory')}</label><input id="img-mem" type="number" min="256" value={form.memory_mb} onChange={set('memory_mb')} style={input} /></div>
                            <div><label style={label} htmlFor="img-dsk">{t('images.disk')}</label><input id="img-dsk" type="number" min={image?.min_disk_gb || 1} value={form.disk_gb} onChange={set('disk_gb')} style={input} /></div>
                        </div>
                        <button type="submit" disabled={busy || !!problem || !form.storage || !form.pool}
                            style={{ padding: '8px 18px', borderRadius: 6, fontSize: 12, fontWeight: 600, background: 'var(--cyan)', color: '#000', border: 'none', cursor: 'pointer', opacity: busy || problem ? 0.6 : 1 }}>
                            {busy ? t('images.starting') : t('images.start')}
                        </button>
                    </form>
                )}

                <div style={{ borderTop: '1px solid var(--border)', marginTop: 18, paddingTop: 12 }}>
                    <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', marginBottom: 8 }}>{t('images.jobs')}</div>
                    {!jobs.length && <div style={{ fontSize: 12, color: 'var(--text3)' }}>{t('images.noJobs')}</div>}
                    <div style={{ display: 'grid', gap: 8 }}>
                        {jobs.map(j => (
                            <div key={j.id} data-testid="image-job" style={{ border: '1px solid var(--border)', borderRadius: 8, padding: '8px 10px', display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                                <span style={{ fontFamily: 'var(--fmono)', fontSize: 12, color: 'var(--text3)' }}>#{j.id}</span>
                                <div style={{ flex: 1, minWidth: 160 }}>
                                    <div style={{ fontSize: 13, fontWeight: 600 }}>{j.image_name}</div>
                                    {j.status === 'running' && j.step && <div style={{ fontSize: 11, color: 'var(--cyan)' }}>{t(`images.step.${j.step}`)}{j.result?.progress ? ` · ${j.result.progress}` : ''}</div>}
                                    {j.status === 'done' && j.result?.template_vmid && <div style={{ fontSize: 11, color: 'var(--green)' }}>{t('images.templateReady', { vmid: j.result.template_vmid })}</div>}
                                    {j.error && <div style={{ fontSize: 11, color: 'var(--red)' }}>{j.error}</div>}
                                    {(j.result?.warnings || []).map(w => <div key={w} style={{ fontSize: 11, color: 'var(--yellow)' }}>{w}</div>)}
                                </div>
                                <span style={{ fontSize: 12, color: COLOR[j.status] }}>{t(`images.status.${j.status}`)}</span>
                                {(j.status === 'failed' || j.status === 'interrupted') && <button type="button" style={small('var(--cyan)')} onClick={() => act(() => retryImageJob(j.id))}>{t('images.retry')}</button>}
                                {!ACTIVE.includes(j.status) && <button type="button" style={small()} aria-label={t('images.remove')} title={t('images.remove')} onClick={() => act(() => deleteImageJob(j.id))}>✕</button>}
                            </div>
                        ))}
                    </div>
                </div>
            </div>
        </div>
    );
}
