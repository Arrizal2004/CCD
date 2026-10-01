// "Tentang Sistem" — system profile modal for academic final defense presentation.
// Branding metadata is centralized here so it stays consistent app-wide.

const META = {
    name: 'Campus Cloud Dashboard',
    version: 'v1.0.0 (Prototype)',
    developer: 'Arrizal Rizki Yuwana',
    purpose: 'Prototype Tugas Akhir',
    department: 'Jurusan Teknologi Rekayasa Internet',
    faculty: 'Sekolah Vokasi',
    institution: 'Universitas Gadjah Mada (UGM)',
    year: '2026',
};

// UGM palette accent — official "biru tua" / gold.
const UGM_BLUE = '#0b2a5b';
const UGM_GOLD = '#f0a500';

export default function AboutModal({ onClose }) {
    return (
        <div style={{
            position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.75)', zIndex: 1200,
            display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20,
            animation: 'aboutFade 0.2s ease-out'
        }}
            onClick={onClose}>
            <div style={{
                background: 'var(--bg-card)', border: '1px solid var(--border-light)',
                borderRadius: 16, width: 'min(560px,95vw)', overflow: 'hidden',
                boxShadow: '0 24px 64px rgba(0,0,0,0.5)', animation: 'aboutPop 0.22s ease-out'
            }}
                onClick={e => e.stopPropagation()}>

                {/* Hero header with UGM accent gradient */}
                <div style={{
                    position: 'relative',
                    padding: '28px 28px 24px',
                    background: `linear-gradient(135deg, ${UGM_BLUE} 0%, #143d80 60%, var(--cyan2) 140%)`,
                    overflow: 'hidden'
                }}>
                    {/* Subtle grid texture */}
                    <div style={{
                        position: 'absolute', inset: 0, opacity: 0.08,
                        backgroundImage: 'linear-gradient(#fff 1px, transparent 1px), linear-gradient(90deg, #fff 1px, transparent 1px)',
                        backgroundSize: '24px 24px'
                    }} />
                    {/* Gold glow */}
                    <div style={{
                        position: 'absolute', top: -40, right: -20, width: 180, height: 180,
                        background: UGM_GOLD, borderRadius: '50%', filter: 'blur(70px)', opacity: 0.25
                    }} />

                    <button onClick={onClose} style={{
                        position: 'absolute', top: 14, right: 14, width: 28, height: 28, borderRadius: 6,
                        background: 'rgba(255,255,255,0.12)', border: '1px solid rgba(255,255,255,0.2)',
                        color: '#fff', cursor: 'pointer', fontSize: 13, zIndex: 2
                    }}>✕</button>

                    <div style={{ position: 'relative', zIndex: 1, display: 'flex', alignItems: 'center', gap: 14 }}>
                        <div style={{
                            width: 56, height: 56, borderRadius: 14, flexShrink: 0,
                            background: 'linear-gradient(135deg,var(--cyan),var(--blue))',
                            display: 'flex', alignItems: 'center', justifyContent: 'center',
                            fontSize: 26, fontWeight: 800, color: '#000',
                            boxShadow: '0 8px 24px rgba(0,229,255,0.35)'
                        }}>C</div>
                        <div>
                            <div style={{ fontSize: 19, fontWeight: 700, color: '#fff', letterSpacing: '-0.01em' }}>
                                {META.name} <span style={{ color: UGM_GOLD, fontWeight: 600, fontSize: 15 }}>{META.version}</span>
                            </div>
                            <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.7)', fontFamily: 'var(--fmono)', marginTop: 3 }}>
                                Clientless Campus Cloud · Proxmox VE + Guacamole + RBAC + Tailscale
                            </div>
                        </div>
                    </div>
                </div>

                {/* Body */}
                <div style={{ padding: '22px 28px 26px' }}>
                    <div style={{ fontSize: 13, lineHeight: 1.65, color: 'var(--text2)', marginBottom: 18 }}>
                        A self-hosted, open-source clientless access platform for campus lab VMs —
                        Proxmox VE virtualization, Apache Guacamole browser-based remote desktop, RBAC
                        self-service, and a Tailscale-secured perimeter.
                    </div>

                    {/* Project attribution card */}
                    <div style={{
                        background: 'var(--bg-card2)', border: '1px solid var(--border)',
                        borderLeft: `3px solid ${UGM_GOLD}`, borderRadius: 10,
                        padding: '14px 16px', marginBottom: 18
                    }}>
                        <div style={{
                            fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.1em',
                            color: 'var(--text3)', marginBottom: 6, fontFamily: 'var(--fmono)'
                        }}>
                            Atribut Proyek
                        </div>
                        <div style={{ fontSize: 12.5, lineHeight: 1.7, color: 'var(--text)' }}>
                            Dikembangkan oleh <strong>{META.developer}</strong> sebagai prototipe{' '}
                            <strong>Tugas Akhir</strong> pada {META.department}, {META.faculty},{' '}
                            {META.institution}, tahun {META.year}.
                        </div>
                    </div>

                    {/* Meta grid */}
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                        {[
                            { label: 'Pengembang', value: META.developer },
                            { label: 'Tahun', value: META.year },
                            { label: 'Program Studi', value: META.department },
                            { label: 'Institusi', value: `${META.faculty}, ${META.institution}` },
                        ].map(item => (
                            <div key={item.label} style={{
                                background: 'var(--bg-hover)', border: '1px solid var(--border)',
                                borderRadius: 8, padding: '9px 12px'
                            }}>
                                <div style={{
                                    fontSize: 9, textTransform: 'uppercase', letterSpacing: '0.08em',
                                    color: 'var(--text3)', marginBottom: 3
                                }}>{item.label}</div>
                                <div style={{ fontSize: 11.5, color: 'var(--text)', fontWeight: 500, lineHeight: 1.4 }}>
                                    {item.value}
                                </div>
                            </div>
                        ))}
                    </div>

                    <div style={{
                        marginTop: 20, paddingTop: 14, borderTop: '1px solid var(--border)',
                        textAlign: 'center', fontSize: 10.5, color: 'var(--text3)', fontFamily: 'var(--fmono)'
                    }}>
                        © {META.year} {META.name} · {META.purpose}
                    </div>
                </div>
            </div>

            <style>{`
                @keyframes aboutFade{from{opacity:0}to{opacity:1}}
                @keyframes aboutPop{from{opacity:0;transform:scale(0.96) translateY(8px)}to{opacity:1;transform:scale(1) translateY(0)}}
            `}</style>
        </div>
    );
}
