import { useState } from 'react';
import { deleteProxmoxVm } from '../api';
import { useT } from '../i18n';

const btn = (filled) => ({
    padding: '5px 14px', fontSize: 11, borderRadius: 5, cursor: 'pointer', fontWeight: filled ? 600 : 400,
    background: filled ? 'var(--red)' : 'transparent', color: filled ? '#fff' : 'var(--red)', border: '1px solid var(--red)',
});

export default function VmDeletePanel({ instance, node, vmid, vmName, onDeleted }) {
    const t = useT();
    const [open, setOpen] = useState(false);
    const [confirmName, setConfirmName] = useState('');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState(null);

    const doDelete = async () => {
        setBusy(true);
        setError(null);
        try {
            onDeleted?.(await deleteProxmoxVm(instance, node, vmid, confirmName));
        } catch (e) {
            setError(e?.response?.data?.detail || t('del.failed'));
            setBusy(false);
        }
    };

    return (
        <div style={{ marginTop: 18, border: '1px solid var(--red)', borderRadius: 8, padding: '10px 12px', background: 'var(--red-glow)' }}>
            <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--red)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 8 }}>{t('del.title')}</div>
            {error && <div style={{ fontSize: 11, color: 'var(--red)', marginBottom: 8 }}>⚠ {error}</div>}
            {!open ? (
                <button onClick={() => setOpen(true)} style={btn(false)}>{t('del.open')}</button>
            ) : (
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
                    <input value={confirmName} onChange={e => setConfirmName(e.target.value)} autoFocus disabled={busy}
                        placeholder={t('del.confirmPh', { name: vmName })}
                        style={{ flex: 1, minWidth: 200, boxSizing: 'border-box', background: 'var(--bg-card)', border: '1px solid var(--red)', borderRadius: 6, padding: '5px 9px', color: 'var(--text)', fontSize: 12 }} />
                    <button onClick={doDelete} disabled={busy || confirmName !== vmName}
                        style={{ ...btn(true), opacity: busy || confirmName !== vmName ? 0.5 : 1 }}>
                        {busy ? t('common.deleting') : t('del.submit')}
                    </button>
                    <button onClick={() => { setOpen(false); setConfirmName(''); }} disabled={busy}
                        style={{ padding: '5px 12px', fontSize: 11, borderRadius: 5, cursor: 'pointer', background: 'transparent', border: '1px solid var(--border)', color: 'var(--text3)' }}>
                        {t('common.cancel')}
                    </button>
                </div>
            )}
        </div>
    );
}
