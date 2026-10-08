import { useState, useEffect, useCallback, useRef } from 'react';
import { fetchTickets, fetchTicket, createTicket, updateTicketStatus, replyTicket, uploadTicketAttachment, fetchMyProxmoxVms, deleteTicket } from '../api';
import { formatCcdId } from '../format';
import { currentLang, locale, useT } from '../i18n';
import { appTimeZone, categoryLabel, useSysConfig } from '../sysconfig';
import useIsMobile from '../useIsMobile';
import PaneTabs from './PaneTabs';
import Icon from './Icons';
import TicketCategoriesModal from './TicketCategoriesModal';
import DeleteRecord from './DeleteRecord';

const API_BASE = import.meta.env.VITE_API_URL || '';
const WS_BASE  = API_BASE
    ? API_BASE.replace(/^http/, 'ws')
    : (window.location.protocol === 'https:' ? 'wss' : 'ws') + '://' + window.location.host;

// Fetch file dengan Authorization header → blob URL (token tidak pernah masuk URL bar / server log)
function _authFetch(path) {
    const tok = localStorage.getItem('hv_token') || '';
    return fetch(`${API_BASE}${path}`, { headers: { Authorization: `Bearer ${tok}` } });
}

// Komponen gambar yang fetch dengan auth header, bukan ?token= di URL
function AuthImage({ path, alt, style, onClickOpen }) {
    const [blobSrc, setBlobSrc] = useState(null);
    const objRef = useRef(null);
    useEffect(() => {
        if (!path) return;
        let alive = true;
        _authFetch(path).then(r => r.blob()).then(blob => {
            if (!alive) return;
            const url = URL.createObjectURL(blob);
            objRef.current = url;
            setBlobSrc(url);
        }).catch(() => {});
        return () => {
            alive = false;
            if (objRef.current) { URL.revokeObjectURL(objRef.current); objRef.current = null; }
        };
    }, [path]);

    if (!blobSrc) return (
        <div style={{ ...style, display: 'flex', alignItems: 'center', justifyContent: 'center',
            background: '#11111180', borderRadius: 6, color: 'var(--text3)', fontSize: 11 }}>
            
        </div>
    );
    return <img src={blobSrc} alt={alt} style={{ ...style, cursor: 'pointer' }} onClick={onClickOpen} />;
}

// Buka file di tab baru via blob (token tidak muncul di URL)
async function openAuthFile(path) {
    try {
        const blob = await _authFetch(path).then(r => r.blob());
        const url = URL.createObjectURL(blob);
        window.open(url, '_blank');
        setTimeout(() => URL.revokeObjectURL(url), 10000);
    } catch { /* silent */ }
}

// Download file non-gambar
async function downloadAuthFile(path, filename) {
    try {
        const blob = await _authFetch(path).then(r => r.blob());
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url; a.download = filename || 'attachment'; a.click();
        URL.revokeObjectURL(url);
    } catch { /* silent */ }
}

const STATUS_COLOR = { OPEN: 'var(--cyan)', IN_PROGRESS: 'var(--yellow)', RESOLVED: 'var(--green)', CLOSED: 'var(--text3)' };
const ROLE_COLOR = { superadmin: '#ff6b35', admin: 'var(--cyan)', sysadmin: 'var(--green)', student: 'var(--purple)' };
const STATUSES = ['OPEN', 'IN_PROGRESS', 'RESOLVED', 'CLOSED'];

// Nama VM dan CCDID. Admin juga melihat VMID dan host-nya, karena VMID hanya unik per Proxmox.
// Tiket lama tanpa CCDID tetap menampilkan VMID.
// Pesan sistem tersimpan dalam dua bahasa; yang lain hanya satu (ditulis pengguna).
const msgText = (m) => (currentLang() === 'en' && m.message_en ? m.message_en : m.message);

function vmLabel(t, isAdmin) {
    const parts = [t.vm_snapshot?.vm_name, t.ccd_id != null ? formatCcdId(t.ccd_id) : null];
    if (t.vm_id && (isAdmin || t.ccd_id == null)) {
        parts.push(`VMID ${t.vm_id}${isAdmin && t.host_name ? ` @ ${t.host_name.replace('__', '/')}` : ''}`);
    }
    return parts.filter(Boolean).join(' · ') || '—';
}

function fmt(iso) {
    if (!iso) return '—';
    try { return new Date(iso).toLocaleString(locale(), { timeZone: appTimeZone(), day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }); }
    catch { return iso; }
}
function Badge({ text, color }) {
    return <span style={{ fontSize: 10, fontFamily: 'var(--fmono)', padding: '1px 8px', borderRadius: 10, color, background: color + '22', whiteSpace: 'nowrap' }}>{text}</span>;
}

