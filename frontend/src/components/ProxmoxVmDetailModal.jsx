import { useState, useEffect, useCallback } from 'react';
import { fetchProxmoxVmDetail, fetchProxmoxVmIp, enableProxmoxGuestAgent, getVmCreds, revealVmPassword, setVmLease } from '../api';
import { cloudInitDefaults } from '../proxmoxCloudInit';
import { formatBytes, formatUptime, formatCcdId, leaseInfo } from '../format';
import ProxmoxAssignmentsPanel from './ProxmoxAssignmentsPanel';
import VmOsAccountsPanel, { NewPasswordNotice, ResetPasswordForm } from './VmOsAccountsPanel';
import ProxmoxVmHistoryChart from './ProxmoxVmHistoryChart';
import SshCredModal from './SshCredModal';
import VmDeletePanel from './VmDeletePanel';
import { CreateTicketModal } from './TicketsPage';
import { t as translate, useT } from '../i18n';

const OS_TYPE_LABEL = {
    l26: 'Linux (2.6+ kernel)', l24: 'Linux (2.4 kernel)',
    win11: 'Windows 11', win10: 'Windows 10', win8: 'Windows 8', win7: 'Windows 7',
    solaris: 'Solaris',
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
    agent_disabled:    'vm.ipAgentDisabled',
    agent_not_running: 'vm.ipAgentNotRunning',
    no_ipv4:           'vm.ipNoIpv4',
};

// Connect memakai manual_ip (SSH Host di kredensial); IP agent hanya info + auto-fill.
function ipLabel(info) {
    if (!info) return '…';
    const manual = info.manual_ip;
    if (info.ip) return manual && manual !== info.ip ? translate('vm.ipAgentManual', { ip: info.ip, manual }) : info.ip;
    if (manual) return translate('vm.ipManual', { ip: manual });
    return translate(IP_REASON[info.reason] || 'vm.ipNotDetected');
}

const vmStatusLabel = (st) => (['running', 'stopped', 'paused'].includes(st) ? translate(`vmstatus.${st}`) : st);

function currentRole() {
    try { return JSON.parse(localStorage.getItem('hv_user'))?.role; } catch { return null; }
}

// Masa sewa VM: admin bisa menambah, mengurangi, atau menghapus batas; mahasiswa bisa meminta
// perpanjangan lewat tiket Helpdesk kalau masa sewanya habis atau tinggal sedikit.
function LeaseBar({ lease, isAdmin, busy, date, setDate, onChange, onRequest }) {
    const t = useT();
    const info = leaseInfo(lease);
    if (!isAdmin && !lease) return null;
    const btn = { padding: '3px 10px', fontSize: 11, borderRadius: 5, cursor: busy ? 'wait' : 'pointer', background: 'transparent', border: '1px solid var(--border)', color: 'var(--text2)' };
    return (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', padding: '8px 10px', marginBottom: 12, borderRadius: 6, background: 'var(--bg-card2)', border: `1px solid ${info.expired ? 'var(--red)' : 'var(--border)'}` }}>
            <span style={{ fontSize: 11, color: 'var(--text3)' }}>{t('lease.label')}</span>
            <span style={{ fontSize: 12, color: info.color, fontFamily: 'var(--fmono)', marginRight: 'auto' }}>{info.text}</span>
            {isAdmin ? (
                <>
                    <button disabled={busy} onClick={() => onChange({ add_days: 7 })} style={btn}>{t('lease.add7')}</button>
                    <button disabled={busy} onClick={() => onChange({ add_days: 30 })} style={btn}>{t('lease.add30')}</button>
                    {lease && <button disabled={busy} onClick={() => onChange({ add_days: -7 })} style={btn}>{t('lease.sub7')}</button>}
                    <input type="date" value={date} onChange={e => setDate(e.target.value)} style={{ ...btn, padding: '2px 6px', colorScheme: 'dark' }} />
                    {date && <button disabled={busy} onClick={() => onChange({ until: new Date(`${date}T23:59:59`).toISOString() })} style={{ ...btn, borderColor: 'var(--cyan)', color: 'var(--cyan)' }}>{t('lease.set')}</button>}
                    {lease && <button disabled={busy} onClick={() => onChange({ clear: true })} style={btn}>{t('lease.none')}</button>}
                </>
            ) : (info.expired || info.days <= 7) && (
                <button onClick={onRequest} style={{ ...btn, borderColor: 'var(--yellow)', color: 'var(--yellow)' }}>{t('lease.request')}</button>
            )}
        </div>
    );
}

