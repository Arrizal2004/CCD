import { useCallback, useEffect, useState } from 'react';
import { createOpenWebTicket, fetchMyOpenWebHistory, reopenOpenWeb, extendOpenWeb } from '../api';
import { fmtTime } from '../adminFormat';
import { useT } from '../i18n';

const STORAGE_KEY = 'ccd-openweb-url';
const btn = { padding: '5px 12px', fontSize: 11, borderRadius: 5, cursor: 'pointer', background: 'transparent', border: '1px solid var(--cyan)', color: 'var(--cyan)' };

// Tambah http:// bila tanpa skema; hanya izinkan http/https (tolak javascript:, data:, dll).
function normalizeUrl(raw) {
    const t = (raw || '').trim();
    if (!t) return null;
    const withScheme = /^[a-z][a-z0-9+.-]*:\/\//i.test(t) ? t : `http://${t}`;
    try {
        const u = new URL(withScheme);
        return (u.protocol === 'http:' || u.protocol === 'https:') ? u.href : null;
    } catch { return null; }
}

// IP privat (RFC1918 + CGNAT/Tailscale) diblokir browser saat dashboard dibuka dari IP publik
// (Local Network Access), jadi dimuat lewat proxy dashboard.
function isPrivateHost(href) {
    const h = new URL(href).hostname;
    const m = h.match(/^(\d{1,3})\.(\d{1,3})\.\d{1,3}\.\d{1,3}$/);
    if (!m) return false;
    const a = +m[1], b = +m[2];
    return a === 10 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168) || (a === 100 && b >= 64 && b <= 127);
}

const HIST_STATUS = { active: ['openweb.stActive', 'var(--green)'], expired: ['openweb.stExpired', 'var(--text3)'], revoked: ['openweb.stRevoked', 'var(--red)'] };

function loadLast() {
    try { return localStorage.getItem(STORAGE_KEY) || ''; } catch { return ''; }
}

