# Fitur Kelas: Reset Lab, Mode Ujian, dan Laporan Pemakaian

Tiga fitur untuk dosen dan admin lab. Semuanya bekerja per **kelas** (grup di halaman Grup): VM kelas adalah VM yang di-assign langsung ke anggota kelas ditambah VM milik grup.

Buka halaman **Grup**, pilih kelas, lalu pakai tab **Lab**, **Ujian**, atau **Laporan**.

## 1. Reset lab ke kondisi awal

Kondisi awal sebuah VM adalah snapshot bernama `ccd-baseline`.

**Menetapkan kondisi awal (sekali per VM):**

1. Siapkan VM sampai bersih (paket terpasang, konfigurasi praktikum, tanpa berkas mahasiswa).
2. **Matikan VM.** Snapshot dari VM yang sedang jalan bisa tidak konsisten, jadi dashboard menolaknya.
3. Buka **Detail VM → Info → Kondisi awal lab → Jadikan kondisi awal**. Menjalankannya lagi menggantikan baseline lama.

**Mengembalikan ke kondisi awal:**

- **Satu VM:** tombol **Reset ke kondisi awal** di bagian yang sama.
- **Seluruh kelas:** tab **Lab** → **Reset semua VM kelas**. Berjalan di latar belakang; hasil tiap VM tampil (berhasil, belum punya baseline, atau galat dari Proxmox). Sysadmin hanya menyentuh VM di Proxmox yang ditugaskan kepadanya.
- **Oleh mahasiswa:** superadmin bisa mengizinkan mahasiswa me-reset VM miliknya sendiri di **Pengaturan Sistem → Reset lab oleh mahasiswa**. Bawaannya mati. Syaratnya: akses penuh ke VM itu, bukan saat ujian, dan jeda 2 menit antar-reset.

Reset mengembalikan disk VM ke snapshot, lalu menyalakannya lagi (bisa dimatikan lewat opsi "Nyalakan VM setelah reset"). **Semua perubahan sejak baseline hilang.** VM tanpa baseline dilewati dan dilaporkan; reset tidak menyentuh VM lain.

Izin Proxmox untuk token: `VM.Snapshot` dan `VM.Snapshot.Rollback` (sama dengan fitur snapshot yang sudah ada), serta `VM.PowerMgmt`.

## 2. Mode ujian

Jadwalkan ujian untuk satu kelas: nama, waktu mulai, dan waktu selesai (paling lama 24 jam; dua ujian satu kelas tidak boleh bertumpuk).

**Selama ujian berjalan, untuk mahasiswa kelas itu:**

- Hanya **VM ujian** yang terlihat dan bisa di-Connect (VM kelas). VM lain, termasuk VM dari grup lain, tersembunyi sampai ujian selesai.
- Tidak bisa menyalakan, mematikan, snapshot, rollback, atau me-reset VM.
- **Open Web ditutup** (bisa dimatikan per ujian).
- Admin tidak terpengaruh. Mahasiswa melihat spanduk ujian di halaman Server.

**Opsi per ujian:**

| Opsi | Bawaan | Efek |
|---|---|---|
| Reset VM ke kondisi awal saat mulai | ya | Setiap mahasiswa mulai dari kondisi bersih (butuh baseline) |
| Nyalakan VM saat mulai | ya | VM siap saat ujian dimulai |
| Matikan VM saat selesai | ya | VM tidak dipakai lagi setelah waktu habis |
| Tutup Open Web selama ujian | ya | |

Saat ujian dimulai, sesi Remote anggota kelas diputus supaya mereka masuk ulang ke VM yang bersih. Hasil aksi mulai dan selesai (berapa VM berhasil) tampil di daftar ujian. Aksi dijalankan sekali oleh job yang berjalan tiap menit, sedangkan **pembatasan mengikuti jam** dan berlaku tepat waktu.

Tombol: **Mulai sekarang**, **Akhiri sekarang** (VM dimatikan), **Batalkan** (pembatasan dicabut, VM dibiarkan), dan **Hapus**. Ujian yang jendelanya sudah lewat sebelum sempat dimulai (mis. backend mati) ditandai selesai tanpa aksi apa pun.

**Batasan yang perlu diketahui:** pembatasan berlaku di dashboard dan API-nya. Mahasiswa yang punya akses langsung ke antarmuka Guacamole tidak dibatasi; jangan mengekspos Guacamole ke pengguna (di instalasi standar ia hanya dijangkau lewat nginx dashboard).

## 3. Laporan pemakaian kelas

Tab **Laporan**: pilih rentang tanggal (bawaan 30 hari terakhir, paling lama 366 hari), lalu **Tampilkan**. **Unduh CSV** menghasilkan berkas yang dibuka Excel dengan benar.

Per mahasiswa:

| Kolom | Sumber |
|---|---|
| Login | Activity Log (`AUTH_LOGIN`) |
| Sesi dan menit Remote | Riwayat Guacamole (SSH/RDP) |
| Sesi dan menit Web | Sesi Open Web (menit dihitung sampai akses terakhir) |
| Sesi dan menit SSH | Sesi bastion SSH |
| Hari aktif, VM dipakai, terakhir aktif | Gabungan di atas, menurut zona waktu sistem |

Ringkasan menampilkan jumlah mahasiswa, **partisipasi** (persen yang punya sedikitnya satu sesi), total menit, dan rata-rata menit mahasiswa aktif. Menit dihitung dari bagian sesi yang jatuh di dalam rentang; sesi yang masih berjalan dihitung sampai sekarang.

Kalau database Guacamole tidak bisa dibaca, laporan tetap tampil tanpa data Remote dan memberi peringatan. Riwayat ikut dihapus sesuai lama penyimpanan log di Pengaturan Sistem, jadi laporan tidak bisa melihat lebih jauh dari itu.
