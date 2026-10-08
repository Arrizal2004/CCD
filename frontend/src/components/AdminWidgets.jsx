// Komponen bersama halaman Audit & Remote: tabel responsif, pager, tombol ekspor CSV, tautan nama
// pengguna (membuka aktivitasnya), dan dialog memutus sesi dengan pilihan pemblokiran.
import { useEffect, useState } from 'react';
import useIsMobile from '../useIsMobile';
import { useT } from '../i18n';
import { downloadAdminCsv } from '../api';
import { TH, TD, btn, killBtn, killBtnMobile } from '../adminFormat';

export function Badge({ text, color }) {
    return <span style={{ fontSize: 10, fontFamily: 'var(--fmono)', padding: '1px 7px', borderRadius: 10, color, background: color + '22', whiteSpace: 'nowrap' }}>{text}</span>;
}

// Di HP setiap baris tabel ditampilkan sebagai kartu: tabel di halaman ini lebarnya 850-1900px,
// tidak nyaman digeser ke samping di layar HP. Nilai kosong atau '-' tidak ditampilkan.
export function MCard({ title, badge, lead, rows = [], children }) {
    return (
        <div style={{ padding: '12px 14px', borderBottom: '1px solid var(--border)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8, marginBottom: 6 }}>
                <div style={{ minWidth: 0, fontSize: 13, color: 'var(--text)', overflowWrap: 'anywhere' }}>{title}</div>
                {badge && <div style={{ flexShrink: 0 }}>{badge}</div>}
            </div>
            {lead && <div style={{ fontSize: 12, color: 'var(--text2)', lineHeight: 1.5, marginBottom: 6, overflowWrap: 'anywhere' }}>{lead}</div>}
            {rows.filter(([, v]) => v != null && v !== '' && v !== '-' && v !== '—').map(([k, v]) => (
                <div key={k} style={{ display: 'flex', gap: 10, fontSize: 12, padding: '2px 0' }}>
                    <span style={{ color: 'var(--text3)', width: 92, flexShrink: 0 }}>{k}</span>
                    <span style={{ color: 'var(--text2)', fontFamily: 'var(--fmono)', minWidth: 0, overflowWrap: 'anywhere' }}>{v}</span>
                </div>
            ))}
            {children}
        </div>
    );
}

export function MEmpty({ children }) {
    return <div style={{ padding: 30, textAlign: 'center', color: 'var(--text3)', fontSize: 12 }}>{children}</div>;
}

/**
 * Tabel di layar lebar, kartu di HP.
 * cols: [{ key, label, render(item), style?, mobile? }] — kolom pertama menjadi judul kartu;
 *       mobile: false menyembunyikan kolom di kartu, 'badge' menaruhnya di pojok kanan kartu.
 * action(item): isi kolom/tombol aksi (opsional).
 */
export function ResponsiveTable({ cols, items, rowKey, empty, loading, action, actionLabel }) {
    const isMobile = useIsMobile();
    if (isMobile) {
        const [first, ...rest] = cols;
        const badgeCol = rest.find(c => c.mobile === 'badge');
        return (
            <div>
                {items.map(it => (
                    <MCard key={rowKey(it)} title={first.render(it)} badge={badgeCol?.render(it)}
                        rows={rest.filter(c => c.mobile !== false && c.mobile !== 'badge').map(c => [c.label, c.render(it)])}>
                        {action && action(it, true)}
                    </MCard>
                ))}
                {!loading && items.length === 0 && <MEmpty>{empty}</MEmpty>}
            </div>
        );
    }
    const span = cols.length + (action ? 1 : 0);
    return (
        <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead><tr>
                    {cols.map(c => <th key={c.key} style={TH}>{c.label}</th>)}
                    {action && <th style={TH}>{actionLabel}</th>}
                </tr></thead>
                <tbody>
                    {items.map(it => (
                        <tr key={rowKey(it)}>
                            {cols.map(c => <td key={c.key} style={{ ...TD, ...(c.style || {}) }}>{c.render(it)}</td>)}
                            {action && <td style={TD}>{action(it, false)}</td>}
                        </tr>
                    ))}
                    {!loading && items.length === 0 && (
                        <tr><td colSpan={span} style={{ ...TD, textAlign: 'center', color: 'var(--text3)', padding: 30 }}>{empty}</td></tr>
                    )}
                </tbody>
            </table>
        </div>
    );
}

export function Pager({ page, totalPages, total, loading, onPrev, onNext }) {
    const t = useT();
    return (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 14px', fontSize: 11, color: 'var(--text3)', fontFamily: 'var(--fmono)' }}>
            <span>{loading ? t('common.loading') : t('admin.pager', { total, page, pages: totalPages })}</span>
            <div style={{ display: 'flex', gap: 6 }}>
                <button onClick={onPrev} disabled={page <= 1} style={{ ...btn, opacity: page <= 1 ? 0.4 : 1 }}>{t('admin.prev')}</button>
                <button onClick={onNext} disabled={page >= totalPages} style={{ ...btn, opacity: page >= totalPages ? 0.4 : 1 }}>{t('admin.next')}</button>
            </div>
        </div>
    );
}

// Nama pengguna yang bisa diklik untuk membuka semua aktivitasnya. 'system' dan nama kosong tidak.
export function UserLink({ name, onUser }) {
    if (!name || name === 'system' || name === '—' || !onUser) return <span style={{ color: 'var(--text)' }}>{name || '—'}</span>;
    return (
        <button type="button" onClick={() => onUser(name)} title={name}
            style={{ background: 'none', border: 'none', padding: 0, cursor: 'pointer', color: 'var(--text)', font: 'inherit', textAlign: 'left', textDecoration: 'underline dotted', textUnderlineOffset: 3 }}>
            {name}
        </button>
    );
}

export function SubTabs({ value, onChange, tabs, children }) {
    return (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 12, flexWrap: 'wrap' }}>
            {tabs.map(([id, l]) => (
                <button key={id} onClick={() => onChange(id)}
                    style={{ padding: '5px 14px', borderRadius: 6, fontSize: 12, cursor: 'pointer', fontFamily: 'var(--fmono)',
                        background: value === id ? 'var(--bg-hover)' : 'transparent', color: value === id ? 'var(--cyan)' : 'var(--text3)',
                        border: `1px solid ${value === id ? 'var(--cyan)44' : 'var(--border)'}` }}>{l}</button>
            ))}
            {children}
        </div>
    );
}

