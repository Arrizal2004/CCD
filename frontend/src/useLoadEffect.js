import { useEffect } from 'react';

// Jalankan `load` saat komponen tampil dan setiap `load` berubah (mis. filter berganti), dan opsional berulang
// tiap `intervalMs`. Pemanggilan pertama ditunda satu microtask supaya setState di dalam `load` tidak berjalan
// sinkron di badan effect; kalau komponen sudah dilepas sebelum itu, pemanggilan dibatalkan.
export default function useLoadEffect(load, intervalMs = 0, enabled = true) {
    useEffect(() => {
        if (!enabled) return undefined;
        let alive = true;
        Promise.resolve().then(() => { if (alive) load(); });
        const id = intervalMs ? setInterval(load, intervalMs) : null;
        return () => { alive = false; if (id) clearInterval(id); };
    }, [load, intervalMs, enabled]);
}
