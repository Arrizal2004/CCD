# Campus Cloud Dashboard (CCD)

**Bahasa Indonesia** | [English](README.en.md)

**Student Lab Portal**: portal lab praktikum self-hosted untuk Proxmox VE, dengan akses VM lewat browser.

Dashboard self-hosted untuk memberi mahasiswa akses ke VM lab praktikum lewat browser. Mahasiswa tidak perlu VPN, dan port SSH/RDP tidak perlu dibuka ke internet. CCD berjalan di atas Proxmox VE yang sudah ada.

Mahasiswa login, melihat VM yang ditugaskan kepadanya, lalu klik **Connect**. Sesi SSH atau RDP terbuka di tab browser lewat Apache Guacamole. Admin mengatur siapa boleh mengakses VM mana, membuat VM dari template, dan memantau sesi dari dashboard yang sama.

## Fitur

- Connect SSH dan RDP dari browser.
- Akses per VM atau per group. Mahasiswa hanya melihat VM miliknya, tanpa nama host Proxmox.
- Membuat VM dari template (clone dan cloud-init), snapshot dan rollback oleh mahasiswa, dan resize RAM, CPU, serta storage oleh admin. Resize hanya saat VM mati, dan storage hanya bisa diperbesar.
- VM massal per kelas (satu VM per anggota grup, langsung di-assign, progres di latar belakang, kredensial CSV) dan Create VM yang terisi otomatis dari spek Infra Request mahasiswa.
- SSH ke host Proxmox dari dashboard lewat Guacamole untuk sysadmin dan superadmin. Username dan password diminta setiap kali dan tidak disimpan. Instance Proxmox bisa ditambah sysadmin, tetapi hanya superadmin yang boleh mengubah atau menghapusnya.
- Open Web: membuka aplikasi web yang berjalan di VM dari dalam dashboard. Alamat privat dimuat lewat proxy dashboard.
- Tiket helpdesk, termasuk laporan langsung dari Detail VM, dan permintaan VM (Infra Request). Superadmin bisa menghapus tiket dan request yang sudah tidak diperlukan; ringkasannya tetap ada di audit log.
- Tampilan dashboard dan Connect bisa dipakai dari smartphone dan tablet.
- Dua bahasa, Indonesia dan Inggris, termasuk pesan galat dari server. Setiap pengguna memilih sendiri lewat tombol ID / EN.
- Halaman Status: kesehatan layanan serta resource VPS dashboard, live dan riwayat 30 hari.
- Masa sewa VM: VM dimatikan otomatis saat masa sewanya habis; mahasiswa bisa meminta perpanjangan lewat Helpdesk.
- Akun: impor dari CSV (termasuk grup kelas), masa berlaku akun, aktifkan/nonaktifkan massal, dan reset password oleh admin. Pengguna yang lupa password bisa mengirim permintaan dari halaman login tanpa email.
- Switch (jaringan) terisolasi per kelas dengan subnet sendiri, dibuat dari dashboard: internet lewat NAT dan tetap bisa di-Connect dari CCD. Switch berada di blok alamat per Proxmox yang bisa ditambah satu per satu; switch baru di blok yang sudah ada tidak perlu advertise route Tailscale lagi.
- Akun OS di dalam VM lewat QEMU Guest Agent: membuat user untuk VM yang dipakai bersama, reset password untuk pengguna yang lupa, dan hapus user, tanpa SSH atau password lama.
- Pengaturan Sistem untuk superadmin: nama, logo, warna aksen, bahasa bawaan (Indonesia/Inggris), zona waktu (jam dan tanggal berjalan di header), pengumuman, aturan pendaftaran, nilai bawaan (masa sewa VM, masa berlaku akun), lama penyimpanan log audit, dan alamat SSH bastion. Kategori helpdesk diatur dari halaman Helpdesk, dan pilihan OS (beserta logonya) dari halaman Infra Requests. Setiap sekolah atau kampus bisa menyesuaikan dashboard tanpa mengubah kode.
- SSH dari terminal sendiri (opsional): lewat bastion di VPS dengan SSH key, hanya ke VM milik pengguna. Bisa untuk `scp`, `sftp`, dan VS Code Remote-SSH.
- Audit & Remote:
  - Activity log yang bisa disaring per akun, aksi, tingkat, dan tanggal, diekspor ke CSV, dan tampil sesuai bahasa admin (Indonesia atau Inggris). Perubahan akun, grup, instance Proxmox, tiket, dan Infra Request ikut tercatat.
  - Rekap login gagal per akun dan per IP.
  - Sesi Remote, Open Web, dan SSH yang sedang aktif beserta riwayatnya (siapa, dari IP mana, ke VM mana). Admin bisa memutusnya, dan sekaligus mencabut akses ke VM itu atau menonaktifkan akunnya.
  - Semua catatan satu pengguna bisa dilihat dalam satu jendela. Lama penyimpanan log diatur di tab Sistem.

