import { useState, useEffect, useCallback } from 'react';
import { fetchTailscaleConfig, saveTailscaleConfig, deleteTailscaleConfig } from '../api';

const input = { width: '100%', boxSizing: 'border-box', background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 6, padding: '6px 10px', color: 'var(--text)', fontSize: 12 };
const label = { fontSize: 10, color: 'var(--text3)', marginBottom: 2 };

const SOURCE_LABEL = { dashboard: 'Dashboard', env: 'Environment (.env)' };

export default function TailscaleConfigPanel() {
    const [config, setConfig] = useState(null);
    const [form, setForm] = useState({ tailnet: '-', api_key: '' });
    const [editing, setEditing] = useState(false);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState(null);
    const [notice, setNotice] = useState(null);

    const load = useCallback(async () => {
        try {
            setConfig(await fetchTailscaleConfig());
        } catch (e) {
            setError(e?.response?.data?.detail || 'Gagal mengambil konfigurasi Tailscale');
        }
    }, []);

    useEffect(() => { load(); }, [load]);

    const startEdit = () => {
        setForm({ tailnet: config?.tailnet || '-', api_key: '' });
        setError(null);
        setNotice(null);
        setEditing(true);
    };

    const handleSave = async (e) => {
        e.preventDefault();
        setSaving(true);
        setError(null);
        try {
            const body = { tailnet: form.tailnet.trim() || '-' };
            if (form.api_key.trim()) body.api_key = form.api_key.trim();
            const res = await saveTailscaleConfig(body);
            setConfig(res);
            setEditing(false);
            setNotice(`Tersambung — ${res.devices_total} device ditemukan di tailnet.`);
        } catch (e) {
            setError(e?.response?.data?.detail || 'Gagal menyimpan konfigurasi');
        } finally {
            setSaving(false);
        }
    };

    const handleDelete = async () => {
        if (!confirm('Hapus konfigurasi Tailscale dari dashboard? Dashboard akan kembali memakai env var (jika ada), atau Tailscale menjadi tidak terkonfigurasi.')) return;
        try {
            setConfig(await deleteTailscaleConfig());
            setNotice('Konfigurasi dashboard dihapus.');
        } catch (e) {
            setError(e?.response?.data?.detail || 'Gagal menghapus konfigurasi');
        }
    };

    return (
        <div>
            {error && (
                <div style={{ background: 'var(--red-glow)', border: '1px solid var(--red)', borderRadius: 6, padding: '6px 10px', color: 'var(--red)', fontSize: 11, marginBottom: 12 }}>⚠ {error}</div>
            )}
            {notice && !error && (
                <div style={{ background: '#4ade8012', border: '1px solid #4ade8055', borderRadius: 6, padding: '6px 10px', color: '#4ade80', fontSize: 11, marginBottom: 12 }}>✓ {notice}</div>
            )}

            {!config && !error && <div style={{ color: 'var(--text3)', fontSize: 12, padding: 10 }}>Loading…</div>}

            {config && !editing && (
                <>
                    <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 6, padding: '8px 10px', marginBottom: 14 }}>
                        {config.configured ? (
                            <>
                                <div style={{ fontSize: 12, color: 'var(--text)', fontFamily: 'var(--fmono)', fontWeight: 600 }}>
                                    {config.tailnet === '-' ? 'Tailnet default (milik API key)' : config.tailnet}
                                </div>
                                <div style={{ fontSize: 11, color: 'var(--text3)' }}>
                                    API key {config.api_key_hint} · sumber: {SOURCE_LABEL[config.source] || config.source}
                                    {config.updated_at && ` · diperbarui ${new Date(config.updated_at).toLocaleString()}`}
                                </div>
                            </>
                        ) : (
                            <div style={{ fontSize: 12, color: 'var(--text3)' }}>
                                Belum dikonfigurasi. Status device & ACL Tailscale tidak akan tampil sampai API key diisi.
                            </div>
                        )}
                    </div>
                    <div style={{ display: 'flex', gap: 6 }}>
                        <button onClick={startEdit}
                            style={{ padding: '6px 14px', fontSize: 12, borderRadius: 6, cursor: 'pointer', background: 'var(--cyan-glow)', border: '1px solid var(--cyan)', color: 'var(--cyan)' }}>
                            {config.configured ? 'Ubah' : '+ Hubungkan Tailscale'}
                        </button>
                        {config.source === 'dashboard' && (
                            <button onClick={handleDelete}
                                style={{ padding: '6px 14px', fontSize: 12, borderRadius: 6, cursor: 'pointer', background: 'transparent', border: '1px solid var(--red)', color: 'var(--red)' }}>
                                Hapus
                            </button>
                        )}
                    </div>
                </>
            )}

            {editing && (
                <form onSubmit={handleSave} style={{ display: 'flex', flexDirection: 'column', gap: 10, maxWidth: 420 }}>
                    <div style={{ fontSize: 11, color: 'var(--text3)', lineHeight: 1.6 }}>
                        Buat API access token di Tailscale admin console → <span style={{ fontFamily: 'var(--fmono)' }}>Settings → Keys → Generate access token</span>.
                        Key divalidasi ke Tailscale API sebelum disimpan, dan disimpan terenkripsi.
                    </div>
                    <div>
                        <div style={label}>Tailnet (isi "-" untuk tailnet milik API key)</div>
                        <input value={form.tailnet} onChange={e => setForm(f => ({ ...f, tailnet: e.target.value }))}
                            placeholder="-" style={input} />
                    </div>
                    <div>
                        <div style={label}>
                            API Access Token {config?.configured && <span>(kosongkan kalau tidak diganti)</span>}
                        </div>
                        <input type="password" value={form.api_key} onChange={e => setForm(f => ({ ...f, api_key: e.target.value }))}
                            placeholder="tskey-api-…" autoComplete="off" style={input} />
                    </div>
                    <div style={{ display: 'flex', gap: 6, marginTop: 4 }}>
                        <button type="submit" disabled={saving}
                            style={{ padding: '6px 16px', fontSize: 12, borderRadius: 6, cursor: 'pointer', background: 'var(--cyan)', color: '#000', border: 'none', fontWeight: 600, opacity: saving ? 0.6 : 1 }}>
                            {saving ? 'Memvalidasi…' : 'Simpan'}
                        </button>
                        <button type="button" onClick={() => { setEditing(false); setError(null); }}
                            style={{ padding: '6px 16px', fontSize: 12, borderRadius: 6, cursor: 'pointer', background: 'transparent', border: '1px solid var(--border)', color: 'var(--text3)' }}>
                            Batal
                        </button>
                    </div>
                </form>
            )}
        </div>
    );
}
