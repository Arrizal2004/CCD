import { useState, useEffect, useCallback, useRef } from 'react';
import {
    fetchTailscalePolicy, previewTailscalePolicy, applyTailscalePolicy,
    setTailscaleGateway, setAdminTailscaleLogin, fetchTailscaleDevices,
} from '../api';

const GATEWAY_TAG = 'tag:ccd-gateway';

const input = { boxSizing: 'border-box', background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 6, padding: '5px 9px', color: 'var(--text)', fontSize: 12 };
const card = { background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 6, padding: '8px 10px' };
const heading = { fontSize: 11, fontWeight: 600, color: 'var(--text2)', margin: '18px 0 8px' };
const btn = (color, filled) => ({
    padding: '5px 12px', fontSize: 11, borderRadius: 5, cursor: 'pointer', whiteSpace: 'nowrap',
    background: filled ? color : 'transparent', color: filled ? '#000' : color,
    border: `1px solid ${color}`, fontWeight: filled ? 600 : 400,
});

function Pill({ ok, children }) {
    const c = ok ? '#4ade80' : '#f0c040';
    return (
        <span style={{ fontSize: 10, padding: '2px 8px', borderRadius: 10, color: c, background: c + '18', border: `1px solid ${c}44`, whiteSpace: 'nowrap' }}>
            {ok ? '✓' : '⚠'} {children}
        </span>
    );
}

const errText = (e, fallback) => e?.response?.data?.detail || fallback;