## Komponen

| Komponen | Peran |
|---|---|
| Proxmox VE | Hypervisor. Dashboard memakai API token dengan izin terbatas, tidak pernah lewat SSH ke host. |
| Apache Guacamole + guacd | Merender SSH dan RDP sebagai HTML5 di browser. |
| FastAPI (backend) | REST API, kontrol akses, sinkronisasi ke Guacamole, audit log. |
| React + Vite (frontend) | Antarmuka, dilayani nginx yang juga menjadi reverse proxy ke backend dan Guacamole. |
| PostgreSQL | Data aplikasi dan data Guacamole. |
| Redis | Cache, pembatasan login, daftar token yang dicabut. |
| Tailscale (opsional, di OS) | Menghubungkan VPS dengan Proxmox lewat subnet route, dan akses HTTPS dari luar. Tidak termasuk dalam docker-compose. |

Semua komponen di atas, kecuali Proxmox dan Tailscale, berjalan sebagai container dari satu file `backend/docker-compose.yml`.

## Instalasi

Kebutuhan: server Linux (Ubuntu, Debian, atau openSUSE) dengan akses root atau sudo, port 80 belum dipakai, dan server itu bisa menjangkau API Proxmox (port 8006). Kami menjalankannya di VPS 2 vCPU dengan RAM 2 GB.

```bash
git clone <url-repo> campus-cloud-dashboard
cd campus-cloud-dashboard
./setup.sh
```

`setup.sh` memasang Docker kalau belum ada, membuat `backend/.env` dengan secret dan password acak, membangun image satu per satu, lalu menjalankan semua container. Skrip ini aman dijalankan ulang dan tidak menimpa `.env` yang sudah ada.

Setelah selesai, buka `http://<ip-server>` dan login sebagai `admin` dengan password yang dicetak di akhir proses. Password admin Guacamole juga dicetak sekali. Simpan keduanya. Nama sistem dan aturan pendaftaran bisa diubah di tab **Sistem**.

Langkah berikutnya ada di [`docs/PANDUAN_DEPLOYMENT.md`](docs/PANDUAN_DEPLOYMENT.md):
- Menghubungkan VPS ke Proxmox lewat Tailscale (Bagian 1.6).
- Membuat pool, API token, dan izin di Proxmox (Bagian 2).
- Mendaftarkan Proxmox ke dashboard (Bagian 3).
- Membuat template VM (Bagian 4, detailnya di [`docs/PANDUAN_TEMPLATE_VM.md`](docs/PANDUAN_TEMPLATE_VM.md)).
- Akses dari luar jaringan, atau VPS privat dengan domain sendiri (Bagian 7 dan 7.1).
- Mengaktifkan SSH lewat bastion (Bagian 8). Panduan untuk mahasiswa ada di [`docs/PANDUAN_SSH.md`](docs/PANDUAN_SSH.md).
- Backup database dan menyalinnya ke luar server (Bagian 9).

## Struktur repo

