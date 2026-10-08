import axios from 'axios';
import { currentLang } from './i18n';

const BASE = import.meta.env.VITE_API_URL || '';

export const api = axios.create({ baseURL: BASE, timeout: 10000 });

// Instance khusus untuk panggilan yang menunggu respons dari agent (polling Redis)
// Timeout 35 detik karena backend poll max 30 detik + network overhead
export const apiAgent = axios.create({ baseURL: BASE, timeout: 35000 });

// Timeout panjang untuk operasi yang bisa memakan 90-180 detik (post-config, auto-extend, create VM)
export const apiLong = axios.create({ baseURL: BASE, timeout: 200000 });

const attachToken = config => {
    const token = localStorage.getItem('hv_token');
    if (token) config.headers.Authorization = `Bearer ${token}`;
    return config;
};
// Bahasa pilihan pengguna: backend memakainya untuk pesan galat dan validasi.
const attachLanguage = config => {
    config.headers['Accept-Language'] = currentLang();
    return config;
};
for (const instance of [api, apiAgent, apiLong, axios]) instance.interceptors.request.use(attachLanguage);
api.interceptors.request.use(attachToken);
apiAgent.interceptors.request.use(attachToken);
apiLong.interceptors.request.use(attachToken);

// ── Penanganan sesi habis (token expired / invalid) ──────────────────────────
// Saat backend membalas 401 atau token kedaluwarsa, bersihkan kredensial lalu
// kirim event global supaya App bisa menampilkan notifikasi & melempar ke login.
// Guard agar tidak memicu berkali-kali bila banyak request gagal bersamaan.
let sessionExpiredFired = false;
export const resetSessionExpired = () => { sessionExpiredFired = false; };

const handleAuthError = error => {
    const status = error?.response?.status;
    // Password direset admin: hanya ganti password yang boleh dilakukan. App menampilkan layarnya.
    if (status === 403 && error?.response?.headers?.['x-password-change-required']) {
        window.dispatchEvent(new CustomEvent('hv:must-change-password'));
        return Promise.reject(error);
    }
    const detail = error?.response?.data?.detail || '';
    const expired = status === 401 || /expired|invalid.*token|not authenticated/i.test(detail);
    if (expired && !sessionExpiredFired) {
        sessionExpiredFired = true;
        const hadSession = !!localStorage.getItem('hv_token');
        const guacToken = localStorage.getItem('hv_guac_token');
        if (guacToken) {
            fetch(`/guacamole/api/tokens/${encodeURIComponent(guacToken)}`, { method: 'DELETE' }).catch(() => null);
        }
        localStorage.removeItem('hv_token');
        localStorage.removeItem('hv_user');
        localStorage.removeItem('hv_guac_token');
        localStorage.removeItem('GUAC_AUTH');
        delete axios.defaults.headers.common['Authorization'];
        // Only show "session expired" notice if the user actually had an active session
        if (hadSession) window.dispatchEvent(new CustomEvent('hv:session-expired'));
    }
    return Promise.reject(error);
};

api.interceptors.response.use(r => r, handleAuthError);
apiAgent.interceptors.response.use(r => r, handleAuthError);
apiLong.interceptors.response.use(r => r, handleAuthError);
export const logoutApi = () => api.post('/api/v1/users/logout').catch(() => null);

/**
 * Tambahkan Guacamole token ke hash search params agar Guacamole auto-login.
 * Guacamole membaca $location.search() dan meneruskannya ke authenticate(),
 * sehingga ?token=<tok> di dalam hash bekerja sebagai auto-auth.
 * URL input: /guacamole/#/client/<id>
 * URL output: /guacamole/#/client/<id>?token=<tok>
 */
export const appendGuacToken = (url) => {
    const tok = localStorage.getItem('hv_guac_token');
    if (!tok || !url.includes('#')) return url;
    const hashIdx = url.indexOf('#');
    return url.slice(0, hashIdx + 1) + url.slice(hashIdx + 1) + '?token=' + encodeURIComponent(tok);
};
/**
 * Di perangkat sentuh tidak ada keyboard fisik, sedangkan input bawaan Guacamole adalah "none",
 * sehingga keyboard HP tidak pernah muncul. Sekali per perangkat, aktifkan "Text input" di
 * preferensi Guacamole (localStorage yang sama, karena Guacamole dilayani di origin yang sama).
 * Setelah itu pilihan pengguna di menu Guacamole tidak diubah lagi.
 */
