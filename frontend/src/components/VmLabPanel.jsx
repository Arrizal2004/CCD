import { useState } from 'react';
import { removeVmBaseline, resetVmLab, setVmBaseline } from '../api';
import { useT } from '../i18n';

const btn = (c) => ({ padding: '5px 14px', fontSize: 11, borderRadius: 5, cursor: 'pointer', background: 'transparent', border: `1px solid ${c}`, color: c });

/** Kondisi awal lab satu VM (admin): jadikan baseline, hapus, atau reset sekarang. */
export default function VmLabPanel({ instance, node, vmid, vmName, running }) {
    const t = useT();
    const [msg, setMsg] = useState(null);
    const [busy, setBusy] = useState(false);
    const run = async (fn, ok, confirmText) => {
        if (confirmText && !window.confirm(confirmText)) return;
        setBusy(true); setMsg(null);
        try { await fn(); setMsg({ ok: true, text: ok }); } catch (e) { setMsg({ ok: false, text: e?.response?.data?.detail || e.message }); } finally { setBusy(false); }
    };
    return (
        <div data-testid="vm-lab" style={{ marginTop: 18, border: '1px solid var(--border)', borderRadius: 8, padding: '10px 12px' }}>
            <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6 }}>{t('lab.vmTitle')}</div>
            <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 8, lineHeight: 1.5 }}>{t('lab.vmHint')}</div>
            {msg && <div role={msg.ok ? 'status' : 'alert'} style={{ fontSize: 11, marginBottom: 8, color: msg.ok ? 'var(--green)' : 'var(--red)' }}>{msg.ok ? '✓' : '⚠'} {msg.text}</div>}
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                <button type="button" disabled={busy || running} title={running ? t('lab.vmHint') : undefined} style={{ ...btn('var(--cyan)'), opacity: running ? 0.5 : 1 }}
                    onClick={() => run(() => setVmBaseline(instance, node, vmid), t('lab.baselineSet'))}>{t('lab.setBaseline')}</button>
                <button type="button" disabled={busy} style={btn('var(--yellow)')}
                    onClick={() => run(() => resetVmLab(instance, node, vmid), t('lab.resetDone'), t('lab.resetVmConfirm', { name: vmName }))}>{t('lab.resetNow')}</button>
                <button type="button" disabled={busy} style={btn('var(--text3)')}
                    onClick={() => run(() => removeVmBaseline(instance, node, vmid), t('lab.baselineRemoved'))}>{t('lab.removeBaseline')}</button>
            </div>
        </div>
    );
}
