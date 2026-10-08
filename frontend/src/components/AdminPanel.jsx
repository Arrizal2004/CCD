import { useState, useEffect, useCallback } from 'react';
import useIsMobile from '../useIsMobile';
import { useT } from '../i18n';
import {
    fetchAuditLogs, fetchAuditActions, fetchFailedLogins, fetchRemoteSessions, killRemoteSession, fetchRemoteHistory,
    guacGrantAdmins, fetchOpenWebSessions, fetchOpenWebHistory, killOpenWebSession, fetchSshConfig, fetchSshSessions,
    fetchSshHistory, killSshSession,
} from '../api';
import {
    ROLE_COLOR, SEV_COLOR, PAGE, WEB_STATUS, SSH_STATUS, statusOf, fmtTime, fmtEpoch, fmtDur, fmtBytes, roleLabel,
    inp, btn, card,
} from '../adminFormat';
import { Badge, ExportButton, KillDialog, Pager, ResponsiveTable, RowKillButton, SubTabs, UserLink } from './AdminWidgets';
import UserActivityModal from './UserActivityModal';

// Nilai yang baru dipakai setelah pengguna berhenti mengetik sebentar, supaya kotak pencarian tidak
// mengirim request pada setiap huruf.
function useDebounced(value, ms = 400) {
    const [v, setV] = useState(value);
    useEffect(() => {
        const id = setTimeout(() => setV(value), ms);
        return () => clearTimeout(id);
    }, [value, ms]);
    return v;
}

export default function AdminPanel({ currentUser }) {
    const t = useT();
    const [tab, setTab] = useState('audit');
    const [sshEnabled, setSshEnabled] = useState(false);
    const [userView, setUserView] = useState('');
    const canKill = ['sysadmin', 'superadmin'].includes(currentUser?.role);
    const isSuper = currentUser?.role === 'superadmin';
    useEffect(() => { fetchSshConfig().then(c => setSshEnabled(!!c.enabled)); }, []);
    const tabs = [['audit', t('admin.tabAudit')], ['failed', t('admin.tabFailed')], ['remote', t('admin.tabRemote')],
        ['web', t('admin.tabWeb')], ...(sshEnabled ? [['ssh', t('admin.tabSsh')]] : [])];
    const isMobile = useIsMobile();
    const onUser = setUserView;
    return (
        <div style={{ padding: isMobile ? 12 : '14px 20px', maxWidth: 1600, margin: '0 auto' }}>
            <div style={isMobile
                ? { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, marginBottom: 12 }
                : { display: 'flex', gap: 6, marginBottom: 16, flexWrap: 'wrap' }}>
                {tabs.map(([id, l]) => (
                    <button key={id} onClick={() => setTab(id)}
                        style={{ padding: isMobile ? '8px 6px' : '7px 16px', borderRadius: 8, fontSize: isMobile ? 12 : 13, cursor: 'pointer', fontFamily: 'var(--fmono)', whiteSpace: 'nowrap',
                            background: tab === id ? 'var(--cyan-glow, #00e5ff22)' : 'var(--bg-card)',
                            color: tab === id ? 'var(--cyan)' : 'var(--text3)',
                            border: `1px solid ${tab === id ? 'var(--cyan)' : 'var(--border)'}` }}>
                        {l}
                    </button>
                ))}
            </div>
            {tab === 'audit' && <ActivityLog onUser={onUser} />}
            {tab === 'failed' && <FailedLogins onUser={onUser} />}
            {tab === 'remote' && <RemoteSessions canKill={canKill} onUser={onUser} />}
            {tab === 'web' && <WebSessions canKill={canKill} isSuper={isSuper} onUser={onUser} />}
            {tab === 'ssh' && <SshSessions canKill={canKill} isSuper={isSuper} onUser={onUser} />}
            {userView && <UserActivityModal username={userView} onClose={() => setUserView('')} />}
        </div>
    );
}

