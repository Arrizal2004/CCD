import { useBranding } from '../branding';
import { useT } from '../i18n';
import BrandLogo from './BrandLogo';

// "Tentang Sistem": identitas deployment ini (dari Pengaturan Sistem) dan kredit proyek open source
// yang dipakainya. Kredit proyek sengaja tidak bisa diubah dari dashboard.
const PROJECT = {
    name: 'Campus Cloud Dashboard',
    version: 'v1.0.0',
    author: 'Arrizal Rizki Yuwana',
    year: '2026',
    license: 'MIT',
    repo: 'https://github.com/Arrizal2004/CCD',
};

const sectionTitle = { fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.1em', color: 'var(--text3)', marginBottom: 8, fontFamily: 'var(--fmono)' };

function Item({ label, value }) {
    return (
        <div style={{ background: 'var(--bg-hover)', border: '1px solid var(--border)', borderRadius: 8, padding: '9px 12px', minWidth: 0 }}>
            <div style={{ fontSize: 9, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text3)', marginBottom: 3 }}>{label}</div>
            <div style={{ fontSize: 12, color: 'var(--text)', fontWeight: 500, lineHeight: 1.4, overflowWrap: 'anywhere' }}>{value}</div>
        </div>
    );
}

export default function AboutModal({ onClose }) {
    const b = useBranding();
    const t = useT();
    const custom = b.name !== PROJECT.name;

    return (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.75)', zIndex: 1200, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16, animation: 'aboutFade 0.2s ease-out' }}
            onClick={onClose}>
            <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border-light)', borderRadius: 16, width: 'min(560px,95vw)', maxHeight: '92vh', overflow: 'auto', boxShadow: '0 24px 64px rgba(0,0,0,0.5)', animation: 'aboutPop 0.22s ease-out' }}
                onClick={e => e.stopPropagation()}>

                <div style={{ position: 'relative', padding: '24px 24px 20px', background: 'linear-gradient(135deg, var(--bg-card2) 0%, var(--bg-panel) 100%)', borderBottom: '1px solid var(--border)' }}>
                    <button onClick={onClose} style={{ position: 'absolute', top: 14, right: 14, width: 28, height: 28, borderRadius: 6, background: 'var(--bg-hover)', border: '1px solid var(--border)', color: 'var(--text2)', cursor: 'pointer', fontSize: 13 }}>✕</button>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 14, paddingRight: 30 }}>
                        <BrandLogo size={52} radius={14} shadow="0 8px 24px var(--cyan-glow)" />
                        <div style={{ minWidth: 0 }}>
                            <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--text)', letterSpacing: '-0.01em', overflowWrap: 'anywhere' }}>{b.name}</div>
                            <div style={{ fontSize: 11, color: 'var(--text3)', fontFamily: 'var(--fmono)', marginTop: 3 }}>{b.institution || b.tagline}</div>
                        </div>
                    </div>
                </div>

                <div style={{ padding: '20px 24px 22px' }}>
                    <div style={sectionTitle}>{t('about.deployment')}</div>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 10, marginBottom: 20 }}>
                        <Item label={t('about.name')} value={b.name} />
                        {b.institution && <Item label={t('about.institution')} value={b.institution} />}
                        {b.tagline && <Item label={t('about.tagline')} value={b.tagline} />}
                    </div>

                    <div style={sectionTitle}>{t('about.project')}</div>
                    <div style={{ fontSize: 13, lineHeight: 1.65, color: 'var(--text2)', marginBottom: 14 }}>
                        {custom ? <><b style={{ color: 'var(--text)' }}>{b.name}</b> {t('about.runsOn')} </> : null}
                        <b style={{ color: 'var(--text)' }}>{PROJECT.name}</b> {PROJECT.version}, {t('about.desc')}
                    </div>
                    <div style={{ background: 'var(--bg-card2)', border: '1px solid var(--border)', borderLeft: '3px solid var(--cyan)', borderRadius: 10, padding: '12px 14px', fontSize: 12.5, lineHeight: 1.7, color: 'var(--text)' }}>
                        {t('about.credit', { author: PROJECT.author, year: PROJECT.year, license: PROJECT.license })}{' '}
                        <a href={PROJECT.repo} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--cyan)' }}>{t('about.source')}</a>
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
