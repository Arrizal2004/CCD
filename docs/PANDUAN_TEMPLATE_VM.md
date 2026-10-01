# Panduan Membuat Template VM — Ubuntu & openSUSE di Proxmox

Panduan ini untuk VM yang **sudah selesai diinstall dari ISO** (masih kondisi default/baru) dan mau dijadikan template yang bisa di-clone lewat CCD ("Create VM" → clone + cloud-init). Ada dua bagian besar:

1. **Konfigurasi di dalam guest** (beda per OS) — bagian ini yang paling sering terlewat dan bikin clone gagal connect.
2. **Langkah di Proxmox** (sama untuk semua OS) — CloudInit Drive, Guest Agent, Convert to Template.

---

## Kenapa perlu "digeneralisasi" dulu?

VM hasil install ISO itu unik: dia punya `machine-id`, SSH host key, dan (kadang) konfigurasi network yang di-hardcode sendiri oleh installer. Kalau langsung di-clone tanpa dibersihkan dulu:
- **Semua clone akan punya `machine-id` dan SSH host key yang SAMA** — bikin peringatan "REMOTE HOST IDENTIFICATION HAS CHANGED" di SSH client, dan bisa bikin service yang berbasis machine-id (systemd, dbus) berperilaku aneh kalau beberapa clone jalan bersamaan di jaringan yang sama.
- **cloud-init tidak akan jalan** kalau paketnya belum terpasang, atau datasource-nya tidak diarahkan ke tempat Proxmox naruh data cloud-init — akibatnya waktu di-clone dari CCD, user/password/IP yang diisi di form **tidak akan pernah masuk ke VM baru**.
- **QEMU Guest Agent tidak lapor IP** kalau paketnya belum terpasang — CCD tidak akan bisa auto-detect IP VM (harus isi manual terus).

Dua hal ini (cloud-init + guest agent) adalah yang paling sering bikin "kok VM baru dari template gak bisa di-Connect" — pastikan keduanya benar-benar jalan sebelum convert to template.

---

## Bagian A — Ubuntu (contoh: VM `Template-Ubuntu`)

### 1. Update sistem
```bash
sudo apt update && sudo apt upgrade -y
```

### 2. Pasang cloud-init & QEMU Guest Agent
```bash
sudo apt install -y cloud-init qemu-guest-agent
sudo systemctl enable --now qemu-guest-agent
```
Ubuntu Server (installer subiquity) biasanya sudah include `cloud-init`, tapi jalankan perintah di atas tetap aman (idempotent) untuk memastikan.

### 3. Arahkan cloud-init ke datasource Proxmox
Proxmox menyuntikkan data cloud-init lewat CD-ROM virtual (drive CloudInit yang nanti ditambahkan di Bagian C) — cloud-init perlu tahu harus cari di situ:
```bash
sudo mkdir -p /etc/cloud/cloud.cfg.d
sudo tee /etc/cloud/cloud.cfg.d/99-pve.cfg > /dev/null <<'EOF'
datasource_list: [ ConfigDrive, NoCloud ]
EOF
```
**Ini langkah yang paling sering terlewat** — tanpa ini, cloud-init kadang tidak menemukan data dari Proxmox sama sekali walau drive-nya sudah terpasang.

### 4. Cek netplan tidak bentrok dengan cloud-init
Installer Ubuntu biasanya bikin file netplan sendiri (network config manual):
```bash
ls /etc/netplan/
```
Kalau ada file selain yang akan dibuat cloud-init (biasanya nama seperti `00-installer-config.yaml`), hapus supaya tidak menimpa/bentrok dengan network config yang diisi CCD saat clone:
```bash
sudo rm -f /etc/netplan/00-installer-config.yaml
```

### 5. Generalisasi — hapus identitas unik
```bash
sudo cloud-init clean --logs
sudo truncate -s 0 /etc/machine-id
sudo rm -f /var/lib/dbus/machine-id
sudo ln -s /etc/machine-id /var/lib/dbus/machine-id
sudo rm -f /etc/ssh/ssh_host_*
```
SSH host key akan otomatis dibuat ulang (unik per-clone) oleh cloud-init saat boot pertama kali — tidak perlu generate manual.

