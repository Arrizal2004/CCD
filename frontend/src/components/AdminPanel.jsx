import { useState, useEffect, useCallback } from 'react';
import { fetchAuditLogs, fetchRemoteSessions, killRemoteSession, fetchRemoteHistory, guacGrantAdmins, fetchOpenWebSessions, fetchOpenWebHistory, killOpenWebSession } from '../api';

const ROLE_COLOR = { superadmin: '#ff6b35', sysadmin: 'var(--cyan)', student: 'var(--purple)' };
const SEV_COLOR = { CRITICAL: 'var(--red)', WARNING: 'var(--yellow)', INFO: 'var(--cyan)' };
const PAGE = 50;

function fmtTime(iso) {
    if (!iso) return '—';
    try {
        return new Date(iso).toLocaleString('id-ID', { timeZone: 'Asia/Jakarta', day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' });
    } catch { return iso; }
}
function fmtEpoch(ms) {
    if (!ms) return '—';
    try { return new Date(ms).toLocaleString('id-ID', { timeZone: 'Asia/Jakarta', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' }); }
    catch { return '—'; }
}
function fmtDur(s) {
    if (s == null) return '—';
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    return h > 0 ? `${h}h ${m}m` : m > 0 ? `${m}m ${sec}s` : `${sec}s`;
}

const TH = { padding: '8px 12px', textAlign: 'left', fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.07em', fontWeight: 600, whiteSpace: 'nowrap', borderBottom: '1px solid var(--border)' };
const TD = { padding: '8px 12px', fontSize: 12, color: 'var(--text2)', borderBottom: '1px solid var(--border)', verticalAlign: 'top' };

function Badge({ text, color }) {
    return <span style={{ fontSize: 10, fontFamily: 'var(--fmono)', padding: '1px 7px', borderRadius: 10, color, background: color + '22', whiteSpace: 'nowrap' }}>{text}</span>;
}

export default function AdminPanel({ currentUser }) {
    const [tab, setTab] = useState('audit');
    const canKill = ['sysadmin', 'superadmin'].includes(currentUser?.role);
    return (
        <div style={{ padding: '14px 20px', maxWidth: 1600, margin: '0 auto' }}>
            <div style={{ display: 'flex', gap: 6, marginBottom: 16 }}>
                {[['audit', '📋 Activity Log'], ['remote', '🖥 Remote Sessions'], ['web', '🌐 Web Sessions']].map(([id, l]) => (
                    <button key={id} onClick={() => setTab(id)}
                        style={{ padding: '7px 16px', borderRadius: 8, fontSize: 13, cursor: 'pointer', fontFamily: 'var(--fmono)',
                            background: tab === id ? 'var(--cyan-glow, #00e5ff22)' : 'var(--bg-card)',
                            color: tab === id ? 'var(--cyan)' : 'var(--text3)',
                            border: `1px solid ${tab === id ? 'var(--cyan)' : 'var(--border)'}` }}>
                        {l}
                    </button>
                ))}
            </div>
            {tab === 'audit' && <ActivityLog />}
            {tab === 'remote' && <RemoteSessions canKill={canKill} />}
            {tab === 'web' && <WebSessions canKill={canKill} />}
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

function ActivityLog() {
    const [data, setData] = useState({ total: 0, items: [] });
    const [loading, setLoading] = useState(true);
    const [page, setPage] = useState(1);
    const [search, setSearch] = useState('');
    const [severity, setSeverity] = useState('');
    const [dateFrom, setDateFrom] = useState('');
    const [dateTo,   setDateTo]   = useState('');

    const load = useCallback(async () => {
        setLoading(true);
        const params = { page, page_size: PAGE };
        if (search)   params.search   = search;
        if (severity) params.severity = severity;
        // dateFrom = start of day local; dateTo = end of day local → full day included
        if (dateFrom) params.start = new Date(dateFrom + 'T00:00:00').toISOString();
        if (dateTo)   params.end   = new Date(dateTo   + 'T23:59:59').toISOString();
        setData(await fetchAuditLogs(params));
        setLoading(false);
    }, [page, search, severity, dateFrom, dateTo]);

    useEffect(() => { load(); }, [load]);
    useEffect(() => { const id = setInterval(load, 15000); return () => clearInterval(id); }, [load]);

    const applyPreset = (from, to) => { setDateFrom(from); setDateTo(to); setPage(1); };
    const resetAll = () => { setSearch(''); setSeverity(''); setDateFrom(''); setDateTo(''); setPage(1); };

    const totalPages = Math.max(1, Math.ceil(data.total / PAGE));
    const hasFilter  = search || severity || dateFrom || dateTo;

    return (
        <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 12, overflow: 'hidden' }}>
            {/* Filters */}
            <div style={{ padding: '10px 12px', borderBottom: '1px solid var(--border)', display: 'flex', flexDirection: 'column', gap: 8 }}>
                {/* Row 1: search + severity + reset */}
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
                    <input value={search} onChange={e => { setSearch(e.target.value); setPage(1); }}
                        placeholder="🔍 Cari user / action / detail / server"
                        style={{ flex: 1, minWidth: 200, ...inp }} />
                    <select value={severity} onChange={e => { setSeverity(e.target.value); setPage(1); }} style={inp}>
                        <option value="">Semua Severity</option>
                        <option value="INFO">Info</option>
                        <option value="WARNING">Warning</option>
                        <option value="CRITICAL">Critical</option>
                    </select>
                    {hasFilter && (
                        <button onClick={resetAll} style={{ ...btn, color: 'var(--red)', borderColor: 'var(--red)44' }}>
                            ✕ Reset
                        </button>
                    )}
                </div>
                {/* Row 2: date range + presets */}
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
                    <span style={{ fontSize: 11, color: 'var(--text3)', whiteSpace: 'nowrap' }}>Tanggal:</span>
                    <input type="date" value={dateFrom} onChange={e => { setDateFrom(e.target.value); setPage(1); }}
                        style={inp} title="Dari tanggal" />
                    <span style={{ fontSize: 11, color: 'var(--text3)' }}>s/d</span>
                    <input type="date" value={dateTo} onChange={e => { setDateTo(e.target.value); setPage(1); }}
                        style={inp} title="Sampai tanggal" />
                    <div style={{ display: 'flex', gap: 4 }}>
                        {[
                            ['Hari Ini',   () => applyPreset(todayStr(), todayStr())],
                            ['7 Hari',     () => applyPreset(daysAgoStr(6), todayStr())],
                            ['30 Hari',    () => applyPreset(daysAgoStr(29), todayStr())],
                        ].map(([label, fn]) => (
                            <button key={label} onClick={fn} style={{ ...btn, fontSize: 11, padding: '4px 10px' }}>{label}</button>
                        ))}
                    </div>
                </div>
                {/* Active filter summary */}
                {(dateFrom || dateTo) && (
                    <div style={{ fontSize: 11, color: 'var(--cyan)', fontFamily: 'var(--fmono)' }}>
                        Filter aktif: {dateFrom || '…'} → {dateTo || '…'}
                        {dateFrom && dateTo && dateFrom === dateTo ? ' (seharian)' : ''}
                    </div>
                )}
            </div>

            <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                    <thead><tr>
                        {['Time', 'User', 'Action', 'Detail', 'Server', 'IP', 'Severity'].map(h => <th key={h} style={TH}>{h}</th>)}
                    </tr></thead>
                    <tbody>
                        {data.items.map(it => (
                            <tr key={it.id}>
                                <td style={{ ...TD, fontFamily: 'var(--fmono)', whiteSpace: 'nowrap' }}>{fmtTime(it.timestamp)}</td>
                                <td style={TD}>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                                        <span style={{ color: 'var(--text)' }}>{it.username}</span>
                                        <Badge text={it.user_role} color={ROLE_COLOR[it.user_role] || 'var(--text3)'} />
                                    </div>
                                </td>
                                <td style={{ ...TD, fontFamily: 'var(--fmono)', color: 'var(--text)' }}>{it.action_type}</td>
                                <td style={{ ...TD, maxWidth: 360 }}>{it.detail}</td>
                                <td style={{ ...TD, fontFamily: 'var(--fmono)' }}>{it.target_name}</td>
                                <td style={{ ...TD, fontFamily: 'var(--fmono)', color: 'var(--text3)' }}>{it.client_ip}</td>
                                <td style={TD}><Badge text={it.severity} color={SEV_COLOR[it.severity] || 'var(--text3)'} /></td>
                            </tr>
                        ))}
                        {!loading && data.items.length === 0 && (
                            <tr><td colSpan={7} style={{ ...TD, textAlign: 'center', color: 'var(--text3)', padding: 30 }}>Tidak ada log</td></tr>
                        )}
                    </tbody>
                </table>
            </div>

            <Pager page={page} totalPages={totalPages} total={data.total} loading={loading} onPrev={() => setPage(p => Math.max(1, p - 1))} onNext={() => setPage(p => Math.min(totalPages, p + 1))} />
        </div>
    );
}

// ── Remote Sessions ──────────────────────────────────────────────────────────
function RemoteSessions({ canKill }) {
    const [sub, setSub] = useState('active');
    const [granting, setGranting] = useState(false);
    const [grantResult, setGrantResult] = useState('');

    const doGrantAdmins = async () => {
        if (!confirm('Grant semua koneksi Guacamole ke seluruh user admin/sysadmin/superadmin yang aktif?')) return;
        setGranting(true); setGrantResult('');
        try {
            const res = await guacGrantAdmins();
            const ok = Object.values(res.granted || {}).filter(v => v === 'ok').length;
            const total = Object.keys(res.granted || {}).length;
            setGrantResult(`Selesai: ${ok}/${total} user berhasil di-grant`);
        } catch (e) {
            setGrantResult('Gagal: ' + (e?.response?.data?.detail || e.message));
        } finally {
            setGranting(false);
        }
    };

    return (
        <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 12, flexWrap: 'wrap' }}>
                {[['active', 'Active Connections'], ['history', 'Connection History']].map(([id, l]) => (
                    <button key={id} onClick={() => setSub(id)}
                        style={{ padding: '5px 14px', borderRadius: 6, fontSize: 12, cursor: 'pointer', fontFamily: 'var(--fmono)',
                            background: sub === id ? 'var(--bg-hover)' : 'transparent', color: sub === id ? 'var(--cyan)' : 'var(--text3)',
                            border: `1px solid ${sub === id ? 'var(--cyan)44' : 'var(--border)'}` }}>{l}</button>
                ))}
                <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
                    {grantResult && <span style={{ fontSize: 11, color: grantResult.startsWith('Gagal') ? 'var(--red)' : 'var(--green)' }}>{grantResult}</span>}
                    {canKill && (
                        <button onClick={doGrantAdmins} disabled={granting}
                            style={{ padding: '5px 14px', borderRadius: 6, fontSize: 11, cursor: 'pointer', fontFamily: 'var(--fmono)',
                                background: 'var(--cyan-glow, #00e5ff22)', color: 'var(--cyan)', border: '1px solid var(--cyan)44',
                                opacity: granting ? 0.6 : 1 }}>
                            {granting ? '⏳ Syncing...' : '⚡ Grant Guac ke Admin'}
                        </button>
                    )}
                </div>
            </div>
            {sub === 'active' ? <ActiveConnections canKill={canKill} /> : <ConnectionHistory />}
        </div>
    );
}

function ActiveConnections({ canKill }) {
    const [sessions, setSessions] = useState([]);
    const [loading, setLoading] = useState(true);
    const [killing, setKilling] = useState('');

    const load = useCallback(async () => {
        const d = await fetchRemoteSessions();
        setSessions(d.sessions || []); setLoading(false);
    }, []);
    useEffect(() => { load(); const id = setInterval(load, 5000); return () => clearInterval(id); }, [load]);

    const kill = async (s) => {
        if (!confirm(`Putuskan sesi ${s.username} → ${s.vm}?`)) return;
        setKilling(s.active_id);
        try { await killRemoteSession(s.active_id); await load(); }
        catch (e) { alert('Gagal: ' + (e?.response?.data?.detail || e.message)); }
        finally { setKilling(''); }
    };

    return (
        <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 12, overflow: 'hidden' }}>
            <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--border)', fontSize: 12, color: 'var(--text2)' }}>
                <span style={{ color: 'var(--green)' }}>●</span> {sessions.length} sesi aktif · auto-refresh 5s
            </div>
            <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                    <thead><tr>{['User', 'VM Target', 'Host', 'Client IP', 'Start', canKill ? 'Aksi' : ''].filter(Boolean).map(h => <th key={h} style={TH}>{h}</th>)}</tr></thead>
                    <tbody>
                        {sessions.map(s => (
                            <tr key={s.active_id}>
                                <td style={{ ...TD, color: 'var(--text)' }}>{s.username}</td>
                                <td style={{ ...TD, fontFamily: 'var(--fmono)', color: 'var(--cyan)' }}>{s.vm}</td>
                                <td style={{ ...TD, fontFamily: 'var(--fmono)' }}>{s.host || '—'}</td>
                                <td style={{ ...TD, fontFamily: 'var(--fmono)', color: 'var(--text3)' }}>{s.remote_host || '—'}</td>
                                <td style={{ ...TD, fontFamily: 'var(--fmono)' }}>{fmtEpoch(s.start_date)}</td>
                                {canKill && (
                                    <td style={TD}>
                                        <button onClick={() => kill(s)} disabled={killing === s.active_id}
                                            style={{ padding: '4px 12px', borderRadius: 6, fontSize: 11, fontFamily: 'var(--fmono)', cursor: 'pointer',
                                                background: 'var(--red-glow, #ff174422)', color: 'var(--red)', border: '1px solid var(--red)' }}>
                                            {killing === s.active_id ? '⏳' : '⛔ Kill Session'}
                                        </button>
                                    </td>
                                )}
                            </tr>
                        ))}
                        {!loading && sessions.length === 0 && (
                            <tr><td colSpan={6} style={{ ...TD, textAlign: 'center', color: 'var(--text3)', padding: 30 }}>Tidak ada sesi remote aktif</td></tr>
                        )}
                    </tbody>
                </table>
            </div>
        </div>
    );
}

function ConnectionHistory() {
    const [data, setData] = useState({ total: 0, items: [] });
    const [loading, setLoading] = useState(true);
    const [page, setPage] = useState(1);

    const load = useCallback(async () => {
        setLoading(true); setData(await fetchRemoteHistory(page, PAGE)); setLoading(false);
    }, [page]);
    useEffect(() => { load(); }, [load]);
    const totalPages = Math.max(1, Math.ceil(data.total / PAGE));

    return (
        <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 12, overflow: 'hidden' }}>
            <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                    <thead><tr>{['User', 'VM Target', 'Host', 'Connect', 'Disconnect', 'Durasi', 'Status'].map(h => <th key={h} style={TH}>{h}</th>)}</tr></thead>
                    <tbody>
                        {data.items.map((h, i) => (
                            <tr key={i}>
                                <td style={{ ...TD, color: 'var(--text)' }}>{h.username}</td>
                                <td style={{ ...TD, fontFamily: 'var(--fmono)', color: 'var(--cyan)' }}>{h.vm}</td>
                                <td style={{ ...TD, fontFamily: 'var(--fmono)' }}>{h.host || '—'}</td>
                                <td style={{ ...TD, fontFamily: 'var(--fmono)' }}>{fmtEpoch(h.start_date)}</td>
                                <td style={{ ...TD, fontFamily: 'var(--fmono)' }}>{h.active ? '—' : fmtEpoch(h.end_date)}</td>
                                <td style={{ ...TD, fontFamily: 'var(--fmono)' }}>{fmtDur(h.duration_s)}</td>
                                <td style={TD}>{h.active ? <Badge text="ACTIVE" color="var(--green)" /> : <Badge text="ENDED" color="var(--text3)" />}</td>
                            </tr>
                        ))}
                        {!loading && data.items.length === 0 && (
                            <tr><td colSpan={7} style={{ ...TD, textAlign: 'center', color: 'var(--text3)', padding: 30 }}>Tidak ada history koneksi</td></tr>
                        )}
                    </tbody>
                </table>
            </div>
            <Pager page={page} totalPages={totalPages} total={data.total} loading={loading} onPrev={() => setPage(p => Math.max(1, p - 1))} onNext={() => setPage(p => Math.min(totalPages, p + 1))} />
        </div>
    );
}

