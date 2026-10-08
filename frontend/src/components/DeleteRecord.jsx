import { useState } from 'react';
import { useT } from '../i18n';
import { btn } from '../adminFormat';
import { Modal } from './AdminWidgets';

// Tombol Hapus dengan konfirmasi untuk tiket Helpdesk dan Infra Request (superadmin). Yang tersisa di
// Audit Trail hanya ringkasannya; isi percakapan tidak disalin, jadi konfirmasinya menyebut itu.
export default function DeleteRecord({ title, summary, onDelete, onDeleted, style }) {
    const t = useT();
    const [open, setOpen] = useState(false);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');

    const confirm = async () => {
        setBusy(true); setError('');
        try { await onDelete(); setOpen(false); onDeleted?.(); }
        catch (e) { setError(e?.response?.data?.detail || e.message); }
        finally { setBusy(false); }
    };

    return (
        <>
            <button onClick={() => setOpen(true)} title={t('del.button')}
                style={{ ...btn, padding: '4px 10px', fontSize: 11, color: 'var(--red)', borderColor: 'var(--red)66', whiteSpace: 'nowrap', ...style }}>
                {t('del.button')}
            </button>
            {open && (
                <Modal label={title} onClose={busy ? () => {} : () => setOpen(false)} width={480}>
                    <div style={{ padding: '16px 18px', borderBottom: '1px solid var(--border)', fontSize: 15, fontWeight: 600, color: 'var(--text)' }}>{title}</div>
                    <div style={{ padding: 18 }}>
                        <div style={{ fontSize: 12, color: 'var(--text2)', fontFamily: 'var(--fmono)', overflowWrap: 'anywhere', marginBottom: 12 }}>{summary}</div>
                        <div style={{ fontSize: 12, color: 'var(--text2)', lineHeight: 1.6 }}>{t('del.warn')}</div>
                        {error && <div role="alert" style={{ marginTop: 12, fontSize: 12, color: 'var(--red)' }}>⚠ {error}</div>}
                        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 16 }}>
                            <button onClick={() => setOpen(false)} disabled={busy} style={btn}>{t('common.cancel')}</button>
                            <button onClick={confirm} disabled={busy}
                                style={{ ...btn, background: 'var(--red)', color: '#fff', borderColor: 'var(--red)', opacity: busy ? 0.6 : 1 }}>
                                {busy ? '…' : t('del.confirm')}
                            </button>
                        </div>
                    </div>
                </Modal>
            )}
        </>
    );
}