### 6. Bersihkan jejak (opsional tapi disarankan)
```bash
sudo apt clean
history -c
cat /dev/null > ~/.bash_history
```

### 7. Matikan VM
```bash
sudo shutdown now
```
Tunggu sampai statusnya **Stopped** di Proxmox (bukan cuma OS-nya mati, pastikan qemu process-nya juga berhenti).

---

## Bagian B — openSUSE (contoh: VM `Template-Opensuse`, Leap 16.0)

### 1. Update sistem
```bash
sudo zypper refresh
sudo zypper update -y
```

### 2. Pasang cloud-init & QEMU Guest Agent
```bash
sudo zypper install -y cloud-init qemu-guest-agent
sudo systemctl enable --now qemu-guest-agent
sudo systemctl enable cloud-init-local cloud-init cloud-config cloud-final
```
Kalau `zypper install` bilang paket tidak ketemu, cek nama persisnya dulu: `zypper search cloud-init` / `zypper search qemu-guest-agent` — kadang beda repo aktif punya nama sedikit berbeda.

### 3. Arahkan cloud-init ke datasource Proxmox
Sama seperti Ubuntu:
```bash
sudo mkdir -p /etc/cloud/cloud.cfg.d
sudo tee /etc/cloud/cloud.cfg.d/99-pve.cfg > /dev/null <<'EOF'
datasource_list: [ ConfigDrive, NoCloud ]
EOF
```

### 4. Cek stack network yang aktif
openSUSE bisa pakai `wicked` (umum di instalasi server/minimal) atau `NetworkManager` (umum di instalasi desktop) — cek yang mana yang aktif:
```bash
systemctl is-active wicked NetworkManager 2>/dev/null
```
cloud-init akan otomatis pakai network renderer yang sesuai. Ini area yang **paling perlu dites langsung setelah clone pertama** (Bagian D) — kalau IP tidak masuk otomatis, ini kandidat penyebab pertama yang perlu dicek.

### 5. Cek firewall
openSUSE default mengaktifkan `firewalld`. Pastikan SSH tidak keblok:
```bash
sudo firewall-cmd --list-services
```
Kalau `ssh` tidak ada di daftar, tambahkan (atau nonaktifkan firewalld sepenuhnya kalau VM lab memang dimaksudkan terbuka dalam jaringan tepercaya):
```bash
sudo firewall-cmd --permanent --add-service=ssh
sudo firewall-cmd --reload
# atau, kalau mau dimatikan total:
# sudo systemctl disable --now firewalld
```

### 6. Generalisasi — hapus identitas unik
```bash
sudo cloud-init clean --logs
sudo truncate -s 0 /etc/machine-id
sudo rm -f /var/lib/dbus/machine-id
sudo ln -s /etc/machine-id /var/lib/dbus/machine-id
sudo rm -f /etc/ssh/ssh_host_*
```

### 7. Bersihkan jejak (opsional tapi disarankan)
```bash
sudo zypper clean --all
history -c
cat /dev/null > ~/.bash_history
```

### 8. Matikan VM
```bash
sudo shutdown now
```

---

## Bagian C — Langkah di Proxmox (sama untuk kedua OS)

Lakukan setelah VM berstatus **Stopped**.

### 1. Lepas ISO installer
Hardware → cari baris CD/DVD Drive (biasanya `ide2`) → Edit → pilih "Do not use any media" (atau lewat CLI):
```bash
qm set <vmid> --ide2 none,media=cdrom
```

### 2. Tambah CloudInit Drive
Hardware → Add → CloudInit Drive → pilih storage (mis. `local-lvm`) → Add.

CLI setara:
```bash
qm set <vmid> --ide0 local-lvm:cloudinit
```

### 3. Pastikan QEMU Guest Agent aktif (opsi Proxmox)
Options → QEMU Guest Agent → Enabled. (Kalau VM dibuat lewat `qm create ... --agent 1` seperti VM 102/103, ini biasanya sudah aktif — tinggal cek centangnya).

