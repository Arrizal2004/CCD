import { useEffect, useMemo, useState } from 'react';
import { locale, useT } from '../i18n';
import { useSysConfig } from '../sysconfig';

// Tanggal dan jam berjalan di header, dalam zona waktu dari Pengaturan Sistem. Pratinjau zona waktu
// di halaman Sistem memakai komponen yang sama dengan `timeZone` dari isian yang belum disimpan.
export default function Clock({ timeZone, showDate = true, style }) {
    useT();                                   // ikut berganti saat bahasa berganti
    const configured = useSysConfig().timezone;
    const [now, setNow] = useState(() => Date.now());
    useEffect(() => {
        const id = setInterval(() => setNow(Date.now()), 1000);
        return () => clearInterval(id);
    }, []);

    const lang = locale();
    const zone = timeZone || configured;
    const fmt = useMemo(() => {
        const make = (opts) => { try { return new Intl.DateTimeFormat(lang, { timeZone: zone, ...opts }); } catch { return new Intl.DateTimeFormat(lang, opts); } };
        return {
            date: make({ weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' }),
            time: make({ hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' }),
            zone: make({ timeZoneName: 'short' }),
            full: make({ dateStyle: 'full', timeStyle: 'long' }),
        };
    }, [lang, zone]);

    const at = new Date(now);
    const zoneName = fmt.zone.formatToParts(at).find(p => p.type === 'timeZoneName')?.value || zone;
    return (
        <span role="timer" aria-live="off" title={`${fmt.full.format(at)} (${zone})`}
            style={{ display: 'inline-flex', alignItems: 'baseline', gap: 6, fontFamily: 'var(--fmono)', fontSize: 11, color: 'var(--text2)', whiteSpace: 'nowrap', ...style }}>
            {showDate && <span className="ccd-hide-mobile" style={{ color: 'var(--text3)' }}>{fmt.date.format(at)}</span>}
            <span style={{ color: 'var(--text)', fontVariantNumeric: 'tabular-nums' }}>{fmt.time.format(at)}</span>
            <span className="ccd-hide-mobile" style={{ color: 'var(--text3)' }}>{zoneName}</span>
        </span>
    );
}
