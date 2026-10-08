// Semua aktivitas satu akun di satu tempat: Activity Log, sesi Remote, link Open Web, dan sesi SSH.
// Dibuka dengan mengeklik nama pengguna di Audit & Remote, atau tombol Aktivitas di halaman Users.
import { useEffect, useState } from 'react';
import { useT } from '../i18n';
import { fetchAuditLogs, fetchRemoteHistory, fetchOpenWebHistory, fetchSshHistory } from '../api';
import { fmtTime, fmtEpoch, fmtDur, SEV_COLOR, WEB_STATUS, SSH_STATUS, statusOf } from '../adminFormat';
import { Badge, ExportButton, Modal, Pager, ResponsiveTable, SubTabs } from './AdminWidgets';

const SIZE = 20;
const SOURCES = {
    audit: { fetch: (username, page) => fetchAuditLogs({ username, page, page_size: SIZE }), csv: 'audit-logs/export' },
    remote: { fetch: (username, page) => fetchRemoteHistory(page, SIZE, { username }), csv: 'remote/history/export' },
    web: { fetch: (username, page) => fetchOpenWebHistory(page, SIZE, { username }), csv: 'openweb/history/export' },
    ssh: { fetch: (username, page) => fetchSshHistory(page, SIZE, { username }), csv: 'ssh/history/export' },
};

