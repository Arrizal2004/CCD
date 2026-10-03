# Panduan SSH ke VM (Linux, macOS, Windows)

Panduan ini untuk mahasiswa yang ingin masuk ke VM praktikum dari terminal sendiri, mengirim file dengan `scp`/`sftp`, atau bekerja dengan VS Code Remote-SSH. Koneksi lewat bastion di server dashboard, jadi tidak perlu VPN.

```
Laptop ──SSH key──▶ bastion <alamat-bastion>:2222 ──▶ VM Anda :22
```

## Yang Anda perlukan

| Data | Didapat dari |
|---|---|
| Akun dashboard yang punya VM | Admin lab |
| Alamat bastion dan port-nya, mis. `ssh.<domain-kampus>` port `2222` | Tombol **SSH** di kartu VM (halaman Servers) |
| IP VM, mis. `10.0.1.21` | Tombol **SSH** di kartu VM |
| Akun OS di dalam VM (user dan password/key) | Admin lab atau dibuat saat VM disiapkan |
| Fingerprint host key bastion (`SHA256:...`) | Tombol **SSH** di kartu VM, atau admin lab |

Bastion hanya mengantar Anda ke VM. Setelah itu Anda tetap login dengan akun OS di dalam VM, bukan dengan password dashboard.

## Langkah umum

1. **Buat SSH key** di laptop (sekali per laptop). Pakai jenis `ed25519`. Key RSA di bawah 3072 bit ditolak dashboard.
2. **Daftarkan public key** di dashboard: menu **Profil**, bagian **SSH Key**, tempel isi file `id_ed25519.pub`. Yang ditempel hanya file berakhiran `.pub`. Private key (`id_ed25519` tanpa `.pub`) tidak boleh dibagikan ke siapa pun.
3. **Isi file `~/.ssh/config`** dengan contoh di bawah, lalu ganti `<alamat-bastion>`, `<ip-vm>`, dan `<akun-os>`.
4. **Hubungkan** dengan `ssh vm-saya`. Saat pertama kali, cocokkan fingerprint yang muncul dengan fingerprint di tombol SSH sebelum mengetik `yes`.

Isi `~/.ssh/config` (sama untuk ketiga OS):
```
Host ccd-bastion
    HostName <alamat-bastion>
    Port 2222
    User tunnel
    IdentityFile ~/.ssh/id_ed25519
    IdentitiesOnly yes

Host vm-saya
    HostName <ip-vm>
    User <akun-os>
    ProxyJump ccd-bastion
    IdentityFile ~/.ssh/id_ed25519
    IdentitiesOnly yes
```
Punya lebih dari satu VM? Tambahkan blok `Host` baru dengan nama lain, mis. `vm-jaringan`, dengan `HostName` IP VM tersebut.

Tanpa file config, perintah sekali jalan juga bisa:
```bash
ssh -J tunnel@<alamat-bastion>:2222 <akun-os>@<ip-vm>
```

---

## Linux

OpenSSH client biasanya sudah terpasang. Cek dengan `ssh -V`. Kalau belum ada:
```bash
sudo apt install openssh-client        # Ubuntu, Debian
sudo zypper install openssh-clients    # openSUSE
sudo dnf install openssh-clients       # Fedora
```

Buat key dan tampilkan public key-nya:
```bash
ssh-keygen -t ed25519 -C "nama@laptop"     # tekan Enter untuk lokasi bawaan, isi passphrase
cat ~/.ssh/id_ed25519.pub                  # salin hasilnya ke dashboard
```

Buat file config dengan izin yang benar:
```bash
mkdir -p ~/.ssh && chmod 700 ~/.ssh
nano ~/.ssh/config                         # tempel contoh config di akhir file
chmod 600 ~/.ssh/config
```

Hubungkan:
```bash
ssh vm-saya
```

## macOS

OpenSSH sudah bawaan. Buka aplikasi **Terminal**, lalu jalankan perintah yang sama dengan Linux:
```bash
ssh-keygen -t ed25519 -C "nama@mac"
cat ~/.ssh/id_ed25519.pub | pbcopy          # public key langsung tersalin ke clipboard
mkdir -p ~/.ssh && chmod 700 ~/.ssh
nano ~/.ssh/config                          # tempel contoh config di akhir file
chmod 600 ~/.ssh/config
ssh vm-saya
```

Supaya passphrase tidak ditanya terus, simpan di Keychain. Tambahkan di bagian paling atas `~/.ssh/config`:
```
Host *
    AddKeysToAgent yes
    UseKeychain yes
```
Lalu sekali jalankan `ssh-add --apple-use-keychain ~/.ssh/id_ed25519`. Opsi `UseKeychain` hanya ada di macOS, jangan disalin ke config Linux atau Windows.

## Windows 10 dan 11

Windows sudah punya OpenSSH client. Buka **PowerShell** (bukan Command Prompt) dan cek dengan `ssh -V`. Kalau tidak dikenali, pasang lewat **Settings → System → Optional features** (Windows 10: **Settings → Apps → Optional features**), tambahkan **OpenSSH Client**, atau di PowerShell yang dibuka sebagai Administrator:
```powershell
Add-WindowsCapability -Online -Name OpenSSH.Client~~~~0.0.1.0
```