export default function TailscalePolicyPanel() {
    const [status, setStatus] = useState(null);
    const [devices, setDevices] = useState([]);
    const [logins, setLogins] = useState({});
    const [ports, setPorts] = useState('');
    const [enforce, setEnforce] = useState(false);
    const [preview, setPreview] = useState(null);
    const [busy, setBusy] = useState(null);
    const [error, setError] = useState(null);
    const [notice, setNotice] = useState(null);
    const portsInitialised = useRef(false);

    const load = useCallback(async () => {
        try {
            const [st, dev] = await Promise.all([fetchTailscalePolicy(), fetchTailscaleDevices()]);
            setStatus(st);
            setDevices(dev);
            setLogins(Object.fromEntries(st.admins.map(a => [a.id, a.tailscale_login || ''])));
            if (!portsInitialised.current) {
                setPorts(st.member_ports.join(', '));
                portsInitialised.current = true;
            }
            setError(null);
        } catch (e) {
            setError(errText(e, 'Gagal memuat status policy Tailscale'));
        }
    }, []);

    useEffect(() => { load(); }, [load]);

    const memberPorts = () => ports.split(/[\s,]+/).filter(Boolean).map(Number);

    const run = async (key, fn) => {
        setBusy(key);
        setError(null);
        setNotice(null);
        try { await fn(); } catch (e) { setError(errText(e, 'Operasi gagal')); } finally { setBusy(null); }
    };

    const runPreview = () => run('preview', async () => {
        setPreview(null);
        setPreview(await previewTailscalePolicy({ member_ports: memberPorts(), remove_allow_all: enforce }));
    });

    const runApply = () => {
        const msg = enforce
            ? 'Terapkan policy DAN hapus aturan allow-all?\n\nDevice/layanan lain di tailnet ini yang tidak tercakup aturan lain akan kehilangan akses.'
            : 'Terapkan policy ini ke tailnet?';
        if (!confirm(msg)) return;
        run('apply', async () => {
            const res = await applyTailscalePolicy({ member_ports: memberPorts(), remove_allow_all: enforce, etag: preview.etag });
            setStatus(res.status);
            setPreview(null);
            setNotice(res.changes.length ? `Policy diterapkan (${res.changes.length} perubahan).` : 'Tidak ada perubahan untuk diterapkan.');
        });
    };

    const toggleGateway = (d, enabled) => {
        if (enabled && !confirm(`Jadikan "${d.hostname}" gateway (${GATEWAY_TAG})?\n\nDevice bertag tidak lagi dimiliki user, jadi aturan Tailscale SSH "autogroup:self" tidak berlaku lagi untuk device ini.`)) return;
        run(`gw:${d.id}`, async () => {
            await setTailscaleGateway(d.id, enabled);
            setNotice(`${d.hostname}: ${enabled ? 'dijadikan' : 'dilepas dari'} gateway.`);
            setPreview(null);
            await load();
        });
    };

    const saveLogin = (a) => run(`login:${a.id}`, async () => {
        await setAdminTailscaleLogin(a.id, logins[a.id]?.trim() || null);
        setNotice(`Login Tailscale untuk ${a.username} disimpan.`);
        setPreview(null);
        await load();
    });

    if (!status) {
        return error
            ? <div style={{ fontSize: 12, color: 'var(--text3)' }}>{error}</div>
            : <div style={{ color: 'var(--text3)', fontSize: 12 }}>Loading…</div>;
    }

    return (
        <div>
            {error && (
                <div style={{ background: 'var(--red-glow)', border: '1px solid var(--red)', borderRadius: 6, padding: '6px 10px', color: 'var(--red)', fontSize: 11, marginBottom: 12 }}>⚠ {error}</div>
            )}
            {notice && !error && (
                <div style={{ background: '#4ade8012', border: '1px solid #4ade8055', borderRadius: 6, padding: '6px 10px', color: '#4ade80', fontSize: 11, marginBottom: 12 }}>✓ {notice}</div>
            )}

            <div style={{ fontSize: 11, color: 'var(--text3)', lineHeight: 1.6, marginBottom: 10 }}>
                Student mengakses VM tanpa client (lewat browser), jadi lapisan Tailscale menjaga perimeter host gateway:
                admin dashboard mendapat akses penuh, member tailnet lain hanya ke port web dashboard.
            </div>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                <Pill ok={!status.allow_all_active}>{status.allow_all_active ? 'Allow-all aktif' : 'Allow-all nonaktif'}</Pill>
                <Pill ok={status.managed_installed}>{status.managed_installed ? 'Policy terkelola terpasang' : 'Policy terkelola belum dipasang'}</Pill>
                {status.managed_installed && <Pill ok={status.in_sync}>{status.in_sync ? 'Sinkron dengan RBAC dashboard' : 'Belum sinkron dengan RBAC dashboard'}</Pill>}
                <Pill ok={status.gateway_devices.length > 0}>
                    {status.gateway_devices.length ? `Gateway: ${status.gateway_devices.join(', ')}` : 'Belum ada device gateway'}
                </Pill>
            </div>

            <div style={heading}>1. Admin dashboard → login Tailscale</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {status.admins.map(a => (
                    <div key={a.id} style={{ ...card, display: 'flex', alignItems: 'center', gap: 8 }}>
                        <div style={{ width: 150, flexShrink: 0 }}>
                            <div style={{ fontSize: 12, color: 'var(--text)', fontFamily: 'var(--fmono)' }}>{a.username}</div>
                            <div style={{ fontSize: 10, color: 'var(--text3)' }}>{a.role}</div>
                        </div>
                        <input value={logins[a.id] ?? ''} onChange={e => setLogins(l => ({ ...l, [a.id]: e.target.value }))}
                            placeholder="login@tailscale (kosong = tidak dipetakan)" style={{ ...input, flex: 1, minWidth: 0 }} />
                        <button onClick={() => saveLogin(a)} disabled={busy === `login:${a.id}` || (logins[a.id] || '') === (a.tailscale_login || '')}
                            style={{ ...btn('var(--cyan)'), opacity: (logins[a.id] || '') === (a.tailscale_login || '') ? 0.4 : 1 }}>
                            Simpan
                        </button>
                    </div>
                ))}
            </div>

            <div style={heading}>2. Device gateway (host dashboard + Guacamole)</div>
            {!status.managed_installed && (
                <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 6 }}>
                    Terapkan policy (langkah 3) dulu — Tailscale menolak tag {GATEWAY_TAG} sebelum tag itu punya tagOwners.
                </div>
            )}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {devices.length === 0 && <div style={{ fontSize: 12, color: 'var(--text3)' }}>Tidak ada device.</div>}
                {devices.map(d => {
                    const isGw = (d.tags || []).includes(GATEWAY_TAG);
                    return (
                        <div key={d.id} style={{ ...card, display: 'flex', alignItems: 'center', gap: 8 }}>
                            <span style={{ width: 8, height: 8, borderRadius: '50%', flexShrink: 0, background: d.connectedToControl ? '#4ade80' : '#6b7280' }} />
                            <div style={{ flex: 1, minWidth: 0 }}>
                                <div style={{ fontSize: 12, color: 'var(--text)', fontFamily: 'var(--fmono)' }}>{d.hostname}</div>
                                <div style={{ fontSize: 10, color: 'var(--text3)' }}>
                                    {d.addresses?.[0]}{(d.tags || []).length ? ` · ${d.tags.join(', ')}` : ''}{d.isExternal ? ' · shared dari tailnet lain' : ''}
                                </div>
                            </div>
                            {!d.isExternal && (
                                <button onClick={() => toggleGateway(d, !isGw)} disabled={!status.managed_installed || busy === `gw:${d.id}`}
                                    style={{ ...btn(isGw ? 'var(--red)' : 'var(--cyan)'), opacity: status.managed_installed ? 1 : 0.4 }}>
                                    {isGw ? 'Lepas gateway' : 'Jadikan gateway'}
                                </button>
                            )}
                        </div>
                    );
                })}
            </div>

            <div style={heading}>3. Policy jaringan</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, maxWidth: 520 }}>
                <label style={{ fontSize: 11, color: 'var(--text2)' }}>
                    Port web dashboard untuk member tailnet
                    <input value={ports} onChange={e => { setPorts(e.target.value); setPreview(null); }}
                        placeholder="80" style={{ ...input, display: 'block', width: 200, marginTop: 3 }} />
                </label>
                <label style={{ display: 'flex', gap: 6, alignItems: 'flex-start', fontSize: 11, color: enforce ? 'var(--red)' : 'var(--text2)', lineHeight: 1.5 }}>
                    <input type="checkbox" checked={enforce} onChange={e => { setEnforce(e.target.checked); setPreview(null); }} style={{ marginTop: 2 }} />
                    <span>Hapus aturan allow-all (menegakkan pembatasan). Semua akses tailnet yang tidak tercakup aturan lain akan diputus.</span>
                </label>
                <div style={{ display: 'flex', gap: 6 }}>
                    <button onClick={runPreview} disabled={busy === 'preview'} style={btn('var(--cyan)')}>
                        {busy === 'preview' ? 'Memvalidasi…' : 'Preview & Validasi'}
                    </button>
                    {preview && preview.valid && preview.changes.length > 0 && (
                        <button onClick={runApply} disabled={busy === 'apply'} style={btn(enforce ? 'var(--red)' : 'var(--cyan)', true)}>
                            {busy === 'apply' ? 'Menerapkan…' : 'Apply ke Tailnet'}
                        </button>
                    )}
                </div>
            </div>

            {preview && (
                <div style={{ ...card, marginTop: 10 }}>
                    <div style={{ fontSize: 11, marginBottom: 6, color: preview.valid ? '#4ade80' : 'var(--red)' }}>
                        {preview.valid ? '✓ Divalidasi oleh Tailscale' : `✗ Ditolak Tailscale: ${preview.validation_error}`}
                    </div>
                    <div style={{ fontSize: 11, color: 'var(--text2)', marginBottom: 4 }}>
                        {preview.changes.length ? 'Perubahan:' : 'Tidak ada perubahan — policy sudah sesuai.'}
                    </div>
                    <ul style={{ margin: '0 0 6px 16px', padding: 0, fontSize: 11, color: 'var(--text)', lineHeight: 1.6 }}>
                        {preview.changes.map(c => <li key={c}>{c}</li>)}
                    </ul>
                    {preview.warnings.map(w => (
                        <div key={w} style={{ fontSize: 11, color: '#f0c040', lineHeight: 1.5, marginBottom: 4 }}>⚠ {w}</div>
                    ))}
                    <details style={{ marginTop: 6 }}>
                        <summary style={{ fontSize: 11, color: 'var(--text3)', cursor: 'pointer' }}>Lihat policy lengkap</summary>
                        <pre style={{ fontSize: 10, color: 'var(--text2)', background: 'var(--bg-panel)', padding: 8, borderRadius: 4, overflow: 'auto', maxHeight: 320 }}>{preview.proposed}</pre>
                    </details>
                </div>
            )}
        </div>
    );
}
