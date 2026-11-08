import { useEffect, useState } from 'react';
import { useT } from '../i18n';

const FIELDS = [['max_vms', 'quota.maxVms'], ['max_vcpu', 'quota.maxVcpu'], ['max_ram_mb', 'quota.maxRam'], ['max_disk_gb', 'quota.maxDisk']];
const input = { width: '100%', boxSizing: 'border-box', background: 'var(--bg-card2)', border: '1px solid var(--border)', borderRadius: 6, padding: '7px 9px', color: 'var(--text)', fontSize: 13 };

/** Form 4 bidang kuota. `load()` mengembalikan {override|...}, `save(values)` menyimpan. allowEmpty: kosong = ikut lapisan berikutnya. */
export default function QuotaEditor({ load, save, allowEmpty = true, summary = null }) {
    const t = useT();
    const [vals, setVals] = useState(null);
    const [msg, setMsg] = useState(null);
    useEffect(() => {
        let alive = true;
        load().then(d => { if (alive) setVals(Object.fromEntries(FIELDS.map(([k]) => [k, d[k] ?? '']))); }).catch(() => {});
        return () => { alive = false; };
    }, [load]);
    if (!vals) return null;
    const submit = async (e) => {
        e.preventDefault();
        try { await save(vals); setMsg({ ok: true, text: t('quota.saved') }); }
        catch (err) { setMsg({ ok: false, text: err?.response?.data?.detail || err.message }); }
    };
    return (
        <form onSubmit={submit}>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(150px,1fr))', gap: 10, marginBottom: 10 }}>
                {FIELDS.map(([k, label]) => (
                    <label key={k} style={{ fontSize: 11, color: 'var(--text3)' }}>
                        {t(label)}
                        <input type="number" min="0" value={vals[k]} placeholder={allowEmpty ? '—' : '0'} aria-label={t(label)}
                            onChange={e => setVals(v => ({ ...v, [k]: e.target.value }))} style={input} />
                    </label>
                ))}
            </div>
            {summary}
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 8 }}>
                <button type="submit" style={{ padding: '7px 16px', borderRadius: 6, fontSize: 12, fontWeight: 600, background: 'var(--cyan)', color: '#000', border: 'none', cursor: 'pointer' }}>{t('quota.save')}</button>
                {msg && <span role="status" style={{ fontSize: 12, color: msg.ok ? 'var(--green)' : 'var(--red)' }}>{msg.ok ? '✓' : '⚠'} {msg.text}</span>}
            </div>
        </form>
    );
}
