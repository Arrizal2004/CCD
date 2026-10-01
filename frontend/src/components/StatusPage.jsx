import { useState, useEffect, useCallback } from 'react';
import { fetchHealthz, fetchProxmoxInstances, fetchProxmoxNodes, fetchProxmoxVms } from '../api';

const DOT = { width: 8, height: 8, borderRadius: '50%', flexShrink: 0 };
const COLOR = { ok: '#4ade80', warn: '#f0c040', error: '#f87171', unknown: '#6b7280' };
const BG    = { ok: '#4ade8012', warn: '#f0c04012', error: '#f8717112', unknown: '#6b728012' };
const BORDER= { ok: '#4ade8033', warn: '#f0c04033', error: '#f8717133', unknown: '#6b728033' };

function statusColor(s) { return COLOR[s] || COLOR.unknown; }
function statusBg(s)    { return BG[s]    || BG.unknown; }
function statusBorder(s){ return BORDER[s] || BORDER.unknown; }

function StatusDot({ status }) {
    return <span style={{ ...DOT, background: statusColor(status), boxShadow: status === 'ok' ? `0 0 5px ${COLOR.ok}88` : 'none' }} />;
}

function StatusBadge({ status, label }) {
    return (
        <span style={{
            display: 'inline-flex', alignItems: 'center', gap: 5,
            padding: '2px 8px', borderRadius: 12, fontSize: 10, fontWeight: 600,
            background: statusBg(status), border: `1px solid ${statusBorder(status)}`,
            color: statusColor(status), textTransform: 'uppercase', letterSpacing: '0.06em',
        }}>
            <StatusDot status={status} /> {label || status}
        </span>
    );
}

function SectionCard({ title, children, extra }) {
    return (
        <div style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', borderRadius: 10, overflow: 'hidden' }}>
            <div style={{ padding: '10px 16px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '0.1em' }}>{title}</span>
                {extra}
            </div>
            <div>{children}</div>
        </div>
    );
}

function Row({ label, status, sub, right, mono }) {
    return (
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 16px', borderBottom: '1px solid var(--border-light)' }}>
            <StatusDot status={status} />
            <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 12, color: 'var(--text)', fontFamily: mono ? 'var(--fmono)' : undefined }}>{label}</div>
                {sub && <div style={{ fontSize: 10, color: 'var(--text3)', marginTop: 1 }}>{sub}</div>}
            </div>
            <div style={{ flexShrink: 0 }}>{right}</div>
        </div>
    );
}

