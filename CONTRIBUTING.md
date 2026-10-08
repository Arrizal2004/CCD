# Berkontribusi

Terima kasih sudah mau membantu CCD. Panduan ini singkat; untuk cara memasang dan memakai dashboard, lihat [README](README.md) dan [panduan deployment](docs/PANDUAN_DEPLOYMENT.md).

**Celah keamanan:** jangan lapor lewat issue publik. Ikuti [SECURITY.md](SECURITY.md).

## Sebelum mulai

Buka issue dulu untuk fitur atau perubahan besar supaya arahnya sama. Perbaikan kecil (typo, bug jelas) boleh langsung lewat pull request.

## Menjalankan test

Backend butuh PostgreSQL dan Redis. Cara termudah, sama seperti CI:

```bash
docker run -d --name ccd-test-pg -p 5432:5432 -e POSTGRES_USER=ccd -e POSTGRES_PASSWORD=ccd123 -e POSTGRES_DB=ccddb_test postgres:16-alpine
docker run -d --name ccd-test-redis -p 6379:6379 redis:7-alpine

cd backend
pip install -r requirements.txt
DATABASE_URL=postgresql://ccd:ccd123@localhost:5432/ccddb_test REDIS_URL=redis://localhost:6379 \
JWT_SECRET=test-secret-not-for-production GUAC_ADMIN_PASS=test-only ALLOWED_ORIGINS=http://localhost \
pytest -q --deselect tests/test_health.py::test_healthz_returns_200 --deselect tests/test_health.py::test_healthz_status_ok
```

Frontend:

```bash
cd frontend
npm ci
npm run lint && npm test && npm run build
```

Pull request harus lolos test, `npm run lint` tanpa error, dan jumlah peringatan lint tidak bertambah.

## Aturan kode

- **Dua bahasa.** Semua teks yang terlihat pengguna harus ada dalam bahasa Indonesia dan Inggris.
  - Frontend: tambahkan pasangan `[Indonesia, English]` di `frontend/src/locales/*.js`, lalu pakai `t('kunci')`. Test `i18n.test.js` memeriksa kunci ganda, pasangan yang kosong, dan kunci yang dipakai tetapi tidak ada.
  - Backend: pesan galat dibungkus `tr("teks Indonesia", "English text")` dari `backend/i18n.py`. Test memeriksa bahwa kedua teks ada dan variabelnya sama.
  - Catatan yang disimpan (Activity Log, pesan sistem di tiket) ditulis dalam bahasa Indonesia saat dicatat; itu disengaja.
- **Skema database** diubah lewat berkas baru `backend/migrations/V0NN__nama.sql` (urut, jangan mengubah migrasi lama), dan dijalankan otomatis saat backend start.
- **Aksi penting dicatat** di Activity Log lewat `log_activity(...)`: siapa, aksi apa, pada apa. Detailnya disusun dengan `both(lambda: tr("teks Indonesia", "English text"))` dari `services/audit.py` supaya tersimpan dalam dua bahasa; test gagal kalau ada yang berupa string biasa. Jangan menulis password, token, secret, atau isi percakapan pengguna ke log.
- **Akses ditentukan di backend.** Pembatasan di tampilan hanya kenyamanan; setiap endpoint harus memeriksa peran lewat `require_*` di `backend/auth.py`, dan test RBAC harus ikut diperbarui.
- **Komentar dan teks** memakai bahasa Indonesia yang ringkas, mengikuti gaya berkas di sekitarnya.

## Data di repositori

Repositori ini publik. Jangan memasukkan IP server, domain, nama pengguna, password, token, atau berkas `.env` yang asli, baik di kode, test, maupun dokumentasi. Pakai placeholder (`<ip-vps>`, `dashboard.<domain>`) atau alamat contoh (`203.0.113.x`, `example.com`).

## Pull request

- Satu pull request untuk satu perubahan, dengan deskripsi singkat tentang apa dan mengapa.
- Sertakan test untuk perilaku baru atau bug yang diperbaiki.
- Perbarui panduan di `docs/` kalau perubahan memengaruhi cara memasang atau memakai.
