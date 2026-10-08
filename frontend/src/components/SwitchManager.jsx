import { useState, useEffect, useCallback } from 'react';
import {
    fetchNetworks, checkNetworkSetup, addNetworkPool, removeNetworkPool, createNetwork, updateNetwork, deleteNetwork,
    fetchNetworkVms,
} from '../api';
import { tNodes, useT } from '../i18n';

// Switch (jaringan) CCD: jaringan terisolasi dengan subnet sendiri di setiap Proxmox. Setiap switch berada
// di salah satu blok alamat Proxmox-nya. Blok diiklankan lewat Tailscale oleh ccd-net-setup.sh, jadi switch
// baru di blok yang sudah ada langsung terjangkau dari dashboard; blok baru butuh skrip itu dijalankan ulang.
// Isolasi dipasang di host oleh skrip yang sama. Dibuka dari tab Topology.

const detail = (e) => e?.response?.data?.detail || e?.message || 'Gagal';

const card = { background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 10, padding: 16, marginBottom: 16 };
const small = { padding: '4px 10px', fontSize: 11, borderRadius: 5, cursor: 'pointer', background: 'transparent', border: '1px solid var(--border)', color: 'var(--text2)' };
const input = { padding: '6px 9px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--bg)', color: 'var(--text)', fontSize: 12, minWidth: 0, boxSizing: 'border-box' };
const mono = { fontFamily: 'var(--fmono)' };
const sectionTitle = { fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.08em', margin: '14px 0 8px' };

// Alamat IPv4 dihitung sebagai angka biasa (bukan operasi bit) supaya tidak terkena batas bilangan bertanda 32-bit.
function parseCidr(text) {
    const m = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\/(\d{1,2})$/.exec((text || '').trim());
    if (!m) return null;
    const octets = m.slice(1, 5).map(Number);
    const prefix = Number(m[5]);
    if (octets.some(o => o > 255) || prefix > 32) return null;
    return { addr: octets.reduce((a, o) => a * 256 + o, 0), prefix };
}
const inBlock = (block, net) => {
    const size = 2 ** (32 - block.prefix);
    return net.prefix >= block.prefix && Math.floor(net.addr / size) === Math.floor(block.addr / size);
};

function Item({ ok, pending, children }) {
    const color = pending ? 'var(--text3)' : ok ? 'var(--green)' : 'var(--yellow)';
    return (
        <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', fontSize: 12, color: 'var(--text2)', lineHeight: 1.5, marginBottom: 4 }}>
            <span style={{ color, width: 14, flexShrink: 0, textAlign: 'center' }}>{pending ? '…' : ok ? '✓' : '✗'}</span>
            <span>{children}</span>
        </div>
    );
}

function CopyBlock({ text }) {
    const t = useT();
    const [copied, setCopied] = useState(false);
    const copy = async () => {
        try { await navigator.clipboard.writeText(text); setCopied(true); } catch { /* clipboard ditolak browser */ }
    };
    return (
        <div style={{ position: 'relative', margin: '6px 0' }}>
            <pre style={{ ...mono, fontSize: 11, background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 6, padding: '10px 70px 10px 10px', margin: 0, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', color: 'var(--text)' }}>{text}</pre>
            <button onClick={copy} style={{ ...small, position: 'absolute', top: 6, right: 6, background: 'var(--bg-card)' }}>{copied ? t('common.copied') : t('common.copy')}</button>
        </div>
    );
}

function SetupStatus({ inst, check, checking, onRecheck, openSetup }) {
    const t = useT();
    const nets = inst.networks;
    const reach = check?.reachable || {};
    const unreachable = nets.filter(n => reach[n.id] === false);
    const permsOk = check && check.perm_zone && check.perm_apply;
    const pools = inst.pools.map(p => p.cidr);
    // Blok tempat switch yang belum terjangkau berada: route blok itulah yang perlu diiklankan/disetujui.
    const unreachableBlocks = [...new Set(unreachable.map(n => {
        const net = parseCidr(n.cidr);
        return pools.find(p => net && inBlock(parseCidr(p), net)) || n.cidr;
    }))];
    const command = `curl -fsSL ${window.location.origin}/api/v1/networks/setup-script -o ccd-net-setup.sh\nbash ccd-net-setup.sh --pool ${pools.join(',') || '10.x.0.0/16'} --token '${inst.token_id}'`;
    const needsSetup = !!check && (!check.ready || unreachable.length > 0);
    return (
        <div>
            <div style={sectionTitle}>{t('switch.readiness')}</div>
            <Item ok={pools.length > 0}>{tNodes('switch.blocksItem', { list: pools.length ? <b style={mono}>{pools.join(', ')}</b> : t('switch.blocksNone') })}</Item>
            <Item ok={check?.zone_ok} pending={!check}>
                {tNodes('switch.zone', {
                    zone: <span style={mono}>{inst.zone}</span>,
                    state: check && !check.zone_ok ? (check.zone_type ? t('switch.zoneWrongType', { type: check.zone_type }) : t('switch.zoneMissing')) : '',
                })}
            </Item>
            <Item ok={permsOk} pending={!check}>{t('switch.perms')}</Item>
            <Item ok={nets.length > 0 && unreachable.length === 0} pending={!check || nets.length === 0}>
                {nets.length === 0
                    ? t('switch.pathPending')
                    : unreachable.length === 0
                        ? t('switch.pathOk')
                        : t('switch.pathBad', { n: unreachable.length, routes: unreachableBlocks.join(', ') })}
            </Item>
            {check?.error && <div role="alert" style={{ fontSize: 11, color: 'var(--red)', margin: '4px 0' }}>⚠ {check.error}</div>}
            <button onClick={onRecheck} disabled={checking} style={{ ...small, marginTop: 4 }}>{checking ? t('switch.checking') : t('switch.recheck')}</button>

            <details open={needsSetup || openSetup} style={{ marginTop: 10 }}>
                <summary style={{ fontSize: 12, color: 'var(--cyan)', cursor: 'pointer' }}>{t('switch.setupTitle')}</summary>
                <div style={{ fontSize: 12, color: 'var(--text2)', lineHeight: 1.6, marginTop: 6 }}>
                    <div>{t('switch.setup1')}</div>
                    <div>{t('switch.setup2')}</div>
                    <CopyBlock text={command} />
                    <div>{t('switch.setupWhat')}</div>
                    <div>{tNodes('switch.setup3', { auto: <span style={mono}>autoApprovers</span> })}</div>
                </div>
            </details>
        </div>
    );
}

function SwitchRow({ net, reachable, onChanged }) {
    const t = useT();
    const [mode, setMode] = useState(null);       // null | 'vms' | 'edit' | 'delete'
    const [vms, setVms] = useState(null);
    const [name, setName] = useState(net.name);
    const [snat, setSnat] = useState(net.snat);
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState('');

    const open = (m) => {
        setErr('');
        setMode(mode === m ? null : m);
        if (m === 'vms' && mode !== 'vms') {
            setVms(null);
            fetchNetworkVms(net.id).then(setVms).catch(e => { setVms([]); setErr(detail(e)); });
        }
        if (m === 'edit') { setName(net.name); setSnat(net.snat); }
    };

    const run = async (fn) => {
        setBusy(true); setErr('');
        try { await fn(); setMode(null); onChanged(); } catch (e) { setErr(detail(e)); } finally { setBusy(false); }
    };

    return (
        <div style={{ padding: '10px 12px', borderBottom: '1px solid var(--border)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <div style={{ minWidth: 0, flex: '1 1 220px' }}>
                    <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)', overflowWrap: 'anywhere' }}>
                        {net.name} <span style={{ ...mono, fontSize: 10, fontWeight: 400, color: 'var(--text3)' }}>{net.vnet}</span>
                    </div>
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', fontSize: 11, color: 'var(--text3)', marginTop: 3 }}>
                        <span style={mono}>{net.cidr}</span>
                        <span>{tNodes('switch.gateway', { ip: <span style={mono}>{net.gateway}</span> })}</span>
                        <span style={{ color: net.snat ? 'var(--green)' : 'var(--text3)' }}>{net.snat ? t('switch.nat') : t('switch.noInternet')}</span>
                        {reachable !== undefined && (
                            <span style={{ color: reachable ? 'var(--green)' : 'var(--yellow)' }}>{reachable ? t('switch.reachable') : t('switch.unreachable')}</span>
                        )}
                    </div>
                </div>
                <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
                    <button onClick={() => open('vms')} style={small}>{t('switch.vms')}</button>
                    <button onClick={() => open('edit')} style={small}>{t('common.edit')}</button>
                    <button onClick={() => open('delete')} style={{ ...small, borderColor: 'var(--red)', color: 'var(--red)' }}>{t('common.delete')}</button>
                </div>
            </div>

            {mode === 'vms' && (
                <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 8 }}>
                    {vms === null ? t('common.loading') : vms.length === 0 ? t('switch.noVms')
                        : vms.map(v => <div key={v.vmid} style={mono}>{v.name} <span style={{ color: 'var(--text3)' }}>({v.vmid} · {v.node})</span></div>)}
                </div>
            )}
            {mode === 'edit' && (
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginTop: 8 }}>
                    <input value={name} onChange={e => setName(e.target.value)} maxLength={40} aria-label={t('switch.name')} style={{ ...input, flex: '1 1 180px' }} />
                    <label style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 12, color: 'var(--text2)' }}>
                        <input type="checkbox" checked={snat} onChange={e => setSnat(e.target.checked)} /> {t('switch.natToggle')}
                    </label>
                    <button disabled={busy} onClick={() => run(() => updateNetwork(net.id, { name, snat }))} style={{ ...small, borderColor: 'var(--cyan)', color: 'var(--cyan)' }}>{busy ? t('common.saving') : t('common.save')}</button>
                    <button disabled={busy} onClick={() => setMode(null)} style={small}>{t('common.cancel')}</button>
                </div>
            )}
            {mode === 'delete' && (
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginTop: 8, fontSize: 12, color: 'var(--text2)' }}>
                    <span>{t('switch.deleteMsg', { name: net.name })}</span>
                    <button disabled={busy} onClick={() => run(() => deleteNetwork(net.id))} style={{ ...small, borderColor: 'var(--red)', color: 'var(--red)' }}>{busy ? t('common.deleting') : t('switch.delete')}</button>
                    <button disabled={busy} onClick={() => setMode(null)} style={small}>{t('common.cancel')}</button>
                </div>
            )}
            {err && <div role="alert" style={{ fontSize: 11, color: 'var(--red)', marginTop: 6 }}>⚠ {err}</div>}
        </div>
    );
}

