# Security Policy / Kebijakan Keamanan

[English](#english) | [Bahasa Indonesia](#bahasa-indonesia)

---

## English

Campus Cloud Dashboard (CCD) manages access to VMs and their credentials, so we take security reports seriously. Thank you for reporting responsibly.

### Reporting a vulnerability

**Please do not open a public issue for a security problem.** Report it privately through **Security → Report a vulnerability** on this repository's GitHub page (GitHub Private Vulnerability Reporting). Only the maintainers can read it.

If you do not have a GitHub account, or the feature is not available, email **arrizalrizki2004@gmail.com** with a subject starting with `[CCD Security]`. Do not put details of the vulnerability in a public issue title or comment.

Please include, if you can:
- the version or commit you tested, and how it was deployed (Docker Compose, with or without the `ssh` profile);
- short steps to reproduce and the impact (what data can be read or changed, without signing in or as a student account);
- logs or screenshots, **without** passwords, tokens or real user data.

We aim to reply within 7 days and to keep you updated until the issue is fixed. We will credit you in the fix notes if you want.

### Supported versions

Only the latest version on the main branch receives security fixes. Older installations need to be updated first (`git pull`, then `docker compose up -d --build`).

### Scope

In scope: bypassing sign-in or role restrictions (student, sysadmin, superadmin), reading or changing another user's data, leaking VM credentials or secrets, injection, breaking switch isolation, bypassing the SSH bastion, and flaws in the Open Web proxy.

Out of scope: weaknesses that need root access to the server or access to the `.env` file, attacks that need the victim's device to be compromised already, automated scanner reports without a clear impact, and denial-of-service attacks that only flood the service without a flaw in the code.

### For people who deploy CCD

A few things are the deployer's responsibility (details are in the deployment guide: [English, sections 1 to 3](docs/DEPLOYMENT.en.md) and the full [Indonesian guide](docs/PANDUAN_DEPLOYMENT.md)):
- Change the default admin password at first sign-in, and deactivate that account once you have your own superadmin account.
- Fill every secret in `backend/.env` with random values (`setup.sh` does this automatically) and never share that file or commit it to git.
- Restrict SSH access to the server with SSH keys, disable root password login, and install a firewall. Only the ports in use (80/443 and, if the bastion is enabled, 2222) need to be open.
- Put HTTPS in front of the dashboard and set `ALLOWED_ORIGINS` to the real address.
- Keep a copy of the database backups off the server (`backend/scripts/backup-db.sh` only stores them on the same disk).
- Set the audit log retention in the System tab according to your institution's policy. Logs cannot be deleted from the dashboard.
- Update dependencies and Docker images regularly.

---

## Bahasa Indonesia

Campus Cloud Dashboard (CCD) mengelola akses ke VM dan kredensialnya, jadi kami menanggapi laporan celah keamanan dengan serius. Terima kasih sudah melapor dengan bertanggung jawab.

### Melaporkan celah

**Jangan membuka issue publik untuk celah keamanan.** Laporkan secara privat lewat fitur **Security → Report a vulnerability** di halaman GitHub repositori ini (GitHub Private Vulnerability Reporting). Hanya pengelola repositori yang bisa membacanya.

Kalau Anda tidak punya akun GitHub atau fitur itu tidak tersedia, kirim email ke **arrizalrizki2004@gmail.com** dengan subjek diawali `[CCD Security]`. Jangan menaruh detail celah di judul issue atau komentar publik.

Sertakan, kalau bisa:
- versi atau commit yang diuji, dan cara memasangnya (Docker Compose, profil `ssh` aktif atau tidak);
- langkah mengulang yang singkat dan dampaknya (data apa yang bisa dibaca atau diubah, tanpa login atau dengan akun mahasiswa);
- log atau tangkapan layar, **tanpa** password, token, atau data pengguna asli.

Kami berusaha menjawab dalam 7 hari dan memberi kabar perkembangannya sampai celah diperbaiki. Kami akan mencantumkan nama Anda di catatan perbaikan kalau Anda mau.

### Versi yang didukung

Hanya versi terbaru di cabang utama yang menerima perbaikan keamanan. Pemasangan lama perlu diperbarui lebih dulu (`git pull` lalu `docker compose up -d --build`).

### Yang termasuk dan tidak

Termasuk: melewati login atau pembatasan peran (mahasiswa, sysadmin, superadmin), membaca atau mengubah data pengguna lain, kebocoran kredensial VM atau secret, injeksi, pelanggaran isolasi switch, melewati bastion SSH, serta celah di proxy Open Web.

Tidak termasuk: kelemahan yang butuh akses root ke server atau akses ke berkas `.env`, serangan yang butuh perangkat korban sudah terinfeksi, laporan pemindai otomatis tanpa dampak yang jelas, dan serangan yang hanya membanjiri layanan (DoS) tanpa celah di kode.

### Untuk yang memasang CCD

Beberapa hal yang menjadi tanggung jawab pemasang (rinciannya ada di [panduan deployment](docs/PANDUAN_DEPLOYMENT.md)):
- Ganti password akun admin bawaan pada login pertama, dan nonaktifkan akun itu setelah Anda punya akun superadmin sendiri.
- Isi semua secret di `backend/.env` dengan nilai acak (`setup.sh` melakukannya otomatis) dan jangan membagikan berkas itu atau memasukkannya ke git.
- Batasi akses SSH ke server dengan kunci SSH, matikan login root dengan password, dan pasang firewall. Hanya port yang dipakai (80/443 dan, kalau bastion aktif, 2222) yang perlu terbuka.
- Pasang HTTPS di depan dashboard dan isi `ALLOWED_ORIGINS` dengan alamat yang sebenarnya.
- Simpan salinan backup database di luar server (skrip `backend/scripts/backup-db.sh` hanya menyimpan di disk yang sama).
- Atur lama penyimpanan log audit di tab Sistem sesuai kebijakan institusi. Log tidak bisa dihapus dari dashboard.
- Perbarui dependensi dan image Docker secara berkala.