export default function StatusPage() {
    const [health, setHealth]     = useState(null);
    const [nodes, setNodes]       = useState([]); // [{instance, node, status}]
    const [vmsByNode, setVmsByNode] = useState({}); // {"instance:node": [vm,...]}
    const [loading, setLoading]   = useState(true);
    const [lastRefresh, setLastRefresh] = useState(null);

    const refresh = useCallback(async () => {
        try {
            const h = await fetchHealthz();
            setHealth(h);
            const instances = await fetchProxmoxInstances().catch(() => []);
            const allNodes = [];
            const vmMap = {};
            await Promise.all(instances.map(async inst => {
                const nodeList = await fetchProxmoxNodes(inst.label).catch(() => []);
                await Promise.all(nodeList.map(async n => {
                    allNodes.push({ instance: inst.label, node: n.node, status: n.status });
                    vmMap[`${inst.label}:${n.node}`] = await fetchProxmoxVms(inst.label, n.node).catch(() => []);
                }));
            }));
            setNodes(allNodes);
            setVmsByNode(vmMap);
        } catch { /* individual fetches already handle errors */ }
        finally { setLoading(false); setLastRefresh(new Date()); }
    }, []);

    useEffect(() => { refresh(); const id = setInterval(refresh, 30000); return () => clearInterval(id); }, [refresh]);

    // Derive overall status
    const dbStatus    = health?.checks?.database?.status  || 'unknown';
    const redisStatus = health?.checks?.redis?.status     || 'unknown';
    const pveStatus   = health?.checks?.proxmox?.status    || 'unknown';
    const guacStatus  = health?.checks?.guacamole?.status  || 'unknown';
    const apiStatus   = health ? (health.status === 'ok' ? 'ok' : 'warn') : 'unknown';
    const overallStatuses = [apiStatus, dbStatus, redisStatus, pveStatus, guacStatus];
    const overall = overallStatuses.includes('error') ? 'error'
        : overallStatuses.includes('warn') ? 'warn'
        : overallStatuses.every(s => s === 'ok') ? 'ok' : 'unknown';

    const overallLabel = { ok: 'Semua Sistem Normal', warn: 'Degraded', error: 'Ada Masalah', unknown: 'Mengecek...' };

    const allVms = Object.entries(vmsByNode).flatMap(([node, vms]) => vms.map(vm => ({ ...vm, _node: node })));
    const runningVms = allVms.filter(v => v.status === 'running');

    return (
        <div style={{ padding: '20px', maxWidth: 1100, margin: '0 auto' }}>

            {/* Header banner */}
            <div style={{
                padding: '16px 20px', borderRadius: 10, marginBottom: 20,
                background: statusBg(overall), border: `1px solid ${statusBorder(overall)}`,
                display: 'flex', alignItems: 'center', gap: 12,
            }}>
                <StatusDot status={overall} />
                <div>
                    <div style={{ fontSize: 14, fontWeight: 700, color: statusColor(overall) }}>{overallLabel[overall]}</div>
                    {lastRefresh && (
                        <div style={{ fontSize: 10, color: 'var(--text3)', marginTop: 2 }}>
                            Diperbarui: {lastRefresh.toLocaleTimeString()} · auto-refresh 30 detik
                        </div>
                    )}
                </div>
                <button onClick={refresh} disabled={loading} style={{
                    marginLeft: 'auto', padding: '5px 14px', borderRadius: 6, fontSize: 11, cursor: 'pointer',
                    background: 'transparent', border: '1px solid var(--border)', color: 'var(--text3)',
                }}>
                    {loading ? '...' : '↻ Refresh'}
                </button>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 16 }}>

                {/* Backend Services */}
                <SectionCard title="Backend Services">
                    <Row
                        status={apiStatus}
                        label="Backend API"
                        sub={health?.version ? `v${health.version}` : undefined}
                        right={<StatusBadge status={apiStatus} label={health?.status || 'unknown'} />}
                    />
                    <Row
                        status={dbStatus}
                        label="PostgreSQL"
                        sub={health?.checks?.database?.error}
                        right={
                            health?.checks?.database?.latency_ms != null
                                ? <span style={{ fontSize: 11, color: 'var(--text3)', fontFamily: 'var(--fmono)' }}>{health.checks.database.latency_ms} ms</span>
                                : <StatusBadge status={dbStatus} label={dbStatus} />
                        }
                    />
                    <Row
                        status={redisStatus}
                        label="Redis"
                        sub={health?.checks?.redis?.error}
                        right={
                            health?.checks?.redis?.latency_ms != null
                                ? <span style={{ fontSize: 11, color: 'var(--text3)', fontFamily: 'var(--fmono)' }}>{health.checks.redis.latency_ms} ms</span>
                                : <StatusBadge status={redisStatus} label={redisStatus} />
                        }
                    />
                    <Row
                        status={guacStatus}
                        label="Guacamole"
                        sub={health?.checks?.guacamole?.error}
                        right={
                            health?.checks?.guacamole?.latency_ms != null
                                ? <span style={{ fontSize: 11, color: 'var(--text3)', fontFamily: 'var(--fmono)' }}>{health.checks.guacamole.latency_ms} ms</span>
                                : <StatusBadge status={guacStatus} label={guacStatus} />
                        }
                    />
                </SectionCard>

                {/* Proxmox Nodes */}
                <SectionCard
                    title="Proxmox Nodes"
                    extra={<span style={{ fontSize: 10, color: 'var(--text3)' }}>{nodes.filter(n => n.status === 'online').length}/{nodes.length} online</span>}
                >
                    {nodes.length === 0 && (
                        <div style={{ padding: '14px 16px', fontSize: 12, color: 'var(--text3)' }}>
                            {pveStatus === 'error' ? (health?.checks?.proxmox?.error || 'Tidak bisa menghubungi Proxmox API') : 'Tidak ada node.'}
                        </div>
                    )}
                    {nodes.map(n => {
                        const st = n.status === 'online' ? 'ok' : 'error';
                        const vms = vmsByNode[`${n.instance}:${n.node}`] || [];
                        const running = vms.filter(v => v.status === 'running').length;
                        return (
                            <Row key={`${n.instance}:${n.node}`}
                                status={st}
                                label={`${n.instance}/${n.node}`}
                                sub={`${running}/${vms.length} VM running`}
                                mono
                                right={<StatusBadge status={st} label={n.status} />}
                            />
                        );
                    })}
                </SectionCard>
            </div>

            {/* VMs per node */}
            <SectionCard
                title="Virtual Machines"
                extra={<span style={{ fontSize: 10, color: 'var(--text3)' }}>{runningVms.length}/{allVms.length} running</span>}
            >
                {allVms.length === 0 && (
                    <div style={{ padding: '14px 16px', fontSize: 12, color: 'var(--text3)' }}>Tidak ada VM.</div>
                )}
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))' }}>
                    {allVms.map(vm => {
                        const st = vm.status === 'running' ? 'ok' : vm.status === 'paused' ? 'warn' : 'unknown';
                        return (
                            <div key={`${vm._node}:${vm.vmid}`} style={{
                                display: 'flex', alignItems: 'center', gap: 10,
                                padding: '8px 16px', borderBottom: '1px solid var(--border-light)',
                                borderRight: '1px solid var(--border-light)',
                            }}>
                                <StatusDot status={st} />
                                <div style={{ flex: 1, minWidth: 0 }}>
                                    <div style={{ fontSize: 11, color: 'var(--text)', fontFamily: 'var(--fmono)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                        {vm.name || vm.vmid}
                                    </div>
                                    <div style={{ fontSize: 10, color: 'var(--text3)' }}>{vm._node} · VMID {vm.vmid}</div>
                                </div>
                                <StatusBadge status={st} label={vm.status} />
                            </div>
                        );
                    })}
                </div>
            </SectionCard>

        </div>
    );
}