### 4. Pastikan boot order benar
Options → Boot Order → pastikan disk utama (`scsi0`) nomor satu, drive CD-ROM/CloudInit tidak perlu ikut boot:
```bash
qm set <vmid> --boot order=scsi0
```

### 5. Masukkan ke pool `campus-cloud`
Wajib supaya token API dashboard bisa melihat template ini (lihat `docs/PANDUAN_DEPLOYMENT.md` Bagian 2). Klik kanan VM → *Move to Pool* → pilih `campus-cloud`. CLI setara:
```bash
pvesh set /pools/campus-cloud -vms <vmid>
```

### 6. Convert to Template
Klik kanan VM → **Convert to Template**. (Tidak bisa dibalik — kalau ragu, boleh backup/snapshot dulu sebelum langkah ini.)

---

## Bagian D — Uji Template

Jangan langsung anggap selesai — clone dulu satu VM percobaan untuk pastikan semuanya benar-benar jalan:

1. Dari dashboard CCD: **Servers → + Create VM** → pilih template yang baru dibuat → isi username/password/IP → Create.
2. Setelah boot (~1 menit), cek:
   - VM table di CCD menampilkan **IP** (bukan kosong/manual) → berarti guest agent jalan.
   - Tombol **Connect** berhasil membuka sesi SSH, dan bisa login dengan username/password yang diisi tadi → berarti cloud-init benar-benar menerapkan data dari Proxmox.
   - `hostname` di dalam VM baru sesuai nama yang diisi (bukan nama VM template) → tanda cloud-init jalan penuh, bukan cuma sebagian.
3. Kalau IP tidak muncul atau Connect gagal, urutan cek:
   - `systemctl status qemu-guest-agent` di dalam guest — harus `active (running)`.
   - `sudo cloud-init status` — kalau `error` atau `disabled`, cek `sudo cat /var/log/cloud-init.log` untuk detail. Penyebab tersering: `datasource_list` di Bagian A.3/B.3 belum benar, atau CloudInit Drive belum ditambahkan di Proxmox (Bagian C.2).
4. Kalau sudah beres, **hapus VM percobaan** (bukan template-nya) lewat tombol Delete VM di CCD — supaya tidak jadi sampah.

Setelah template lolos uji ini sekali, semua clone berikutnya dari template yang sama akan berperilaku sama — tidak perlu diuji ulang tiap kali.

Ukuran disk template jadi batas bawah semua clone: disk VM bisa diperbesar dari CCD (Resize) tapi tidak bisa dikecilkan. Jadi buat template dengan disk secukupnya, lalu besarkan per VM kalau perlu.

---

## Bagian E — Backup Template & Pindahkan ke Proxmox Lain

Ini operasi native Proxmox (`vzdump`/`qmrestore`) — dilakukan langsung di Proxmox, **bukan lewat dashboard CCD** (fitur backup di dashboard sudah dicabut, lihat bagian "Produksi" di panduan presentasi). Berikut langkahnya, sudah diuji langsung dan **dikonfirmasi status "template" ikut terbawa utuh** saat direstore.

### 1. Backup di Proxmox sumber
```bash
vzdump <vmid> --storage local --mode stop --compress zstd
```
- `--storage local` → tempat file backup disimpan sementara (storage manapun yang punya content type "VZDump backup file", cek dengan `pvesm status`).
- `--mode stop` → aman dipakai untuk template (template memang selalu dalam kondisi berhenti).
- Hasil: file `.vma.zst` di `/var/lib/vz/dump/vzdump-qemu-<vmid>-<tanggal>.vma.zst`. Contoh nyata dari template Ubuntu 32GB: ukuran terkompresi **~2,2 GB** (disk mayoritas kosong/sparse, jadi jauh lebih kecil dari ukuran disk aslinya), proses ±1 menit.

> Catatan: kalau muncul warning `Sum of all thin volume sizes exceeds the size of thin pool` — itu cuma peringatan overcommit LVM-thin (normal, storage-nya memang dirancang begitu), bukan error, backup tetap jalan.

