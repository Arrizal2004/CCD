import { useState, useEffect, useCallback, useRef } from 'react';
import {
    api,
    fetchInfraRequests, createInfraRequest, reviewInfraRequest,
    uploadInfraDocument, uploadInfraConfig,
    getInfraConfigUrl, getInfraDocUrl,
    fetchInfraMessages, infraRequestWsUrl,
    fetchAllProxmoxVms,
} from '../api';
import useIsMobile from '../useIsMobile';
import { useSysConfig } from '../sysconfig';
import { formatCcdId } from '../format';
import { locale, t as translate, useT } from '../i18n';
import PaneTabs from './PaneTabs';
import CreateVmModal from './CreateVmModal';
import Icon from './Icons';

const API_BASE = import.meta.env.VITE_API_URL || '';

// ── Status config ──────────────────────────────────────────────────────────────
const STATUS_CFG = {
    PENDING:     { label: 'infra.stPending',  color: 'var(--yellow)' },
    ON_PROGRESS: { label: 'infra.stProgress', color: 'var(--cyan)'   },
    DONE:        { label: 'infra.stDone',     color: 'var(--green)'  },
    DECLINE:     { label: 'infra.stDecline',  color: 'var(--red)'    },
};

// ── Small helpers ──────────────────────────────────────────────────────────────
const StatusBadge = ({ status }) => {
    const t = useT();
    const cfg = STATUS_CFG[status] || { color: 'var(--text3)' };
    return (
        <span style={{
            fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 10,
            background: cfg.color + '22', color: cfg.color,
            border: `1px solid ${cfg.color}44`, letterSpacing: '0.04em',
            textTransform: 'uppercase',
        }}>{cfg.label ? t(cfg.label) : status}</span>
    );
};

const TypeBadge = ({ type }) => (
    <span style={{
        fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 10,
        background: type === 'VPS' ? 'rgba(0,229,255,0.15)' : 'rgba(124,58,237,0.15)',
        color: type === 'VPS' ? 'var(--cyan)' : 'var(--purple)',
        border: `1px solid ${type === 'VPS' ? 'var(--cyan)' : 'var(--purple)'}44`,
        letterSpacing: '0.04em',
    }}>{type}</span>
);

const SpecsSummary = ({ specs, type }) => {
    const t = useT();
    if (!specs) return <span style={{ color: 'var(--text3)', fontSize: 11 }}>—</span>;
    if (type === 'VPN') return <span style={{ fontSize: 11, color: 'var(--text2)' }}>{t('infra.vpnAccess')}</span>;
    const parts = [];
    if (specs.cpu)        parts.push(`${specs.cpu} vCPU`);
    if (specs.ram_gb)     parts.push(`${specs.ram_gb} GB RAM`);
    if (specs.storage_gb) parts.push(`${specs.storage_gb} GB Disk`);
    if (specs.os)         parts.push(specs.os);
    return <span style={{ fontSize: 11, color: 'var(--text2)' }}>{parts.join(' · ') || '—'}</span>;
};

const fmtDate = (s) => s
    ? new Date(s).toLocaleString(locale(), { dateStyle: 'short', timeStyle: 'short' })
    : '—';

