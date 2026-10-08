# Panduan Deployment — Campus Cloud Dashboard (CCD)

Panduan lengkap dari VPS kosong sampai mahasiswa bisa `Connect` ke VM lab lewat browser. Ditulis berdasarkan langkah yang sudah kami jalankan dan verifikasi.

**Urutan:** (1) Deploy dashboard di VPS → (2) Siapkan Proxmox (token, user, pool) → (3) Daftarkan Proxmox ke dashboard → (4) Buat template → (5) Deploy VM dari dashboard → (6) Connect dan Open Web.

---

## 1. Clone & Deploy Dashboard di VPS

### 1.1 Prasyarat
- VPS/server Linux (Ubuntu/Debian/openSUSE — stack-nya full container, jadi distro-agnostic) dengan akses SSH root/sudo.
- VPS ini **tidak perlu** satu jaringan dengan Proxmox — cukup bisa mengakses `https://<ip-proxmox>:8006` (port API Proxmox) lewat jaringan apa pun (LAN, VPN, atau internet kalau Proxmox-nya expose port itu).
- Docker **tidak perlu** sudah terpasang — `setup.sh` (Bagian 1.2) akan mengecek dan menginstall otomatis kalau belum ada.

### 1.2 Cara Cepat — `./setup.sh` (direkomendasikan)

```bash
git clone <url-repo-anda> campus-cloud-dashboard
cd campus-cloud-dashboard
./setup.sh
```

Skrip ini otomatis:
1. Cek Docker + Docker Compose plugin — kalau belum ada, install (Ubuntu/Debian lewat installer resmi Docker; openSUSE lewat `zypper`; distro lain dicoba lewat installer resmi Docker sebagai fallback).
2. Buat `backend/.env` dari `.env.example`. Semua secret dan password dibuat acak dengan `openssl rand`: `JWT_SECRET`, `AGENT_ENC_SECRET`, `GUAC_ADMIN_PASS`, `INITIAL_ADMIN_PASSWORD`, `POSTGRES_PASSWORD`, `GUAC_DB_PASSWORD`, dan `BASTION_TOKEN`. `ALLOWED_ORIGINS` diisi dari IP server yang terdeteksi. File ini diberi izin `600`.
3. Memastikan port 80 kosong, membangun image backend lalu frontend satu per satu, menjalankan semua container, dan menunggu backend siap.
4. Memasang cron backup database harian (Bagian 9).
5. Kalau RAM server di bawah 4 GB dan belum ada swap, membuat swapfile 2 GB (`/swapfile`), supaya container tidak dimatikan kernel saat RAM penuh.
6. Mencetak ringkasan: URL dashboard, password admin dashboard, dan password admin Guacamole. Keduanya hanya dicetak sekali, jadi simpan.

**Idempotent** — aman dijalankan ulang kapan saja (redeploy setelah `git pull`, misalnya): tidak akan reinstall Docker yang sudah ada, dan tidak akan menimpa `backend/.env` yang sudah ada.

Update ke versi terbaru cukup dengan:
```bash
git pull
./setup.sh
```
Image dibangun ulang (termasuk bastion kalau aktif), container diganti, dan isi database serta `backend/.env` tidak berubah. Dashboard tidak bisa diakses selama sekitar satu menit saat container diganti.

Kalau `backend/.env` sudah pernah dibuat manual (Bagian 1.3 di bawah) sebelum menjalankan `setup.sh`, skrip ini otomatis memakainya apa adanya.

Lanjut ke **Bagian 1.5** (login pertama kali), lalu **Bagian 2**.

### 1.3 Cara Manual (alternatif, kalau mau kontrol tiap langkah)

Prasyarat tambahan untuk jalur ini: Docker + Docker Compose plugin sudah terpasang (`docker compose version` harus jalan).

```bash
git clone <url-repo-anda> campus-cloud-dashboard
cd campus-cloud-dashboard/backend
```

Buat file `.env` di folder `backend/`. Variabel yang **wajib**:

| Variabel | Cara isi | Keterangan |
|---|---|---|
| `JWT_SECRET` | `openssl rand -hex 32` | Dipakai untuk tanda tangan JWT **dan** sebagai basis kunci enkripsi (Fernet) yang menyimpan kredensial VM & token Proxmox di database. Jangan pernah expose/commit nilai ini. |
| `GUAC_ADMIN_PASS` | Password kuat pilihan Anda | Password admin Guacamole (`guacadmin`) akan otomatis dirotasi ke nilai ini saat backend start pertama kali — menggantikan password default `guacadmin/guacadmin` yang publik diketahui. |
| `ALLOWED_ORIGINS` | Domain/IP dashboard Anda, mis. `http://vps-ip` atau `https://dashboard.domain.com` | CORS. Kalau dashboard diakses lewat domain berbeda-beda (mis. LAN + Tailscale), pisahkan dengan koma. |
| `INITIAL_ADMIN_PASSWORD` | Password kuat pilihan Anda | Password akun `admin` yang dibuat saat database masih kosong. Kalau dikosongkan, dipakai `admin123` yang wajib langsung diganti. |
| `POSTGRES_PASSWORD`, `GUAC_DB_PASSWORD` | `openssl rand -hex 24` | Password database aplikasi dan database Guacamole. Hanya berlaku saat volume database pertama kali dibuat. |

Variabel **opsional**:

| Variabel | Kapan diisi |
|---|---|
| `AGENT_ENC_SECRET` | `openssl rand -hex 32` — mengaktifkan enkripsi payload Redis. Kosongkan kalau tidak perlu (mode kompatibel, tetap aman untuk kredensial karena itu sudah dienkripsi lewat `JWT_SECRET` di atas). |
| `COMPOSE_PROFILES`, `BASTION_*` | Hanya kalau mengaktifkan SSH lewat bastion. Lihat Bagian 8. |

Contoh `.env` minimal:
```bash
JWT_SECRET=<hasil openssl rand -hex 32>
GUAC_ADMIN_PASS=<password-kuat-anda>
ALLOWED_ORIGINS=http://<ip-vps>
INITIAL_ADMIN_PASSWORD=<password-kuat-anda>
POSTGRES_PASSWORD=<hasil openssl rand -hex 24>
GUAC_DB_PASSWORD=<hasil openssl rand -hex 24>
```
Batasi akses file ini: `chmod 600 .env`.

### 1.4 Jalankan Docker Compose (jalur manual)
Dashboard memakai port 80, jadi pastikan port itu tidak dipakai web server lain (`sudo ss -ltnp 'sport = :80'`). Bangun image satu per satu supaya VPS kecil tidak kehabisan memori:
```bash
sudo docker compose build backend
sudo docker compose build frontend
sudo docker compose up -d
```

Tunggu sampai semua container `Up`:
```bash
sudo docker compose ps
```
Harus ada 6 container: `ccd-backend`, `ccd-frontend`, `ccd-guacamole`, `ccd-guacd`, `ccd-postgres`, `ccd-redis`. Kalau bastion diaktifkan (Bagian 8), ada satu lagi: `ccd-bastion`.

Di deploy pertama, `up` terasa lebih lama (1-2 menit) karena backend sengaja menunggu Postgres, Redis, guacd, dan Guacamole berstatus *healthy* dulu (lihat kolom `STATUS`), supaya password admin Guacamole pasti terpasang sebelum Connect dipakai. Kalau backend tidak kunjung start, cek container mana yang belum healthy:
```bash
sudo docker compose ps
sudo docker compose logs --tail=30 guacamole
```

Cek log backend untuk pastikan tidak ada error saat startup:
```bash
sudo docker compose logs --tail=30 backend
```

### 1.5 Login pertama kali
Buka `http://<ip-vps>` di browser dan login sebagai `admin`. Akun ini dibuat otomatis saat backend pertama kali jalan dengan database kosong. Passwordnya:
- Lewat `./setup.sh`: password acak yang dicetak di akhir proses (juga ada di `backend/.env`, variabel `INITIAL_ADMIN_PASSWORD`).
- Lewat jalur manual: nilai `INITIAL_ADMIN_PASSWORD` yang Anda isi, atau `admin123` kalau dikosongkan.

