# Contributing / Berkontribusi

[English](#english) | [Bahasa Indonesia](#bahasa-indonesia)

---

## English

Thank you for helping with CCD. This guide is short; for how to install and use the dashboard, see the [README](README.en.md) and the deployment guide ([English, sections 1 to 3](docs/DEPLOYMENT.en.md); full guide in [Indonesian](docs/PANDUAN_DEPLOYMENT.md)).

**Security issues:** do not report them in a public issue. Follow [SECURITY.md](SECURITY.md).

### Before you start

Open an issue first for a new feature or a large change so we agree on the direction. Small fixes (typos, obvious bugs) can go straight to a pull request. You are welcome to write issues and pull requests in English or Indonesian.

### Running the tests

The backend needs PostgreSQL and Redis. The easiest way, same as CI:

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

A pull request must pass the tests, `npm run lint` with no errors, and must not increase the number of lint warnings.

### Code rules

- **Two languages.** Every user-visible text must exist in both Indonesian and English.
  - Frontend: add an `[Indonesian, English]` pair in `frontend/src/locales/*.js`, then use `t('key')`. The `i18n.test.js` test checks for duplicate keys, empty pairs, and keys that are used but missing.
  - Backend: wrap error messages in `tr("Indonesian text", "English text")` from `backend/i18n.py`. A test checks that both texts exist and use the same variables.
- **Database schema** changes go in a new file `backend/migrations/V0NN__name.sql` (sequential, never edit an old migration). It runs automatically when the backend starts.
- **Important actions are recorded** in the activity log with `log_activity(...)`: who, what, on what. Build the detail with `both(lambda: tr("Indonesian text", "English text"))` from `services/audit.py` so it is stored in both languages; a test fails if a plain string is used. Never write passwords, tokens, secrets, or the content of user conversations to the log.
- **Access is decided on the backend.** Restrictions in the interface are only a convenience; every endpoint must check the role with `require_*` in `backend/auth.py`, and the RBAC tests must be updated.
- **Comments and text** are short and follow the surrounding files, which are mostly in Indonesian. If you are not comfortable writing Indonesian, write in English and we will help with the translation.

### Data in the repository

This repository is public. Never commit real server IPs, domains, usernames, passwords, tokens, or `.env` files, whether in code, tests or documentation. Use placeholders (`<vps-ip>`, `dashboard.<domain>`) or example addresses (`203.0.113.x`, `example.com`).

### Commits

We keep the history readable. There is no strict format, but please follow these habits, which the existing history already uses:

- **One logical change per commit.** The code, its tests and the documentation it affects go in the same commit, and the tests should pass at every commit. Split unrelated changes into separate commits.
- **Title:** one short line (about 70 characters or fewer), no trailing period, describing what changed in plain words. Indonesian or English are both fine. A short prefix such as `Backend:` or `Frontend:` is welcome when a change is limited to one side. We do not require Conventional Commits.
- **Body (optional but encouraged):** after a blank line, a few bullet points on what changed and, when it is not obvious, why. Wrap lines at roughly 80 characters.
- **Never rewrite a migration or a published commit.** Fix mistakes with a new commit. Rewrite history (`rebase`, `--amend`, force push) only on your own branch before it is merged, never on `main`.
- **Use your own name and email** in `git config`. If you do not want your address public, use the `noreply` address GitHub gives you.

Example:

```text
Superadmin can delete helpdesk tickets

- A Delete button in the ticket window (superadmin only), with a confirmation.
- A summary is written to the audit log before the ticket is removed;
  the conversation itself is not kept.
- Tests cover the permissions and the audit entry.
```

Before you commit:

```bash
git status                      # only the files you meant to change?
git diff --staged               # read it once; no secrets, no real IPs or domains
(cd backend && pytest -q)       # see "Running the tests" above for the environment variables
(cd frontend && npm run lint && npm test)
```

Never commit `.env`, backups, `node_modules/`, `dist/`, or any file with real credentials or user data. If a secret was committed by mistake, treat it as leaked: rotate it first, then remove it from the history.

### Pull requests

- One pull request per change, with a short description of what and why.
- Include tests for new behaviour or a fixed bug.
- Update the guides in `docs/` if the change affects how to install or use the dashboard.

---

## Bahasa Indonesia

Terima kasih sudah mau membantu CCD. Panduan ini singkat; untuk cara memasang dan memakai dashboard, lihat [README](README.md) dan [panduan deployment](docs/PANDUAN_DEPLOYMENT.md).

**Celah keamanan:** jangan lapor lewat issue publik. Ikuti [SECURITY.md](SECURITY.md).

### Sebelum mulai

Buka issue dulu untuk fitur atau perubahan besar supaya arahnya sama. Perbaikan kecil (typo, bug jelas) boleh langsung lewat pull request. Issue dan pull request boleh ditulis dalam bahasa Indonesia atau Inggris.

### Menjalankan test

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

### Aturan kode

- **Dua bahasa.** Semua teks yang terlihat pengguna harus ada dalam bahasa Indonesia dan Inggris.
  - Frontend: tambahkan pasangan `[Indonesia, English]` di `frontend/src/locales/*.js`, lalu pakai `t('kunci')`. Test `i18n.test.js` memeriksa kunci ganda, pasangan yang kosong, dan kunci yang dipakai tetapi tidak ada.
  - Backend: pesan galat dibungkus `tr("teks Indonesia", "English text")` dari `backend/i18n.py`. Test memeriksa bahwa kedua teks ada dan variabelnya sama.
- **Skema database** diubah lewat berkas baru `backend/migrations/V0NN__nama.sql` (urut, jangan mengubah migrasi lama), dan dijalankan otomatis saat backend start.
- **Aksi penting dicatat** di Activity Log lewat `log_activity(...)`: siapa, aksi apa, pada apa. Detailnya disusun dengan `both(lambda: tr("teks Indonesia", "English text"))` dari `services/audit.py` supaya tersimpan dalam dua bahasa; test gagal kalau ada yang berupa string biasa. Jangan menulis password, token, secret, atau isi percakapan pengguna ke log.
- **Akses ditentukan di backend.** Pembatasan di tampilan hanya kenyamanan; setiap endpoint harus memeriksa peran lewat `require_*` di `backend/auth.py`, dan test RBAC harus ikut diperbarui.
- **Komentar dan teks** singkat dan mengikuti gaya berkas di sekitarnya, yang sebagian besar berbahasa Indonesia. Kalau kurang nyaman menulis bahasa Indonesia, tulis dalam bahasa Inggris dan kami bantu menerjemahkan.

### Data di repositori

Repositori ini publik. Jangan memasukkan IP server, domain, nama pengguna, password, token, atau berkas `.env` yang asli, baik di kode, test, maupun dokumentasi. Pakai placeholder (`<ip-vps>`, `dashboard.<domain>`) atau alamat contoh (`203.0.113.x`, `example.com`).

### Commit

Kami menjaga riwayat tetap mudah dibaca. Tidak ada format yang kaku, tetapi tolong ikuti kebiasaan berikut, yang memang sudah dipakai di riwayat repo ini:

- **Satu perubahan logis per commit.** Kode, test-nya, dan dokumentasi yang terpengaruh ikut dalam commit yang sama, dan test sebaiknya lolos di setiap commit. Pisahkan perubahan yang tidak berhubungan menjadi commit sendiri.
- **Judul:** satu baris pendek (sekitar 70 karakter atau kurang), tanpa titik di akhir, menjelaskan apa yang berubah dengan kata-kata sederhana. Bahasa Indonesia atau Inggris sama-sama boleh. Awalan singkat seperti `Backend:` atau `Frontend:` boleh dipakai kalau perubahannya hanya di satu sisi. Conventional Commits tidak diwajibkan.
- **Isi (opsional tetapi dianjurkan):** setelah satu baris kosong, beberapa butir tentang apa yang berubah dan, kalau tidak jelas dengan sendirinya, mengapa. Potong baris sekitar 80 karakter.
- **Jangan menulis ulang migrasi atau commit yang sudah dipublikasikan.** Perbaiki kesalahan dengan commit baru. Menulis ulang riwayat (`rebase`, `--amend`, force push) hanya di cabang Anda sendiri sebelum digabung, tidak pernah di `main`.
- **Pakai nama dan email Anda sendiri** di `git config`. Kalau tidak ingin alamat email Anda terlihat publik, pakai alamat `noreply` yang diberikan GitHub.

Contoh:

```text
Superadmin bisa menghapus tiket Helpdesk

- Tombol Hapus di jendela tiket (khusus superadmin), dengan konfirmasi.
- Ringkasannya dicatat di audit log sebelum tiket dihapus;
  isi percakapan tidak disimpan.
- Test mencakup hak akses dan catatan auditnya.
```

Sebelum commit:

```bash
git status                      # hanya berkas yang memang ingin diubah?
git diff --staged               # baca sekali; tidak ada secret, IP atau domain asli
(cd backend && pytest -q)       # lihat "Menjalankan test" di atas untuk variabel lingkungannya
(cd frontend && npm run lint && npm test)
```

Jangan pernah meng-commit `.env`, backup, `node_modules/`, `dist/`, atau berkas apa pun yang berisi kredensial atau data pengguna asli. Kalau secret terlanjur ter-commit, anggap sudah bocor: ganti dulu secret-nya, baru hapus dari riwayat.

### Pull request

- Satu pull request untuk satu perubahan, dengan deskripsi singkat tentang apa dan mengapa.
- Sertakan test untuk perilaku baru atau bug yang diperbaiki.
- Perbarui panduan di `docs/` kalau perubahan memengaruhi cara memasang atau memakai.
