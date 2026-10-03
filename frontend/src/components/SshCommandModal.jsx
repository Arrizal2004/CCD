import { useState } from 'react';
import { copyText } from '../clipboard';

const OS_LIST = [
    { id: 'linux', label: 'Linux' },
    { id: 'macos', label: 'macOS' },
    { id: 'windows', label: 'Windows' },
];

function detectOs() {
    const ua = navigator.userAgent || '';
    if (/Windows/i.test(ua)) return 'windows';
    if (/Macintosh|Mac OS X/i.test(ua) && !/iPhone|iPad/i.test(ua)) return 'macos';
    return 'linux';
}

const mono = { fontFamily: 'var(--fmono)', color: 'var(--text2)' };

function Block({ text }) {
    const [copied, setCopied] = useState(false);
    const copy = async () => {
        setCopied(await copyText(text));
        setTimeout(() => setCopied(false), 1500);
    };
    return (
        <div style={{ position: 'relative', marginTop: 6 }}>
            <pre style={{ margin: 0, padding: '10px 64px 10px 12px', borderRadius: 6, background: 'var(--bg-card2)', border: '1px solid var(--border)', fontFamily: 'var(--fmono)', fontSize: 12, color: 'var(--text)', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{text}</pre>
            <button onClick={copy}
                style={{ position: 'absolute', top: 6, right: 6, padding: '3px 10px', fontSize: 11, borderRadius: 5, cursor: 'pointer', background: 'var(--bg-panel)', border: '1px solid var(--cyan)', color: 'var(--cyan)' }}>
                {copied ? 'Tersalin' : 'Salin'}
            </button>
        </div>
    );
}

function Step({ n, title, children }) {
    return (
        <div style={{ display: 'flex', gap: 12, marginBottom: 16 }}>
            <div style={{ flexShrink: 0, width: 24, height: 24, borderRadius: '50%', border: '1px solid var(--cyan)', color: 'var(--cyan)', fontSize: 12, fontFamily: 'var(--fmono)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>{n}</div>
            <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)', marginBottom: 2 }}>{title}</div>
                <div style={{ fontSize: 12, color: 'var(--text2)', lineHeight: 1.6 }}>{children}</div>
            </div>
        </div>
    );
}

export default function SshCommandModal({ vm, cfg, onClose }) {
    const [os, setOs] = useState(detectOs);
    const ip = vm.manual_ip;
    const port = vm.ssh_port || 22;
    const alias = 'ccd-' + String(vm.name || vm.vmid).toLowerCase().replace(/[^a-z0-9-]/g, '-');
    const oneLine = `ssh -J ${cfg.user}@${cfg.host}:${cfg.port}${port !== 22 ? ` -p ${port}` : ''} AKUN-OS@${ip}`;
    const sshConfig = [
        'Host ccd-bastion',
        `    HostName ${cfg.host}`,
        `    Port ${cfg.port}`,
        `    User ${cfg.user}`,
        '    IdentityFile ~/.ssh/id_ed25519',
        '    IdentitiesOnly yes',
        '',
        `Host ${alias}`,
        `    HostName ${ip}`,
        ...(port !== 22 ? [`    Port ${port}`] : []),
        '    User AKUN-OS',
        '    ProxyJump ccd-bastion',
        '    IdentityFile ~/.ssh/id_ed25519',
        '    IdentitiesOnly yes',
    ].join('\n');

    const keygen = {
        linux: 'ssh-keygen -t ed25519 -C "nama@laptop"\ncat ~/.ssh/id_ed25519.pub',
        macos: 'ssh-keygen -t ed25519 -C "nama@mac"\ncat ~/.ssh/id_ed25519.pub | pbcopy',
        windows: 'ssh-keygen -t ed25519 -C "nama@laptop"\nGet-Content $env:USERPROFILE\\.ssh\\id_ed25519.pub | Set-Clipboard',
    }[os];
    const openConfig = {
        linux: 'mkdir -p ~/.ssh && chmod 700 ~/.ssh\nnano ~/.ssh/config\nchmod 600 ~/.ssh/config',
        macos: 'mkdir -p ~/.ssh && chmod 700 ~/.ssh\nnano ~/.ssh/config\nchmod 600 ~/.ssh/config',
        windows: 'New-Item -ItemType Directory -Force $env:USERPROFILE\\.ssh | Out-Null\n$f = "$env:USERPROFILE\\.ssh\\config"\nif (!(Test-Path $f)) { New-Item -ItemType File $f | Out-Null }\nnotepad $f',
    }[os];
    const terminal = { linux: 'Terminal', macos: 'aplikasi Terminal', windows: 'PowerShell (bukan Command Prompt)' }[os];

    return (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 200, padding: 12 }} onClick={onClose}>
            <div style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', borderRadius: 10, width: 620, maxWidth: '100%', maxHeight: '90vh', overflow: 'auto', padding: 20 }} onClick={e => e.stopPropagation()}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                    <div>
                        <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text)' }}>SSH ke {vm.name || vm.vmid}</div>
                        <div style={{ fontSize: 11, color: 'var(--text3)', fontFamily: 'var(--fmono)' }}>{ip}:{port} lewat bastion {cfg.host}:{cfg.port}</div>
                    </div>
                    <button onClick={onClose} style={{ background: 'transparent', border: 'none', color: 'var(--text3)', fontSize: 18, cursor: 'pointer' }}>×</button>
                </div>

                <div role="tablist" style={{ display: 'flex', gap: 6, marginBottom: 16, borderBottom: '1px solid var(--border)' }}>
                    {OS_LIST.map(o => (
                        <button key={o.id} role="tab" aria-selected={os === o.id} onClick={() => setOs(o.id)}
                            style={{ padding: '8px 14px', fontSize: 12, cursor: 'pointer', background: 'transparent', border: 'none', borderBottom: `2px solid ${os === o.id ? 'var(--cyan)' : 'transparent'}`, color: os === o.id ? 'var(--cyan)' : 'var(--text3)', fontWeight: os === o.id ? 600 : 400 }}>
                            {o.label}
                        </button>
                    ))}
                </div>

                <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 14 }}>
                    Langkah 1 sampai 3 cukup dilakukan sekali per laptop. Jalankan perintah di {terminal}.
                </div>

                <Step n="1" title="Buat SSH key">
                    Tekan Enter untuk lokasi bawaan, lalu isi passphrase. Perintah kedua menampilkan atau menyalin public key.
                    <Block text={keygen} />
                    {os === 'windows' && <div style={{ marginTop: 6 }}>Kalau <span style={mono}>ssh-keygen</span> tidak dikenali, pasang <b>OpenSSH Client</b> lewat Settings, Optional features.</div>}
                </Step>

                <Step n="2" title="Daftarkan public key di dashboard">
                    Buka <b>Profil</b> (badge peran di kanan atas), bagian <b>SSH Key</b>, lalu tempel isi file <span style={mono}>id_ed25519.pub</span>. Jangan tempel private key (file tanpa <span style={mono}>.pub</span>).
                </Step>

                <Step n="3" title="Tambahkan ke file ~/.ssh/config">
                    Buka file config, tempel blok di bawah di bagian akhir, lalu ganti <b>AKUN-OS</b> dengan akun Anda di dalam VM.
                    <Block text={openConfig} />
                    <Block text={sshConfig} />
                    {os === 'windows' && <div style={{ marginTop: 6 }}>Nama file harus <span style={mono}>config</span>, bukan <span style={mono}>config.txt</span>.</div>}
                </Step>

                <Step n="4" title="Hubungkan">
                    <Block text={`ssh ${alias}`} />
                    {cfg.host_fingerprint && (
                        <div style={{ marginTop: 6 }}>
                            Saat pertama kali, pastikan fingerprint yang muncul adalah <span style={{ ...mono, overflowWrap: 'anywhere' }}>{cfg.host_fingerprint}</span>, lalu ketik <span style={mono}>yes</span>. Kalau berbeda, jangan lanjutkan dan hubungi admin.
                        </div>
                    )}
                    {os === 'windows' && (
                        <div style={{ marginTop: 6 }}>
                            Kalau muncul error <span style={mono}>CreateProcessW failed</span>, ganti baris <span style={mono}>ProxyJump ccd-bastion</span> dengan:
                            <Block text={'    ProxyCommand C:\\Windows\\System32\\OpenSSH\\ssh.exe -W %h:%p ccd-bastion'} />
                        </div>
                    )}
                </Step>

                <div style={{ borderTop: '1px solid var(--border)', paddingTop: 14, fontSize: 12, color: 'var(--text2)', lineHeight: 1.6 }}>
                    <div style={{ marginBottom: 6 }}>Tanpa file config, bisa juga dengan satu perintah:</div>
                    <Block text={oneLine} />
                    <div style={{ marginTop: 10, color: 'var(--text3)' }}>
                        Nama <span style={mono}>{alias}</span> juga dipakai untuk <span style={mono}>scp file {alias}:~/</span>, <span style={mono}>sftp {alias}</span>, dan VS Code Remote-SSH.
                        Kalau ditolak dengan <span style={mono}>Permission denied (publickey)</span>, key belum terdaftar di Profil.
                    </div>
                </div>
            </div>
        </div>
    );
}
