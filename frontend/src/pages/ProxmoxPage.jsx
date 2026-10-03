import { useState, useEffect, useCallback, useRef } from 'react';
import {
    fetchProxmoxInstances, fetchProxmoxNodes, fetchProxmoxVms, fetchMyProxmoxVms, proxmoxVmAction,
    getGuacUrl, appendGuacToken, applyGuacTouchInputDefault, fetchSshConfig, fetchProxmoxVmIp, fetchMyAssignedVmids, fetchProxmoxVmDetail,
} from '../api';
import { formatBytes, formatUptime } from '../format';
import ProxmoxSnapshotModal from '../components/ProxmoxSnapshotModal';
import SshCredModal from '../components/SshCredModal';
import ProxmoxVmDetailModal from '../components/ProxmoxVmDetailModal';
import ProxmoxInstancesModal from '../components/ProxmoxInstancesModal';
import HostPerformancePanel from '../components/HostPerformancePanel';
import CreateVmModal from '../components/CreateVmModal';
import ProxmoxResizeModal from '../components/ProxmoxResizeModal';
import { cloudInitDefaults } from '../proxmoxCloudInit';
import useIsMobile from '../useIsMobile';
import SshCommandModal from '../components/SshCommandModal';

const STATUS_COLOR = {
    running: 'var(--green)',
    stopped: 'var(--text3)',
    paused:  'var(--yellow)',
};

function deleteSummary(r) {
    const rows = Object.values(r.dashboard_rows_removed || {}).reduce((a, b) => a + b, 0);
    const left = r.disks_left || [];
    return `VM ${r.vmid} "${r.name}" dihapus${r.stopped_first ? ' (dimatikan dulu)' : ''}`
        + ` · disk tersisa: ${left.length ? left.join(', ') : 'tidak ada'}`
        + ` · koneksi Guacamole dihapus: ${r.guacamole_connections_removed}${r.guacamole_error ? ` (⚠ ${r.guacamole_error})` : ''}`
        + ` · data dashboard dibersihkan: ${rows} baris`;
}


const ACTIONS_BY_STATUS = {
    running: [
        { action: 'shutdown', label: 'Shutdown', accent: 'yellow' },
        { action: 'stop',     label: 'Stop (force)', accent: 'red' },
        { action: 'reboot',   label: 'Reboot', accent: 'cyan' },
    ],
    stopped: [
        { action: 'start', label: 'Start', accent: 'green' },
    ],
    paused: [
        { action: 'resume', label: 'Resume', accent: 'green' },
    ],
};