const GUAC_PREFS_KEY = 'GUAC_PREFERENCES';
const GUAC_TOUCH_DEFAULT_APPLIED = 'ccd_guac_touch_input_applied';
export const applyGuacTouchInputDefault = () => {
    try {
        if (!window.matchMedia('(pointer: coarse)').matches) return;
        if (localStorage.getItem(GUAC_TOUCH_DEFAULT_APPLIED)) return;
        let prefs = {};
        try { prefs = JSON.parse(localStorage.getItem(GUAC_PREFS_KEY)) || {}; } catch { prefs = {}; }
        if (typeof prefs !== 'object' || Array.isArray(prefs)) prefs = {};
        if (!prefs.inputMethod || prefs.inputMethod === 'none') {
            localStorage.setItem(GUAC_PREFS_KEY, JSON.stringify({ ...prefs, inputMethod: 'text' }));
        }
        localStorage.setItem(GUAC_TOUCH_DEFAULT_APPLIED, '1');
    } catch { /* localStorage tidak tersedia: biarkan bawaan Guacamole */ }
};
export const fetchSshConfig = () => api.get('/api/v1/ssh-keys/config').then(r => r.data).catch(() => ({ enabled: false }));
export const fetchSshKeys   = () => api.get('/api/v1/ssh-keys').then(r => r.data);
export const addSshKey      = (body) => api.post('/api/v1/ssh-keys', body).then(r => r.data);
export const deleteSshKey   = (id) => api.delete(`/api/v1/ssh-keys/${id}`).then(r => r.data);
export const fetchAllProxmoxVms = () => api.get('/api/v1/proxmox/all-vms').then(r => r.data);
/** Simpan sesi login: token dashboard dan (kalau ada) token Guacamole untuk Connect. */
export const storeSession = (token, guacAuth) => {
    localStorage.setItem('hv_token', token);
    axios.defaults.headers.common['Authorization'] = `Bearer ${token}`;
    if (guacAuth?.authToken) {
        const old = localStorage.getItem('hv_guac_token');
        if (old && old !== guacAuth.authToken) {
            fetch(`/guacamole/api/tokens/${encodeURIComponent(old)}`, { method: 'DELETE' }).catch(() => null);
        }
        localStorage.setItem('hv_guac_token', guacAuth.authToken);
        // Format GUAC_AUTH: localStorageService.setItem(key, authResultObj)
        // yang di-JSON.stringify langsung — bukan dibungkus { [dataSource]: ... }
        localStorage.setItem('GUAC_AUTH', JSON.stringify(guacAuth));
    }
};
// Mengganti password mengakhiri semua sesi lain; sesi ini memakai token baru dari respons.
export const changePassword = (old_password, new_password) =>
    api.post('/api/v1/users/me/change-password', { old_password, new_password }).then(r => {
        if (r.data.access_token) storeSession(r.data.access_token, r.data.guac_auth);
        return r.data;
    });
export const fetchHealthz = () => api.get('/healthz').then(r => r.data).catch(() => ({ status: 'error', checks: {} }));


// Users and VM Assignments
export const fetchUsers    = ()          => api.get('/api/v1/users').then(r => r.data);
export const createUser    = (body)      => api.post('/api/v1/users', body).then(r => r.data);
export const updateUser    = (id, body)  => api.put(`/api/v1/users/${id}`, body).then(r => r.data);
export const deleteUserApi = (id)        => api.delete(`/api/v1/users/${id}`).then(r => r.data);
export const resetUserPassword = (id)    => api.post(`/api/v1/users/${id}/reset-password`).then(r => r.data);
export const fetchPasswordHelp = ()      => api.get('/api/v1/users/password-help').then(r => r.data);
export const dismissPasswordHelp = (id)  => api.post(`/api/v1/users/password-help/${id}/dismiss`).then(r => r.data);
// Publik (halaman login): jawabannya selalu sama, terdaftar atau tidak.
export const requestPasswordHelp = (username, message) =>
    axios.post(`${BASE}/api/v1/users/password-help`, { username, message }).then(r => r.data);
export const fetchUserAssignments = (user_id) => api.get(`/api/v1/users/${user_id}/vm-assignments`).then(r => r.data);
export const assignVm = (user_id, vm_id, host_name, os_account_id = null, vm_name = null) =>
    api.post('/api/v1/users/vm-assignments', { user_id, vm_id, host_name, vm_name, os_account_id }).then(r => r.data);
export const fetchVmOsAccounts = (host_name, vm_id) =>
    api.get(`/api/v1/ssh-creds/vm-os-accounts/${host_name}/${encodeURIComponent(vm_id)}`).then(r => r.data);
// create_in_vm / remove_in_vm menjalankan perintah lewat QEMU Guest Agent, jadi pakai timeout agent.
export const upsertVmOsAccount = (host_name, vm_id, data) =>
    apiAgent.post(`/api/v1/ssh-creds/vm-os-accounts/${host_name}/${encodeURIComponent(vm_id)}`, data).then(r => r.data);
export const updateVmOsAccount = (host_name, vm_id, account_id, data) =>
    api.put(`/api/v1/ssh-creds/vm-os-accounts/${host_name}/${encodeURIComponent(vm_id)}/${account_id}`, data).then(r => r.data);
export const deleteVmOsAccount = (host_name, vm_id, account_id, removeInVm = false) =>
    apiAgent.delete(`/api/v1/ssh-creds/vm-os-accounts/${host_name}/${encodeURIComponent(vm_id)}/${account_id}`,
        { params: removeInVm ? { remove_in_vm: true } : {} }).then(r => r.data);