const TH = { padding: '8px 12px', textAlign: 'left', fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.07em', fontWeight: 600, whiteSpace: 'nowrap', borderBottom: '1px solid var(--border)' };
const TD = { padding: '8px 12px', fontSize: 12, color: 'var(--text2)', borderBottom: '1px solid var(--border)' };
const inp = { background: 'var(--bg-hover)', border: '1px solid var(--border)', borderRadius: 6, padding: '6px 10px', color: 'var(--text)', fontSize: 12, fontFamily: 'var(--fmono)', outline: 'none' };
const btnPrimary = { padding: '7px 16px', borderRadius: 8, fontSize: 13, fontWeight: 600, background: 'var(--cyan)', color: '#000', border: 'none', cursor: 'pointer' };

// Status card config — order matches display
const STAT_CARDS = [
    { key: '',            label: 'tickets.total',        color: 'var(--cyan)'   },
    { key: 'OPEN',        label: 'tstatus.OPEN',         color: 'var(--cyan)'   },
    { key: 'IN_PROGRESS', label: 'tstatus.IN_PROGRESS',  color: 'var(--yellow)' },
    { key: 'RESOLVED',    label: 'tstatus.RESOLVED',     color: 'var(--green)'  },
    { key: 'CLOSED',      label: 'tstatus.CLOSED',       color: 'var(--text3)'  },
];

export default function TicketsPage({ currentUser }) {
    const isAdmin = ['sysadmin', 'superadmin'].includes(currentUser?.role);
    // `t` dipakai untuk objek tiket di file ini, jadi fungsi terjemahan bernama `tr`.
    const tr = useT();
    const { ticket_categories: cats } = useSysConfig();
    const [data,     setData]     = useState({ total: 0, items: [] });
    const [counts,   setCounts]   = useState({ OPEN: 0, IN_PROGRESS: 0, RESOLVED: 0, CLOSED: 0 });
    const [loading,  setLoading]  = useState(true);
    const [status,   setStatus]   = useState('');
    const [category, setCategory] = useState('');
    const [search,   setSearch]   = useState('');
    const [openId,   setOpenId]   = useState(null);
    const [creating, setCreating] = useState(false);
    const [managingCats, setManagingCats] = useState(false);

    // Load per-status counts in parallel — each request only needs total, not items
    const loadStats = useCallback(async () => {
        try {
            const [a, b, c, d] = await Promise.all([
                fetchTickets({ status: 'OPEN',        page_size: 1 }),
                fetchTickets({ status: 'IN_PROGRESS', page_size: 1 }),
                fetchTickets({ status: 'RESOLVED',    page_size: 1 }),
                fetchTickets({ status: 'CLOSED',      page_size: 1 }),
            ]);
            setCounts({ OPEN: a.total, IN_PROGRESS: b.total, RESOLVED: c.total, CLOSED: d.total });
        } catch { /* */ }
    }, []);

    const load = useCallback(async () => {
        setLoading(true);
        const params = {};
        if (status)   params.status   = status;
        if (category) params.category = category;
        if (search)   params.search   = search;
        setData(await fetchTickets(params));
        setLoading(false);
    }, [status, category, search]);

    useEffect(() => { load(); }, [load]);
    useEffect(() => { loadStats(); }, [loadStats]);

    const refresh = useCallback(() => { load(); loadStats(); }, [load, loadStats]);

    const totalCount = counts.OPEN + counts.IN_PROGRESS + counts.RESOLVED + counts.CLOSED;

    return (
        <div style={{ padding: '16px 20px', maxWidth: 1500, margin: '0 auto' }}>

            {/* ── Header ── */}
            <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 18, flexWrap: 'wrap', gap: 10 }}>
                <div>
                    <div style={{ fontSize: 16, fontWeight: 700 }}>
                        {isAdmin ? tr('tickets.titleAdmin') : tr('tickets.title')}
                    </div>
                    <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 3 }}>
                        {isAdmin ? tr('tickets.subtitleAdmin') : tr('tickets.subtitle')}
                    </div>
                </div>
                {!isAdmin && (
                    <button onClick={() => setCreating(true)} style={btnPrimary}>{tr('tickets.open')}</button>
                )}
            </div>

            {/* ── Stat cards (clickable filter) ── */}
            <div className="ccd-stat-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(5,1fr)', gap: 10, marginBottom: 18 }}>
                {STAT_CARDS.map(({ key, label, color }) => {
                    const val  = key === '' ? totalCount : counts[key];
                    const active = status === key;
                    return (
                        <div key={label}
                            onClick={() => setStatus(active ? '' : key)}
                            style={{
                                background:  active ? color + '15' : 'var(--bg-card)',
                                border:      `1px solid ${active ? color : 'var(--border)'}`,
                                borderLeft:  `3px solid ${color}`,
                                borderRadius: 8, padding: '10px 14px',
                                cursor: 'pointer', transition: 'all 0.15s',
                            }}>
                            <div style={{ fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>{tr(label)}</div>
                            <div style={{ fontFamily: 'var(--fmono)', fontSize: 22, fontWeight: 700, color, marginTop: 2 }}>{val}</div>
                        </div>
                    );
                })}
            </div>

            {/* ── Filter bar ── */}
            <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap', alignItems: 'center' }}>
                <input value={search} onChange={e => setSearch(e.target.value)}
                    placeholder={tr('tickets.search')}
                    style={{ ...inp, flex: 1, minWidth: 200 }} />
                <select value={category} onChange={e => setCategory(e.target.value)} style={inp}>
                    <option value="">{tr('tickets.allCategories')}</option>
                    {cats.map(c => <option key={c.key} value={c.key}>{categoryLabel(c.key, cats)}</option>)}
                </select>
                {/* Active status chip — shows which stat card is active, click to clear */}
                {status && (
                    <button onClick={() => setStatus('')}
                        style={{ display: 'flex', alignItems: 'center', gap: 5, padding: '6px 10px', borderRadius: 6, fontSize: 11, fontFamily: 'var(--fmono)', cursor: 'pointer', background: (STATUS_COLOR[status] || 'var(--cyan)') + '22', border: `1px solid ${STATUS_COLOR[status] || 'var(--cyan)'}44`, color: STATUS_COLOR[status] || 'var(--cyan)' }}>
                        {STATUSES.includes(status) ? tr(`tstatus.${status}`) : status} ✕
                    </button>
                )}
                <button onClick={refresh}
                    style={{ padding: '6px 12px', borderRadius: 6, fontSize: 11, cursor: 'pointer', background: 'var(--bg-hover)', border: '1px solid var(--border)', color: 'var(--text3)', fontFamily: 'var(--fmono)' }}>
                    ↻ {tr('common.refresh')}
                </button>
                {currentUser?.role === 'superadmin' && (
                    <button onClick={() => setManagingCats(true)}
                        style={{ padding: '6px 12px', borderRadius: 6, fontSize: 11, cursor: 'pointer', background: 'var(--bg-hover)', border: '1px solid var(--border)', color: 'var(--text2)', fontFamily: 'var(--fmono)' }}>
                        {tr('tcat.open')}
                    </button>
                )}
            </div>

            {/* ── Table ── */}
            <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 12, overflow: 'hidden' }}>
                {loading ? (
                    <div style={{ padding: 40, textAlign: 'center', color: 'var(--text3)', fontFamily: 'var(--fmono)', fontSize: 12 }}>
                        {tr('tickets.loading')}
                    </div>
                ) : (
                    <div style={{ overflowX: 'auto' }}>
                        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                            <thead><tr>
                                {[tr('tickets.colNo'), ...(isAdmin ? [tr('tickets.colStudent')] : []), tr('tickets.colTitle'), tr('tickets.colVm'), tr('tickets.colCategory'), tr('tickets.colStatus'), tr('tickets.colCreated'), tr('tickets.colClosed')].map(h => (
                                    <th key={h} style={TH}>{h}</th>
                                ))}
                            </tr></thead>
                            <tbody>
                                {data.items.map(t => (
                                    <tr key={t.id} onClick={() => setOpenId(t.id)} style={{ cursor: 'pointer' }}
                                        onMouseEnter={e => e.currentTarget.style.background = 'var(--bg-hover)'}
                                        onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>
                                        <td style={{ ...TD, fontFamily: 'var(--fmono)', color: 'var(--cyan)' }}>{t.ticket_number}</td>
                                        {isAdmin && <td style={{ ...TD, color: 'var(--text)' }}>{t.student_name}</td>}
                                        <td style={{ ...TD, color: 'var(--text)', maxWidth: 320 }}>{t.title}</td>
                                        <td style={{ ...TD, fontFamily: 'var(--fmono)' }}>{vmLabel(t, isAdmin)}</td>
                                        <td style={TD}>{categoryLabel(t.category, cats)}</td>
                                        <td style={TD}><Badge text={STATUSES.includes(t.status) ? tr(`tstatus.${t.status}`) : t.status} color={STATUS_COLOR[t.status] || 'var(--text3)'} /></td>
                                        <td style={{ ...TD, fontFamily: 'var(--fmono)', whiteSpace: 'nowrap' }}>{fmt(t.created_at)}</td>
                                        <td style={{ ...TD, fontFamily: 'var(--fmono)', whiteSpace: 'nowrap', color: t.closed_at ? 'var(--text2)' : 'var(--text3)' }}>
                                            {t.closed_at ? fmt(t.closed_at) : '—'}
                                        </td>
                                    </tr>
                                ))}
                                {data.items.length === 0 && (
                                    <tr><td colSpan={isAdmin ? 8 : 7} style={{ ...TD, textAlign: 'center', color: 'var(--text3)', padding: 36 }}>
                                        {status
                                            ? tr('tickets.emptyStatus', { status: tr(`tstatus.${status}`) })
                                            : isAdmin ? tr('tickets.emptyAdmin') : tr('tickets.emptyStudent')}
                                    </td></tr>
                                )}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>

            {managingCats && <TicketCategoriesModal onClose={() => setManagingCats(false)} />}
            {creating && <CreateTicketModal isAdmin={isAdmin} onClose={() => setCreating(false)} onCreated={() => { setCreating(false); refresh(); }} />}
            {openId    && <TicketThread ticketId={openId} currentUser={currentUser} onClose={() => { setOpenId(null); }} onChanged={refresh} />}
        </div>
    );
}

