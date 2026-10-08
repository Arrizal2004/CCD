import { useState, useEffect } from 'react';
import { fetchProxmoxVmResources, updateProxmoxVmResources } from '../api';
import { useT } from '../i18n';

const field = { width: '100%', boxSizing: 'border-box', background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 6, padding: '6px 10px', color: 'var(--text)', fontSize: 12 };
const label = { fontSize: 11, color: 'var(--text3)', marginBottom: 4 };

export default function ProxmoxResizeModal({ instance, node, vmid, vmName, onClose, onSaved }) {
    const t = useT();
    const [res, setRes] = useState(null);
    const [memory, setMemory] = useState('');
    const [cores, setCores] = useState('');
    const [diskKey, setDiskKey] = useState('');
    const [diskSize, setDiskSize] = useState('');
    const [error, setError] = useState(null);
    const [saving, setSaving] = useState(false);
    const [done, setDone] = useState(null);

    useEffect(() => {
        fetchProxmoxVmResources(instance, node, vmid)
            .then(r => {
                setRes(r);
                setMemory(String(r.memory_mb));
                setCores(String(r.cores));
                if (r.disks.length) { setDiskKey(r.disks[0].key); setDiskSize(String(Math.ceil(r.disks[0].size_gb))); }
            })
            .catch(e => setError(e?.response?.data?.detail || t('resize.loadFailed')));
    }, [instance, node, vmid, t]);

    const disk = res?.disks.find(d => d.key === diskKey);
    const stopped = res?.status === 'stopped';
    const diskShrink = disk && diskSize !== '' && Number(diskSize) < disk.size_gb;

    const body = {};
    if (res) {
        if (Number(memory) !== res.memory_mb) body.memory_mb = Number(memory);
        if (Number(cores) !== res.cores) body.cores = Number(cores);
        if (disk && Number(diskSize) > disk.size_gb) { body.disk_key = diskKey; body.disk_size_gb = Number(diskSize); }
    }
    const changed = Object.keys(body).length > 0;

    const save = async (e) => {
        e.preventDefault();
        if (!changed || !stopped || diskShrink) return;
        setSaving(true); setError(null);
        try {
            const r = await updateProxmoxVmResources(instance, node, vmid, body);
            setDone(r.changes);
            onSaved?.();
        } catch (err) {
            setError(err?.response?.data?.detail || t('resize.saveFailed'));
        } finally {
            setSaving(false);
        }
    };

    return (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 200 }} onClick={onClose}>
            <div style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', borderRadius: 10, width: 440, maxWidth: '92vw', maxHeight: '85vh', overflow: 'auto', padding: 20 }} onClick={e => e.stopPropagation()}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
                    <div>
                        <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text)' }}>{t('resize.title', { name: vmName || vmid })}</div>
                        <div style={{ fontSize: 11, color: 'var(--text3)' }}>Node {node} · VMID {vmid}{res ? ` · ${res.status}` : ''}</div>
                    </div>
                    <button onClick={onClose} aria-label={t('common.close')} style={{ background: 'transparent', border: 'none', color: 'var(--text3)', fontSize: 18, cursor: 'pointer' }}>×</button>
                </div>

                {error && (
                    <div style={{ background: 'var(--red-glow)', border: '1px solid var(--red)', borderRadius: 6, padding: '6px 10px', color: 'var(--red)', fontSize: 11, marginBottom: 12 }}>⚠ {error}</div>
                )}
                {res && !stopped && (
                    <div style={{ border: '1px solid var(--yellow)', borderRadius: 6, padding: '6px 10px', color: 'var(--yellow)', fontSize: 11, marginBottom: 12 }}>
                        {t('resize.mustStop', { status: res.status })}
                    </div>
                )}
                {done && (
                    <div style={{ border: '1px solid var(--green)', borderRadius: 6, padding: '6px 10px', color: 'var(--green)', fontSize: 11, marginBottom: 12 }}>
                        {t('resize.done', { changes: done.join(', ') })}
                    </div>
                )}

                {res && !done && (
                    <form onSubmit={save} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                        <div>
                            <div style={label}>{t('resize.ram', { n: res.memory_mb })}</div>
                            <input type="number" min={256} step={256} value={memory} onChange={e => setMemory(e.target.value)} disabled={!stopped} style={field} />
                        </div>
                        <div>
                            <div style={label}>{t('resize.cpu', { sockets: res.sockets > 1 ? t('resize.sockets', { n: res.sockets }) : '', n: res.cores })}</div>
                            <input type="number" min={1} step={1} value={cores} onChange={e => setCores(e.target.value)} disabled={!stopped} style={field} />
                        </div>
                        <div>
                            <div style={label}>{t('resize.storage')}</div>
                            {res.disks.length === 0 ? (
                                <div style={{ fontSize: 11, color: 'var(--text3)' }}>{t('resize.noDisks')}</div>
                            ) : (
                                <div style={{ display: 'flex', gap: 8 }}>
                                    <select value={diskKey} onChange={e => { setDiskKey(e.target.value); const d = res.disks.find(x => x.key === e.target.value); if (d) setDiskSize(String(Math.ceil(d.size_gb))); }}
                                        disabled={!stopped} style={{ ...field, width: 140 }}>
                                        {res.disks.map(d => <option key={d.key} value={d.key}>{d.key} ({d.size_gb} GB)</option>)}
                                    </select>
                                    <input type="number" min={disk ? Math.ceil(disk.size_gb) : 1} step={1} value={diskSize} onChange={e => setDiskSize(e.target.value)} disabled={!stopped} style={field} />
                                </div>
                            )}
                            {diskShrink && <div style={{ fontSize: 11, color: 'var(--red)', marginTop: 4 }}>{t('resize.noShrink', { n: disk.size_gb })}</div>}
                        </div>
                        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 4 }}>
                            <button type="button" onClick={onClose} style={{ padding: '6px 14px', fontSize: 12, borderRadius: 6, cursor: 'pointer', background: 'transparent', border: '1px solid var(--border)', color: 'var(--text2)' }}>{t('common.cancel')}</button>
                            <button type="submit" disabled={saving || !stopped || !changed || diskShrink}
                                style={{ padding: '6px 14px', fontSize: 12, borderRadius: 6, cursor: 'pointer', background: 'var(--cyan-glow)', border: '1px solid var(--cyan)', color: 'var(--cyan)', opacity: (saving || !stopped || !changed || diskShrink) ? 0.5 : 1 }}>
                                {saving ? t('common.saving') : t('common.save')}
                            </button>
                        </div>
                    </form>
                )}
                {!res && !error && <div style={{ color: 'var(--text3)', fontSize: 12 }}>{t('common.loading')}</div>}
                {done && (
                    <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 10 }}>
                        <button onClick={onClose} style={{ padding: '6px 14px', fontSize: 12, borderRadius: 6, cursor: 'pointer', background: 'transparent', border: '1px solid var(--border)', color: 'var(--text2)' }}>{t('common.close')}</button>
                    </div>
                )}
            </div>
        </div>
    );
}