// Ganti password user di dalam VM lewat QEMU Guest Agent. Body kosong = user Login Connect, password acak.
export const resetVmPassword = (host_name, vm_id, body = {}) =>
    apiAgent.post(`/api/v1/ssh-creds/vm/${host_name}/${encodeURIComponent(vm_id)}/reset-password`, body).then(r => r.data);
export const removeAssignment = (user_id, vm_id) =>
    api.delete(`/api/v1/users/${user_id}/vm-assignments/${vm_id}`).then(r => r.data);

// vSwitch Management — gunakan apiAgent karena backend poll ke agent (max 30s)
export const listVSwitches   = (host_name) => apiAgent.get(`/api/v1/vms/${host_name}/vswitches`).then(r => r.data);
// Host-level vSwitch management (dipakai wizard Create VM & Topology vSwitch Manager)
export const fetchHostExporter  = (host_name) => apiAgent.get(`/api/host/${encodeURIComponent(host_name)}/exporter`).then(r => r.data).catch(() => null);
export const listHostSwitches   = (host_name) => apiAgent.get('/api/host/switches', { params: { host_name } }).then(r => r.data);
export const listHostInterfaces = (host_name) => apiAgent.get('/api/host/interfaces', { params: { host_name } }).then(r => r.data);
export const createHostSwitch   = (host_name, switch_name, switch_type, interface_name = '') =>
    apiAgent.post('/api/host/switches', { switch_name, switch_type, interface_name }, { params: { host_name } }).then(r => r.data);
export const deleteHostSwitch   = (host_name, switch_name) =>
    apiAgent.delete(`/api/host/switches/${encodeURIComponent(switch_name)}`, { params: { host_name } }).then(r => r.data);
export const listNetAdapters = (host_name) => apiAgent.get(`/api/v1/vms/${host_name}/net-adapters`).then(r => r.data);
export const createVSwitch   = (host_name, switch_name, switch_type, net_adapter_name = '') =>
    api.post(`/api/v1/vms/${host_name}/vswitches`, { switch_name, switch_type, net_adapter_name }).then(r => r.data);
export const deleteVSwitch   = (host_name, switch_name) =>
    api.delete(`/api/v1/vms/${host_name}/vswitches/${encodeURIComponent(switch_name)}`).then(r => r.data);

// VM NIC real-time query — poll ke agent, butuh timeout panjang
export const listVmNics = (host_name, vm_name) =>
    apiAgent.get(`/api/v1/vms/${host_name}/${encodeURIComponent(vm_name)}/nics`).then(r => r.data);

// Alternate Throughput Metrics — baca langsung dari Guest OS (SSH/PSDirect)
export const getGuestThroughput = (host_name, vm_id) =>
    apiLong.get(`/api/v1/vms/${host_name}/${encodeURIComponent(vm_id)}/guest-throughput`)
        .then(r => r.data).catch(e => ({ ok: false, reason: e?.response?.data?.detail || e.message }));

// Agent throughput — cache poller exporter :9100 (ringan, untuk polling real-time)
export const getAgentThroughput = (host_name, vm_id) =>
    api.get(`/api/v1/vms/${host_name}/${encodeURIComponent(vm_id)}/agent-throughput`)
        .then(r => r.data).catch(() => ({ ok: false }));

// Exporter rolling buffer — last 240 points (~20 min) dari Redis ZSET
export const fetchExporterBuf = (host_name, vm_id) =>
    api.get(`/api/v1/vms/${host_name}/${encodeURIComponent(vm_id)}/exporter-buf`)
        .then(r => r.data).catch(() => []);

// VM Network Management
export const updateVmNetwork = (host_name, vm_name, action, nic_name = '', switch_name = '', vlan_id = null) =>
    api.post(`/api/v1/vms/${host_name}/${encodeURIComponent(vm_name)}/network`, { action, nic_name, switch_name, vlan_id }).then(r => r.data);

// SSH / VM Credentials
export const getHostSshCreds   = (host_name) => api.get(`/api/v1/ssh-creds/host/${host_name}`).then(r => r.data);
export const upsertHostSshCreds = (host_name, body) => api.put(`/api/v1/ssh-creds/host/${host_name}`, body).then(r => r.data);
export const deleteHostSshCreds = (host_name) => api.delete(`/api/v1/ssh-creds/host/${host_name}`).then(r => r.data);
export const testHostSshCreds   = (host_name) => api.post(`/api/v1/ssh-creds/host/${host_name}/test`).then(r => r.data);
export const getVmOsMap    = (host_name) => api.get(`/api/v1/ssh-creds/vm-os/${host_name}`).then(r => r.data).catch(() => ({}));
export const getVmCreds    = (host_name, vm_id) => api.get(`/api/v1/ssh-creds/vm/${host_name}/${encodeURIComponent(vm_id)}`).then(r => r.data);
export const upsertVmCreds = (host_name, vm_id, body) => api.put(`/api/v1/ssh-creds/vm/${host_name}/${encodeURIComponent(vm_id)}`, body).then(r => r.data);
export const deleteVmCreds = (host_name, vm_id) => api.delete(`/api/v1/ssh-creds/vm/${host_name}/${encodeURIComponent(vm_id)}`).then(r => r.data);
export const testVmCreds   = (host_name, vm_id) => api.post(`/api/v1/ssh-creds/vm/${host_name}/${encodeURIComponent(vm_id)}/test`).then(r => r.data);
export const revealVmPassword = (host_name, vm_id) => api.get(`/api/v1/ssh-creds/vm/${host_name}/${encodeURIComponent(vm_id)}/password`).then(r => r.data);