```
backend/     FastAPI: routers/, services/, migrations/, tests/, scripts/ (backup), bastion/ (SSH), docker-compose.yml
frontend/    React + Vite: src/pages/, src/components/, nginx.conf
docs/        Panduan deployment, pembuatan template VM, dan SSH untuk mahasiswa
setup.sh     Instalasi satu perintah
SECURITY.md  Cara melaporkan celah keamanan
CONTRIBUTING.md  Panduan berkontribusi
```

## Pengembangan

```bash
# Backend: butuh PostgreSQL dan Redis, lihat .github/workflows/ci.yml untuk variabelnya
cd backend
pip install -r requirements.txt
pytest   # perintah lengkap dengan PostgreSQL dan Redis sementara ada di CONTRIBUTING.md

# Frontend
cd frontend
npm ci
npm run dev      # http://localhost:5173
npm run lint
npm test         # test unit dan komponen (Vitest)
npm run build
```

CI di `.github/workflows/ci.yml` menjalankan test backend, lint, test, dan build frontend di setiap push dan pull request.

## Keamanan

- Token Proxmox dibuat dengan privilege separation dan hanya diberi izin per path (pool, storage, dan opsional SDN serta node).
- Hak akses diperiksa di server. Menyembunyikan tombol di tampilan tidak dianggap sebagai kontrol akses.
- Kredensial VM dan token Proxmox disimpan terenkripsi di database aplikasi. Saat Connect, kredensial juga diberikan ke Guacamole dan tersimpan di database Guacamole tanpa enkripsi tambahan dari CCD.
- `setup.sh` membuat password admin, password database, dan semua secret secara acak, lalu membatasi izin `backend/.env` menjadi `600`.
- Hanya port 80 yang dibuka ke luar. Backend, Guacamole, PostgreSQL, dan Redis hanya bisa dijangkau dari server itu sendiri atau dari jaringan Docker. Bastion SSH (port 2222) hanya terbuka kalau diaktifkan, hanya menerima SSH key, tidak memberi shell, dan hanya meneruskan ke VM yang diizinkan dashboard.
- Login dibatasi: 5 kali gagal, akun terkunci 5 menit. Token JWT berlaku 8 jam dan dicabut saat logout. Semua sesi akun berakhir saat password-nya diganti atau direset, dan password sementara dari admin wajib diganti saat login.
- `/healthz` terbuka tanpa login, tetapi pesan error dan detail Proxmox hanya ditampilkan untuk admin.
- Aktivitas penting tercatat di audit log, termasuk siapa yang menghapus tiket atau mengubah akun. Log tidak bisa dihapus dari dashboard dan otomatis dibersihkan setelah masa penyimpanannya (bawaan 180 hari, bisa diatur). Isi sesi remote dan isi percakapan tiket yang dihapus sengaja tidak direkam demi privasi.

Dashboard tidak menyediakan HTTPS sendiri. Untuk akses dari luar jaringan, pakai `tailscale serve` atau `tailscale funnel` (Bagian 7 panduan deployment), atau reverse proxy dengan sertifikat TLS.

Cara melaporkan celah keamanan ada di [SECURITY.md](SECURITY.md); jangan lewat issue publik.

## Batasan yang diketahui

- Login hanya dengan akun lokal dashboard.
- Peran yang tersedia: superadmin, sysadmin, dan mahasiswa. Tidak ada kuota sumber daya per mahasiswa.
- Belum ada autentikasi dua faktor (2FA). Lindungi akun superadmin dengan password yang kuat.
- Backup bawaan hanya disimpan di server yang sama; salin ke luar server sendiri (lihat Bagian 9 panduan).
- Notifikasi keluar (email atau pesan) belum ada; kondisi layanan dilihat di halaman Status.
- Reset password akun dashboard dilakukan admin; dashboard tidak mengirim email.
- Hanya mendukung Proxmox VE.
- Dipakai pada skala satu lab praktikum.

## Status

Prototype yang sudah berjalan di lab praktikum. Proyek ini berawal sebagai proyek riset dan dibawakan di openSUSE Summit Asia 2026.

## Berkontribusi

Panduan kontribusi ada di [CONTRIBUTING.md](CONTRIBUTING.md).

## Lisensi

MIT. Lihat [LICENSE](LICENSE).