Ganti password setelah login pertama lewat menu Profil (pojok kanan atas). Ini wajib kalau Anda memakai `admin123`, karena password itu tertulis di kode sumber.

Lalu buka tab **Sistem** (khusus superadmin) untuk menyesuaikan dashboard dengan sekolah atau kampus Anda:
- **Identitas dan tampilan:** nama sistem, nama singkat (header di HP), nama institusi, tagline, logo (PNG/JPG/WebP, maks 512 KB), dan warna aksen. Dipakai di judul dan ikon tab browser, halaman login, header, footer, dan jendela Tentang. Warna yang terlalu gelap ditolak supaya teks di tombol tetap terbaca.
- **Bahasa dan tema bawaan:** bahasa Indonesia atau Inggris, tema gelap, terang, atau ikuti perangkat pengguna. Setiap pengguna, termasuk yang belum punya akun di halaman login, bisa mengganti lewat tombol **ID / EN** dan tombol ikon matahari atau bulan; pilihannya disimpan di browser masing-masing. Seluruh halaman dan pesan galat dari server mengikuti bahasa pilihan pengguna. Catatan yang sudah tersimpan, seperti Activity Log dan pesan sistem di tiket, tetap dalam bahasa saat dicatat.
- **Pengumuman:** teks dengan jenis Info, Peringatan, atau Penting, bisa diberi waktu tampil, dan bisa ditampilkan juga di halaman login. Pengguna bisa menutup pengumuman Info dan Peringatan.
- **Zona waktu:** zona waktu dashboard (bawaan Asia/Jakarta, WIB). Dipakai untuk jam dan tanggal yang berjalan di header, di dekat tombol Tentang, semua tanggal dan jam di dashboard, dan berkas CSV yang diekspor. Yang berubah hanya cara menampilkannya; catatan lama tidak ikut berubah. Zona Indonesia (WIB, WITA, WIT) ada di urutan pertama.
- **Nilai bawaan:** masa sewa untuk VM baru (terisi otomatis di form Create VM) dan masa berlaku untuk akun baru (pendaftaran mandiri dan baris impor CSV tanpa `expires_at`).
- **Penyimpanan log audit:** berapa hari Activity Log dan riwayat sesi Remote, Web, dan SSH disimpan sebelum dihapus otomatis (minimal 7 hari). Kalau dikosongkan dipakai `AUDIT_RETENTION_DAYS` di `.env` (bawaan 180). Di bawahnya tampil jumlah entri, entri tertua, dan ukurannya. Pembersihan berjalan tiap 6 jam dan tidak bisa dibatalkan, jadi mempersingkat angka ini menghapus catatan lama pada pembersihan berikutnya.
- **Alamat SSH untuk pengguna:** nama host atau IP bastion SSH yang tampil di perintah SSH mahasiswa (Bagian 8). Ganti di sini kalau domain berubah, tanpa menyunting `.env`.
- **Kategori tiket Helpdesk** diatur dari halaman **Helpdesk** (tombol *Kelola kategori*, superadmin): ganti nama, tambah, atau hapus. *Perpanjang Sewa* dan *Lainnya* selalu ada karena dipakai sistem.
- **Pilihan OS di Infra Request** diatur dari halaman **Infra Requests** (tombol *Kelola pilihan OS*, superadmin): daftar OS yang bisa dipilih mahasiswa saat mengajukan VPS (bawaan: Windows dan Ubuntu). Bisa ditambah, diganti nama, diurutkan, atau dihapus, minimal satu. Tiap OS boleh diberi logo (PNG, JPG, atau WebP, maks 256 KB) yang tampil di samping namanya; logo ikut pindah kalau OS diganti nama dan terhapus kalau OSnya dihapus. Yang paling atas terpilih otomatis. Request lama tetap menampilkan OS yang dipilih waktu itu.
- **Pendaftaran mandiri:** bisa dibuka atau ditutup. Daftar email yang diizinkan berisi satu aturan per baris: `@kampus.ac.id` menerima semua email di domain itu termasuk subdomainnya (mis. `@student.kampus.ac.id`), alamat lengkap hanya menerima email itu. Kalau daftar kosong, email apa pun diterima dan email tetap opsional. Halaman login hanya menampilkan domainnya, bukan alamat perorangan.

Dashboard tidak mengirim email verifikasi, jadi daftar email hanya menyaring email yang diketik. Akses penuh tetap menunggu verifikasi admin (lewat Infrastructure Request). Satu email hanya bisa dipakai satu akun. Setiap perubahan pengaturan tercatat di Activity Log.

### 1.6 Menghubungkan VPS ke Proxmox lewat Tailscale
VPS dashboard harus bisa menjangkau API Proxmox (port 8006) dan IP setiap VM (untuk SSH, RDP, dan Open Web). Kalau VPS dan Proxmox tidak satu jaringan, cara yang kami pakai adalah Tailscale dengan subnet route. Perintah dijalankan sebagai root.

1. Pasang Tailscale di VPS dan di setiap Proxmox, lalu login ke tailnet yang sama:
   ```bash
   curl -fsSL https://tailscale.com/install.sh | sh
   tailscale up
   ```
2. Di setiap Proxmox, aktifkan IP forwarding:
   ```bash
   echo 'net.ipv4.ip_forward = 1' > /etc/sysctl.d/99-tailscale.conf
   sysctl -p /etc/sysctl.d/99-tailscale.conf
   ```
3. Di setiap Proxmox, iklankan subnet VM-nya (ganti dengan subnet Anda):
   ```bash
   tailscale up --advertise-routes=10.0.1.0/24
   ```
   Kalau ditolak karena flag sebelumnya, ulangi dengan semua flag lama, atau pakai `tailscale set --advertise-routes=...`.
4. Di admin console Tailscale: Machines, pilih mesin Proxmox, Edit route settings, lalu setujui subnet-nya.
5. Di VPS, terima route tersebut, lalu uji:
   ```bash
   tailscale up --accept-routes
   ip route get <ip-sebuah-vm>     # harus lewat tailscale0
   ```

Kalau tailnet memakai ACL kustom, pastikan VPS diizinkan mengakses subnet itu. Saat mendaftarkan Proxmox ke dashboard (Bagian 3), isi Host dengan IP tailnet Proxmox, bukan IP LAN-nya.

Kalau nanti memakai fitur Switch (Bagian 5.6), blok alamat switch ikut diiklankan dengan cara yang sama oleh skrip penyiapan host: sekali per blok, bukan per switch.

---

## 2. Siapkan Proxmox — User, Pool, API Token

Dashboard **tidak pernah** login ke Proxmox pakai username/password — semua lewat **API token yang di-scope ketat** (cuma bisa lihat/kontrol VM di dalam satu Pool tertentu, plus storage yang diizinkan). Ini yang membuat token aman dibawa-bawa di `.env`/database dashboard.

Jalankan semua perintah berikut **di server Proxmox** (SSH sebagai root, atau lewat Shell di web GUI Proxmox: Datacenter → node → Shell).

### 2.1 Buat Pool
```bash
pveum pool add campus-cloud --comment "Campus Cloud Dashboard"
```
Semua VM & template yang mau dikelola dashboard harus berada di pool ini — token dashboard cuma bisa "melihat" isi pool ini, bukan seluruh Proxmox.

### 2.2 Buat API Token
Dua opsi — pilih salah satu:

**Opsi A — token dari user `root@pam` (yang kami pakai):**
```bash
pveum user token add root@pam ccd-dashboard --privsep 1
```
`--privsep 1` penting — artinya token ini **tidak** otomatis punya hak akses root, dia tunduk sepenuhnya ke ACL yang di-grant manual di langkah 2.3. Tanpa privsep, token akan punya akses penuh setara root (jangan pakai itu).

**Opsi B — user PVE khusus, lebih terisolasi (best practice kalau mau lebih hati-hati):**
```bash
pveum user add ccd@pve --comment "Campus Cloud Dashboard service account"
pveum user token add ccd@pve dashboard --privsep 1
```