// Linux VM Automation
export const getLinuxDiskInfo     = (host_name, vm_id) => api.get(`/api/v1/linux-vm/${host_name}/${encodeURIComponent(vm_id)}/disk-info`).then(r => r.data);
export const getLinuxNetworkInfo  = (host_name, vm_id) => api.get(`/api/v1/linux-vm/${host_name}/${encodeURIComponent(vm_id)}/network-info`).then(r => r.data);


// VM Templates
export const listVmTemplates   = () => api.get('/api/v1/vm-templates').then(r => r.data);
export const createVmTemplate  = (body) => api.post('/api/v1/vm-templates', body).then(r => r.data);
export const updateVmTemplate  = (id, body) => api.put(`/api/v1/vm-templates/${id}`, body).then(r => r.data);
export const deleteVmTemplate  = (id) => api.delete(`/api/v1/vm-templates/${id}`).then(r => r.data);

// Create VM — endpoint kini streaming (SSE); dipanggil via fetch di CreateVmModal.

// Browse direktori/file di host (picker storage_dir & master VHDX)
export const browseHostPath = (host_name, path = '') =>
    apiAgent.get(`/api/v1/${host_name}/browse`, { params: { path } }).then(r => r.data);

// Guacamole Quick-Connect
export const getGuacUrl      = (host_name, vm_id) => api.get(`/api/v1/ssh-creds/guac-url/${host_name}/${encodeURIComponent(vm_id)}`).then(r => r.data);
export const getMyVmCred     = (host_name, vm_id) => api.get(`/api/v1/ssh-creds/my-vm-cred/${host_name}/${encodeURIComponent(vm_id)}`).then(r => r.data).catch(() => null);
export const forceGuacSync   = (host_name, vm_id) => api.post(`/api/v1/ssh-creds/guac-sync/${host_name}/${encodeURIComponent(vm_id)}`).then(r => r.data);

// VM Metadata tags
export const getVmMetadata    = (host_name, vm_id) => api.get(`/api/v1/vm-metadata/${host_name}/${encodeURIComponent(vm_id)}`).then(r => r.data).catch(() => null);
export const upsertVmMetadata = (host_name, vm_id, body) => api.put(`/api/v1/vm-metadata/${host_name}/${encodeURIComponent(vm_id)}`, body).then(r => r.data);

// VM Scale Resources
export const scaleVmCpu  = (host_name, vm_name, vcpu_count) =>
    api.post(`/api/v1/vms/${host_name}/${encodeURIComponent(vm_name)}/scale`, { resource: 'cpu', vcpu_count }).then(r => r.data);
export const scaleVmRam  = (host_name, vm_name, ram_type, ram_mb, ram_min_mb, ram_max_mb, ram_buffer_pct) =>
    api.post(`/api/v1/vms/${host_name}/${encodeURIComponent(vm_name)}/scale`, { resource: 'ram', ram_type, ram_mb, ram_min_mb, ram_max_mb, ram_buffer_pct }).then(r => r.data);
export const scaleVmDisk = (host_name, vm_name, disk_path, disk_size_gb, auto_extend = false, guest_user = 'Administrator', guest_pass = '') =>
    apiLong.post(`/api/v1/vms/${host_name}/${encodeURIComponent(vm_name)}/scale`, { resource: 'disk', disk_path, disk_size_gb, auto_extend, guest_user, guest_pass }).then(r => r.data);

// Windows VM Post-Config (PS Direct)
export const winPostConfig = (host_name, vm_name, body) =>
    apiLong.post(`/api/v1/vms/${host_name}/${encodeURIComponent(vm_name)}/win-post-config`, body).then(r => r.data);

// VM IP / DNS change — Windows via PS Direct, Linux via SSH+netplan (long timeout: up to 90s)
export const changeVmIp = (host_name, vm_name, body) =>
    apiLong.post(`/api/v1/vms/${host_name}/${encodeURIComponent(vm_name)}/change-ip`, body).then(r => r.data);
// ── Admin: Audit Trail + Guacamole Remote Monitoring ──────────────────────────
export const fetchAuditLogs = (params = {}) =>
    api.get('/api/admin/audit-logs', { params }).then(r => r.data).catch(() => ({ total: 0, items: [] }));
