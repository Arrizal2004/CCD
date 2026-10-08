import { useState, useEffect, useRef } from 'react';
import { fetchProxmoxTemplates, createProxmoxVm, fetchNetworks, fetchProxmoxInstances, fetchProxmoxNodes } from '../api';
import { useSysConfig } from '../sysconfig';
import { useT } from '../i18n';

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

// Template yang namanya paling cocok dengan OS yang diminta (mis. "Ubuntu" → "Template-Ubuntu").
function pickTemplate(list, os) {
    const words = String(os || '').toLowerCase().split(/[^a-z0-9]+/).filter(w => w.length >= 3);
    if (/windows/i.test(os || '')) words.push('win');
    const score = tp => words.filter(w => tp.name.toLowerCase().includes(w)).length;
    return [...list].sort((a, b) => score(b) - score(a))[0];
}

// Isian form dari template; spek dari permintaan mahasiswa didahulukan, disk tidak lebih kecil dari template.
function templateFields(tpl, initial) {
    return {
        template_vmid: tpl ? String(tpl.vmid) : '',
        cores: String(initial?.cores || tpl?.cores || ''),
        memory_mb: String(initial?.memory_mb || tpl?.memory_mb || ''),
        disk_gb: String(Math.max(initial?.disk_gb || 0, tpl?.disk_gb || 0) || ''),
        bridge: tpl?.bridge || '',
    };
}

