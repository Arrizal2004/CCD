#!/usr/bin/env bash
# Campus Cloud Dashboard — one-shot setup.
#
#   ./setup.sh
#
# Apa yang dilakukan (idempotent — aman dijalankan berkali-kali):
#   1. Cek Docker + Docker Compose plugin. Kalau belum ada, install otomatis
#      (Ubuntu/Debian lewat installer resmi Docker; openSUSE lewat zypper).
#   2. Siapkan backend/.env — kalau belum ada, dibuat dari .env.example dengan
#      secret ter-generate otomatis (openssl rand). Kalau sudah ada, dipakai
#      apa adanya, tidak ditimpa.
#   3. Cek port 80 kosong, bangun image satu per satu (hemat memori), jalankan, tunggu backend siap.
#   4. Cetak ringkasan akses + langkah berikutnya.
#
# Setelah ini selesai, ikuti docs/PANDUAN_DEPLOYMENT.md mulai Bagian 2 untuk
# menyambungkan Proxmox (itu tidak bisa diotomasi — tergantung topologi Anda).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"

log()  { echo -e "\033[1;36m[setup]\033[0m $*"; }
warn() { echo -e "\033[1;33m[setup]\033[0m $*"; }
err()  { echo -e "\033[1;31m[setup]\033[0m $*" >&2; }

if [ ! -d "$BACKEND_DIR" ]; then
    err "Folder 'backend/' tidak ditemukan di $SCRIPT_DIR — jalankan skrip ini dari root repo hasil git clone."
    exit 1
fi

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
    if command -v sudo >/dev/null 2>&1; then
        SUDO="sudo"
    else
        err "Perlu root atau sudo untuk install Docker / jalankan container."
        exit 1
    fi
fi

COMPOSE=()

detect_compose() {
    if $SUDO docker compose version >/dev/null 2>&1; then
        COMPOSE=($SUDO docker compose)
        return 0
    elif command -v docker-compose >/dev/null 2>&1; then
        COMPOSE=($SUDO docker-compose)
        return 0
    fi
    return 1
}

install_docker() {
    log "Docker/Compose belum lengkap — menginstall..."
    if [ ! -f /etc/os-release ]; then
        err "Tidak bisa deteksi distro (/etc/os-release tidak ada). Install Docker manual: https://docs.docker.com/engine/install/"
        exit 1
    fi
    . /etc/os-release
    case "${ID:-}" in
        ubuntu|debian)
            log "Distro: $ID — pakai installer resmi Docker (get.docker.com)."
            curl -fsSL https://get.docker.com | $SUDO sh
            ;;
        opensuse*|sles)
            log "Distro: $ID — pakai zypper."
            $SUDO zypper --non-interactive refresh
            $SUDO zypper --non-interactive install docker docker-compose
            ;;
        *)
            warn "Distro '${ID:-unknown}' tidak dikenali skrip ini — mencoba installer resmi Docker (get.docker.com) sebagai fallback."
            curl -fsSL https://get.docker.com | $SUDO sh
            ;;
    esac
    $SUDO systemctl enable --now docker 2>/dev/null || true
}

check_docker() {
    if command -v docker >/dev/null 2>&1 && detect_compose; then
        log "Docker + Compose sudah siap ($($SUDO docker --version))."
        return
    fi
    install_docker
    $SUDO systemctl enable --now docker 2>/dev/null || true
    if ! detect_compose; then
        err "Docker Compose tetap tidak terdeteksi setelah instalasi. Install manual: https://docs.docker.com/compose/install/"
        exit 1
    fi
    log "Docker + Compose berhasil terpasang."
}

gen_secret()   { openssl rand -hex 32; }

# Token bastion SSH juga ditambahkan ke .env lama, supaya bastion tinggal diaktifkan
# dengan COMPOSE_PROFILES=ssh tanpa langkah manual lain.
ensure_bastion_token() {
    local env_file="$1"
    grep -qE '^BASTION_TOKEN=.+' "$env_file" && return
    if grep -q '^BASTION_TOKEN=' "$env_file"; then
        sed -i "s#^BASTION_TOKEN=.*#BASTION_TOKEN=$(gen_secret)#" "$env_file"
    else
        printf '\nBASTION_TOKEN=%s\n' "$(gen_secret)" >> "$env_file"
    fi
}
gen_password() { openssl rand -base64 18 | tr -dc 'A-Za-z0-9' | cut -c1-20; }

# Jaringan yang dipakai VPS ini (alamat interface dan route), format "iface=cidr,...". Backend berjalan
# di container dan tidak bisa melihatnya sendiri; dengan daftar ini blok alamat switch yang bentrok
# dengan jaringan VPS (mis. jaringan privat provider) ditolak. Diperbarui setiap setup.sh dijalankan.
host_networks() {
    {
        ip -4 -o addr show 2>/dev/null | awk '$2 != "lo" && $2 !~ /^tailscale/ {print $2 "=" $4}'
        ip -4 route show table main 2>/dev/null | awk '$1 != "default" && $0 !~ / dev tailscale/ {
            dst = $1; if (dst !~ /\//) dst = dst "/32"
            for (i = 1; i <= NF; i++) if ($i == "dev") print $(i + 1) "=" dst }'
    } | sort -u | paste -sd, -
}