function CreateSwitch({ inst, onCreated }) {
    const t = useT();
    const [name, setName] = useState('');
    const [cidr, setCidr] = useState('');
    const [snat, setSnat] = useState(true);
    const [addPool, setAddPool] = useState(false);
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState('');
    const net = parseCidr(cidr);
    const outside = !!net && !inst.pools.some(p => inBlock(parseCidr(p.cidr), net));
    const noRoom = !cidr.trim() && (inst.pools.length === 0 || !inst.suggested_cidr);
    const blocked = !name.trim() || noRoom || (outside && !addPool);
    const submit = async (e) => {
        e.preventDefault();
        setBusy(true); setErr('');
        try {
            const res = await createNetwork({ instance: inst.label, name: name.trim(), cidr: cidr.trim() || null, snat, add_pool: outside && addPool });
            setName(''); setCidr(''); setSnat(true); setAddPool(false);
            onCreated(res.pool_added);
        } catch (e2) {
            setErr(detail(e2));
        } finally {
            setBusy(false);
        }
    };
    return (
        <form onSubmit={submit}>
            <div style={sectionTitle}>{t('switch.create')}</div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                <input value={name} onChange={e => setName(e.target.value)} placeholder={t('switch.newNamePh')} maxLength={40} required aria-label={t('switch.newName')} style={{ ...input, flex: '2 1 200px' }} />
                <input value={cidr} onChange={e => setCidr(e.target.value)} placeholder={inst.suggested_cidr ? t('switch.subnetAuto', { cidr: inst.suggested_cidr }) : t('switch.subnetPh')} aria-label={t('switch.subnet')} style={{ ...input, ...mono, flex: '1 1 170px' }} />
                <button type="submit" disabled={busy || blocked}
                    style={{ ...small, background: 'var(--cyan)', color: '#000', border: 'none', fontWeight: 600, padding: '6px 14px', opacity: busy || blocked ? 0.6 : 1 }}>
                    {busy ? t('switch.creating') : t('switch.create')}
                </button>
            </div>
            <label style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 12, color: 'var(--text2)', marginTop: 8 }}>
                <input type="checkbox" checked={snat} onChange={e => setSnat(e.target.checked)} /> {t('switch.natHost')}
            </label>
            {outside && (
                <div style={{ marginTop: 8, padding: '8px 10px', borderRadius: 6, border: '1px solid var(--yellow)55', background: 'var(--yellow-glow)' }}>
                    <label style={{ display: 'flex', gap: 6, alignItems: 'flex-start', fontSize: 12, color: 'var(--text)' }}>
                        <input type="checkbox" checked={addPool} onChange={e => setAddPool(e.target.checked)} style={{ marginTop: 2 }} />
                        <span>{tNodes('switch.addAsBlock', { cidr: <b style={mono}>{cidr.trim()}</b> })}</span>
                    </label>
                    <div style={{ fontSize: 11, color: 'var(--text2)', lineHeight: 1.5, marginTop: 4, paddingLeft: 20 }}>
                        {t('switch.addAsBlockHint')}
                    </div>
                </div>
            )}
            {busy && <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 6 }}>{t('switch.applying')}</div>}
            {noRoom && (
                <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 6 }}>
                    {inst.pools.length === 0
                        ? t('switch.noBlocks')
                        : t('switch.allFull')}
                </div>
            )}
            {err && <div role="alert" style={{ fontSize: 11, color: 'var(--red)', marginTop: 6 }}>⚠ {err}</div>}
        </form>
    );
}

