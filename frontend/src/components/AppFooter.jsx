import { useBranding } from '../branding';
import { useT } from '../i18n';

// Footer tipis. Nama institusi dari Pengaturan Sistem; nama proyek tetap disebut sebagai atribusi
// open source walaupun sistemnya diberi nama lain.
export default function AppFooter() {
    const b = useBranding();
    const t = useT();
    const owner = b.institution || b.name;
    const based = b.name === 'Campus Cloud Dashboard' ? t('footer.license') : t('footer.based');
    return (
        <footer style={{
            borderTop: '1px solid var(--border)',
            background: 'var(--bg-panel)',
            padding: '10px 20px',
            textAlign: 'center',
            opacity: 0.7
        }}>
            <span style={{
                fontSize: 11,
                color: 'var(--text3)',
                fontFamily: 'var(--fmono)',
                letterSpacing: '0.01em'
            }}>
                © {new Date().getFullYear()} {owner} · {based}
            </span>
        </footer>
    );
}
