import { useState, useEffect } from 'react';
import { fetchProxmoxTemplates, createProxmoxVm } from '../api';

const NET_KEY = 'ccd_create_vm_net';   // last gateway/DNS/prefix — convenience only

const input = { width: '100%', boxSizing: 'border-box', background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 6, padding: '6px 10px', color: 'var(--text)', fontSize: 12 };
const label = { fontSize: 10, color: 'var(--text3)', marginBottom: 2, display: 'block' };
const section = { fontSize: 10, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '0.08em', fontWeight: 600, margin: '14px 0 8px' };
const grid2 = { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 };
const grid3 = { display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10 };

function loadNet() {
    try { return JSON.parse(localStorage.getItem(NET_KEY)) || {}; } catch { return {}; }
}

function Field({ title, children }) {
    return <label style={{ display: 'block' }}><span style={label}>{title}</span>{children}</label>;
}

export default function CreateVmModal({ instance, node, onClose, onCreated }) {
    const net = loadNet();
    const [templates, setTemplates] = useState(null);
    const [form, setForm] = useState({
        template_vmid: '', name: '', cores: '', memory_mb: '', disk_gb: '', bridge: '',
        username: '', password: '', ip_mode: 'static',
        ip_cidr: net.prefix ? `/${net.prefix}` : '', gateway: net.gateway || '', dns: net.dns || '',
        full_clone: false, start: true,
    });
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState(null);
    const [result, setResult] = useState(null);
    const set = (k) => (e) => setForm(f => ({ ...f, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value }));

    const applyTemplate = (tpl) => setForm(f => ({
        ...f, template_vmid: tpl ? String(tpl.vmid) : '',
        cores: tpl ? String(tpl.cores) : '', memory_mb: tpl ? String(tpl.memory_mb) : '',
        disk_gb: tpl?.disk_gb ? String(tpl.disk_gb) : '', bridge: tpl?.bridge || '',
    }));

    useEffect(() => {
        fetchProxmoxTemplates(instance, node)
            .then(list => { setTemplates(list); applyTemplate(list[0]); })
            .catch(e => { setTemplates([]); setError(e?.response?.data?.detail || 'Gagal memuat daftar template'); });
    }, [instance, node]);

    const tpl = templates?.find(t => String(t.vmid) === form.template_vmid);

    const submit = async (e) => {
        e.preventDefault();
        setBusy(true);
        setError(null);
        const num = (v) => (v === '' ? null : Number(v));
        try {
            const res = await createProxmoxVm(instance, node, {
                template_vmid: Number(form.template_vmid), name: form.name.trim(),
                username: form.username.trim(), password: form.password,
                ip_mode: form.ip_mode,
                ip_cidr: form.ip_mode === 'static' ? form.ip_cidr.trim() : null,
                gateway: form.ip_mode === 'static' ? form.gateway.trim() : null,
                dns: form.dns.trim() || null,
                cores: num(form.cores), memory_mb: num(form.memory_mb), disk_gb: num(form.disk_gb),
                bridge: form.bridge.trim() || null,
                full_clone: form.full_clone, start: form.start,
            });
            try {
                localStorage.setItem(NET_KEY, JSON.stringify({
                    gateway: form.gateway.trim(), dns: form.dns.trim(), prefix: form.ip_cidr.split('/')[1] || '',
                }));
            } catch { /* remembered settings are optional */ }
            setResult(res);
            onCreated?.();
        } catch (err) {
            setError(err?.response?.data?.detail || 'Gagal membuat VM');
        } finally {
            setBusy(false);
        }
    };

    return (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 210 }}
            onClick={busy ? undefined : onClose}>
            <div style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', borderRadius: 10, width: 560, maxWidth: '94vw', maxHeight: '90vh', overflow: 'auto', padding: 20 }}
                onClick={e => e.stopPropagation()}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <div>
                        <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text)' }}>Create VM dari Template</div>
                        <div style={{ fontSize: 11, color: 'var(--text3)' }}>{instance}/{node} · clone + cloud-init</div>
                    </div>
                    <button onClick={onClose} disabled={busy} style={{ background: 'transparent', border: 'none', color: 'var(--text3)', fontSize: 18, cursor: 'pointer' }}>×</button>
                </div>

                {error && (
                    <div style={{ background: 'var(--red-glow)', border: '1px solid var(--red)', borderRadius: 6, padding: '6px 10px', color: 'var(--red)', fontSize: 11, marginTop: 12 }}>⚠ {error}</div>
                )}

                {result ? (
                    <div style={{ marginTop: 14, fontSize: 12, color: 'var(--text)', lineHeight: 1.7 }}>
                        <div style={{ color: '#4ade80', fontWeight: 600 }}>✓ VM {result.vmid} “{result.name}” dibuat ({result.clone} clone)</div>
                        <div>IP: <span style={{ fontFamily: 'var(--fmono)' }}>{result.static_ip || result.agent_ip || 'DHCP — belum terdeteksi'}</span>
                            {result.agent_ip && <span style={{ color: 'var(--text3)' }}> · guest agent aktif</span>}</div>
                        <div style={{ color: 'var(--text3)' }}>
                            {result.connect_ready
                                ? 'Kredensial tersimpan — tombol Connect sudah siap dipakai.'
                                : 'Kredensial tersimpan. Isi IP lewat “Atur IP manual” di detail VM setelah IP-nya diketahui.'}
                        </div>
                        <button onClick={onClose} style={{ marginTop: 12, padding: '6px 16px', fontSize: 12, borderRadius: 6, cursor: 'pointer', background: 'var(--cyan)', color: '#000', border: 'none', fontWeight: 600 }}>Tutup</button>
                    </div>
                ) : templates === null ? (
                    <div style={{ color: 'var(--text3)', fontSize: 12, padding: '14px 0' }}>Memuat template…</div>
                ) : templates.length === 0 ? (
                    <div style={{ color: 'var(--text3)', fontSize: 12, padding: '14px 0', lineHeight: 1.6 }}>
                        Belum ada template di node ini. Buat VM, generalisasi, tambahkan CloudInit Drive, lalu “Convert to template” di Proxmox (di pool yang sama).
                    </div>
                ) : (
                    <form onSubmit={submit}>
                        <div style={section}>VM</div>
                        <div style={grid2}>
                            <Field title="Template">
                                <select value={form.template_vmid} style={input}
                                    onChange={e => applyTemplate(templates.find(t => String(t.vmid) === e.target.value))}>
                                    {templates.map(t => <option key={t.vmid} value={t.vmid}>{t.name} ({t.vmid})</option>)}
                                </select>
                            </Field>
                            <Field title="Nama VM / hostname">
                                <input value={form.name} onChange={set('name')} required placeholder="lab-ubuntu-01" style={input} />
                            </Field>
                        </div>
                        {tpl && !tpl.cloudinit && (
                            <div style={{ fontSize: 11, color: '#f0c040', marginTop: 6 }}>⚠ Template ini belum punya CloudInit drive — user/IP tidak akan diterapkan.</div>
                        )}
                        <div style={{ ...grid3, marginTop: 10 }}>
                            <Field title="CPU (core)"><input type="number" min="1" max="64" value={form.cores} onChange={set('cores')} style={input} /></Field>
                            <Field title="RAM (MB)"><input type="number" min="256" step="256" value={form.memory_mb} onChange={set('memory_mb')} style={input} /></Field>
                            <Field title={`Disk (GB, min ${tpl?.disk_gb ?? '—'})`}><input type="number" min={tpl?.disk_gb || 1} value={form.disk_gb} onChange={set('disk_gb')} style={input} /></Field>
                        </div>

                        <div style={section}>Akun (cloud-init)</div>
                        <div style={grid2}>
                            <Field title="Username"><input value={form.username} onChange={set('username')} required placeholder="student" autoComplete="off" style={input} /></Field>
                            <Field title="Password"><input type="password" value={form.password} onChange={set('password')} required autoComplete="new-password" style={input} /></Field>
                        </div>

                        <div style={section}>Jaringan</div>
                        <div style={grid2}>
                            <Field title="Bridge (vSwitch)"><input value={form.bridge} onChange={set('bridge')} placeholder="vmbr0" style={input} /></Field>
                            <Field title="Mode IP">
                                <select value={form.ip_mode} onChange={set('ip_mode')} style={input}>
                                    <option value="static">Statis</option>
                                    <option value="dhcp">DHCP</option>
                                </select>
                            </Field>
                        </div>
                        {form.ip_mode === 'static' && (
                            <div style={{ ...grid2, marginTop: 10 }}>
                                <Field title="IP / prefix"><input value={form.ip_cidr} onChange={set('ip_cidr')} required placeholder="192.168.1.50/24" style={input} /></Field>
                                <Field title="Gateway"><input value={form.gateway} onChange={set('gateway')} required placeholder="192.168.1.1" style={input} /></Field>
                            </div>
                        )}
                        <div style={{ marginTop: 10 }}>
                            <Field title="DNS (opsional, pisahkan dengan spasi)"><input value={form.dns} onChange={set('dns')} placeholder="10.13.10.13" style={input} /></Field>
                        </div>

                        <div style={section}>Opsi</div>
                        <label style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 12, color: 'var(--text2)' }}>
                            <input type="checkbox" checked={form.full_clone} onChange={set('full_clone')} />
                            Full clone (independen dari template, lebih lambat) — default linked clone
                        </label>
                        <label style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 12, color: 'var(--text2)', marginTop: 6 }}>
                            <input type="checkbox" checked={form.start} onChange={set('start')} />
                            Nyalakan VM setelah dibuat
                        </label>

                        <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 16 }}>
                            <button type="submit" disabled={busy || !form.template_vmid}
                                style={{ padding: '7px 18px', fontSize: 12, borderRadius: 6, cursor: 'pointer', background: 'var(--cyan)', color: '#000', border: 'none', fontWeight: 600, opacity: busy ? 0.6 : 1 }}>
                                {busy ? 'Membuat VM…' : 'Create VM'}
                            </button>
                            {busy && <span style={{ fontSize: 11, color: 'var(--text3)' }}>clone → cloud-init → boot (±1 menit)</span>}
                        </div>
                    </form>
                )}
            </div>
        </div>
    );
}
