import { useEffect, useState } from 'react';
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts';
import { locale, useT } from '../i18n';
import { fetchVpsLive, fetchVpsHistory } from '../api';
import { formatBytes, formatUptime } from '../format';

// Resource VPS tempat dashboard berjalan (bukan node Proxmox). Live: sampel tiap 5 detik dari
// memori backend. Riwayat: rata-rata dan puncak per menit dari tabel vps_metrics, disimpan 30 hari.
const RANGES = [['live', 'vps.range15m'], ['1h', 'vps.range1h'], ['24h', 'vps.range24h'], ['7d', 'vps.range7d'], ['30d', 'vps.range30d']];
const LIVE_MS = 5000;
const HISTORY_MS = 60000;

const pct = (used, total) => (total ? Math.round((1000 * used) / total) / 10 : 0);
const level = (p) => (p >= 85 ? 'var(--red)' : p >= 70 ? 'var(--yellow)' : 'var(--green)');
const rate = (bps) => (bps == null ? '—' : `${formatBytes(bps)}/s`);

// Tambahkan persentase RAM supaya grafik live dan riwayat memakai kunci yang sama.
const withPct = (p) => ({ ...p, mem_pct: pct(p.mem_used, p.mem_total), mem_max_pct: p.mem_max != null ? pct(p.mem_max, p.mem_total) : undefined });

function fmtTick(range) {
    return (ms) => {
        const d = new Date(ms);
        const p = (n) => String(n).padStart(2, '0');
        if (range === 'live' || range === '1h' || range === '24h') return `${p(d.getHours())}:${p(d.getMinutes())}`;
        return `${p(d.getDate())}/${p(d.getMonth() + 1)}`;
    };
}

function Spark({ data, k, color }) {
    return (
        <ResponsiveContainer width="100%" height={30}>
            <LineChart data={data} margin={{ top: 4, right: 0, bottom: 0, left: 0 }}>
                <YAxis hide domain={[0, 'auto']} />
                <Line type="monotone" dataKey={k} stroke={color} strokeWidth={1.4} dot={false} isAnimationActive={false} />
            </LineChart>
        </ResponsiveContainer>
    );
}

