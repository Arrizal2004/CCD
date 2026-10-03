import { useState, useEffect, useCallback, useRef } from 'react';
import { fetchTickets, fetchTicket, createTicket, updateTicketStatus, replyTicket, uploadTicketAttachment, fetchMyProxmoxVms } from '../api';
import { formatCcdId } from '../format';

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
            ⏳
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
const CAT_LABEL = { REMOTE_ISSUE: 'Remote Issue', PERFORMANCE: 'Performance', RESOURCE_REQUEST: 'Resource Request', OTHERS: 'Others' };
const ROLE_COLOR = { superadmin: '#ff6b35', admin: 'var(--cyan)', sysadmin: 'var(--green)', student: 'var(--purple)' };
const STATUSES = ['OPEN', 'IN_PROGRESS', 'RESOLVED', 'CLOSED'];
const CATEGORIES = ['REMOTE_ISSUE', 'PERFORMANCE', 'RESOURCE_REQUEST', 'OTHERS'];

// Nama VM dan CCDID. Admin juga melihat VMID dan host-nya, karena VMID hanya unik per Proxmox.
// Tiket lama tanpa CCDID tetap menampilkan VMID.
function vmLabel(t, isAdmin) {
    const parts = [t.vm_snapshot?.vm_name, t.ccd_id != null ? formatCcdId(t.ccd_id) : null];
    if (t.vm_id && (isAdmin || t.ccd_id == null)) {
        parts.push(`VMID ${t.vm_id}${isAdmin && t.host_name ? ` @ ${t.host_name.replace('__', '/')}` : ''}`);
    }
    return parts.filter(Boolean).join(' · ') || '—';
}

function fmt(iso) {
    if (!iso) return '—';
    try { return new Date(iso).toLocaleString('id-ID', { timeZone: 'Asia/Jakarta', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }); }
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
    { key: '',            label: 'Total',       color: 'var(--cyan)'   },
    { key: 'OPEN',        label: 'Open',        color: 'var(--cyan)'   },
    { key: 'IN_PROGRESS', label: 'In Progress', color: 'var(--yellow)' },
    { key: 'RESOLVED',    label: 'Resolved',    color: 'var(--green)'  },
    { key: 'CLOSED',      label: 'Closed',      color: 'var(--text3)'  },
];