// instance/node kosong (dibuka dari request VPS): admin memilih Proxmox-nya di form.
// initial: nilai awal dari permintaan mahasiswa {name, username, password, cores, memory_mb, disk_gb, os, note}.
export default function CreateVmModal({ instance, node, initial = null, onClose, onCreated }) {
    const t = useT();
    const net = loadNet();
    const { default_vm_lease_days: defaultLease } = useSysConfig();
    const [templates, setTemplates] = useState(null);
    const [target, setTarget] = useState(instance && node ? { instance, node } : null);
    const [targets, setTargets] = useState(null);   // pilihan Proxmox/node saat instance tidak diberikan
    const [form, setForm] = useState({
        template_vmid: '', name: initial?.name || '', cores: '', memory_mb: '', disk_gb: '', bridge: '',
        username: initial?.username || '', password: initial?.password || '', ip_mode: 'static',
        ip_cidr: net.prefix ? `/${net.prefix}` : '', gateway: net.gateway || '', dns: net.dns || '',
        full_clone: false, start: true, lease_days: defaultLease ? String(defaultLease) : '',
        network_id: '',
    });
    const [switches, setSwitches] = useState([]);   // switch CCD di Proxmox ini
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState(null);
    const [result, setResult] = useState(null);
    const set = (k) => (e) => setForm(f => ({ ...f, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value }));

    const initialRef = useRef(initial);          // nilai awal tetap selama jendela terbuka
    const applyTemplate = (tpl) => setForm(f => ({ ...f, ...templateFields(tpl, initial) }));

    useEffect(() => {
        if (instance && node) return undefined;
        let alive = true;
        (async () => {
            const list = [];
            for (const inst of await fetchProxmoxInstances()) {
                for (const n of await fetchProxmoxNodes(inst.label).catch(() => [])) list.push({ instance: inst.label, node: n.node });
            }
            if (!alive) return;
            setTargets(list);
            setTarget(cur => cur || list[0] || null);
        })().catch(e => { if (alive) { setTargets([]); setError(e?.response?.data?.detail || t('create.loadProxmoxFailed')); } });
        return () => { alive = false; };
    }, [instance, node, t]);

    useEffect(() => {
        if (!target) return undefined;
        let alive = true;
        fetchNetworks()
            .then(d => { if (alive) setSwitches(d.instances.find(i => i.label === target.instance)?.networks || []); })
            .catch(() => {});
        return () => { alive = false; };
    }, [target]);

    useEffect(() => {
        if (!target) return undefined;
        let alive = true;
        fetchProxmoxTemplates(target.instance, target.node)
            .then(list => {
                if (!alive) return;
                setTemplates(list);
                const init = initialRef.current;
                setForm(f => ({ ...f, ...templateFields(list.length ? pickTemplate(list, init?.os) : null, init) }));
            })
            .catch(e => { if (alive) { setTemplates([]); setError(e?.response?.data?.detail || t('create.loadTemplatesFailed')); } });
        return () => { alive = false; };
    }, [target, t]);

    const tpl = templates?.find(tp => String(tp.vmid) === form.template_vmid);
    // Di switch CCD: bridge, gateway, dan DNS bawaan dari switch; IP kosong = dibagikan otomatis.
    const sw = switches.find(n => String(n.id) === form.network_id);

    const submit = async (e) => {
        e.preventDefault();
        setBusy(true);
        setError(null);
        const num = (v) => (v === '' ? null : Number(v));
        try {
            const network = sw ? {
                network_id: sw.id, ip_mode: 'static', ip_cidr: form.ip_cidr.trim().replace(/^\/\d+$/, '') || null,
                gateway: null, bridge: null,
            } : {
                ip_mode: form.ip_mode,
                ip_cidr: form.ip_mode === 'static' ? form.ip_cidr.trim() : null,
                gateway: form.ip_mode === 'static' ? form.gateway.trim() : null,
                bridge: form.bridge.trim() || null,
            };
            const res = await createProxmoxVm(target.instance, target.node, {
                template_vmid: Number(form.template_vmid), name: form.name.trim(),
                username: form.username.trim(), password: form.password,
                ...network,
                dns: form.dns.trim() || null,
                cores: num(form.cores), memory_mb: num(form.memory_mb), disk_gb: num(form.disk_gb),
                full_clone: form.full_clone, start: form.start, lease_days: num(form.lease_days),
            });
            if (!sw) {
                try {
                    localStorage.setItem(NET_KEY, JSON.stringify({
                        gateway: form.gateway.trim(), dns: form.dns.trim(), prefix: form.ip_cidr.split('/')[1] || '',
                    }));
                } catch { /* remembered settings are optional */ }
            }
            setResult(res);
            onCreated?.(res, target);
        } catch (err) {
            setError(err?.response?.data?.detail || t('create.failed'));
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
                        <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text)' }}>{t('create.title')}</div>
                        <div style={{ fontSize: 11, color: 'var(--text3)' }}>{target ? `${target.instance}/${target.node}` : t('create.pickProxmox')} · clone + cloud-init</div>
                        {initial?.note && <div style={{ fontSize: 11, color: 'var(--cyan)', marginTop: 2 }}>{initial.note}</div>}
                    </div>
                    <button onClick={onClose} disabled={busy} aria-label={t('common.close')} style={{ background: 'transparent', border: 'none', color: 'var(--text3)', fontSize: 18, cursor: 'pointer' }}>×</button>
                </div>

                {error && (
                    <div style={{ background: 'var(--red-glow)', border: '1px solid var(--red)', borderRadius: 6, padding: '6px 10px', color: 'var(--red)', fontSize: 11, marginTop: 12 }}>⚠ {error}</div>
                )}

                {targets && !result && (
                    <div style={{ marginTop: 12 }}>
                        <Field title={t('create.target')}>
                            <select value={target ? `${target.instance}|${target.node}` : ''} style={input}
                                onChange={e => {
                                    const [i, n] = e.target.value.split('|');
                                    setTemplates(null);
                                    setTarget({ instance: i, node: n });
                                    setForm(f => ({ ...f, network_id: '' }));
                                }}>
                                {targets.map(tg => <option key={`${tg.instance}|${tg.node}`} value={`${tg.instance}|${tg.node}`}>{tg.instance} / {tg.node}</option>)}
                            </select>
                        </Field>
                    </div>
                )}

                {result ? (
                    <div style={{ marginTop: 14, fontSize: 12, color: 'var(--text)', lineHeight: 1.7 }}>
                        <div style={{ color: '#4ade80', fontWeight: 600 }}>{t('create.done', { vmid: result.vmid, name: result.name, clone: result.clone })}</div>
                        <div>IP: <span style={{ fontFamily: 'var(--fmono)' }}>{result.static_ip || result.agent_ip || t('create.ipUnknown')}</span>
                            {result.switch && <span style={{ color: 'var(--text3)' }}>{t('create.onSwitch', { name: result.switch })}</span>}
                            {result.agent_ip && <span style={{ color: 'var(--text3)' }}>{t('create.agentOn')}</span>}</div>
                        <div style={{ color: 'var(--text3)' }}>
                            {result.connect_ready
                                ? t('create.connectReady')
                                : t('create.connectLater')}
                        </div>
                        <button onClick={onClose} style={{ marginTop: 12, padding: '6px 16px', fontSize: 12, borderRadius: 6, cursor: 'pointer', background: 'var(--cyan)', color: '#000', border: 'none', fontWeight: 600 }}>{t('common.close')}</button>
                    </div>
                ) : !target ? (
                    <div style={{ color: 'var(--text3)', fontSize: 12, padding: '14px 0' }}>{targets?.length === 0 ? t('create.noProxmox') : t('create.loadingProxmox')}</div>
                ) : templates === null ? (
                    <div style={{ color: 'var(--text3)', fontSize: 12, padding: '14px 0' }}>{t('create.loadingTemplates')}</div>
                ) : templates.length === 0 ? (
                    <div style={{ color: 'var(--text3)', fontSize: 12, padding: '14px 0', lineHeight: 1.6 }}>
                        {t('create.noTemplates')}
                    </div>
                ) : (
                    <form onSubmit={submit}>
                        <div style={section}>{t('create.sectionVm')}</div>
                        <div style={grid2}>
                            <Field title={t('create.template')}>
                                <select value={form.template_vmid} style={input}
                                    onChange={e => applyTemplate(templates.find(tp => String(tp.vmid) === e.target.value))}>
                                    {templates.map(tp => <option key={tp.vmid} value={tp.vmid}>{tp.name} ({tp.vmid})</option>)}
                                </select>
                            </Field>
                            <Field title={t('create.name')}>
                                <input value={form.name} onChange={set('name')} required placeholder="lab-ubuntu-01" style={input} />
                            </Field>
                        </div>
                        {tpl && !tpl.cloudinit && (
                            <div style={{ fontSize: 11, color: '#f0c040', marginTop: 6 }}>{t('create.noCloudInit')}</div>
                        )}
                        <div style={{ ...grid3, marginTop: 10 }}>
                            <Field title={t('create.cpu')}><input type="number" min="1" max="64" value={form.cores} onChange={set('cores')} style={input} /></Field>
                            <Field title={t('create.ram')}><input type="number" min="256" step="256" value={form.memory_mb} onChange={set('memory_mb')} style={input} /></Field>
                            <Field title={t('create.disk', { min: tpl?.disk_gb ?? '—' })}><input type="number" min={tpl?.disk_gb || 1} value={form.disk_gb} onChange={set('disk_gb')} style={input} /></Field>
                        </div>

                        <div style={section}>{t('create.sectionAccount')}</div>
                        <div style={grid2}>
                            <Field title={t('create.username')}><input value={form.username} onChange={set('username')} required placeholder="student" autoComplete="off" style={input} /></Field>
                            <Field title={t('create.password')}><input type={initial?.password ? 'text' : 'password'} value={form.password} onChange={set('password')} required autoComplete="new-password" style={input} /></Field>
                        </div>

                        <div style={section}>{t('create.sectionNetwork')}</div>
                        {switches.length > 0 && (
                            <div style={{ marginBottom: 10 }}>
                                <Field title={t('create.connectTo')}>
                                    <select value={form.network_id} onChange={set('network_id')} style={input}>
                                        <option value="">{t('create.bridgeManual')}</option>
                                        {switches.map(n => <option key={n.id} value={n.id}>{t('create.switchOption', { name: n.name, cidr: n.cidr })}</option>)}
                                    </select>
                                </Field>
                            </div>
                        )}
                        {sw ? (
                            <>
                                <Field title={t('create.switchIp', { cidr: sw.cidr })}>
                                    <input value={form.ip_cidr.replace(/^\/\d+$/, '')} onChange={set('ip_cidr')} placeholder={t('create.auto')} style={input} />
                                </Field>
                                <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 6, lineHeight: 1.5 }}>
                                    {t('create.switchInfo', { gw: sw.gateway, net: sw.snat ? t('create.natOn') : t('create.natOff') })}
                                </div>
                            </>
                        ) : (
                            <>
                                <div style={grid2}>
                                    <Field title={t('create.bridge')}><input value={form.bridge} onChange={set('bridge')} placeholder="vmbr0" style={input} /></Field>
                                    <Field title={t('create.ipMode')}>
                                        <select value={form.ip_mode} onChange={set('ip_mode')} style={input}>
                                            <option value="static">{t('create.static')}</option>
                                            <option value="dhcp">DHCP</option>
                                        </select>
                                    </Field>
                                </div>
                                {form.ip_mode === 'static' && (
                                    <div style={{ ...grid2, marginTop: 10 }}>
                                        <Field title={t('create.ipPrefix')}><input value={form.ip_cidr} onChange={set('ip_cidr')} required placeholder="192.168.1.50/24" style={input} /></Field>
                                        <Field title={t('create.gateway')}><input value={form.gateway} onChange={set('gateway')} required placeholder="192.168.1.1" style={input} /></Field>
                                    </div>
                                )}
                            </>
                        )}
                        <div style={{ marginTop: 10 }}>
                            <Field title={sw ? t('create.dnsSwitch', { gw: sw.gateway }) : t('create.dns')}>
                                <input value={form.dns} onChange={set('dns')} placeholder={sw ? sw.gateway : '10.13.10.13'} style={input} />
                            </Field>
                        </div>

                        <div style={section}>{t('create.sectionOptions')}</div>
                        <div style={{ marginBottom: 10, maxWidth: 260 }}>
                            <Field title={t('create.lease')}>
                                <input type="number" min="1" max="3650" value={form.lease_days} onChange={set('lease_days')} placeholder={t('create.leasePh')} style={input} />
                            </Field>
                        </div>
                        <label style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 12, color: 'var(--text2)' }}>
                            <input type="checkbox" checked={form.full_clone} onChange={set('full_clone')} />
                            {t('create.fullClone')}
                        </label>
                        <label style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 12, color: 'var(--text2)', marginTop: 6 }}>
                            <input type="checkbox" checked={form.start} onChange={set('start')} />
                            {t('create.start')}
                        </label>

                        <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 16 }}>
                            <button type="submit" disabled={busy || !form.template_vmid}
                                style={{ padding: '7px 18px', fontSize: 12, borderRadius: 6, cursor: 'pointer', background: 'var(--cyan)', color: '#000', border: 'none', fontWeight: 600, opacity: busy ? 0.6 : 1 }}>
                                {busy ? t('create.creating') : t('create.submit')}
                            </button>
                            {busy && <span style={{ fontSize: 11, color: 'var(--text3)' }}>{t('create.progress')}</span>}
                        </div>
                    </form>
                )}
            </div>
        </div>
    );
}