export const fetchRemoteSessions = () =>
    api.get('/api/admin/remote/sessions').then(r => r.data).catch(() => ({ sessions: [] }));
// block: 'none' | 'account' (nonaktifkan akun & putus semua sesinya) | 'vm' (cabut penugasan VM itu)
export const killRemoteSession = (active_id, block = 'none') =>
    api.post('/api/admin/remote/kill-session', { active_id, block }).then(r => r.data);
export const fetchRemoteHistory = (page = 1, page_size = 50, filters = {}) =>
    api.get('/api/admin/remote/history', { params: { page, page_size, ...filters } }).then(r => r.data).catch(() => ({ total: 0, items: [] }));
export const fetchAuditActions = () =>
    api.get('/api/admin/audit-logs/actions').then(r => r.data.actions).catch(() => []);
export const fetchFailedLogins = (days = 7) =>
    api.get('/api/admin/audit-logs/failed-logins', { params: { days } }).then(r => r.data);
// Ekspor CSV (Activity Log / riwayat Remote, Web, SSH) sesuai filter; browser langsung mengunduh berkasnya.
export const downloadAdminCsv = async (path, params = {}) => {
    const r = await apiLong.get(`/api/admin/${path}`, { params, responseType: 'blob' });
    const name = /filename="([^"]+)"/.exec(r.headers?.['content-disposition'] || '')?.[1] || 'ccd-export.csv';
    const url = URL.createObjectURL(r.data);
    const a = document.createElement('a');
    a.href = url; a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    return name;
};
export const guacGrantAdmins = () =>
    api.post('/api/admin/guac-grant-admins').then(r => r.data);

// ── Dynamic Agent Deployment Pipeline ─────────────────────────────────────────
// Base WebSocket (http→ws / https→wss) untuk endpoint streaming.
export const WS_BASE = BASE.replace(/^http/, 'ws');

// URL WebSocket untuk stream lifecycle "Sync & Upgrade Host Agent".
// Token dikirim sebagai query param karena browser tak bisa set header pada WS.
export const hostSyncAgentWsUrl = (hostName) =>
    `${WS_BASE}/api/host/${encodeURIComponent(hostName)}/sync-agent` +
    `?token=${encodeURIComponent(localStorage.getItem('hv_token') || '')}`;

// Fallback non-stream (jalan sampai selesai, balas seluruh log).
export const syncHostAgent = (hostName) =>
    apiLong.post(`/api/host/${encodeURIComponent(hostName)}/sync-agent`).then(r => r.data);

// ── Helpdesk / Ticketing ──────────────────────────────────────────────────────
export const createTicket = (body) => api.post('/api/tickets', body).then(r => r.data);
export const fetchTickets = (params = {}) =>
    api.get('/api/tickets', { params }).then(r => r.data).catch(() => ({ total: 0, items: [] }));
export const fetchTicket = (id) => api.get(`/api/tickets/${id}`).then(r => r.data);
export const updateTicketStatus = (id, status) =>
    api.patch(`/api/tickets/${id}/status`, { status }).then(r => r.data);
// attachment: { url, filename } — optional; both message and attachment may be present simultaneously.
export const replyTicket = (id, message, attachment = null) =>
    api.post(`/api/tickets/${id}/messages`, {
        message,
        ...(attachment ? { attachment_url: attachment.url, attachment_name: attachment.filename } : {}),
    }).then(r => r.data);

// Upload a file attachment for a ticket; returns { url, filename, size, mime }.
// Uses a dedicated axios instance with a generous timeout (60 s) for large files.
const apiUpload = axios.create({ baseURL: BASE, timeout: 60000 });
apiUpload.interceptors.request.use(attachToken);
apiUpload.interceptors.response.use(r => r, handleAuthError);

export const uploadTicketAttachment = (ticketId, file) => {
    const form = new FormData();
    form.append('file', file);
    return apiUpload.post(`/api/tickets/${ticketId}/upload`, form).then(r => r.data);
};

// ── Student Self-Registration (public — no auth required) ─────────────────────
export const registerStudent = (body) =>
    axios.post(`${BASE}/api/v1/users/register`, body).then(r => r.data);

// ── Infrastructure Requests (VPS / VPN provisioning pipeline) ─────────────────
export const fetchInfraRequests = (params = {}) =>
    api.get('/api/v1/infra-requests', { params }).then(r => r.data).catch(() => ({ total: 0, items: [] }));
export const createInfraRequest = (body) =>
    api.post('/api/v1/infra-requests', body).then(r => r.data);
export const reviewInfraRequest = (id, body) =>
    api.patch(`/api/v1/infra-requests/${id}/status`, body).then(r => r.data);