function PoolList({ inst, onAdded, onRemoved }) {
    const t = useT();
    const [cidr, setCidr] = useState('');
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState('');
    const run = async (fn) => {
        setBusy(true); setErr('');
        try { await fn(); } catch (e) { setErr(detail(e)); } finally { setBusy(false); }
    };
    const add = (e) => {
        e.preventDefault();
        run(async () => { const r = await addNetworkPool(inst.label, cidr.trim()); setCidr(''); onAdded(r.added, r.replaced); });
    };
    return (
        <div>
            <div style={sectionTitle}>{t('switch.blocks')}</div>
            {inst.pools.length === 0 && <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 6 }}>{t('switch.noBlocksShort')}</div>}
            {inst.pools.map(p => (
                <div key={p.cidr} style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', fontSize: 12, marginBottom: 4 }}>
                    <b style={{ ...mono, color: 'var(--text)' }}>{p.cidr}</b>
                    <span style={{ color: 'var(--text3)' }}>{t('switch.blockUsage', { n: p.switches })}{p.full ? t('switch.blockFull') : ''}</span>
                    <button disabled={busy || p.switches > 0} onClick={() => run(async () => { await removeNetworkPool(inst.label, p.cidr); onRemoved(p.cidr); })}
                        title={p.switches > 0 ? t('switch.blockInUse') : t('switch.blockDelete', { cidr: p.cidr })} aria-label={t('switch.blockDelete', { cidr: p.cidr })}
                        style={{ ...small, padding: '1px 8px', opacity: p.switches > 0 ? 0.4 : 1 }}>{t('common.delete')}</button>
                </div>
            ))}
            <form onSubmit={add} style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 6 }}>
                <input value={cidr} onChange={e => setCidr(e.target.value)} placeholder={t('switch.blockPh')} aria-label={t('switch.blockNew', { label: inst.label })} style={{ ...input, ...mono, width: 170 }} />
                <button type="submit" disabled={busy || !cidr.trim()} style={small}>{busy ? t('common.saving') : t('switch.blockAdd')}</button>
            </form>
            {err && <div role="alert" style={{ fontSize: 11, color: 'var(--red)', marginTop: 6 }}>⚠ {err}</div>}
        </div>
    );
}

