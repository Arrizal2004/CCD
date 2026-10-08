#!/usr/bin/env bash
# Siapkan host Proxmox untuk fitur Switch (Jaringan) di Campus Cloud Dashboard.
# Jalankan sebagai root di setiap host Proxmox, sekali saja (aman dijalankan ulang):
#
#   bash ccd-net-setup.sh --pool 10.111.0.0/16 --token 'root@pam!ccd-dashboard'
#   bash ccd-net-setup.sh --pool 192.168.111.0/24,192.168.112.0/24 --token 'root@pam!ccd-dashboard'
#
# --pool berisi SEMUA blok alamat Proxmox ini, sama dengan daftar di dashboard (Topology → Switch).
# Jalankan ulang setiap kali blok ditambah atau dihapus di dashboard.
#
# Yang dilakukan:
#   1. Membuat SDN zone tipe Simple (bawaan: "ccd") tempat dashboard membuat switch.
#   2. Memberi token CCD izin mengelola VNet di zone itu dan menerapkan konfigurasi SDN.
#      Token tidak mendapat izin atas zone lain, VM lain, atau konfigurasi host.
#   3. Memasang aturan isolasi (nftables) yang tetap aktif setelah reboot. VM di sebuah switch hanya
#      bisa berbicara dengan VM lain di switch yang sama, diakses dari CCD lewat Tailscale, dan keluar
#      ke internet lewat NAT. Switch lain, jaringan kampus, tailnet, dan host Proxmox tertutup.
#   4. Menjalankan penerus DNS (dnsmasq) yang hanya mendengarkan di bridge switch: VM memakai gateway
#      switch sebagai DNS, host meneruskannya ke DNS yang dipakai host sendiri. Jaringan kampus sering
#      memblokir DNS publik, dan DNS kampus berada di jaringan privat yang ditutup untuk switch.
#   5. Menambahkan blok alamat ke route yang diiklankan Tailscale. Route lain tetap dipertahankan; route
#      blok yang dulu dipasang skrip ini tapi sudah tidak ada di --pool dicabut.
#
# Opsi: --zone <id>  nama SDN zone (bawaan ccd, harus sama dengan di dashboard)
#       --yes        jangan bertanya sebelum mengubah route Tailscale
#       --remove     lepas aturan isolasi dan penerus DNS (zone, izin, dan route Tailscale tidak diubah)
set -euo pipefail

POOL="" TOKEN="" ZONE="ccd" YES=0 REMOVE=0
VNET_PREFIX="ccd"                       # nama bridge switch buatan dashboard selalu diawali ini
NFT_DIR=/etc/ccd-net
NFT_FILE=$NFT_DIR/ccd-net.nft
UNIT=/etc/systemd/system/ccd-net.service
DNS_CONF=$NFT_DIR/dnsmasq.conf
DNS_UNIT=/etc/systemd/system/ccd-dns.service

say()  { echo -e "\033[1;36m[ccd-net]\033[0m $*"; }
warn() { echo -e "\033[1;33m[ccd-net]\033[0m $*"; }
die()  { echo -e "\033[1;31m[ccd-net]\033[0m $*" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --pool)   POOL="${2:-}"; shift 2 ;;
        --token)  TOKEN="${2:-}"; shift 2 ;;
        --zone)   ZONE="${2:-}"; shift 2 ;;
        --yes|-y) YES=1; shift ;;
        --remove) REMOVE=1; shift ;;
        *) die "Opsi tidak dikenal: $1" ;;
    esac
done

[ "$(id -u)" = 0 ] || die "Jalankan sebagai root."
command -v pvesh >/dev/null || die "Ini bukan host Proxmox VE (pvesh tidak ditemukan)."

if [ "$REMOVE" = 1 ]; then
    systemctl disable --now ccd-net.service ccd-dns.service 2>/dev/null || true
    nft delete table inet ccd_net 2>/dev/null || true
    rm -f "$UNIT" "$NFT_FILE" "$DNS_UNIT" "$DNS_CONF"; systemctl daemon-reload
    say "Aturan isolasi dan penerus DNS dilepas. SDN zone, izin token, dan route Tailscale tidak diubah."
    exit 0
fi

