# Kebijakan Keamanan

Campus Cloud Dashboard (CCD) mengelola akses ke VM dan kredensialnya, jadi kami menanggapi laporan celah keamanan dengan serius. Terima kasih sudah melapor dengan bertanggung jawab.

## Melaporkan celah

**Jangan membuka issue publik untuk celah keamanan.** Laporkan secara privat lewat fitur **Security → Report a vulnerability** di halaman GitHub repositori ini (GitHub Private Vulnerability Reporting). Hanya pengelola repositori yang bisa membacanya.

Kalau Anda tidak punya akun GitHub atau fitur itu tidak tersedia, kirim email ke **arrizalrizki2004@gmail.com** dengan subjek diawali `[CCD Security]`. Jangan menaruh detail celah di judul issue atau komentar publik.

Sertakan, kalau bisa:
- versi atau commit yang diuji, dan cara memasangnya (Docker Compose, profil `ssh` aktif atau tidak);
- langkah mengulang yang singkat dan dampaknya (data apa yang bisa dibaca atau diubah, tanpa login atau dengan akun mahasiswa);
- log atau tangkapan layar, **tanpa** password, token, atau data pengguna asli.

Kami berusaha menjawab dalam 7 hari dan memberi kabar perkembangannya sampai celah diperbaiki. Kami akan mencantumkan nama Anda di catatan perbaikan kalau Anda mau.

## Versi yang didukung

Hanya versi terbaru di cabang utama yang menerima perbaikan keamanan. Pemasangan lama perlu diperbarui lebih dulu (`git pull` lalu `docker compose up -d --build`).

## Yang termasuk dan tidak

Termasuk: melewati login atau pembatasan peran (mahasiswa, sysadmin, superadmin), membaca atau mengubah data pengguna lain, kebocoran kredensial VM atau secret, injeksi, pelanggaran isolasi switch, melewati bastion SSH, serta celah di proxy Open Web.

Tidak termasuk: kelemahan yang butuh akses root ke server atau akses ke berkas `.env`, serangan yang butuh perangkat korban sudah terinfeksi, laporan pemindai otomatis tanpa dampak yang jelas, dan serangan yang hanya membanjiri layanan (DoS) tanpa celah di kode.

## Untuk yang memasang CCD

Beberapa hal yang menjadi tanggung jawab pemasang (rinciannya ada di [panduan deployment](docs/PANDUAN_DEPLOYMENT.md)):
- Ganti password akun admin bawaan pada login pertama, dan nonaktifkan akun itu setelah Anda punya akun superadmin sendiri.
- Isi semua secret di `backend/.env` dengan nilai acak (`setup.sh` melakukannya otomatis) dan jangan membagikan berkas itu atau memasukkannya ke git.
- Batasi akses SSH ke server dengan kunci SSH, matikan login root dengan password, dan pasang firewall. Hanya port yang dipakai (80/443 dan, kalau bastion aktif, 2222) yang perlu terbuka.
- Pasang HTTPS di depan dashboard dan isi `ALLOWED_ORIGINS` dengan alamat yang sebenarnya.
- Simpan salinan backup database di luar server (skrip `backend/scripts/backup-db.sh` hanya menyimpan di disk yang sama).
- Perbarui dependensi dan image Docker secara berkala.
