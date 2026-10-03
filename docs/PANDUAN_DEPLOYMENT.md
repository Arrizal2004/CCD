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
5. Mencetak ringkasan: URL dashboard, password admin dashboard, dan password admin Guacamole. Keduanya hanya dicetak sekali, jadi simpan.

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
| `TAILSCALE_API_KEY`, `TAILSCALE_TAILNET` | Hanya kalau mau integrasi API Tailscale dari dashboard. **Rekomendasi kami: lewati ini** — atur Tailscale langsung di level OS VPS (`tailscale up`), bukan lewat dashboard. Lihat catatan di Bagian 7. |
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
3. **Manage Instances** → **+ Add Instance**.
4. Isi:
   - **Label**: nama bebas, mis. `lab` (dipakai internal sebagai identifier, tidak tampil ke mahasiswa).
   - **Host**: `<ip-proxmox>:8006`.
   - **Token ID**: dari langkah 2.2, mis. `root@pam!ccd-dashboard`.
   - **Token Secret**: dari langkah 2.2.
   - **Verify SSL**: matikan kalau Proxmox pakai sertifikat self-signed (default Proxmox baru install).
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
- **Per-VM langsung**: buka detail VM → tab *Assignments* → tambahkan user.
- **Lewat Group** (direkomendasikan untuk satu kelas): tab **Groups** → buat group → tambahkan anggota (mahasiswa) → tambahkan VM ke group. Semua anggota otomatis dapat akses ke semua VM di group itu.

### 5.2 Ubah RAM / CPU / Storage (Resize)
Hanya superadmin dan sysadmin. Tombol **Resize** di daftar VM aktif kalau VM sudah **mati** (status `stopped`):
- RAM dan CPU bisa dinaikkan atau diturunkan, dalam batas kapasitas node.
- Storage **hanya bisa diperbesar**, tidak bisa diperkecil.
- Setelah storage diperbesar dan VM dinyalakan, partisi dan filesystem di dalam VM harus diperluas manual (lihat `docs/PANDUAN_TEMPLATE_VM.md`, Bagian F). Proxmox hanya memperbesar disknya.
- Setiap perubahan tercatat di Activity Log.

---

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
- Tab **Audit & Remote → Remote Sessions**: lihat siapa yang sedang connect ke VM mana, bisa paksa putus sesi (*Kill Session*).
- Tab **Audit & Remote → Web Sessions**: link Open Web yang aktif dan riwayatnya (user, IP target, IP pengakses, jumlah request). IP pengakses kuning berarti link dipakai dari lebih dari satu IP. *Kill Link* mematikan link itu seketika. Jumlah request hanya perkiraan.
- Tab **Audit & Remote → SSH Sessions** (muncul kalau bastion aktif, Bagian 8): siapa yang SSH lewat bastion, dari IP mana, ke VM mana, durasi, dan jumlah data.
- Tab **Audit & Remote → Activity Log**: riwayat semua aksi penting (login, create/delete VM, resize, Open Web, SSH, dst).

---

## 7. (Opsional) Akses dari Luar Jaringan — Tailscale

Kalau VPS dashboard perlu diakses dari luar jaringan lokalnya (mis. mahasiswa dari rumah), pasang **Tailscale langsung di level OS VPS**, bukan lewat dashboard (panel Tailscale di dashboard sengaja disembunyikan — fungsinya cuma untuk kelola ACL tailnet lewat API, bukan untuk mendaftarkan mesin ke tailnet).

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
BASTION_PUBLIC_HOST=            # kosong = alamat yang dipakai membuka dashboard; isi domain kalau ada
```
`BASTION_TOKEN` diisi otomatis oleh `./setup.sh`, juga untuk `.env` lama. Lalu:
```bash
./setup.sh
# atau manual: cd backend && sudo docker compose build bastion && sudo docker compose up -d
cd backend && sudo docker compose logs bastion | grep SHA256    # fingerprint host key
```
Pastikan port 2222 tidak diblokir firewall VPS. Untuk mematikan lagi: kosongkan `COMPOSE_PROFILES`, lalu `sudo docker compose stop bastion`. Fingerprint juga tampil di tombol **SSH** pada kartu VM.

**Memakai domain.** Buat record DNS khusus untuk bastion, mis. `ssh.<domain>` → IP VPS, lalu isi `BASTION_PUBLIC_HOST=ssh.<domain>` dan jalankan `cd backend && sudo docker compose up -d backend`. Kalau domain dikelola Cloudflare, record bastion harus **DNS only** (awan abu-abu). Proxy Cloudflare hanya meneruskan HTTP/HTTPS, jadi SSH ke port 2222 lewat record yang di-proxy akan gagal. Record untuk dashboard boleh di-proxy, tetapi dashboard hanya melayani HTTP di port 80. Dengan mode SSL *Flexible*, jalur dari Cloudflare ke server tidak terenkripsi, jadi pakai hanya kalau risiko itu bisa diterima. Tambahkan juga alamat dashboard yang baru ke `ALLOWED_ORIGINS`.

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
Bastion meneruskan log `sshd` ke dashboard, jadi setiap koneksi tercatat di **Audit & Remote → SSH Sessions**: user, key, IP asal, VM tujuan, waktu mulai dan selesai, serta jumlah data. Di **Activity Log** muncul:

| Kejadian | Artinya |
|---|---|
| `SSH_LOGIN` | Key diterima bastion |
| `SSH_LOGOUT` | Sesi selesai, dengan durasi dan VM tujuan |
| `SSH_DENIED` | Mencoba membuka VM yang bukan haknya, atau key milik akun yang sedang tidak berhak (nonaktif, belum diverifikasi, tidak punya VM) |

Isi sesi tidak direkam. Bastion memang tidak bisa melihatnya, karena koneksi terenkripsi langsung antara laptop dan VM. Percobaan dengan key yang tidak terdaftar di dashboard (biasanya pemindaian dari internet) hanya ada di `sudo docker compose logs bastion`.

Sesi yang sedang berjalan tidak ikut terputus saat key dihapus atau akun dinonaktifkan. Yang ditolak adalah login berikutnya. Kalau sesi yang sedang berjalan harus diputus, `sudo docker compose restart bastion` memutus semua sesi SSH lewat bastion.

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