// ── Create Modal ───────────────────────────────────────────────────────────────
function CreateModal({ onClose, onCreated }) {
    const t = useT();
    const [type,    setType]    = useState('VPS');
    const [specs,   setSpecs]   = useState({ cpu: '', ram_gb: '', storage_gb: '', os: '' });
    // Pilihan OS diatur superadmin di Pengaturan Sistem; yang pertama terpilih kalau belum memilih.
    const osOptions = useSysConfig().vps_os_options;
    const os = osOptions.includes(specs.os) ? specs.os : osOptions[0];
    const [notes,   setNotes]   = useState('');
    const [docFile, setDocFile] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error,   setError]   = useState('');

    const setSpec = (k) => (v) => setSpecs(s => ({ ...s, [k]: v }));

    const submit = async (e) => {
        e.preventDefault();
        setLoading(true); setError('');
        if (type === 'VPS') {
            if (!specs.cpu || !specs.ram_gb || !specs.storage_gb) {
                setError(t('infra.specsRequired'));
                setLoading(false); return;
            }
        }
        try {
            const body = { request_type: type, notes, specs: type === 'VPS' ? { ...specs, os } : null };
            const req  = await createInfraRequest(body);
            if (docFile) await uploadInfraDocument(req.id, docFile).catch(() => {});
            onCreated();
            onClose();
        } catch (err) {
            setError(err?.response?.data?.detail || t('infra.createFailed'));
        } finally { setLoading(false); }
    };

    const numInput = (label, val, onChange, unit, placeholder) => (
        <div style={{ marginBottom: 8 }}>
            <div style={{ fontSize: 10, color: 'var(--text3)', marginBottom: 3, textTransform: 'uppercase', letterSpacing: '0.06em' }}>{label}</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 0 }}>
                <input type="number" value={val} min={1} placeholder={placeholder}
                    onChange={e => onChange(e.target.value === '' ? '' : Number(e.target.value))}
                    style={{ flex: 1, minWidth: 0, background: 'var(--bg-hover)', border: '1px solid var(--border)', borderRight: 'none', borderRadius: '6px 0 0 6px', padding: '5px 6px', color: 'var(--text)', fontSize: 11, fontFamily: 'var(--fmono)', outline: 'none' }} />
                <span style={{ background: 'var(--bg-hover)', border: '1px solid var(--border)', borderRadius: '0 6px 6px 0', padding: '5px 6px', fontSize: 10, color: 'var(--text3)', whiteSpace: 'nowrap' }}>{unit}</span>
            </div>
        </div>
    );

    return (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)', zIndex: 200, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border-light)', borderRadius: 14, padding: 28, width: '95%', maxWidth: 520, maxHeight: '90vh', overflowY: 'auto' }}>
                <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 18 }}>{t('infra.newTitle')}</div>
                <form onSubmit={submit}>
                    <div style={{ marginBottom: 12 }}>
                        <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.06em' }}>{t('infra.type')}</div>
                        <div style={{ display: 'flex', gap: 8 }}>
                            {['VPS', 'VPN'].map(rt => (
                                <button key={rt} type="button" onClick={() => setType(rt)}
                                    style={{
                                        flex: 1, padding: '8px 0', borderRadius: 8,
                                        border: `2px solid ${type === rt ? (rt === 'VPS' ? 'var(--cyan)' : 'var(--purple)') : 'var(--border)'}`,
                                        background: type === rt ? (rt === 'VPS' ? 'rgba(0,229,255,0.1)' : 'rgba(124,58,237,0.1)') : 'var(--bg-hover)',
                                        color: type === rt ? (rt === 'VPS' ? 'var(--cyan)' : 'var(--purple)') : 'var(--text3)',
                                        fontWeight: 700, fontSize: 13, cursor: 'pointer',
                                    }}>
                                    {rt}
                                </button>
                            ))}
                        </div>
                    </div>

                    {type === 'VPS' && (
                        <>
                            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 8 }}>
                                <div>{numInput('vCPU', specs.cpu, setSpec('cpu'), 'core', t('infra.egCpu'))}</div>
                                <div>{numInput('RAM', specs.ram_gb, setSpec('ram_gb'), 'GB', t('infra.egRam'))}</div>
                                <div>{numInput(t('infra.storage'), specs.storage_gb, setSpec('storage_gb'), 'GB', t('infra.egStorage'))}</div>
                            </div>
                            <div style={{ marginBottom: 10 }}>
                                <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.06em' }}>OS</div>
                                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))', gap: 8 }}>
                                    {osOptions.map(name => (
                                        <button key={name} type="button" onClick={() => setSpec('os')(name)} aria-pressed={os === name}
                                            style={{
                                                padding: '8px 10px', borderRadius: 8, cursor: 'pointer', fontSize: 12, fontWeight: 600, lineHeight: 1.3,
                                                border: `2px solid ${os === name ? 'var(--cyan)' : 'var(--border)'}`,
                                                background: os === name ? 'rgba(0,229,255,0.1)' : 'var(--bg-hover)',
                                                color: os === name ? 'var(--cyan)' : 'var(--text3)',
                                            }}>
                                            {name}
                                        </button>
                                    ))}
                                </div>
                            </div>
                        </>
                    )}

                    <div style={{ marginBottom: 10 }}>
                        <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 4, textTransform: 'uppercase', letterSpacing: '0.06em' }}>{t('infra.purpose')}</div>
                        <textarea value={notes} onChange={e => setNotes(e.target.value)} rows={3}
                            placeholder={type === 'VPN' ? t('infra.purposeVpn') : t('infra.purposeVps')}
                            style={{ width: '100%', background: 'var(--bg-hover)', border: '1px solid var(--border)', borderRadius: 6, padding: '8px 10px', color: 'var(--text)', fontSize: 12, resize: 'vertical', fontFamily: 'var(--font)', boxSizing: 'border-box' }} />
                    </div>

                    <div style={{ marginBottom: 14 }}>
                        <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 4, textTransform: 'uppercase', letterSpacing: '0.06em' }}>{t('infra.document')}</div>
                        <input type="file" accept=".pdf,.jpg,.jpeg,.png"
                            onChange={e => setDocFile(e.target.files?.[0] || null)}
                            style={{ fontSize: 11, color: 'var(--text2)' }} />
                    </div>

                    {error && (
                        <div style={{ fontSize: 11, color: 'var(--red)', marginBottom: 10, padding: '6px 10px', background: 'var(--red-glow)', borderRadius: 6 }}>
                            {error}
                        </div>
                    )}

                    <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                        <button type="button" onClick={onClose}
                            style={{ padding: '8px 16px', borderRadius: 7, background: 'var(--bg-hover)', border: '1px solid var(--border)', color: 'var(--text2)', fontSize: 12, cursor: 'pointer' }}>
                            {t('common.cancel')}
                        </button>
                        <button type="submit" disabled={loading}
                            style={{ padding: '8px 20px', borderRadius: 7, background: loading ? 'var(--bg-hover)' : 'var(--cyan)', color: loading ? 'var(--text3)' : '#000', fontSize: 12, fontWeight: 700, border: 'none', cursor: loading ? 'not-allowed' : 'pointer' }}>
                            {loading ? t('infra.sending') : t('infra.send')}
                        </button>
                    </div>
                </form>
            </div>
        </div>
    );
}

const ROLE_COLOR = { superadmin: '#ff6b35', admin: 'var(--cyan)', sysadmin: 'var(--green)', student: 'var(--purple)' };

// Nilai awal form Create VM dari permintaan VPS: spek, OS, nama VM, akun, dan password acak.
function vmFromRequest(req) {
    const user = String(req.student_username || 'mahasiswa').toLowerCase();
    const slug = user.replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'mahasiswa';
    let osUser = user.replace(/[^a-z0-9_-]+/g, '_').replace(/^[_-]+|[_-]+$/g, '').slice(0, 32);
    if (!/^[a-z_][a-z0-9_-]*$/.test(osUser) || osUser === 'root') osUser = 'siswa';
    const alphabet = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789';
    const rnd = crypto.getRandomValues(new Uint32Array(12));
    const raw = Array.from(rnd, n => alphabet[n % alphabet.length]).join('');
    const sp = req.specs || {};
    const asked = [sp.cpu && `${sp.cpu} vCPU`, sp.ram_gb && `${sp.ram_gb} GB RAM`, sp.storage_gb && `${sp.storage_gb} GB disk`, sp.os].filter(Boolean).join(' · ');
    return {
        name: `vps-${slug}`.slice(0, 63), username: osUser, password: `${raw.slice(0, 4)}-${raw.slice(4, 8)}-${raw.slice(8)}`,
        cores: sp.cpu || undefined, memory_mb: sp.ram_gb ? Number(sp.ram_gb) * 1024 : undefined, disk_gb: sp.storage_gb || undefined,
        os: sp.os, note: translate('infra.vmNote', { name: req.student_name || user, specs: asked || translate('infra.noSpecs') }),
    };
}