ensure_host_networks() {
    local env_file="$1" nets
    nets="$(host_networks)"
    [ -n "$nets" ] || return 0
    if grep -q '^HOST_NETWORKS=' "$env_file"; then
        sed -i "s#^HOST_NETWORKS=.*#HOST_NETWORKS=${nets}#" "$env_file"
    else
        printf '\n# Diisi otomatis oleh setup.sh: jaringan VPS ini, supaya blok alamat switch tidak bentrok.\nHOST_NETWORKS=%s\n' "$nets" >> "$env_file"
    fi
}

GENERATED_GUAC_PASS=""
GENERATED_ADMIN_PASS=""
DETECTED_HOST=""

setup_env() {
    local env_file="$BACKEND_DIR/.env"
    DETECTED_HOST="$(hostname -I 2>/dev/null | awk '{print $1}')"
    DETECTED_HOST="${DETECTED_HOST:-localhost}"

    if [ -f "$env_file" ]; then
        log "backend/.env sudah ada — dipakai apa adanya (tidak ditimpa)."
        ensure_bastion_token "$env_file"
        ensure_host_networks "$env_file"
        return
    fi
    if [ ! -f "$BACKEND_DIR/.env.example" ]; then
        err "backend/.env.example tidak ditemukan — tidak bisa membuat .env otomatis."
        exit 1
    fi

    log "Membuat backend/.env baru dari .env.example, dengan secret ter-generate otomatis..."
    cp "$BACKEND_DIR/.env.example" "$env_file"

    local jwt_secret agent_secret guac_pass
    jwt_secret="$(gen_secret)"
    agent_secret="$(gen_secret)"
    guac_pass="$(gen_password)"

    sed -i "s#^JWT_SECRET=.*#JWT_SECRET=${jwt_secret}#"           "$env_file"
    sed -i "s#^AGENT_ENC_SECRET=.*#AGENT_ENC_SECRET=${agent_secret}#" "$env_file"
    sed -i "s#^GUAC_ADMIN_PASS=.*#GUAC_ADMIN_PASS=${guac_pass}#"   "$env_file"
    sed -i "s#^ALLOWED_ORIGINS=.*#ALLOWED_ORIGINS=http://${DETECTED_HOST}#" "$env_file"

    local admin_pass
    admin_pass="$(gen_password)"
    sed -i "s#^INITIAL_ADMIN_PASSWORD=.*#INITIAL_ADMIN_PASSWORD=${admin_pass}#" "$env_file"
    sed -i "s#^POSTGRES_PASSWORD=.*#POSTGRES_PASSWORD=$(openssl rand -hex 24)#" "$env_file"
    ensure_bastion_token "$env_file"
    sed -i "s#^GUAC_DB_PASSWORD=.*#GUAC_DB_PASSWORD=$(openssl rand -hex 24)#"   "$env_file"
    ensure_host_networks "$env_file"
    chmod 600 "$env_file"

    GENERATED_GUAC_PASS="$guac_pass"
    GENERATED_ADMIN_PASS="$admin_pass"
    log "backend/.env dibuat. Secret di-generate otomatis (openssl rand) — tidak perlu diisi manual."
}

check_port80() {
    # Dashboard dilayani di port 80. Kalau port itu sudah dipakai aplikasi lain (Apache, nginx
    # bawaan OS, dsb.), container frontend gagal start — hentikan di sini dengan pesan yang jelas.
    command -v ss >/dev/null 2>&1 || return 0
    if $SUDO docker ps --format '{{.Names}}' 2>/dev/null | grep -qx ccd-frontend; then
        return 0   # sudah dipakai dashboard ini sendiri (redeploy)
    fi
    if [ -n "$(ss -ltnH 'sport = :80' 2>/dev/null)" ]; then
        err "Port 80 sudah dipakai proses lain:"
        $SUDO ss -ltnp 'sport = :80' >&2 || true
        err "Hentikan proses itu dulu (mis. 'sudo systemctl disable --now apache2' atau nginx), lalu jalankan ulang ./setup.sh."
        exit 1
    fi
}

deploy() {
    # Image dibangun satu per satu: build backend (pip) dan frontend (npm) bersamaan bisa
    # menghabiskan RAM di VPS kecil (2 GB) sampai SSH tidak responsif.
    log "Membangun image backend (beberapa menit di percobaan pertama)..."
    (cd "$BACKEND_DIR" && "${COMPOSE[@]}" build backend)
    log "Membangun image frontend (npm ci + vite build)..."
    (cd "$BACKEND_DIR" && "${COMPOSE[@]}" build frontend)
    if $SUDO grep -qE '^COMPOSE_PROFILES=.*\bssh\b' "$BACKEND_DIR/.env" 2>/dev/null; then
        log "Membangun image bastion SSH..."
        (cd "$BACKEND_DIR" && "${COMPOSE[@]}" build bastion)
    fi
    log "Menjalankan semua container (menunggu database dan Guacamole siap)..."
    (cd "$BACKEND_DIR" && "${COMPOSE[@]}" up -d)
    # nginx di frontend mengingat IP backend saat start. Restart supaya tetap benar setelah
    # backend dibuat ulang saat update.
    (cd "$BACKEND_DIR" && "${COMPOSE[@]}" restart frontend)
}

