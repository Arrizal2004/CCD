#!/usr/bin/env bash
# Backup otomatis kedua database Campus Cloud Dashboard (data aplikasi + Guacamole).
#
#   ./backend/scripts/backup-db.sh
#
# Dipasang sebagai cron job harian oleh setup.sh — aman juga dijalankan manual kapan saja.
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

BACKUP_DIR="${CCD_BACKUP_DIR:-/var/backups/campus-cloud-dashboard}"
RETENTION_DAYS="${CCD_BACKUP_RETENTION_DAYS:-14}"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"

log()  { echo "[backup-db] $*"; }
err()  { echo "[backup-db] $*" >&2; }

mkdir -p "$BACKUP_DIR"

dump() {
    local user="$1" pass="$2" db="$3" label="$4"
    local out="$BACKUP_DIR/${label}-${TIMESTAMP}.sql.gz"
    if docker exec -e PGPASSWORD="$pass" ccd-postgres pg_dump -U "$user" "$db" 2>/dev/null | gzip > "$out"; then
        log "OK: $out ($(du -h "$out" 2>/dev/null | cut -f1))"
    else
        err "GAGAL backup '$label' — cek container ccd-postgres jalan & kredensial masih cocok"
        rm -f "$out"
        return 1
    fi
}

status=0
dump ccd       "$CCD_DB_PASS"  ccddb       ccddb       || status=1
dump guacamole "$GUAC_DB_PASS" guacamoledb guacamoledb || status=1

log "Membersihkan backup lebih tua dari ${RETENTION_DAYS} hari di $BACKUP_DIR..."
find "$BACKUP_DIR" -name '*.sql.gz' -mtime +"$RETENTION_DAYS" -print -delete

log "Selesai."
exit $status
