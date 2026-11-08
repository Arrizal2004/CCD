import { useCallback } from 'react';
import { fetchQuotaDefaults, saveQuotaDefaults } from '../api';
import { useT } from '../i18n';
import QuotaEditor from './QuotaEditor';

/** Kartu kuota bawaan mahasiswa di Pengaturan Sistem (superadmin). */
export default function QuotaDefaultsCard() {
    const t = useT();
    const load = useCallback(() => fetchQuotaDefaults(), []);
    return (
        <div style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', borderRadius: 10, padding: 18, marginBottom: 16 }}>
            <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text)', marginBottom: 6 }}>{t('quota.defaultsTitle')}</div>
            <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 12, lineHeight: 1.5 }}>{t('quota.defaultsHint')}</div>
            <QuotaEditor load={load} save={saveQuotaDefaults} allowEmpty={false} />
        </div>
    );
}