export default function TicketsPage({ currentUser }) {
    const isAdmin = ['sysadmin', 'superadmin'].includes(currentUser?.role);
    const [data,     setData]     = useState({ total: 0, items: [] });
    const [counts,   setCounts]   = useState({ OPEN: 0, IN_PROGRESS: 0, RESOLVED: 0, CLOSED: 0 });
    const [loading,  setLoading]  = useState(true);
    const [status,   setStatus]   = useState('');
    const [category, setCategory] = useState('');
    const [search,   setSearch]   = useState('');
    const [openId,   setOpenId]   = useState(null);
    const [creating, setCreating] = useState(false);

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
                        {isAdmin ? 'Helpdesk / Support Tickets' : 'My Tickets'}
                    </div>
                    <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 3 }}>
                        {isAdmin ? 'Kelola semua tiket masuk dari mahasiswa' : 'Buat dan pantau tiket bantuan teknis kamu'}
                    </div>
                </div>
                {!isAdmin && (
                    <button onClick={() => setCreating(true)} style={btnPrimary}>+ Open Ticket</button>
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
                            <div style={{ fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>{label}</div>
                            <div style={{ fontFamily: 'var(--fmono)', fontSize: 22, fontWeight: 700, color, marginTop: 2 }}>{val}</div>
                        </div>
                    );
                })}
            </div>

            {/* ── Filter bar ── */}
            <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap', alignItems: 'center' }}>
                <input value={search} onChange={e => setSearch(e.target.value)}
                    placeholder="🔍 Cari judul / no. tiket / CCDID / student"
                    style={{ ...inp, flex: 1, minWidth: 200 }} />
                <select value={category} onChange={e => setCategory(e.target.value)} style={inp}>
                    <option value="">Semua Kategori</option>
                    {CATEGORIES.map(c => <option key={c} value={c}>{CAT_LABEL[c]}</option>)}
                </select>
                {/* Active status chip — shows which stat card is active, click to clear */}
                {status && (
                    <button onClick={() => setStatus('')}
                        style={{ display: 'flex', alignItems: 'center', gap: 5, padding: '6px 10px', borderRadius: 6, fontSize: 11, fontFamily: 'var(--fmono)', cursor: 'pointer', background: (STATUS_COLOR[status] || 'var(--cyan)') + '22', border: `1px solid ${STATUS_COLOR[status] || 'var(--cyan)'}44`, color: STATUS_COLOR[status] || 'var(--cyan)' }}>
                        {status.replace('_', ' ')} ✕
                    </button>
                )}
                <button onClick={refresh}
                    style={{ padding: '6px 12px', borderRadius: 6, fontSize: 11, cursor: 'pointer', background: 'var(--bg-hover)', border: '1px solid var(--border)', color: 'var(--text3)', fontFamily: 'var(--fmono)' }}>
                    ↻ Refresh
                </button>
            </div>

            {/* ── Table ── */}
            <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 12, overflow: 'hidden' }}>
                {loading ? (
                    <div style={{ padding: 40, textAlign: 'center', color: 'var(--text3)', fontFamily: 'var(--fmono)', fontSize: 12 }}>
                        Memuat data...
                    </div>
                ) : (
                    <div style={{ overflowX: 'auto' }}>
                        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                            <thead><tr>
                                {['Ticket No', ...(isAdmin ? ['Student'] : []), 'Title', 'VM Target', 'Category', 'Status', 'Created', 'Closed'].map(h => (
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
                                        <td style={TD}>{CAT_LABEL[t.category] || t.category}</td>
                                        <td style={TD}><Badge text={t.status} color={STATUS_COLOR[t.status] || 'var(--text3)'} /></td>
                                        <td style={{ ...TD, fontFamily: 'var(--fmono)', whiteSpace: 'nowrap' }}>{fmt(t.created_at)}</td>
                                        <td style={{ ...TD, fontFamily: 'var(--fmono)', whiteSpace: 'nowrap', color: t.closed_at ? 'var(--text2)' : 'var(--text3)' }}>
                                            {t.closed_at ? fmt(t.closed_at) : '—'}
                                        </td>
                                    </tr>
                                ))}
                                {data.items.length === 0 && (
                                    <tr><td colSpan={isAdmin ? 8 : 7} style={{ ...TD, textAlign: 'center', color: 'var(--text3)', padding: 36 }}>
                                        {status
                                            ? `Tidak ada tiket dengan status ${status.replace('_',' ')}`
                                            : isAdmin ? 'Belum ada tiket masuk.' : 'Kamu belum punya tiket. Klik "+ Open Ticket" untuk membuat.'}
                                    </td></tr>
                                )}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>

            {creating && <CreateTicketModal isAdmin={isAdmin} onClose={() => setCreating(false)} onCreated={() => { setCreating(false); refresh(); }} />}
            {openId    && <TicketThread ticketId={openId} currentUser={currentUser} onClose={() => { setOpenId(null); }} onChanged={refresh} />}
        </div>
    );
}