Setelah perintah di atas dijalankan, Proxmox akan mencetak **token secret** — **ini cuma muncul sekali**, salin dan simpan sekarang. Formatnya:
```
┌──────────────┬──────────────────────────────────────┐
│ key          │ value                                  │
├──────────────┼──────────────────────────────────────┤
│ full-tokenid │ root@pam!ccd-dashboard                 │
│ value        │ xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx   │
└──────────────┴──────────────────────────────────────┘
```
`full-tokenid` = **Token ID** dan `value` = **Token Secret**, dua-duanya perlu diisi ke dashboard nanti (Bagian 3).

### 2.3 Beri Izin (ACL) ke Token

Ganti `root@pam!ccd-dashboard` di bawah dengan token ID Anda, dan `<storage-disk>` dengan nama storage tempat disk VM disimpan (cek dengan `pvesm status`, biasanya `local-lvm`).

**Wajib:**
```bash
# Kontrol penuh atas pool campus-cloud (create/delete VM, snapshot, dst — tapi HANYA di dalam pool ini)
pveum acl modify /pool/campus-cloud --tokens 'root@pam!ccd-dashboard' --roles PVEAdmin

# Baca/tulis disk VM di storage tempat VM disimpan
pveum acl modify /storage/<storage-disk> --tokens 'root@pam!ccd-dashboard' --roles PVEDatastoreUser
```

**Opsional** (aktifkan sesuai fitur yang mau dipakai):
```bash
# Kalau bridge jaringan VM dikelola lewat SDN zone (bukan Linux bridge biasa vmbr0)
pveum acl modify /sdn/zones/<nama-zone>/<bridge> --tokens 'root@pam!ccd-dashboard' --roles PVESDNUser

# Kalau mau tampilkan panel "Host Performance" (CPU/RAM/Disk/Network live) di dashboard utama (hanya tampil untuk admin)
pveum acl modify /nodes/<nama-node> --tokens 'root@pam!ccd-dashboard' --roles PVEAuditor
```

Verifikasi ACL sudah terpasang:
```bash
pveum acl list
```

Catatan: ACL eksplisit tetap diperlukan walaupun tokennya milik `root@pam`, karena `--privsep 1`. Token dengan privsep tidak mewarisi hak akses super-user dari `root` — dia diperlakukan sebagai identitas terpisah yang harus di-ACL manual, persis seperti user PVE biasa. Ini yang membuat token aman dipakai aplikasi eksternal.

---

## 3. Daftarkan Proxmox ke Dashboard

1. Login ke CCD sebagai admin/superadmin.
2. Buka tab **Integrations** (menu atas, hanya muncul untuk superadmin/sysadmin).
3. **Kelola Instance** → **+ Tambah Instance**.
4. Isi:
   - **Label**: nama bebas, mis. `lab` (dipakai internal sebagai identifier, tidak tampil ke mahasiswa).
   - **Host**: `<ip-proxmox>:8006`.
   - **Token ID**: dari langkah 2.2, mis. `root@pam!ccd-dashboard`.
   - **Token Secret**: dari langkah 2.2.
   - **Verifikasi sertifikat SSL**: matikan kalau Proxmox pakai sertifikat self-signed (default Proxmox baru install).
5. Simpan. Dashboard langsung mencoba konek — kalau berhasil, node & VM di dalam pool `campus-cloud` akan muncul di tab **Servers**.

Kalau gagal konek, cek lagi: token ID/secret benar, ACL pool sudah di-grant (Bagian 2.3), dan port 8006 Proxmox bisa dijangkau dari VPS dashboard (`curl -k https://<ip-proxmox>:8006` dari VPS).

---

## 4. Buat Template

Ini dilakukan **manual di Proxmox** (bukan lewat dashboard) — dashboard hanya mengelola akses ke VM yang templatenya sudah jadi, bukan proses instalasi OS.

> Panduan konfigurasi di dalam guest (Ubuntu & openSUSE) secara lebih detail — termasuk pitfall cloud-init/datasource yang paling sering bikin clone gagal Connect — ada di **[`docs/PANDUAN_TEMPLATE_VM.md`](PANDUAN_TEMPLATE_VM.md)**. Ringkasan langkahnya:

1. **Siapkan ISO** — upload lewat GUI Proxmox (Datacenter → storage → ISO Images → Upload), atau download langsung dari node (Download from URL).
2. **Buat VM baru**, attach ISO tadi sebagai CD-ROM boot.
3. **Install OS** seperti biasa lewat Console Proxmox (noVNC bawaan Proxmox — dashboard CCD sengaja tidak menyediakan console instalasi sendiri).
4. **Di dalam guest**, sebelum dimatikan:
   - Install `qemu-guest-agent` (`apt install qemu-guest-agent` / `zypper install qemu-guest-agent`), aktifkan servicenya.
   - Untuk Linux: pastikan `cloud-init` terpasang (biasanya sudah ada di cloud image; kalau install manual dari ISO server biasa, install manual: `apt install cloud-init`).
   - **Generalisasi**: jalankan `sudo cloud-init clean` (Linux) supaya machine-id, SSH host key, dan state cloud-init lama tidak ikut ter-clone ke semua VM turunannya. Untuk Windows, jalankan `sysprep`.
5. **Matikan VM** (bukan cuma shutdown OS, tunggu status Stopped di Proxmox).
6. **Hardware → Add → CloudInit Drive** — pilih storage yang sama dengan disk VM.
7. **Options → QEMU Guest Agent → Enabled**.
8. **Pastikan VM ini ada di dalam pool `campus-cloud`** — kalau belum, klik kanan VM → *Move to Pool*, pilih `campus-cloud`. Ini wajib, kalau tidak template tidak akan terlihat oleh token dashboard.
9. Klik kanan VM → **Convert to Template**.

Template siap. Cek dari dashboard: **Servers → + Create VM** — template ini harus muncul di dropdown pilihan.

> Ulangi langkah ini untuk tiap OS yang mau disediakan (mis. satu template Ubuntu, satu template openSUSE) — makin banyak template siap pakai, makin cepat mahasiswa dapat VM baru.

---

## 5. Deploy VM dari CCD

1. Login sebagai admin/sysadmin.
2. Tab **Servers** → pastikan instance & node Proxmox yang benar terpilih di toolbar atas.
3. Klik **+ Create VM**.
4. Isi form:
   - **Template**: pilih dari daftar template yang sudah dibuat di Bagian 4.
   - **Nama VM / hostname**: unik per node (dipakai juga sebagai nama koneksi Guacamole).
   - **CPU / RAM / Disk**: boleh dikosongkan untuk ikut nilai template, atau override (disk cuma boleh lebih besar dari template, tidak bisa dikecilkan).
   - **Username & Password**: akun OS yang akan dibuatkan cloud-init di dalam VM baru — inilah yang dipakai untuk Connect nanti.
   - **Mode IP**: *Statis* (isi IP/CIDR + gateway, dashboard otomatis cek supaya tidak bentrok dengan VM lain) atau *DHCP*.
   - **Full clone** vs **linked clone** (default) — linked clone jauh lebih cepat & hemat storage, cukup untuk kebanyakan kasus praktikum.
   - **Nyalakan VM setelah dibuat** — biarkan tercentang supaya bisa langsung dites.
5. Klik **Create VM**, tunggu proses clone + cloud-init + boot (biasanya di bawah 1 menit untuk linked clone).
6. Setelah selesai, dashboard otomatis:
   - Menyimpan kredensial VM (terenkripsi) untuk dipakai tombol Connect.
   - Membuat koneksi Guacamole yang sesuai (SSH untuk Linux, RDP untuk Windows).

### 5.1 Assign VM ke Mahasiswa
Supaya mahasiswa bisa melihat & connect ke VM ini:
- **Per-VM langsung**: buka detail VM → tab *Penugasan* → tambahkan user.
- **Lewat Group** (direkomendasikan untuk satu kelas): tab **Groups** → buat group → tambahkan anggota (mahasiswa) → tambahkan VM ke group. Semua anggota otomatis dapat akses ke semua VM di group itu.