// Upload a supporting document (PDF / JPG / PNG, max 5 MB) for a request.
const apiUploadDoc = axios.create({ baseURL: BASE, timeout: 30000 });
apiUploadDoc.interceptors.request.use(attachToken);
apiUploadDoc.interceptors.response.use(r => r, handleAuthError);
export const uploadInfraDocument = (reqId, file) => {
    const form = new FormData();
    form.append('file', file);
    return apiUploadDoc.post(`/api/v1/infra-requests/${reqId}/document`, form).then(r => r.data);
};

// Admin uploads VPN config / cert file (ovpn, conf, zip, pem…)
export const uploadInfraConfig = (reqId, file) => {
    const form = new FormData();
    form.append('file', file);
    return apiUploadDoc.post(`/api/v1/infra-requests/${reqId}/config`, form).then(r => r.data);
};
export const getInfraConfigUrl = (reqId) => {
    const tok = localStorage.getItem('hv_token') || '';
    return `${BASE}/api/v1/infra-requests/${reqId}/config?token=${encodeURIComponent(tok)}`;
};
export const getInfraDocUrl = (reqId) => {
    const tok = localStorage.getItem('hv_token') || '';
    return `${BASE}/api/v1/infra-requests/${reqId}/document?token=${encodeURIComponent(tok)}`;
};

// Chat messages per infra request
export const fetchInfraMessages = (reqId) =>
    api.get(`/api/v1/infra-requests/${reqId}/messages`).then(r => r.data).catch(() => []);

// WebSocket URL for realtime infra request chat
export const infraRequestWsUrl = (reqId) => {
    const tok = localStorage.getItem('hv_token') || '';
    return `${WS_BASE}/api/v1/infra-requests/${reqId}/ws?token=${encodeURIComponent(tok)}&lang=${currentLang()}`;
};

// ── Groups (ReBAC) ────────────────────────────────────────────────────────────
export const fetchGroups       = ()              => api.get('/api/v1/groups').then(r => r.data);
export const createGroup       = (body)          => api.post('/api/v1/groups', body).then(r => r.data);
export const updateGroup       = (id, body)      => api.put(`/api/v1/groups/${id}`, body).then(r => r.data);
export const deleteGroup       = (id)            => api.delete(`/api/v1/groups/${id}`);

export const fetchGroupMembers = (id)            => api.get(`/api/v1/groups/${id}/members`).then(r => r.data);
export const addGroupMember    = (id, user_id)   => api.post(`/api/v1/groups/${id}/members`, { user_id }).then(r => r.data);
export const removeGroupMember = (id, user_id)   => api.delete(`/api/v1/groups/${id}/members/${user_id}`);

export const fetchGroupVms     = (id)            => api.get(`/api/v1/groups/${id}/vms`).then(r => r.data);
export const addGroupVm        = (id, body)      => apiAgent.post(`/api/v1/groups/${id}/vms`, body).then(r => r.data);
export const updateGroupVm     = (id, body)      => apiAgent.put(`/api/v1/groups/${id}/vms`, body).then(r => r.data);
export const removeGroupVm     = (id, vm_id, host_name) => api.delete(`/api/v1/groups/${id}/vms`, { data: { vm_id, host_name } });

export const fetchMyGroups     = ()              => api.get('/api/v1/groups/my').then(r => r.data).catch(() => []);

// ── Proxmox VE (multi-instance) ────────────────────────────────────────────────
export const fetchProxmoxInstances = () => api.get('/api/v1/proxmox/instances').then(r => r.data);
// Live CPU/RAM/disk/network per node — panel Host Performance di dashboard utama.
export const fetchHostStatus = () => api.get('/api/v1/proxmox/host-status').then(r => r.data);
export const createProxmoxInstance = (body) => api.post('/api/v1/proxmox/instances', body).then(r => r.data);
export const updateProxmoxInstance = (label, body) => api.put(`/api/v1/proxmox/instances/${encodeURIComponent(label)}`, body).then(r => r.data);
export const deleteProxmoxInstance = (label) => api.delete(`/api/v1/proxmox/instances/${encodeURIComponent(label)}`).then(r => r.data);

const pveBase = (instance, node) => `/api/v1/proxmox/instances/${encodeURIComponent(instance)}/nodes/${encodeURIComponent(node)}`;

export const fetchProxmoxNodes    = (instance)            => api.get(`/api/v1/proxmox/instances/${encodeURIComponent(instance)}/nodes`).then(r => r.data);
export const fetchProxmoxVms      = (instance, node)      => api.get(`${pveBase(instance, node)}/vms`).then(r => r.data);
export const fetchProxmoxVmDetail = (instance, node, vmid) => api.get(`${pveBase(instance, node)}/vms/${vmid}`).then(r => r.data);
export const proxmoxVmAction      = (instance, node, vmid, action) =>
    apiAgent.post(`${pveBase(instance, node)}/vms/${vmid}/action`, { action }).then(r => r.data);
export const fetchProxmoxSnapshots = (instance, node, vmid) =>
    api.get(`${pveBase(instance, node)}/vms/${vmid}/snapshots`).then(r => r.data);
