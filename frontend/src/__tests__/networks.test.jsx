import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../api', () => ({
    fetchNetworks: vi.fn(),
    checkNetworkSetup: vi.fn(),
    addNetworkPool: vi.fn(),
    removeNetworkPool: vi.fn(),
    createNetwork: vi.fn(),
    updateNetwork: vi.fn(),
    deleteNetwork: vi.fn(),
    fetchNetworkVms: vi.fn(),
}));

import * as api from '../api';
import SwitchManager from '../components/SwitchManager';

const SWITCH = { id: 3, instance: 'pve1', vnet: 'ccdab12x', name: 'Kelas A', cidr: '10.111.1.0/24', gateway: '10.111.1.1', snat: true };
const instance = (extra = {}) => ({
    label: 'pve1', token_id: 'root@pam!ccd', pools: [{ cidr: '10.111.0.0/16', switches: 1, full: false }], zone: 'ccd',
    suggested_cidr: '10.111.2.0/24', networks: [SWITCH], ...extra,
});

beforeEach(() => {
    vi.mocked(api.fetchNetworks).mockResolvedValue({ instances: [instance()] });
    vi.mocked(api.checkNetworkSetup).mockResolvedValue({ zone_ok: true, perm_zone: true, perm_apply: true, ready: true, reachable: { 3: true } });
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe('SwitchManager (dibuka dari Topology)', () => {
    it('menampilkan switch, status jalur dari CCD, dan membuat switch dengan subnet otomatis', async () => {
        vi.mocked(api.createNetwork).mockResolvedValue({ ...SWITCH, id: 4, name: 'Kelas B', cidr: '10.111.2.0/24' });
        const onChanged = vi.fn();
        render(<SwitchManager onChanged={onChanged} />);
        await screen.findByText('Kelas A');
        expect(await screen.findByText('● terjangkau dari CCD')).toBeTruthy();
        expect(screen.getByLabelText('Subnet').getAttribute('placeholder')).toBe('otomatis: 10.111.2.0/24');

        fireEvent.change(screen.getByLabelText('Nama switch baru'), { target: { value: ' Kelas B ' } });
        fireEvent.click(screen.getByRole('button', { name: 'Buat switch' }));
        await waitFor(() => expect(api.createNetwork).toHaveBeenCalledWith({ instance: 'pve1', name: 'Kelas B', cidr: null, snat: true, add_pool: false }));
        await waitFor(() => expect(api.fetchNetworks).toHaveBeenCalledTimes(2));     // daftar dimuat ulang
        expect(onChanged).toHaveBeenCalled();                                        // Topology digambar ulang
    });

    it('host yang belum siap menampilkan perintah setup berisi blok dan token', async () => {
        vi.mocked(api.fetchNetworks).mockResolvedValue({ instances: [instance({ networks: [] })] });
        vi.mocked(api.checkNetworkSetup).mockResolvedValue({ zone_ok: false, zone_type: null, perm_zone: false, perm_apply: false, ready: false, reachable: {} });
        render(<SwitchManager />);
        expect(await screen.findByText(/\(Simple\)\s+belum ada/)).toBeTruthy();
        const command = await screen.findByText(/ccd-net-setup.sh --pool/);
        expect(command.textContent).toContain("--pool 10.111.0.0/16 --token 'root@pam!ccd'");
        expect(command.textContent).toContain('/api/v1/networks/setup-script');
    });

    it('subnet di luar blok hanya bisa dibuat kalau dicentang sebagai blok baru, lalu langkah host ditampilkan', async () => {
        const full = instance({ pools: [{ cidr: '192.168.111.0/24', switches: 1, full: true }], suggested_cidr: null,
            networks: [{ ...SWITCH, cidr: '192.168.111.0/24', gateway: '192.168.111.1' }] });
        vi.mocked(api.fetchNetworks).mockResolvedValueOnce({ instances: [full] }).mockResolvedValue({ instances: [{
            ...full, pools: [...full.pools, { cidr: '192.168.112.0/24', switches: 1, full: true }] }] });
        vi.mocked(api.createNetwork).mockResolvedValue({ ...SWITCH, id: 4, name: 'TRIB1', cidr: '192.168.112.0/24', pool_added: '192.168.112.0/24' });
        render(<SwitchManager />);
        expect(await screen.findByText(/Semua blok sudah penuh/)).toBeTruthy();

        fireEvent.change(screen.getByLabelText('Nama switch baru'), { target: { value: 'TRIB1' } });
        fireEvent.change(screen.getByLabelText('Subnet'), { target: { value: '192.168.112.0/24' } });
        const create = screen.getByRole('button', { name: 'Buat switch' });
        expect(create.disabled).toBe(true);                                          // belum dicentang
        fireEvent.click(screen.getByRole('checkbox', { name: /sebagai blok alamat baru/ }));
        expect(create.disabled).toBe(false);
        fireEvent.click(create);

        await waitFor(() => expect(api.createNetwork).toHaveBeenCalledWith(
            { instance: 'pve1', name: 'TRIB1', cidr: '192.168.112.0/24', snat: true, add_pool: true }));
        const notice = await screen.findByText(/Blok 192.168.112.0\/24 ditambahkan/);
        expect(notice.textContent).toContain('setujui route 192.168.112.0/24');
        const command = await screen.findByText(/ccd-net-setup.sh --pool/);
        await waitFor(() => expect(command.textContent).toContain('--pool 192.168.111.0/24,192.168.112.0/24 '));
    });

    it('subnet di dalam blok yang ada tidak meminta blok baru', async () => {
        render(<SwitchManager />);
        await screen.findByText('Kelas A');
        fireEvent.change(screen.getByLabelText('Subnet'), { target: { value: '10.111.7.0/24' } });
        expect(screen.queryByRole('checkbox', { name: /sebagai blok alamat baru/ })).toBeNull();
    });

    it('blok bisa ditambah, dan hanya blok tanpa switch yang bisa dihapus', async () => {
        vi.mocked(api.fetchNetworks).mockResolvedValue({ instances: [instance({
            pools: [{ cidr: '10.111.0.0/16', switches: 1, full: false }, { cidr: '10.112.0.0/24', switches: 0, full: false }] })] });
        vi.mocked(api.addNetworkPool).mockResolvedValue({ added: '10.113.0.0/24', replaced: [], pools: [] });
        vi.mocked(api.removeNetworkPool).mockResolvedValue({ removed: '10.112.0.0/24', pools: [] });
        render(<SwitchManager />);
        expect((await screen.findByRole('button', { name: 'Hapus blok 10.111.0.0/16' })).disabled).toBe(true);

        fireEvent.click(screen.getByRole('button', { name: 'Hapus blok 10.112.0.0/24' }));
        await waitFor(() => expect(api.removeNetworkPool).toHaveBeenCalledWith('pve1', '10.112.0.0/24'));
        expect(await screen.findByText(/route-nya dicabut dari Tailscale/)).toBeTruthy();

        fireEvent.change(screen.getByLabelText('Blok alamat baru pve1'), { target: { value: ' 10.113.0.0/24 ' } });
        fireEvent.click(screen.getByRole('button', { name: 'Tambah blok' }));
        await waitFor(() => expect(api.addNetworkPool).toHaveBeenCalledWith('pve1', '10.113.0.0/24'));
        expect(await screen.findByText(/Blok 10.113.0.0\/24 ditambahkan/)).toBeTruthy();
    });

    it('hapus yang ditolak karena masih ada VM menampilkan alasannya', async () => {
        vi.mocked(api.deleteNetwork).mockRejectedValue({ response: { data: { detail: 'Switch masih dipakai 1 VM: router-a (701)' } } });
        render(<SwitchManager />);
        fireEvent.click(await screen.findByRole('button', { name: 'Hapus' }));
        fireEvent.click(screen.getByRole('button', { name: 'Hapus switch' }));
        await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('router-a'));
        expect(api.deleteNetwork).toHaveBeenCalledWith(3);
    });
});