export default function OpenWebPage() {
    const t = useT();
    const [input, setInput] = useState(loadLast);
    const [target, setTarget] = useState(null);   // { src, shown, proxied }
    const [error, setError] = useState(null);
    const [loading, setLoading] = useState(false);
    const [reloadKey, setReloadKey] = useState(0);
    const [history, setHistory] = useState([]);
    const [showHistory, setShowHistory] = useState(true);
    const [busyId, setBusyId] = useState(null);

    const loadHistory = useCallback(async () => {
        try { setHistory((await fetchMyOpenWebHistory()).items || []); } catch { /* riwayat tidak wajib */ }
    }, []);
    // Sisa waktu dan tombol tambah mengikuti server, jadi dimuat ulang berkala selama halaman terbuka.
    useEffect(() => {
        let alive = true;
        const pull = () => fetchMyOpenWebHistory().then(d => { if (alive) setHistory(d.items || []); }).catch(() => {});
        pull();
        const timer = setInterval(pull, 30000);
        return () => { alive = false; clearInterval(timer); };
    }, []);

    const open = async (raw) => {
        const n = normalizeUrl(raw);
        if (!n) { setError(t('openweb.invalid')); return; }
        setError(null);
        setInput(n);
        setLoading(true);
        try {
            if (isPrivateHost(n)) {
                const { proxy_path, session_id } = await createOpenWebTicket(n);
                setTarget({ src: proxy_path, shown: n, proxied: true, sid: session_id });
                loadHistory();
            } else {
                setTarget({ src: n, shown: n, proxied: false });
            }
            setReloadKey(k => k + 1);
            try { localStorage.setItem(STORAGE_KEY, n); } catch { /* storage tidak tersedia */ }
        } catch (e) {
            setTarget(null);
            setError(e?.response?.data?.detail || t('openweb.failed'));
        } finally {
            setLoading(false);
        }
    };

    // Sesi yang masih berlaku dibuka lagi tanpa membuat sesi baru.
    const reopen = async (item) => {
        setBusyId(item.id); setError(null);
        try {
            const r = await reopenOpenWeb(item.id);
            setInput(item.url);
            setTarget({ src: r.proxy_path, shown: item.url, proxied: true, sid: item.id });
            setReloadKey(k => k + 1);
            setShowHistory(false);
        } catch (e) {
            setError(e?.response?.data?.detail || t('openweb.failed'));
            loadHistory();
        } finally { setBusyId(null); }
    };

    // +1 jam. Path proxy yang sedang terbuka tetap sah, jadi halaman tidak dimuat ulang.
    const extend = async (item) => {
        setBusyId(item.id); setError(null);
        try { await extendOpenWeb(item.id); }
        catch (e) { setError(e?.response?.data?.detail || t('openweb.extendFailed')); }
        finally { setBusyId(null); loadHistory(); }
    };

    const left = (s) => (s < 60 ? t('openweb.leftLess') : t('openweb.left', { min: Math.floor(s / 60) }));

    const mixed = target && !target.proxied && window.location.protocol === 'https:' && target.src.startsWith('http:');

    return (
        <div style={{ padding: '14px 20px', maxWidth: 1600, margin: '0 auto' }}>
            <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 10 }}>
                {t('openweb.title')}
            </div>

            <form onSubmit={e => { e.preventDefault(); open(input); }} style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 10 }}>
                <input value={input} onChange={e => setInput(e.target.value)}
                    spellCheck={false} autoComplete="off" aria-label={t('openweb.address')}
                    style={{ flex: 1, minWidth: 220, padding: '6px 10px', fontSize: 12, fontFamily: 'var(--fmono)', background: 'var(--bg-panel)', color: 'var(--text)', border: '1px solid var(--border)', borderRadius: 5 }} />
                <button type="submit" style={btn} disabled={loading}>{loading ? '…' : t('openweb.open')}</button>
                <button type="button" style={btn} disabled={!target || loading} onClick={() => open(target.shown)}>{t('openweb.reload')}</button>
                {target && <a href={target.src} target="_blank" rel="noopener noreferrer" style={{ ...btn, textDecoration: 'none' }}>{t('openweb.newTab')}</a>}
                <button type="button" style={btn} onClick={() => setShowHistory(v => !v)}>
                    {t('openweb.history')}{history.length ? ` (${history.length})` : ''}
                </button>
            </form>

            {showHistory && (
                <div style={{ border: '1px solid var(--border)', borderRadius: 8, marginBottom: 10, overflow: 'hidden' }}>
                    {history.length === 0 && <div style={{ padding: 12, fontSize: 12, color: 'var(--text3)' }}>{t('openweb.historyEmpty')}</div>}
                    {history.map(item => {
                        const [stKey, stColor] = HIST_STATUS[item.status] || ['', 'var(--text3)'];
                        const active = item.status === 'active';
                        const extendTitle = item.can_extend ? t('openweb.extendHint')
                            : active ? t('openweb.extendLater', { min: Math.max(1, Math.ceil(item.extend_in / 60)) }) : '';
                        return (
                            <div key={item.id} style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', padding: '8px 12px', borderBottom: '1px solid var(--border)' }}>
                                <div title={item.url} style={{ flex: '1 1 260px', minWidth: 0, fontSize: 12, fontFamily: 'var(--fmono)', color: 'var(--text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.url}</div>
                                <span style={{ fontSize: 11, fontFamily: 'var(--fmono)', color: stColor }}>{stKey ? t(stKey) : item.status}</span>
                                <span style={{ fontSize: 11, color: 'var(--text3)', fontFamily: 'var(--fmono)' }}>
                                    {t('openweb.validUntil')} {fmtTime(item.expires_at)}{active ? ` · ${left(item.remaining)}` : ''}
                                </span>
                                {active ? (
                                    <>
                                        <button style={btn} disabled={busyId === item.id} onClick={() => reopen(item)}>{t('openweb.openAgain')}</button>
                                        <button style={{ ...btn, opacity: item.can_extend ? 1 : 0.5 }} disabled={busyId === item.id || !item.can_extend}
                                            title={extendTitle} onClick={() => extend(item)}>{t('openweb.extend')}</button>
                                    </>
                                ) : (
                                    <button style={btn} disabled={loading} onClick={() => open(item.url)}>{t('openweb.openNew')}</button>
                                )}
                            </div>
                        );
                    })}
                </div>
            )}

            {error && <div style={{ color: 'var(--red)', fontSize: 12, marginBottom: 8 }}>{error}</div>}
            {mixed && (
                <div style={{ color: 'var(--yellow, #ffb300)', fontSize: 11, marginBottom: 8 }}>
                    {t('openweb.mixed')}
                </div>
            )}

            {target ? (
                <>
                    <iframe key={`${target.src}|${reloadKey}`} title={t('openweb.title')} src={target.src}
                        sandbox={target.proxied
                            ? 'allow-scripts allow-forms allow-popups allow-downloads'
                            : 'allow-scripts allow-forms allow-same-origin allow-popups allow-downloads'}
                        referrerPolicy="no-referrer"
                        style={{ width: '100%', height: 'calc(100vh - 210px)', minHeight: 400, border: '1px solid var(--border)', borderRadius: 6, background: '#fff' }} />
                    <div style={{ color: 'var(--text3)', fontSize: 10, marginTop: 6 }}>
                        {target.proxied
                            ? t('openweb.proxied')
                            : t('openweb.direct')}
                    </div>
                </>
            ) : (
                !error && <div style={{ color: 'var(--text3)', fontSize: 12 }}>{t('openweb.hint')}</div>
            )}
        </div>
    );
}
