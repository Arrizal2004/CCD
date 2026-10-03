import { useState, useEffect, useCallback } from 'react';
import { fetchProxmoxVmDetail, fetchProxmoxVmIp, enableProxmoxGuestAgent, getVmCreds, revealVmPassword } from '../api';
import { cloudInitDefaults } from '../proxmoxCloudInit';
import { formatBytes, formatUptime } from '../format';
import ProxmoxAssignmentsPanel from './ProxmoxAssignmentsPanel';
import ProxmoxVmHistoryChart from './ProxmoxVmHistoryChart';
import SshCredModal from './SshCredModal';
import VmDeletePanel from './VmDeletePanel';

const OS_TYPE_LABEL = {
    l26: 'Linux (2.6+ kernel)', l24: 'Linux (2.4 kernel)',
    win11: 'Windows 11', win10: 'Windows 10', win8: 'Windows 8', win7: 'Windows 7',
    solaris: 'Solaris', other: 'Other',
};

function parseDisks(config) {
    const busPattern = /^(scsi|sata|ide|virtio)(\d+)$/;
    return Object.entries(config)
        .filter(([k]) => busPattern.test(k))
        .map(([k, v]) => {
            const isCdrom = /media=cdrom/.test(v);
            const sizeMatch = v.match(/size=([\d.]+[KMGT]?)/i);
            const storageMatch = v.match(/^([^,:]+):/);
            return {
                bus: k,
                isCdrom,
                storage: storageMatch ? storageMatch[1] : (isCdrom ? '(cdrom)' : '—'),
                size: sizeMatch ? sizeMatch[1] : (isCdrom ? '—' : '—'),
                raw: v,
            };
        })
        .sort((a, b) => a.bus.localeCompare(b.bus));
}

function parseNics(config) {
    const nicPattern = /^net(\d+)$/;
    return Object.entries(config)
        .filter(([k]) => nicPattern.test(k))
        .map(([k, v]) => {
            const modelMatch = v.match(/^(\w+)=([0-9A-Fa-f:]+)/);
            const bridgeMatch = v.match(/bridge=([^,]+)/);
            const vlanMatch = v.match(/tag=(\d+)/);
            return {
                nic: k,
                model: modelMatch ? modelMatch[1] : '—',
                mac: modelMatch ? modelMatch[2] : '—',
                bridge: bridgeMatch ? bridgeMatch[1] : '—',
                vlan: vlanMatch ? vlanMatch[1] : null,
            };
        })
        .sort((a, b) => a.nic.localeCompare(b.nic));
}

function Row({ label, value }) {
    return (
        <div style={{ display: 'flex', justifyContent: 'space-between', padding: '5px 0', borderBottom: '1px solid var(--border)' }}>
            <span style={{ color: 'var(--text3)', fontSize: 11 }}>{label}</span>
            <span style={{ color: 'var(--text)', fontSize: 12, fontFamily: 'var(--fmono)' }}>{value}</span>
        </div>
    );
}

const IP_REASON = {
    agent_disabled:    'tidak terdeteksi — opsi QEMU Guest Agent nonaktif di VM ini',
    agent_not_running: 'tidak terdeteksi — agent belum berjalan di guest (pasang qemu-guest-agent, lalu Shutdown → Start VM)',
    no_ipv4:           'agent aktif, tapi guest belum punya IPv4',
};

// Connect memakai manual_ip (SSH Host di kredensial); IP agent hanya info + auto-fill.
function ipLabel(info) {
    if (!info) return '…';
    const manual = info.manual_ip;
    if (info.ip) return manual && manual !== info.ip ? `${info.ip} (agent) · manual: ${manual}` : info.ip;
    if (manual) return `${manual} (manual)`;
    return IP_REASON[info.reason] || 'tidak terdeteksi';
}

function currentRole() {
    try { return JSON.parse(localStorage.getItem('hv_user'))?.role; } catch { return null; }
}