**VM massal per kelas.** Di Servers, pilih Proxmox dan node lalu klik **⧉ VM Massal**. Pilih grup kelas, template, spek tiap VM, jaringan (switch atau bridge template dengan DHCP), username OS (username tiap mahasiswa atau satu username untuk semua), masa sewa, dan apakah VM langsung dinyalakan. **Lihat rencana** menampilkan nama VM per mahasiswa, nama yang bentrok (otomatis tidak dicentang), kebutuhan RAM dibanding RAM kosong node, dan sisa IP switch. Setelah **Buat**, VM dibuat satu per satu di latar belakang lewat jalur yang sama dengan Create VM dan langsung di-assign ke mahasiswanya. Jendela boleh ditutup; progres bisa dibuka lagi dari tombol yang sama. VM yang gagal bisa diulang, dan batch yang terputus karena backend berhenti bisa dilanjutkan. Password OS dibuat acak per VM dan bisa diunduh sebagai CSV (tercatat di Activity Log); mahasiswa tidak memerlukannya untuk Connect.

**Dari Infra Request.** Di detail request VPS, **Buat VM sesuai request** membuka Create VM yang sudah terisi spek permintaan mahasiswa (vCPU, RAM, disk), template yang namanya cocok dengan OS yang diminta, nama `vps-<username>`, dan password acak; admin tinggal memilih Proxmox dan node. VM yang dibuat langsung tertaut ke request. Untuk menautkan VM yang sudah ada, ketik nama, VMID, CCDID, atau host di kotak pencarian. Saat request dikonfirmasi **Selesai**, VM yang tertaut otomatis di-assign ke mahasiswa yang meminta.

### 5.2 Ubah RAM / CPU / Storage (Resize)
Hanya superadmin dan sysadmin. Tombol **Resize** di daftar VM aktif kalau VM sudah **mati** (status `stopped`):
- RAM dan CPU bisa dinaikkan atau diturunkan, dalam batas kapasitas node.
- Storage **hanya bisa diperbesar**, tidak bisa diperkecil.
- Setelah storage diperbesar dan VM dinyalakan, partisi dan filesystem di dalam VM harus diperluas manual (lihat `docs/PANDUAN_TEMPLATE_VM.md`, Bagian F). Proxmox hanya memperbesar disknya.
- Setiap perubahan tercatat di Activity Log.

---

### 5.3 Masa Sewa VM
Setiap VM bisa diberi batas masa sewa: di form **Create VM** (kolom *Masa sewa*), atau kapan saja lewat Detail VM, bar **Masa sewa**: **+7 hari**, **+30 hari**, **−7 hari**, pilih tanggal, atau **Tanpa batas**. Menambah hari pada sewa yang sudah habis dihitung dari hari ini. Kolom **Sewa** di daftar VM menampilkan sisa harinya: kuning kalau tinggal 3 hari atau kurang, merah kalau habis.

Saat masa sewa habis, VM yang masih menyala dimatikan otomatis (shutdown biasa, dipaksa mati kalau 2 menit tidak merespons). Mahasiswa tidak bisa menyalakannya lagi; admin tetap bisa. Data di dalam VM tidak dihapus. Mahasiswa bisa meminta perpanjangan lewat Detail VM → **Minta perpanjangan**, yang membuat tiket Helpdesk kategori *Perpanjang Sewa*. Setelah admin menambah masa sewa, VM bisa dinyalakan lagi. Semua perubahan dan pematian otomatis tercatat di Activity Log.

### 5.4 Akun Mahasiswa: Impor, Masa Berlaku, dan Aksi Massal
Di tab **Users** (superadmin):
- **Impor CSV** membuat banyak akun sekaligus. Kolom: `username, full_name, email, password, role, expires_at, group`; hanya `username` dan `full_name` yang wajib. Password kosong dibuatkan acak dan bisa diunduh sekali setelah impor. `group` (nama grup yang sudah ada) langsung memasukkan akun ke grup kelasnya. Semua baris dicek dulu; kalau satu saja salah, tidak ada akun yang dibuat. Template CSV bisa diunduh dari jendela impor. Akun hasil impor langsung terverifikasi.
- **Masa berlaku akun** diatur per akun (Edit) atau massal. Setelah lewat, akun tidak bisa login, token lamanya ditolak, dan sesi remote-nya diputus. Memperpanjang masa berlaku langsung memulihkan aksesnya.
- **Aksi massal**: centang akun (atau saring dengan filter grup lalu centang semua), lalu **Aktifkan**, **Nonaktifkan**, **Atur masa berlaku**, atau **Tanpa batas**. Akun Anda sendiri tidak ikut diubah.

Menonaktifkan akun, mengubah masa berlakunya, atau menghapusnya langsung berlaku: dashboard memeriksa status akun di setiap permintaan, bukan hanya saat login.

**Lupa password akun dashboard.** Dashboard tidak mengirim email, jadi password direset oleh admin:
- Di tab **Users**, tombol **Reset** (superadmin, untuk semua akun selain akunnya sendiri) atau **Reset password** (sysadmin, hanya akun mahasiswa) membuat password sementara acak. Password itu hanya ditampilkan sekali. Berikan langsung ke pemilik akun.
- Begitu direset, password lama dan semua sesi login akun itu berakhir, sesi remote yang sedang berjalan diputus, dan kuncian karena salah password dihapus. Saat login dengan password sementara, pengguna langsung diminta membuat password baru. Sebelum menggantinya, ia belum bisa memakai fitur lain, termasuk Connect. Akunnya ditandai *Wajib ganti password* di daftar Users.
- Halaman login punya tautan **Lupa password?**. Pengguna mengisi username dan pesan opsional (mis. kelas atau cara menghubunginya). Permintaannya muncul di bagian atas tab **Users**, lengkap dengan tombol **Reset password** dan **Abaikan**, dan jumlahnya tampil sebagai angka merah di tab itu. Sysadmin hanya melihat permintaan dari akun mahasiswa.
- Jawaban di halaman login selalu sama, terdaftar atau tidak, jadi form ini tidak bisa dipakai untuk menebak username. Satu IP dibatasi 10 permintaan per jam. Siapa pun bisa mengirim permintaan untuk username apa pun, jadi pastikan yang meminta memang pemilik akunnya sebelum memberikan password sementara.
- Mengganti password sendiri lewat Profil juga mengakhiri sesi di perangkat lain. Hal yang sama terjadi saat superadmin mengganti password atau peran akun lewat **Edit**.
- Kalau satu-satunya superadmin lupa password, reset dari server:
  ```bash
  cd backend && sudo docker compose exec backend python scripts/reset_password.py admin
  ```
  Perintah ini mencetak password sementara yang wajib diganti saat login.

### 5.5 VM Dipakai Bersama, Akun OS, dan Reset Password
Satu VM bisa dipakai banyak mahasiswa, masing-masing dengan akun OS sendiri. Cara mereka masuk saat **Connect** diatur di tab **Groups** → VM milik grup → *Mode Koneksi*:
- **Login Mandiri**: mahasiswa mengetik username dan password akun OS-nya sendiri.
- **Kredensial Grup**: semua anggota masuk dengan satu akun OS yang sama.

Akun OS juga bisa dipasangkan langsung ke seorang mahasiswa di Detail VM → tab *Penugasan*. Connect-nya langsung masuk tanpa mengetik.

Akun OS dikelola di Detail VM → tab **Akun OS** (superadmin dan sysadmin):
- **Tambah** dengan centang *Buat juga user ini di dalam VM*: user Linux baru dibuat di dalam VM dengan folder home sendiri, tanpa hak sudo. Password yang dikosongkan dibuatkan acak dan ditampilkan sekali. Tanpa centang, akun hanya dicatat di dashboard dan user-nya harus sudah ada di dalam VM.
- **Reset password**, untuk pengguna yang lupa password. Password lama tidak diperlukan.
- **Hapus**: hanya dari dashboard, atau sekalian dari dalam VM (folder home dan isinya ikut terhapus). User yang masih dipakai untuk Login Connect atau kredensial grup tidak dihapus dari VM.

User Login Connect sebuah VM bisa direset di Detail VM → tab Info → **Reset password**. Form Kredensial Grup juga punya centang *Buat atau perbarui akun ini di dalam VM*.