[ -n "$POOL" ] || die "Isi --pool, mis. --pool 10.111.0.0/16 (semua blok alamat di dashboard, pisahkan dengan koma)."
[ -n "$TOKEN" ] || die "Isi --token, mis. --token 'root@pam!ccd-dashboard' (Token ID yang dipakai dashboard)."
[[ "$ZONE" =~ ^[a-zA-Z][a-zA-Z0-9]{0,7}$ ]] || die "Nama zone hanya huruf/angka, maks 8 karakter."
# POOL dirapikan menjadi "a,b,..." (tanpa spasi, urutan dipertahankan).
POOL=$(python3 - "$POOL" <<'PY'
import ipaddress, sys
private = [ipaddress.ip_network(x) for x in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]
pools = []
for text in [t.strip() for t in sys.argv[1].split(",") if t.strip()]:
    try:
        n = ipaddress.ip_network(text, strict=True)
    except ValueError as e:
        sys.exit(f"Blok alamat tidak valid: {e}")
    if n.version != 4 or not any(n.subnet_of(p) for p in private) or not 8 <= n.prefixlen <= 29:
        sys.exit(f"Blok {text} harus jaringan IPv4 privat antara /8 dan /29, mis. 10.111.0.0/16")
    clash = next((p for p in pools if p.overlaps(n)), None)
    if clash:
        sys.exit(f"Blok {n} bertabrakan dengan blok {clash}")
    pools.append(n)
if not pools:
    sys.exit("Isi minimal satu blok alamat")
print(",".join(map(str, pools)))
PY
) || die "Blok alamat tidak valid."
# Blok tidak boleh bertabrakan dengan alamat yang sudah dipakai host ini (mis. vmbr0).
python3 - "$POOL" "$VNET_PREFIX" "$(ip -j -4 addr show)" <<'PY' || die "Pilih blok alamat lain."
import ipaddress, json, sys
pools, prefix = [ipaddress.ip_network(p) for p in sys.argv[1].split(",")], sys.argv[2]
for link in json.loads(sys.argv[3]):
    if link["ifname"].startswith(prefix):
        continue
    for a in link.get("addr_info", []):
        net = ipaddress.ip_interface("%s/%s" % (a["local"], a["prefixlen"])).network
        for pool in pools:
            if net.overlaps(pool) and not net.is_loopback:
                sys.exit("Blok %s bertabrakan dengan %s di %s" % (pool, net, link["ifname"]))
PY
# Blok yang dipasang skrip ini sebelumnya (dari berkas aturan lama), supaya route blok yang sudah
# dihapus di dashboard bisa dicabut dari Tailscale. Format lama hanya punya satu blok di komentarnya.
PREV_POOL=""
if [ -f "$NFT_FILE" ]; then
    PREV_POOL=$(sed -n -e 's/^# ccd-pools: //p' -e 's/^# Blok switch CCD: \([0-9./]*\)\. .*/\1/p' "$NFT_FILE" | head -1)
fi

# ── Prasyarat ────────────────────────────────────────────────────────────────
dpkg -s ifupdown2 >/dev/null 2>&1 || die "SDN Proxmox butuh paket ifupdown2 (apt install ifupdown2)."
grep -qE '^\s*source\s+/etc/network/interfaces\.d/\*' /etc/network/interfaces \
    || die "Tambahkan baris 'source /etc/network/interfaces.d/*' di akhir /etc/network/interfaces, lalu jalankan ulang skrip ini (wajib untuk SDN)."
if ! command -v nft >/dev/null; then
    say "Memasang nftables…"
    apt-get install -y nftables >/dev/null
fi
if [ "$(sysctl -n net.ipv4.ip_forward)" != 1 ]; then
    say "Mengaktifkan IP forwarding…"
    echo 'net.ipv4.ip_forward = 1' > /etc/sysctl.d/99-ccd-net.conf
    sysctl -p /etc/sysctl.d/99-ccd-net.conf >/dev/null
fi

# ── 1. SDN zone ──────────────────────────────────────────────────────────────
if pvesh get "/cluster/sdn/zones/$ZONE" --output-format json >/dev/null 2>&1; then
    TYPE=$(pvesh get "/cluster/sdn/zones/$ZONE" --output-format json | python3 -c 'import json,sys; print(json.load(sys.stdin).get("type",""))')
    [ "$TYPE" = simple ] || die "Zone '$ZONE' sudah ada tapi bertipe '$TYPE'. Pakai --zone dengan nama lain."
    say "SDN zone '$ZONE' (Simple) sudah ada."
else
    say "Membuat SDN zone '$ZONE' (Simple)…"
    pvesh create /cluster/sdn/zones --zone "$ZONE" --type simple --ipam pve
    pvesh set /cluster/sdn >/dev/null
fi

# ── 2. Izin token ────────────────────────────────────────────────────────────
pveum user token list "${TOKEN%%!*}" --output-format json 2>/dev/null \
    | python3 -c 'import json,sys; ids={t["tokenid"] for t in json.load(sys.stdin)}; sys.exit(0 if sys.argv[1] in ids else 1)' "${TOKEN#*!}" \
    || die "Token '$TOKEN' tidak ditemukan. Cek Token ID di dashboard (Integrations → Proxmox Instances)."
