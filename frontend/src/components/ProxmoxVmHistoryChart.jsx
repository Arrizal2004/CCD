import { useState, useEffect, useCallback } from 'react';
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts';
import { fetchProxmoxRrddata, fetchProxmoxIops } from '../api';
import { formatBytes } from '../format';

// CPU/Memory/Network datang dari RRD bawaan Proxmox, yang cuma punya 3 preset (hour/day/week) dengan
// resolusi tetap (hour ≈ 1 titik/menit, day ≈ 1 titik/30menit, week ≈ 1 titik/3jam) — Proxmox tidak
// punya endpoint untuk data lebih detail dari itu. Jendela pendek (1m-1h) karena itu memakai `rrdSource:
// 'hour'` yang sama lalu di-crop di browser; 6h/24h memakai 'day'; 7d memakai 'week'. Disk IOPS beda —
// itu sampling milik CCD sendiri tiap 15 detik (services/proxmox_iops_poller.py), jadi jendela pendek
// di situ benar-benar dapat resolusi lebih halus dari backend (lihat _IOPS_WINDOWS di routers/proxmox.py).
const TIMEFRAMES = [
    { id: '1m',  label: '1m',  ms: 60_000,          rrdSource: 'hour', live: true },
    { id: '5m',  label: '5m',  ms: 5 * 60_000,      rrdSource: 'hour', live: true },
    { id: '10m', label: '10m', ms: 10 * 60_000,     rrdSource: 'hour', live: true },
    { id: '30m', label: '30m', ms: 30 * 60_000,     rrdSource: 'hour', live: true },
    { id: '1h',  label: '1h',  ms: 60 * 60_000,     rrdSource: 'hour', live: false },
    { id: '6h',  label: '6h',  ms: 6 * 3600_000,    rrdSource: 'day',  live: false },
    { id: '24h', label: '24h', ms: 24 * 3600_000,   rrdSource: 'day',  live: false },
    { id: '7d',  label: '7d',  ms: 7 * 86400_000,   rrdSource: 'week', live: false },
];
const LIVE_REFRESH_MS = 10_000; // ~2/3 dari interval sampling IOPS (15s) — cukup cepat tanpa membebani

const tfById = (id) => TIMEFRAMES.find(t => t.id === id) || TIMEFRAMES[4];

function fmtTick(tf) {
    return (ms) => {
        const d = new Date(ms);
        if (isNaN(d)) return '';
        const p = (n) => String(n).padStart(2, '0');
        if (tf.ms <= 3600_000) return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
        if (tf.ms <= 86400_000) return `${p(d.getHours())}:${p(d.getMinutes())}`;
        return `${p(d.getMonth() + 1)}-${p(d.getDate())}`;
    };
}

function Chart({ title, data, lines, tf, valueFmt }) {
    return (
        <div style={{ marginBottom: 14 }}>
            <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 4 }}>{title}</div>
            <ResponsiveContainer width="100%" height={120}>
                <LineChart data={data} margin={{ top: 4, right: 12, bottom: 0, left: -6 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" opacity={0.3} />
                    <XAxis dataKey="t" tickFormatter={fmtTick(tf)} tick={{ fontSize: 9, fill: 'var(--text3)' }} minTickGap={30} />
                    <YAxis tick={{ fontSize: 9, fill: 'var(--text3)' }} width={44}
                        tickFormatter={valueFmt ? (v) => valueFmt(v) : undefined} />
                    <Tooltip
                        contentStyle={{ background: 'var(--bg-card2)', border: '1px solid var(--border)', borderRadius: 6, fontSize: 11 }}
                        labelFormatter={(ms) => new Date(ms).toLocaleString('en-GB')}
                        formatter={(v, name) => [valueFmt ? valueFmt(v) : v, name]} />
                    {lines.map(l => (
                        <Line key={l.key} type="monotone" dataKey={l.key} name={l.name} stroke={l.color} strokeWidth={1.6} dot={false} isAnimationActive={false} />
                    ))}
                </LineChart>
            </ResponsiveContainer>
        </div>
    );
}

