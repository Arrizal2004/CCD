import { useSyncExternalStore } from 'react';
import { fetchSystemConfig } from './api';
import { t } from './i18n';

// Konfigurasi untuk pengguna yang sudah login: pengumuman aktif, masa sewa bawaan, kategori tiket,
// dan pilihan OS di form Infra Request.
// Dimuat setelah login dan diperbarui tiap 5 menit selama dashboard terbuka.
const DEFAULT = {
    announcement: null, default_vm_lease_days: null,
    ticket_categories: ['REMOTE_ISSUE', 'PERFORMANCE', 'RESOURCE_REQUEST', 'LEASE_EXTENSION', 'OTHERS'].map(key => ({ key, label: '' })),
    vps_os_options: ['Windows', 'Ubuntu'],
};
let state = DEFAULT;
const listeners = new Set();

export function loadSysConfig() {
    return fetchSystemConfig()
        .then(c => { state = { ...DEFAULT, ...c }; listeners.forEach(fn => fn()); })
        .catch(() => {});
}

export function useSysConfig() {
    return useSyncExternalStore(fn => { listeners.add(fn); return () => listeners.delete(fn); }, () => state);
}

// Ikon pilihan OS dari namanya; nama yang tidak dikenali memakai ikon cakram.
export function osIcon(name = '') {
    if (/windows/i.test(name)) return '🪟';
    if (/bsd/i.test(name)) return '😈';
    if (/linux|ubuntu|debian|suse|fedora|rocky|alma|cent ?os|red ?hat|rhel|kali|arch|mint|manjaro|alpine|oracle/i.test(name)) return '🐧';
    return '💿';
}

// Label kategori: label dari Pengaturan Sistem, atau label bawaan yang diterjemahkan.
export function categoryLabel(key, categories = state.ticket_categories) {
    const c = categories.find(x => x.key === key);
    if (c?.label) return c.label;
    const builtin = t(`cat.${key}`);
    return builtin === `cat.${key}` ? key : builtin;
}