PRIVS="SDN.Allocate,SDN.Audit,SDN.Use"
pveum role add CCDNetwork --privs "$PRIVS" 2>/dev/null || pveum role modify CCDNetwork --privs "$PRIVS"
pveum acl modify "/sdn/zones/$ZONE" --tokens "$TOKEN" --roles CCDNetwork
# Menerapkan konfigurasi SDN butuh SDN.Allocate di /sdn; tanpa propagasi, jadi tidak berlaku ke zone lain.
pveum acl modify /sdn --tokens "$TOKEN" --roles CCDNetwork --propagate 0
say "Izin token '$TOKEN' untuk zone '$ZONE' diberikan."

# ── 3. Aturan isolasi ────────────────────────────────────────────────────────
mkdir -p "$NFT_DIR"
cat > "$NFT_FILE" <<EOF
#!/usr/sbin/nft -f
# Dibuat oleh ccd-net-setup.sh. Jangan diedit manual: jalankan ulang skripnya.
# Blok switch CCD: ${POOL//,/, }. Bridge switch buatan dashboard bernama ${VNET_PREFIX}*.
# ccd-pools: $POOL
table inet ccd_net
delete table inet ccd_net
table inet ccd_net {
    set ccd_pool { type ipv4_addr; flags interval; elements = { ${POOL//,/, } } }
    set ccd_private { type ipv4_addr; flags interval; elements = { 10.0.0.0/8, 100.64.0.0/10, 169.254.0.0/16, 172.16.0.0/12, 192.168.0.0/16 } }

    # Isolasi dikenali dari bridge switch (${VNET_PREFIX}*), bukan dari alamatnya, supaya switch di blok yang
    # baru ditambahkan di dashboard langsung terisolasi walau skrip ini belum dijalankan ulang.
    chain forward {
        type filter hook forward priority -5; policy accept;
        # Lalu lintas di dalam bridge firewall per-VM (fwbr) adalah bagian dari switch yang sama.
        iifname "fwbr*" accept
        # Antar-switch: tujuan bukan di bridge tempat paket masuk berarti paket di-route ke switch lain.
        iifname "${VNET_PREFIX}*" oifname "${VNET_PREFIX}*" fib daddr . iif oif missing counter drop comment "antar-switch"
        # Alamat di blok yang belum menjadi switch tidak boleh bocor ke jaringan kampus.
        ip daddr @ccd_pool oifname != "${VNET_PREFIX}*" counter drop comment "alamat blok yang belum dipakai"
        # Dari switch ke jaringan privat (kampus, tailnet): hanya balasan, bukan koneksi baru.
        iifname "${VNET_PREFIX}*" oifname != "${VNET_PREFIX}*" ip daddr @ccd_private ct state new counter drop comment "switch ke jaringan privat"
        # Ke switch hanya dari CCD lewat Tailscale.
        oifname "${VNET_PREFIX}*" iifname != "${VNET_PREFIX}*" iifname != "tailscale0" ct state new counter drop comment "masuk selain dari CCD"
    }

    chain input {
        type filter hook input priority -5; policy accept;
        iifname "${VNET_PREFIX}*" ct state established,related accept
        iifname "${VNET_PREFIX}*" icmp type echo-request accept comment "ping gateway"
        iifname "${VNET_PREFIX}*" meta l4proto { udp, tcp } th dport 53 accept comment "DNS ke gateway (penerus dnsmasq)"
        iifname "${VNET_PREFIX}*" counter drop comment "switch tidak boleh mengakses host Proxmox"
    }
}
EOF
nft -c -f "$NFT_FILE" || die "Aturan nftables ditolak; tidak ada yang diubah."
cat > "$UNIT" <<EOF
[Unit]
Description=CCD: isolasi switch (nftables)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/sbin/nft -f $NFT_FILE
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable ccd-net.service >/dev/null 2>&1
systemctl restart ccd-net.service
say "Aturan isolasi aktif (nft list table inet ccd_net)."

# ── 4. Penerus DNS ───────────────────────────────────────────────────────────
# dnsmasq-base hanya berisi programnya, tanpa layanan dnsmasq bawaan yang mendengarkan di semua interface.
if ! command -v dnsmasq >/dev/null; then
    say "Memasang dnsmasq-base…"
    apt-get install -y dnsmasq-base >/dev/null
fi
cat > "$DNS_CONF" <<EOF
# Dibuat oleh ccd-net-setup.sh. Penerus DNS untuk switch CCD: hanya di bridge ${VNET_PREFIX}*, tanpa DHCP.
# Kueri diteruskan ke DNS yang dipakai host ini (/etc/resolv.conf).
interface=${VNET_PREFIX}*
except-interface=lo
bind-dynamic
domain-needed
bogus-priv
cache-size=1000
pid-file=/run/ccd-dns.pid
EOF
dnsmasq --test --conf-file="$DNS_CONF" >/dev/null 2>&1 || die "Konfigurasi dnsmasq ditolak."
cat > "$DNS_UNIT" <<EOF
[Unit]
Description=CCD: penerus DNS untuk switch (dnsmasq di bridge ${VNET_PREFIX}*)
After=network-online.target tailscaled.service
Wants=network-online.target

[Service]
ExecStart=/usr/sbin/dnsmasq --keep-in-foreground --conf-file=$DNS_CONF
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable ccd-dns.service >/dev/null 2>&1
systemctl restart ccd-dns.service
sleep 1
systemctl is-active --quiet ccd-dns.service || die "Penerus DNS gagal jalan: journalctl -u ccd-dns -n 20"
say "Penerus DNS aktif: VM di switch memakai gateway switch sebagai DNS."

# ── 5. Route Tailscale ───────────────────────────────────────────────────────
if ! command -v tailscale >/dev/null; then
    warn "Tailscale tidak terpasang di host ini. Iklankan $POOL dari subnet router Anda secara manual."
else
    # Daftar route lama wajib terbaca: "tailscale set" mengganti seluruh daftar, jadi kalau gagal dibaca
    # route yang sudah berjalan (mis. subnet vmbr0) bisa hilang. Dalam kasus itu jangan ubah apa pun.
    if ! CUR=$(tailscale debug prefs 2>/dev/null | python3 -c 'import json,sys; print(",".join(json.load(sys.stdin)["AdvertiseRoutes"] or []))' 2>/dev/null); then
        warn "Route Tailscale saat ini tidak bisa dibaca, jadi tidak diubah."
        warn "Cek dengan 'tailscale debug prefs', lalu: tailscale set --advertise-routes=<route-lama>,$POOL"
    else
        # Route baru = route sekarang, tanpa blok lama skrip ini yang sudah dihapus, ditambah blok yang belum ada.
        # Blok lama yang tercakup blok baru (mis. diperbesar) tidak dicabut: switch di dalamnya tetap
        # terjangkau sampai route blok baru disetujui. Route itu boleh dicabut manual setelahnya.
        # Baris 1: daftar baru, baris 2: route yang ditambahkan, baris 3: route yang dicabut.
        PLAN=$(python3 - "$CUR" "$PREV_POOL" "$POOL" <<'PY'
import ipaddress, sys
split = lambda s: [x for x in s.split(",") if x]
cur, prev, pools = split(sys.argv[1]), split(sys.argv[2]), split(sys.argv[3])
nets = [ipaddress.ip_network(p) for p in pools]
covered = lambda r: any(ipaddress.ip_network(r).subnet_of(n) for n in nets)
dropped = [r for r in cur if r in prev and r not in pools and not covered(r)]
added = [p for p in pools if p not in cur]
print(",".join([r for r in cur if r not in dropped] + added))
print(",".join(added))
print(",".join(dropped))
PY
)
        NEW=$(sed -n 1p <<<"$PLAN"); ADDED=$(sed -n 2p <<<"$PLAN"); DROPPED=$(sed -n 3p <<<"$PLAN")
        if [ -z "$ADDED" ] && [ -z "$DROPPED" ]; then
            say "Tailscale sudah mengiklankan semua blok: $POOL."
        else
            say "Route Tailscale sekarang: ${CUR:-(kosong)}"
            say "Route Tailscale baru    : $NEW"
            [ -n "$ADDED" ] && say "  ditambah: $ADDED"
            [ -n "$DROPPED" ] && say "  dicabut : $DROPPED (blok yang sudah dihapus dari dashboard)"
            ANSWER=y
            if [ "$YES" != 1 ]; then read -r -p "Terapkan? [y/N] " ANSWER; fi
            if [[ "$ANSWER" =~ ^[yY] ]]; then
                tailscale set --advertise-routes="$NEW"
                if [ -n "$ADDED" ]; then
                    say "Diiklankan. Setujui route $ADDED di admin console Tailscale (Machines → $(hostname) → Edit route settings)."
                else
                    say "Route Tailscale diperbarui."
                fi
            else
                warn "Route belum diubah. Jalankan sendiri: tailscale set --advertise-routes=$NEW"
            fi
        fi
    fi
fi

say "Selesai. Blok alamat host ini: $POOL. Daftar blok di dashboard (Topology → Switch) harus sama."