function Stat({ label, value, sub, color, children }) {
    return (
        <div style={{ background: 'var(--bg-card2)', border: '1px solid var(--border)', borderRadius: 8, padding: '10px 12px', minWidth: 0 }}>
            <div style={{ fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>{label}</div>
            <div style={{ fontSize: 18, fontWeight: 600, color: color || 'var(--text)', fontFamily: 'var(--fmono)', marginTop: 2, whiteSpace: 'nowrap' }}>{value}</div>
            {sub && <div style={{ fontSize: 11, color: 'var(--text3)', fontFamily: 'var(--fmono)', overflowWrap: 'anywhere' }}>{sub}</div>}
            {children}
        </div>
    );
}

function Chart({ title, data, lines, range, fmt, domain }) {
    return (
        <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 4 }}>{title}</div>
            <ResponsiveContainer width="100%" height={140}>
                <LineChart data={data} margin={{ top: 4, right: 10, bottom: 0, left: -4 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" opacity={0.3} />
                    <XAxis dataKey="t" type="number" scale="time" domain={['dataMin', 'dataMax']} tickFormatter={fmtTick(range)}
                        tick={{ fontSize: 9, fill: 'var(--text3)' }} minTickGap={30} />
                    <YAxis tick={{ fontSize: 9, fill: 'var(--text3)' }} width={52} domain={domain || [0, 'auto']} tickFormatter={fmt} />
                    <Tooltip contentStyle={{ background: 'var(--bg-card2)', border: '1px solid var(--border)', borderRadius: 6, fontSize: 11 }}
                        labelFormatter={(ms) => new Date(ms).toLocaleString(locale())} formatter={(v, name) => [fmt ? fmt(v) : v, name]} />
                    {lines.map(l => (
                        <Line key={l.key} type="monotone" dataKey={l.key} name={l.name} stroke={l.color} strokeWidth={1.6}
                            strokeDasharray={l.dashed ? '4 3' : undefined} dot={false} isAnimationActive={false} connectNulls />
                    ))}
                </LineChart>
            </ResponsiveContainer>
        </div>
    );
}

export default function VpsResourcePanel() {
    const t = useT();
    const [live, setLive] = useState(null);
    const [error, setError] = useState('');
    const [range, setRange] = useState('live');
    const [hist, setHist] = useState({ since: null, points: [] });

    useEffect(() => {
        let alive = true;
        const tick = () => fetchVpsLive()
            .then(d => { if (alive) { setLive(d); setError(''); } })
            .catch(e => { if (alive) setError(e?.response?.data?.detail || t('vps.loadFailed')); });
        tick();
        const id = setInterval(tick, LIVE_MS);
        return () => { alive = false; clearInterval(id); };
    }, [t]);

    useEffect(() => {
        if (range === 'live') return undefined;
        let alive = true;
        const tick = () => fetchVpsHistory(range).then(d => { if (alive) setHist(d); }).catch(() => {});
        tick();
        const id = setInterval(tick, HISTORY_MS);
        return () => { alive = false; clearInterval(id); };
    }, [range]);

    if (error) return <div style={{ padding: 16, fontSize: 12, color: 'var(--red)' }}>⚠ {error}</div>;
    if (!live) return <div style={{ padding: 16, fontSize: 12, color: 'var(--text3)' }}>{t('vps.loading')}</div>;

    const cur = live.current;
    const recent = live.recent.map(withPct);
    const data = range === 'live' ? recent : hist.points.map(withPct);
    const isHistory = range !== 'live';
    const memPct = cur ? pct(cur.mem_used, cur.mem_total) : 0;
    const diskPct = cur ? pct(cur.disk_used, cur.disk_total) : 0;
    const loadPct = cur ? (100 * cur.load1) / (live.cpus || 1) : 0;

    return (
        <div style={{ padding: 16 }}>
            {!cur ? (
                <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 12 }}>{t('vps.firstSample')}</div>
            ) : (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 10, marginBottom: 16 }}>
                    <Stat label="CPU" value={`${cur.cpu}%`} color={level(cur.cpu)} sub={t('vps.cpuSub', { n: live.cpus, io: cur.iowait })}>
                        <Spark data={recent} k="cpu" color="var(--cyan)" />
                    </Stat>
                    <Stat label="RAM" value={`${memPct}%`} color={level(memPct)} sub={`${formatBytes(cur.mem_used)} / ${formatBytes(cur.mem_total)}`}>
                        <Spark data={recent} k="mem_pct" color="var(--purple)" />
                    </Stat>
                    <Stat label="Disk" value={`${diskPct}%`} color={level(diskPct)} sub={`${formatBytes(cur.disk_used)} / ${formatBytes(cur.disk_total)}`}>
                        <div style={{ height: 6, borderRadius: 3, background: 'var(--bg-hover)', marginTop: 12, overflow: 'hidden' }}>
                            <div style={{ width: `${diskPct}%`, height: '100%', background: level(diskPct) }} />
                        </div>
                    </Stat>
                    <Stat label="Load" value={cur.load1.toFixed(2)} color={level(loadPct)} sub={`5m ${cur.load5.toFixed(2)} · 15m ${cur.load15.toFixed(2)}`}>
                        <Spark data={recent} k="load1" color="var(--yellow)" />
                    </Stat>
                    <Stat label={t('vps.network')} value={`↓ ${rate(cur.net_rx)}`} sub={live.net_available ? `↑ ${rate(cur.net_tx)}` : t('vps.noNetDev')}>
                        {live.net_available && <Spark data={recent} k="net_rx" color="var(--green)" />}
                    </Stat>
                    <Stat label="Uptime" value={formatUptime(live.uptime)}
                        sub={cur.swap_total ? `swap ${formatBytes(cur.swap_used)} / ${formatBytes(cur.swap_total)}` : t('vps.noSwap')} />
                </div>
            )}

            <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', marginBottom: 12 }}>
                {RANGES.map(([id, label]) => (
                    <button key={id} onClick={() => setRange(id)}
                        style={{ padding: '5px 12px', borderRadius: 6, fontSize: 12, cursor: 'pointer', fontFamily: 'var(--fmono)',
                            background: range === id ? 'var(--bg-hover)' : 'transparent', color: range === id ? 'var(--cyan)' : 'var(--text3)',
                            border: `1px solid ${range === id ? 'var(--cyan)44' : 'var(--border)'}` }}>{t(label)}</button>
                ))}
                <span style={{ fontSize: 11, color: 'var(--text3)' }}>
                    {isHistory
                        ? (hist.since ? t('vps.histInfo', { period: t(range === '1h' ? 'vps.avgMinute' : range === '24h' ? 'vps.avg5m' : range === '7d' ? 'vps.avg30m' : 'vps.avg2h'), since: new Date(hist.since).toLocaleString(locale()) }) : t('vps.histEmpty'))
                        : t('vps.liveInfo')}
                </span>
            </div>

            {data.length === 0 ? (
                <div style={{ fontSize: 12, color: 'var(--text3)', padding: '20px 0', textAlign: 'center' }}>{t('vps.noData')}</div>
            ) : (
                <div className="ccd-stack-mobile" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                    <Chart title="CPU (%)" data={data} range={range} domain={[0, 100]} fmt={v => `${Math.round(v)}%`}
                        lines={[{ key: 'cpu', name: t('vps.avg'), color: 'var(--cyan)' }, ...(isHistory ? [{ key: 'cpu_max', name: t('vps.peak'), color: 'var(--cyan)', dashed: true }] : [])]} />
                    <Chart title="RAM (%)" data={data} range={range} domain={[0, 100]} fmt={v => `${Math.round(v)}%`}
                        lines={[{ key: 'mem_pct', name: t('vps.avg'), color: 'var(--purple)' }, ...(isHistory ? [{ key: 'mem_max_pct', name: t('vps.peak'), color: 'var(--purple)', dashed: true }] : [])]} />
                    <Chart title={t('vps.network')} data={data} range={range} fmt={v => rate(v)}
                        lines={[{ key: 'net_rx', name: t('vps.in'), color: 'var(--green)' }, { key: 'net_tx', name: t('vps.out'), color: 'var(--yellow)' }]} />
                    <Chart title={t('vps.loadTitle', { n: live.cpus })} data={data} range={range} fmt={v => Number(v).toFixed(1)}
                        lines={[{ key: 'load1', name: t('vps.load1'), color: 'var(--yellow)' }]} />
                </div>
            )}
        </div>
    );
}