// ── Create Ticket Modal (juga dipakai dari VmDetailModal) ─────────────────────
export function CreateTicketModal({ onClose, onCreated, vm, isAdmin = false }) {
    const [form, setForm] = useState({
        title: '', category: vm ? 'REMOTE_ISSUE' : 'OTHERS', description: '',
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
        if (!form.title.trim()) { setErr('Judul wajib diisi'); return; }
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
                <Header title="Open Support Ticket" onClose={onClose} />
                <div style={{ padding: 18, display: 'flex', flexDirection: 'column', gap: 12 }}>
                    {vm && <div style={{ fontSize: 11, color: 'var(--cyan)', fontFamily: 'var(--fmono)' }}>VM: {vm.vm_name} ({vm.ccd_id != null ? formatCcdId(vm.ccd_id) : vm.vm_id})</div>}
                    <Field label="Judul"><input value={form.title} onChange={e => setForm(f => ({ ...f, title: e.target.value }))} placeholder="Ringkas masalahnya" style={{ ...inp, width: '100%', boxSizing: 'border-box' }} /></Field>
                    <Field label="Kategori">
                        <select value={form.category} onChange={e => setForm(f => ({ ...f, category: e.target.value }))} style={{ ...inp, width: '100%' }}>
                            {CATEGORIES.map(c => <option key={c} value={c}>{CAT_LABEL[c]}</option>)}
                        </select>
                    </Field>
                    {!vm && !isAdmin && (
                        <Field label="VM terkait (opsional)">
                            <select value={form.ccd_id} onChange={e => setForm(f => ({ ...f, ccd_id: e.target.value }))} style={{ ...inp, width: '100%' }}>
                                <option value="">Tidak terkait VM tertentu</option>
                                {myVms.map(v => <option key={v.ccd_id} value={String(v.ccd_id)}>{v.name || 'VM'} ({formatCcdId(v.ccd_id)})</option>)}
                            </select>
                        </Field>
                    )}
                    {!vm && isAdmin && <Field label="CCDID (opsional)"><input value={form.ccd_id} onChange={e => setForm(f => ({ ...f, ccd_id: e.target.value }))} placeholder="mis. CCD-0007" style={{ ...inp, width: '100%', boxSizing: 'border-box' }} /></Field>}
                    <Field label="Deskripsi"><textarea value={form.description} onChange={e => setForm(f => ({ ...f, description: e.target.value }))} rows={5} placeholder="Jelaskan detail masalah / permintaan" style={{ ...inp, width: '100%', boxSizing: 'border-box', resize: 'vertical', fontFamily: 'inherit' }} /></Field>
                    {/* Optional file attachment — uploaded as the ticket's first message */}
                    <Field label="Lampiran (opsional)">
                        <input ref={attachRef} type="file" accept="image/*,.pdf,.txt,.log,.zip"
                            style={{ display: 'none' }}
                            onChange={e => setAttachFile(e.target.files?.[0] || null)} />
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                            <button type="button" onClick={() => attachRef.current?.click()}
                                style={{ ...inp, cursor: 'pointer', flexShrink: 0 }}>
                                📎 Pilih File
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
                                <span style={{ fontSize: 11, color: 'var(--text3)' }}>Gambar / PDF / TXT / ZIP · maks 10 MB</span>
                            )}
                        </div>
                    </Field>
                    {err && <div style={{ color: 'var(--red)', fontSize: 12 }}>✗ {err}</div>}
                    <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
                        <button onClick={onClose} style={{ ...inp, cursor: 'pointer' }}>Batal</button>
                        <button onClick={submit} disabled={saving} style={btnPrimary}>{saving ? 'Mengirim...' : 'Kirim Tiket'}</button>
                    </div>
                </div>
            </div>
        </Overlay>
    );
}

