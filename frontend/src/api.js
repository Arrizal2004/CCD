import axios from 'axios';

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
export const fetchAllProxmoxVms = () => api.get('/api/v1/proxmox/all-vms').then(r => r.data);
export const changePassword = (old_password, new_password) =>
    api.post('/api/v1/users/me/change-password', { old_password, new_password }).then(r => r.data);
export const fetchHealthz = () => api.get('/healthz').then(r => r.data).catch(() => ({ status: 'error', checks: {} }));


// Users and VM Assignments
export const fetchUsers    = ()          => api.get('/api/v1/users').then(r => r.data);
export const createUser    = (body)      => api.post('/api/v1/users', body).then(r => r.data);
export const updateUser    = (id, body)  => api.put(`/api/v1/users/${id}`, body).then(r => r.data);
export const deleteUserApi = (id)        => api.delete(`/api/v1/users/${id}`).then(r => r.data);
export const fetchUserAssignments = (user_id) => api.get(`/api/v1/users/${user_id}/vm-assignments`).then(r => r.data);
export const assignVm = (user_id, vm_id, host_name, os_account_id = null, vm_name = null) =>
    api.post('/api/v1/users/vm-assignments', { user_id, vm_id, host_name, vm_name, os_account_id }).then(r => r.data);
export const fetchVmOsAccounts = (host_name, vm_id) =>
    api.get(`/api/v1/ssh-creds/vm-os-accounts/${host_name}/${encodeURIComponent(vm_id)}`).then(r => r.data);
export const upsertVmOsAccount = (host_name, vm_id, data) =>
    api.post(`/api/v1/ssh-creds/vm-os-accounts/${host_name}/${encodeURIComponent(vm_id)}`, data).then(r => r.data);
export const updateVmOsAccount = (host_name, vm_id, account_id, data) =>
    api.put(`/api/v1/ssh-creds/vm-os-accounts/${host_name}/${encodeURIComponent(vm_id)}/${account_id}`, data).then(r => r.data);
export const deleteVmOsAccount = (host_name, vm_id, account_id) =>
    api.delete(`/api/v1/ssh-creds/vm-os-accounts/${host_name}/${encodeURIComponent(vm_id)}/${account_id}`).then(r => r.data);
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
export const killRemoteSession = (active_id) =>
    api.post('/api/admin/remote/kill-session', { active_id }).then(r => r.data);
export const fetchRemoteHistory = (page = 1, page_size = 50) =>
    api.get('/api/admin/remote/history', { params: { page, page_size } }).then(r => r.data).catch(() => ({ total: 0, items: [] }));
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
    return `${WS_BASE}/api/v1/infra-requests/${reqId}/ws?token=${encodeURIComponent(tok)}`;
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
export const addGroupVm        = (id, body)      => api.post(`/api/v1/groups/${id}/vms`, body).then(r => r.data);
export const updateGroupVm     = (id, body)      => api.put(`/api/v1/groups/${id}/vms`, body).then(r => r.data);
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

// ── Tailscale ─────────────────────────────────────────────────────────────────
export const fetchTailscaleDevices = () => api.get('/api/v1/tailscale/devices').then(r => r.data).catch(() => []);
export const fetchTailscaleAcl = () => api.get('/api/v1/tailscale/acl').then(r => r.data);
export const fetchTailscaleConfig  = () => api.get('/api/v1/tailscale/config').then(r => r.data);
export const saveTailscaleConfig   = (body) => api.put('/api/v1/tailscale/config', body).then(r => r.data);
export const deleteTailscaleConfig = () => api.delete('/api/v1/tailscale/config').then(r => r.data);
export const fetchTailscalePolicy   = () => api.get('/api/v1/tailscale/policy').then(r => r.data);
export const previewTailscalePolicy = (body) => api.post('/api/v1/tailscale/policy/preview', body).then(r => r.data);
export const applyTailscalePolicy   = (body) => api.post('/api/v1/tailscale/policy/apply', body).then(r => r.data);
export const setTailscaleGateway    = (deviceId, enabled) =>
    api.put(`/api/v1/tailscale/devices/${encodeURIComponent(deviceId)}/gateway`, { enabled }).then(r => r.data);
export const setAdminTailscaleLogin = (userId, tailscale_login) =>
    api.put(`/api/v1/tailscale/admin-logins/${userId}`, { tailscale_login }).then(r => r.data);
export const createOpenWebTicket = (url) => api.post('/api/v1/openweb/ticket', { url }).then(r => r.data);
export const fetchProxmoxVmResources = (instance, node, vmid) => api.get(`${pveBase(instance, node)}/vms/${vmid}/resources`).then(r => r.data);
export const updateProxmoxVmResources = (instance, node, vmid, body) => apiLong.put(`${pveBase(instance, node)}/vms/${vmid}/resources`, body).then(r => r.data);
export const fetchMyProxmoxVms = () => api.get('/api/v1/proxmox/my-vms').then(r => r.data);
export const fetchOpenWebSessions = () =>
    api.get('/api/admin/openweb/sessions').then(r => r.data).catch(() => ({ sessions: [] }));
export const fetchOpenWebHistory = (page = 1, page_size = 50) =>
    api.get('/api/admin/openweb/history', { params: { page, page_size } }).then(r => r.data).catch(() => ({ total: 0, items: [] }));
export const killOpenWebSession = (session_id) =>
    api.post('/api/admin/openweb/kill', { session_id }).then(r => r.data);