// ── Activity Log ─────────────────────────────────────────────────────────────
function localDateStr(d = new Date()) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${day}`;
}
function todayStr() { return localDateStr(); }
function daysAgoStr(n) {
    const d = new Date(); d.setDate(d.getDate() - n);
    return localDateStr(d);
}

function ActivityLog({ onUser }) {
    const t = useT();
    const [data, setData] = useState({ total: 0, items: [] });
    const [loading, setLoading] = useState(true);
    const [page, setPage] = useState(1);
    const [searchInput, setSearchInput] = useState('');
    const [userInput, setUserInput] = useState('');
    const [severity, setSeverity] = useState('');
    const [action, setAction] = useState('');
    const [actions, setActions] = useState([]);
    const [dateFrom, setDateFrom] = useState('');
    const [dateTo,   setDateTo]   = useState('');
    const search = useDebounced(searchInput.trim());
    const username = useDebounced(userInput.trim());
    const isMobile = useIsMobile();

    useEffect(() => { fetchAuditActions().then(setActions); }, []);

    const params = { search, severity, action, username };
    if (dateFrom) params.start = new Date(dateFrom + 'T00:00:00').toISOString();
    if (dateTo)   params.end   = new Date(dateTo   + 'T23:59:59').toISOString();
    for (const k of Object.keys(params)) if (!params[k]) delete params[k];
    const key = JSON.stringify(params);

    const load = useCallback(async () => {
        setData(await fetchAuditLogs({ ...JSON.parse(key), page, page_size: PAGE }));
        setLoading(false);
    }, [key, page]);

    useEffect(() => { load(); }, [load]);
    // Muat ulang otomatis hanya di halaman pertama: di halaman berikutnya baris akan bergeser
    // setiap ada entri baru, sehingga yang sedang dibaca berpindah tempat.
    useEffect(() => {
        if (page !== 1) return undefined;
        const id = setInterval(load, 15000);
        return () => clearInterval(id);
    }, [load, page]);

    const applyPreset = (from, to) => { setDateFrom(from); setDateTo(to); setPage(1); };
    const resetAll = () => { setSearchInput(''); setUserInput(''); setSeverity(''); setAction(''); setDateFrom(''); setDateTo(''); setPage(1); };

    const totalPages = Math.max(1, Math.ceil(data.total / PAGE));
    const hasFilter  = searchInput || userInput || severity || action || dateFrom || dateTo;
    const presets = [
        [t('admin.today'),  () => applyPreset(todayStr(), todayStr())],
        [t('admin.days7'),  () => applyPreset(daysAgoStr(6), todayStr())],
        [t('admin.days30'), () => applyPreset(daysAgoStr(29), todayStr())],
    ];

    const cols = [
        { key: 'time', label: t('admin.colTime'), render: it => fmtTime(it.timestamp), style: { fontFamily: 'var(--fmono)', whiteSpace: 'nowrap' } },
        { key: 'user', label: t('admin.colUser'), render: it => (
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                <UserLink name={it.username} onUser={onUser} />
                <Badge text={roleLabel(it.user_role)} color={ROLE_COLOR[it.user_role] || 'var(--text3)'} />
            </span>) },
        { key: 'action', label: t('admin.colAction'), render: it => it.action_type, style: { fontFamily: 'var(--fmono)', color: 'var(--text)' } },
        { key: 'detail', label: t('admin.colDetail'), render: it => it.detail, style: { maxWidth: 360 } },
        { key: 'server', label: t('admin.colServer'), render: it => it.target_name, style: { fontFamily: 'var(--fmono)' } },
        { key: 'ip', label: t('admin.colIp'), render: it => it.client_ip, style: { fontFamily: 'var(--fmono)', color: 'var(--text3)' } },
        { key: 'sev', label: t('admin.colSeverity'), render: it => <Badge text={it.severity} color={SEV_COLOR[it.severity] || 'var(--text3)'} />, mobile: 'badge' },
    ];
    // Di HP: aksi sebagai judul kartu, detail tetap terbaca.
    const mobileCols = isMobile ? [cols[2], cols[3], cols[0], cols[1], cols[4], cols[5], cols[6]] : cols;

    return (
        <div style={card}>
            {/* Filters */}
            <div style={{ padding: '10px 12px', borderBottom: '1px solid var(--border)', display: 'flex', flexDirection: 'column', gap: 8 }}>
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
                    <input value={searchInput} onChange={e => { setSearchInput(e.target.value); setPage(1); }}
                        placeholder={t('admin.search')} aria-label={t('admin.search')}
                        style={{ flex: 1, minWidth: 200, ...inp }} />
                    <input value={userInput} onChange={e => { setUserInput(e.target.value); setPage(1); }}
                        placeholder={t('admin.filterUser')} aria-label={t('admin.filterUser')}
                        style={{ width: isMobile ? '100%' : 160, boxSizing: 'border-box', ...inp }} />
                    <select value={action} onChange={e => { setAction(e.target.value); setPage(1); }} style={{ ...inp, maxWidth: isMobile ? '100%' : 230 }}
                        aria-label={t('admin.colAction')}>
                        <option value="">{t('admin.allActions')}</option>
                        {actions.map(a => <option key={a} value={a}>{a}</option>)}
                    </select>
                    <select value={severity} onChange={e => { setSeverity(e.target.value); setPage(1); }} style={inp} aria-label={t('admin.colSeverity')}>
                        <option value="">{t('admin.allSeverity')}</option>
                        <option value="INFO">{t('admin.sevInfo')}</option>
                        <option value="WARNING">{t('admin.sevWarning')}</option>
                        <option value="CRITICAL">{t('admin.sevCritical')}</option>
                    </select>
                    {hasFilter && (
                        <button onClick={resetAll} style={{ ...btn, color: 'var(--red)', borderColor: 'var(--red)44' }}>
                            {t('admin.reset')}
                        </button>
                    )}
                    <ExportButton path="audit-logs/export" params={JSON.parse(key)} />
                </div>
                {isMobile ? (
                    <>
                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                            {[[t('admin.from'), dateFrom, setDateFrom], [t('admin.to'), dateTo, setDateTo]].map(([label, value, set]) => (
                                <label key={label} style={{ fontSize: 11, color: 'var(--text3)', display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0 }}>
                                    {label}
                                    <input type="date" value={value} onChange={e => { set(e.target.value); setPage(1); }}
                                        style={{ ...inp, width: '100%', minWidth: 0, boxSizing: 'border-box' }} />
                                </label>
                            ))}
                        </div>
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 6 }}>
                            {presets.map(([label, fn]) => <button key={label} onClick={fn} style={{ ...btn, padding: '7px 4px' }}>{label}</button>)}
                        </div>
                    </>
                ) : (
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
                    <span style={{ fontSize: 11, color: 'var(--text3)', whiteSpace: 'nowrap' }}>{t('admin.date')}</span>
                    <input type="date" value={dateFrom} onChange={e => { setDateFrom(e.target.value); setPage(1); }}
                        style={inp} title={t('admin.fromDate')} aria-label={t('admin.fromDate')} />
                    <span style={{ fontSize: 11, color: 'var(--text3)' }}>{t('admin.until')}</span>
                    <input type="date" value={dateTo} onChange={e => { setDateTo(e.target.value); setPage(1); }}
                        style={inp} title={t('admin.toDate')} aria-label={t('admin.toDate')} />
                    <div style={{ display: 'flex', gap: 4 }}>
                        {presets.map(([label, fn]) => (
                            <button key={label} onClick={fn} style={{ ...btn, fontSize: 11, padding: '4px 10px' }}>{label}</button>
                        ))}
                    </div>
                    {page !== 1 && <span style={{ fontSize: 11, color: 'var(--text3)', marginLeft: 'auto' }}>{t('admin.autoPaused')}</span>}
                </div>
                )}
                {(dateFrom || dateTo) && (
                    <div style={{ fontSize: 11, color: 'var(--cyan)', fontFamily: 'var(--fmono)' }}>
                        {t('admin.activeFilter', { from: dateFrom || '…', to: dateTo || '…' })}
                        {dateFrom && dateTo && dateFrom === dateTo ? t('admin.wholeDay') : ''}
                    </div>
                )}
            </div>

            <ResponsiveTable cols={mobileCols} items={data.items} rowKey={it => it.id} loading={loading} empty={t('admin.noLogs')} />
            <Pager page={page} totalPages={totalPages} total={data.total} loading={loading} onPrev={() => setPage(p => Math.max(1, p - 1))} onNext={() => setPage(p => Math.min(totalPages, p + 1))} />
        </div>
    );
}

// ── Login gagal ──────────────────────────────────────────────────────────────
function FailedLogins({ onUser }) {
    const t = useT();
    const [days, setDays] = useState(7);
    const [data, setData] = useState(null);
    const [error, setError] = useState('');

    useEffect(() => {
        let alive = true;
        fetchFailedLogins(days)
            .then(d => { if (alive) { setData(d); setError(''); } })
            .catch(e => { if (alive) setError(e?.response?.data?.detail || e.message); });
        return () => { alive = false; };
    }, [days]);

    const userCols = [
        { key: 'user', label: t('admin.colUser'), render: u => (
            <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                {u.known ? <UserLink name={u.username} onUser={onUser} /> : <span style={{ fontFamily: 'var(--fmono)' }}>{u.username}</span>}
                {!u.known && <Badge text={t('admin.unknownAccount')} color="var(--text3)" />}
            </span>) },
        { key: 'n', label: t('admin.colAttempts'), render: u => <b style={{ color: u.attempts >= 10 ? 'var(--red)' : 'var(--yellow)' }}>{u.attempts}</b> },
        { key: 'last', label: t('admin.colLast'), render: u => fmtTime(u.last_at), style: { fontFamily: 'var(--fmono)', whiteSpace: 'nowrap' } },
        { key: 'ips', label: t('admin.colFromIp'), render: u => u.ips.join(', ') || '—', style: { fontFamily: 'var(--fmono)', color: 'var(--text3)' } },
        { key: 'lock', label: t('admin.colStatus'), render: u => u.locked_for > 0
            ? <Badge text={t('admin.lockedFor', { s: u.locked_for })} color="var(--red)" /> : '—', mobile: 'badge' },
    ];
    const ipCols = [
        { key: 'ip', label: t('admin.colIp'), render: r => r.ip, style: { fontFamily: 'var(--fmono)', color: 'var(--text)' } },
        { key: 'n', label: t('admin.colAttempts'), render: r => <b style={{ color: r.attempts >= 10 ? 'var(--red)' : 'var(--yellow)' }}>{r.attempts}</b> },
        { key: 'acc', label: t('admin.colAccounts'), render: r => (
            <span title={r.usernames.join(', ')}>
                {r.accounts}{r.accounts > 1 && <span style={{ color: 'var(--yellow)' }}> ⚠</span>}
                <span style={{ color: 'var(--text3)', fontFamily: 'var(--fmono)' }}> · {r.usernames.slice(0, 4).join(', ')}{r.usernames.length > 4 ? '…' : ''}</span>
            </span>) },
        { key: 'last', label: t('admin.colLast'), render: r => fmtTime(r.last_at), style: { fontFamily: 'var(--fmono)', whiteSpace: 'nowrap' } },
    ];
    const section = { padding: '10px 14px', borderBottom: '1px solid var(--border)', fontSize: 12, color: 'var(--text2)', fontWeight: 600 };

    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                {[1, 7, 30].map(d => (
                    <button key={d} onClick={() => setDays(d)}
                        style={{ ...btn, color: days === d ? 'var(--cyan)' : 'var(--text3)', borderColor: days === d ? 'var(--cyan)66' : 'var(--border)' }}>
                        {t('admin.lastDays', { n: d })}
                    </button>
                ))}
                <span style={{ fontSize: 12, color: 'var(--text2)', marginLeft: 6 }}>
                    {data ? t('admin.failedTotal', { n: data.total, days: data.days }) : t('common.loading')}
                </span>
                <ExportButton path="audit-logs/export" params={() => ({ action: 'AUTH_LOGIN_FAILED', start: new Date(Date.now() - days * 864e5).toISOString() })} />
            </div>
            <div style={{ fontSize: 11, color: 'var(--text3)', lineHeight: 1.6 }}>{t('admin.failedHint')}</div>
            {error && <div role="alert" style={{ fontSize: 12, color: 'var(--red)' }}>⚠ {error}</div>}
            <div style={card}>
                <div style={section}>{t('admin.byAccount')}</div>
                <ResponsiveTable cols={userCols} items={data?.by_user || []} rowKey={u => u.username} loading={!data} empty={t('admin.noFailed')} />
            </div>
            <div style={card}>
                <div style={section}>{t('admin.byIp')}</div>
                <ResponsiveTable cols={ipCols} items={data?.by_ip || []} rowKey={r => r.ip} loading={!data} empty={t('admin.noFailed')} />
            </div>
        </div>
    );
}

// Hasil memutus sesi untuk ditampilkan di dialog.
function describeKill(t, who) {
    return (done) => {
        if (done.sessions) {
            return t('admin.lockDone', { user: who, remote: done.sessions.remote, web: done.sessions.web, ssh: done.sessions.ssh });
        }
        return done.block === 'vm' ? t('admin.vmRevokedDone') : t('admin.killDone');
    };
}

// Pilihan "nonaktifkan akun" untuk sysadmin hanya berlaku bagi akun mahasiswa.
function accountOption(t, role, isSuper) {
    const allowed = isSuper || !role || role === 'student';
    return { value: 'account', label: t('admin.blockAccount'), hint: allowed ? t('admin.blockAccountHint') : t('admin.blockAccountDenied'), disabled: !allowed };
}

// ── Remote Sessions ──────────────────────────────────────────────────────────
function RemoteSessions({ canKill, onUser }) {
    const t = useT();
    const [sub, setSub] = useState('active');
    const [granting, setGranting] = useState(false);
    const [grantResult, setGrantResult] = useState('');

    const doGrantAdmins = async () => {
        if (!confirm(t('admin.grantConfirm'))) return;
        setGranting(true); setGrantResult('');
        try {
            const res = await guacGrantAdmins();
            const ok = Object.values(res.granted || {}).filter(v => v === 'ok').length;
            const total = Object.keys(res.granted || {}).length;
            setGrantResult({ ok: true, text: t('admin.grantDone', { ok, total }) });
        } catch (e) {
            setGrantResult({ ok: false, text: t('admin.failedPrefix', { msg: e?.response?.data?.detail || e.message }) });
        } finally {
            setGranting(false);
        }
    };

    return (
        <div>
            <SubTabs value={sub} onChange={setSub} tabs={[['active', t('admin.subActive')], ['history', t('admin.subHistory')]]}>
                <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
                    {grantResult && <span style={{ fontSize: 11, color: grantResult.ok ? 'var(--green)' : 'var(--red)' }}>{grantResult.text}</span>}
                    {canKill && (
                        <button onClick={doGrantAdmins} disabled={granting}
                            style={{ padding: '5px 14px', borderRadius: 6, fontSize: 11, cursor: 'pointer', fontFamily: 'var(--fmono)',
                                background: 'var(--cyan-glow, #00e5ff22)', color: 'var(--cyan)', border: '1px solid var(--cyan)44',
                                opacity: granting ? 0.6 : 1 }}>
                            {granting ? t('admin.granting') : t('admin.grant')}
                        </button>
                    )}
                </div>
            </SubTabs>
            {sub === 'active' ? <ActiveConnections canKill={canKill} onUser={onUser} /> : <ConnectionHistory onUser={onUser} />}
        </div>
    );
}

const vmCell = (s) => <>{s.vm}{s.os_account && <span style={{ color: 'var(--text3)' }}> @{s.os_account}</span>}</>;

function ActiveConnections({ canKill, onUser }) {
    const t = useT();
    const [sessions, setSessions] = useState([]);
    const [loading, setLoading] = useState(true);
    const [killing, setKilling] = useState(null);

    const load = useCallback(async () => {
        const d = await fetchRemoteSessions();
        setSessions(d.sessions || []); setLoading(false);
    }, []);
    useEffect(() => { load(); const id = setInterval(load, 5000); return () => clearInterval(id); }, [load]);

    const cols = [
        { key: 'user', label: t('admin.colUser'), render: s => <UserLink name={s.username} onUser={onUser} /> },
        { key: 'vm', label: t('admin.colVm'), render: vmCell, style: { fontFamily: 'var(--fmono)', color: 'var(--cyan)' } },
        { key: 'host', label: t('admin.colHost'), render: s => s.host || '—', style: { fontFamily: 'var(--fmono)' } },
        { key: 'proto', label: t('admin.colProtocol'), render: s => s.protocol || '—', style: { fontFamily: 'var(--fmono)' } },
        { key: 'ip', label: t('admin.colClientIp'), render: s => s.remote_host || '—', style: { fontFamily: 'var(--fmono)', color: 'var(--text3)' } },
        { key: 'start', label: t('admin.colStart'), render: s => fmtEpoch(s.start_date), style: { fontFamily: 'var(--fmono)' } },
    ];

    return (
        <div style={card}>
            <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--border)', fontSize: 12, color: 'var(--text2)' }}>
                <span style={{ color: 'var(--green)' }}>●</span> {t('admin.activeCount', { n: sessions.length })}
            </div>
            <ResponsiveTable cols={cols} items={sessions} rowKey={s => s.active_id} loading={loading} empty={t('admin.noActive')}
                actionLabel={t('admin.colActions')}
                action={canKill ? (s, mobile) => <RowKillButton mobile={mobile} onClick={() => setKilling(s)} label={t('admin.kill')} /> : null} />
            {killing && (
                <KillDialog title={t('admin.killTitle')} subject={`${killing.username} → ${killing.vm}${killing.host ? ` · ${killing.host}` : ''}`}
                    confirmLabel={t('admin.kill')}
                    options={[
                        { value: 'none', label: t('admin.blockNone'), hint: t('admin.blockNoneHint') },
                        { value: 'vm', label: t('admin.blockVm'), hint: t('admin.blockVmHint') },
                        accountOption(t, null, true),
                    ]}
                    describe={describeKill(t, killing.username)}
                    onConfirm={(block) => killRemoteSession(killing.active_id, block)}
                    onClose={() => { setKilling(null); load(); }} />
            )}
        </div>
    );
}

function ConnectionHistory({ onUser }) {
    const t = useT();
    const [data, setData] = useState({ total: 0, items: [] });
    const [loading, setLoading] = useState(true);
    const [page, setPage] = useState(1);
    const [searchInput, setSearchInput] = useState('');
    const search = useDebounced(searchInput.trim());

    useEffect(() => {
        let alive = true;
        fetchRemoteHistory(page, PAGE, search ? { search } : {}).then(d => { if (alive) { setData(d); setLoading(false); } });
        return () => { alive = false; };
    }, [page, search]);
    const totalPages = Math.max(1, Math.ceil(data.total / PAGE));

    const cols = [
        { key: 'user', label: t('admin.colUser'), render: h => <UserLink name={h.username} onUser={onUser} /> },
        { key: 'vm', label: t('admin.colVm'), render: vmCell, style: { fontFamily: 'var(--fmono)', color: 'var(--cyan)' } },
        { key: 'host', label: t('admin.colHost'), render: h => h.host || '—', style: { fontFamily: 'var(--fmono)' } },
        { key: 'proto', label: t('admin.colProtocol'), render: h => h.protocol || '—', style: { fontFamily: 'var(--fmono)' } },
        { key: 'ip', label: t('admin.colClientIp'), render: h => h.remote_host || '—', style: { fontFamily: 'var(--fmono)', color: 'var(--text3)' } },
        { key: 'start', label: t('admin.colConnect'), render: h => fmtEpoch(h.start_date), style: { fontFamily: 'var(--fmono)' } },
        { key: 'end', label: t('admin.colDisconnect'), render: h => (h.active ? '—' : fmtEpoch(h.end_date)), style: { fontFamily: 'var(--fmono)' } },
        { key: 'dur', label: t('admin.colDuration'), render: h => fmtDur(h.duration_s), style: { fontFamily: 'var(--fmono)' } },
        { key: 'st', label: t('admin.colStatus'), render: h => (h.active ? <Badge text={t('admin.stActive')} color="var(--green)" /> : <Badge text={t('admin.stEnded')} color="var(--text3)" />), mobile: 'badge' },
    ];

    return (
        <div style={card}>
            <div style={{ padding: '10px 12px', borderBottom: '1px solid var(--border)', display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
                <input value={searchInput} onChange={e => { setSearchInput(e.target.value); setPage(1); }} placeholder={t('admin.remoteSearch')}
                    aria-label={t('admin.remoteSearch')} style={{ flex: 1, minWidth: 200, ...inp }} />
                <ExportButton path="remote/history/export" params={search ? { search } : {}} />
            </div>
            <ResponsiveTable cols={cols} items={data.items} rowKey={h => h.id ?? `${h.start_date}-${h.username}-${h.vm}`} loading={loading} empty={t('admin.noHistory')} />
            <Pager page={page} totalPages={totalPages} total={data.total} loading={loading} onPrev={() => setPage(p => Math.max(1, p - 1))} onNext={() => setPage(p => Math.min(totalPages, p + 1))} />
        </div>
    );
}

// ── Web Sessions (Open Web lewat proxy ke IP privat) ─────────────────────────
function WebSessions({ canKill, isSuper, onUser }) {
    const t = useT();
    const [sub, setSub] = useState('active');
    return (
        <div>
            <SubTabs value={sub} onChange={setSub} tabs={[['active', t('admin.webActive')], ['history', t('admin.webHistory')]]}>
                {sub === 'history' && <span style={{ marginLeft: 'auto' }}><ExportButton path="openweb/history/export" params={{}} /></span>}
            </SubTabs>
            <WebSessionTable key={sub} mode={sub} canKill={canKill} isSuper={isSuper} onUser={onUser} />
        </div>
    );
}

function WebSessionTable({ mode, canKill, isSuper, onUser }) {
    const t = useT();
    const active = mode === 'active';
    const [data, setData] = useState({ total: 0, items: [] });
    const [loading, setLoading] = useState(true);
    const [page, setPage] = useState(1);
    const [killing, setKilling] = useState(null);

    const load = useCallback(async () => {
        if (active) {
            const d = await fetchOpenWebSessions();
            setData({ total: (d.sessions || []).length, items: d.sessions || [] });
        } else {
            setData(await fetchOpenWebHistory(page, PAGE));
        }
        setLoading(false);
    }, [active, page]);

    useEffect(() => {
        load();
        if (!active) return undefined;
        const id = setInterval(load, 5000);
        return () => clearInterval(id);
    }, [load, active]);

    const totalPages = Math.max(1, Math.ceil(data.total / PAGE));
    const cols = [
        { key: 'user', label: t('admin.colUser'), render: s => (
            <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                <UserLink name={s.username} onUser={onUser} />
                <Badge text={roleLabel(s.role)} color={ROLE_COLOR[s.role] || 'var(--text3)'} />
            </span>) },
        { key: 'target', label: t('admin.colTarget'), render: s => s.target_ip, style: { fontFamily: 'var(--fmono)', color: 'var(--cyan)' } },
        { key: 'created', label: t('admin.colCreated'), render: s => fmtTime(s.created_at), style: { fontFamily: 'var(--fmono)' } },
        { key: 'expires', label: t('admin.colExpires'), render: s => fmtTime(s.expires_at), style: { fontFamily: 'var(--fmono)' } },
        { key: 'seen', label: t('admin.colLastSeen'), render: s => (s.last_seen ? fmtTime(s.last_seen) : '—'), style: { fontFamily: 'var(--fmono)' } },
        { key: 'hits', label: t('admin.colHits'), render: s => String(s.hits), style: { fontFamily: 'var(--fmono)' } },
        { key: 'ips', label: t('admin.colClientIps'), render: s => {
            const ips = s.client_ips || [];
            if (!ips.length) return '—';
            return <span title={ips.length > 1 ? t('admin.manyIpsHint') : undefined} style={{ color: ips.length > 1 ? 'var(--yellow)' : 'var(--text3)' }}>{ips.join(', ')}{ips.length > 1 ? t('admin.manyIps') : ''}</span>;
        }, style: { fontFamily: 'var(--fmono)' } },
        { key: 'st', label: t('admin.colStatus'), render: s => {
            const [text, color] = statusOf(WEB_STATUS, s.status);
            return <>
                <Badge text={text} color={color} />
                {s.revoked_by && <div style={{ fontSize: 10, color: 'var(--text3)', marginTop: 2 }}>{t('admin.revokedBy', { name: s.revoked_by })}</div>}
            </>;
        }, mobile: 'badge' },
    ];

    return (
        <div style={card}>
            {active && (
                <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--border)', fontSize: 12, color: 'var(--text2)' }}>
                    <span style={{ color: 'var(--green)' }}>●</span> {t('admin.webCount', { n: data.total })}
                </div>
            )}
            <ResponsiveTable cols={cols} items={data.items} rowKey={s => s.id} loading={loading}
                empty={active ? t('admin.noWeb') : t('admin.noWebHistory')} actionLabel={t('admin.colActions')}
                action={canKill ? (s, mobile) => (s.status === 'active'
                    ? <RowKillButton mobile={mobile} onClick={() => setKilling(s)} label={t('admin.killLink')} /> : null) : null} />
            {!active && <Pager page={page} totalPages={totalPages} total={data.total} loading={loading} onPrev={() => setPage(p => Math.max(1, p - 1))} onNext={() => setPage(p => Math.min(totalPages, p + 1))} />}
            {killing && (
                <KillDialog title={t('admin.revokeTitle')} subject={`${killing.username} → ${killing.target_ip}`}
                    confirmLabel={t('admin.killLink')}
                    options={[
                        { value: 'none', label: t('admin.blockNoneLink'), hint: t('admin.blockNoneLinkHint') },
                        accountOption(t, killing.role, isSuper),
                    ]}
                    describe={describeKill(t, killing.username)}
                    onConfirm={(block) => killOpenWebSession(killing.id, block)}
                    onClose={() => { setKilling(null); load(); }} />
            )}
        </div>
    );
}

// ── SSH Sessions (bastion) ───────────────────────────────────────────────────
function SshSessions({ canKill, isSuper, onUser }) {
    const t = useT();
    const [sub, setSub] = useState('active');
    return (
        <div>
            <SubTabs value={sub} onChange={setSub} tabs={[['active', t('admin.sshActive')], ['history', t('admin.sshHistory')]]}>
                <span style={{ fontSize: 11, color: 'var(--text3)', marginLeft: 6 }}>
                    {t('admin.sshNote')}
                </span>
                {sub === 'history' && <span style={{ marginLeft: 'auto' }}><ExportButton path="ssh/history/export" params={{}} /></span>}
            </SubTabs>
            <SshSessionTable key={sub} mode={sub} canKill={canKill} isSuper={isSuper} onUser={onUser} />
        </div>
    );
}

function SshSessionTable({ mode, canKill, isSuper, onUser }) {
    const t = useT();
    const active = mode === 'active';
    const [data, setData] = useState({ total: 0, items: [] });
    const [loading, setLoading] = useState(true);
    const [page, setPage] = useState(1);
    const [killing, setKilling] = useState(null);
    const [tick, setTick] = useState(0);

    useEffect(() => {
        let alive = true;
        const load = () => (active
            ? fetchSshSessions().then(d => ({ total: (d.sessions || []).length, items: d.sessions || [] }))
            : fetchSshHistory(page, PAGE)
        ).then(d => { if (alive) { setData(d); setLoading(false); } });
        load();
        const id = active ? setInterval(load, 5000) : null;
        return () => { alive = false; if (id) clearInterval(id); };
    }, [active, page, tick]);

    const totalPages = Math.max(1, Math.ceil(data.total / PAGE));
    const goto = (p) => {
        const next = Math.min(totalPages, Math.max(1, p));
        if (next !== page) { setLoading(true); setPage(next); }
    };
    const cols = [
        { key: 'user', label: t('admin.colUser'), render: s => (
            <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                <UserLink name={s.username || '—'} onUser={onUser} />
                {s.role && <Badge text={roleLabel(s.role)} color={ROLE_COLOR[s.role] || 'var(--text3)'} />}
            </span>) },
        { key: 'ip', label: t('admin.colFromIp'), render: s => s.client_ip, style: { fontFamily: 'var(--fmono)' } },
        { key: 'key', label: t('admin.colKey'), render: s => <span title={s.fingerprint}>{s.key_name || <span style={{ fontFamily: 'var(--fmono)' }}>{s.fingerprint.slice(0, 18)}…</span>}</span> },
        { key: 'target', label: t('admin.colTarget'), render: s => {
            const denied = s.denied_targets || [];
            if (!s.targets.length && !denied.length) return '—';
            return <>
                {s.targets.map(tg => <div key={tg.target} style={{ color: 'var(--cyan)' }}>{tg.target}{tg.vm && <span style={{ color: 'var(--text3)' }}> · {tg.vm}</span>}</div>)}
                {denied.map(tg => <div key={tg} style={{ color: 'var(--red)' }} title={t('admin.deniedHint')}>{t('admin.denied', { target: tg })}</div>)}
            </>;
        }, style: { fontFamily: 'var(--fmono)' } },
        { key: 'start', label: t('admin.colStart'), render: s => fmtTime(s.started_at), style: { fontFamily: 'var(--fmono)' } },
        active
            ? { key: 'dur', label: t('admin.colDuration'), render: s => fmtDur(s.duration), style: { fontFamily: 'var(--fmono)' } }
            : { key: 'end', label: t('admin.colEnd'), render: s => <>{fmtTime(s.ended_at)}<div style={{ fontSize: 10, color: 'var(--text3)' }}>{fmtDur(s.duration)}</div></>, style: { fontFamily: 'var(--fmono)' } },
        { key: 'data', label: t('admin.colData'), render: s => (s.bytes_sent == null ? '—' : `${fmtBytes(s.bytes_sent)} / ${fmtBytes(s.bytes_received)}`), style: { fontFamily: 'var(--fmono)' } },
        { key: 'st', label: t('admin.colStatus'), render: s => {
            const [text, color] = statusOf(SSH_STATUS, s.status);
            return <>
                <Badge text={text} color={color} />
                {s.killed_by && s.status === 'killed' && <div style={{ fontSize: 10, color: 'var(--text3)', marginTop: 2 }}>{t('admin.revokedBy', { name: s.killed_by })}</div>}
            </>;
        }, mobile: 'badge' },
    ];

    return (
        <div style={card}>
            {active && (
                <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--border)', fontSize: 12, color: 'var(--text2)' }}>
                    <span style={{ color: 'var(--green)' }}>●</span> {t('admin.sshCount', { n: data.total })}
                </div>
            )}
            <ResponsiveTable cols={cols} items={data.items} rowKey={s => s.id} loading={loading}
                empty={active ? t('admin.noSsh') : t('admin.noSshHistory')} actionLabel={t('admin.colActions')}
                action={canKill && active ? (s, mobile) => <RowKillButton mobile={mobile} onClick={() => setKilling(s)} label={t('admin.kill')} /> : null} />
            {!active && <Pager page={page} totalPages={totalPages} total={data.total} loading={loading} onPrev={() => goto(page - 1)} onNext={() => goto(page + 1)} />}
            {killing && (
                <KillDialog title={t('admin.killSshTitle')} subject={`${killing.username || killing.fingerprint} · ${killing.client_ip}`}
                    confirmLabel={t('admin.kill')}
                    options={[
                        { value: 'none', label: t('admin.blockNone'), hint: t('admin.blockNoneSshHint') },
                        ...(killing.username ? [accountOption(t, killing.role, isSuper)] : []),
                    ]}
                    describe={describeKill(t, killing.username)}
                    onConfirm={(block) => killSshSession(killing.id, block)}
                    onClose={() => { setKilling(null); setTick(x => x + 1); }} />
            )}
        </div>
    );
}