export default function ProxmoxVmDetailModal({ instance, node, vmid, maskHost = false, onClose, onDeleted }) {
    const [data, setData] = useState(null);
    const [error, setError] = useState(null);
    const [loading, setLoading] = useState(true);
    const [tab, setTab] = useState('info'); // 'info' | 'history' | 'assignments'
    const [ipInfo, setIpInfo] = useState(null);
    const [agentBusy, setAgentBusy] = useState(false);
    const [showCreds, setShowCreds] = useState(false);
    const [creds, setCreds] = useState(null);      // null = loading, 'none' = not configured
    const [password, setPassword] = useState(null);
    const isAdmin = ['superadmin', 'sysadmin'].includes(currentRole());

    function loadCreds() {
        setPassword(null);
        getVmCreds(`${instance}__${node}`, String(vmid)).then(setCreds).catch(() => setCreds('none'));
    }

    const load = useCallback(async () => {
        try {
            const d = await fetchProxmoxVmDetail(instance, node, vmid);
            setData(d);
            setError(null);
        } catch (e) {
            setError(e?.response?.data?.detail || 'Gagal mengambil detail VM');
        } finally {
            setLoading(false);
        }
        fetchProxmoxVmIp(instance, node, vmid).then(setIpInfo);
        if (isAdmin) loadCreds();
    }, [instance, node, vmid]);

    const enableAgent = async () => {
        setAgentBusy(true);
        try {
            await enableProxmoxGuestAgent(instance, node, vmid);
            setIpInfo(await fetchProxmoxVmIp(instance, node, vmid));
        } catch (e) {
            setError(e?.response?.data?.detail || 'Gagal mengaktifkan QEMU Guest Agent');
        } finally {
            setAgentBusy(false);
        }
    };

    const togglePassword = async () => {
        if (password !== null) { setPassword(null); return; }
        try {
            setPassword((await revealVmPassword(`${instance}__${node}`, String(vmid))).password);
        } catch (e) {
            setError(e?.response?.data?.detail || 'Gagal menampilkan password');
        }
    };

    useEffect(() => { load(); }, [load]);

    const config = data?.config || {};
    const status = data?.status || {};
    const disks = data ? parseDisks(config) : [];
    const nics = data ? parseNics(config) : [];

    return (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 200 }} onClick={onClose}>
            <div style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', borderRadius: 10, width: 640, maxWidth: '92vw', maxHeight: '85vh', overflow: 'auto', padding: 20 }} onClick={e => e.stopPropagation()}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
                    <div>
                        <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text)' }}>{config.name || `VM ${vmid}`}</div>
                        <div style={{ fontSize: 11, color: 'var(--text3)' }}>
                            {maskHost ? '' : `${instance}/${node} · `}VMID {vmid} · {status.qmpstatus === 'running'
                                ? <span style={{ color: 'var(--green)' }}>running</span>
                                : <span style={{ color: 'var(--text3)' }}>{status.qmpstatus || 'unknown'}</span>}
                        </div>
                    </div>
                    <button onClick={onClose} style={{ background: 'transparent', border: 'none', color: 'var(--text3)', fontSize: 18, cursor: 'pointer' }}>×</button>
                </div>

                <div style={{ display: 'flex', gap: 4, marginBottom: 14, borderBottom: '1px solid var(--border)' }}>
                    {[{ id: 'info', label: 'Info' }, { id: 'history', label: 'History' }, { id: 'assignments', label: 'Assignments' }].map(t => (
                        <button key={t.id} onClick={() => setTab(t.id)}
                            style={{
                                padding: '6px 12px', fontSize: 12, cursor: 'pointer', background: 'transparent', border: 'none',
                                borderBottom: `2px solid ${tab === t.id ? 'var(--cyan)' : 'transparent'}`,
                                color: tab === t.id ? 'var(--cyan)' : 'var(--text3)', fontWeight: tab === t.id ? 600 : 400,
                            }}>
                            {t.label}
                        </button>
                    ))}
                </div>

                {loading && <div style={{ color: 'var(--text3)', fontSize: 12, padding: 10 }}>Loading…</div>}
                {error && (
                    <div style={{ background: 'var(--red-glow)', border: '1px solid var(--red)', borderRadius: 6, padding: '6px 10px', color: 'var(--red)', fontSize: 11, marginBottom: 12 }}>⚠ {error}</div>
                )}

                {showCreds && (
                    <SshCredModal
                        vm={{ vm_id: String(vmid), vm_name: config.name, network_adapters: ipInfo?.ip ? [{ ip_addresses: [ipInfo.ip] }] : [] }}
                        hostName={`${instance}__${node}`}
                        onClose={() => setShowCreds(false)}
                        defaults={cloudInitDefaults(config)}
                        onSaved={async () => { setShowCreds(false); loadCreds(); setIpInfo(await fetchProxmoxVmIp(instance, node, vmid)); }}
                    />
                )}

                {tab === 'history' && (
                    <ProxmoxVmHistoryChart instance={instance} node={node} vmid={vmid} />
                )}

                {tab === 'assignments' && (
                    <ProxmoxAssignmentsPanel hostName={`${instance}__${node}`} vmid={vmid} vmName={config.name} />
                )}

                {tab === 'info' && data && (
                    <div className="ccd-stack-mobile" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                        <div>
                            <div style={{ fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6 }}>General</div>
                            <Row label="Guest IP" value={ipLabel(ipInfo)} />
                            {isAdmin && ipInfo && (
                                <button onClick={() => setShowCreds(true)}
                                    style={{ padding: '3px 10px', fontSize: 10, borderRadius: 5, cursor: 'pointer', background: 'transparent', border: '1px solid var(--border)', color: 'var(--text2)', margin: '2px 0 8px' }}>
                                    {creds && creds !== 'none' ? 'Ubah kredensial & IP' : 'Atur kredensial & IP'}
                                </button>
                            )}
                            {isAdmin && creds && (creds === 'none' ? (
                                <Row label="Login Connect" value="belum diatur" />
                            ) : (
                                <>
                                    <Row label="Login Connect" value={`${creds.username}@${creds.ssh_host || '—'}:${creds.ssh_port} (${creds.guac_protocol || 'ssh'})`} />
                                    <Row label="Password" value={password ?? (creds.has_password ? '••••••••' : creds.has_pkey ? 'private key' : '—')} />
                                    {creds.has_password && (
                                        <button onClick={togglePassword}
                                            style={{ padding: '3px 10px', fontSize: 10, borderRadius: 5, cursor: 'pointer', background: 'transparent', border: '1px solid var(--border)', color: 'var(--text2)', margin: '2px 0 8px' }}>
                                            {password !== null ? 'Sembunyikan password' : 'Tampilkan password'}
                                        </button>
                                    )}
                                </>
                            ))}
                            {isAdmin && ipInfo?.reason === 'agent_disabled' && (
                                <div style={{ margin: '2px 0 8px', fontSize: 10, color: 'var(--text3)', lineHeight: 1.5 }}>
                                    <button onClick={enableAgent} disabled={agentBusy}
                                        style={{ padding: '3px 10px', fontSize: 10, borderRadius: 5, cursor: 'pointer', background: 'transparent', border: '1px solid var(--cyan)', color: 'var(--cyan)', marginBottom: 4 }}>
                                        {agentBusy ? 'Mengaktifkan…' : 'Aktifkan QEMU Guest Agent'}
                                    </button>
                                    <div>Berlaku setelah VM di-Shutdown lalu Start (reboot dari dalam guest tidak cukup). Paket qemu-guest-agent juga harus terpasang di guest.</div>
                                </div>
                            )}
                            <Row label="OS Type" value={OS_TYPE_LABEL[config.ostype] || config.ostype || '—'} />
                            <Row label="CPU" value={`${config.sockets || 1} socket × ${config.cores || 1} core (${config.cpu || 'kvm64'})`} />
                            <Row label="Memory (max)" value={formatBytes((config.memory || 0) * 1024 * 1024)} />
                            <Row label="Boot Order" value={config.boot || '—'} />
                            <Row label="UUID" value={(config.smbios1 || '').match(/uuid=([\w-]+)/)?.[1] || '—'} />

                            <div style={{ fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.08em', margin: '14px 0 6px' }}>Live Status</div>
                            <Row label="CPU Usage" value={status.cpu != null ? `${(status.cpu * 100).toFixed(1)}%` : '—'} />
                            <Row label="Memory Usage" value={`${formatBytes(status.mem)} / ${formatBytes(status.maxmem)}`} />
                            <Row label="Uptime" value={status.qmpstatus === 'running' ? formatUptime(status.uptime) : '—'} />
                            <Row label="Disk Read/Write" value={`${formatBytes(status.diskread)} / ${formatBytes(status.diskwrite)}`} />
                            <Row label="Network In/Out" value={`${formatBytes(status.netin)} / ${formatBytes(status.netout)}`} />
                        </div>

                        <div>
                            <div style={{ fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6 }}>Disks</div>
                            {disks.length === 0 && <div style={{ fontSize: 11, color: 'var(--text3)' }}>—</div>}
                            {disks.map(d => (
                                <Row key={d.bus} label={d.bus + (d.isCdrom ? ' (cdrom)' : '')}
                                    value={d.isCdrom ? d.storage : `${d.storage} · ${d.size}`} />
                            ))}

                            <div style={{ fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.08em', margin: '14px 0 6px' }}>Network Interfaces</div>
                            {nics.length === 0 && <div style={{ fontSize: 11, color: 'var(--text3)' }}>—</div>}
                            {nics.map(n => (
                                <Row key={n.nic} label={n.nic}
                                    value={`${n.model} · ${n.mac} · ${n.bridge}${n.vlan ? ` (VLAN ${n.vlan})` : ''}`} />
                            ))}
                        </div>
                    </div>
                )}

                {tab === 'info' && data && isAdmin && (
                    <VmDeletePanel instance={instance} node={node} vmid={vmid}
                        vmName={config.name || String(vmid)} onDeleted={onDeleted} />
                )}
            </div>
        </div>
    );
}
