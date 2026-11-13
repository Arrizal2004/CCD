# Template Otomatis dari Cloud Image

Membuat template VM dari image resmi tanpa langkah manual di Proxmox. Dari halaman **Server**, pilih Proxmox dan node, lalu **Template dari image**. Hasilnya (pool, CloudInit drive, guest agent diaktifkan) langsung muncul di **Create VM** dan VM massal.

## Image yang tersedia

| Image | Guest agent | Akun bawaan |
|---|---|---|
| Ubuntu Server 24.04 LTS | **tidak ada** di image | `ubuntu` |
| openSUSE Leap 16.0 (Minimal-VM Cloud) | ada | diatur saat Create VM (cloud-init) |
| openSUSE Leap 16.1 (Minimal-VM Cloud) | ada | diatur saat Create VM (cloud-init) |

Hanya tiga image ini supaya mudah dipelihara. Leap 16.0 sudah lebih lama dipakai; Leap 16.1 (rilis November 2026) membawa mode immutable dan kernel yang lebih baru. Untuk praktikum yang mengutamakan kestabilan, mulai dari 16.0. Checksum dibaca dari berkas checksum resmi dan diperiksa Proxmox saat mengunduh; semua URL memakai HTTPS.

> **Ubuntu tidak membawa qemu-guest-agent.** Akibatnya IP DHCP VM dari template ini tidak terdeteksi. Buat VM dengan **IP statis** atau lewat **switch CCD**, atau pasang `qemu-guest-agent` sendiri lalu buat ulang template. Dashboard menampilkan peringatan ini di hasil proses.

## Prasyarat Proxmox

- Proxmox VE **8.2 atau lebih baru** (tipe konten `import` dan `import-from`).
- Storage unduhan dengan konten **Import** aktif: *Datacenter → Storage → pilih storage → Content → Import*.
- Storage disk dengan konten *Disk image*.
- Sebuah **pool** (VM baru harus di dalam pool agar terlihat dashboard).
- Node punya akses internet ke host image.

### Izin token API

Tambahkan di *Datacenter → Permissions* untuk token yang dipakai CCD:

| Path | Izin |
|---|---|
| `/nodes/<node>` | `Sys.AccessNetwork` (node yang mengunduh image) |
| `/storage/<storage import>` | `Datastore.AllocateTemplate`, `Datastore.AllocateSpace` |
| `/storage/<storage disk>` | `Datastore.AllocateSpace` |
| `/pool/<pool>` | `VM.Allocate`, `VM.Config.Disk`, `VM.Config.Cloudinit`, `VM.Clone` |

Dashboard memeriksa izin ini lebih dulu dan menyebut yang kurang, tanpa mengunduh apa pun.

## Cara kerja

1. **Memeriksa node:** versi, storage, dan izin.
2. **Mengunduh** lewat Proxmox (`download-url`) dengan checksum. Ruang storage dicek lebih dulu.
3. **Mengimpor disk** ke VM baru (`import-from`) dan memperbesarnya sesuai pilihan Disk.
4. **Menjadikan template.**

Prosesnya berjalan di latar belakang dan tetap terlihat setelah halaman ditutup. Satu proses per image per node. Kalau backend berhenti di tengah jalan, proses ditandai *Terputus*; **Ulangi** memakai ulang berkas yang sudah diunduh. Kalau impor gagal, VM setengah jadi dihapus otomatis.
