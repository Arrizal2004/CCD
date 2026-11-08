import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../api', () => ({
    fetchImageCatalog: vi.fn(), fetchImageOptions: vi.fn(), startImageJob: vi.fn(), fetchImageJobs: vi.fn(),
    retryImageJob: vi.fn(), deleteImageJob: vi.fn(),
}));

import * as api from '../api';
import CloudImagesModal from '../components/CloudImagesModal';

afterEach(() => { cleanup(); vi.clearAllMocks(); });

const CATALOG = [
    { key: 'ubuntu-2404', name: 'Ubuntu Server 24.04 LTS', agent_included: false, default_user: 'ubuntu', min_disk_gb: 10 },
    { key: 'opensuse-leap-160', name: 'openSUSE Leap 16.0', agent_included: true, default_user: '', min_disk_gb: 10 },
];
const OPTS = { version_ok: true, version: '9.1', node_network: true, pools: ['lab'], bridges: ['vmbr0'],
    import_storages: [{ storage: 'local', avail_gb: 80 }], disk_storages: [{ storage: 'local-lvm', avail_gb: 200 }] };

function setup(opts = OPTS, jobs = []) {
    vi.mocked(api.fetchImageCatalog).mockResolvedValue(CATALOG);
    vi.mocked(api.fetchImageOptions).mockResolvedValue(opts);
    vi.mocked(api.fetchImageJobs).mockResolvedValue(jobs);
    return render(<CloudImagesModal instance="lab" node="pve" onClose={() => {}} />);
}

describe('CloudImagesModal', () => {
    it('hanya menawarkan Ubuntu dan openSUSE, dan memulai proses dengan pilihan node', async () => {
        vi.mocked(api.startImageJob).mockResolvedValue({ id: 5 });
        setup();
        const select = await screen.findByLabelText('Image');
        expect([...select.options].map(o => o.text)).toEqual(['Ubuntu Server 24.04 LTS', 'openSUSE Leap 16.0']);
        expect(screen.getByText(/Tanpa guest agent/)).toBeTruthy();
        fireEvent.change(select, { target: { value: 'opensuse-leap-160' } });
        expect(screen.getByText(/Membawa guest agent/)).toBeTruthy();

        fireEvent.change(screen.getByLabelText('Disk (GB)'), { target: { value: '20' } });
        fireEvent.click(screen.getByText('Buat template'));
        await waitFor(() => expect(api.startImageJob).toHaveBeenCalledWith(expect.objectContaining({
            instance: 'lab', node: 'pve', image_key: 'opensuse-leap-160', storage: 'local', disk_storage: 'local-lvm', pool: 'lab', bridge: 'vmbr0', disk_gb: 20,
        })));
    });

    it('menolak memulai kalau node belum siap (Proxmox lama)', async () => {
        setup({ ...OPTS, version_ok: false, version: '8.1' });
        expect(await screen.findByText(/Proxmox VE 8.1 terlalu lama/)).toBeTruthy();
        expect(screen.getByText('Buat template').disabled).toBe(true);
    });

    it('memperingatkan kalau token belum punya izin jaringan', async () => {
        setup({ ...OPTS, node_network: false });
        expect(await screen.findByText(/Sys.AccessNetwork/)).toBeTruthy();
    });

    it('menampilkan progres, hasil, dan peringatan proses; mengulang yang gagal', async () => {
        vi.mocked(api.retryImageJob).mockResolvedValue({});
        setup(OPTS, [
            { id: 3, instance: 'lab', node: 'pve', image_name: 'Ubuntu Server 24.04 LTS', status: 'running', step: 'download', result: { progress: '41% selesai' }, error: '' },
            { id: 2, instance: 'lab', node: 'pve', image_name: 'openSUSE Leap 16.0', status: 'failed', step: '', result: {}, error: 'Unduhan image gagal: checksum' },
            { id: 1, instance: 'lab', node: 'pve', image_name: 'Ubuntu Server 24.04 LTS', status: 'done', result: { template_vmid: 9001, warnings: ['Image ini tidak membawa guest agent'] }, error: '' },
            { id: 9, instance: 'lain', node: 'pve', image_name: 'bukan node ini', status: 'done', result: {}, error: '' },
        ]);
        expect(await screen.findByText(/Mengunduh image · 41% selesai/)).toBeTruthy();
        expect(screen.getAllByTestId('image-job')).toHaveLength(3);                // proses node lain tidak tampil
        expect(screen.getByText(/Template VMID 9001 siap/)).toBeTruthy();
        expect(screen.getByText('Image ini tidak membawa guest agent')).toBeTruthy();
        fireEvent.click(screen.getByText('Ulangi'));
        await waitFor(() => expect(api.retryImageJob).toHaveBeenCalledWith(2));
    });
});