// ── Create Ticket Modal (juga dipakai dari VmDetailModal) ─────────────────────
export function CreateTicketModal({ onClose, onCreated, vm, isAdmin = false, initial = null }) {
    const tr = useT();
    const { ticket_categories: cats } = useSysConfig();
    const [form, setForm] = useState({
        title: initial?.title || '', category: initial?.category || (vm ? 'REMOTE_ISSUE' : 'OTHERS'), description: '',
        vm_id: vm?.vm_id || '', host_name: vm?.hostName || vm?.host_name || '', ccd_id: '',
    });
    // Tiket dari menu Helpdesk: mahasiswa memilih VM-nya sendiri (CCDID), admin mengetik CCDID.
    const [myVms, setMyVms] = useState([]);
    useEffect(() => {
        if (vm || isAdmin) return undefined;
        let alive = true;
        fetchMyProxmoxVms().then(list => { if (alive) setMyVms(list.filter(v => v.ccd_id != null)); }).catch(() => {});
        return () => { alive = false; };
    }, [vm, isAdmin]);
    const [saving, setSaving] = useState(false);
    const [err, setErr] = useState('');
    const [attachFile, setAttachFile] = useState(null);
    const attachRef = useRef(null);

    const submit = async () => {
        if (!form.title.trim()) { setErr(tr('ticket.titleRequired')); return; }
        setSaving(true); setErr('');
        try {
            const snapshot = vm ? {
                vm_name: vm.vm_name, state: vm.state, cpu: vm.cpu_usage_percent,
                ram_mb: vm.memory_assigned_mb, vcpu: vm.processor_count,
            } : null;
            const result = await createTicket({ ...form, vm_snapshot: snapshot });
            // If an attachment was chosen, upload it and post as the first message
            if (attachFile) {
                try {
                    const uploaded = await uploadTicketAttachment(result.id, attachFile);
                    await replyTicket(result.id, '', { url: uploaded.url, filename: uploaded.filename });
                } catch { /* ticket created — ignore upload failure silently */ }
            }
            onCreated?.(result);
        } catch (e) { setErr(e?.response?.data?.detail || e.message); }
        finally { setSaving(false); }
    };

    const clearAttach = () => { setAttachFile(null); if (attachRef.current) attachRef.current.value = ''; };

    return (
        <Overlay onClose={onClose}>
            <div style={{ width: 'min(560px,95vw)' }}>
                <Header title={tr('ticket.new')} onClose={onClose} />
                <div style={{ padding: 18, display: 'flex', flexDirection: 'column', gap: 12 }}>
                    {vm && <div style={{ fontSize: 11, color: 'var(--cyan)', fontFamily: 'var(--fmono)' }}>VM: {vm.vm_name} ({vm.ccd_id != null ? formatCcdId(vm.ccd_id) : vm.vm_id})</div>}
                    <Field label={tr('ticket.title')}><input value={form.title} onChange={e => setForm(f => ({ ...f, title: e.target.value }))} placeholder={tr('ticket.titlePh')} style={{ ...inp, width: '100%', boxSizing: 'border-box' }} /></Field>
                    <Field label={tr('ticket.category')}>
                        <select value={form.category} onChange={e => setForm(f => ({ ...f, category: e.target.value }))} style={{ ...inp, width: '100%' }}>
                            {cats.map(c => <option key={c.key} value={c.key}>{categoryLabel(c.key, cats)}</option>)}
                        </select>
                    </Field>
                    {!vm && !isAdmin && (
                        <Field label={tr('ticket.relatedVm')}>
                            <select value={form.ccd_id} onChange={e => setForm(f => ({ ...f, ccd_id: e.target.value }))} style={{ ...inp, width: '100%' }}>
                                <option value="">{tr('ticket.noVm')}</option>
                                {myVms.map(v => <option key={v.ccd_id} value={String(v.ccd_id)}>{v.name || 'VM'} ({formatCcdId(v.ccd_id)})</option>)}
                            </select>
                        </Field>
                    )}
                    {!vm && isAdmin && <Field label={tr('ticket.ccdidOptional')}><input value={form.ccd_id} onChange={e => setForm(f => ({ ...f, ccd_id: e.target.value }))} placeholder={tr('ticket.ccdidPh')} style={{ ...inp, width: '100%', boxSizing: 'border-box' }} /></Field>}
                    <Field label={tr('ticket.description')}><textarea value={form.description} onChange={e => setForm(f => ({ ...f, description: e.target.value }))} rows={5} placeholder={tr('ticket.descriptionPh')} style={{ ...inp, width: '100%', boxSizing: 'border-box', resize: 'vertical', fontFamily: 'inherit' }} /></Field>
                    {/* Optional file attachment — uploaded as the ticket's first message */}
                    <Field label={tr('ticket.attachment')}>
                        <input ref={attachRef} type="file" accept="image/*,.pdf,.txt,.log,.zip"
                            style={{ display: 'none' }}
                            onChange={e => setAttachFile(e.target.files?.[0] || null)} />
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                            <button type="button" onClick={() => attachRef.current?.click()}
                                style={{ ...inp, cursor: 'pointer', flexShrink: 0 }}>
                                {tr('ticket.chooseFile')}
                            </button>
                            {attachFile ? (
                                <>
                                    <span style={{ fontSize: 11, color: 'var(--text2)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 220 }}>
                                        {attachFile.name} <span style={{ color: 'var(--text3)' }}>({(attachFile.size / 1024).toFixed(1)} KB)</span>
                                    </span>
                                    <button type="button" onClick={clearAttach}
                                        style={{ background: 'none', border: 'none', color: 'var(--text3)', cursor: 'pointer', fontSize: 14, lineHeight: 1 }}>✕</button>
                                </>
                            ) : (
                                <span style={{ fontSize: 11, color: 'var(--text3)' }}>{tr('ticket.fileHint')}</span>
                            )}
                        </div>
                    </Field>
                    {err && <div style={{ color: 'var(--red)', fontSize: 12 }}>✗ {err}</div>}
                    <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
                        <button onClick={onClose} style={{ ...inp, cursor: 'pointer' }}>{tr('ticket.cancel')}</button>
                        <button onClick={submit} disabled={saving} style={btnPrimary}>{saving ? tr('ticket.sending') : tr('ticket.send')}</button>
                    </div>
                </div>
            </div>
        </Overlay>
    );
}

// ── Ticket Thread (split-screen) ──────────────────────────────────────────────
function TicketThread({ ticketId, currentUser, onClose, onChanged }) {
    const tr = useT();
    const { ticket_categories: cats } = useSysConfig();
    const isAdmin = ['sysadmin', 'superadmin'].includes(currentUser?.role);
    const [data, setData] = useState(null);
    const [messages, setMessages] = useState([]);
    const [reply, setReply] = useState('');
    const [sending, setSending] = useState(false);
    const [wsReady, setWsReady] = useState(false);
    const [pendingFile, setPendingFile] = useState(null);
    const endRef = useRef(null);
    const wsRef = useRef(null);
    const fileInputRef = useRef(null);
    const isMobile = useIsMobile();
    const [pane, setPane] = useState('chat');   // HP: hanya satu panel yang tampil

    const load = useCallback(async () => {
        try {
            const d = await fetchTicket(ticketId);
            setData(d);
            setMessages(d.messages || []);
        } catch { /* */ }
    }, [ticketId]);

    // Muat awal (riwayat + detail tiket)
    useEffect(() => { load(); }, [load]);

    // WebSocket real-time: append pesan baru instan, tanpa refresh manual
    useEffect(() => {
        const token = localStorage.getItem('hv_token') || '';
        const url = `${WS_BASE}/api/tickets/${ticketId}/ws?token=${encodeURIComponent(token)}&lang=${currentLang()}`;
        let ws;
        try {
            ws = new WebSocket(url);
            wsRef.current = ws;
            ws.onopen = () => setWsReady(true);
            ws.onclose = () => setWsReady(false);
            ws.onmessage = (ev) => {
                let m; try { m = JSON.parse(ev.data); } catch { return; }
                if (m.type === 'message') {
                    setMessages(prev => prev.some(x => x.id === m.id) ? prev : [...prev, {
                        id: m.id, sender_id: m.sender_id, sender_role: m.sender_role,
                        sender_name: m.sender_name, message: m.message, message_en: m.message_en, timestamp: m.timestamp,
                        attachment_url: m.attachment_url || null,
                        attachment_name: m.attachment_name || null,
                    }]);
                } else if (m.type === 'status') {
                    // refresh penuh agar closed_at ikut ter-update
                    load();
                    onChanged?.();
                } else if (m.type === 'error') {
                    alert(m.message || tr('ticket.actionDenied'));
                }
            };
        } catch { /* fallback: tetap pakai HTTP reply + reload */ }
        return () => { try { ws && ws.close(); } catch { /* noop */ } };
    }, [ticketId]);

    // Auto-scroll ke pesan terbaru, juga saat kembali ke tab Chat di HP
    useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages.length, pane]);

    const clearFile = () => { setPendingFile(null); if (fileInputRef.current) fileInputRef.current.value = ''; };

    const send = async () => {
        const msg = reply.trim();
        if (!msg && !pendingFile) return;
        if (data?.ticket?.status === 'CLOSED') { alert(tr('ticket.closedNoReply')); return; }
        setSending(true);
        try {
            // Upload attachment first (if any) — then include the URL in the message
            let attachment = null;
            if (pendingFile) {
                const uploaded = await uploadTicketAttachment(ticketId, pendingFile);
                attachment = { url: uploaded.url, filename: uploaded.filename };
            }
            // Kirim via WS bila terhubung (server broadcast balik → muncul instan); else fallback HTTP
            if (wsReady && wsRef.current?.readyState === WebSocket.OPEN) {
                wsRef.current.send(JSON.stringify({
                    message: msg,
                    ...(attachment ? { attachment_url: attachment.url, attachment_name: attachment.filename } : {}),
                }));
            } else {
                await replyTicket(ticketId, msg, attachment);
                await load();
            }
            setReply('');
            clearFile();
            onChanged?.();
        } catch (e) { alert(e?.response?.data?.detail || e.message); }
        finally { setSending(false); }
    };
    const setStatus = async (s) => {
        // Konfirmasi khusus saat menutup tiket: aksi mengunci chat & hanya superadmin yang bisa membuka kembali.
        if (s === 'CLOSED') {
            if (!window.confirm(tr('ticket.closeConfirm'))) return;
        }
        try { await updateTicketStatus(ticketId, s); await load(); onChanged?.(); }
        catch (e) { alert(e?.response?.data?.detail || e.message); }
    };

    if (!data) return <Overlay onClose={onClose}><div style={{ padding: 30, color: 'var(--text3)' }}>{tr('ticket.loadingThread')}</div></Overlay>;
    const t = data.ticket;
    const snap = t.vm_snapshot;
    const isClosed = t.status === 'CLOSED';
    const isSuper = currentUser?.role === 'superadmin';

    return (
        <Overlay onClose={onClose} fullscreen={isMobile}>
            <div style={{ width: isMobile ? '100vw' : 'min(900px,96vw)', height: isMobile ? '100dvh' : 'min(640px,92vh)', display: 'flex', flexDirection: 'column' }}>
                <Header title={`${t.ticket_number} · ${t.title}`} onClose={onClose}
                    actions={currentUser?.role === 'superadmin' && (
                        <DeleteRecord title={tr('del.ticketTitle', { n: t.ticket_number })}
                            summary={`${t.title} · ${t.student_name} · ${tr(`tstatus.${t.status}`)}`}
                            onDelete={() => deleteTicket(ticketId)} onDeleted={() => { onChanged?.(); onClose(); }} />
                    )} />
                {isMobile && <PaneTabs value={pane} onChange={setPane} chatCount={messages.filter(m => m.sender_role !== 'system').length} />}
                <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
                    {/* Left: detail + snapshot */}
                    {(!isMobile || pane === 'detail') && (
                    <div style={{ width: isMobile ? '100%' : 280, borderRight: isMobile ? 'none' : '1px solid var(--border)', padding: 16, overflowY: 'auto', flexShrink: 0, boxSizing: 'border-box' }}>
                        <Row k={tr('ticket.status')}><Badge text={STATUSES.includes(t.status) ? tr(`tstatus.${t.status}`) : t.status} color={STATUS_COLOR[t.status]} /></Row>
                        <Row k={tr('ticket.student')}>{t.student_name}</Row>
                        <Row k={tr('ticket.category')}>{categoryLabel(t.category, cats)}</Row>
                        <Row k={tr('ticket.vm')}>{vmLabel(t, isAdmin)}</Row>
                        <Row k={tr('ticket.created')}>{fmt(t.created_at)}</Row>
                        {t.closed_at && <Row k={tr('ticket.closed')}>{fmt(t.closed_at)}</Row>}
                        <div style={{ marginTop: 12, fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6 }}>{tr('ticket.description')}</div>
                        <div style={{ fontSize: 12, color: 'var(--text2)', whiteSpace: 'pre-wrap' }}>{t.description || '—'}</div>
                        {snap && (
                            <>
                                <div style={{ marginTop: 14, fontSize: 11, color: 'var(--cyan)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6 }}>{tr('ticket.snapshot')}</div>
                                <Row k={tr('ticket.snapState')}>{snap.state || '—'}</Row>
                                <Row k="CPU">{snap.cpu != null ? `${Math.round(snap.cpu)}%` : '—'}</Row>
                                <Row k="RAM">{snap.ram_mb != null ? `${(snap.ram_mb / 1024).toFixed(1)} GB` : '—'}</Row>
                                <Row k="vCPU">{snap.vcpu ?? '—'}</Row>
                            </>
                        )}
                        {isAdmin && (
                            <div style={{ marginTop: 16 }}>
                                <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6 }}>{tr('ticket.changeStatus')}</div>
                                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                                    {STATUSES.map(s => {
                                        // Tiket CLOSED terkunci: hanya superadmin yang boleh mengubah lagi.
                                        const locked = isClosed && !isSuper;
                                        const disabled = s === t.status || locked;
                                        return (
                                            <button key={s} onClick={() => setStatus(s)} disabled={disabled}
                                                style={{
                                                    padding: '4px 10px', borderRadius: 6, fontSize: 10, fontFamily: 'var(--fmono)', cursor: disabled ? 'default' : 'pointer',
                                                    background: s === t.status ? STATUS_COLOR[s] + '33' : 'var(--bg-hover)', color: STATUS_COLOR[s], border: `1px solid ${STATUS_COLOR[s]}55`, opacity: disabled ? (s === t.status ? 1 : 0.35) : 0.8
                                                }}>
                                                {tr(`tstatus.${s}`)}
                                            </button>
                                        );
                                    })}
                                </div>
                                {isClosed && (
                                    <div style={{ fontSize: 10, color: 'var(--text3)', marginTop: 8, lineHeight: 1.5 }}>
                                        {isSuper ? tr('ticket.reopenSuper') : tr('ticket.closedLocked')}
                                    </div>
                                )}
                            </div>
                        )}
                    </div>
                    )}

                    {/* Right: chat thread */}
                    {(!isMobile || pane === 'chat') && (
                    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
                        <div style={{ flex: 1, overflowY: 'auto', padding: 16, display: 'flex', flexDirection: 'column', gap: 10 }}>
                            {messages.length === 0 && <div style={{ color: 'var(--text3)', fontSize: 12, textAlign: 'center', marginTop: 20 }}>{tr('ticket.noReplies')}</div>}
                            {messages.map(m => {
                                // Pesan sistem (perubahan status) — tampil di tengah, gaya berbeda.
                                if (m.sender_role === 'system') {
                                    return (
                                        <div key={m.id} style={{ alignSelf: 'center', maxWidth: '90%', textAlign: 'center' }}>
                                            <span style={{ fontSize: 10, fontFamily: 'var(--fmono)', color: 'var(--text3)', background: 'var(--bg-hover)', border: '1px solid var(--border)', borderRadius: 10, padding: '3px 10px' }}>
                                                {msgText(m)} · {fmt(m.timestamp)}
                                            </span>
                                        </div>
                                    );
                                }
                                const mine = m.sender_id === currentUser?.id;
                                const staff = ['sysadmin', 'superadmin'].includes(m.sender_role);
                                const isImage = m.attachment_url && /\.(jpe?g|png|gif|webp)$/i.test(m.attachment_url);
                                return (
                                    <div key={m.id} style={{ alignSelf: mine ? 'flex-end' : 'flex-start', maxWidth: isMobile ? '88%' : '78%' }}>
                                        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 2, justifyContent: mine ? 'flex-end' : 'flex-start' }}>
                                            <span style={{ fontSize: 10, color: 'var(--text2)', fontFamily: 'var(--fmono)' }}>{m.sender_name}</span>
                                            <Badge text={tr(`role.${m.sender_role}`)} color={ROLE_COLOR[m.sender_role] || 'var(--text3)'} />
                                        </div>
                                        <div style={{
                                            padding: '8px 12px', borderRadius: 10, fontSize: 13, color: 'var(--text)', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere',
                                            background: staff ? 'var(--cyan-glow, #00e5ff18)' : 'var(--bg-hover)', border: `1px solid ${staff ? 'var(--cyan)44' : 'var(--border)'}`
                                        }}>
                                            {m.message && <div>{msgText(m)}</div>}
                                            {m.attachment_url && (
                                                <div style={{ marginTop: m.message ? 8 : 0 }}>
                                                    {isImage ? (
                                                        <AuthImage
                                                            path={m.attachment_url}
                                                            alt={m.attachment_name || 'attachment'}
                                                            style={{ maxWidth: '100%', maxHeight: 220, borderRadius: 6, display: 'block' }}
                                                            onClickOpen={() => openAuthFile(m.attachment_url)}
                                                        />
                                                    ) : (
                                                        <button onClick={() => downloadAuthFile(m.attachment_url, m.attachment_name)}
                                                            style={{ background: 'none', border: 'none', padding: 0, cursor: 'pointer',
                                                                color: 'var(--cyan)', fontSize: 12, display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                                                            <Icon name="paperclip" size={13} />
                                                            <span style={{ textDecoration: 'underline' }}>{m.attachment_name || tr('ticket.attachmentFallback')}</span>
                                                        </button>
                                                    )}
                                                </div>
                                            )}
                                        </div>
                                        <div style={{ fontSize: 9, color: 'var(--text3)', marginTop: 2, textAlign: mine ? 'right' : 'left' }}>{fmt(m.timestamp)}</div>
                                    </div>
                                );
                            })}
                            <div ref={endRef} />
                        </div>
                        {isClosed ? (
                            <div style={{ borderTop: '1px solid var(--border)', padding: 14, textAlign: 'center', fontSize: 12, color: 'var(--text3)' }}>
                                {tr('ticket.locked', { when: t.closed_at ? ` · ${fmt(t.closed_at)}` : '' })}
                            </div>
                        ) : (
                            <div style={{ borderTop: '1px solid var(--border)', padding: 12 }}>
                                {/* Pending attachment preview strip */}
                                {pendingFile && (
                                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8, padding: '4px 8px', background: 'var(--bg-hover)', borderRadius: 6, fontSize: 11, color: 'var(--text2)' }}>
                                        <Icon name="paperclip" size={13} />
                                        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0, flex: 1 }}>{pendingFile.name}</span>
                                        <span style={{ color: 'var(--text3)', flexShrink: 0 }}>({(pendingFile.size / 1024).toFixed(1)} KB)</span>
                                        <button onClick={clearFile} style={{ background: 'none', border: 'none', color: 'var(--text3)', cursor: 'pointer', fontSize: 14, lineHeight: 1, flexShrink: 0 }}>✕</button>
                                    </div>
                                )}
                                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                                    {/* Hidden file input + visible trigger button */}
                                    <input ref={fileInputRef} type="file" accept="image/*,.pdf,.txt,.log,.zip"
                                        style={{ display: 'none' }}
                                        onChange={e => { const f = e.target.files?.[0]; if (f) setPendingFile(f); }} />
                                    <button type="button" onClick={() => fileInputRef.current?.click()}
                                        title={tr('ticket.attach')} aria-label={tr('ticket.attach')}
                                        style={{ width: 34, height: 34, borderRadius: 6, flexShrink: 0, background: pendingFile ? 'var(--cyan)22' : 'var(--bg-hover)', border: `1px solid ${pendingFile ? 'var(--cyan)55' : 'var(--border)'}`, color: pendingFile ? 'var(--cyan)' : 'var(--text2)', cursor: 'pointer', fontSize: 16, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                                        
                                    </button>
                                    <input value={reply} onChange={e => setReply(e.target.value)}
                                        onKeyDown={e => e.key === 'Enter' && !e.shiftKey && send()}
                                        placeholder={tr('ticket.replyPh')} style={{ ...inp, flex: 1, minWidth: 0 }} />
                                    <button onClick={send} disabled={sending} style={btnPrimary}>{sending ? '...' : tr('ticket.reply')}</button>
                                </div>
                            </div>
                        )}
                    </div>
                    )}
                </div>
            </div>
        </Overlay>
    );
}

