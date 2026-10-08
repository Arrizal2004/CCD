import { useState, useEffect, useCallback } from 'react';
import { fetchHostStatus } from '../api';
import { formatBytes, formatUptime } from '../format';
import { useT } from '../i18n';

const POLL_MS = 5000;

function pct(used, total) {
    if (!total) return 0;
    return Math.min(100, Math.round((used / total) * 100));
}

function barColor(p) {
    if (p >= 90) return 'var(--red)';
    if (p >= 75) return 'var(--yellow)';
    return 'var(--cyan)';
}

function Bar({ label, used, total, valuePct }) {
    const p = valuePct != null ? valuePct : pct(used, total);
    return (
        <div style={{ marginBottom: 8 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: 'var(--text3)', marginBottom: 3 }}>
                <span>{label}</span>
                <span style={{ fontFamily: 'var(--fmono)' }}>
                    {total != null ? `${formatBytes(used)} / ${formatBytes(total)}` : `${p}%`}
                </span>
            </div>
            <div style={{ height: 6, borderRadius: 3, background: 'var(--bg-hover)', overflow: 'hidden' }}>
                <div style={{ height: '100%', width: `${p}%`, background: barColor(p), borderRadius: 3, transition: 'width 0.4s ease' }} />
            </div>
        </div>
    );
}

function NetRate({ label, bps, color }) {
    return (
        <div style={{ flex: 1, textAlign: 'center' }}>
            <div style={{ fontSize: 9, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>{label}</div>
            <div style={{ fontSize: 13, fontFamily: 'var(--fmono)', color, fontWeight: 600 }}>
                {bps != null ? `${formatBytes(bps)}/s` : '—'}
            </div>
        </div>
    );
}

export default function HostPerformancePanel() {
    const t = useT();
    const [hosts, setHosts] = useState(null);
    const [error, setError] = useState(null);

    const load = useCallback(async (silent = false) => {
        try {
            const data = await fetchHostStatus();
            setHosts(data);
            setError(null);
        } catch (e) {
            if (!silent) setError(e?.response?.data?.detail || t('host.loadFailed'));
        }
    }, [t]);

    useEffect(() => {
        load();
        const iv = setInterval(() => load(true), POLL_MS);
        return () => clearInterval(iv);
    }, [load]);

    if (error) return null; // panel non-esensial — jangan ganggu halaman utama kalau gagal
    if (!hosts) return null;
    if (hosts.length === 0) return null;

    return (
        <div style={{ marginBottom: 16 }}>
            <div style={{ fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 8, display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ color: 'var(--green)' }}>●</span> {t('host.title')}
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 12 }}>
                {hosts.map(h => (
                    <div key={`${h.instance}__${h.node}`}
                        style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 10, padding: 14 }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 10 }}>
                            <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text)' }}>{h.instance}/{h.node}</div>
                            <div style={{ fontSize: 10, color: 'var(--text3)' }}>{t('host.up', { time: formatUptime(h.uptime_s) })}</div>
                        </div>

                        <Bar label={t('host.cpu', { n: h.cpus || '?' })} valuePct={h.cpu_pct} />
                        <Bar label="RAM" used={h.mem_used} total={h.mem_total} />
                        <Bar label={t('host.disk')} used={h.disk_used} total={h.disk_total} />

                        <div style={{ display: 'flex', gap: 4, marginTop: 10, paddingTop: 8, borderTop: '1px solid var(--border)' }}>
                            <NetRate label="↓ RX" bps={h.net_in_bps} color="var(--green)" />
                            <NetRate label="↑ TX" bps={h.net_out_bps} color="var(--orange)" />
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
}
