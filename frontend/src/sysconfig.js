import { useSyncExternalStore } from 'react';
import { fetchSystemConfig } from './api';
import { t } from './i18n';

// Konfigurasi untuk pengguna yang sudah login: pengumuman aktif, masa sewa bawaan, kategori tiket,
// pilihan OS (beserta logonya) di form Infra Request, dan zona waktu.
// Dimuat setelah login dan diperbarui tiap 5 menit selama dashboard terbuka.
const DEFAULT = {
    announcement: null, default_vm_lease_days: null,
    ticket_categories: ['REMOTE_ISSUE', 'PERFORMANCE', 'RESOURCE_REQUEST', 'LEASE_EXTENSION', 'OTHERS'].map(key => ({ key, label: '' })),
    vps_os_options: ['Windows', 'Ubuntu'],
    os_logos: {},                 // {nama OS: versi logo} untuk OS yang punya logo
    timezone: 'Asia/Jakarta',
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

// Zona waktu untuk semua waktu yang tampil (diatur di Pengaturan Sistem).
export const appTimeZone = () => state.timezone || DEFAULT.timezone;

// Alamat gambar logo sebuah OS, atau null kalau OS itu belum punya logo.
export const osLogoUrl = (name, logos = state.os_logos) =>
    (logos?.[name] ? `/api/v1/system/os-logo?name=${encodeURIComponent(name)}&v=${logos[name]}` : null);

// Label kategori: label dari Pengaturan Sistem, atau label bawaan yang diterjemahkan.
export function categoryLabel(key, categories = state.ticket_categories) {
    const c = categories.find(x => x.key === key);
    if (c?.label) return c.label;
    const builtin = t(`cat.${key}`);
    return builtin === `cat.${key}` ? key : builtin;
}
