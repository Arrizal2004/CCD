import { useEffect, useRef, useState, useCallback } from 'react';
import { Network, DataSet } from 'vis-network/standalone';
import { fetchProxmoxInstances, fetchProxmoxNodes, fetchProxmoxVms, fetchMyProxmoxVms, fetchProxmoxVmDetail, fetchNetworks } from '../api';
import { formatBytes, formatCcdId } from '../format';
import useIsMobile from '../useIsMobile';
import SwitchManager from './SwitchManager';
import { useT } from '../i18n';

// Bridge dari setiap kartu jaringan VM (net0, net1, …), tanpa duplikat, urut nomor kartunya.
function parseBridges(config) {
    return [...new Set(Object.keys(config)
        .filter(k => /^net\d+$/.test(k))
        .sort((a, b) => Number(a.slice(3)) - Number(b.slice(3)))
        .map(k => (String(config[k]).match(/bridge=([^,]+)/) || [])[1])
        .filter(Boolean))];
}

const STATUS_COLOR = { running: '#00e676', stopped: '#4a6a8a', paused: '#ffd600' };

export default function ProxmoxTopology({ currentUser }) {
    const t = useT();
    const isAdmin = ['superadmin', 'sysadmin'].includes(currentUser?.role);
    const username = currentUser?.username || 'me';
    const containerRef = useRef(null);
    const networkRef = useRef(null);
    const [selected, setSelected] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [stats, setStats] = useState({ instances: 0, nodes: 0, vms: 0, bridges: 0, switches: 0 });
    const [managing, setManaging] = useState(false);     // jendela kelola switch
    const [dirty, setDirty] = useState(false);           // switch berubah: gambar ulang saat jendela ditutup
    const isMobile = useIsMobile();

    const build = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const nodes = new DataSet();
            const edges = new DataSet();
            const bridgeIds = new Set();
            let switchCount = 0;
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
                        _raw: { type: 'vm', vmid: vm.vmid, ccd_id: vm.ccd_id, name: vm.name, status: vm.status, cpus: vm.cpus, maxmem: vm.maxmem },
                    });
                    edges.add({ from: rootId, to: vmId, color: { color: '#1e2d47' }, width: 1 });
                }
            } else {
            instances = await fetchProxmoxInstances();

            // Bridge milik switch CCD ditampilkan dengan nama dan subnet switch-nya, bukan ID VNet.
            // Switch juga digambar walau belum ada VM-nya, supaya switch baru langsung terlihat.
            const switches = {};
            const switchesOf = {};
            try {
                for (const i of (await fetchNetworks()).instances) {
                    switchesOf[i.label] = i.networks;
                    for (const n of i.networks) switches[`${i.label}:${n.vnet}`] = n;
                }
            } catch { /* tanpa daftar switch, bridge tetap tampil dengan namanya */ }

            const ensureBridge = (label, node, nodeId, bridge) => {
                const bridgeId = `bridge:${label}:${node}:${bridge}`;
                if (bridgeIds.has(bridgeId)) return bridgeId;
                bridgeIds.add(bridgeId);
                const sw = switches[`${label}:${bridge}`];
                if (sw) switchCount++;
                const tint = sw ? '#ffab40' : '#d500f9';
                nodes.add({
                    id: bridgeId, label: sw ? `${sw.name}\n${sw.cidr}` : bridge, shape: 'diamond', size: 14,
                    color: { background: tint, border: tint },
                    font: { color: tint, size: 10, face: 'JetBrains Mono' },
                    _raw: { type: 'bridge', instance: label, node, bridge, switch: sw },
                });
                edges.add({ from: nodeId, to: bridgeId, color: { color: '#243655' }, width: 2 });
                return bridgeId;
            };

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
                    for (const sw of switchesOf[inst.label] || []) ensureBridge(inst.label, n.node, nodeId, sw.vnet);

                    const vms = await fetchProxmoxVms(inst.label, n.node);
                    for (const vm of vms) {
                        vmCount++;
                        const vmId = `vm:${inst.label}:${n.node}:${vm.vmid}`;
                        const color = STATUS_COLOR[vm.status] || '#4a6a8a';
                        nodes.add({
                            id: vmId, label: vm.name || String(vm.vmid), shape: 'dot', size: 16,
                            color: { background: color, border: color },
                            font: { color, size: 10, face: 'JetBrains Mono' },
                            _raw: { type: 'vm', instance: inst.label, node: n.node, vmid: vm.vmid, ccd_id: vm.ccd_id, name: vm.name, status: vm.status, cpus: vm.cpus, maxmem: vm.maxmem },
                        });

                        let bridges = [];
                        try {
                            const detail = await fetchProxmoxVmDetail(inst.label, n.node, vm.vmid);
                            bridges = parseBridges(detail.config || {});
                        } catch { /* VM mungkin tak bisa diakses — tetap gambar node→VM langsung */ }

                        // Satu garis per kartu jaringan: VM router terlihat menghubungkan dua jaringan.
                        for (const bridge of bridges) {
                            edges.add({ from: ensureBridge(inst.label, n.node, nodeId, bridge), to: vmId, color: { color: '#1e2d47' }, width: 1 });
                        }
                        if (bridges.length === 0) {
                            edges.add({ from: nodeId, to: vmId, color: { color: '#1e2d47' }, width: 1 });
                        }
                    }
                }
            }

            }

            setStats({ instances: instances.length, nodes: nodeCount, vms: vmCount, bridges: bridgeIds.size - switchCount, switches: switchCount });

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
            setError(e?.response?.data?.detail || t('topo.loadFailed'));
        } finally {
            setLoading(false);
        }
    }, [isAdmin, username, t]);

    useEffect(() => {
        build();
        return () => { if (networkRef.current) networkRef.current.destroy(); };
    }, [build]);

    const closeManager = () => {
        setManaging(false);
        if (dirty) { setDirty(false); build(); }
    };

    return (
        <div style={{ height: 'calc(100vh - 92px)', position: 'relative' }}>
            <div style={{ position: 'absolute', top: 12, left: 16, zIndex: 10, display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', fontSize: 11, color: 'var(--text3)', fontFamily: 'var(--fmono)' }}>
                <span>{isAdmin ? t('topo.statsAdmin', stats) : t('topo.statsStudent', stats)}</span>
                {isAdmin && (
                    <button onClick={() => setManaging(true)}
                        style={{ padding: '4px 12px', fontSize: 11, borderRadius: 6, cursor: 'pointer', background: '#ffab4022', border: '1px solid #ffab40', color: '#ffab40', fontWeight: 600, fontFamily: 'var(--font)' }}>
                        {t('topo.addSwitch')}
                    </button>
                )}
            </div>
            {loading && (
                <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text3)', fontSize: 12 }}>
                    {t('topo.building')}
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
                        <button onClick={() => setSelected(null)} aria-label={t('common.close')} style={{ background: 'transparent', border: 'none', color: 'var(--text3)', cursor: 'pointer' }}>×</button>
                    </div>
                    {selected.type === 'instance' && (
                        <div style={{ fontSize: 11, color: 'var(--text2)' }}>{t('topo.host', { host: selected.host })}</div>
                    )}
                    {selected.type === 'node' && (
                        <div style={{ fontSize: 11, color: 'var(--text2)' }}>{t('topo.nodeInfo', { instance: selected.instance, status: selected.status })}</div>
                    )}
                    {selected.type === 'bridge' && (
                        <div style={{ fontSize: 11, color: 'var(--text2)', display: 'flex', flexDirection: 'column', gap: 3 }}>
                            {selected.switch ? (
                                <>
                                    <div>{t('topo.switchInfo', { name: selected.switch.name, vnet: selected.bridge })}</div>
                                    <div>{t('topo.subnetInfo', { cidr: selected.switch.cidr, gw: selected.switch.gateway })}</div>
                                    <div>{selected.switch.snat ? t('topo.nat') : t('topo.noNat')} · {selected.instance}/{selected.node}</div>
                                    <button onClick={() => setManaging(true)}
                                        style={{ alignSelf: 'flex-start', marginTop: 6, padding: '3px 10px', fontSize: 11, borderRadius: 5, cursor: 'pointer', background: 'transparent', border: '1px solid #ffab40', color: '#ffab40' }}>
                                        {t('topo.manage')}
                                    </button>
                                </>
                            ) : `${selected.instance}/${selected.node}`}
                        </div>
                    )}
                    {selected.type === 'vm' && (
                        <div style={{ fontSize: 11, color: 'var(--text2)', display: 'flex', flexDirection: 'column', gap: 3 }}>
                            <div>{selected.instance ? `${selected.instance}/${selected.node} · VMID ${selected.vmid} · ` : ''}CCDID {formatCcdId(selected.ccd_id)}</div>
                            <div>{t('topo.status')} <span style={{ color: STATUS_COLOR[selected.status] }}>{['running', 'stopped', 'paused'].includes(selected.status) ? t(`vmstatus.${selected.status}`) : selected.status}</span></div>
                            <div>{t('topo.vcpu', { n: selected.cpus })}</div>
                            <div>{t('topo.memory', { size: formatBytes(selected.maxmem) })}</div>
                        </div>
                    )}
                </div>
            )}

            {managing && (
                <div onClick={closeManager} style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', zIndex: 200, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: isMobile ? 0 : 16 }}>
                    <div onClick={e => e.stopPropagation()} role="dialog" aria-label={t('topo.manage')}
                        style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', borderRadius: isMobile ? 0 : 10, width: isMobile ? '100vw' : 'min(920px, 96vw)', height: isMobile ? '100dvh' : 'auto', maxHeight: isMobile ? '100dvh' : '90vh', overflow: 'auto', padding: isMobile ? 14 : 20, boxSizing: 'border-box' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
                            <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--text)' }}>{t('topo.title')}</div>
                            <button onClick={closeManager} aria-label={t('common.close')} style={{ background: 'transparent', border: 'none', color: 'var(--text3)', fontSize: 20, cursor: 'pointer' }}>×</button>
                        </div>
                        <SwitchManager onChanged={() => setDirty(true)} />
                    </div>
                </div>
            )}
        </div>
    );
}
