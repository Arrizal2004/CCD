import { useState, useEffect, useRef } from 'react';
import { getVmCreds, upsertVmCreds, deleteVmCreds, testVmCreds } from '../api';

export default function SshCredModal({ vm, hostName, onClose, onSaved, defaults }) {
    const [osType,   setOsType]   = useState('');      // '' = belum dipilih
    const [form, setForm] = useState({
        cred_type:     'ssh',
        guac_protocol: '',   // 'ssh' | 'rdp' — protokol Quick-Connect Guacamole
        ssh_host:  '',
        ssh_port:  22,
        username:  '',
        password:  '',
        pkey:      '',
    });
    // true bila protokol Guacamole sudah dipilih user / dimuat dari DB (jangan di-default ulang)
    const protoTouched = useRef(false);
    const [authMode,    setAuthMode]    = useState('password');
    const [existing,    setExisting]    = useState(null);
    const [loading,     setLoading]     = useState(true);
    const [saving,      setSaving]      = useState(false);
    const [testing,     setTesting]     = useState(false);
    const [testResult,  setTestResult]  = useState(null);
    const [error,       setError]       = useState('');

    const vmId = vm.vm_id || vm.vm_name;

    useEffect(() => {
        (async () => {
            try {
                const data = await getVmCreds(hostName, vmId);
                setExisting(data);
                setOsType(data.os_type || 'linux');
                if (data.guac_protocol) protoTouched.current = true;
                setForm(f => ({
                    ...f,
                    cred_type:     data.cred_type || 'ssh',
                    guac_protocol: data.guac_protocol || f.guac_protocol,
                    ssh_host:  data.ssh_host  || '',
                    ssh_port:  data.ssh_port  || 22,
                    username:  data.username  || '',
                }));
            } catch {
                // Belum ada — isi dari IP agent / cloud-init Proxmox (username + IP statis) bila tersedia
                const knownIp = (vm.network_adapters || [])
                    .flatMap(n => n.ip_addresses || [])
                    .find(ip => ip && !ip.includes(':'));
                setForm(f => ({
                    ...f,
                    ssh_host: knownIp || defaults?.ssh_host || f.ssh_host,
                    username: defaults?.username || f.username,
                }));
                if (defaults?.os_type) setOsType(defaults.os_type);
            } finally {
                setLoading(false);
            }
        })();
    }, []);

    // Sinkronkan cred_type, username, dan protokol Guacamole default saat os_type berubah
    useEffect(() => {
        if (!osType) return;
        setForm(f => {
            const proto = protoTouched.current ? f.guac_protocol : (osType === 'linux' ? 'ssh' : 'rdp');
            return {
                ...f,
                cred_type:     osType === 'linux' ? 'ssh' : 'ps_direct',
                username:      f.username || (osType === 'linux' ? 'root' : 'Administrator'),
                guac_protocol: proto,
                ssh_port:      protoTouched.current ? f.ssh_port : (proto === 'rdp' ? 3389 : 22),
            };
        });
    }, [osType]);

    // Pilih protokol Guacamole + sesuaikan port default
    const pickProto = (v) => {
        protoTouched.current = true;
        setForm(f => {
            let port = f.ssh_port;
            if (v === 'rdp' && (!port || Number(port) === 22))   port = 3389;
            if (v === 'ssh' && (!port || Number(port) === 3389)) port = 22;
            return { ...f, guac_protocol: v, ssh_port: port };
        });
    };

    const handleSave = async () => {
        if (!osType) { setError('Pilih OS terlebih dahulu'); return; }
        if (!form.ssh_host.trim()) { setError('IP / Hostname VM wajib diisi'); return; }
        if (!form.username.trim()) { setError('Username wajib diisi'); return; }
        if (authMode === 'password' && !form.password.trim() && !existing?.has_password) {
            setError('Password wajib diisi'); return;
        }
        if (authMode === 'pkey' && !form.pkey.trim() && !existing?.has_pkey) {
            setError('Private key wajib diisi'); return;
        }

        setSaving(true);
        setError('');
        try {
            const body = {
                os_type:       osType,
                cred_type:     form.cred_type,
                guac_protocol: form.guac_protocol || (osType === 'linux' ? 'ssh' : 'rdp'),
                ssh_host:      form.ssh_host,
                ssh_port:      Number(form.ssh_port),
                username:      form.username,
                password:      (authMode === 'password' && form.password) ? form.password : undefined,
                pkey:          (authMode === 'pkey'     && form.pkey)     ? form.pkey     : undefined,
            };
            // Kalau edit dan field kosong, tetap kirim password/pkey (backend tidak overwrite jika undefined)
            if (!body.password && !body.pkey && existing) {
                // Tidak ada update auth — kirim salah satu dummy agar tidak error validasi backend
                // Backend akan ignore jika undefined
            }
            await upsertVmCreds(hostName, vmId, body);
            onSaved?.(osType);
            onClose();
        } catch (e) {
            setError(e?.response?.data?.detail || e.message);
        } finally {
            setSaving(false);
        }
    };

    const handleTest = async () => {
        setTesting(true);
        setTestResult(null);
        try {
            const res = await testVmCreds(hostName, vmId);
            setTestResult(res);
        } catch (e) {
            setTestResult({ ok: false, error: e?.response?.data?.detail || e.message });
        } finally {
            setTesting(false);
        }
    };

    const handleDelete = async () => {
        if (!confirm(`Hapus credentials untuk VM "${vm.vm_name}"?`)) return;
        try {
            await deleteVmCreds(hostName, vmId);
            onSaved?.(null);
            onClose();
        } catch (e) {
            setError(e?.response?.data?.detail || e.message);
        }
    };

    if (loading) return null;

    return (
        <div style={{
            position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.78)', zIndex: 3500,
            display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20,
        }} onClick={onClose}>
            <div style={{
                background: 'var(--bg-card)', border: '1px solid var(--border-light)',
                borderRadius: 14, width: 'min(560px, 95vw)', overflow: 'hidden',
            }} onClick={e => e.stopPropagation()}>

                {/* Header */}
                <div style={{
                    padding: '14px 18px', borderBottom: '1px solid var(--border)',
                    background: 'var(--bg-card2)',
                    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                }}>
                    <div>
                        <div style={{ fontFamily: 'var(--fmono)', fontSize: 15, fontWeight: 600, color: 'var(--cyan)' }}>
                            🔑 Konfigurasi Koneksi VM
                        </div>
                        <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>
                            {vm.vm_name} · {hostName}
                            {existing && (
                                <span style={{ marginLeft: 8, color: osType === 'linux' ? 'var(--green)' : 'var(--cyan)' }}>
                                    ● Tersimpan
                                </span>
                            )}
                        </div>
                    </div>
                    <button onClick={onClose} style={closeBtn}>✕</button>
                </div>

                <div style={{ padding: 18, display: 'flex', flexDirection: 'column', gap: 16 }}>

                    {/* ── Langkah 1: Pilih OS ─────────────────────────────── */}
                    <div>
                        <div style={stepLabel}>① Pilih Sistem Operasi VM</div>
                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                            <OsCard
                                selected={osType === 'linux'}
                                onClick={() => setOsType('linux')}
                                icon="🐧"
                                title="Linux"
                                subtitle="Ubuntu, Debian, CentOS, dll"
                                color="#4ade80"
                                proto="SSH (Paramiko PTY)"
                            />
                            <OsCard
                                selected={osType === 'windows'}
                                onClick={() => setOsType('windows')}
                                icon="⊞"
                                title="Windows"
                                subtitle="Windows Server, Windows 10/11"
                                color="#8be9fd"
                                proto="RDP (Guacamole)"
                            />
                        </div>
                    </div>

                    {/* ── Langkah 2: Isi credentials (hanya tampil setelah OS dipilih) ── */}
                    {osType && (
                        <>
                            {/* Protokol info */}
                            <div style={{
                                padding: '8px 12px', borderRadius: 8, fontSize: 11,
                                background: osType === 'linux' ? '#4ade8015' : '#8be9fd15',
                                color:      osType === 'linux' ? '#4ade80'   : '#8be9fd',
                                border: `1px solid ${osType === 'linux' ? '#4ade8044' : '#8be9fd44'}`,
                                lineHeight: 1.6,
                            }}>
                                {osType === 'linux'
                                    ? '🐧 Protokol: SSH via Paramiko. Backend terhubung langsung ke IP VM. Terminal mendukung PTY penuh, resize, dan Ctrl+C.'
                                    : '⊞ Protokol: RDP standar via Guacamole. Memerlukan VM terhubung ke jaringan (IP dapat dijangkau backend).'}
                            </div>

                            {/* Langkah 2 */}
                            <div style={{ borderTop: '1px solid var(--border)', paddingTop: 14 }}>
                                <div style={stepLabel}>② Konfigurasi Akses</div>
                                {!existing && defaults?.username && (
                                    <div style={{ fontSize: 11, color: 'var(--text3)', lineHeight: 1.5, marginBottom: 10 }}>
                                        Username & IP diisi dari cloud-init Proxmox. Password tidak bisa dibaca dari Proxmox
                                        (disimpan dalam bentuk hash) — isi password yang dipakai saat membuat VM.
                                    </div>
                                )}
                                <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>

                                    {/* Pilih protokol Quick-Connect Guacamole: SSH atau RDP */}
                                    <div>
                                        <label style={labelStyle}>Protokol Quick-Connect (Guacamole)</label>
                                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                                            <ProtoCard
                                                selected={form.guac_protocol === 'ssh'}
                                                onClick={() => pickProto('ssh')}
                                                icon="🖥" title="SSH" subtitle="Terminal teks · port 22" color="#4ade80"
                                            />
                                            <ProtoCard
                                                selected={form.guac_protocol === 'rdp'}
                                                onClick={() => pickProto('rdp')}
                                                icon="🪟" title="RDP" subtitle="Desktop grafis · port 3389" color="#8be9fd"
                                            />
                                        </div>
                                    </div>

                                    {/* IP + Port — target koneksi Guacamole */}
                                    <div>
                                        <label style={labelStyle}>
                                            {form.guac_protocol === 'rdp' ? 'IP Address VM (target RDP)' : 'IP Address / Hostname VM'}
                                        </label>
                                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 80px', gap: 8 }}>
                                            <input
                                                value={form.ssh_host}
                                                onChange={e => setForm(f => ({ ...f, ssh_host: e.target.value }))}
                                                placeholder="192.168.1.100"
                                                style={{ ...inputStyle, width: '100%', boxSizing: 'border-box' }}
                                            />
                                            <input
                                                type="number"
                                                value={form.ssh_port}
                                                onChange={e => setForm(f => ({ ...f, ssh_port: e.target.value }))}
                                                placeholder={form.guac_protocol === 'rdp' ? '3389' : '22'}
                                                style={{ ...inputStyle, width: '100%', boxSizing: 'border-box' }}
                                            />
                                        </div>
                                        {osType === 'windows' && (
                                            <div style={{ fontSize: 10, color: 'var(--text3)', marginTop: 5, lineHeight: 1.5 }}>
                                                💡 Otomasi Windows tetap via PowerShell Direct (nama VM {vm.vm_name}).
                                                IP di atas dipakai untuk koneksi {(form.guac_protocol || 'rdp').toUpperCase()} Guacamole.
                                            </div>
                                        )}
                                    </div>

                                    {/* Username */}
                                    <div>
                                        <label style={labelStyle}>
                                            {osType === 'linux' ? 'Username SSH' : 'Username lokal VM (akun administrator)'}
                                        </label>
                                        <input
                                            value={form.username}
                                            onChange={e => setForm(f => ({ ...f, username: e.target.value }))}
                                            placeholder={osType === 'linux' ? 'root / ubuntu' : 'Administrator'}
                                            style={{ ...inputStyle, width: '100%', boxSizing: 'border-box' }}
                                        />
                                    </div>

                                    {/* Auth mode — hanya untuk Linux */}
                                    {osType === 'linux' && (
                                        <div>
                                            <label style={labelStyle}>Metode Autentikasi</label>
                                            <div style={{ display: 'flex', gap: 8 }}>
                                                {[['password', '🔑 Password'], ['pkey', '🗝️ Private Key']].map(([v, l]) => (
                                                    <button key={v} onClick={() => setAuthMode(v)} style={tabBtn(authMode === v)}>
                                                        {l}
                                                    </button>
                                                ))}
                                            </div>
                                        </div>
                                    )}

                                    {/* Password */}
                                    {(osType === 'windows' || authMode === 'password') && (
                                        <div>
                                            <label style={labelStyle}>
                                                Password
                                                {existing?.has_password && (
                                                    <span style={{ color: 'var(--green)', marginLeft: 6 }}>
                                                        ✓ Tersimpan (kosongkan untuk tetap pakai yg lama)
                                                    </span>
                                                )}
                                            </label>
                                            <input
                                                type="password"
                                                value={form.password}
                                                onChange={e => setForm(f => ({ ...f, password: e.target.value }))}
                                                placeholder={existing?.has_password ? '••••••••' : 'Password'}
                                                style={{ ...inputStyle, width: '100%', boxSizing: 'border-box' }}
                                            />
                                        </div>
                                    )}

                                    {/* Private Key */}
                                    {osType === 'linux' && authMode === 'pkey' && (
                                        <div>
                                            <label style={labelStyle}>
                                                Private Key (PEM)
                                                {existing?.has_pkey && (
                                                    <span style={{ color: 'var(--green)', marginLeft: 6 }}>✓ Tersimpan</span>
                                                )}
                                            </label>
                                            <textarea
                                                value={form.pkey}
                                                onChange={e => setForm(f => ({ ...f, pkey: e.target.value }))}
                                                placeholder="-----BEGIN RSA PRIVATE KEY-----&#10;..."
                                                rows={5}
                                                style={{
                                                    ...inputStyle, width: '100%', boxSizing: 'border-box',
                                                    resize: 'vertical', fontFamily: 'var(--fmono)', fontSize: 11,
                                                }}
                                            />
                                        </div>
                                    )}

                                    {/* Sudo note */}
                                    {osType === 'linux' && (
                                        <div style={noteBanner('#4ade80')}>
                                            💡 Untuk otomasi (konfigurasi IP, ekspansi disk), user memerlukan akses <code>sudo</code>.
                                            Rekomendasi: tambahkan <code>{form.username || 'ubuntu'} ALL=(ALL) NOPASSWD:ALL</code> di sudoers.
                                        </div>
                                    )}
                                    {osType === 'windows' && (
                                        <div style={noteBanner('#8be9fd')}>
                                            ⚠ PS Direct memerlukan VM dalam keadaan <strong>Running</strong> dan
                                            Integration Services aktif. Username harus akun lokal administrator di VM.
                                        </div>
                                    )}
                                </div>
                            </div>

                            {/* Test result */}
                            {testResult && (
                                <div style={noteBanner(testResult.ok ? '#4ade80' : '#f87171')}>
                                    {testResult.ok
                                        ? '✓ Koneksi berhasil!'
                                        : `✗ Gagal: ${testResult.error}`}
                                </div>
                            )}

                            {error && <div style={noteBanner('#f87171')}>✗ {error}</div>}

                            {/* Actions */}
                            <div style={{ display: 'flex', gap: 8, justifyContent: 'space-between', borderTop: '1px solid var(--border)', paddingTop: 14 }}>
                                <div style={{ display: 'flex', gap: 8 }}>
                                    {existing && osType === 'linux' && (
                                        <button onClick={handleTest} disabled={testing} style={secBtn}>
                                            {testing ? 'Testing...' : '⚡ Test SSH'}
                                        </button>
                                    )}
                                    {existing && (
                                        <button onClick={handleDelete} style={dangerBtn}>🗑 Hapus</button>
                                    )}
                                </div>
                                <div style={{ display: 'flex', gap: 8 }}>
                                    <button onClick={onClose} style={secBtn}>Batal</button>
                                    <button onClick={handleSave} disabled={saving} style={primaryBtn(osType)}>
                                        {saving ? 'Menyimpan...' : '💾 Simpan'}
                                    </button>
                                </div>
                            </div>
                        </>
                    )}
                </div>
            </div>
        </div>
    );
}

