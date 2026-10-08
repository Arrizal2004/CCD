import ProxmoxInstancesPanel from './ProxmoxInstancesPanel';
import { useT } from '../i18n';

export default function ProxmoxInstancesModal({ onClose, onChanged, canModify = false }) {
    const t = useT();
    return (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 210 }} onClick={onClose}>
            <div style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', borderRadius: 10, width: 560, maxWidth: '92vw', maxHeight: '85vh', overflow: 'auto', padding: 20 }} onClick={e => e.stopPropagation()}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
                    <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text)' }}>{t('inst.title')}</div>
                    <button onClick={onClose} aria-label={t('common.close')} style={{ background: 'transparent', border: 'none', color: 'var(--text3)', fontSize: 18, cursor: 'pointer' }}>×</button>
                </div>
                <ProxmoxInstancesPanel onChanged={onChanged} canModify={canModify} />
            </div>
        </div>
    );
}