// Ekspor CSV sesuai filter yang sedang dipakai (path relatif ke /api/admin/). params boleh berupa
// fungsi yang dipanggil saat tombol ditekan (mis. rentang waktu relatif terhadap sekarang).
export function ExportButton({ path, params }) {
    const t = useT();
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState('');
    const run = async () => {
        setBusy(true); setErr('');
        try { await downloadAdminCsv(path, typeof params === 'function' ? params() : params); }
        catch (e) { setErr(t('admin.failedPrefix', { msg: e?.response?.status ? `HTTP ${e.response.status}` : e.message })); }
        finally { setBusy(false); }
    };
    return (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <button type="button" onClick={run} disabled={busy} style={{ ...btn, opacity: busy ? 0.6 : 1, whiteSpace: 'nowrap' }}>
                {busy ? t('admin.exporting') : t('admin.exportCsv')}
            </button>
            {err && <span style={{ fontSize: 11, color: 'var(--red)' }}>{err}</span>}
        </span>
    );
}

export function Modal({ label, onClose, width = 520, children }) {
    useEffect(() => {
        const onKey = (e) => { if (e.key === 'Escape') onClose(); };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [onClose]);
    return (
        <div onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
            style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', zIndex: 1000, display: 'flex', alignItems: 'flex-start', justifyContent: 'center', padding: '6vh 12px', overflowY: 'auto' }}>
            <div role="dialog" aria-modal="true" aria-label={label}
                style={{ width: '100%', maxWidth: width, background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 12, boxShadow: '0 20px 60px rgba(0,0,0,0.5)', boxSizing: 'border-box' }}>
                {children}
            </div>
        </div>
    );
}

/**
 * Dialog memutus sesi. options: [{ value, label, hint, disabled? }]. onConfirm(block) mengembalikan
 * hasil dari backend; hasilnya ditampilkan di dialog sebelum ditutup.
 */
export function KillDialog({ title, subject, options, confirmLabel, onConfirm, onClose, describe }) {
    const t = useT();
    const [block, setBlock] = useState('none');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');
    const [done, setDone] = useState(null);
    const submit = async () => {
        setBusy(true); setError('');
        try { setDone(await onConfirm(block)); }
        catch (e) { setError(e?.response?.data?.detail || e.message); }
        finally { setBusy(false); }
    };
    return (
        <Modal label={title} onClose={busy ? () => {} : onClose}>
            <div style={{ padding: '16px 18px', borderBottom: '1px solid var(--border)' }}>
                <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--text)' }}>{title}</div>
                <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 4, fontFamily: 'var(--fmono)', overflowWrap: 'anywhere' }}>{subject}</div>
            </div>
            {done ? (
                <div style={{ padding: 18 }}>
                    <div style={{ fontSize: 13, color: 'var(--green)', lineHeight: 1.6 }}>{describe(done)}</div>
                    <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 16 }}>
                        <button onClick={onClose} autoFocus style={{ ...btn, color: 'var(--cyan)', borderColor: 'var(--cyan)66' }}>{t('common.close')}</button>
                    </div>
                </div>
            ) : (
                <div style={{ padding: 18 }}>
                    <div role="radiogroup" aria-label={t('admin.afterKill')} style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                        {options.map(o => (
                            <label key={o.value} style={{ display: 'flex', gap: 10, alignItems: 'flex-start', padding: '10px 12px', borderRadius: 8, cursor: o.disabled ? 'not-allowed' : 'pointer', opacity: o.disabled ? 0.5 : 1,
                                border: `1px solid ${block === o.value ? 'var(--red)88' : 'var(--border)'}`, background: block === o.value ? 'var(--red-glow, #ff174411)' : 'transparent' }}>
                                <input type="radio" name="kill-block" value={o.value} checked={block === o.value} disabled={o.disabled || busy}
                                    onChange={() => setBlock(o.value)} style={{ marginTop: 3 }} />
                                <span>
                                    <span style={{ display: 'block', fontSize: 13, color: 'var(--text)' }}>{o.label}</span>
                                    <span style={{ display: 'block', fontSize: 11, color: 'var(--text3)', marginTop: 2, lineHeight: 1.5 }}>{o.hint}</span>
                                </span>
                            </label>
                        ))}
                    </div>
                    {error && <div role="alert" style={{ marginTop: 12, fontSize: 12, color: 'var(--red)', lineHeight: 1.5 }}>⚠ {error}</div>}
                    <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 16 }}>
                        <button onClick={onClose} disabled={busy} style={btn}>{t('common.cancel')}</button>
                        <button onClick={submit} disabled={busy}
                            style={{ ...btn, background: 'var(--red)', color: '#fff', borderColor: 'var(--red)', opacity: busy ? 0.6 : 1 }}>
                            {busy ? '…' : confirmLabel}
                        </button>
                    </div>
                </div>
            )}
        </Modal>
    );
}

// Tombol aksi baris (tabel) atau kartu (HP).
export function RowKillButton({ mobile, onClick, label }) {
    return mobile
        ? <button onClick={onClick} style={killBtnMobile}>{label}</button>
        : <button onClick={onClick} style={killBtn}>{label}</button>;
}