function ProtoCard({ selected, onClick, icon, title, subtitle, color }) {
    return (
        <div onClick={onClick} style={{
            padding: '10px 12px', borderRadius: 8, cursor: 'pointer',
            border: selected ? `2px solid ${color}` : '2px solid var(--border)',
            background: selected ? `${color}12` : 'var(--bg-hover)',
            transition: 'all 0.15s', display: 'flex', alignItems: 'center', gap: 10,
        }}>
            <span style={{ fontSize: 18 }}>{icon}</span>
            <div>
                <div style={{ fontSize: 13, fontWeight: 600, color: selected ? color : 'var(--text)' }}>{title}</div>
                <div style={{ fontSize: 10, color: 'var(--text3)', marginTop: 1 }}>{subtitle}</div>
            </div>
        </div>
    );
}

function OsCard({ selected, onClick, icon, title, subtitle, color, proto }) {
    return (
        <div
            onClick={onClick}
            style={{
                padding: '14px 16px', borderRadius: 10, cursor: 'pointer',
                border: selected ? `2px solid ${color}` : '2px solid var(--border)',
                background: selected ? `${color}12` : 'var(--bg-hover)',
                transition: 'all 0.15s',
                position: 'relative',
            }}
        >
            {selected && (
                <div style={{
                    position: 'absolute', top: 8, right: 8,
                    width: 18, height: 18, borderRadius: '50%',
                    background: color, color: '#000',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    fontSize: 11, fontWeight: 700,
                }}>✓</div>
            )}
            <div style={{ fontSize: 24, marginBottom: 6 }}>{icon}</div>
            <div style={{ fontSize: 14, fontWeight: 600, color: selected ? color : 'var(--text)', marginBottom: 2 }}>
                {title}
            </div>
            <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 6 }}>{subtitle}</div>
            <div style={{
                fontSize: 10, fontFamily: 'var(--fmono)',
                color: selected ? color : 'var(--text3)',
                padding: '2px 6px', borderRadius: 4,
                background: selected ? `${color}18` : 'transparent',
                display: 'inline-block',
            }}>
                {proto}
            </div>
        </div>
    );
}

