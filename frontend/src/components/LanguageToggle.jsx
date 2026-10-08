import { LANGUAGES, currentLang, setLanguage, useT } from '../i18n';

// Tombol ID / EN. Pilihan disimpan di browser dan menimpa bahasa bawaan sistem.
export default function LanguageToggle({ style }) {
    const t = useT();
    const lang = currentLang();
    return (
        <div role="group" aria-label={t('header.language')} title={t('header.language')}
            style={{ display: 'flex', border: '1px solid var(--border)', borderRadius: 6, overflow: 'hidden', flexShrink: 0, ...style }}>
            {LANGUAGES.map(([code, label]) => (
                <button key={code} onClick={() => setLanguage(code)} aria-pressed={lang === code}
                    style={{ padding: '4px 7px', fontSize: 10, fontFamily: 'var(--fmono)', cursor: 'pointer', border: 'none',
                        background: lang === code ? 'var(--cyan-glow)' : 'var(--bg-hover)', color: lang === code ? 'var(--cyan)' : 'var(--text3)', fontWeight: lang === code ? 700 : 400 }}>
                    {label}
                </button>
            ))}
        </div>
    );
}
