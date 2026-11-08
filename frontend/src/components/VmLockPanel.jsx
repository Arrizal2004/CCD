import { useState } from 'react';
import { setVmLock } from '../api';
import { useT } from '../i18n';

// Kunci VM (admin): memakai Protection Proxmox, jadi VM tidak bisa dihapus, juga dari antarmuka Proxmox.
export default function VmLockPanel({ instance, node, vmid, vmName, locked, onChanged }) {
    const t = useT();
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');
    const flip = async () => {
        if (locked && !window.confirm(t('lock.unlockConfirm', { name: vmName }))) return;
        setBusy(true); setError('');
        try { const r = await setVmLock(instance, node, vmid, !locked); onChanged?.(r.locked); }
        catch (e) { setError(e?.response?.data?.detail || t('lock.failed')); }
        finally { setBusy(false); }
    };
    return (
        <div data-testid="vm-lock" style={{ marginTop: 18, border: '1px solid var(--border)', borderRadius: 8, padding: '10px 12px' }}>
            <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6 }}>{t('lock.lock')}</div>
            <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 8, lineHeight: 1.5 }}>{locked ? t('lock.lockedNotice') : t('lock.lockHint')}</div>
            {error && <div role="alert" style={{ fontSize: 11, color: 'var(--red)', marginBottom: 8 }}>⚠ {error}</div>}
            <button type="button" disabled={busy} onClick={flip}
                style={{ padding: '5px 14px', fontSize: 11, borderRadius: 5, cursor: busy ? 'wait' : 'pointer', background: 'transparent',
                         border: `1px solid ${locked ? 'var(--yellow)' : 'var(--cyan)'}`, color: locked ? 'var(--yellow)' : 'var(--cyan)' }}>
                {locked ? t('lock.unlock') : t('lock.lock')}
            </button>
        </div>
    );
}
