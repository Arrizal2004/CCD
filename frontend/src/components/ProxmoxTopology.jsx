import { useEffect, useRef, useState, useCallback } from 'react';
import { Network, DataSet } from 'vis-network/standalone';
import { fetchProxmoxInstances, fetchProxmoxNodes, fetchProxmoxVms, fetchMyProxmoxVms, fetchProxmoxVmDetail } from '../api';
import { formatBytes } from '../format';

function parseBridge(config) {
    const netKey = Object.keys(config).find(k => /^net\d+$/.test(k));
    if (!netKey) return null;
    const m = config[netKey].match(/bridge=([^,]+)/);
    return m ? m[1] : null;
}

const STATUS_COLOR = { running: '#00e676', stopped: '#4a6a8a', paused: '#ffd600' };

export default function ProxmoxTopology({ currentUser }) {
    const isAdmin = ['superadmin', 'sysadmin'].includes(currentUser?.role);
    const username = currentUser?.username || 'me';
    const containerRef = useRef(null);
    const networkRef = useRef(null);
    const [selected, setSelected] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [stats, setStats] = useState({ instances: 0, nodes: 0, vms: 0, bridges: 0 });

    const build = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const nodes = new DataSet();
            const edges = new DataSet();
            const bridgeIds = new Set();
            let vmCount = 0;
            let nodeCount = 0;
            let instances = [];

            if (!isAdmin) {
                // Student: root = username sendiri, tanpa nama instance/node/host Proxmox.
                const rootId = 'user:me';
                nodes.add({
                    id: rootId, label: username, shape: 'box', margin: 12,
                    color: { background: '#141e30', border: '#00e5ff' },
                    font: { color: '#00e5ff', size: 14, face: 'JetBrains Mono', bold: true },
                    _raw: { type: 'user', name: username },
                });
                for (const vm of await fetchMyProxmoxVms()) {
                    vmCount++;
                    const vmId = `vm:${vm.instance}:${vm.node}:${vm.vmid}`;
                    const color = STATUS_COLOR[vm.status] || '#4a6a8a';
                    nodes.add({
                        id: vmId, label: vm.name || String(vm.vmid), shape: 'dot', size: 16,
                        color: { background: color, border: color },
                        font: { color, size: 10, face: 'JetBrains Mono' },
                        _raw: { type: 'vm', vmid: vm.vmid, name: vm.name, status: vm.status, cpus: vm.cpus, maxmem: vm.maxmem },
                    });
                    edges.add({ from: rootId, to: vmId, color: { color: '#1e2d47' }, width: 1 });
                }
            } else {
            instances = await fetchProxmoxInstances();

            for (const inst of instances) {
                const instId = `instance:${inst.label}`;
                nodes.add({
                    id: instId, label: inst.label, shape: 'box', margin: 12,
                    color: { background: '#141e30', border: '#d500f9' },
                    font: { color: '#d500f9', size: 14, face: 'JetBrains Mono', bold: true },
                    _raw: { type: 'instance', label: inst.label, host: inst.host },
                });

                const nodesList = await fetchProxmoxNodes(inst.label);
                for (const n of nodesList) {
                    nodeCount++;
                    const nodeId = `node:${inst.label}:${n.node}`;
                    nodes.add({
                        id: nodeId, label: n.node, shape: 'box', margin: 10,
                        color: { background: '#111827', border: '#00e5ff' },
                        font: { color: '#00e5ff', size: 13, face: 'JetBrains Mono', bold: true },
                        _raw: { type: 'node', instance: inst.label, node: n.node, status: n.status },
                    });
                    edges.add({ from: instId, to: nodeId, color: { color: '#243655' }, width: 2 });

                    const vms = await fetchProxmoxVms(inst.label, n.node);
                    for (const vm of vms) {
                        vmCount++;
                        const vmId = `vm:${inst.label}:${n.node}:${vm.vmid}`;
                        const color = STATUS_COLOR[vm.status] || '#4a6a8a';
                        nodes.add({
                            id: vmId, label: vm.name || String(vm.vmid), shape: 'dot', size: 16,
                            color: { background: color, border: color },
                            font: { color, size: 10, face: 'JetBrains Mono' },
                            _raw: { type: 'vm', instance: inst.label, node: n.node, vmid: vm.vmid, name: vm.name, status: vm.status, cpus: vm.cpus, maxmem: vm.maxmem },
                        });

                        let bridge = null;
                        try {
                            const detail = await fetchProxmoxVmDetail(inst.label, n.node, vm.vmid);
                            bridge = parseBridge(detail.config || {});
                        } catch { /* VM mungkin tak bisa diakses — tetap gambar node→VM langsung */ }

                        if (bridge) {
                            const bridgeId = `bridge:${inst.label}:${n.node}:${bridge}`;
                            if (!bridgeIds.has(bridgeId)) {
                                bridgeIds.add(bridgeId);
                                nodes.add({
                                    id: bridgeId, label: bridge, shape: 'diamond', size: 14,
                                    color: { background: '#d500f9', border: '#d500f9' },
                                    font: { color: '#d500f9', size: 10, face: 'JetBrains Mono' },
                                    _raw: { type: 'bridge', instance: inst.label, node: n.node, bridge },
                                });
                                edges.add({ from: nodeId, to: bridgeId, color: { color: '#243655' }, width: 2 });
                            }
                            edges.add({ from: bridgeId, to: vmId, color: { color: '#1e2d47' }, width: 1 });
                        } else {
                            edges.add({ from: nodeId, to: vmId, color: { color: '#1e2d47' }, width: 1 });
                        }
                    }
                }
            }

            }

            setStats({ instances: instances.length, nodes: nodeCount, vms: vmCount, bridges: bridgeIds.size });

            if (networkRef.current) networkRef.current.destroy();
            if (containerRef.current) {
                const network = new Network(containerRef.current, { nodes, edges }, {
                    physics: { stabilization: true, barnesHut: { gravitationalConstant: -4000, springLength: 90 } },
                    interaction: { hover: true },
                });
                network.on('click', (params) => {
                    if (params.nodes.length === 0) { setSelected(null); return; }
                    const node = nodes.get(params.nodes[0]);
                    setSelected(node._raw);
                });
                networkRef.current = network;
            }
        } catch (e) {
            setError(e?.response?.data?.detail || 'Gagal memuat topologi');
        } finally {
            setLoading(false);
        }
    }, [isAdmin, username]);

    useEffect(() => {
        build();
        return () => { if (networkRef.current) networkRef.current.destroy(); };
    }, [build]);

    return (
        <div style={{ height: 'calc(100vh - 92px)', position: 'relative' }}>
            <div style={{ position: 'absolute', top: 12, left: 16, zIndex: 10, fontSize: 11, color: 'var(--text3)', fontFamily: 'var(--fmono)' }}>
                {isAdmin ? `${stats.instances} instance · ${stats.nodes} node · ${stats.bridges} bridge · ${stats.vms} VM` : `${stats.vms} VM`}
            </div>
            {loading && (
                <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text3)', fontSize: 12 }}>
                    Membangun topologi…
                </div>
            )}
            {error && (
                <div style={{ position: 'absolute', top: 12, right: 16, zIndex: 10, background: 'var(--red-glow)', border: '1px solid var(--red)', borderRadius: 6, padding: '6px 10px', color: 'var(--red)', fontSize: 11 }}>
                    ⚠ {error}
                </div>
            )}
            <div ref={containerRef} style={{ width: '100%', height: '100%' }} />

            {selected && (
                <div style={{
                    position: 'absolute', top: 12, right: 16, width: 260, background: 'var(--bg-panel)',
                    border: '1px solid var(--border)', borderRadius: 8, padding: 14, zIndex: 10,
                }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>
                            {selected.type === 'user' ? selected.name : selected.type === 'instance' ? selected.label : selected.type === 'node' ? selected.node : selected.type === 'bridge' ? selected.bridge : selected.name}
                        </div>
                        <button onClick={() => setSelected(null)} style={{ background: 'transparent', border: 'none', color: 'var(--text3)', cursor: 'pointer' }}>×</button>
                    </div>
                    {selected.type === 'instance' && (
                        <div style={{ fontSize: 11, color: 'var(--text2)' }}>Host: {selected.host}</div>
                    )}
                    {selected.type === 'node' && (
                        <div style={{ fontSize: 11, color: 'var(--text2)' }}>Instance: {selected.instance} · Status: {selected.status}</div>
                    )}
                    {selected.type === 'bridge' && (
                        <div style={{ fontSize: 11, color: 'var(--text2)' }}>{selected.instance}/{selected.node}</div>
                    )}
                    {selected.type === 'vm' && (
                        <div style={{ fontSize: 11, color: 'var(--text2)', display: 'flex', flexDirection: 'column', gap: 3 }}>
                            <div>{selected.instance ? `${selected.instance}/${selected.node} · ` : ''}VMID {selected.vmid}</div>
                            <div>Status: <span style={{ color: STATUS_COLOR[selected.status] }}>{selected.status}</span></div>
                            <div>vCPU: {selected.cpus}</div>
                            <div>Memory: {formatBytes(selected.maxmem)}</div>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