export default function ProxmoxVmHistoryChart({ instance, node, vmid }) {
    const [timeframeId, setTimeframeId] = useState('1h');
    const tf = tfById(timeframeId);
    const [data, setData] = useState([]);
    const [iops, setIops] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [lastUpdated, setLastUpdated] = useState(null);

    const load = useCallback(async (silent = false) => {
        if (!silent) setLoading(true);
        try {
            const [raw, io] = await Promise.all([
                fetchProxmoxRrddata(instance, node, vmid, tf.rrdSource),
                fetchProxmoxIops(instance, node, vmid, tf.id).catch(() => []),
            ]);
            const cutoff = Date.now() - tf.ms;
            setIops(io
                .map(p => ({ t: p.time * 1000, read_iops: p.read_iops, write_iops: p.write_iops }))
                .filter(p => p.t >= cutoff));
            setData(raw
                .map(p => ({
                    t: (p.time || 0) * 1000,
                    cpu_pct: p.cpu != null ? +(p.cpu * 100).toFixed(2) : null,
                    mem_mb: p.mem != null ? Math.round(p.mem / 1024 / 1024) : null,
                    netin_kbps: p.netin != null ? Math.round(p.netin / 1024) : null,
                    netout_kbps: p.netout != null ? Math.round(p.netout / 1024) : null,
                }))
                .filter(p => p.t >= cutoff));
            setError(null);
            setLastUpdated(new Date());
        } catch (e) {
            if (!silent) setError(e?.response?.data?.detail || 'Gagal mengambil data historis');
        } finally {
            if (!silent) setLoading(false);
        }
    }, [instance, node, vmid, tf.id, tf.rrdSource, tf.ms]);

    useEffect(() => { load(); }, [load]);

    // Auto-refresh untuk jendela pendek (1m-30m) — trace performa "live" tanpa klik ulang.
    useEffect(() => {
        if (!tf.live) return;
        const iv = setInterval(() => load(true), LIVE_REFRESH_MS);
        return () => clearInterval(iv);
    }, [tf.live, load]);

    return (
        <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10, flexWrap: 'wrap', gap: 6 }}>
                <div style={{ fontSize: 10, color: 'var(--text3)' }}>
                    {tf.live && <span style={{ color: 'var(--green)' }}>● live</span>}
                    {lastUpdated && <span> · update {lastUpdated.toLocaleTimeString('id-ID')}</span>}
                </div>
                <div style={{ display: 'inline-flex', gap: 2, background: 'var(--bg-hover)', borderRadius: 7, padding: 2 }}>
                    {TIMEFRAMES.map(t => (
                        <button key={t.id} onClick={() => setTimeframeId(t.id)}
                            style={{
                                padding: '4px 10px', borderRadius: 5, fontSize: 11, fontFamily: 'var(--fmono)', cursor: 'pointer', border: 'none',
                                background: timeframeId === t.id ? 'var(--cyan)' : 'transparent',
                                color: timeframeId === t.id ? '#000' : 'var(--text3)',
                                fontWeight: timeframeId === t.id ? 700 : 400,
                            }}>
                            {t.label}
                        </button>
                    ))}
                </div>
            </div>

            {loading && <div style={{ color: 'var(--text3)', fontSize: 12, padding: 10 }}>Loading…</div>}
            {error && (
                <div style={{ background: 'var(--red-glow)', border: '1px solid var(--red)', borderRadius: 6, padding: '6px 10px', color: 'var(--red)', fontSize: 11, marginBottom: 12 }}>⚠ {error}</div>
            )}

            {!loading && !error && (
                <>
                    {tf.ms <= 10 * 60_000 && (
                        <div style={{ fontSize: 10, color: 'var(--text3)', marginBottom: 10, lineHeight: 1.5 }}>
                            CPU/Memory/Network dibatasi resolusi Proxmox sendiri (maksimal ±1 titik/menit) — di jendela sependek ini
                            mungkin cuma kelihatan 1-2 titik. Disk IOPS di bawah lebih detail karena datanya sampling milik dashboard sendiri tiap 15 detik.
                        </div>
                    )}
                    <Chart title="CPU Usage (%)" data={data} tf={tf}
                        lines={[{ key: 'cpu_pct', name: 'CPU %', color: 'var(--cyan)' }]}
                        valueFmt={v => `${v}%`} />
                    <Chart title="Memory Usage (MB)" data={data} tf={tf}
                        lines={[{ key: 'mem_mb', name: 'Memory MB', color: 'var(--purple)' }]}
                        valueFmt={v => formatBytes(v * 1024 * 1024)} />
                    <Chart title="Network (KB/s)" data={data} tf={tf}
                        lines={[
                            { key: 'netin_kbps', name: 'RX KB/s', color: 'var(--green)' },
                            { key: 'netout_kbps', name: 'TX KB/s', color: 'var(--orange)' },
                        ]}
                        valueFmt={v => `${v} KB/s`} />
                    {iops.length > 0 ? (
                        <Chart title="Disk IOPS (ops/s)" data={iops} tf={tf}
                            lines={[
                                { key: 'read_iops', name: 'Read IOPS', color: 'var(--cyan)' },
                                { key: 'write_iops', name: 'Write IOPS', color: 'var(--yellow)' },
                            ]}
                            valueFmt={v => `${v}`} />
                    ) : (
                        <div style={{ fontSize: 11, color: 'var(--text3)' }}>
                            Disk IOPS: belum ada data di jendela ini. IOPS disampling dashboard tiap 15 detik selama VM running (disimpan 14 hari).
                        </div>
                    )}
                </>
            )}
        </div>
    );
}
