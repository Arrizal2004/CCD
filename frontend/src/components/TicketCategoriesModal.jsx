import { useState } from 'react';
import { saveTicketCategories } from '../api';
import { useT } from '../i18n';
import { loadSysConfig, useSysConfig } from '../sysconfig';
import { btn, inp } from '../adminFormat';
import { Modal } from './AdminWidgets';

const REQUIRED = ['LEASE_EXTENSION', 'OTHERS'];     // dipakai alur "Minta perpanjangan" dan cadangan
const MAX = 20;

// Kelola kategori tiket Helpdesk (superadmin). Dibuka dari halaman Helpdesk.
export default function TicketCategoriesModal({ onClose }) {
    const t = useT();
    const current = useSysConfig().ticket_categories;
    const [rows, setRows] = useState(() => current.map(c => ({ ...c, _id: c.key })));
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');

    const setLabel = (i, label) => setRows(r => r.map((c, j) => (j === i ? { ...c, label } : c)));
    const add = () => setRows(r => [...r, { key: '', label: '', _id: Math.random().toString(36).slice(2) }]);
    const remove = (i) => setRows(r => r.filter((_, j) => j !== i));

    const save = async () => {
        setBusy(true); setError('');
        try {
            // Kategori baru dikirim tanpa kode (dibuatkan server dari namanya); baris baru yang kosong diabaikan.
            await saveTicketCategories(rows.filter(c => c.key || c.label.trim()).map(({ key, label }) => ({ key, label })));
            await loadSysConfig();
            onClose();
        } catch (e) {
            setError(e?.response?.data?.detail || e.message);
        } finally { setBusy(false); }
    };

    return (
        <Modal label={t('tcat.title')} onClose={busy ? () => {} : onClose}>
            <div style={{ padding: '16px 18px', borderBottom: '1px solid var(--border)', fontSize: 15, fontWeight: 600, color: 'var(--text)' }}>{t('tcat.title')}</div>
            <div style={{ padding: 18 }}>
                {rows.map((c, i) => {
                    const locked = REQUIRED.includes(c.key);
                    return (
                        <div key={c._id} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                            <input value={c.label} maxLength={40} aria-label={t('tcat.rowLabel', { n: i + 1 })}
                                placeholder={t(`cat.${c.key}`) === `cat.${c.key}` ? t('tcat.namePh') : t(`cat.${c.key}`)}
                                onChange={e => setLabel(i, e.target.value)} style={{ ...inp, flex: 1, minWidth: 0 }} />
                            <span style={{ fontSize: 10, fontFamily: 'var(--fmono)', color: 'var(--text3)', width: 120, flexShrink: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}>{c.key || t('tcat.new')}</span>
                            <button disabled={locked} onClick={() => remove(i)} title={locked ? t('tcat.locked') : t('common.delete')} aria-label={t('common.delete')}
                                style={{ ...btn, opacity: locked ? 0.35 : 1, cursor: locked ? 'not-allowed' : 'pointer' }}>✕</button>
                        </div>
                    );
                })}
                {rows.length < MAX && <button onClick={add} style={{ ...btn, marginTop: 4 }}>{t('tcat.add')}</button>}
                <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 10, lineHeight: 1.5 }}>{t('tcat.hint')}</div>
                {error && <div role="alert" style={{ marginTop: 12, fontSize: 12, color: 'var(--red)' }}>⚠ {error}</div>}
                <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 16 }}>
                    <button onClick={onClose} disabled={busy} style={btn}>{t('common.cancel')}</button>
                    <button onClick={save} disabled={busy} style={{ ...btn, background: 'var(--cyan)', color: '#000', borderColor: 'var(--cyan)', fontWeight: 600, opacity: busy ? 0.6 : 1 }}>
                        {busy ? '…' : t('common.save')}
                    </button>
                </div>
            </div>
        </Modal>
    );
}