const stepLabel = {
    fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase',
    letterSpacing: '0.1em', marginBottom: 10, fontWeight: 600,
};
const labelStyle = {
    display: 'block', fontSize: 11, color: 'var(--text3)',
    textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6,
};
const inputStyle = {
    background: 'var(--bg-hover)', border: '1px solid var(--border-light)',
    borderRadius: 6, padding: '7px 10px', color: 'var(--text)',
    fontSize: 12, fontFamily: 'var(--fmono)', outline: 'none',
};
const noteBanner = (color) => ({
    padding: '8px 12px', borderRadius: 8, fontSize: 11,
    background: color + '15', color, border: `1px solid ${color}44`, lineHeight: 1.6,
});
const tabBtn = (active) => ({
    padding: '5px 12px', borderRadius: 6, fontSize: 11, cursor: 'pointer',
    fontFamily: 'var(--fmono)',
    background: active ? 'var(--bg-card2)' : 'var(--bg-hover)',
    color:      active ? 'var(--cyan)'     : 'var(--text3)',
    border:     active ? '1px solid var(--cyan)' : '1px solid var(--border)',
});
const primaryBtn = (os) => ({
    padding: '7px 16px', borderRadius: 6, fontSize: 12, cursor: 'pointer',
    background: os === 'linux' ? '#4ade8022' : '#8be9fd22',
    color:      os === 'linux' ? '#4ade80'   : '#8be9fd',
    border:     `1px solid ${os === 'linux' ? '#4ade80' : '#8be9fd'}`,
    fontFamily: 'var(--fmono)',
});
const secBtn = {
    padding: '7px 12px', borderRadius: 6, fontSize: 12, cursor: 'pointer',
    background: 'var(--bg-hover)', color: 'var(--text2)', border: '1px solid var(--border)',
};
const dangerBtn = {
    padding: '7px 12px', borderRadius: 6, fontSize: 12, cursor: 'pointer',
    background: 'var(--red-glow)', color: 'var(--red)', border: '1px solid var(--red)',
};
const closeBtn = {
    width: 28, height: 28, borderRadius: 6, background: 'var(--bg-hover)',
    border: '1px solid var(--border)', color: 'var(--text2)', cursor: 'pointer', fontSize: 14,
};
