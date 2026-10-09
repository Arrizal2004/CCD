import { useEffect, useState } from 'react';
import { fetchProxmoxInstances, fetchUserInstances, saveUserInstances } from '../api';
import { useT } from '../i18n';

// Superadmin memilih Proxmox yang boleh dikelola seorang sysadmin. Tanpa pilihan, sysadmin tidak melihat Proxmox apa pun.
export default function UserInstancesModal({ user, onClose, onSaved }) {
    const t = useT();
    const [all, setAll] = useState(null);
    const [picked, setPicked] = useState(new Set());
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');

    useEffect(() => {
        let alive = true;
        Promise.all([fetchProxmoxInstances(), fetchUserInstances(user.id)])
            .then(([inst, mine]) => { if (alive) { setAll(inst.map(i => i.label)); setPicked(new Set(mine.instances)); } })
            .catch(e => { if (alive) setError(e?.response?.data?.detail || t('uinst.loadFailed')); });
        return () => { alive = false; };
    }, [user.id, t]);

    const toggle = (label) => setPicked(prev => { const n = new Set(prev); if (n.has(label)) n.delete(label); else n.add(label); return n; });

    const save = async () => {
        setSaving(true); setError('');
        try { await saveUserInstances(user.id, [...picked]); onSaved?.(); onClose(); }
        catch (e) { setError(e?.response?.data?.detail || t('uinst.saveFailed')); setSaving(false); }
    };

    return (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.75)', zIndex: 1001, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 12 }}>
            <div role="dialog" aria-label={t('uinst.title', { user: user.username })}
                style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 12, padding: 22, width: 'min(420px, 100%)', maxHeight: '90vh', overflowY: 'auto' }}>
                <div style={{ fontWeight: 600, color: 'var(--cyan)', marginBottom: 6 }}>{t('uinst.title', { user: user.username })}</div>
                <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 14, lineHeight: 1.5 }}>{t('uinst.hint')}</div>
                {all === null && !error && <div style={{ fontSize: 12, color: 'var(--text3)' }}>{t('common.loading')}</div>}
                {all && all.length === 0 && <div style={{ fontSize: 12, color: 'var(--text3)' }}>{t('uinst.none')}</div>}
                {all && all.map(label => (
                    <label key={label} style={{ display: 'flex', gap: 8, alignItems: 'center', padding: '7px 4px', fontSize: 13, fontFamily: 'var(--fmono)', color: 'var(--text)', cursor: 'pointer' }}>
                        <input type="checkbox" checked={picked.has(label)} onChange={() => toggle(label)} />
                        {label}
                    </label>
                ))}
                {all && all.length > 0 && picked.size === 0 && (
                    <div style={{ fontSize: 11, color: 'var(--yellow)', marginTop: 8 }}>{t('uinst.emptyWarn')}</div>
                )}
                {error && <div style={{ fontSize: 12, color: 'var(--red)', marginTop: 10 }}>{error}</div>}
                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 18 }}>
                    <button onClick={onClose} style={{ padding: '6px 14px', borderRadius: 6, background: 'var(--bg-hover)', border: '1px solid var(--border)', color: 'var(--text2)', fontSize: 12, cursor: 'pointer' }}>{t('common.cancel')}</button>
                    <button onClick={save} disabled={saving || all === null}
                        style={{ padding: '6px 14px', borderRadius: 6, background: 'var(--cyan)', color: '#000', fontSize: 12, fontWeight: 600, border: 'none', cursor: 'pointer', opacity: saving ? 0.6 : 1 }}>
                        {saving ? t('common.saving') : t('common.save')}
                    </button>
                </div>
            </div>
        </div>
    );
}