wait_healthy() {
    log "Menunggu backend siap..."
    local tries=0
    until curl -sf http://localhost:8001/health >/dev/null 2>&1; do
        tries=$((tries + 1))
        if [ "$tries" -ge 90 ]; then
            warn "Backend belum merespons setelah 3 menit. Cek log: (cd backend && ${COMPOSE[*]} logs backend)"
            return
        fi
        sleep 2
    done
    log "Backend siap."
}

setup_backup_cron() {
    chmod +x "$SCRIPT_DIR/backend/scripts/backup-db.sh" "$SCRIPT_DIR/backend/scripts/restore-db.sh" 2>/dev/null || true
    local marker="# campus-cloud-dashboard: backup-db (jangan hapus baris ini, dipakai untuk cek idempotensi)"
    local cron_line="0 2 * * * ${SCRIPT_DIR}/backend/scripts/backup-db.sh >> /var/log/ccd-backup.log 2>&1 ${marker}"
    local current
    current="$($SUDO crontab -l 2>/dev/null || true)"
    if echo "$current" | grep -qF "$marker"; then
        log "Cron backup database harian sudah terpasang — tidak diubah."
        return
    fi
    log "Memasang cron backup database harian (02:00, retensi 14 hari, ke /var/backups/campus-cloud-dashboard)..."
    { [ -n "$current" ] && echo "$current"; echo "$cron_line"; } | $SUDO crontab -
    log "Cron terpasang. Uji manual kapan saja: sudo ${SCRIPT_DIR}/backend/scripts/backup-db.sh"
}

# VPS kecil tanpa swap: saat RAM penuh (build image, banyak sesi Guacamole) kernel mematikan proses
# secara acak. Swapfile 2 GB dibuat sekali, hanya kalau RAM < 4 GB, belum ada swap, dan disk cukup.
ensure_swap() {
    if [ -n "$(swapon --show --noheadings 2>/dev/null)" ]; then return 0; fi
    local mem_mb free_mb
    mem_mb=$(awk '/MemTotal/ {print int($2 / 1024)}' /proc/meminfo)
    [ "$mem_mb" -ge 4096 ] && return 0
    free_mb=$(df -Pm / | awk 'NR == 2 {print $4}')
    if [ "$free_mb" -lt 6144 ]; then
        warn "RAM ${mem_mb} MB tanpa swap, tetapi disk kosong < 6 GB. Swap tidak dibuat."
        return 0
    fi
    log "RAM ${mem_mb} MB tanpa swap: membuat swapfile 2 GB di /swapfile..."
    $SUDO fallocate -l 2G /swapfile 2>/dev/null || $SUDO dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
    $SUDO chmod 600 /swapfile
    $SUDO mkswap /swapfile >/dev/null
    $SUDO swapon /swapfile
    grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' | $SUDO tee -a /etc/fstab >/dev/null
    echo 'vm.swappiness=10' | $SUDO tee /etc/sysctl.d/99-ccd-swap.conf >/dev/null
    $SUDO sysctl -p /etc/sysctl.d/99-ccd-swap.conf >/dev/null 2>&1 || true
}

main() {
    check_docker
    setup_env
    check_port80
    ensure_swap
    deploy
    wait_healthy
    setup_backup_cron

    echo
    log "========================================================"
    log " Selesai."
    log " Dashboard : http://${DETECTED_HOST}"
    if [ -n "$GENERATED_ADMIN_PASS" ]; then
        log " Login awal: admin / ${GENERATED_ADMIN_PASS}  (SIMPAN INI, tersimpan juga di backend/.env → INITIAL_ADMIN_PASSWORD)"
    else
        log " Login awal: admin dengan INITIAL_ADMIN_PASSWORD di backend/.env (kosong = admin123, segera ganti)."
    fi
    if [ -n "$GENERATED_GUAC_PASS" ]; then
        log " Password admin Guacamole (auto-generated, SIMPAN INI): ${GENERATED_GUAC_PASS}"
        log " (Tersimpan juga di backend/.env → GUAC_ADMIN_PASS)"
    fi
    log " Backup database: cron harian jam 02:00 aktif → /var/backups/campus-cloud-dashboard (retensi 14 hari)."
    log " Langkah berikutnya (setup Proxmox, template, dst):"
    log "   docs/PANDUAN_DEPLOYMENT.md — mulai dari Bagian 2"
    log "========================================================"
}

main "$@"