Aksi di dalam VM memakai QEMU Guest Agent, jadi tidak perlu SSH atau jaringan di dalam VM. Syaratnya VM menyala dan agent berjalan (Bagian 4). Token dari Bagian 2.3 sudah punya izin Guest Agent yang dibutuhkan.
- Di distro dengan SELinux aktif (mis. openSUSE Leap 16, Fedora, Rocky/RHEL), SELinux membatasi Guest Agent sehingga tidak bisa membuat user atau mengganti password. Dashboard lalu otomatis memakai SSH dengan Login Connect VM itu, yang harus punya sudo (user cloud-init bawaan sudah punya). Jalur yang sama dipakai kalau agent tidak merespons. Password dikirim lewat stdin `chpasswd`, tidak lewat argumen perintah.
- Saat ini hanya untuk VM Linux. Password VM Windows diganti lewat RDP atau console Proxmox.
- Hanya akun pengguna biasa (UID 1000 ke atas) yang bisa diubah. Root dan akun sistem tidak bisa.
- Setelah password berubah, semua salinan yang disimpan dashboard untuk user itu ikut diperbarui, jadi tombol Connect tetap jalan.
- Setiap aksi tercatat di Activity Log, tanpa password-nya.

### 5.6 Switch: Jaringan Terisolasi per Kelas
Di tab **Topology**, tombol **＋ Switch** (superadmin dan sysadmin) membuka pengelola switch, yaitu jaringan virtual dengan subnet sendiri di sebuah Proxmox. Berbeda dengan `vmbr0` (bridge bawaan Proxmox yang tersambung ke kartu jaringan fisik, jadi VM di sana langsung berada di LAN kampus), switch tidak tersambung ke kartu fisik; lalu lintasnya lewat host Proxmox. VM di sebuah switch:
- bisa saling terhubung, dan bisa di-Connect, dibuka lewat Open Web, serta diakses lewat SSH dari CCD;
- bisa ke internet lewat NAT host Proxmox (bisa dimatikan per switch);
- tidak bisa menjangkau switch lain, jaringan kampus (termasuk VM di `vmbr0`), tailnet, atau host Proxmox.

**Blok alamat.** Setiap switch berada di salah satu blok alamat Proxmox-nya. Satu Proxmox bisa punya beberapa blok, jadi Anda cukup mencadangkan rentang yang memang dipakai, mis. `192.168.111.0/24` lalu `192.168.112.0/24`, atau satu blok besar seperti `10.111.0.0/16`. Host Proxmox mengiklankan setiap blok lewat Tailscale. Switch baru di blok yang sudah ada langsung terjangkau dari VPS; kosongkan subnet-nya supaya /24 kosong berikutnya terisi otomatis.

Blok baru bisa ditambah dua cara: lewat **Tambah blok** di pengelola switch, atau dengan mengisi subnet switch di luar blok yang ada lalu mencentang **Tambahkan sebagai blok alamat baru**. Blok yang memuat blok lama (mis. `192.168.96.0/19` untuk `192.168.111.0/24`) menggantikannya. Blok hanya bisa dihapus kalau tidak ada switch di dalamnya. Setiap kali blok ditambah atau dihapus, jalankan ulang skrip di host (lihat di bawah) dan, untuk blok baru, setujui route-nya di Tailscale. Sebelum itu switch di blok baru belum terjangkau dari CCD, tapi sudah terisolasi.

Pilih rentang yang tidak dipakai jaringan kampus; tanyakan ke pengelola jaringan kalau ragu. Dashboard hanya bisa menolak blok yang bertabrakan dengan jaringan yang ia ketahui: LAN Proxmox, blok Proxmox lain, rentang Docker (172.17.x sampai 172.31.x), dan jaringan VPS. Jaringan VPS dicatat `setup.sh` ke `HOST_NETWORKS` di `backend/.env` setiap kali dijalankan, karena backend di dalam container tidak bisa melihatnya sendiri.

**Menyiapkan host Proxmox: sekali per host, lalu setiap kali blok berubah.** Pengelola switch menampilkan perintahnya, sudah terisi semua blok dan Token ID:
```bash
curl -fsSL https://<domain-dashboard>/api/v1/networks/setup-script -o ccd-net-setup.sh
bash ccd-net-setup.sh --pool 192.168.111.0/24,192.168.112.0/24 --token 'root@pam!ccd-dashboard'
```
Skrip ini aman dijalankan ulang dan melakukan lima hal:
1. Membuat SDN zone tipe Simple bernama `ccd`.
2. Memberi token CCD izin mengelola VNet di zone itu dan menerapkan konfigurasi SDN. Token tidak mendapat izin atas zone lain.
3. Memasang aturan isolasi nftables (`nft list table inet ccd_net`) lewat layanan `ccd-net.service`, supaya tetap aktif setelah reboot. Aturannya mengenali switch dari nama bridge-nya (`ccd…`), jadi switch di blok yang baru ditambahkan langsung terisolasi.
4. Menjalankan penerus DNS `ccd-dns.service` (dnsmasq) yang hanya mendengarkan di bridge switch, tanpa DHCP.
5. Mengatur route Tailscale: blok yang belum diiklankan ditambahkan, dan blok yang dulu dipasang skrip ini tapi sudah dihapus dari dashboard dicabut. Route lain (mis. LAN `vmbr0`) tetap ada. Blok lama yang tercakup blok baru yang lebih besar tidak dicabut supaya switch-nya tidak putus sebelum route baru disetujui. Skrip bertanya dulu sebelum mengubah route.

Setelah itu setujui route blok baru di admin console Tailscale (Machines → host Proxmox → Edit route settings). Supaya langkah ini otomatis, beri host Proxmox sebuah tag dan tambahkan `autoApprovers` di policy Tailscale. Route di dalam rentang itu lalu disetujui sendiri:
```json
"tagOwners":     { "tag:proxmox": ["autogroup:admin"] },
"autoApprovers": { "routes": { "192.168.0.0/16": ["tag:proxmox"] } }
```
Pasang tag-nya dengan `tailscale up --advertise-tags=tag:proxmox` di host (atau dari admin console), dan pastikan aturan akses di policy tetap mengizinkan VPS menjangkau perangkat bertag itu. Syaratnya `ifupdown2` terpasang dan `/etc/network/interfaces` memuat baris `source /etc/network/interfaces.d/*` (bawaan Proxmox 8 ke atas). Untuk melepas aturan isolasi dan penerus DNS: `bash ccd-net-setup.sh --remove`.

**Membuat VM di switch.** Servers → Create VM → *Sambungkan ke* → pilih switch. IP dibagikan otomatis atau diisi sendiri, dan gateway sekaligus DNS-nya adalah host Proxmox (`x.x.x.1`). Host meneruskan DNS itu ke DNS yang dipakainya sendiri lewat penerus `ccd-dns.service`, karena jaringan kampus sering memblokir DNS publik dan DNS kampus berada di jaringan privat yang ditutup untuk switch. Switch tidak punya DHCP.

**Batasan.** Switch hanya berlaku di satu Proxmox. Switch yang masih dipakai VM tidak bisa dihapus. Bagian Kesiapan di pengelola switch menunjukkan zone, izin token, dan apakah CCD bisa menjangkau setiap switch. Kalau belum terjangkau, biasanya skrip belum dijalankan ulang dengan blok itu atau route-nya belum disetujui di Tailscale. Topology menampilkan switch (oranye) dengan nama dan subnetnya, termasuk yang belum punya VM, dan setiap kartu jaringan VM sebagai garis ke switch atau bridge-nya; klik switch lalu **Kelola switch** untuk mengubahnya.

## 6. Connect