// ── Small shared bits ─────────────────────────────────────────────────────────
// fullscreen: dipakai di HP, modal mengisi seluruh layar tanpa jarak dan sudut membulat.
function Overlay({ children, onClose, fullscreen = false }) {
    return (
        <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.78)', zIndex: 3000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: fullscreen ? 0 : 16 }}>
            <div onClick={e => e.stopPropagation()} style={{ background: 'var(--bg-card)', border: fullscreen ? 'none' : '1px solid var(--border-light)', borderRadius: fullscreen ? 0 : 14, overflow: 'auto', maxWidth: '100%', maxHeight: '100%' }}>{children}</div>
        </div>
    );
}
function Header({ title, onClose, actions }) {
    const tr = useT();
    return (
        <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', background: 'var(--bg-card2)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, flexShrink: 0 }}>
            <span title={title} style={{ fontSize: 14, fontWeight: 600, color: 'var(--cyan)', fontFamily: 'var(--fmono)', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{title}</span>
            {actions}
            <button onClick={onClose} aria-label={tr('common.close')} style={{ width: 28, height: 28, borderRadius: 6, background: 'var(--bg-hover)', border: '1px solid var(--border)', color: 'var(--text2)', cursor: 'pointer', flexShrink: 0 }}>✕</button>
        </div>
    );
}
function Field({ label, children }) {
    return <div><div style={{ fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 4 }}>{label}</div>{children}</div>;
}
function Row({ k, children }) {
    return <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10, padding: '5px 0', borderBottom: '1px solid var(--border)', fontSize: 12 }}><span style={{ color: 'var(--text3)', flexShrink: 0 }}>{k}</span><span style={{ color: 'var(--text)', fontFamily: 'var(--fmono)', textAlign: 'right', overflowWrap: 'anywhere', minWidth: 0 }}>{children}</span></div>;
}
