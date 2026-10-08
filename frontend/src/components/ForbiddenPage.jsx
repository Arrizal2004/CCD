import { useT } from '../i18n';

// Ditampilkan kalau alamat halaman admin dibuka oleh akun yang tidak berhak (mis. mahasiswa mengetik /users).
// Backend tetap menolak datanya; halaman ini hanya supaya yang tampil bukan pesan error mentah.
export default function ForbiddenPage({ need = 'admin', role, onBack }) {
    const t = useT();
    return (
        <div style={{ maxWidth: 480, margin: '0 auto', padding: '72px 20px', textAlign: 'center', boxSizing: 'border-box' }}>
            <div style={{ fontFamily: 'var(--fmono)', fontSize: 64, fontWeight: 700, lineHeight: 1, color: 'var(--red)' }}>403</div>
            <div style={{ fontFamily: 'var(--fmono)', fontSize: 13, letterSpacing: '0.2em', textTransform: 'uppercase', color: 'var(--text3)', margin: '10px 0 20px' }}>Forbidden</div>
            <div style={{ fontSize: 13, color: 'var(--text2)', lineHeight: 1.6, marginBottom: 24 }}>
                {t('forbidden.body', { who: t(`role.${need}`), role: t(`role.${role}`) })}
            </div>
            <button onClick={onBack}
                style={{ padding: '9px 20px', borderRadius: 8, background: 'var(--cyan)', color: '#000', fontSize: 13, fontWeight: 600, border: 'none', cursor: 'pointer' }}>
                {t('forbidden.back')}
            </button>
        </div>
    );
}
