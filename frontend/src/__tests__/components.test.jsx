import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import PaneTabs from '../components/PaneTabs';
import SshCommandModal from '../components/SshCommandModal';
import { DEFAULT_BRANDING, logoLetter, setBranding, useBranding } from '../branding';

afterEach(cleanup);

describe('PaneTabs', () => {
    it('menampilkan jumlah pesan dan memberi tahu tab yang dipilih', () => {
        const onChange = vi.fn();
        render(<PaneTabs value="chat" onChange={onChange} chatCount={5} />);
        expect(screen.getByRole('tab', { name: 'Chat (5)' }).getAttribute('aria-selected')).toBe('true');
        fireEvent.click(screen.getByRole('tab', { name: 'Detail' }));
        expect(onChange).toHaveBeenCalledWith('detail');
    });
});

describe('SshCommandModal', () => {
    const cfg = { enabled: true, host: 'ssh.contoh.ac.id', port: 2222, user: 'tunnel', host_fingerprint: 'SHA256:abc' };
    it('membuat ~/.ssh/config dan perintah satu baris untuk VM', () => {
        render(<SshCommandModal vm={{ name: 'Web Server', vmid: 101, manual_ip: '10.0.1.21', ssh_port: 22 }} cfg={cfg} onClose={() => {}} />);
        const text = document.body.textContent;
        expect(text).toContain('HostName ssh.contoh.ac.id');
        expect(text).toContain('Host ccd-web-server');
        expect(text).toContain('ProxyJump ccd-bastion');
        expect(text).toContain('ssh -J tunnel@ssh.contoh.ac.id:2222 AKUN-OS@10.0.1.21');
        expect(text).toContain('SHA256:abc');
    });

    it('menambahkan Port kalau SSH VM tidak di port 22', () => {
        render(<SshCommandModal vm={{ name: 'db', vmid: 5, manual_ip: '10.0.1.5', ssh_port: 2200 }} cfg={cfg} onClose={() => {}} />);
        expect(document.body.textContent).toContain('-p 2200');
    });
});

describe('branding', () => {
    function Title() {
        const b = useBranding();
        return <h1>{logoLetter(b)} {b.name}</h1>;
    }

    it('nama baru langsung dipakai komponen dan judul tab', () => {
        render(<Title />);
        expect(screen.getByRole('heading').textContent).toBe(`C ${DEFAULT_BRANDING.name}`);
        act(() => setBranding({ name: 'Lab Cloud SMK', short_name: 'LCS' }));
        expect(screen.getByRole('heading').textContent).toBe('L Lab Cloud SMK');
        expect(document.title).toBe('Lab Cloud SMK');
        act(() => setBranding(DEFAULT_BRANDING));
    });
});
