// Connect defaults for a VM provisioned in Proxmox with cloud-init: ciuser, static IP from ipconfig0,
// OS family from ostype. The cloud-init password can't be read back — Proxmox only keeps a hash.
export function cloudInitDefaults(config = {}) {
    const ip = (config.ipconfig0 || '').match(/(?:^|,)ip=(\d{1,3}(?:\.\d{1,3}){3})\//)?.[1];
    return {
        username: config.ciuser || '',
        ssh_host: ip || '',
        os_type: (config.ostype || '').startsWith('w') ? 'windows' : config.ostype ? 'linux' : '',
    };
}
