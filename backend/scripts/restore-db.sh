#!/usr/bin/env bash
# Restore database Campus Cloud Dashboard dari hasil backup-db.sh.
#
#   ./backend/scripts/restore-db.sh <ccddb|guacamoledb> <file.sql.gz>
#
# PERINGATAN: ini menimpa database tujuan sepenuhnya. Pastikan container yang memakainya
# (backend untuk ccddb, guacamole untuk guacamoledb) sudah dihentikan dulu supaya tidak ada
# koneksi aktif yang bikin restore gagal setengah jalan.
set -euo pipefail

# Password database dibaca dari backend/.env (default sama dengan docker-compose.yml).
ENV_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.env"
env_get() {
    local key="$1" def="$2" val=""
    [ -f "$ENV_FILE" ] && val="$(grep -E "^${key}=" "$ENV_FILE" | tail -n1 | cut -d= -f2-)"
    echo "${val:-$def}"
}
CCD_DB_PASS="$(env_get POSTGRES_PASSWORD ccd123)"
GUAC_DB_PASS="$(env_get GUAC_DB_PASSWORD guacamole123)"

DB="${1:-}"
FILE="${2:-}"

if [ -z "$DB" ] || [ -z "$FILE" ]; then
    echo "Pemakaian: $0 <ccddb|guacamoledb> <file.sql.gz>" >&2
    exit 1
fi
if [ ! -f "$FILE" ]; then
    echo "File tidak ditemukan: $FILE" >&2
    exit 1
fi

case "$DB" in
    ccddb)       USER=ccd;       PASS="$CCD_DB_PASS" ;;
    guacamoledb) USER=guacamole; PASS="$GUAC_DB_PASS" ;;
    *) echo "Nama database harus 'ccddb' atau 'guacamoledb'" >&2; exit 1 ;;
esac

echo "=== Restore '$DB' dari $FILE ==="
echo "Ini akan MENIMPA isi database '$DB' saat ini. Lanjutkan? (ketik 'ya' untuk lanjut)"
read -r CONFIRM
if [ "$CONFIRM" != "ya" ]; then
    echo "Dibatalkan."
    exit 1
fi

gunzip -c "$FILE" | docker exec -i -e PGPASSWORD="$PASS" ccd-postgres psql -U "$USER" -d "$DB"
echo "Selesai. Restart container terkait supaya koneksi lama tidak memakai state basi:"
echo "  cd backend && docker compose restart backend   # kalau restore ccddb"
echo "  cd backend && docker compose restart guacamole  # kalau restore guacamoledb"
