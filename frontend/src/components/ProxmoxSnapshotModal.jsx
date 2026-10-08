import { useState, useEffect, useCallback } from 'react';
import { fetchProxmoxSnapshots, createProxmoxSnapshot, deleteProxmoxSnapshot, rollbackProxmoxSnapshot } from '../api';
import { formatCcdId } from '../format';
import { locale, useT } from '../i18n';

export default function ProxmoxSnapshotModal({ instance, node, vmid, vmName, ccdId = null, canDelete = true, maskHost = false, onClose }) {
    const t = useT();
    const [snapshots, setSnapshots] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [busy, setBusy] = useState(null); // snapname sedang diproses
    const [newName, setNewName] = useState('');
    const [newDesc, setNewDesc] = useState('');
    const [creating, setCreating] = useState(false);

    const load = useCallback(async () => {
        try {
            const list = await fetchProxmoxSnapshots(instance, node, vmid);
            setSnapshots(list);
            setError(null);
        } catch (e) {
            setError(e?.response?.data?.detail || t('snap.loadFailed'));
        } finally {
            setLoading(false);
        }
    }, [instance, node, vmid, t]);

    useEffect(() => { load(); }, [load]);

    const handleCreate = async (e) => {
        e.preventDefault();
        if (!newName.trim()) return;
        setCreating(true);
        setError(null);
        try {
            await createProxmoxSnapshot(instance, node, vmid, newName.trim(), newDesc.trim());
            setNewName(''); setNewDesc('');
            await load();
        } catch (e) {
            setError(e?.response?.data?.detail || t('snap.createFailed'));
        } finally {
            setCreating(false);
        }
    };

    const handleDelete = async (snapname) => {
        if (!confirm(t('snap.deleteConfirm', { name: snapname }))) return;
        setBusy(snapname);
        try {
            await deleteProxmoxSnapshot(instance, node, vmid, snapname);
            await load();
        } catch (e) {
            setError(e?.response?.data?.detail || t('snap.deleteFailed'));
        } finally {
            setBusy(null);
        }
    };

    const handleRollback = async (snapname) => {
        if (!confirm(t('snap.rollbackConfirm', { name: snapname }))) return;
        setBusy(snapname);
        try {
            await rollbackProxmoxSnapshot(instance, node, vmid, snapname);
            await load();
        } catch (e) {
            setError(e?.response?.data?.detail || t('snap.rollbackFailed'));
        } finally {
            setBusy(null);
        }
    };

    return (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 200 }} onClick={onClose}>
            <div style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', borderRadius: 10, width: 560, maxWidth: '92vw', maxHeight: '85vh', overflow: 'auto', padding: 20 }} onClick={e => e.stopPropagation()}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
                    <div>
                        <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text)' }}>{t('snap.title', { name: vmName || vmid })}</div>
                        <div style={{ fontSize: 11, color: 'var(--text3)' }}>{maskHost ? formatCcdId(ccdId) : `Node ${node} · VMID ${vmid} · ${formatCcdId(ccdId)}`}</div>
                    </div>
                    <button onClick={onClose} aria-label={t('common.close')} style={{ background: 'transparent', border: 'none', color: 'var(--text3)', fontSize: 18, cursor: 'pointer' }}>×</button>
                </div>

                {error && (
                    <div style={{ background: 'var(--red-glow)', border: '1px solid var(--red)', borderRadius: 6, padding: '6px 10px', color: 'var(--red)', fontSize: 11, marginBottom: 12 }}>⚠ {error}</div>
                )}

                <form onSubmit={handleCreate} className="ccd-wrap-mobile" style={{ display: 'flex', gap: 6, marginBottom: 16 }}>
                    <input value={newName} onChange={e => setNewName(e.target.value)} placeholder={t('snap.namePh')}
                        style={{ flex: 1, background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 6, padding: '6px 10px', color: 'var(--text)', fontSize: 12 }} />
                    <input value={newDesc} onChange={e => setNewDesc(e.target.value)} placeholder={t('snap.descPh')}
                        style={{ flex: 1, background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 6, padding: '6px 10px', color: 'var(--text)', fontSize: 12 }} />
                    <button type="submit" disabled={creating || !newName.trim()}
                        style={{ padding: '6px 14px', fontSize: 12, borderRadius: 6, cursor: 'pointer', background: 'var(--cyan-glow)', border: '1px solid var(--cyan)', color: 'var(--cyan)', opacity: creating ? 0.5 : 1 }}>
                        {creating ? t('snap.creating') : t('snap.create')}
                    </button>
                </form>

                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    {loading && <div style={{ color: 'var(--text3)', fontSize: 12, padding: 10 }}>{t('common.loading')}</div>}
                    {!loading && snapshots.length === 0 && (
                        <div style={{ color: 'var(--text3)', fontSize: 12, padding: 10 }}>{t('snap.none')}</div>
                    )}
                    {snapshots.map(s => (
                        <div key={s.name} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 6, padding: '8px 10px' }}>
                            <div>
                                <div style={{ fontSize: 12, color: 'var(--text)', fontFamily: 'var(--fmono)' }}>{s.name}</div>
                                {s.description && <div style={{ fontSize: 11, color: 'var(--text3)' }}>{s.description}</div>}
                                {s.snaptime && <div style={{ fontSize: 10, color: 'var(--text3)' }}>{new Date(s.snaptime * 1000).toLocaleString(locale())}</div>}
                            </div>
                            <div style={{ display: 'flex', gap: 6 }}>
                                <button disabled={busy === s.name} onClick={() => handleRollback(s.name)}
                                    style={{ padding: '4px 10px', fontSize: 10, borderRadius: 5, cursor: 'pointer', background: 'transparent', border: '1px solid var(--yellow)', color: 'var(--yellow)', opacity: busy === s.name ? 0.5 : 1 }}>
                                    {busy === s.name ? '…' : t('snap.rollback')}
                                </button>
                                {canDelete && (
                                    <button disabled={busy === s.name} onClick={() => handleDelete(s.name)}
                                        style={{ padding: '4px 10px', fontSize: 10, borderRadius: 5, cursor: 'pointer', background: 'transparent', border: '1px solid var(--red)', color: 'var(--red)', opacity: busy === s.name ? 0.5 : 1 }}>
                                        {busy === s.name ? '…' : t('common.delete')}
                                    </button>
                                )}
                            </div>
                        </div>
                    ))}
                </div>
            </div>
        </div>
    );
}
