import { useCallback, useEffect, useState } from 'react';
import { fetchGroupQuota, fetchUserQuota, saveGroupQuota, saveUserQuota } from '../api';
import { useT } from '../i18n';
import QuotaEditor from './QuotaEditor';

/** Kuota satu pengguna (kind="user") atau satu grup (kind="group"), dibuka dari halaman Users dan Grup. */
export default function QuotaModal({ kind, id, name, onClose }) {
    const t = useT();
    const [info, setInfo] = useState(null);
    const isUser = kind === 'user';
    const refresh = useCallback(() => (isUser ? fetchUserQuota(id) : fetchGroupQuota(id)).then(d => { setInfo(d); return d; }), [isUser, id]);
    const load = useCallback(async () => { const d = await refresh(); return isUser ? d.override : d; }, [refresh, isUser]);
    const save = async (vals) => { await (isUser ? saveUserQuota(id, vals) : saveGroupQuota(id, vals)); if (isUser) await refresh(); };
    useEffect(() => { const h = (e) => { if (e.key === 'Escape') onClose(); }; window.addEventListener('keydown', h); return () => window.removeEventListener('keydown', h); }, [onClose]);

    const lim = (f) => (info?.limits?.[f] ? info.limits[f] : t('quota.unlimited'));
    const summary = isUser && info ? (
        <div style={{ fontSize: 11, color: 'var(--text3)', lineHeight: 1.7 }}>
            <div>{t('quota.effective', { value: `${lim('max_vms')} VM · ${lim('max_vcpu')} vCPU · ${lim('max_ram_mb')} MB · ${lim('max_disk_gb')} GB`, source: t(`quota.src.${info.source.max_vms}`) })}</div>
            <div>{t('quota.usage', { vms: info.usage.vms, vcpu: info.usage.vcpu, ram: info.usage.ram_mb, disk: info.usage.disk_gb })}</div>
        </div>
    ) : null;

    return (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 210 }} onClick={onClose}>
            <div role="dialog" aria-label={t('quota.title')} onClick={e => e.stopPropagation()}
                style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', borderRadius: 10, width: 520, maxWidth: '94vw', padding: 20 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                    <div style={{ fontSize: 15, fontWeight: 600 }}>{t('quota.title')} · {name}</div>
                    <button type="button" onClick={onClose} aria-label={t('common.close')} style={{ background: 'none', border: 'none', color: 'var(--text3)', cursor: 'pointer', fontSize: 18 }}>✕</button>
                </div>
                <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 12, lineHeight: 1.5 }}>{t('quota.hint')}</div>
                <QuotaEditor load={load} save={save} summary={summary} />
            </div>
        </div>
    );
}
