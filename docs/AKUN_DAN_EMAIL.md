# Keamanan Akun dan Email

## Verifikasi 2 langkah (2FA)

Memakai TOTP (6 digit, 30 detik), cocok dengan Google Authenticator, Microsoft Authenticator, Authy, dan 1Password.

**Pengguna:** *Profil → Verifikasi 2 langkah → Aktifkan 2FA*. Pindai kode QR, masukkan kode, lalu **simpan 10 kode pemulihan**: tiap kode bisa dipakai sekali kalau ponsel hilang, dan tidak ditampilkan lagi. Pengguna bisa membuat kode pemulihan baru atau menonaktifkan 2FA (butuh password dan satu kode).

**Superadmin:** *Pengaturan Sistem → Kebijakan 2FA*: opsional untuk semua, **wajib untuk admin** (superadmin dan sysadmin), atau wajib untuk semua. Akun yang wajib tetapi belum memasang diminta memasangnya saat login berikutnya, dan tidak bisa menonaktifkannya. **Sangat disarankan mewajibkan 2FA untuk admin.**

**Pengguna kehilangan ponsel dan kode pemulihan:** admin memakai tombol **Reset 2FA** di halaman Users. Semua sesi pengguna itu berakhir dan ia bisa memasang ulang.

Keamanan: rahasia TOTP disimpan terenkripsi; kode yang sudah dipakai tidak bisa dipakai lagi; setelah 5 kode salah, tantangan login hangus dan percobaan dihitung ke kuncian akun; kode pemulihan disimpan sebagai hash.

## Email (SMTP)

*Pengaturan Sistem → Email (SMTP)*: server, port, keamanan (STARTTLS, SSL/TLS, atau tanpa), akun, alamat pengirim, dan **alamat dashboard untuk tautan** (mis. `https://ccd.kampus.ac.id`). Gunakan **Kirim uji** untuk memastikan SMTP bekerja; galatnya tampil langsung.

Email tidak dikirim langsung dari permintaan pengguna. Email masuk antrean dan dikirim worker dengan percobaan ulang (jeda naik hingga 1 jam, menyerah setelah 8 kali), jadi server SMTP yang lambat atau mati tidak mengganggu dashboard. Password SMTP disimpan terenkripsi dan tidak pernah dikembalikan oleh API.

Yang dikirim:

- **Peringatan masa sewa VM** ke pemilik VM, N hari sebelum habis (1–30, bawaan 3), sekali per batas sewa.
- **Tautan reset password** (lihat di bawah).

## Reset password lewat email

Bawaannya **mati**. Nyalakan di kartu Email (*Izinkan reset password mandiri lewat email*); butuh SMTP aktif dan alamat dashboard. Setelah itu, **Lupa password?** di halaman login meminta username atau email, dan mengirim tautan sekali pakai ke email akun.

- Jawaban selalu sama, terdaftar atau tidak, dan dikerjakan di latar belakang, jadi tidak bisa dipakai menebak akun.
- Token acak 256 bit, hanya hash-nya yang disimpan, berlaku 30 menit, sekali pakai; tautan baru membatalkan yang lama.
- Dibatasi 3 permintaan per akun dan 10 per alamat IP per jam.
- Setelah password diganti, semua sesi akun itu berakhir. 2FA tetap diminta saat login berikutnya.
- Akun tanpa email, nonaktif, atau kedaluwarsa tidak bisa memakai jalur ini; pengguna itu tetap bisa meminta bantuan admin.

Tanpa fitur ini, alurnya tetap seperti sebelumnya: pengguna mengirim permintaan bantuan dari halaman login dan admin memberi password sementara.
