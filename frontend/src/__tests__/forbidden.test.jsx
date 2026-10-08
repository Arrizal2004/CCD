import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import ForbiddenPage from '../components/ForbiddenPage';

afterEach(cleanup);

describe('ForbiddenPage', () => {
    it('menampilkan 403 Forbidden, bukan pesan error mentah, dan bisa kembali', () => {
        const onBack = vi.fn();
        render(<ForbiddenPage need="admin" role="student" onBack={onBack} />);
        expect(screen.getByText('403')).toBeTruthy();
        expect(screen.getByText('Forbidden')).toBeTruthy();
        expect(screen.getByText('Halaman ini khusus Admin. Anda login sebagai Mahasiswa.')).toBeTruthy();
        fireEvent.click(screen.getByRole('button', { name: '← Kembali ke Servers' }));
        expect(onBack).toHaveBeenCalled();
    });
});
