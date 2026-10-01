#!/bin/bash
# Buat user dan database Guacamole di PostgreSQL yang sama.
# Dijalankan otomatis oleh image postgres saat volume database pertama kali dibuat.
# Password diambil dari GUAC_DB_PASSWORD (diisi setup.sh di backend/.env).
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
     -v guac_pass="${GUAC_DB_PASSWORD:-guacamole123}" <<'EOSQL'
CREATE USER guacamole WITH PASSWORD :'guac_pass';
CREATE DATABASE guacamoledb OWNER guacamole;
GRANT ALL PRIVILEGES ON DATABASE guacamoledb TO guacamole;
\connect guacamoledb
GRANT ALL ON SCHEMA public TO guacamole;
EOSQL