### Sebagai mahasiswa (VM sudah di-assign)
1. Login ke dashboard dengan akun mahasiswa.
2. Tab **Servers** — hanya VM yang di-assign yang tampil, dengan tombol aksi (Start/Stop/Snapshot/**Connect**). Nama host Proxmox dan panel Host Performance tidak ditampilkan ke mahasiswa, dan tab **Topology** menampilkan username mereka sebagai induk VM.
3. Klik **Connect** → sesi SSH/RDP terbuka **langsung di tab browser** (Apache Guacamole) — tidak perlu install client SSH/RDP apa pun.
4. Ada masalah dengan VM? Klik nama VM untuk membuka Detail VM, lalu **Laporkan masalah**. Tiket otomatis berisi VM tersebut beserta kondisinya saat dilaporkan (status, CPU, RAM), dan balasan admin bisa dipantau di tab **Tickets**. Tiket yang dibuat dari tab Tickets juga bisa dikaitkan ke salah satu VM milik mahasiswa.

Setiap VM punya **CCDID**, mis. `CCD-0007`, yaitu nomor yang unik di seluruh dashboard. Mahasiswa melihat CCDID, bukan VMID, karena VMID hanya unik di dalam satu Proxmox: dua Proxmox bisa sama-sama punya VM 101. Admin melihat VMID dan CCDID di daftar VM, dan di tiket juga host-nya. CCDID tidak dipakai ulang. VM yang dihapus lalu dibuat lagi dengan VMID sama mendapat CCDID baru.

### Connect dari smartphone atau tablet
Saat Connect pertama kali dari perangkat sentuh, dashboard mengaktifkan input "Text input" di Guacamole: kolom teks muncul di bawah layar, dan mengetuknya membuka keyboard HP. Untuk mengganti cara input, geser jari dari tepi kiri layar ke kanan untuk membuka menu Guacamole, lalu pilih di bagian **Input method**:
- **Text input**: memakai keyboard HP.
- **On-screen keyboard**: keyboard bawaan Guacamole dengan tombol Ctrl, Alt, Esc, Tab, dan panah.
- **None**: hanya keyboard fisik (mis. keyboard Bluetooth).

Pilihan ini tersimpan di browser perangkat itu. Di menu yang sama, **Mouse emulation mode** "Relative" membuat jari bekerja seperti touchpad, biasanya lebih presisi untuk RDP.

Keyboard HP, terutama aplikasi keyboard pihak ketiga, bisa menyimpan kata yang diketik untuk prediksi. Untuk mengetik password di sesi remote (mis. `sudo`), pakai On-screen keyboard Guacamole.

### Kalau Connect gagal / IP belum kedeteksi
Biasanya karena QEMU Guest Agent di dalam guest belum jalan (baru saja boot, atau lupa install). Admin bisa:
- Tunggu ~1 menit lalu refresh (agent butuh waktu untuk mulai melapor setelah boot).
- Atau isi IP manual: buka detail VM → **Atur kredensial & IP** → isi IP yang benar. Connect langsung memakai IP manual ini kalau ada, tidak perlu tunggu guest agent.

### Open Web (melihat web di VM)
Tab **Open Web**: ketik alamat web lalu klik **Buka**, halaman tampil di dalam dashboard.
- Alamat publik (`https://...`) dibuka langsung oleh browser.
- Alamat privat (`10.x`, `172.16-31.x`, `192.168.x`, `100.64-127.x` untuk Tailscale) dibuka lewat proxy dashboard, karena browser memblokir halaman publik yang mengakses jaringan lokal. Server dashboard harus bisa menjangkau IP itu. Hanya `http://` yang didukung untuk IP privat.
- Admin boleh ke IP privat mana pun. Mahasiswa hanya ke IP VM yang di-assign ke mereka, jadi IP VM harus sudah terisi di **Atur kredensial & IP**.
- Link proxy berlaku 1 jam dan bisa dipakai siapa pun yang memegang URL-nya selama itu, jadi jangan dibagikan. Admin bisa mencabutnya (lihat di bawah).
- Web yang memakai path absolut (`/static/...`) atau sangat bergantung pada cookie bisa tampil tidak lengkap. Web yang melarang di-embed (`X-Frame-Options`) tampil kosong untuk alamat publik.

### Sebagai admin — memantau sesi
- Tab **Audit & Remote → Activity Log**: riwayat semua aksi penting, termasuk login, VM, resize, Open Web, SSH, perubahan akun (buat, ubah peran/status/email/masa berlaku, hapus), perubahan grup dan anggotanya, serta tambah/ubah/hapus instance Proxmox. Bisa disaring per kata, per akun, per jenis aksi, per tingkat, dan per tanggal. **Ekspor CSV** mengunduh hasil saringan itu (maks. 50.000 baris, waktu dalam WIB). Daftar dimuat ulang otomatis tiap 15 detik hanya di halaman 1, supaya baris tidak bergeser saat membaca halaman berikutnya.
- Tab **Audit & Remote → Login Gagal**: rekap login gagal 1, 7, atau 30 hari terakhir, per akun dan per IP. Akun yang sedang terkunci (5 kali gagal dalam 5 menit) ditandai. Satu IP yang mencoba banyak akun berbeda patut dicurigai.
- Tab **Audit & Remote → Sesi Remote**: siapa yang sedang connect ke VM mana, protokolnya, dan dari IP mana. Riwayatnya dibaca langsung dari database Guacamole, jadi tidak terbatas jumlah, bisa dicari per user atau VM, dan bisa diekspor. Sesi yang dibuka sebelum versi ini tidak menampilkan IP, karena yang tercatat waktu itu adalah IP container backend.
- Tab **Audit & Remote → Sesi Web**: link Open Web yang aktif dan riwayatnya (user, IP target, IP pengakses, jumlah request). IP pengakses kuning berarti link dipakai dari lebih dari satu IP. *Cabut Link* mematikan link itu seketika. Jumlah request hanya perkiraan.
- Tab **Audit & Remote → Sesi SSH** (muncul kalau bastion aktif, Bagian 8): siapa yang SSH lewat bastion, dari IP mana, ke VM mana, durasi, dan jumlah data.
- **Menghapus tiket Helpdesk dan Infra Request** (khusus superadmin): tombol *Hapus* di jendela detailnya, dengan konfirmasi. Pesan dan lampirannya ikut terhapus dan tidak bisa dikembalikan. Yang tersisa di Activity Log hanya ringkasan, dicatat sebagai `TICKET_DELETE` atau `INFRA_REQUEST_DELETE`: nomor, judul, pemilik, kategori atau tipe dan spek, status, tanggal, jumlah pesan dan lampiran, serta siapa yang menghapus. Isi percakapan, catatan admin, dan kredensial VPN tidak ikut disalin. Pembuatan dan perubahan status Infra Request juga tercatat (`INFRA_REQUEST_CREATE`, `INFRA_REQUEST_STATUS`), sama seperti tiket.
- Klik nama pengguna di tab mana pun (atau tombol **Aktivitas** di halaman Users) untuk melihat semua catatannya di satu jendela: Activity Log, sesi Remote, link Open Web, dan sesi SSH.

**Memutus sesi.** *Putuskan Sesi* (Remote, SSH) dan *Cabut Link* (Web) menanyakan apa yang dilakukan sesudahnya:
- **Putuskan saja**: pengguna bisa langsung menyambung lagi.
- **Putuskan dan cabut akses ke VM ini** (Remote): penugasan VM itu dihapus dari pengguna. Kalau aksesnya berasal dari grup, dashboard menolak dan menyebut grupnya; keluarkan pengguna dari grup itu di halaman Groups.
- **Putuskan dan nonaktifkan akun**: akun dinonaktifkan dan semua sesi Remote, Web, dan SSH-nya ikut diputus. Aktifkan lagi lewat halaman Users. Sysadmin hanya bisa melakukannya untuk akun mahasiswa.

Pilihan yang tidak bisa dijalankan ditolak sebelum sesinya diputus. Semua tindakan ini tercatat di Activity Log beserta nama akun dan VM-nya.
- Tab **Status**: kesehatan layanan, dan resource VPS tempat dashboard berjalan (CPU, RAM, disk, load, jaringan). Angka live diperbarui tiap 5 detik; riwayatnya disimpan per menit selama 30 hari (1 jam, 24 jam, 7 hari, 30 hari). Angka jaringan VPS dibaca dari `/proc/1/net/dev` host yang di-mount read-only ke backend.

---

## 7. (Opsional) Akses dari Luar Jaringan — Tailscale

Kalau VPS dashboard perlu diakses dari luar jaringan lokalnya (mis. mahasiswa dari rumah), pasang **Tailscale langsung di level OS VPS**. Dashboard tidak mengelola Tailscale sendiri (tidak ada panel atau kunci API Tailscale); aturan akses tailnet diatur di admin console Tailscale.

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --hostname=<nama-vps-anda>
```
Buka link login yang muncul, authorize di browser.

**Pilih salah satu mode akses:**
- **Tailnet-only** (device pengakses wajib login Tailscale juga) — pakai `tailscale serve`:
  ```bash
  sudo tailscale serve --bg --https=443 http://localhost:80
  ```
- **Publik ke internet** (siapa saja bisa akses, tanpa install Tailscale) — pakai `tailscale funnel`:
  ```bash
  sudo tailscale funnel --bg --https=443 http://localhost:80
  ```

Kedua mode otomatis dapat sertifikat HTTPS valid dan URL bersih (`https://<hostname>.<tailnet-domain>.ts.net/`), tanpa perlu urus reverse proxy/TLS sendiri. Konfigurasi ini tersimpan permanen, otomatis jalan lagi setelah reboot.

Dengan mode Funnel, halaman login bisa dicoba siapa saja di internet. Pastikan password admin bukan `admin123` (Bagian 1.5). Pembatasan login sudah aktif: 5 kali gagal, akun terkunci 5 menit.

### 7.1 VPS privat dengan domain

Dashboard tidak butuh IP publik: semua komponennya berjalan di Docker di satu mesin, dan nginx di dalamnya hanya melayani HTTP di port 80. Domain berguna untuk alamat yang mudah diingat dan HTTPS. Pilih jalur sesuai siapa yang mengakses:

| Pengguna | Jalur | HTTPS dari |
|---|---|---|
| Hanya di LAN atau VPN | **A.** Domain menunjuk ke IP privat | Caddy dengan sertifikat DNS-01 |
| Dari internet, VPS tanpa port terbuka | **B.** Cloudflare Tunnel | Cloudflare |
| Perangkat yang login Tailscale | **C.** Domain menunjuk ke IP Tailscale | Caddy dengan sertifikat DNS-01 |

Untuk ketiganya, isi `ALLOWED_ORIGINS` di `backend/.env` dengan alamat yang dipakai pengguna (mis. `https://dashboard.<domain>`), lalu `cd backend && sudo docker compose up -d backend`. Kalau salah, login gagal karena CORS.

**A dan C: domain ke IP privat, HTTPS dengan Caddy.** Buat record DNS `A` untuk `dashboard.<domain>` ke IP privat VPS (jalur A, mis. `10.20.0.5`) atau ke IP Tailscale VPS (jalur C, `100.x.y.z`, lihat `tailscale ip -4`). Record yang menunjuk ke IP privat tidak bisa dibuka dari internet, tetapi namanya tetap terlihat publik; pakai DNS internal kalau nama itu tidak boleh terlihat. Jangan memakai `CNAME` ke alamat `.ts.net` untuk jalur C: sertifikat Tailscale hanya berlaku untuk nama `.ts.net`, jadi browser akan memperingatkan sertifikat tidak cocok.

Let's Encrypt biasanya memeriksa lewat port 80 atau 443 dari internet, yang tidak mungkin di VPS privat. Pakai tantangan **DNS-01**: Caddy membuat record TXT lewat API penyedia DNS, tanpa port terbuka. Contoh untuk DNS di Cloudflare (penyedia lain memakai modul `caddy-dns/<nama>` yang sesuai). Buat folder terpisah, mis. `/opt/ccd-caddy`:

```dockerfile
# Dockerfile
FROM caddy:builder AS builder
RUN xcaddy build --with github.com/caddy-dns/cloudflare
FROM caddy:2
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
```
```yaml
# docker-compose.yml
services:
  caddy:
    build: .
    restart: unless-stopped
    network_mode: host
    environment:
      - CF_API_TOKEN=${CF_API_TOKEN}
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
volumes:
  caddy_data:
```
```
# Caddyfile
{
    # Port 80 sudah dipakai container frontend dashboard; tanpa ini Caddy gagal start.
    auto_https disable_redirects
}
dashboard.<domain> {
    tls {
        dns cloudflare {env.CF_API_TOKEN}
    }
    reverse_proxy 127.0.0.1:80 {
        # nginx dashboard hanya mempercayai header ini dari proxy yang didaftarkan (lihat di bawah).
        # Caddy menimpa nilai kiriman klien dengan alamat sebenarnya.
        header_up CF-Connecting-IP {remote_host}
    }
}
```
Isi `CF_API_TOKEN` di berkas `.env` di folder yang sama (token Cloudflare dengan izin *Zone → DNS → Edit* hanya untuk zona itu), lalu `sudo docker compose up -d --build`. WebSocket (Connect, terminal, metrik) diteruskan Caddy tanpa pengaturan tambahan. Kalau VPS juga punya IP publik dan HTTP biasa di port 80 tidak boleh terbuka, batasi dengan firewall.

**B: Cloudflare Tunnel.** Pasang `cloudflared` di VPS, buat tunnel di dashboard Cloudflare Zero Trust (*Networks → Tunnels*), lalu tambahkan *Public Hostname* `dashboard.<domain>` dengan layanan `http://localhost:80`. VPS cukup bisa keluar ke internet; tidak ada port yang perlu dibuka. Cloudflare yang mengurus HTTPS. Header `CF-Connecting-IP` diteruskan Cloudflare, jadi hanya alamat tempat `cloudflared` menyambung ke nginx yang perlu didaftarkan (lihat di bawah). **Bastion SSH tidak bisa lewat tunnel ini**; lihat catatan SSH di bawah.

**IP asli pengguna di Activity Log.** Secara bawaan nginx dashboard hanya mempercayai header IP dari alamat Cloudflare. Di belakang Caddy atau `cloudflared` di VPS yang sama, yang dilihat nginx adalah alamat proxy itu, sehingga Activity Log, Sesi Web, dan Sesi Remote mencatat IP proxy, bukan IP pengguna. Daftarkan alamat proxy sebagai tepercaya:

1. Lihat alamat yang dipakai proxy untuk menyambung: buka dashboard sekali, lalu `sudo docker logs --tail 5 ccd-frontend`. Kata pertama tiap baris adalah alamat itu. Untuk proxy di host yang sama biasanya gateway jaringan Docker, mis. `172.18.0.1`.
2. Di `frontend/nginx.conf`, tambahkan satu baris tepat di bawah daftar `set_real_ip_from` Cloudflare: `set_real_ip_from 172.18.0.1;` (ganti dengan alamat dari langkah 1; tulis alamat tepatnya, jangan rentang lebar).
3. `cd backend && sudo docker compose up -d --build frontend`.
4. Periksa: coba login dengan password salah dari perangkat Anda, lalu lihat IP di **Audit & Remote → Activity Log**. Harus IP perangkat Anda, bukan IP proxy.

Header ini tidak bisa dipalsukan oleh pengguna: nginx mengabaikannya dari sumber yang tidak terdaftar, dan Caddy menimpa nilai kiriman klien. Perubahan `nginx.conf` ikut berkas yang dilacak git; simpan sebagai commit lokal agar tidak bentrok saat `git pull`.

**SSH (bastion) dengan domain.** Bastion memakai port 2222 dan TCP biasa, jadi tidak lewat Caddy atau Cloudflare Tunnel. Buat record `ssh.<domain>` yang **DNS only** (bukan di-proxy) ke alamat yang bisa dijangkau pengguna: IP privat untuk pengguna di LAN atau VPN, atau IP Tailscale untuk pengguna Tailscale. Isi `ssh.<domain>` di **Sistem → Alamat SSH untuk pengguna**. Dari internet, SSH ke VPS privat hanya bisa kalau port 2222 diteruskan (port forward) atau pengguna memakai VPN atau Tailscale.

**Open Web dan Remote.** Server dashboard harus bisa menjangkau IP VM yang dibuka. Di VPS yang satu jaringan dengan VM ini otomatis terpenuhi; kalau VM berada di switch Bagian 5.6, ikuti langkah Tailscale di bagian itu.

**Domain berganti atau dihapus.** Tidak ada domain yang tertanam di kode. Cukup ubah `ALLOWED_ORIGINS`, `Caddyfile` atau *Public Hostname* tunnel, dan alamat SSH di tab Sistem.

---

## 8. (Opsional) SSH dari Terminal Sendiri — Bastion

Selain lewat browser, pengguna bisa SSH ke VM-nya dari terminal sendiri, `scp`/`sftp`, atau VS Code Remote-SSH, lewat bastion di VPS:

```
Laptop ──SSH (key)──▶ VPS :2222 (bastion) ──Tailscale──▶ VM :22
```

Bastion tidak menyimpan key dan tidak memberi shell. Setiap login, bastion bertanya ke dashboard: key ini milik siapa, dan boleh diteruskan ke mana. Mahasiswa hanya bisa ke VM yang di-assign kepadanya (langsung atau lewat group), admin ke semua VM yang terdaftar. Menonaktifkan akun, mencabut assignment, atau menghapus key langsung berlaku di login berikutnya.

### 8.1 Mengaktifkan
Fitur ini mati secara bawaan karena membuka satu port baru ke internet. Di `backend/.env`:
```bash
COMPOSE_PROFILES=ssh
BASTION_PUBLIC_PORT=2222        # port yang dibuka di VPS
BASTION_PUBLIC_HOST=            # kosong = alamat yang dipakai membuka dashboard; bisa juga diatur di tab Sistem
```
`BASTION_TOKEN` diisi otomatis oleh `./setup.sh`, juga untuk `.env` lama. Lalu:
```bash
./setup.sh
# atau manual: cd backend && sudo docker compose build bastion && sudo docker compose up -d
cd backend && sudo docker compose logs bastion | grep SHA256    # fingerprint host key
```
Pastikan port 2222 tidak diblokir firewall VPS. Untuk mematikan lagi: kosongkan `COMPOSE_PROFILES`, lalu `sudo docker compose stop bastion`. Fingerprint juga tampil di tombol **SSH** pada kartu VM.

**Memakai domain.** Buat record DNS khusus untuk bastion, mis. `ssh.<domain>` → IP VPS, lalu isi **Alamat SSH untuk pengguna** di tab **Sistem** dengan `ssh.<domain>`. Cara lama juga masih bisa: isi `BASTION_PUBLIC_HOST=ssh.<domain>` di `.env` lalu jalankan `cd backend && sudo docker compose up -d backend`. Nilai di tab Sistem lebih diutamakan daripada `.env`. Kalau keduanya kosong, dipakai alamat yang sedang dibuka pengguna di browser. Saat domain berganti atau tidak memakai domain lagi, cukup ganti atau kosongkan alamat itu di tab Sistem. Kalau domain dikelola Cloudflare, record bastion harus **DNS only** (awan abu-abu). Proxy Cloudflare hanya meneruskan HTTP/HTTPS, jadi SSH ke port 2222 lewat record yang di-proxy akan gagal. Record untuk dashboard boleh di-proxy, tetapi dashboard hanya melayani HTTP di port 80. Dengan mode SSL *Flexible*, jalur dari Cloudflare ke server tidak terenkripsi, jadi pakai hanya kalau risiko itu bisa diterima. Tambahkan juga alamat dashboard yang baru ke `ALLOWED_ORIGINS`. Di belakang proxy Cloudflare, nginx membaca IP asli pengunjung dari header `CF-Connecting-IP`, hanya untuk permintaan yang datang dari alamat Cloudflare, jadi Activity Log, Web Sessions, dan Remote Sessions tetap mencatat IP pengguna. Daftar alamat Cloudflare ada di `frontend/nginx.conf`; perbarui dari https://www.cloudflare.com/ips/ kalau berubah.

Setelah alamat bastion pindah dari IP ke domain, pengguna yang pernah terhubung akan ditanya konfirmasi host key sekali lagi. Fingerprint-nya tetap sama, karena host key disimpan di volume `bastion_keys`.

### 8.2 Syarat VM
- IP VM sudah diisi di **Atur kredensial & IP**, dan protokolnya SSH (VM Linux).
- VM bisa dijangkau dari VPS (Bagian 1.6). Lewat subnet route Tailscale, koneksi biasanya terlihat datang dari IP LAN Proxmox, jadi firewall di VM harus mengizinkan SSH dari alamat itu.
- Pengguna tetap login ke akun OS di dalam VM. Bastion hanya mengantar sampai VM.

### 8.3 Cara pakai (untuk mahasiswa)
Panduan lengkap per OS (Linux, macOS, Windows), termasuk `scp`, VS Code, dan penanganan error, ada di [`PANDUAN_SSH.md`](PANDUAN_SSH.md). Ringkasnya:

1. Buat key di laptop (sekali saja): `ssh-keygen -t ed25519`.
2. Di dashboard, buka **Profil**, bagian **SSH Key**, lalu tempel isi `~/.ssh/id_ed25519.pub`. Jangan pernah menempel private key.
3. Di halaman **Servers**, klik tombol **SSH** pada VM. Dashboard menampilkan perintah dan isi `~/.ssh/config` yang siap disalin, contohnya:
   ```bash
   ssh -J tunnel@<alamat-bastion>:2222 akun-os@<ip-vm>
   ```
4. Saat pertama kali terhubung, cocokkan fingerprint host key bastion dengan yang tampil di tombol SSH.

Kalau muncul `administratively prohibited`, VM itu tidak termasuk VM Anda. Kalau ditolak di bastion (`Permission denied (publickey)`), periksa apakah key sudah didaftarkan dan akun Anda aktif.

### 8.4 Audit
Bastion meneruskan log `sshd` ke dashboard, jadi setiap koneksi tercatat di **Audit & Remote → Sesi SSH**: user, key, IP asal, VM tujuan, waktu mulai dan selesai, serta jumlah data. Di **Activity Log** muncul:

| Kejadian | Artinya |
|---|---|
| `SSH_LOGIN` | Key diterima bastion |
| `SSH_LOGOUT` | Sesi selesai, dengan durasi dan VM tujuan |
| `SSH_DENIED` | Mencoba membuka VM yang bukan haknya, atau key milik akun yang sedang tidak berhak (nonaktif, belum diverifikasi, tidak punya VM) |

Isi sesi tidak direkam. Bastion memang tidak bisa melihatnya, karena koneksi terenkripsi langsung antara laptop dan VM. Percobaan dengan key yang tidak terdaftar di dashboard (biasanya pemindaian dari internet) hanya ada di `sudo docker compose logs bastion`.

Menghapus key atau menonaktifkan akun dari halaman Users hanya menolak login berikutnya. Untuk memutus sesi yang sedang berjalan, pakai **Audit & Remote → Sesi SSH → Putuskan Sesi** (bisa sekaligus menonaktifkan akunnya). Perintahnya dijalankan skrip `ccd-kill` di container bastion; kalau bastion belum diperbarui ke versi ini, dashboard menampilkan pesan agar container bastion dibangun ulang (`sudo docker compose --profile ssh up -d --build bastion`). Pemutusan tercatat sebagai `SSH_KILL`. `sudo docker compose restart bastion` tetap bisa dipakai untuk memutus semua sesi SSH sekaligus.

---

## 9. Backup Database

`setup.sh` memasang cron harian jam 02:00 yang membackup kedua database (data aplikasi `ccddb` dan Guacamole `guacamoledb`) ke `/var/backups/campus-cloud-dashboard`, dengan retensi 14 hari. Log-nya di `/var/log/ccd-backup.log`. Backup manual:
```bash
sudo ./backend/scripts/backup-db.sh
```

Restore menimpa database tujuan sepenuhnya. Hentikan dulu container yang memakainya:
```bash
cd backend
sudo docker compose stop backend            # untuk ccddb; untuk guacamoledb hentikan guacamole
sudo ./scripts/restore-db.sh ccddb /var/backups/campus-cloud-dashboard/ccddb-<tanggal>.sql.gz
sudo docker compose start backend
```

Simpan juga salinan `backend/.env` di tempat yang aman. Kredensial VM dan token Proxmox di database dienkripsi dengan `JWT_SECRET`, jadi backup database tidak bisa dipakai tanpa `.env` yang sama.
