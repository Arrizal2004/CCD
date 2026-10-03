import { useCallback, useEffect, useState } from 'react';
import { fetchSshKeys, addSshKey, deleteSshKey } from '../api';

const label = { display: 'block', fontSize: 11, color: 'var(--text3)', marginBottom: 5, textTransform: 'uppercase', letterSpacing: '0.06em' };
const input = { width: '100%', boxSizing: 'border-box', background: 'var(--bg-card2)', border: '1px solid var(--border)', borderRadius: 6, padding: '8px 10px', color: 'var(--text)', fontSize: 13, outline: 'none' };

function fmtDate(iso) {
    if (!iso) return 'belum pernah';
    try { return new Date(iso).toLocaleString('id-ID', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }); }
    catch { return iso; }
}

export default function SshKeysSection() {
    const [keys, setKeys] = useState([]);
    const [name, setName] = useState('');
    const [pub, setPub] = useState('');
    const [status, setStatus] = useState(null);
    const [busy, setBusy] = useState(false);

    const load = useCallback(async () => {
        try { setKeys(await fetchSshKeys()); } catch { setKeys([]); }
    }, []);
    useEffect(() => {
        let alive = true;
        fetchSshKeys().then(k => { if (alive) setKeys(k); }).catch(() => {});
        return () => { alive = false; };
    }, []);

    const add = async (e) => {
        e.preventDefault();
        if (!pub.trim()) return;
        setBusy(true); setStatus(null);
        try {
            await addSshKey({ name: name.trim(), public_key: pub.trim() });
            setName(''); setPub('');
            setStatus({ type: 'success', msg: 'SSH key ditambahkan' });
            await load();
        } catch (err) {
            setStatus({ type: 'error', msg: err?.response?.data?.detail || 'Gagal menambahkan SSH key' });
        } finally { setBusy(false); }
    };

    const remove = async (k) => {
        if (!confirm(`Hapus SSH key "${k.name}"? Laptop yang memakai key ini tidak bisa SSH lagi.`)) return;
        try { await deleteSshKey(k.id); await load(); }
        catch (err) { setStatus({ type: 'error', msg: err?.response?.data?.detail || 'Gagal menghapus SSH key' }); }
    };

    return (
        <div style={{ padding: '18px 20px 20px', borderTop: '1px solid var(--border)' }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.08em' }}>
                SSH Key
            </div>
            <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 14, lineHeight: 1.5 }}>
                Untuk SSH ke VM dari terminal sendiri. Buat key di laptop dengan{' '}
                <code style={{ fontFamily: 'var(--fmono)', color: 'var(--text2)' }}>ssh-keygen -t ed25519</code>, lalu tempel isi file{' '}
                <code style={{ fontFamily: 'var(--fmono)', color: 'var(--text2)' }}>~/.ssh/id_ed25519.pub</code> di bawah. Jangan pernah menempel private key.
            </div>

            {keys.length > 0 && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 14 }}>
                    {keys.map(k => (
                        <div key={k.id} style={{ display: 'flex', gap: 10, alignItems: 'flex-start', justifyContent: 'space-between', padding: '10px 12px', border: '1px solid var(--border)', borderRadius: 8, background: 'var(--bg-card2)' }}>
                            <div style={{ minWidth: 0 }}>
                                <div style={{ fontSize: 13, color: 'var(--text)', fontWeight: 600 }}>{k.name}</div>
                                <div style={{ fontSize: 11, color: 'var(--text3)', fontFamily: 'var(--fmono)', overflowWrap: 'anywhere' }}>{k.key_type} · {k.fingerprint}</div>
                                <div style={{ fontSize: 11, color: 'var(--text3)' }}>Terakhir dipakai: {fmtDate(k.last_used_at)}</div>
                            </div>
                            <button type="button" onClick={() => remove(k)}
                                style={{ flexShrink: 0, padding: '4px 10px', fontSize: 11, borderRadius: 5, cursor: 'pointer', background: 'transparent', border: '1px solid var(--red)', color: 'var(--red)' }}>
                                Hapus
                            </button>
                        </div>
                    ))}
                </div>
            )}

            <form onSubmit={add}>
                <div style={{ marginBottom: 10 }}>
                    <label style={label} htmlFor="ssh-key-name">Nama (opsional)</label>
                    <input id="ssh-key-name" value={name} onChange={e => setName(e.target.value)} placeholder="mis. laptop pribadi" maxLength={60} style={input} />
                </div>
                <div style={{ marginBottom: 12 }}>
                    <label style={label} htmlFor="ssh-key-pub">Public key</label>
                    <textarea id="ssh-key-pub" value={pub} onChange={e => setPub(e.target.value)} rows={3} spellCheck={false} autoComplete="off"
                        placeholder="ssh-ed25519 AAAA... nama@laptop"
                        style={{ ...input, fontFamily: 'var(--fmono)', fontSize: 12, resize: 'vertical' }} />
                </div>
                {status && (
                    <div style={{ padding: '8px 12px', borderRadius: 6, marginBottom: 12, fontSize: 12,
                        border: `1px solid ${status.type === 'success' ? 'var(--green)' : 'var(--red)'}`,
                        color: status.type === 'success' ? 'var(--green)' : 'var(--red)' }}>
                        {status.msg}
                    </div>
                )}
                <button type="submit" disabled={busy || !pub.trim()}
                    style={{ width: '100%', padding: '9px 0', borderRadius: 6, border: '1px solid var(--cyan)', background: 'transparent', color: 'var(--cyan)', fontSize: 13, fontWeight: 600, cursor: busy ? 'wait' : 'pointer', opacity: busy || !pub.trim() ? 0.5 : 1 }}>
                    {busy ? 'Menyimpan…' : 'Tambah SSH Key'}
                </button>
            </form>
        </div>
    );
}
