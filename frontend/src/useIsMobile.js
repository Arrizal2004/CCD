import { useEffect, useState } from 'react';

// Sama dengan batas @media (max-width: 640px) di index.css.
const QUERY = '(max-width: 640px)';

export default function useIsMobile() {
    const [isMobile, setIsMobile] = useState(() => window.matchMedia(QUERY).matches);
    useEffect(() => {
        const mq = window.matchMedia(QUERY);
        const onChange = (e) => setIsMobile(e.matches);
        mq.addEventListener('change', onChange);
        return () => mq.removeEventListener('change', onChange);
    }, []);
    return isMobile;
}
