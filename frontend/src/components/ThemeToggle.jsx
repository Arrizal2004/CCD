import { toggleTheme, useTheme } from '../theme';
import { useT } from '../i18n';

// Tombol mode gelap/terang, di header dashboard dan di halaman login.
export default function ThemeToggle({ style }) {
    const theme = useTheme();
    const t = useT();
    return (
        <button onClick={toggleTheme} title={t('header.theme')} aria-label={t('header.theme')}
            style={{ width: 30, height: 30, borderRadius: 6, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 14, background: 'var(--bg-hover)', border: '1px solid var(--border)', color: 'var(--text2)', cursor: 'pointer', flexShrink: 0, ...style }}>
            {theme === 'dark' ? '☀️' : '🌙'}
        </button>
    );
}
