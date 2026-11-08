import { useEffect, useRef, useState } from 'react';
import { consoleWsUrl, openVmConsole } from '../api';
import { useT } from '../i18n';

// Konsol VNC VM di browser (admin). Backend menyiapkan sesi sekali pakai lalu meneruskan WebSocket ke Proxmox, jadi
// browser tidak perlu akses ke port 8006. noVNC dimuat saat konsol dibuka supaya tidak menambah ukuran halaman.
const btn = { padding: '4px 10px', fontSize: 11, borderRadius: 5, cursor: 'pointer', background: 'transparent', border: '1px solid var(--border)', color: 'var(--text2)' };

export default function VmConsoleModal({ instance, node, vmid, vmName, onClose }) {
    const t = useT();
    const screenRef = useRef(null);
    const frameRef = useRef(null);
    const rfbRef = useRef(null);
    const [state, setState] = useState('connecting');   // connecting | connected | disconnected | failed
    const [error, setError] = useState(null);
    const [scale, setScale] = useState(true);
    const [attempt, setAttempt] = useState(0);

    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                const [{ default: RFB }, session] = await Promise.all([import('@novnc/novnc'), openVmConsole(instance, node, vmid)]);
                if (cancelled || !screenRef.current) return;
                const rfb = new RFB(screenRef.current, consoleWsUrl(session.session),
                    { credentials: { password: session.password }, wsProtocols: ['binary'] });
                rfb.scaleViewport = true;
                rfb.background = '#000';
                rfb.addEventListener('connect', () => { setState('connected'); rfb.focus(); });
                rfb.addEventListener('disconnect', (e) => setState(e.detail?.clean ? 'disconnected' : 'failed'));
                rfb.addEventListener('credentialsrequired', () => rfb.sendCredentials({ password: session.password }));
                rfbRef.current = rfb;
            } catch (e) {
                if (!cancelled) { setError(e?.response?.data?.detail || null); setState('failed'); }
            }
        })();
        return () => {
            cancelled = true;
            try { rfbRef.current?.disconnect(); } catch { /* sudah terputus */ }
            rfbRef.current = null;
        };
    }, [instance, node, vmid, attempt]);

    useEffect(() => { if (rfbRef.current) rfbRef.current.scaleViewport = scale; }, [scale]);

    const reconnect = () => { setError(null); setState('connecting'); setAttempt(a => a + 1); };
    const tone = state === 'connected' ? 'var(--green)' : state === 'connecting' ? 'var(--text3)' : 'var(--red)';

    return (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.65)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 220 }}>
            <div ref={frameRef} role="dialog" aria-label={t('console.title', { name: vmName || vmid })}
                style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', borderRadius: 10, width: '96vw', height: '92vh', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px', borderBottom: '1px solid var(--border)', flexWrap: 'wrap' }}>
                    <div style={{ flex: 1, minWidth: 160 }}>
                        <div style={{ fontSize: 14, fontWeight: 600 }}>{t('console.title', { name: vmName || vmid })}</div>
                        <div style={{ fontSize: 11, color: tone }} data-testid="console-state">{t(`console.${state}`)}</div>
                    </div>
                    <button type="button" style={btn} disabled={state !== 'connected'} onClick={() => rfbRef.current?.sendCtrlAltDel()}>{t('console.ctrlAltDel')}</button>
                    <label style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 12, color: 'var(--text2)' }}>
                        <input type="checkbox" checked={scale} onChange={e => setScale(e.target.checked)} /> {t('console.scale')}
                    </label>
                    <button type="button" style={btn} onClick={() => frameRef.current?.requestFullscreen?.()}>{t('console.fullscreen')}</button>
                    {(state === 'disconnected' || state === 'failed') && <button type="button" style={{ ...btn, borderColor: 'var(--cyan)', color: 'var(--cyan)' }} onClick={reconnect}>{t('console.reconnect')}</button>}
                    <button type="button" style={btn} onClick={onClose}>{t('console.close')}</button>
                </div>
                {state === 'failed' && (
                    <div style={{ background: 'var(--red-glow)', borderBottom: '1px solid var(--red)', padding: '6px 12px', color: 'var(--red)', fontSize: 12 }}>
                        ⚠ {error || t('console.failed')}
                    </div>
                )}
                <div ref={screenRef} data-testid="console-screen" style={{ flex: 1, minHeight: 0, background: '#000' }} />
                <div style={{ fontSize: 11, color: 'var(--text3)', padding: '4px 12px', borderTop: '1px solid var(--border)' }}>{t('console.note')}</div>
            </div>
        </div>
    );
}