function Pager({ page, totalPages, total, loading, onPrev, onNext }) {
    return (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 14px', fontSize: 11, color: 'var(--text3)', fontFamily: 'var(--fmono)' }}>
            <span>{loading ? 'Memuat...' : `${total} total · hal ${page}/${totalPages}`}</span>
            <div style={{ display: 'flex', gap: 6 }}>
                <button onClick={onPrev} disabled={page <= 1} style={{ ...btn, opacity: page <= 1 ? 0.4 : 1 }}>‹ Prev</button>
                <button onClick={onNext} disabled={page >= totalPages} style={{ ...btn, opacity: page >= totalPages ? 0.4 : 1 }}>Next ›</button>
            </div>
        </div>
    );
}

const inp = { background: 'var(--bg-hover)', border: '1px solid var(--border)', borderRadius: 6, padding: '6px 10px', color: 'var(--text)', fontSize: 12, fontFamily: 'var(--fmono)', outline: 'none' };
const btn = { background: 'var(--bg-hover)', border: '1px solid var(--border)', borderRadius: 6, padding: '6px 12px', color: 'var(--text2)', fontSize: 12, cursor: 'pointer', fontFamily: 'var(--fmono)' };

// ── Web Sessions (Open Web lewat proxy ke IP privat) ─────────────────────────
const WEB_STATUS = { active: ['ACTIVE', 'var(--green)'], expired: ['EXPIRED', 'var(--text3)'], revoked: ['REVOKED', 'var(--red)'] };