export const createProxmoxSnapshot = (instance, node, vmid, snapname, description = '') =>
    apiLong.post(`${pveBase(instance, node)}/vms/${vmid}/snapshots`, { snapname, description }).then(r => r.data);
export const deleteProxmoxSnapshot = (instance, node, vmid, snapname) =>
    apiLong.delete(`${pveBase(instance, node)}/vms/${vmid}/snapshots/${encodeURIComponent(snapname)}`).then(r => r.data);
export const rollbackProxmoxSnapshot = (instance, node, vmid, snapname) =>
    apiLong.post(`${pveBase(instance, node)}/vms/${vmid}/snapshots/${encodeURIComponent(snapname)}/rollback`).then(r => r.data);
export const fetchProxmoxRrddata = (instance, node, vmid, timeframe = 'hour') =>
    api.get(`${pveBase(instance, node)}/vms/${vmid}/rrddata`, { params: { timeframe } }).then(r => r.data);
export const fetchProxmoxVmIp = (instance, node, vmid) =>
    api.get(`${pveBase(instance, node)}/vms/${vmid}/ip`).then(r => r.data).catch(() => ({ ip: null }));
export const fetchProxmoxIops = (instance, node, vmid, timeframe = 'hour') =>
    api.get(`${pveBase(instance, node)}/vms/${vmid}/iops`, { params: { timeframe } }).then(r => r.data);
export const fetchProxmoxTemplates = (instance, node) =>
    api.get(`${pveBase(instance, node)}/templates`).then(r => r.data);
// Clone + cloud-init + boot wait; a full clone of a large disk can take minutes.
export const createProxmoxVm = (instance, node, body) =>
    apiLong.post(`${pveBase(instance, node)}/vms`, body, { timeout: 900000 }).then(r => r.data);
export const deleteProxmoxVm = (instance, node, vmid, confirm_name) =>
    apiLong.delete(`${pveBase(instance, node)}/vms/${vmid}`, { params: { confirm_name } }).then(r => r.data);
export const enableProxmoxGuestAgent = (instance, node, vmid) =>
    api.post(`${pveBase(instance, node)}/vms/${vmid}/guest-agent`).then(r => r.data);
export const fetchMyAssignedVmids = (instance, node) =>
    api.get(`${pveBase(instance, node)}/my-assigned-vmids`).then(r => r.data).catch(() => ({ vmids: [], all: false }));

// Flatten semua VM di semua instance/node jadi satu list {vm_id, host_name, vm_name} generik —
// dipakai tempat yang butuh daftar VM lintas-instance tanpa peduli topologi (mis. GroupsPage).
export const fetchAllProxmoxVmsFlat = async () => {
    const instances = await fetchProxmoxInstances().catch(() => []);
    const results = [];
    await Promise.all(instances.map(async inst => {
        const nodes = await fetchProxmoxNodes(inst.label).catch(() => []);
        await Promise.all(nodes.map(async n => {
            const vms = await fetchProxmoxVms(inst.label, n.node).catch(() => []);
            vms.forEach(vm => results.push({
                vm_id: String(vm.vmid),
                host_name: `${inst.label}__${n.node}`,
                vm_name: vm.name || String(vm.vmid),
            }));
        }));
    }));
    return results;
};

export const createOpenWebTicket = (url) => api.post('/api/v1/openweb/ticket', { url }).then(r => r.data);
export const fetchProxmoxVmResources = (instance, node, vmid) => api.get(`${pveBase(instance, node)}/vms/${vmid}/resources`).then(r => r.data);
export const updateProxmoxVmResources = (instance, node, vmid, body) => apiLong.put(`${pveBase(instance, node)}/vms/${vmid}/resources`, body).then(r => r.data);
export const fetchMyProxmoxVms = () => api.get('/api/v1/proxmox/my-vms').then(r => r.data);
export const fetchOpenWebSessions = () =>
    api.get('/api/admin/openweb/sessions').then(r => r.data).catch(() => ({ sessions: [] }));
export const fetchOpenWebHistory = (page = 1, page_size = 50, filters = {}) =>
    api.get('/api/admin/openweb/history', { params: { page, page_size, ...filters } }).then(r => r.data).catch(() => ({ total: 0, items: [] }));
export const killOpenWebSession = (session_id, block = 'none') =>
    api.post('/api/admin/openweb/kill', { session_id, block }).then(r => r.data);
export const fetchSshSessions = () =>
    api.get('/api/admin/ssh/sessions').then(r => r.data).catch(() => ({ sessions: [] }));
export const fetchSshHistory = (page = 1, page_size = 50, filters = {}) =>
    api.get('/api/admin/ssh/history', { params: { page, page_size, ...filters } }).then(r => r.data).catch(() => ({ total: 0, items: [] }));
// Menunggu konfirmasi bastion sampai ~8 detik, jadi memakai apiLong.
export const killSshSession = (session_id, block = 'none') =>
    apiLong.post('/api/admin/ssh/kill', { session_id, block }).then(r => r.data);