Buat key dan tampilkan public key-nya:
```powershell
ssh-keygen -t ed25519 -C "nama@laptop"
Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub | Set-Clipboard     # tersalin ke clipboard
```
Key tersimpan di `C:\Users\<nama-anda>\.ssh\`. Perintah `ssh` dan `ssh-keygen` juga jalan di Command Prompt. Di sana, tampilkan public key dengan `type %USERPROFILE%\.ssh\id_ed25519.pub`.

Buat file config. Nama file harus `config` tanpa ekstensi. Kalau dibuat langsung dengan Notepad, sering tersimpan sebagai `config.txt` dan tidak terbaca. Buat dulu filenya lewat PowerShell (file yang sudah ada tidak ditimpa), baru buka dengan Notepad:
```powershell
New-Item -ItemType Directory -Force $env:USERPROFILE\.ssh | Out-Null
$f = "$env:USERPROFILE\.ssh\config"
if (!(Test-Path $f)) { New-Item -ItemType File $f | Out-Null }
notepad $f                                  # tempel contoh config di akhir file, lalu simpan
Get-ChildItem $env:USERPROFILE\.ssh         # pastikan namanya "config", bukan "config.txt"
```

Hubungkan:
```powershell
ssh vm-saya
```

Kalau muncul error `CreateProcessW failed` atau `posix_spawn`, ganti baris `ProxyJump ccd-bastion` di blok `vm-saya` dengan:
```
    ProxyCommand C:\Windows\System32\OpenSSH\ssh.exe -W %h:%p ccd-bastion
```

**Pakai MobaXterm?** Buat session SSH ke `<ip-vm>` dengan user `<akun-os>`. Di tab **Network settings**, klik **SSH gateway (jump host)**, isi host `<alamat-bastion>`, port `2222`, user `tunnel`, dan pilih private key Anda. Cara paling sederhana tetap OpenSSH bawaan Windows di atas.

---

## Mengirim file

Dengan `~/.ssh/config` yang sudah benar, nama `vm-saya` bisa dipakai di semua perintah:
```bash
scp tugas.zip vm-saya:~/                 # laptop ke VM
scp vm-saya:~/hasil.log .                # VM ke laptop
scp -r folder-proyek vm-saya:~/          # satu folder
sftp vm-saya                             # mode interaktif
```
Di Windows, perintah yang sama dijalankan di PowerShell. Aplikasi seperti WinSCP dan FileZilla juga bisa, asalkan mendukung jump host atau tunnel.

## VS Code Remote-SSH

1. Pasang extension **Remote - SSH** di VS Code.
2. Tekan `Ctrl+Shift+P` (`Cmd+Shift+P` di macOS), pilih **Remote-SSH: Connect to Host...**, lalu pilih `vm-saya`. VS Code membaca `~/.ssh/config` yang sama.
3. Pilih jenis OS VM (Linux) kalau ditanya. VS Code memasang komponen server di VM saat pertama kali, jadi VM perlu akses internet.

## Kalau gagal

| Pesan | Artinya | Yang dilakukan |
|---|---|---|
| `tunnel@...: Permission denied (publickey)` | Bastion tidak mengenali key Anda | Pastikan key sudah didaftarkan di dashboard dengan akun yang punya VM tersebut, dan config memakai `IdentityFile` yang benar. Lihat di bawah cara mengecek key mana yang dikirim laptop |
| `Connection closed by UNKNOWN port 65535` | Lanjutan dari error di baris sebelumnya | Perbaiki error di baris atasnya |
| `This host key is known by the following other names/addresses` | Alamat bastion berubah, mis. dari IP ke domain | Aman diterima kalau fingerprint sama dengan yang di tombol SSH |
| `Too many authentication failures` | Terlalu banyak key dicoba | Pastikan ada `IdentitiesOnly yes` di config |
| `administratively prohibited` | VM itu bukan milik akun Anda, atau IP-nya salah | Cek IP di tombol SSH pada kartu VM |
| `<akun-os>@<ip-vm>: Permission denied` | Bastion sudah lewat, login ke VM yang gagal | Cek user dan password akun OS di VM |
| `Connection timed out` ke port 2222 | Jaringan Anda memblokir port 2222 | Coba jaringan lain (mis. hotspot HP) |
| `REMOTE HOST IDENTIFICATION HAS CHANGED` | Host key berubah sejak koneksi terakhir | Jangan langsung dihapus. Tanyakan ke admin apakah server memang diganti |
| `Bad owner or permissions on ~/.ssh/config` (Linux/macOS) | Izin file terlalu longgar | `chmod 600 ~/.ssh/config` |

**Key mana yang dikirim laptop?** Penyebab `Permission denied (publickey)` yang paling sering: laptop hanya punya key lama (mis. `id_rsa`) yang tidak didaftarkan, sedangkan `id_ed25519` belum pernah dibuat. Cek dengan:
```bash
ssh -v -J tunnel@<alamat-bastion>:2222 <akun-os>@<ip-vm> 2>&1 | grep -i offering       # Linux, macOS
ssh -v -J tunnel@<alamat-bastion>:2222 <akun-os>@<ip-vm> 2>&1 | findstr /i offering    # Windows
```
Baris `Offering public key: ...` menunjukkan file key dan fingerprint-nya. Fingerprint itu harus sama dengan salah satu key di **Profil → SSH Key**. Kalau tidak ada baris `Offering` untuk `id_ed25519`, buat key-nya dulu (langkah umum nomor 1).

Akses lewat SSH mengikuti akses di dashboard. Kalau VM sudah tidak di-assign ke Anda atau akun dinonaktifkan, SSH langsung ditolak.

## Keamanan

- Pakai passphrase saat membuat key.
- Laptop hilang atau key bocor? Segera hapus key itu di **Profil → SSH Key**, lalu buat key baru.
- Jangan pernah mengirim isi private key (file tanpa `.pub`) ke siapa pun, termasuk admin.
- Setiap koneksi lewat bastion tercatat di dashboard: akun, key, IP asal, VM tujuan, dan waktunya. Isi sesi (perintah dan file) tidak terlihat oleh bastion dan tidak direkam.