// Cari VM untuk ditautkan ke request: nama, VMID, CCDID, atau host. Lebih praktis daripada daftar panjang.
function VmSearch({ vms, value, onSelect }) {
    const t = useT();
    const [q, setQ] = useState('');
    const term = q.trim().toLowerCase();
    const hits = term ? vms.filter(v => [v.vm_name, v.vm_id, v.host_name, v.ccd_id != null ? formatCcdId(v.ccd_id) : '']
        .some(x => String(x || '').toLowerCase().includes(term))).slice(0, 8) : [];
    const row = { display: 'block', width: '100%', textAlign: 'left', padding: '6px 8px', background: 'transparent', border: 'none', borderTop: '1px solid var(--border)', color: 'var(--text)', fontSize: 11, cursor: 'pointer', fontFamily: 'var(--fmono)' };
    return (
        <div style={{ marginBottom: 6 }}>
            {value?.vm_id && (
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, padding: '5px 8px', marginBottom: 4, borderRadius: 6, border: '1px solid var(--green)', color: 'var(--green)' }}>
                    <span style={{ flex: 1, minWidth: 0, overflowWrap: 'anywhere' }}>{t('infra.vmSelected', { name: value.vm_name, id: value.vm_id, host: value.host_name })}</span>
                    <button type="button" onClick={() => onSelect(null)} aria-label={t('infra.vmUnselect')} style={{ background: 'none', border: 'none', color: 'var(--text3)', cursor: 'pointer' }}>✕</button>
                </div>
            )}
            <input value={q} onChange={e => setQ(e.target.value)} aria-label={t('infra.vmSearch')}
                placeholder={t('infra.vmSearchPh', { n: vms.length })}
                style={{ width: '100%', background: 'var(--bg-hover)', border: '1px solid var(--border)', borderRadius: 6, padding: '5px 8px', color: 'var(--text)', fontSize: 11, boxSizing: 'border-box' }} />
            {term && (
                <div style={{ border: '1px solid var(--border)', borderTop: 'none', borderRadius: '0 0 6px 6px', maxHeight: 220, overflowY: 'auto' }}>
                    {hits.length === 0
                        ? <div style={{ padding: '6px 8px', fontSize: 11, color: 'var(--text3)' }}>{t('infra.vmNoMatch')}</div>
                        : hits.map(v => (
                            <button key={`${v.vm_id}|${v.host_name}`} type="button" style={row} onClick={() => { onSelect(v); setQ(''); }}>
                                {v.vm_name} <span style={{ color: 'var(--text3)' }}>· {v.vm_id}{v.ccd_id != null ? ` · ${formatCcdId(v.ccd_id)}` : ''} · {v.host_name}</span>
                            </button>
                        ))}
                </div>
            )}
        </div>
    );
}