export default function UserActivityModal({ username, onClose }) {
    const t = useT();
    const [tab, setTab] = useState('audit');
    const [pages, setPages] = useState({ audit: 1, remote: 1, web: 1, ssh: 1 });
    const [data, setData] = useState({});

    // Jumlah untuk label tab: semua sumber dimuat sekali saat dialog dibuka.
    useEffect(() => {
        let alive = true;
        for (const [key, src] of Object.entries(SOURCES)) {
            src.fetch(username, 1).then(d => { if (alive) setData(prev => ({ ...prev, [key]: prev[key] || d })); });
        }
        return () => { alive = false; };
    }, [username]);

    const page = pages[tab];
    useEffect(() => {
        let alive = true;
        SOURCES[tab].fetch(username, page).then(d => { if (alive) setData(prev => ({ ...prev, [tab]: d })); });
        return () => { alive = false; };
    }, [username, tab, page]);

    const cur = data[tab];
    const totalPages = Math.max(1, Math.ceil((cur?.total || 0) / SIZE));
    const go = (p) => setPages(prev => ({ ...prev, [tab]: Math.min(totalPages, Math.max(1, p)) }));
    const count = (k) => (data[k] ? ` (${data[k].total})` : '');
    const tabs = [['audit', t('uact.audit') + count('audit')], ['remote', t('uact.remote') + count('remote')],
        ['web', t('uact.web') + count('web')], ['ssh', t('uact.ssh') + count('ssh')]];

    const cols = {
        audit: [
            { key: 'time', label: t('admin.colTime'), render: it => fmtTime(it.timestamp), style: { fontFamily: 'var(--fmono)', whiteSpace: 'nowrap' } },
            { key: 'action', label: t('admin.colAction'), render: it => it.action_type, style: { fontFamily: 'var(--fmono)', color: 'var(--text)' } },
            { key: 'detail', label: t('admin.colDetail'), render: it => it.detail, style: { maxWidth: 360 } },
            { key: 'ip', label: t('admin.colIp'), render: it => it.client_ip, style: { fontFamily: 'var(--fmono)', color: 'var(--text3)' } },
            { key: 'sev', label: t('admin.colSeverity'), render: it => <Badge text={it.severity} color={SEV_COLOR[it.severity] || 'var(--text3)'} />, mobile: 'badge' },
        ],
        remote: [
            { key: 'vm', label: t('admin.colVmShort'), render: h => <>{h.vm}{h.os_account && <span style={{ color: 'var(--text3)' }}> @{h.os_account}</span>}</>, style: { fontFamily: 'var(--fmono)', color: 'var(--cyan)' } },
            { key: 'proto', label: t('admin.colProtocol'), render: h => h.protocol || '—', style: { fontFamily: 'var(--fmono)' } },
            { key: 'ip', label: t('admin.colClientIp'), render: h => h.remote_host || '—', style: { fontFamily: 'var(--fmono)', color: 'var(--text3)' } },
            { key: 'start', label: t('admin.colConnect'), render: h => fmtEpoch(h.start_date), style: { fontFamily: 'var(--fmono)' } },
            { key: 'dur', label: t('admin.colDuration'), render: h => fmtDur(h.duration_s), style: { fontFamily: 'var(--fmono)' } },
            { key: 'st', label: t('admin.colStatus'), render: h => h.active ? <Badge text={t('admin.stActive')} color="var(--green)" /> : <Badge text={t('admin.stEnded')} color="var(--text3)" />, mobile: 'badge' },
        ],
        web: [
            { key: 'target', label: t('admin.colTarget'), render: s => s.target_ip, style: { fontFamily: 'var(--fmono)', color: 'var(--cyan)' } },
            { key: 'created', label: t('admin.colCreated'), render: s => fmtTime(s.created_at), style: { fontFamily: 'var(--fmono)' } },
            { key: 'hits', label: t('admin.colHits'), render: s => String(s.hits), style: { fontFamily: 'var(--fmono)' } },
            { key: 'ips', label: t('admin.colClientIps'), render: s => (s.client_ips || []).join(', ') || '—', style: { fontFamily: 'var(--fmono)', color: 'var(--text3)' } },
            { key: 'st', label: t('admin.colStatus'), render: s => { const [x, c] = statusOf(WEB_STATUS, s.status); return <Badge text={x} color={c} />; }, mobile: 'badge' },
        ],
        ssh: [
            { key: 'start', label: t('admin.colStart'), render: s => fmtTime(s.started_at), style: { fontFamily: 'var(--fmono)', whiteSpace: 'nowrap' } },
            { key: 'ip', label: t('admin.colFromIp'), render: s => s.client_ip, style: { fontFamily: 'var(--fmono)' } },
            { key: 'target', label: t('admin.colTarget'), render: s => s.targets.map(tg => tg.vm ? `${tg.target} (${tg.vm})` : tg.target).join(', ') || '—', style: { fontFamily: 'var(--fmono)', color: 'var(--cyan)' } },
            { key: 'dur', label: t('admin.colDuration'), render: s => fmtDur(s.duration), style: { fontFamily: 'var(--fmono)' } },
            { key: 'st', label: t('admin.colStatus'), render: s => { const [x, c] = statusOf(SSH_STATUS, s.status); return <Badge text={x} color={c} />; }, mobile: 'badge' },
        ],
    };

    return (
        <Modal label={t('uact.title', { user: username })} onClose={onClose} width={980}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, padding: '14px 18px', borderBottom: '1px solid var(--border)' }}>
                <div>
                    <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--text)' }}>{t('uact.title', { user: username })}</div>
                    <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>{t('uact.hint')}</div>
                </div>
                <button onClick={onClose} aria-label={t('common.close')} title={t('common.close')}
                    style={{ background: 'none', border: 'none', color: 'var(--text3)', fontSize: 22, cursor: 'pointer', lineHeight: 1 }}>×</button>
            </div>
            <div style={{ padding: '12px 18px 0' }}>
                <SubTabs value={tab} onChange={setTab} tabs={tabs}>
                    <span style={{ marginLeft: 'auto' }}><ExportButton path={SOURCES[tab].csv} params={{ username }} /></span>
                </SubTabs>
            </div>
            <div style={{ borderTop: '1px solid var(--border)' }}>
                <ResponsiveTable cols={cols[tab]} items={cur?.items || []} rowKey={it => it.id ?? `${it.start_date}-${it.vm}`}
                    loading={!cur} empty={t('uact.empty')} />
                <Pager page={page} totalPages={totalPages} total={cur?.total || 0} loading={!cur}
                    onPrev={() => go(page - 1)} onNext={() => go(page + 1)} />
            </div>
        </Modal>
    );
}