function WebSessions({ canKill }) {
    const [sub, setSub] = useState('active');
    return (
        <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 12, flexWrap: 'wrap' }}>
                {[['active', 'Active Links'], ['history', 'Link History']].map(([id, l]) => (
                    <button key={id} onClick={() => setSub(id)}
                        style={{ padding: '5px 14px', borderRadius: 6, fontSize: 12, cursor: 'pointer', fontFamily: 'var(--fmono)',
                            background: sub === id ? 'var(--bg-hover)' : 'transparent', color: sub === id ? 'var(--cyan)' : 'var(--text3)',
                            border: `1px solid ${sub === id ? 'var(--cyan)44' : 'var(--border)'}` }}>{l}</button>
                ))}
            </div>
            <WebSessionTable mode={sub} canKill={canKill} />
        </div>
    );
}

function WebSessionTable({ mode, canKill }) {
    const active = mode === 'active';
    const [data, setData] = useState({ total: 0, items: [] });
    const [loading, setLoading] = useState(true);
    const [page, setPage] = useState(1);
    const [killing, setKilling] = useState('');

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
        setLoading(true);
        load();
        if (!active) return undefined;
        const id = setInterval(load, 5000);
        return () => clearInterval(id);
    }, [load, active]);

    const kill = async (s) => {
        if (!confirm(`Cabut link ${s.username} → ${s.target_ip}? Link langsung berhenti bekerja.`)) return;
        setKilling(s.id);
        try { await killOpenWebSession(s.id); await load(); }
        catch (e) { alert('Gagal: ' + (e?.response?.data?.detail || e.message)); }
        finally { setKilling(''); }
    };

    const totalPages = Math.max(1, Math.ceil(data.total / PAGE));
    const heads = ['User', 'Target', 'Dibuat', 'Berlaku s/d', 'Akses terakhir', 'Request', 'IP Pengakses', 'Status', canKill ? 'Aksi' : ''].filter(Boolean);

    return (
        <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 12, overflow: 'hidden' }}>
            {active && (
                <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--border)', fontSize: 12, color: 'var(--text2)' }}>
                    <span style={{ color: 'var(--green)' }}>●</span> {data.total} link aktif · auto-refresh 5s
                </div>
            )}
            <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                    <thead><tr>{heads.map(h => <th key={h} style={TH}>{h}</th>)}</tr></thead>
                    <tbody>
                        {data.items.map(s => {
                            const [stText, stColor] = WEB_STATUS[s.status] || [s.status, 'var(--text3)'];
                            const ips = s.client_ips || [];
                            return (
                                <tr key={s.id}>
                                    <td style={TD}>
                                        <span style={{ color: 'var(--text)' }}>{s.username}</span>{' '}
                                        <Badge text={s.role} color={ROLE_COLOR[s.role] || 'var(--text3)'} />
                                    </td>
                                    <td style={{ ...TD, fontFamily: 'var(--fmono)', color: 'var(--cyan)' }}>{s.target_ip}</td>
                                    <td style={{ ...TD, fontFamily: 'var(--fmono)' }}>{fmtTime(s.created_at)}</td>
                                    <td style={{ ...TD, fontFamily: 'var(--fmono)' }}>{fmtTime(s.expires_at)}</td>
                                    <td style={{ ...TD, fontFamily: 'var(--fmono)' }}>{fmtTime(s.last_seen)}</td>
                                    <td style={{ ...TD, fontFamily: 'var(--fmono)' }}>{s.hits}</td>
                                    <td style={{ ...TD, fontFamily: 'var(--fmono)', color: ips.length > 1 ? 'var(--yellow)' : 'var(--text3)' }}
                                        title={ips.length > 1 ? 'Diakses dari lebih dari satu IP — kemungkinan link dibagikan' : undefined}>
                                        {ips.length ? ips.join(', ') : '—'}{ips.length > 1 ? ' ⚠' : ''}
                                    </td>
                                    <td style={TD}>
                                        <Badge text={stText} color={stColor} />
                                        {s.revoked_by && <div style={{ fontSize: 10, color: 'var(--text3)', marginTop: 2 }}>oleh {s.revoked_by}</div>}
                                    </td>
                                    {canKill && (
                                        <td style={TD}>
                                            {s.status === 'active' && (
                                                <button onClick={() => kill(s)} disabled={killing === s.id}
                                                    style={{ padding: '4px 12px', borderRadius: 6, fontSize: 11, fontFamily: 'var(--fmono)', cursor: 'pointer',
                                                        background: 'var(--red-glow, #ff174422)', color: 'var(--red)', border: '1px solid var(--red)' }}>
                                                    {killing === s.id ? '⏳' : '⛔ Kill Link'}
                                                </button>
                                            )}
                                        </td>
                                    )}
                                </tr>
                            );
                        })}
                        {!loading && data.items.length === 0 && (
                            <tr><td colSpan={heads.length} style={{ ...TD, textAlign: 'center', color: 'var(--text3)', padding: 30 }}>
                                {active ? 'Tidak ada link Open Web aktif' : 'Belum ada riwayat link Open Web'}
                            </td></tr>
                        )}
                    </tbody>
                </table>
            </div>
            {!active && <Pager page={page} totalPages={totalPages} total={data.total} loading={loading} onPrev={() => setPage(p => Math.max(1, p - 1))} onNext={() => setPage(p => Math.min(totalPages, p + 1))} />}
        </div>
    );
}
