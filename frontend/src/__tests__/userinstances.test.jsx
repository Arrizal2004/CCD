import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../api', () => ({
    fetchProxmoxInstances: vi.fn(),
    fetchUserInstances: vi.fn(),
    saveUserInstances: vi.fn(),
}));

import * as api from '../api';
import { setLanguage } from '../i18n';
import UserInstancesModal from '../components/UserInstancesModal';

const user = { id: 7, username: 'sysadmin1' };

beforeEach(() => {
    setLanguage('id');
    vi.mocked(api.fetchProxmoxInstances).mockResolvedValue([{ label: 'kampus1' }, { label: 'kampus2' }]);
    vi.mocked(api.fetchUserInstances).mockResolvedValue({ instances: ['kampus1'] });
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe('Proxmox untuk sysadmin', () => {
    it('menampilkan semua Proxmox dengan yang sudah ditugaskan tercentang', async () => {
        render(<UserInstancesModal user={user} onClose={() => {}} />);
        const first = await screen.findByLabelText('kampus1');
        expect(first.checked).toBe(true);
        expect(screen.getByLabelText('kampus2').checked).toBe(false);
        expect(document.body.textContent).toContain('Proxmox untuk sysadmin1');
    });

    it('menyimpan pilihan baru lalu menutup dan memuat ulang daftar', async () => {
        vi.mocked(api.saveUserInstances).mockResolvedValue({ instances: ['kampus2'] });
        const onClose = vi.fn(), onSaved = vi.fn();
        render(<UserInstancesModal user={user} onClose={onClose} onSaved={onSaved} />);
        fireEvent.click(await screen.findByLabelText('kampus2'));
        fireEvent.click(screen.getByLabelText('kampus1'));
        fireEvent.click(screen.getByRole('button', { name: 'Simpan' }));
        await waitFor(() => expect(api.saveUserInstances).toHaveBeenCalledWith(7, ['kampus2']));
        await waitFor(() => expect(onSaved).toHaveBeenCalled());
        expect(onClose).toHaveBeenCalled();
    });

    it('tanpa pilihan: peringatan bahwa sysadmin tidak akan melihat Proxmox apa pun', async () => {
        vi.mocked(api.fetchUserInstances).mockResolvedValue({ instances: [] });
        render(<UserInstancesModal user={user} onClose={() => {}} />);
        await screen.findByLabelText('kampus1');
        expect(document.body.textContent).toContain('tidak melihat Proxmox apa pun');
    });

    it('pesan dari server ditampilkan kalau gagal menyimpan dan dialog tetap terbuka', async () => {
        vi.mocked(api.saveUserInstances).mockRejectedValue({ response: { data: { detail: 'Hanya akun sysadmin yang dibatasi per Proxmox' } } });
        const onClose = vi.fn();
        render(<UserInstancesModal user={user} onClose={onClose} />);
        await screen.findByLabelText('kampus1');
        fireEvent.click(screen.getByRole('button', { name: 'Simpan' }));
        await screen.findByText('Hanya akun sysadmin yang dibatasi per Proxmox');
        expect(onClose).not.toHaveBeenCalled();
    });

    it('Batal menutup tanpa menyimpan', async () => {
        const onClose = vi.fn();
        render(<UserInstancesModal user={user} onClose={onClose} />);
        await screen.findByLabelText('kampus1');
        fireEvent.click(screen.getByRole('button', { name: 'Batal' }));
        expect(onClose).toHaveBeenCalled();
        expect(api.saveUserInstances).not.toHaveBeenCalled();
    });
});