export default function ProxmoxVmDetailModal({ instance, node, vmid, ccdId = null, vmName = '', leaseUntil = null, maskHost = false, zIndex = 200, onClose, onDeleted, onLeaseChanged }) {
    const [data, setData] = useState(null);
    const [error, setError] = useState(null);
    const [loading, setLoading] = useState(true);
    const [tab, setTab] = useState('info'); // 'info' | 'history' | 'assignments' | 'accounts'
    const [ipInfo, setIpInfo] = useState(null);
    const [agentBusy, setAgentBusy] = useState(false);
    const [showCreds, setShowCreds] = useState(false);
    const [creds, setCreds] = useState(null);      // null = loading, 'none' = not configured
    const [password, setPassword] = useState(null);
    const [resetting, setResetting] = useState(false);
    const [resetResult, setResetResult] = useState(null);
    const [reporting, setReporting] = useState(false);
    const [reported, setReported] = useState(null);   // nomor tiket yang baru dikirim
    const [ticketInit, setTicketInit] = useState(null); // kategori dan judul awal form tiket
    const [lease, setLease] = useState(leaseUntil);
    const [leaseDate, setLeaseDate] = useState('');
    const [leaseBusy, setLeaseBusy] = useState(false);
    const t = useT();
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
            setError(e?.response?.data?.detail || translate('vm.loadFailed'));
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
            setError(e?.response?.data?.detail || t('vm.agentFailed'));
        } finally {
            setAgentBusy(false);
        }
    };

    const togglePassword = async () => {
        if (password !== null) { setPassword(null); return; }
        try {
            setPassword((await revealVmPassword(`${instance}__${node}`, String(vmid))).password);
        } catch (e) {
            setError(e?.response?.data?.detail || t('vm.revealFailed'));
        }
    };

    useEffect(() => { load(); }, [load]);

    const changeLease = async (body) => {
        if (body.add_days < 0 && !confirm(t('lease.confirmReduce', { days: -body.add_days }))) return;
        setLeaseBusy(true);
        try {
            const r = await setVmLease(instance, node, vmid, body);
            setLease(r.lease_until);
            setLeaseDate('');
            onLeaseChanged?.(r.lease_until);
        } catch (e) {
            setError(e?.response?.data?.detail || t('lease.error'));
        } finally { setLeaseBusy(false); }
    };

    const config = data?.config || {};
    // Nama dari daftar VM dipakai selama detail belum termuat. Mahasiswa tidak diperlihatkan VMID.
    const displayName = config.name || vmName || (maskHost ? formatCcdId(ccdId) : `VM ${vmid}`);
    const status = data?.status || {};
    const disks = data ? parseDisks(config) : [];
    const nics = data ? parseNics(config) : [];

    return (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex }} onClick={onClose}>
            <div style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', borderRadius: 10, width: 640, maxWidth: '92vw', maxHeight: '85vh', overflow: 'auto', padding: 20 }} onClick={e => e.stopPropagation()}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
                    <div>
                        <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text)' }}>{displayName}</div>
                        <div style={{ fontSize: 11, color: 'var(--text3)' }}>
                            {maskHost ? formatCcdId(ccdId) : `${instance}/${node} · VMID ${vmid} · ${formatCcdId(ccdId)}`} · {status.qmpstatus === 'running'
                                ? <span style={{ color: 'var(--green)' }}>{t('vm.running')}</span>
                                : <span style={{ color: 'var(--text3)' }}>{status.qmpstatus ? vmStatusLabel(status.qmpstatus) : t('vm.unknown')}</span>}
                        </div>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        {data && (
                            <button onClick={() => { setReported(null); setTicketInit(null); setReporting(true); }} title={t('vm.reportHint')}
                                style={{ padding: '4px 10px', fontSize: 11, borderRadius: 5, cursor: 'pointer', background: 'transparent', border: '1px solid var(--yellow)', color: 'var(--yellow)', whiteSpace: 'nowrap' }}>
                                {t('vm.report')}
                            </button>
                        )}
                        <button onClick={onClose} aria-label={t('common.close')} style={{ background: 'transparent', border: 'none', color: 'var(--text3)', fontSize: 18, cursor: 'pointer' }}>×</button>
                    </div>
                </div>

                {reported && (
                    <div style={{ background: '#4ade8012', border: '1px solid #4ade8055', borderRadius: 6, padding: '6px 10px', color: '#4ade80', fontSize: 11, marginBottom: 12 }}>
                        ✓ {t('vm.ticketSent', { n: reported })}
                    </div>
                )}

                <LeaseBar lease={lease} isAdmin={isAdmin} busy={leaseBusy} date={leaseDate} setDate={setLeaseDate}
                    onChange={changeLease}
                    onRequest={() => {
                        setReported(null);
                        setTicketInit({ category: 'LEASE_EXTENSION', title: t('lease.ticketTitle', { name: displayName }) });
                        setReporting(true);
                    }} />

                {reporting && (
                    <CreateTicketModal
                        initial={ticketInit}
                        vm={{
                            vm_id: String(vmid), host_name: `${instance}__${node}`, ccd_id: ccdId, vm_name: displayName,
                            state: status.qmpstatus, cpu_usage_percent: status.cpu != null ? status.cpu * 100 : null,
                            memory_assigned_mb: status.maxmem ? Math.round(status.maxmem / 1048576) : null,
                            processor_count: status.cpus ?? null,
                        }}
                        onClose={() => setReporting(false)}
                        onCreated={(tk) => { setReporting(false); setReported(tk?.ticket_number || t('vm.newTicket')); }}
                    />
                )}

                <div style={{ display: 'flex', gap: 4, marginBottom: 14, borderBottom: '1px solid var(--border)' }}>
                    {[{ id: 'info', label: t('vm.tabInfo') }, { id: 'history', label: t('vm.tabHistory') },
                      ...(isAdmin ? [{ id: 'assignments', label: t('vm.tabAssignments') }, { id: 'accounts', label: t('vm.tabAccounts') }] : [])].map(tb => (
                        <button key={tb.id} onClick={() => setTab(tb.id)}
                            style={{
                                padding: '6px 12px', fontSize: 12, cursor: 'pointer', background: 'transparent', border: 'none',
                                borderBottom: `2px solid ${tab === tb.id ? 'var(--cyan)' : 'transparent'}`,
                                color: tab === tb.id ? 'var(--cyan)' : 'var(--text3)', fontWeight: tab === tb.id ? 600 : 400,
                            }}>
                            {tb.label}
                        </button>
                    ))}
                </div>

                {loading && <div style={{ color: 'var(--text3)', fontSize: 12, padding: 10 }}>{t('common.loading')}</div>}
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

                {tab === 'assignments' && isAdmin && (
                    <ProxmoxAssignmentsPanel hostName={`${instance}__${node}`} vmid={vmid} vmName={config.name} />
                )}

                {tab === 'accounts' && isAdmin && (
                    <VmOsAccountsPanel hostName={`${instance}__${node}`} vmid={vmid} />
                )}

                {tab === 'info' && data && (
                    <div className="ccd-stack-mobile" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                        <div>
                            <div style={{ fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6 }}>{t('vm.general')}</div>
                            <Row label={t('vm.guestIp')} value={ipLabel(ipInfo)} />
                            {isAdmin && ipInfo && (
                                <button onClick={() => setShowCreds(true)}
                                    style={{ padding: '3px 10px', fontSize: 10, borderRadius: 5, cursor: 'pointer', background: 'transparent', border: '1px solid var(--border)', color: 'var(--text2)', margin: '2px 0 8px' }}>
                                    {creds && creds !== 'none' ? t('vm.editCreds') : t('vm.setCreds')}
                                </button>
                            )}
                            {isAdmin && creds && (creds === 'none' ? (
                                <Row label={t('vm.connectLogin')} value={t('vm.notSet')} />
                            ) : (
                                <>
                                    <Row label={t('vm.connectLogin')} value={`${creds.username}@${creds.ssh_host || '—'}:${creds.ssh_port} (${creds.guac_protocol || 'ssh'})`} />
                                    <Row label={t('vm.password')} value={password ?? (creds.has_password ? '••••••••' : creds.has_pkey ? t('vm.privateKey') : '—')} />
                                    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', margin: '2px 0 8px' }}>
                                        {creds.has_password && (
                                            <button onClick={togglePassword}
                                                style={{ padding: '3px 10px', fontSize: 10, borderRadius: 5, cursor: 'pointer', background: 'transparent', border: '1px solid var(--border)', color: 'var(--text2)' }}>
                                                {password !== null ? t('vm.hidePassword') : t('vm.showPassword')}
                                            </button>
                                        )}
                                        <button onClick={() => { setResetResult(null); setResetting(r => !r); }} title={t('vm.resetHint')}
                                            style={{ padding: '3px 10px', fontSize: 10, borderRadius: 5, cursor: 'pointer', background: 'transparent', border: '1px solid var(--yellow)', color: 'var(--yellow)' }}>
                                            {t('vm.resetPassword')}
                                        </button>
                                    </div>
                                    {resetting && (
                                        <ResetPasswordForm hostName={`${instance}__${node}`} vmid={vmid} username={creds.username}
                                            onDone={r => { setResetting(false); setResetResult(r); loadCreds(); }}
                                            onCancel={() => setResetting(false)} />
                                    )}
                                    {resetResult && <NewPasswordNotice {...resetResult} onClose={() => setResetResult(null)} />}
                                </>
                            ))}
                            {isAdmin && ipInfo?.reason === 'agent_disabled' && (
                                <div style={{ margin: '2px 0 8px', fontSize: 10, color: 'var(--text3)', lineHeight: 1.5 }}>
                                    <button onClick={enableAgent} disabled={agentBusy}
                                        style={{ padding: '3px 10px', fontSize: 10, borderRadius: 5, cursor: 'pointer', background: 'transparent', border: '1px solid var(--cyan)', color: 'var(--cyan)', marginBottom: 4 }}>
                                        {agentBusy ? t('vm.enablingAgent') : t('vm.enableAgent')}
                                    </button>
                                    <div>{t('vm.agentHint')}</div>
                                </div>
                            )}
                            <Row label={t('vm.osType')} value={config.ostype === 'other' ? t('vm.osOther') : OS_TYPE_LABEL[config.ostype] || config.ostype || '—'} />
                            <Row label={t('vm.cpu')} value={t('vm.cpuValue', { sockets: config.sockets || 1, cores: config.cores || 1, cpu: config.cpu || 'kvm64' })} />
                            <Row label={t('vm.memoryMax')} value={formatBytes((config.memory || 0) * 1024 * 1024)} />
                            <Row label={t('vm.bootOrder')} value={config.boot || '—'} />
                            <Row label={t('vm.uuid')} value={(config.smbios1 || '').match(/uuid=([\w-]+)/)?.[1] || '—'} />

                            <div style={{ fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.08em', margin: '14px 0 6px' }}>{t('vm.live')}</div>
                            <Row label={t('vm.cpuUsage')} value={status.cpu != null ? `${(status.cpu * 100).toFixed(1)}%` : '—'} />
                            <Row label={t('vm.memoryUsage')} value={`${formatBytes(status.mem)} / ${formatBytes(status.maxmem)}`} />
                            <Row label={t('vm.uptime')} value={status.qmpstatus === 'running' ? formatUptime(status.uptime) : '—'} />
                            <Row label={t('vm.diskRw')} value={`${formatBytes(status.diskread)} / ${formatBytes(status.diskwrite)}`} />
                            <Row label={t('vm.netInOut')} value={`${formatBytes(status.netin)} / ${formatBytes(status.netout)}`} />
                        </div>

                        <div>
                            <div style={{ fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6 }}>{t('vm.disks')}</div>
                            {disks.length === 0 && <div style={{ fontSize: 11, color: 'var(--text3)' }}>—</div>}
                            {disks.map(d => (
                                <Row key={d.bus} label={d.bus + (d.isCdrom ? ' (cdrom)' : '')}
                                    value={d.isCdrom ? d.storage : `${d.storage} · ${d.size}`} />
                            ))}

                            <div style={{ fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.08em', margin: '14px 0 6px' }}>{t('vm.nics')}</div>
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