// ── Detail / Chat Modal ────────────────────────────────────────────────────────
function DetailModal({ req, currentUser, onClose, onUpdated }) {
    const t = useT();
    const isAdmin  = ['superadmin', 'sysadmin'].includes(currentUser?.role);
    const isDone   = req.status === 'DONE';
    const isClosed = req.status === 'DONE' || req.status === 'DECLINE';

    const [messages,     setMessages]     = useState([]);
    const [msgInput,     setMsgInput]     = useState('');
    const [wsReady,      setWsReady]      = useState(false);
    const [actionStatus, setActionStatus] = useState('');
    const [adminNote,    setAdminNote]    = useState(req.admin_note || '');
    const [vpnUser,      setVpnUser]      = useState(req.vpn_username || '');
    const [vpnPass,      setVpnPass]      = useState(req.vpn_password || '');
    const [showPass,     setShowPass]     = useState(false);
    const [configFile,   setConfigFile]   = useState(null);
    const [allVMs,       setAllVMs]       = useState([]);
    const [linkedVM,     setLinkedVM]     = useState(
        req.linked_vm_id
            ? { vm_id: req.linked_vm_id, vm_name: req.linked_vm_name, host_name: req.linked_host_name }
            : null
    );
    const [submitting,  setSubmitting]  = useState(false);
    const [actionError, setActionError] = useState('');
    const [creatingVm,  setCreatingVm]  = useState(false);
    const [vmInitial]                   = useState(() => vmFromRequest(req));
    const wsRef  = useRef(null);
    const endRef = useRef(null);
    const isMobile = useIsMobile();
    const [pane, setPane] = useState('chat');   // HP: hanya satu panel yang tampil

    useEffect(() => {
        fetchInfraMessages(req.id).then(data => {
            setMessages(data);
            setTimeout(() => endRef.current?.scrollIntoView({ behavior: 'smooth' }), 50);
        }).catch(() => {});
        if (isClosed) return;
        let ws;
        try {
            ws = new WebSocket(infraRequestWsUrl(req.id));
            wsRef.current = ws;
            ws.onopen  = () => setWsReady(true);
            ws.onclose = () => setWsReady(false);
            ws.onmessage = (ev) => {
                let m; try { m = JSON.parse(ev.data); } catch { return; }
                if (m.type === 'message')
                    setMessages(prev => prev.some(x => x.id === m.id) ? prev : [...prev, m]);
                else if (m.type === 'error') alert(m.message);
            };
        } catch { /* noop */ }
        return () => { try { ws?.close(); } catch { /* noop */ } };
    }, [req.id, isClosed]);

    useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages.length, pane]);

    const loadVMs = useCallback(async () => {
        if (req.request_type !== 'VPS' || !isAdmin) return;
        try {
            setAllVMs(await fetchAllProxmoxVms());
        } catch { /* ignore */ }
    }, [req.request_type, isAdmin]);

    useEffect(() => { loadVMs(); }, [loadVMs]);

    const sendMsg = (e) => {
        e.preventDefault();
        if (!msgInput.trim() || !wsReady || wsRef.current?.readyState !== WebSocket.OPEN) return;
        wsRef.current.send(JSON.stringify({ message: msgInput.trim() }));
        setMsgInput('');
    };

    const submitAction = async () => {
        if (!actionStatus) return;
        setSubmitting(true); setActionError('');
        try {
            const body = {
                status: actionStatus, admin_note: adminNote,
                ...(actionStatus === 'DONE' && req.request_type === 'VPN'
                    ? { vpn_username: vpnUser || null, vpn_password: vpnPass || null } : {}),
                ...(actionStatus === 'DONE' && req.request_type === 'VPS' && linkedVM
                    ? { linked_vm_id: linkedVM.vm_id, linked_vm_name: linkedVM.vm_name, linked_host_name: linkedVM.host_name } : {}),
            };
            await reviewInfraRequest(req.id, body);
            if (configFile && req.request_type === 'VPN') await uploadInfraConfig(req.id, configFile);
            onUpdated();
        } catch (err) {
            setActionError(err?.response?.data?.detail || t('infra.updateFailed'));
            setSubmitting(false);
        }
    };

    const inpSt = { width: '100%', background: 'var(--bg-hover)', border: '1px solid var(--border)', borderRadius: 6, padding: '5px 8px', color: 'var(--text)', fontSize: 11, fontFamily: 'var(--fmono)', boxSizing: 'border-box' };
    const lblSt = { fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.06em', display: 'block', marginBottom: 3, marginTop: 8 };

    return (
        <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.78)', zIndex: 200, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: isMobile ? 0 : 16 }}>
            <div onClick={e => e.stopPropagation()} style={{
                background: 'var(--bg-card)', overflow: 'hidden', display: 'flex', flexDirection: 'column',
                ...(isMobile
                    ? { width: '100vw', height: '100dvh', borderRadius: 0, border: 'none' }
                    : { width: 'min(900px, 96vw)', height: 'min(640px, 92vh)', borderRadius: 14, border: '1px solid var(--border-light)' }),
            }}>

                {/* Header */}
                <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', background: 'var(--bg-hover)', display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0 }}>
                    {!isMobile && <TypeBadge type={req.request_type} />}
                    <span style={{ flex: 1, minWidth: 0, fontSize: 14, fontWeight: 600, fontFamily: 'var(--fmono)', color: 'var(--cyan)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {req.request_type === 'VPS' ? t('infra.titleVps') : t('infra.titleVpn')}
                        {isAdmin && req.student_name && <span style={{ color: 'var(--text3)', fontSize: 11, fontWeight: 400, marginLeft: 8 }}>— {req.student_name}</span>}
                    </span>
                    <StatusBadge status={req.status} />
                    <button onClick={onClose} aria-label={t('infra.close')} style={{ width: 28, height: 28, borderRadius: 6, background: 'var(--bg-card)', border: '1px solid var(--border)', color: 'var(--text2)', cursor: 'pointer', marginLeft: 4, flexShrink: 0 }}>✕</button>
                </div>

                {isMobile && <PaneTabs value={pane} onChange={setPane} chatCount={messages.length} />}

                {/* Body */}
                <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>

                    {/* Left panel: info + admin actions */}
                    {(!isMobile || pane === 'detail') && (
                    <div style={{ width: isMobile ? '100%' : 290, borderRight: isMobile ? 'none' : '1px solid var(--border)', padding: '12px 14px', overflowY: 'auto', flexShrink: 0, boxSizing: 'border-box' }}>

                        {/* Info rows */}
                        {[
                            [t('infra.rowType'),    <TypeBadge type={req.request_type} />],
                            [t('infra.rowStatus'),  <StatusBadge status={req.status} />],
                            ...(isAdmin && req.student_name ? [[t('infra.rowStudent'), req.student_name]] : []),
                            [t('infra.rowCreated'), fmtDate(req.created_at)],
                            ...(req.request_type === 'VPS' && req.specs ? [[t('infra.rowSpecs'), <SpecsSummary specs={req.specs} type="VPS" />]] : []),
                        ].map(([k, v]) => (
                            <div key={k} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '5px 0', borderBottom: '1px solid var(--border)', fontSize: 12, gap: 8 }}>
                                <span style={{ color: 'var(--text3)', flexShrink: 0 }}>{k}</span>
                                <span style={{ color: 'var(--text)', fontFamily: 'var(--fmono)', textAlign: 'right', overflowWrap: 'anywhere', minWidth: 0 }}>{v}</span>
                            </div>
                        ))}

                        {req.notes && (
                            <div style={{ padding: '8px 0', borderBottom: '1px solid var(--border)' }}>
                                <div style={{ fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 4 }}>{t('infra.notes')}</div>
                                <div style={{ fontSize: 12, color: 'var(--text2)', lineHeight: 1.5 }}>{req.notes}</div>
                            </div>
                        )}
                        {req.document_url && (
                            <div style={{ padding: '6px 0', borderBottom: '1px solid var(--border)' }}>
                                <a href={getInfraDocUrl(req.id)} target="_blank" rel="noopener noreferrer"
                                    style={{ fontSize: 11, color: 'var(--cyan)', textDecoration: 'none', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                                    {t('infra.viewDoc')}
                                </a>
                            </div>
                        )}
                        {req.admin_note && (
                            <div style={{ padding: '8px 0', borderBottom: '1px solid var(--border)' }}>
                                <div style={{ fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 4 }}>{t('infra.adminNote')}</div>
                                <div style={{ fontSize: 12, color: 'var(--text2)', lineHeight: 1.5 }}>{req.admin_note}</div>
                            </div>
                        )}

                        {/* VPN credentials display */}
                        {isDone && req.request_type === 'VPN' && (req.vpn_username || req.vpn_password || req.config_file_url) && (
                            <div style={{ marginTop: 10, padding: '10px 12px', background: 'rgba(0,230,118,0.06)', border: '1px solid var(--green)44', borderRadius: 8 }}>
                                <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--green)', marginBottom: 8 }}>{t('infra.vpnCreds')}</div>
                                {req.vpn_username && <>
                                    <div style={{ fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase' }}>{t('infra.username')}</div>
                                    <div style={{ fontFamily: 'var(--fmono)', fontSize: 12, color: 'var(--text)', marginBottom: 6 }}>{req.vpn_username}</div>
                                </>}
                                {req.vpn_password && <>
                                    <div style={{ fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase' }}>{t('infra.password')}</div>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
                                        <span style={{ fontFamily: 'var(--fmono)', fontSize: 12, color: 'var(--text)' }}>{showPass ? req.vpn_password : '••••••••'}</span>
                                        <button onClick={() => setShowPass(v => !v)} aria-label={t('profile.showPassword')} title={t('profile.showPassword')} style={{ background: 'none', border: 'none', color: 'var(--text3)', cursor: 'pointer', fontSize: 12, padding: 0 }}>
                                            <Icon name={showPass ? 'eyeOff' : 'eye'} />
                                        </button>
                                    </div>
                                </>}
                                {req.config_file_url && (
                                    <a href={getInfraConfigUrl(req.id)} target="_blank" rel="noopener noreferrer"
                                        style={{ fontSize: 11, color: 'var(--cyan)', textDecoration: 'none', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                                        {t('infra.downloadConfig')}
                                    </a>
                                )}
                            </div>
                        )}

                        {/* VPS linked VM display */}
                        {isDone && req.request_type === 'VPS' && req.linked_vm_id && (
                            <div style={{ marginTop: 10, padding: '10px 12px', background: 'rgba(0,230,118,0.06)', border: '1px solid var(--green)44', borderRadius: 8 }}>
                                <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--green)', marginBottom: 6 }}>{t('infra.deployedVm')}</div>
                                <div style={{ fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase' }}>{t('infra.vmName')}</div>
                                <div style={{ fontFamily: 'var(--fmono)', fontSize: 12, color: 'var(--text)', marginBottom: 4 }}>{req.linked_vm_name || '—'}</div>
                                <div style={{ fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase' }}>{t('infra.host')}</div>
                                <div style={{ fontFamily: 'var(--fmono)', fontSize: 12, color: 'var(--text)' }}>{req.linked_host_name || '—'}</div>
                            </div>
                        )}

                        {/* Admin action panel */}
                        {isAdmin && !isClosed && (
                            <div style={{ marginTop: 12 }}>
                                <div style={{ fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 6 }}>{t('infra.adminActions')}</div>
                                {req.request_type === 'VPS' && (
                                    <button onClick={() => setCreatingVm(true)}
                                        style={{ width: '100%', padding: '6px', borderRadius: 6, marginBottom: 8, background: 'var(--cyan-glow)', border: '1px solid var(--cyan)', color: 'var(--cyan)', fontSize: 11, fontWeight: 700, cursor: 'pointer' }}>
                                        {t('infra.createVm')}
                                    </button>
                                )}
                                {creatingVm && (
                                    <CreateVmModal initial={vmInitial} onClose={() => setCreatingVm(false)}
                                        onCreated={(res, target) => {
                                            // VM baru langsung dipilih; saat dikonfirmasi Selesai, VM itu di-assign ke mahasiswanya.
                                            setLinkedVM({ vm_id: String(res.vmid), vm_name: res.name, host_name: `${target.instance}__${target.node}` });
                                            setActionStatus('DONE');
                                            const ip = res.static_ip || res.agent_ip;
                                            setAdminNote(n => n || t('infra.vmCreatedNote', { name: res.name, ip: ip ? ` (IP ${ip})` : '' }));
                                            loadVMs();
                                        }} />
                                )}
                                <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
                                    {[
                                        { s: 'ON_PROGRESS', label: t('infra.actProgress'), color: 'var(--cyan)'  },
                                        { s: 'DONE',        label: t('infra.actDone'),     color: 'var(--green)' },
                                        { s: 'DECLINE',     label: t('infra.actDecline'),  color: 'var(--red)'   },
                                    ].map(({ s, label, color }) => (
                                        <button key={s} onClick={() => setActionStatus(p => p === s ? '' : s)}
                                            style={{ flex: 1, padding: '5px 2px', borderRadius: 6, border: `2px solid ${actionStatus === s ? color : 'var(--border)'}`, background: actionStatus === s ? color + '22' : 'var(--bg-card)', color: actionStatus === s ? color : 'var(--text3)', fontSize: 10, fontWeight: 700, cursor: 'pointer' }}>
                                            {label}
                                        </button>
                                    ))}
                                </div>

                                {actionStatus && <>
                                    {actionStatus === 'DONE' && req.request_type === 'VPN' && <>
                                        <label style={lblSt}>{t('infra.vpnUser')}</label>
                                        <input value={vpnUser} onChange={e => setVpnUser(e.target.value)} placeholder="vpn-user@domain" style={inpSt} />
                                        <label style={lblSt}>{t('infra.vpnPass')}</label>
                                        <div style={{ position: 'relative' }}>
                                            <input type={showPass ? 'text' : 'password'} value={vpnPass} onChange={e => setVpnPass(e.target.value)} placeholder={t('infra.vpnPassPh')}
                                                style={{ ...inpSt, paddingRight: 28 }} />
                                            <button type="button" onClick={() => setShowPass(v => !v)} aria-label={t('profile.showPassword')} title={t('profile.showPassword')}
                                                style={{ position: 'absolute', right: 6, top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', color: 'var(--text3)', cursor: 'pointer', fontSize: 11 }}>
                                                <Icon name={showPass ? 'eyeOff' : 'eye'} />
                                            </button>
                                        </div>
                                        <label style={lblSt}>{t('infra.vpnConfig')}</label>
                                        <input type="file" accept=".ovpn,.conf,.zip,.pdf,.txt,.pem,.crt,.key"
                                            onChange={e => setConfigFile(e.target.files?.[0] || null)}
                                            style={{ fontSize: 10, color: 'var(--text2)', marginBottom: 2 }} />
                                    </>}

                                    {actionStatus === 'DONE' && req.request_type === 'VPS' && <>
                                        <label style={lblSt}>{t('infra.deployVm')}</label>
                                        <VmSearch vms={allVMs} value={linkedVM} onSelect={setLinkedVM} />
                                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 4 }}>
                                            <div>
                                                <label style={{ ...lblSt, marginTop: 0 }}>{t('infra.vmName')}</label>
                                                <input value={linkedVM?.vm_name || ''} placeholder="VM-Student-01"
                                                    onChange={e => setLinkedVM(v => ({ ...(v || {}), vm_name: e.target.value }))}
                                                    style={inpSt} />
                                            </div>
                                            <div>
                                                <label style={{ ...lblSt, marginTop: 0 }}>{t('infra.host')}</label>
                                                <input value={linkedVM?.host_name || ''} placeholder="HOST-01"
                                                    onChange={e => setLinkedVM(v => ({ ...(v || {}), host_name: e.target.value }))}
                                                    style={inpSt} />
                                            </div>
                                        </div>
                                    </>}

                                    <label style={lblSt}>{t('infra.adminNote')}</label>
                                    <textarea value={adminNote} onChange={e => setAdminNote(e.target.value)} rows={2}
                                        placeholder={t('infra.adminNotePh')}
                                        style={{ ...inpSt, fontFamily: 'var(--font)', resize: 'vertical', marginBottom: 8 }} />

                                    {actionError && <div style={{ fontSize: 11, color: 'var(--red)', marginBottom: 6 }}>{actionError}</div>}

                                    <button onClick={submitAction} disabled={submitting}
                                        style={{ width: '100%', padding: '7px', borderRadius: 7, background: submitting ? 'var(--bg-hover)' : 'var(--cyan)', color: submitting ? 'var(--text3)' : '#000', fontSize: 11, fontWeight: 700, border: 'none', cursor: submitting ? 'not-allowed' : 'pointer' }}>
                                        {submitting ? t('common.saving') : t(actionStatus === 'ON_PROGRESS' ? 'infra.confirmProgress' : actionStatus === 'DONE' ? 'infra.confirmDone' : 'infra.confirmDecline')}
                                    </button>
                                </>}
                            </div>
                        )}
                    </div>
                    )}

                    {/* Right panel: chat */}
                    {(!isMobile || pane === 'chat') && (
                    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
                        <div style={{ flex: 1, overflowY: 'auto', padding: 16, display: 'flex', flexDirection: 'column', gap: 10 }}>
                            {messages.length === 0 && (
                                <div style={{ color: 'var(--text3)', fontSize: 12, textAlign: 'center', marginTop: 20 }}>
                                    {t('infra.noMessages')}
                                </div>
                            )}
                            {messages.map(m => {
                                const mine       = m.sender_id === currentUser?.id;
                                const isAdminMsg = ['superadmin', 'sysadmin'].includes(m.sender_role);
                                const roleColor  = ROLE_COLOR[m.sender_role] || 'var(--text3)';
                                return (
                                    <div key={m.id} style={{ alignSelf: mine ? 'flex-end' : 'flex-start', maxWidth: isMobile ? '88%' : '78%' }}>
                                        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 2, justifyContent: mine ? 'flex-end' : 'flex-start' }}>
                                            <span style={{ fontSize: 10, color: 'var(--text2)', fontFamily: 'var(--fmono)' }}>{m.sender_name}</span>
                                            {m.sender_role && (
                                                <span style={{ fontSize: 10, fontFamily: 'var(--fmono)', padding: '1px 7px', borderRadius: 10, color: roleColor, background: roleColor + '22', whiteSpace: 'nowrap' }}>
                                                    {t(`role.${m.sender_role}`)}
                                                </span>
                                            )}
                                        </div>
                                        <div style={{
                                            padding: '8px 12px', borderRadius: 10, fontSize: 13,
                                            color: 'var(--text)', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere',
                                            background: isAdminMsg ? 'rgba(0,229,255,0.10)' : 'var(--bg-hover)',
                                            border: `1px solid ${isAdminMsg ? 'rgba(0,229,255,0.27)' : 'var(--border)'}`,
                                        }}>
                                            {m.message}
                                        </div>
                                        <div style={{ fontSize: 9, color: 'var(--text3)', marginTop: 2, textAlign: mine ? 'right' : 'left' }}>
                                            {fmtDate(m.created_at)}
                                        </div>
                                    </div>
                                );
                            })}
                            <div ref={endRef} />
                        </div>

                        {isClosed ? (
                            <div style={{ borderTop: '1px solid var(--border)', padding: 14, textAlign: 'center', fontSize: 12, color: 'var(--text3)' }}>
                                {isDone ? t('infra.lockedDone') : t('infra.lockedDecline')}
                            </div>
                        ) : (
                            <form onSubmit={sendMsg} style={{ borderTop: '1px solid var(--border)', padding: 12, display: 'flex', gap: 8, alignItems: 'center' }}>
                                <input value={msgInput} onChange={e => setMsgInput(e.target.value)}
                                    placeholder={wsReady ? t('infra.typeMsg') : t('infra.connecting')}
                                    disabled={!wsReady}
                                    onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMsg(e); } }}
                                    style={{ flex: 1, minWidth: 0, background: 'var(--bg-hover)', border: '1px solid var(--border)', borderRadius: 8, padding: '8px 12px', color: 'var(--text)', fontSize: 12, fontFamily: 'var(--font)', outline: 'none', opacity: wsReady ? 1 : 0.5 }} />
                                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3, fontSize: 9, color: wsReady ? 'var(--green)' : 'var(--text3)', flexShrink: 0 }}>
                                    <span style={{ width: 6, height: 6, borderRadius: '50%', background: wsReady ? 'var(--green)' : 'var(--text3)', display: 'inline-block' }} />
                                    {wsReady ? t('infra.live') : '...'}
                                </span>
                                <button type="submit" disabled={!wsReady || !msgInput.trim()}
                                    style={{ padding: '8px 18px', borderRadius: 8, background: (wsReady && msgInput.trim()) ? 'var(--cyan)' : 'var(--bg-hover)', color: (wsReady && msgInput.trim()) ? '#000' : 'var(--text3)', fontSize: 12, fontWeight: 700, border: 'none', cursor: (wsReady && msgInput.trim()) ? 'pointer' : 'not-allowed', whiteSpace: 'nowrap' }}>
                                    {t('infra.sendMsg')}
                                </button>
                            </form>
                        )}
                    </div>
                    )}
                </div>
            </div>
        </div>
    );
}

// ── Main Page ──────────────────────────────────────────────────────────────────
export default function InfraRequestsPage({ currentUser }) {
    const t = useT();
    const [items,        setItems]        = useState([]);
    const [loading,      setLoading]      = useState(true);
    const [filterStatus, setFilterStatus] = useState('');
    const [showCreate,   setShowCreate]   = useState(false);
    const [detail,       setDetail]       = useState(null);
    const [justVerified, setJustVerified] = useState(false);
    const [counts,       setCounts]       = useState({ PENDING: 0, ON_PROGRESS: 0, DONE: 0, DECLINE: 0 });

    const isAdmin      = ['superadmin', 'sysadmin'].includes(currentUser?.role);
    const isRestricted = currentUser?.role === 'student' && currentUser?.is_verified === false;

    const STAT_CARDS = [
        { key: '',            label: t('infra.total'),      color: 'var(--cyan)'   },
        { key: 'PENDING',     label: t('infra.stPending'),  color: 'var(--yellow)' },
        { key: 'ON_PROGRESS', label: t('infra.stProgress'), color: 'var(--cyan)'   },
        { key: 'DONE',        label: t('infra.stDone'),     color: 'var(--green)'  },
        { key: 'DECLINE',     label: t('infra.stDecline'),  color: 'var(--red)'    },
    ];

    const loadStats = useCallback(async () => {
        try {
            const results = await Promise.all(
                ['PENDING', 'ON_PROGRESS', 'DONE', 'DECLINE'].map(s =>
                    fetchInfraRequests({ status: s, page_size: 1 }).then(d => [s, d.total || 0])
                )
            );
            setCounts(Object.fromEntries(results));
        } catch { /* ignore */ }
    }, []);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const params = {};
            if (filterStatus) params.status = filterStatus;
            const data = await fetchInfraRequests(params);
            setItems(data.items || []);
        } catch { /* ignore */ }
        finally { setLoading(false); }
    }, [filterStatus]);

    const refresh = useCallback(() => { load(); loadStats(); }, [load, loadStats]);

    useEffect(() => { refresh(); }, [refresh]);

    // Auto-upgrade detection for restricted students
    useEffect(() => {
        if (!isRestricted) return;
        if (!items.some(r => r.status === 'DONE')) return;
        api.get('/api/v1/users/me')
            .then(r => {
                if (r.data.is_verified) {
                    const updated = { ...currentUser, is_verified: true };
                    localStorage.setItem('hv_user', JSON.stringify(updated));
                    setJustVerified(true);
                }
            }).catch(() => {});
    }, [items, isRestricted]);

    const totalCount = Object.values(counts).reduce((a, b) => a + b, 0);

    return (
        <div style={{ padding: '16px 20px', maxWidth: 1400, margin: '0 auto' }}>

            {/* Upgrade banner */}
            {justVerified && (
                <div style={{ marginBottom: 18, padding: '14px 18px', background: 'rgba(0,230,118,0.1)', border: '1px solid var(--green)55', borderRadius: 10, display: 'flex', alignItems: 'center', gap: 14 }}>
                    <div style={{ flex: 1 }}>
                        <div style={{ fontWeight: 700, color: 'var(--green)', fontSize: 13 }}>{t('infra.verifiedTitle')}</div>
                        <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 2 }}>{t('infra.verifiedBody')}</div>
                    </div>
                    <button onClick={() => window.location.reload()}
                        style={{ padding: '8px 18px', borderRadius: 8, background: 'var(--green)', color: '#000', fontSize: 13, fontWeight: 700, border: 'none', cursor: 'pointer' }}>
                        {t('infra.reload')}
                    </button>
                </div>
            )}

            {/* Restricted banner */}
            {isRestricted && !justVerified && (
                <div style={{ marginBottom: 18, padding: '12px 16px', background: 'rgba(255,214,0,0.07)', border: '1px solid var(--yellow)44', borderRadius: 10, display: 'flex', alignItems: 'center', gap: 12, fontSize: 12 }}>
                    <div style={{ color: 'var(--text2)', lineHeight: 1.5 }}>
                        <span style={{ color: 'var(--yellow)', fontWeight: 600 }}>{t('infra.restrictedTitle')}</span>
                        {t('infra.restrictedBody')}
                    </div>
                </div>
            )}

            {/* Header */}
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
                <div>
                    <div style={{ fontSize: 16, fontWeight: 700 }}>{isAdmin ? t('infra.titleAdmin') : t('infra.titleMine')}</div>
                    <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>{t('infra.subtitle')}</div>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                    <button onClick={refresh}
                        style={{ padding: '7px 12px', borderRadius: 7, background: 'var(--bg-hover)', border: '1px solid var(--border)', color: 'var(--text2)', fontSize: 11, cursor: 'pointer' }}>
                        {t('infra.refresh')}
                    </button>
                    {!isAdmin && (
                        <button onClick={() => setShowCreate(true)}
                            style={{ padding: '7px 14px', borderRadius: 7, background: 'var(--cyan)', color: '#000', fontSize: 11, fontWeight: 700, border: 'none', cursor: 'pointer' }}>
                            {t('infra.new')}
                        </button>
                    )}
                </div>
            </div>

            {/* Stat cards */}
            <div className="ccd-stat-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 10, marginBottom: 18 }}>
                {STAT_CARDS.map(({ key, label, color }) => {
                    const count  = key ? (counts[key] ?? 0) : totalCount;
                    const active = filterStatus === key;
                    return (
                        <button key={key} onClick={() => setFilterStatus(prev => prev === key ? '' : key)}
                            style={{ padding: 12, borderRadius: 10, background: active ? color + '18' : 'var(--bg-card)', border: `1px solid ${active ? color : 'var(--border)'}`, cursor: 'pointer', textAlign: 'left', transition: 'all 0.15s' }}>
                            <div style={{ fontSize: 22, fontWeight: 800, color }}>{count}</div>
                            <div style={{ fontSize: 10, color: 'var(--text3)', marginTop: 2, textTransform: 'uppercase', letterSpacing: '0.06em' }}>{label}</div>
                        </button>
                    );
                })}
            </div>

            {/* Active filter */}
            {filterStatus && (
                <div style={{ marginBottom: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ fontSize: 11, color: 'var(--text3)' }}>{t('infra.filter')}</span>
                    <StatusBadge status={filterStatus} />
                    <button onClick={() => setFilterStatus('')}
                        style={{ fontSize: 11, color: 'var(--text3)', background: 'none', border: 'none', cursor: 'pointer' }}>
                        {t('infra.clearFilter')}
                    </button>
                </div>
            )}

            {/* Table */}
            {loading ? (
                <div style={{ textAlign: 'center', padding: 40, color: 'var(--text3)', fontSize: 12 }}>{t('common.loading')}</div>
            ) : items.length === 0 ? (
                <div style={{ textAlign: 'center', padding: 40, color: 'var(--text3)', fontSize: 12 }}>
                    {filterStatus
                        ? t('infra.noneWithStatus', { status: STATUS_CFG[filterStatus] ? t(STATUS_CFG[filterStatus].label) : filterStatus })
                        : t('infra.none')}
                </div>
            ) : (
                <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 10, overflow: 'hidden' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                        <thead>
                            <tr style={{ borderBottom: '1px solid var(--border)', background: 'var(--bg-hover)' }}>
                                {['#', t('infra.colType'), t('infra.colSpecs'), ...(isAdmin ? [t('infra.colStudent')] : []), t('infra.colStatus'), t('infra.colDate'), ''].map(h => (
                                    <th key={h} style={{ padding: '10px 14px', textAlign: 'left', fontSize: 10, color: 'var(--text3)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>{h}</th>
                                ))}
                            </tr>
                        </thead>
                        <tbody>
                            {items.map(r => (
                                <tr key={r.id}
                                    style={{ borderBottom: '1px solid var(--border)22' }}
                                    onMouseEnter={e => e.currentTarget.style.background = 'var(--bg-hover)'}
                                    onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>
                                    <td style={{ padding: '10px 14px', color: 'var(--text3)', fontFamily: 'var(--fmono)', fontSize: 10 }}>
                                        {r.id.slice(0, 8)}
                                    </td>
                                    <td style={{ padding: '10px 14px' }}><TypeBadge type={r.request_type} /></td>
                                    <td style={{ padding: '10px 14px' }}><SpecsSummary specs={r.specs} type={r.request_type} /></td>
                                    {isAdmin && (
                                        <td style={{ padding: '10px 14px', color: 'var(--text2)' }}>
                                            {r.student_name || r.student_username}
                                        </td>
                                    )}
                                    <td style={{ padding: '10px 14px' }}><StatusBadge status={r.status} /></td>
                                    <td style={{ padding: '10px 14px', color: 'var(--text3)', fontSize: 11 }}>{fmtDate(r.created_at)}</td>
                                    <td style={{ padding: '10px 14px' }}>
                                        <button onClick={() => setDetail(r)}
                                            style={{ padding: '5px 12px', borderRadius: 6, background: 'var(--bg-hover)', border: '1px solid var(--border)', color: 'var(--text2)', fontSize: 11, cursor: 'pointer' }}>
                                            {t('infra.detail')}
                                        </button>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}

            {/* Modals */}
            {showCreate && (
                <CreateModal onClose={() => setShowCreate(false)} onCreated={refresh} />
            )}
            {detail && (
                <DetailModal
                    req={detail}
                    currentUser={currentUser}
                    onClose={() => setDetail(null)}
                    onUpdated={() => { refresh(); setDetail(null); }}
                />
            )}
        </div>
    );
}
