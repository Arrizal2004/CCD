import { useBranding, logoLetter, logoUrl } from '../branding';

// Logo institusi dari Pengaturan Sistem, atau huruf pertama nama singkat kalau belum ada logo.
export default function BrandLogo({ size = 26, radius = 6, shadow, branding }) {
    const current = useBranding();
    const b = branding || current;
    const url = logoUrl(b);
    const box = { width: size, height: size, borderRadius: radius, flexShrink: 0, boxShadow: shadow };
    if (url) {
        return <img src={url} alt="" style={{ ...box, objectFit: 'contain', background: 'var(--bg-card2)' }} />;
    }
    return (
        <div style={{ ...box, background: 'linear-gradient(135deg,var(--cyan),var(--blue))', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: Math.round(size * 0.48), fontWeight: 800, color: '#000' }}>
            {logoLetter(b)}
        </div>
    );
}