### 2. Pindahkan file ke Proxmox tujuan
Pilih salah satu, tergantung apakah dua Proxmox itu bisa saling menjangkau lewat jaringan:

**a. Langsung `scp` antar-Proxmox** (paling simpel, kalau dua host bisa saling ping):
```bash
scp /var/lib/vz/dump/vzdump-qemu-<vmid>-*.vma.zst root@<ip-proxmox-tujuan>:/var/lib/vz/dump/
```

**b. Lewat komputer perantara** (kalau dua Proxmox tidak satu jaringan) — download dulu ke laptop/PC, lalu upload ke Proxmox tujuan lewat GUI-nya: Datacenter → node tujuan → storage → **local** → tab *Backups* → **Upload**.

### 3. Restore di Proxmox tujuan
```bash
qmrestore /var/lib/vz/dump/vzdump-qemu-<vmid>-*.vma.zst <vmid-baru> --storage local-lvm
```
- `<vmid-baru>` — pilih VMID yang belum dipakai di Proxmox tujuan. Cek dengan `pvesh get /cluster/nextid`.
- `--storage local-lvm` — storage tujuan untuk disk hasil restore (ganti sesuai storage yang tersedia di Proxmox tujuan itu).
- Proses ini otomatis mendeteksi archive-nya berasal dari template, dan mengembalikan VM hasil restore **langsung sebagai template juga** (log akan menampilkan baris `Convert to template.` di akhir) — tidak perlu convert manual lagi.

### 4. Beres-beres setelah restore
1. **Masukkan ke pool** `campus-cloud` di Proxmox tujuan (kalau instance Proxmox tujuan ini juga dipakai dashboard CCD yang lain):
   ```bash
   pvesh set /pools/campus-cloud -vms <vmid-baru>
   ```
2. Cek konfigurasi tetap lengkap — CloudInit drive, `agent: 1`, `template: 1` — dengan `qm config <vmid-baru>`.
3. **Uji sekali** dengan clone percobaan dari CCD di deployment tujuan (ulangi Bagian D) — pastikan cloud-init & guest agent tetap berfungsi normal, karena walau config ikut terbawa, storage backend Proxmox tujuan bisa saja beda (mis. ZFS vs LVM-thin) dan kadang mempengaruhi performa clone.
4. **Hapus file backup** dari kedua sisi kalau sudah tidak perlu disimpan (`rm /var/lib/vz/dump/vzdump-qemu-*.vma.zst`) — file ini besar dan menumpuk kalau dibiarkan.

### Catatan
- Backup ini menyimpan **satu VM/template**, bukan seluruh pool sekaligus — ulangi Bagian E.1–E.3 untuk tiap template yang mau dipindahkan.
- Ukuran file backup sebanding dengan **data terpakai**, bukan ukuran disk yang dialokasikan (berkat kompresi zstd + deteksi blok kosong) — template 32GB bisa jadi cuma ~2GB kalau isinya masih instalasi OS bersih.
- Kalau Proxmox sumber dan tujuan beda versi mayor (mis. PVE 8 vs PVE 9), restore tetap biasanya kompatibel (format `.vma` sudah stabil lama), tapi tetap disarankan uji clone (Langkah 4.3) sebelum dipakai produksi.

---

## Bagian F — Perluas Disk Setelah Resize

Resize storage di CCD hanya memperbesar disk virtualnya. Partisi dan filesystem di dalam VM tidak ikut membesar. Setelah VM dinyalakan:

```bash
lsblk -f                      # lihat disk, partisi, dan jenis filesystem
sudo growpart /dev/sda 1      # perbesar partisi (sesuaikan disk dan nomor partisinya)
sudo resize2fs /dev/sda1      # ext4; untuk xfs: sudo xfs_growfs /; untuk btrfs: sudo btrfs filesystem resize max /
```

Nama disk dan partisi beda-beda per template, jadi ikuti hasil `lsblk`. `growpart` ada di paket `cloud-guest-utils` (Ubuntu); kalau belum ada di template, pasang di template supaya clone berikutnya langsung punya.