export default function ProxmoxPage({ currentUser }) {
    const [instances, setInstances] = useState([]);
    const [selectedInstance, setSelectedInstance] = useState(null);
    const [showInstances, setShowInstances] = useState(false);
    const [nodes, setNodes] = useState([]);
    const [selectedNode, setSelectedNode] = useState(null);
    const [vms, setVms] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [notice, setNotice] = useState(null);
    const [pendingAction, setPendingAction] = useState(null); // vmid currently running an action
    const [lastUpdate, setLastUpdate] = useState(null);
    const pollRef = useRef(null);
    const [resizeVm, setResizeVm] = useState(null);       // vm sedang dibuka modal resize-nya (admin)
    const [snapshotVm, setSnapshotVm] = useState(null);   // vm sedang dibuka modal snapshot-nya
    const [credsVm, setCredsVm] = useState(null);          // vm sedang dibuka modal credentials-nya
    const [detailVm, setDetailVm] = useState(null);        // vm sedang dibuka modal detail-nya
    const [showCreate, setShowCreate] = useState(false);
    const [connecting, setConnecting] = useState(null);    // vmid sedang proses connect

    const canControl = ['superadmin', 'sysadmin'].includes(currentUser?.role);
    const isMobile = useIsMobile();
    const [sshCfg, setSshCfg] = useState(null);     // konfigurasi bastion SSH (null = belum dimuat)
    const [sshVm, setSshVm] = useState(null);       // vm yang sedang dibuka modal perintah SSH-nya
    useEffect(() => { fetchSshConfig().then(setSshCfg); }, []);
    const [myVmids, setMyVmids] = useState([]); // VMID (string) yang di-assign ke user student ini

    const canControlVm = useCallback((vmid) => canControl || myVmids.includes(String(vmid)), [canControl, myVmids]);


    const loadInstances = useCallback(async () => {
        if (!canControl) return; // student tidak boleh melihat daftar host — pakai /my-vms
        try {
            const list = await fetchProxmoxInstances();
            setInstances(list);
            setError(null);
            if (!selectedInstance && list.length > 0) setSelectedInstance(list[0].label);
            if (list.length === 0 && canControl) setShowInstances(true);
        } catch (e) {
            setError(e?.response?.data?.detail || 'Gagal mengambil daftar Proxmox instance');
        }
    }, [selectedInstance, canControl]);

    const loadNodes = useCallback(async (instance) => {
        if (!instance) return;
        try {
            const list = await fetchProxmoxNodes(instance);
            setNodes(list);
            setError(null);
            setSelectedNode(prev => prev && list.some(n => n.node === prev) ? prev : (list[0]?.node || null));
        } catch (e) {
            setError(e?.response?.data?.detail || 'Gagal menghubungi Proxmox API');
        }
    }, []);

    const loadVms = useCallback(async (instance, node) => {
        if (!instance || !node) return;
        try {
            const list = await fetchProxmoxVms(instance, node);
            setVms(list.sort((a, b) => a.vmid - b.vmid));
            setError(null);
        } catch (e) {
            setError(e?.response?.data?.detail || 'Gagal mengambil daftar VM');
        } finally {
            setLoading(false);
            setLastUpdate(Date.now());
        }
    }, []);

    useEffect(() => { loadInstances(); }, [loadInstances]);

    useEffect(() => { loadNodes(selectedInstance); }, [selectedInstance, loadNodes]);

    useEffect(() => {
        if (!selectedInstance || !selectedNode) return;
        setLoading(true);
        loadVms(selectedInstance, selectedNode);
        pollRef.current = setInterval(() => loadVms(selectedInstance, selectedNode), 10000);
        return () => clearInterval(pollRef.current);
    }, [selectedInstance, selectedNode, loadVms]);

    useEffect(() => {
        if (!selectedInstance || !selectedNode || canControl) return; // admin/sysadmin lihat semua
        fetchMyAssignedVmids(selectedInstance, selectedNode).then(r => setMyVmids(r.vmids || []));
    }, [selectedInstance, selectedNode, canControl]);

    // Student: daftar VM miliknya saja lintas host (tanpa pemilih instance/node).
    const loadMyVms = useCallback(async () => {
        try {
            const list = await fetchMyProxmoxVms();
            setVms(list.sort((a, b) => a.vmid - b.vmid));
            setMyVmids(list.map(v => String(v.vmid)));
            setError(null);
        } catch (e) {
            setError(e?.response?.data?.detail || 'Gagal mengambil daftar VM');
        } finally {
            setLoading(false);
            setLastUpdate(Date.now());
        }
    }, []);

    useEffect(() => {
        if (canControl) return;
        loadMyVms();
        const t = setInterval(loadMyVms, 10000);
        return () => clearInterval(t);
    }, [canControl, loadMyVms]);

    const refresh = () => (canControl ? loadVms(selectedInstance, selectedNode) : loadMyVms());
    // instance/node per VM: dari VM itu sendiri (student, /my-vms) atau dari pemilih host (admin)
    const ctx = (vm) => ({ instance: vm?.instance ?? selectedInstance, node: vm?.node ?? selectedNode });

    const handleAction = async (vmid, action) => {
        if (!canControlVm(vmid)) return;
        setPendingAction(vmid);
        try {
            const c = ctx(vms.find(v => v.vmid === vmid));
            await proxmoxVmAction(c.instance, c.node, vmid, action);
            setTimeout(refresh, 1500);
        } catch (e) {
            setError(e?.response?.data?.detail || `Aksi '${action}' gagal`);
        } finally {
            setPendingAction(null);
        }
    };

    const doConnect = async (vm) => {
        setConnecting(vm.vmid);
        setError(null);
        try {
            const c = ctx(vm);
            const res = await getGuacUrl(`${c.instance}__${c.node}`, String(vm.vmid));
            applyGuacTouchInputDefault();
            window.open(appendGuacToken(res.url), '_blank');
        } catch (e) {
            const detail = e?.response?.data?.detail || '';
            if (e?.response?.status === 404 && canControl) {
                // Belum ada credentials tersimpan untuk VM ini — buka form isi credentials dulu,
                // coba auto-fill IP dari QEMU Guest Agent kalau tersedia.
                const [{ ip }, detail] = await Promise.all([
                    fetchProxmoxVmIp(ctx(vm).instance, ctx(vm).node, vm.vmid),
                    fetchProxmoxVmDetail(ctx(vm).instance, ctx(vm).node, vm.vmid).catch(() => null),
                ]);
                setCredsVm({ ...vm, network_adapters: ip ? [{ ip_addresses: [ip] }] : [], defaults: cloudInitDefaults(detail?.config) });
            } else {
                setError(detail || 'Gagal membuka koneksi Guacamole');
            }
        } finally {
            setConnecting(null);
        }
    };

    const runningCount = vms.filter(v => v.status === 'running').length;

    // Tombol aksi per VM, dipakai tabel (desktop) dan kartu (HP). Di HP tombol dibuat lebih besar
    // supaya mudah disentuh.
    const btnSize = isMobile ? { padding: '8px 14px', fontSize: 13 } : { padding: '4px 10px', fontSize: 10 };
    const renderActions = (vm) => (
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {canControlVm(vm.vmid) && (ACTIONS_BY_STATUS[vm.status] || []).map(a => (
                    <button key={a.action}
                        disabled={pendingAction === vm.vmid}
                        onClick={() => handleAction(vm.vmid, a.action)}
                        style={{
                            ...btnSize, borderRadius: 5, cursor: pendingAction === vm.vmid ? 'wait' : 'pointer',
                            background: 'transparent', border: `1px solid var(--${a.accent})`, color: `var(--${a.accent})`,
                            opacity: pendingAction === vm.vmid ? 0.5 : 1,
                        }}>
                        {pendingAction === vm.vmid ? '…' : a.label}
                    </button>
                ))}
                {canControl && (
                    <button disabled={vm.status !== 'stopped'} onClick={() => setResizeVm(vm)}
                        title={vm.status !== 'stopped' ? 'Matikan VM dulu untuk mengubah RAM/CPU/storage' : 'Ubah RAM, CPU, storage'}
                        style={{ ...btnSize, borderRadius: 5, cursor: vm.status !== 'stopped' ? 'not-allowed' : 'pointer', background: 'transparent', border: '1px solid var(--yellow)', color: 'var(--yellow)', opacity: vm.status !== 'stopped' ? 0.4 : 1 }}>
                        Resize
                    </button>
                )}
                {canControlVm(vm.vmid) && (
                    <button onClick={() => setSnapshotVm(vm)}
                        style={{ ...btnSize, borderRadius: 5, cursor: 'pointer', background: 'transparent', border: '1px solid var(--purple)', color: 'var(--purple)' }}>
                        Snapshots
                    </button>
                )}
                {vm.status === 'running' && (
                    <button disabled={connecting === vm.vmid} onClick={() => doConnect(vm)}
                        style={{ ...btnSize, borderRadius: 5, cursor: connecting === vm.vmid ? 'wait' : 'pointer', background: 'transparent', border: '1px solid var(--cyan)', color: 'var(--cyan)', opacity: connecting === vm.vmid ? 0.5 : 1 }}>
                        {connecting === vm.vmid ? '…' : 'Connect'}
                    </button>
                )}
                {sshCfg?.enabled && vm.status === 'running' && vm.manual_ip && canControlVm(vm.vmid) && (
                    <button onClick={() => setSshVm(vm)} title="Perintah SSH lewat bastion"
                        style={{ ...btnSize, borderRadius: 5, cursor: 'pointer', background: 'transparent', border: '1px solid var(--green)', color: 'var(--green)' }}>
                        SSH
                    </button>
                )}
                {!canControlVm(vm.vmid) && vm.status !== 'running' && <span style={{ color: 'var(--text3)' }}>—</span>}
            </div>
    );

    return (
        <div style={{ padding: '14px 20px', maxWidth: 1400, margin: '0 auto' }}>
            {canControl && <HostPerformancePanel />}

            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14, flexWrap: 'wrap', gap: 10 }}>
                <div>
                    <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.1em' }}>
                        Proxmox VE
                    </div>
                    <div style={{ fontSize: 13, color: 'var(--text2)', marginTop: 2 }}>
                        {vms.length} VM{vms.length !== 1 ? 's' : ''} · {runningCount} running
                        {lastUpdate && <span style={{ marginLeft: 10, color: 'var(--text3)' }}>updated {new Date(lastUpdate).toLocaleTimeString()}</span>}
                    </div>
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                    {instances.length > 1 && (
                        <div style={{ display: 'flex', gap: 6 }}>
                            {instances.map(inst => (
                                <button key={inst.label} onClick={() => setSelectedInstance(inst.label)}
                                    style={{
                                        padding: '6px 12px', fontSize: 11, borderRadius: 6, cursor: 'pointer',
                                        background: selectedInstance === inst.label ? 'var(--purple-glow)' : 'var(--bg-card)',
                                        border: `1px solid ${selectedInstance === inst.label ? 'var(--purple)' : 'var(--border)'}`,
                                        color: selectedInstance === inst.label ? 'var(--purple)' : 'var(--text2)',
                                    }}>
                                    {inst.label}
                                </button>
                            ))}
                        </div>
                    )}

                    {nodes.length > 1 && (
                        <div style={{ display: 'flex', gap: 6 }}>
                            {nodes.map(n => (
                                <button key={n.node} onClick={() => setSelectedNode(n.node)}
                                    style={{
                                        padding: '6px 12px', fontSize: 11, borderRadius: 6, cursor: 'pointer',
                                        background: selectedNode === n.node ? 'var(--cyan-glow)' : 'var(--bg-card)',
                                        border: `1px solid ${selectedNode === n.node ? 'var(--cyan)' : 'var(--border)'}`,
                                        color: selectedNode === n.node ? 'var(--cyan)' : 'var(--text2)',
                                    }}>
                                    {n.node} {n.status === 'online' ? '●' : '○'}
                                </button>
                            ))}
                        </div>
                    )}

                    {canControl && selectedInstance && selectedNode && (
                        <button onClick={() => setShowCreate(true)}
                            style={{ padding: '6px 12px', fontSize: 11, borderRadius: 6, cursor: 'pointer', background: 'var(--cyan-glow)', border: '1px solid var(--cyan)', color: 'var(--cyan)' }}>
                            + Create VM
                        </button>
                    )}
                    {canControl && (
                        <button onClick={() => setShowInstances(true)}
                            style={{ padding: '6px 12px', fontSize: 11, borderRadius: 6, cursor: 'pointer', background: 'transparent', border: '1px solid var(--border)', color: 'var(--text3)' }}>
                            ⚙ Manage Instances
                        </button>
                    )}
                </div>
            </div>

            {error && (
                <div style={{ background: 'var(--red-glow)', border: '1px solid var(--red)', borderRadius: 8, padding: '8px 14px', color: 'var(--red)', fontFamily: 'var(--fmono)', fontSize: 11, marginBottom: 14 }}>
                    ⚠ {error}
                </div>
            )}

            {notice && (
                <div style={{ background: '#4ade8012', border: '1px solid #4ade8055', borderRadius: 8, padding: '8px 14px', color: '#4ade80', fontSize: 11, marginBottom: 14, display: 'flex', justifyContent: 'space-between', gap: 10 }}>
                    <span>✓ {notice}</span>
                    <button onClick={() => setNotice(null)} style={{ background: 'transparent', border: 'none', color: 'inherit', cursor: 'pointer', fontSize: 14 }}>×</button>
                </div>
            )}

            {canControl && instances.length === 0 && !loading ? (
                <div style={{ padding: 30, textAlign: 'center', color: 'var(--text3)', fontSize: 13 }}>
                    Belum ada Proxmox instance dikonfigurasi.
                    {canControl && <div style={{ marginTop: 10 }}>
                        <button onClick={() => setShowInstances(true)}
                            style={{ padding: '6px 14px', fontSize: 12, borderRadius: 6, cursor: 'pointer', background: 'var(--cyan-glow)', border: '1px solid var(--cyan)', color: 'var(--cyan)' }}>
                            + Tambah Instance
                        </button>
                    </div>}
                </div>
            ) : isMobile ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {loading && <div style={{ padding: 20, textAlign: 'center', color: 'var(--text3)' }}>Loading…</div>}
                {!loading && vms.length === 0 && (
                    <div style={{ padding: 20, textAlign: 'center', color: 'var(--text3)', background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 8 }}>
                        {canControl ? 'Belum ada VM di node ini. Buat VM/template di Proxmox terlebih dahulu.' : 'Belum ada VM yang ditugaskan kepada Anda.'}
                    </div>
                )}
                {!loading && vms.map(vm => (
                    <div key={vm.vmid} style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 10, padding: 14, display: 'flex', flexDirection: 'column', gap: 10 }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 10 }}>
                            <button onClick={() => setDetailVm(vm)}
                                style={{ background: 'transparent', border: 'none', padding: 0, textAlign: 'left', color: 'var(--text)', cursor: 'pointer', minWidth: 0 }}>
                                <div style={{ fontSize: 15, fontWeight: 600, overflowWrap: 'anywhere', textDecoration: 'underline', textDecorationColor: 'var(--border-light)' }}>{vm.name || '—'}</div>
                                <div style={{ fontSize: 11, color: 'var(--text3)', fontFamily: 'var(--fmono)', marginTop: 2 }}>VMID {vm.vmid}</div>
                            </button>
                            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, flexShrink: 0, fontSize: 12, color: STATUS_COLOR[vm.status] || 'var(--text3)' }}>
                                <span style={{ width: 7, height: 7, borderRadius: '50%', background: 'currentColor' }} />
                                {vm.status}
                            </span>
                        </div>
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: '6px 12px', fontSize: 12, fontFamily: 'var(--fmono)', color: 'var(--text2)' }}>
                            <div><span style={{ color: 'var(--text3)' }}>IP </span>{vm.ip || vm.manual_ip || '—'}</div>
                            <div><span style={{ color: 'var(--text3)' }}>CPU </span>{vm.cpus ? `${vm.cpus} vCPU` : '—'}{vm.cpu != null && vm.status === 'running' ? ` (${(vm.cpu * 100).toFixed(0)}%)` : ''}</div>
                            <div><span style={{ color: 'var(--text3)' }}>RAM </span>{formatBytes(vm.mem)} / {formatBytes(vm.maxmem)}</div>
                            <div><span style={{ color: 'var(--text3)' }}>Uptime </span>{vm.status === 'running' ? formatUptime(vm.uptime) : '—'}</div>
                        </div>
                        {renderActions(vm)}
                    </div>
                ))}
            </div>
            ) : (
            <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                    <thead>
                        <tr style={{ background: 'var(--bg-card2)', textAlign: 'left' }}>
                            {['VMID', 'Name', 'Status', 'IP', 'CPU', 'Memory', 'Uptime', 'Actions'].map(h => (
                                <th key={h} style={{ padding: '8px 12px', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text3)', fontWeight: 600 }}>{h}</th>
                            ))}
                        </tr>
                    </thead>
                    <tbody>
                        {loading && (
                            <tr><td colSpan={8} style={{ padding: 20, textAlign: 'center', color: 'var(--text3)' }}>Loading…</td></tr>
                        )}
                        {!loading && vms.length === 0 && (
                            <tr><td colSpan={8} style={{ padding: 20, textAlign: 'center', color: 'var(--text3)' }}>
                                {canControl ? 'Belum ada VM di node ini. Buat VM/template di Proxmox terlebih dahulu.' : 'Belum ada VM yang ditugaskan kepada Anda.'}
                            </td></tr>
                        )}
                        {vms.map(vm => (
                            <tr key={vm.vmid} style={{ borderTop: '1px solid var(--border)' }}>
                                <td style={{ padding: '8px 12px', fontFamily: 'var(--fmono)', color: 'var(--text2)' }}>{vm.vmid}</td>
                                <td style={{ padding: '8px 12px' }}>
                                    <button onClick={() => setDetailVm(vm)}
                                        style={{ background: 'transparent', border: 'none', padding: 0, color: 'var(--text)', cursor: 'pointer', textDecoration: 'underline', textDecorationColor: 'var(--border-light)', fontSize: 12 }}>
                                        {vm.name || '—'}
                                    </button>
                                </td>
                                <td style={{ padding: '8px 12px' }}>
                                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, color: STATUS_COLOR[vm.status] || 'var(--text3)' }}>
                                        <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'currentColor' }} />
                                        {vm.status}
                                    </span>
                                </td>
                                <td style={{ padding: '8px 12px', fontFamily: 'var(--fmono)', color: 'var(--text2)', whiteSpace: 'nowrap' }}
                                    title={vm.ip && vm.manual_ip && vm.ip !== vm.manual_ip ? `manual: ${vm.manual_ip}` : undefined}>
                                    {vm.ip || vm.manual_ip || '—'}
                                    {!vm.ip && vm.manual_ip && <span style={{ marginLeft: 5, fontSize: 9, color: 'var(--text3)', fontFamily: 'inherit' }}>manual</span>}
                                </td>
                                <td style={{ padding: '8px 12px', fontFamily: 'var(--fmono)', color: 'var(--text2)' }}>
                                    {vm.cpus ? `${vm.cpus} vCPU` : '—'}{vm.cpu != null && vm.status === 'running' ? ` (${(vm.cpu * 100).toFixed(0)}%)` : ''}
                                </td>
                                <td style={{ padding: '8px 12px', fontFamily: 'var(--fmono)', color: 'var(--text2)' }}>
                                    {formatBytes(vm.mem)} / {formatBytes(vm.maxmem)}
                                </td>
                                <td style={{ padding: '8px 12px', fontFamily: 'var(--fmono)', color: 'var(--text2)' }}>
                                    {vm.status === 'running' ? formatUptime(vm.uptime) : '—'}
                                </td>
                                <td style={{ padding: '8px 12px' }}>
                                    {renderActions(vm)}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
            )}

            {sshVm && sshCfg && (
                <SshCommandModal vm={sshVm} cfg={sshCfg} onClose={() => setSshVm(null)} />
            )}

            {resizeVm && (
                <ProxmoxResizeModal
                    instance={ctx(resizeVm).instance}
                    node={ctx(resizeVm).node}
                    vmid={resizeVm.vmid}
                    vmName={resizeVm.name}
                    onClose={() => setResizeVm(null)}
                    onSaved={refresh}
                />
            )}

            {snapshotVm && (
                <ProxmoxSnapshotModal
                    instance={ctx(snapshotVm).instance}
                    node={ctx(snapshotVm).node}
                    maskHost={!canControl}
                    vmid={snapshotVm.vmid}
                    vmName={snapshotVm.name}
                    canDelete={canControl}
                    onClose={() => setSnapshotVm(null)}
                />
            )}

            {credsVm && (
                <SshCredModal
                    vm={{ vm_id: String(credsVm.vmid), vm_name: credsVm.name, network_adapters: credsVm.network_adapters || [] }}
                    defaults={credsVm.defaults}
                    hostName={`${ctx(credsVm).instance}__${ctx(credsVm).node}`}
                    onClose={() => setCredsVm(null)}
                    onSaved={() => { setCredsVm(null); doConnect(credsVm); }}
                />
            )}

            {showCreate && (
                <CreateVmModal
                    instance={selectedInstance}
                    node={selectedNode}
                    onClose={() => setShowCreate(false)}
                    onCreated={() => loadVms(selectedInstance, selectedNode)}
                />
            )}

            {detailVm && (
                <ProxmoxVmDetailModal
                    instance={ctx(detailVm).instance}
                    node={ctx(detailVm).node}
                    maskHost={!canControl}
                    vmid={detailVm.vmid}
                    onClose={() => setDetailVm(null)}
                    onDeleted={(res) => { setDetailVm(null); setNotice(deleteSummary(res)); refresh(); }}
                />
            )}

            {showInstances && (
                <ProxmoxInstancesModal
                    onClose={() => setShowInstances(false)}
                    onChanged={loadInstances}
                />
            )}
        </div>
    );
}