function InstanceCard({ inst, onChanged }) {
    const t = useT();
    const [check, setCheck] = useState(null);
    const [checking, setChecking] = useState(false);
    const [hostNotice, setHostNotice] = useState(null);   // { key, vars }: langkah di host setelah blok ditambah/dihapus
    const netCount = inst.networks.length;

    const recheck = useCallback(() => {
        setChecking(true);
        checkNetworkSetup(inst.label)
            .then(setCheck)
            .catch(e => setCheck({ error: detail(e) }))
            .finally(() => setChecking(false));
    }, [inst.label]);

    // Diperiksa saat kartu dibuka dan setiap jumlah switch berubah (jalur ke switch baru ikut diuji).
    useEffect(() => {
        let alive = true;
        checkNetworkSetup(inst.label)
            .then(c => { if (alive) setCheck(c); })
            .catch(e => { if (alive) setCheck({ error: detail(e) }); });
        return () => { alive = false; };
    }, [inst.label, netCount]);

    const blockAdded = (cidr, replaced = []) => {
        if (cidr) {
            setHostNotice({ key: 'switch.noticeAdded', vars: { cidr, replaced: replaced.length ? t('switch.noticeReplaced', { list: replaced.join(', ') }) : '' } });
        }
        onChanged();
    };
    const blockRemoved = (cidr) => {
        setHostNotice({ key: 'switch.noticeRemoved', vars: { cidr } });
        onChanged();
    };

    return (
        <div style={card}>
            <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--text)', marginBottom: 2 }}>{inst.label}</div>

            <PoolList inst={inst} onAdded={blockAdded} onRemoved={blockRemoved} />
            {hostNotice && (
                <div role="status" style={{ marginTop: 10, padding: '8px 10px', borderRadius: 6, border: '1px solid var(--yellow)55', background: 'var(--yellow-glow)', fontSize: 12, color: 'var(--text)', lineHeight: 1.5, display: 'flex', gap: 8 }}>
                    <span style={{ flex: 1 }}>⚠ {t(hostNotice.key, hostNotice.vars)}</span>
                    <button onClick={() => setHostNotice(null)} aria-label={t('switch.noticeClose')} style={{ ...small, border: 'none', padding: '0 4px', fontSize: 14 }}>×</button>
                </div>
            )}

            <SetupStatus inst={inst} check={check} checking={checking} onRecheck={recheck} openSetup={!!hostNotice} />

            <div style={sectionTitle}>{t('switch.list', { n: netCount })}</div>
            <div style={{ border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
                {netCount === 0
                    ? <div style={{ padding: 12, fontSize: 12, color: 'var(--text3)' }}>{t('switch.none')}</div>
                    : inst.networks.map(n => <SwitchRow key={n.id} net={n} reachable={check?.reachable?.[n.id]} onChanged={onChanged} />)}
            </div>

            <CreateSwitch inst={inst} onCreated={(added) => blockAdded(added)} />
        </div>
    );
}

// onChanged dipanggil setelah blok alamat atau switch berubah, supaya Topology bisa digambar ulang.
export default function SwitchManager({ onChanged }) {
    const t = useT();
    const [data, setData] = useState(null);
    const [err, setErr] = useState('');
    const load = useCallback(() => fetchNetworks().then(d => { setData(d); setErr(''); }).catch(e => setErr(detail(e))), []);
    useEffect(() => { load(); }, [load]);
    const changed = () => { load(); onChanged?.(); };

    return (
        <div>
            <div style={{ fontSize: 12, color: 'var(--text2)', lineHeight: 1.6, marginBottom: 14 }}>
                {t('switch.intro')}
            </div>
            {err && <div role="alert" style={{ fontSize: 12, color: 'var(--red)', marginBottom: 12 }}>⚠ {err}</div>}
            {data === null && !err && <div style={{ fontSize: 12, color: 'var(--text3)' }}>{t('common.loading')}</div>}
            {data?.instances.length === 0 && <div style={{ fontSize: 12, color: 'var(--text3)' }}>{t('switch.noInstances')}</div>}
            {data?.instances.map(inst => <InstanceCard key={inst.label} inst={inst} onChanged={changed} />)}
        </div>
    );
}
