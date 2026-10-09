import { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import {
    fetchProxmoxInstances, fetchProxmoxNodes, fetchProxmoxVms, fetchMyProxmoxVms, proxmoxVmAction,
    getGuacUrl, appendGuacToken, applyGuacTouchInputDefault, fetchSshConfig, fetchProxmoxVmIp, fetchMyAssignedVmids, fetchProxmoxVmDetail,
} from '../api';
import { formatBytes, formatUptime, formatCcdId, leaseInfo } from '../format';
import ProxmoxSnapshotModal from '../components/ProxmoxSnapshotModal';
import SshCredModal from '../components/SshCredModal';
import ProxmoxVmDetailModal from '../components/ProxmoxVmDetailModal';
import ProxmoxInstancesModal from '../components/ProxmoxInstancesModal';
import HostPerformancePanel from '../components/HostPerformancePanel';
import CreateVmModal from '../components/CreateVmModal';
import BulkVmModal from '../components/BulkVmModal';
import ProxmoxResizeModal from '../components/ProxmoxResizeModal';
import { cloudInitDefaults } from '../proxmoxCloudInit';
import useIsMobile from '../useIsMobile';
import { t as translate, useT } from '../i18n';
import SshCommandModal from '../components/SshCommandModal';

const STATUS_COLOR = {
    running: 'var(--green)',
    stopped: 'var(--text3)',
    paused:  'var(--yellow)',
};

function deleteSummary(r) {
    const rows = Object.values(r.dashboard_rows_removed || {}).reduce((a, b) => a + b, 0);
    const left = r.disks_left || [];
    return translate('servers.deleted', {
        vmid: r.vmid, name: r.name, stopped: r.stopped_first ? translate('servers.deletedStopped') : '',
        disks: left.length ? left.join(', ') : translate('servers.noDisks'),
        conns: r.guacamole_connections_removed, guacErr: r.guacamole_error ? ` (⚠ ${r.guacamole_error})` : '', rows,
    });
}

// Urut VMID, lalu host, supaya urutan VMID kembar dari host berbeda tidak berubah-ubah.
const byVmid = (a, b) => a.vmid - b.vmid || `${a.instance}/${a.node}`.localeCompare(`${b.instance}/${b.node}`);

const ACTIONS_BY_STATUS = {
    running: [
        { action: 'shutdown', label: 'action.shutdown', accent: 'yellow' },
        { action: 'stop',     label: 'action.stop', accent: 'red' },
        { action: 'reboot',   label: 'action.reboot', accent: 'cyan' },
    ],
    stopped: [
        { action: 'start', label: 'action.start', accent: 'green' },
    ],
    paused: [
        { action: 'resume', label: 'action.resume', accent: 'green' },
    ],
};

export default function ProxmoxPage({ currentUser }) {
    const navigate = useNavigate();
    const [instances, setInstances] = useState([]);
    const [selectedInstance, setSelectedInstance] = useState(null);
    const [showInstances, setShowInstances] = useState(false);
    const [nodes, setNodes] = useState([]);
    const [selectedNode, setSelectedNode] = useState(null);
    const [vms, setVms] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [notice, setNotice] = useState(null);
    const [pendingAction, setPendingAction] = useState(null); // vmKey VM yang sedang menjalankan aksi
    const [lastUpdate, setLastUpdate] = useState(null);
    const pollRef = useRef(null);
    const [resizeVm, setResizeVm] = useState(null);       // vm sedang dibuka modal resize-nya (admin)
    const [snapshotVm, setSnapshotVm] = useState(null);   // vm sedang dibuka modal snapshot-nya
    const [credsVm, setCredsVm] = useState(null);          // vm sedang dibuka modal credentials-nya
    const [detailVm, setDetailVm] = useState(null);        // vm sedang dibuka modal detail-nya
    const [showCreate, setShowCreate] = useState(false);
    const [showBulk, setShowBulk] = useState(false);
    const [connecting, setConnecting] = useState(null);    // vmKey VM yang sedang proses connect

    const canControl = ['superadmin', 'sysadmin'].includes(currentUser?.role);
    const isMobile = useIsMobile();
    const t = useT();
    const [sshCfg, setSshCfg] = useState(null);     // konfigurasi bastion SSH (null = belum dimuat)
    const [sshVm, setSshVm] = useState(null);       // vm yang sedang dibuka modal perintah SSH-nya
    useEffect(() => { fetchSshConfig().then(setSshCfg); }, []);
    const [myVmKeys, setMyVmKeys] = useState([]); // vmKey VM yang di-assign ke user student ini


    const loadInstances = useCallback(async () => {
        if (!canControl) return; // student tidak boleh melihat daftar host — pakai /my-vms
        try {
            const list = await fetchProxmoxInstances();
            setInstances(list);
            setError(null);
            if (!selectedInstance && list.length > 0) setSelectedInstance(list[0].label);
            if (list.length === 0 && canControl) setShowInstances(true);
        } catch (e) {
            setError(e?.response?.data?.detail || translate('servers.loadInstancesFailed'));
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
            setError(e?.response?.data?.detail || translate('servers.proxmoxUnreachable'));
        }
    }, []);

    const loadVms = useCallback(async (instance, node) => {
        if (!instance || !node) return;
        try {
            const list = await fetchProxmoxVms(instance, node);
            setVms(list.sort(byVmid));
            setError(null);
        } catch (e) {
            setError(e?.response?.data?.detail || translate('servers.loadVmsFailed'));
        } finally {
            setLoading(false);
            setLastUpdate(Date.now());
        }
    }, []);

    useEffect(() => { loadInstances(); }, [loadInstances]);

    // Ganti instance: kosongkan node dulu. Kalau tidak, daftar VM sempat diminta dengan instance baru
    // dan node instance lama (mis. Proxmox1 + node milik Proxmox2), yang ditolak Proxmox.
    const selectInstance = (label) => {
        if (label === selectedInstance) return;
        setSelectedInstance(label);
        setSelectedNode(null);
        setNodes([]);
        setVms([]);
        setLoading(true);
    };

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
        fetchMyAssignedVmids(selectedInstance, selectedNode)
            .then(r => setMyVmKeys((r.vmids || []).map(id => `${selectedInstance}__${selectedNode}__${id}`)));
    }, [selectedInstance, selectedNode, canControl]);

    // Student: daftar VM miliknya saja lintas host (tanpa pemilih instance/node).
    const loadMyVms = useCallback(async () => {
        try {
            const list = await fetchMyProxmoxVms();
            setVms(list.sort(byVmid));
            setMyVmKeys(list.map(v => `${v.instance}__${v.node}__${v.vmid}`));
            setError(null);
        } catch (e) {
            setError(e?.response?.data?.detail || translate('servers.loadVmsFailed'));
        } finally {
            setLoading(false);
            setLastUpdate(Date.now());
        }
    }, []);

    useEffect(() => {
        if (canControl) return;
        loadMyVms();
        const timer = setInterval(loadMyVms, 10000);
        return () => clearInterval(timer);
    }, [canControl, loadMyVms]);

    const refresh = () => (canControl ? loadVms(selectedInstance, selectedNode) : loadMyVms());
    // instance/node per VM: dari VM itu sendiri (student, /my-vms) atau dari pemilih host (admin)
    const ctx = (vm) => ({ instance: vm?.instance ?? selectedInstance, node: vm?.node ?? selectedNode });
    // VMID hanya unik di dalam satu Proxmox. Daftar VM mahasiswa menggabungkan beberapa host, jadi
    // setiap VM dikenali dari host + VMID, bukan VMID saja.
    const vmKey = (vm) => { const c = ctx(vm); return `${c.instance}__${c.node}__${vm.vmid}`; };
    // Akses 'Hanya Open Web': mahasiswa hanya melihat status dan membuka web, tanpa power, snapshot, dan Connect.
    const isWebOnly = (vm) => !canControl && vm.access === 'web';
    const canControlVm = (vm) => canControl || (myVmKeys.includes(vmKey(vm)) && !isWebOnly(vm));
    const openWeb = (vm) => {
        const ip = vm.manual_ip || vm.ip;
        if (ip) { try { localStorage.setItem('ccd-openweb-url', `http://${ip}`); } catch { /* storage tidak tersedia */ } }
        navigate('/openweb');
    };

    // Mahasiswa melihat CCDID (unik di seluruh dashboard), bukan VMID yang bisa kembar antar-Proxmox.
    // Admin melihat keduanya.
    const columns = canControl
        ? [t('servers.colVmid'), t('servers.colCcdid'), t('servers.colName'), t('servers.colStatus'), t('lease.col'), t('servers.colIp'), t('servers.colCpu'), t('servers.colMemory'), t('servers.colUptime'), t('servers.colActions')]
        : [t('servers.colCcdid'), t('servers.colName'), t('servers.colStatus'), t('lease.col'), t('servers.colIp'), t('servers.colCpu'), t('servers.colMemory'), t('servers.colUptime'), t('servers.colActions')];

    const handleAction = async (vm, action) => {
        if (!canControlVm(vm)) return;
        setPendingAction(vmKey(vm));
        try {
            const c = ctx(vm);
            await proxmoxVmAction(c.instance, c.node, vm.vmid, action);
            setTimeout(refresh, 1500);
        } catch (e) {
            setError(e?.response?.data?.detail || t('action.failed', { action: t(`action.${action}`) }));
        } finally {
            setPendingAction(null);
        }
    };

    const doConnect = async (vm) => {
        setConnecting(vmKey(vm));
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
                setError(detail || t('servers.connectFailed'));
            }
        } finally {
            setConnecting(null);
        }
    };

    const runningCount = vms.filter(v => v.status === 'running').length;
    const statusLabel = (st) => (['running', 'stopped', 'paused'].includes(st) ? t(`vmstatus.${st}`) : st);

    // Tombol aksi per VM, dipakai tabel (desktop) dan kartu (HP). Di HP tombol dibuat lebih besar
    // supaya mudah disentuh.
    const btnSize = isMobile ? { padding: '8px 14px', fontSize: 13 } : { padding: '4px 10px', fontSize: 10 };
    const renderActions = (vm) => {
        const key = vmKey(vm);
        // Masa sewa habis: mahasiswa tidak bisa menyalakan VM lagi (backend juga menolak).
        const leaseBlocked = !canControl && leaseInfo(vm.lease_until).expired;
        const actions = (ACTIONS_BY_STATUS[vm.status] || []).filter(a => !(leaseBlocked && ['start', 'resume', 'reboot'].includes(a.action)));
        return (
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {leaseBlocked && <span title={t('lease.blockedHint')} style={{ ...btnSize, borderRadius: 5, border: '1px solid var(--red)', color: 'var(--red)' }}>{t('lease.blocked')}</span>}
                {canControlVm(vm) && actions.map(a => (
                    <button key={a.action}
                        disabled={pendingAction === key}
                        onClick={() => handleAction(vm, a.action)}
                        style={{
                            ...btnSize, borderRadius: 5, cursor: pendingAction === key ? 'wait' : 'pointer',
                            background: 'transparent', border: `1px solid var(--${a.accent})`, color: `var(--${a.accent})`,
                            opacity: pendingAction === key ? 0.5 : 1,
                        }}>
                        {pendingAction === key ? '…' : t(a.label)}
                    </button>
                ))}
                {canControl && (
                    <button disabled={vm.status !== 'stopped'} onClick={() => setResizeVm(vm)}
                        title={vm.status !== 'stopped' ? t('servers.resizeNeedsStop') : t('servers.resizeHint')}
                        style={{ ...btnSize, borderRadius: 5, cursor: vm.status !== 'stopped' ? 'not-allowed' : 'pointer', background: 'transparent', border: '1px solid var(--yellow)', color: 'var(--yellow)', opacity: vm.status !== 'stopped' ? 0.4 : 1 }}>
                        {t('servers.resize')}
                    </button>
                )}
                {canControlVm(vm) && (
                    <button onClick={() => setSnapshotVm(vm)}
                        style={{ ...btnSize, borderRadius: 5, cursor: 'pointer', background: 'transparent', border: '1px solid var(--purple)', color: 'var(--purple)' }}>
                        {t('servers.snapshots')}
                    </button>
                )}
                {isWebOnly(vm) && vm.status === 'running' && (vm.manual_ip || vm.ip) && (
                    <button onClick={() => openWeb(vm)} title={t('servers.openWebHint')}
                        style={{ ...btnSize, borderRadius: 5, cursor: 'pointer', background: 'transparent', border: '1px solid var(--cyan)', color: 'var(--cyan)' }}>
                        {t('servers.openWeb')}
                    </button>
                )}
                {vm.status === 'running' && !isWebOnly(vm) && (
                    <button disabled={connecting === key} onClick={() => doConnect(vm)}
                        style={{ ...btnSize, borderRadius: 5, cursor: connecting === key ? 'wait' : 'pointer', background: 'transparent', border: '1px solid var(--cyan)', color: 'var(--cyan)', opacity: connecting === key ? 0.5 : 1 }}>
                        {connecting === key ? '…' : t('servers.connect')}
                    </button>
                )}
                {sshCfg?.enabled && vm.status === 'running' && vm.manual_ip && canControlVm(vm) && (
                    <button onClick={() => setSshVm(vm)} title={t('servers.sshHint')}
                        style={{ ...btnSize, borderRadius: 5, cursor: 'pointer', background: 'transparent', border: '1px solid var(--green)', color: 'var(--green)' }}>
                        SSH
                    </button>
                )}
                {!canControlVm(vm) && (vm.status !== 'running' || (isWebOnly(vm) && !(vm.manual_ip || vm.ip))) && <span style={{ color: 'var(--text3)' }}>—</span>}
            </div>
        );
    };

    return (
        <div style={{ padding: '14px 20px', maxWidth: 1400, margin: '0 auto' }}>
            {canControl && <HostPerformancePanel />}

            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14, flexWrap: 'wrap', gap: 10 }}>
                <div>
                    <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.1em' }}>
                        {t('servers.heading')}
                    </div>
                    <div style={{ fontSize: 13, color: 'var(--text2)', marginTop: 2 }}>
                        {t('servers.vms', { n: vms.length, running: runningCount })}
                        {lastUpdate && <span style={{ marginLeft: 10, color: 'var(--text3)' }}>{t('servers.updated', { time: new Date(lastUpdate).toLocaleTimeString() })}</span>}
                    </div>
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                    {instances.length > 1 && (
                        <div style={{ display: 'flex', gap: 6 }}>
                            {instances.map(inst => (
                                <button key={inst.label} onClick={() => selectInstance(inst.label)}
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
                            {t('servers.createVm')}
                        </button>
                    )}
                    {canControl && selectedInstance && selectedNode && (
                        <button onClick={() => setShowBulk(true)}
                            style={{ padding: '6px 12px', fontSize: 11, borderRadius: 6, cursor: 'pointer', background: 'var(--cyan-glow)', border: '1px solid var(--cyan)', color: 'var(--cyan)' }}>
                            {t('servers.bulk')}
                        </button>
                    )}
                    {canControl && (
                        <button onClick={() => setShowInstances(true)}
                            style={{ padding: '6px 12px', fontSize: 11, borderRadius: 6, cursor: 'pointer', background: 'transparent', border: '1px solid var(--border)', color: 'var(--text3)' }}>
                            {t('servers.manage')}
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
                    <button onClick={() => setNotice(null)} aria-label={t('servers.closeNotice')} style={{ background: 'transparent', border: 'none', color: 'inherit', cursor: 'pointer', fontSize: 14 }}>×</button>
                </div>
            )}

            {canControl && instances.length === 0 && !loading ? (
                <div style={{ padding: 30, textAlign: 'center', color: 'var(--text3)', fontSize: 13 }}>
                    {t('servers.noInstances')}
                    {canControl && <div style={{ marginTop: 10 }}>
                        <button onClick={() => setShowInstances(true)}
                            style={{ padding: '6px 14px', fontSize: 12, borderRadius: 6, cursor: 'pointer', background: 'var(--cyan-glow)', border: '1px solid var(--cyan)', color: 'var(--cyan)' }}>
                            {t('servers.addInstance')}
                        </button>
                    </div>}
                </div>
            ) : isMobile ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {loading && <div style={{ padding: 20, textAlign: 'center', color: 'var(--text3)' }}>{t('common.loading')}</div>}
                {!loading && vms.length === 0 && (
                    <div style={{ padding: 20, textAlign: 'center', color: 'var(--text3)', background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 8 }}>
                        {canControl ? t('servers.emptyAdmin') : t('servers.emptyStudent')}
                    </div>
                )}
                {!loading && vms.map(vm => (
                    <div key={vmKey(vm)} style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 10, padding: 14, display: 'flex', flexDirection: 'column', gap: 10 }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 10 }}>
                            <button onClick={() => setDetailVm(vm)}
                                style={{ background: 'transparent', border: 'none', padding: 0, textAlign: 'left', color: 'var(--text)', cursor: 'pointer', minWidth: 0 }}>
                                <div style={{ fontSize: 15, fontWeight: 600, overflowWrap: 'anywhere', textDecoration: 'underline', textDecorationColor: 'var(--border-light)' }}>{vm.name || '—'}</div>
                                <div style={{ fontSize: 11, color: 'var(--text3)', fontFamily: 'var(--fmono)', marginTop: 2 }}>{canControl ? `VMID ${vm.vmid} · ` : ''}{formatCcdId(vm.ccd_id)}</div>
                            </button>
                            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, flexShrink: 0, fontSize: 12, color: STATUS_COLOR[vm.status] || 'var(--text3)' }}>
                                <span style={{ width: 7, height: 7, borderRadius: '50%', background: 'currentColor' }} />
                                {statusLabel(vm.status)}
                            </span>
                        </div>
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: '6px 12px', fontSize: 12, fontFamily: 'var(--fmono)', color: 'var(--text2)' }}>
                            <div><span style={{ color: 'var(--text3)' }}>IP </span>{vm.ip || vm.manual_ip || '—'}</div>
                            <div><span style={{ color: 'var(--text3)' }}>CPU </span>{vm.cpus ? `${vm.cpus} vCPU` : '—'}{vm.cpu != null && vm.status === 'running' ? ` (${(vm.cpu * 100).toFixed(0)}%)` : ''}</div>
                            <div><span style={{ color: 'var(--text3)' }}>RAM </span>{formatBytes(vm.mem)} / {formatBytes(vm.maxmem)}</div>
                            <div><span style={{ color: 'var(--text3)' }}>Uptime </span>{vm.status === 'running' ? formatUptime(vm.uptime) : '—'}</div>
                            {vm.lease_until && (
                                <div style={{ gridColumn: '1 / -1', color: leaseInfo(vm.lease_until).color }}>
                                    <span style={{ color: 'var(--text3)' }}>{t('lease.col')} </span>{leaseInfo(vm.lease_until).text}
                                </div>
                            )}
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
                            {columns.map(h => (
                                <th key={h} style={{ padding: '8px 12px', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text3)', fontWeight: 600 }}>{h}</th>
                            ))}
                        </tr>
                    </thead>
                    <tbody>
                        {loading && (
                            <tr><td colSpan={columns.length} style={{ padding: 20, textAlign: 'center', color: 'var(--text3)' }}>{t('common.loading')}</td></tr>
                        )}
                        {!loading && vms.length === 0 && (
                            <tr><td colSpan={columns.length} style={{ padding: 20, textAlign: 'center', color: 'var(--text3)' }}>
                                {canControl ? t('servers.emptyAdmin') : t('servers.emptyStudent')}
                            </td></tr>
                        )}
                        {vms.map(vm => (
                            <tr key={vmKey(vm)} style={{ borderTop: '1px solid var(--border)' }}>
                                {canControl && <td style={{ padding: '8px 12px', fontFamily: 'var(--fmono)', color: 'var(--text2)' }}>{vm.vmid}</td>}
                                <td style={{ padding: '8px 12px', fontFamily: 'var(--fmono)', color: 'var(--text2)', whiteSpace: 'nowrap' }}>{formatCcdId(vm.ccd_id)}</td>
                                <td style={{ padding: '8px 12px' }}>
                                    <button onClick={() => setDetailVm(vm)}
                                        style={{ background: 'transparent', border: 'none', padding: 0, color: 'var(--text)', cursor: 'pointer', textDecoration: 'underline', textDecorationColor: 'var(--border-light)', fontSize: 12 }}>
                                        {vm.name || '—'}
                                    </button>
                                </td>
                                <td style={{ padding: '8px 12px' }}>
                                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, color: STATUS_COLOR[vm.status] || 'var(--text3)' }}>
                                        <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'currentColor' }} />
                                        {statusLabel(vm.status)}
                                    </span>
                                </td>
                                <td style={{ padding: '8px 12px', fontFamily: 'var(--fmono)', whiteSpace: 'nowrap', color: leaseInfo(vm.lease_until).color }}
                                    title={leaseInfo(vm.lease_until).text}>
                                    {leaseInfo(vm.lease_until).short}
                                </td>
                                <td style={{ padding: '8px 12px', fontFamily: 'var(--fmono)', color: 'var(--text2)', whiteSpace: 'nowrap' }}
                                    title={vm.ip && vm.manual_ip && vm.ip !== vm.manual_ip ? t('servers.manualHint', { ip: vm.manual_ip }) : undefined}>
                                    {vm.ip || vm.manual_ip || '—'}
                                    {!vm.ip && vm.manual_ip && <span style={{ marginLeft: 5, fontSize: 9, color: 'var(--text3)', fontFamily: 'inherit' }}>{t('servers.manual')}</span>}
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
                    ccdId={snapshotVm.ccd_id}
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

            {showBulk && (
                <BulkVmModal
                    instance={selectedInstance}
                    node={selectedNode}
                    onClose={() => setShowBulk(false)}
                    onChanged={() => loadVms(selectedInstance, selectedNode)}
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
                    ccdId={detailVm.ccd_id}
                    vmName={detailVm.name}
                    leaseUntil={detailVm.lease_until}
                    onLeaseChanged={(lease_until) => { setDetailVm(v => ({ ...v, lease_until })); refresh(); }}
                    onClose={() => setDetailVm(null)}
                    onDeleted={(res) => { setDetailVm(null); setNotice(deleteSummary(res)); refresh(); }}
                />
            )}

            {showInstances && (
                <ProxmoxInstancesModal
                    onClose={() => setShowInstances(false)}
                    onChanged={loadInstances}
                    canModify={currentUser?.role === 'superadmin'}
                />
            )}
        </div>
    );
}
