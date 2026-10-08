import { useEffect, useRef, useState } from 'react';
import { deleteOsLogo, saveOsOptions, uploadOsLogo } from '../api';
import { useT } from '../i18n';
import { loadSysConfig, osLogoUrl, useSysConfig } from '../sysconfig';
import { btn, inp } from '../adminFormat';
import { Modal } from './AdminWidgets';

const MAX = 20;
const MAX_LOGO = 256 * 1024;
const LOGO_TYPES = ['image/png', 'image/jpeg', 'image/webp'];

function OsLogo({ url, name, size = 28 }) {
    const t = useT();
    return url
        ? <img src={url} alt={t('osopt.logoAlt', { name })} width={size} height={size} style={{ objectFit: 'contain', borderRadius: 4, flexShrink: 0 }} />
        : <span aria-hidden="true" style={{ width: size, height: size, flexShrink: 0, borderRadius: 4, border: '1px dashed var(--border)' }} />;
}

// Kelola pilihan OS di form Infra Request beserta logonya (superadmin). Dibuka dari halaman Infra Requests.
export default function OsOptionsModal({ onClose }) {
    const t = useT();
    const cfg = useSysConfig();
    const [rows, setRows] = useState(() => cfg.vps_os_options.map(name => ({
        _id: name, name, from: name, logo: osLogoUrl(name, cfg.os_logos), file: null, removeLogo: false,
    })));
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');
    const previews = useRef(new Set());
    useEffect(() => { const set = previews.current; return () => set.forEach(u => URL.revokeObjectURL(u)); }, []);

    const patch = (i, change) => setRows(r => r.map((row, j) => (j === i ? { ...row, ...change } : row)));
    const move = (i) => setRows(r => r.map((row, j) => (j === i - 1 ? r[i] : j === i ? r[i - 1] : row)));
    const add = () => setRows(r => [...r, { _id: Math.random().toString(36).slice(2), name: '', from: null, logo: null, file: null, removeLogo: false }]);

    const pick = (i, file) => {
        if (!file) return;
        if (!LOGO_TYPES.includes(file.type)) { setError(t('osopt.logoBad')); return; }
        if (file.size > MAX_LOGO) { setError(t('osopt.logoBig')); return; }
        setError('');
        const url = URL.createObjectURL(file);
        previews.current.add(url);
        patch(i, { file, logo: url, removeLogo: false });
    };

    const save = async () => {
        const list = rows.filter(r => r.name.trim());
        setBusy(true); setError('');
        try {
            // 1) daftar (nama baru dan urutan), 2) logo: unggah yang dipilih, hapus yang dibuang.
            const saved = await saveOsOptions(list.map(r => ({ name: r.name.trim(), from: r.from })));
            const names = saved.vps_os_options;
            for (const r of list) {
                const name = names.find(n => n.toLowerCase() === r.name.trim().replace(/\s+/g, ' ').toLowerCase());
                if (!name) continue;
                if (r.file) await uploadOsLogo(name, r.file);
                else if (r.removeLogo) await deleteOsLogo(name);
            }
            await loadSysConfig();
            onClose();
        } catch (e) {
            setError(e?.response?.data?.detail || e.message);
            await loadSysConfig();
        } finally { setBusy(false); }
    };

    return (
        <Modal label={t('osopt.title')} onClose={busy ? () => {} : onClose} width={600}>
            <div style={{ padding: '16px 18px', borderBottom: '1px solid var(--border)', fontSize: 15, fontWeight: 600, color: 'var(--text)' }}>{t('osopt.title')}</div>
            <div style={{ padding: 18 }}>
                {rows.map((r, i) => (
                    <div key={r._id} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
                        <OsLogo url={r.logo} name={r.name} />
                        <input value={r.name} maxLength={40} placeholder={t('osopt.namePh')} aria-label={t('osopt.rowLabel', { n: i + 1 })}
                            onChange={e => patch(i, { name: e.target.value })} style={{ ...inp, flex: 1, minWidth: 140 }} />
                        <label style={{ ...btn, cursor: 'pointer', whiteSpace: 'nowrap' }}>
                            {r.logo ? t('osopt.changeLogo') : t('osopt.chooseLogo')}
                            <input type="file" accept={LOGO_TYPES.join(',')} aria-label={`${t('osopt.chooseLogo')} ${i + 1}`} style={{ display: 'none' }}
                                onChange={e => { pick(i, e.target.files?.[0]); e.target.value = ''; }} />
                        </label>
                        {r.logo && (
                            <button onClick={() => patch(i, { logo: null, file: null, removeLogo: true })} style={{ ...btn, whiteSpace: 'nowrap' }}>{t('osopt.removeLogo')}</button>
                        )}
                        <button disabled={i === 0} onClick={() => move(i)} title={t('osopt.up')} aria-label={t('osopt.up')}
                            style={{ ...btn, opacity: i === 0 ? 0.35 : 1, cursor: i === 0 ? 'default' : 'pointer' }}>↑</button>
                        <button disabled={rows.length === 1} onClick={() => setRows(x => x.filter((_, j) => j !== i))}
                            title={rows.length === 1 ? t('osopt.min') : t('common.delete')} aria-label={t('common.delete')}
                            style={{ ...btn, opacity: rows.length === 1 ? 0.35 : 1, cursor: rows.length === 1 ? 'not-allowed' : 'pointer' }}>✕</button>
                    </div>
                ))}
                {rows.length < MAX && <button onClick={add} style={{ ...btn, marginTop: 4 }}>{t('osopt.add')}</button>}
                <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 10, lineHeight: 1.5 }}>{t('osopt.hint')}</div>
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

export { OsLogo };