// ── Ticket Thread (split-screen) ──────────────────────────────────────────────
function TicketThread({ ticketId, currentUser, onClose, onChanged }) {
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
        const url = `${WS_BASE}/api/tickets/${ticketId}/ws?token=${encodeURIComponent(token)}`;
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
                        sender_name: m.sender_name, message: m.message, timestamp: m.timestamp,
                        attachment_url: m.attachment_url || null,
                        attachment_name: m.attachment_name || null,
                    }]);
                } else if (m.type === 'status') {
                    // refresh penuh agar closed_at ikut ter-update
                    load();
                    onChanged?.();
                } else if (m.type === 'error') {
                    alert(m.message || 'Aksi ditolak');
                }
            };
        } catch { /* fallback: tetap pakai HTTP reply + reload */ }
        return () => { try { ws && ws.close(); } catch { /* noop */ } };
    }, [ticketId]);

    // Auto-scroll ke pesan terbaru
    useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages.length]);

    const clearFile = () => { setPendingFile(null); if (fileInputRef.current) fileInputRef.current.value = ''; };

    const send = async () => {
        const msg = reply.trim();
        if (!msg && !pendingFile) return;
        if (data?.ticket?.status === 'CLOSED') { alert('Tiket sudah ditutup — tidak bisa menambahkan pesan.'); return; }
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
            if (!window.confirm('Tutup tiket ini?\n\nSetelah ditutup, chat dikunci dan status tidak bisa diubah lagi kecuali oleh superadmin. Lanjutkan?')) return;
        }
        try { await updateTicketStatus(ticketId, s); await load(); onChanged?.(); }
        catch (e) { alert(e?.response?.data?.detail || e.message); }
    };

    if (!data) return <Overlay onClose={onClose}><div style={{ padding: 30, color: 'var(--text3)' }}>Memuat...</div></Overlay>;
    const t = data.ticket;
    const snap = t.vm_snapshot;
    const isClosed = t.status === 'CLOSED';
    const isSuper = currentUser?.role === 'superadmin';

    return (
        <Overlay onClose={onClose}>
            <div style={{ width: 'min(900px,96vw)', height: 'min(640px,92vh)', display: 'flex', flexDirection: 'column' }}>
                <Header title={`${t.ticket_number} · ${t.title}`} onClose={onClose} />
                <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
                    {/* Left: detail + snapshot */}
                    <div style={{ width: 280, borderRight: '1px solid var(--border)', padding: 16, overflowY: 'auto', flexShrink: 0 }}>
                        <Row k="Status"><Badge text={t.status} color={STATUS_COLOR[t.status]} /></Row>
                        <Row k="Student">{t.student_name}</Row>
                        <Row k="Kategori">{CAT_LABEL[t.category] || t.category}</Row>
                        <Row k="VM">{vmLabel(t, isAdmin)}</Row>
                        <Row k="Dibuat">{fmt(t.created_at)}</Row>
                        {t.closed_at && <Row k="Ditutup">{fmt(t.closed_at)}</Row>}
                        <div style={{ marginTop: 12, fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6 }}>Deskripsi</div>
                        <div style={{ fontSize: 12, color: 'var(--text2)', whiteSpace: 'pre-wrap' }}>{t.description || '—'}</div>
                        {snap && (
                            <>
                                <div style={{ marginTop: 14, fontSize: 11, color: 'var(--cyan)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6 }}>Snapshot VM saat lapor</div>
                                <Row k="State">{snap.state || '—'}</Row>
                                <Row k="CPU">{snap.cpu != null ? `${Math.round(snap.cpu)}%` : '—'}</Row>
                                <Row k="RAM">{snap.ram_mb != null ? `${(snap.ram_mb / 1024).toFixed(1)} GB` : '—'}</Row>
                                <Row k="vCPU">{snap.vcpu ?? '—'}</Row>
                            </>
                        )}
                        {isAdmin && (
                            <div style={{ marginTop: 16 }}>
                                <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6 }}>Ubah Status</div>
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
                                                {s}
                                            </button>
                                        );
                                    })}
                                </div>
                                {isClosed && (
                                    <div style={{ fontSize: 10, color: 'var(--text3)', marginTop: 8, lineHeight: 1.5 }}>
                                        {isSuper ? '🔓 Sebagai superadmin, Anda dapat membuka kembali tiket ini.'
                                                 : '🔒 Tiket sudah ditutup. Hanya superadmin yang dapat membukanya kembali.'}
                                    </div>
                                )}
                            </div>
                        )}
                    </div>

                    {/* Right: chat thread */}
                    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
                        <div style={{ flex: 1, overflowY: 'auto', padding: 16, display: 'flex', flexDirection: 'column', gap: 10 }}>
                            {messages.length === 0 && <div style={{ color: 'var(--text3)', fontSize: 12, textAlign: 'center', marginTop: 20 }}>Belum ada balasan. Mulai percakapan.</div>}
                            {messages.map(m => {
                                // Pesan sistem (perubahan status) — tampil di tengah, gaya berbeda.
                                if (m.sender_role === 'system') {
                                    return (
                                        <div key={m.id} style={{ alignSelf: 'center', maxWidth: '90%', textAlign: 'center' }}>
                                            <span style={{ fontSize: 10, fontFamily: 'var(--fmono)', color: 'var(--text3)', background: 'var(--bg-hover)', border: '1px solid var(--border)', borderRadius: 10, padding: '3px 10px' }}>
                                                {m.message} · {fmt(m.timestamp)}
                                            </span>
                                        </div>
                                    );
                                }
                                const mine = m.sender_id === currentUser?.id;
                                const staff = ['sysadmin', 'superadmin'].includes(m.sender_role);
                                const isImage = m.attachment_url && /\.(jpe?g|png|gif|webp)$/i.test(m.attachment_url);
                                return (
                                    <div key={m.id} style={{ alignSelf: mine ? 'flex-end' : 'flex-start', maxWidth: '78%' }}>
                                        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 2, justifyContent: mine ? 'flex-end' : 'flex-start' }}>
                                            <span style={{ fontSize: 10, color: 'var(--text2)', fontFamily: 'var(--fmono)' }}>{m.sender_name}</span>
                                            <Badge text={m.sender_role} color={ROLE_COLOR[m.sender_role] || 'var(--text3)'} />
                                        </div>
                                        <div style={{
                                            padding: '8px 12px', borderRadius: 10, fontSize: 13, color: 'var(--text)', whiteSpace: 'pre-wrap',
                                            background: staff ? 'var(--cyan-glow, #00e5ff18)' : 'var(--bg-hover)', border: `1px solid ${staff ? 'var(--cyan)44' : 'var(--border)'}`
                                        }}>
                                            {m.message && <div>{m.message}</div>}
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
                                                            <span>📎</span>
                                                            <span style={{ textDecoration: 'underline' }}>{m.attachment_name || 'Attachment'}</span>
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
                                🔒 Tiket ditutup{t.closed_at ? ` pada ${fmt(t.closed_at)}` : ''} — chat dikunci.
                            </div>
                        ) : (
                            <div style={{ borderTop: '1px solid var(--border)', padding: 12 }}>
                                {/* Pending attachment preview strip */}
                                {pendingFile && (
                                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8, padding: '4px 8px', background: 'var(--bg-hover)', borderRadius: 6, fontSize: 11, color: 'var(--text2)' }}>
                                        <span>📎</span>
                                        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 280 }}>{pendingFile.name}</span>
                                        <span style={{ color: 'var(--text3)', flexShrink: 0 }}>({(pendingFile.size / 1024).toFixed(1)} KB)</span>
                                        <button onClick={clearFile} style={{ background: 'none', border: 'none', color: 'var(--text3)', cursor: 'pointer', fontSize: 14, lineHeight: 1, flexShrink: 0 }}>✕</button>
                                    </div>
                                )}
                                <div style={{ display: 'flex', gap: 8 }}>
                                    {/* Hidden file input + visible trigger button */}
                                    <input ref={fileInputRef} type="file" accept="image/*,.pdf,.txt,.log,.zip"
                                        style={{ display: 'none' }}
                                        onChange={e => { const f = e.target.files?.[0]; if (f) setPendingFile(f); }} />
                                    <button type="button" onClick={() => fileInputRef.current?.click()}
                                        title="Lampirkan file (gambar / PDF / TXT / ZIP · maks 10 MB)"
                                        style={{ width: 34, height: 34, borderRadius: 6, flexShrink: 0, background: pendingFile ? 'var(--cyan)22' : 'var(--bg-hover)', border: `1px solid ${pendingFile ? 'var(--cyan)55' : 'var(--border)'}`, color: pendingFile ? 'var(--cyan)' : 'var(--text2)', cursor: 'pointer', fontSize: 16, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                                        📎
                                    </button>
                                    <input value={reply} onChange={e => setReply(e.target.value)}
                                        onKeyDown={e => e.key === 'Enter' && !e.shiftKey && send()}
                                        placeholder="Ketik balasan..." style={{ ...inp, flex: 1 }} />
                                    <button onClick={send} disabled={sending} style={btnPrimary}>{sending ? '...' : 'Kirim'}</button>
                                </div>
                            </div>
                        )}
                    </div>
                </div>
            </div>
        </Overlay>
    );
}

// ── Small shared bits ─────────────────────────────────────────────────────────
function Overlay({ children, onClose }) {
    return (
        <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.78)', zIndex: 3000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }}>
            <div onClick={e => e.stopPropagation()} style={{ background: 'var(--bg-card)', border: '1px solid var(--border-light)', borderRadius: 14, overflow: 'hidden' }}>{children}</div>
        </div>
    );
}
function Header({ title, onClose }) {
    return (
        <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', background: 'var(--bg-card2)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--cyan)', fontFamily: 'var(--fmono)' }}>{title}</span>
            <button onClick={onClose} style={{ width: 28, height: 28, borderRadius: 6, background: 'var(--bg-hover)', border: '1px solid var(--border)', color: 'var(--text2)', cursor: 'pointer' }}>✕</button>
        </div>
    );
}
function Field({ label, children }) {
    return <div><div style={{ fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 4 }}>{label}</div>{children}</div>;
}
function Row({ k, children }) {
    return <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '5px 0', borderBottom: '1px solid var(--border)', fontSize: 12 }}><span style={{ color: 'var(--text3)' }}>{k}</span><span style={{ color: 'var(--text)', fontFamily: 'var(--fmono)' }}>{children}</span></div>;
}