export const fetchVpsLive = () => api.get('/api/admin/vps/live').then(r => r.data);
export const fetchVpsHistory = (range) => api.get('/api/admin/vps/history', { params: { range } }).then(r => r.data);

// ── Pengaturan Sistem (identitas dan aturan pendaftaran) ─────────────────────
export const fetchBranding = () => api.get('/api/v1/system/branding').then(r => r.data);
export const fetchSystemConfig = () => api.get('/api/v1/system/config').then(r => r.data);
export const uploadSystemLogo = (file) => {
    const form = new FormData();
    form.append('file', file);
    return apiUpload.post('/api/v1/system/logo', form).then(r => r.data);
};
export const deleteSystemLogo = () => api.delete('/api/v1/system/logo').then(r => r.data);
export const fetchSystemSettings = () => api.get('/api/v1/system/settings').then(r => r.data);
export const saveSystemSettings = (body) => api.put('/api/v1/system/settings', body).then(r => r.data);
export const saveTicketCategories = (categories) =>
    api.put('/api/v1/system/ticket-categories', { categories }).then(r => r.data);
export const saveOsOptions = (options) => api.put('/api/v1/system/os-options', { options }).then(r => r.data);
export const uploadOsLogo = (name, file) => {
    const form = new FormData();
    form.append('file', file);
    return api.post('/api/v1/system/os-logo', form, { params: { name } }).then(r => r.data);
};
export const deleteOsLogo = (name) => api.delete('/api/v1/system/os-logo', { params: { name } }).then(r => r.data);
export const deleteTicket = (id) => api.delete(`/api/tickets/${id}`).then(r => r.data);
export const deleteInfraRequest = (id) => api.delete(`/api/v1/infra-requests/${encodeURIComponent(id)}`).then(r => r.data);
export const getProxmoxSshUrl = (label, port = 22) =>
    api.get(`/api/v1/proxmox/instances/${encodeURIComponent(label)}/ssh-url`, { params: { port } }).then(r => r.data);
export const fetchAuditStats = () => api.get('/api/v1/system/audit-stats').then(r => r.data);

// ── Siklus akun dan masa sewa VM ─────────────────────────────────────────────
export const bulkUsers = (body) => api.post('/api/v1/users/bulk', body).then(r => r.data);
export const importUsers = (rows, dry_run) => api.post('/api/v1/users/import', { rows, dry_run }).then(r => r.data);
export const setVmLease = (instance, node, vmid, body) =>
    api.put(`${pveBase(instance, node)}/vms/${vmid}/lease`, body).then(r => r.data);

// Switch (jaringan) CCD: blok alamat per Proxmox dan switch terisolasi di dalamnya.
// Membuat, mengubah, dan menghapus switch menerapkan SDN di Proxmox (memuat ulang jaringan host), jadi pakai timeout panjang.
const netBase = '/api/v1/networks';
export const fetchNetworks      = ()            => api.get(netBase).then(r => r.data);
export const checkNetworkSetup  = (label)       => apiAgent.get(`${netBase}/instances/${encodeURIComponent(label)}/check`).then(r => r.data);
export const addNetworkPool     = (label, cidr) => apiAgent.post(`${netBase}/instances/${encodeURIComponent(label)}/pools`, { cidr }).then(r => r.data);
export const removeNetworkPool  = (label, cidr) => apiAgent.delete(`${netBase}/instances/${encodeURIComponent(label)}/pools`, { params: { cidr } }).then(r => r.data);
export const createNetwork      = (body)        => apiLong.post(netBase, body).then(r => r.data);
export const updateNetwork      = (id, body)    => apiLong.patch(`${netBase}/${id}`, body).then(r => r.data);
export const deleteNetwork      = (id)          => apiLong.delete(`${netBase}/${id}`).then(r => r.data);
export const fetchNetworkVms    = (id)          => apiAgent.get(`${netBase}/${id}/vms`).then(r => r.data);
export const fetchNetworkFreeIp = (id)          => apiAgent.get(`${netBase}/${id}/free-ip`).then(r => r.data);

// VM massal per kelas: rencana, mulai (berjalan di latar belakang), progres, ulangi, dan CSV kredensial.
const batchBase = '/api/v1/vm-batches';
export const previewVmBatch     = (body) => apiAgent.post(`${batchBase}/preview`, body).then(r => r.data);
export const createVmBatch      = (body) => apiAgent.post(batchBase, body).then(r => r.data);
export const fetchVmBatches     = ()     => api.get(batchBase).then(r => r.data);
export const fetchVmBatch       = (id)   => api.get(`${batchBase}/${id}`).then(r => r.data);
export const retryVmBatch       = (id)   => apiAgent.post(`${batchBase}/${id}/retry`).then(r => r.data);
export const downloadVmBatchCsv = (id)   => api.get(`${batchBase}/${id}/credentials.csv`, { responseType: 'blob' }).then(r => r.data);
