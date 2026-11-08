import { useCallback, useState } from 'react';
import { fetchMyExam } from '../api';
import { locale, useT } from '../i18n';
import useLoadEffect from '../useLoadEffect';
import { appTimeZone } from '../sysconfig';

const fmt = (iso) => {
    try { return new Date(iso).toLocaleString(locale(), { timeZone: appTimeZone(), weekday: 'short', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }); }
    catch { return iso; }
};

/** Spanduk untuk mahasiswa: ujian kelasnya sedang berlangsung atau akan segera dimulai. Dimuat ulang tiap menit. */
export default function ExamBanner({ onLiveChange }) {
    const t = useT();
    const [exam, setExam] = useState(null);
    const load = useCallback(async () => {
        try {
            const r = await fetchMyExam();
            setExam(r.exam);
            onLiveChange?.(!!r.exam?.live);
        } catch { /* spanduk opsional */ }
    }, [onLiveChange]);
    useLoadEffect(load, 60000);
    if (!exam) return null;
    return (
        <div role="status" data-testid="exam-banner" style={{
            padding: '10px 14px', borderRadius: 8, marginBottom: 12, fontSize: 13, lineHeight: 1.5,
            background: exam.live ? 'var(--yellow-glow)' : 'var(--cyan-glow)', border: `1px solid ${exam.live ? 'var(--yellow)' : 'var(--cyan)'}`,
            color: exam.live ? 'var(--yellow)' : 'var(--cyan)',
        }}>
            {exam.live ? t('exam.bannerLive', { name: exam.name, end: fmt(exam.ends_at) }) : t('exam.bannerSoon', { name: exam.name, start: fmt(exam.starts_at) })}
        </div>
    );
}
