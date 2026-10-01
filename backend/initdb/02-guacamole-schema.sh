#!/bin/bash
# Script ini dijalankan saat postgres pertama init.
# Generate skema Guacamole dari image resmi dan import ke guacamoledb.
# Karena kita tidak bisa jalankan Docker dari dalam Docker,
# skema sudah di-embed di bawah ini (dari guacamole/guacamole:1.5.5 --initdb).

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "guacamoledb" << 'EOSQL'

-- ============================================================
-- Guacamole 1.5.x PostgreSQL Schema
-- Source: docker run --rm guacamole/guacamole:1.5.5 /opt/guacamole/bin/initdb.sh --postgresql
-- ============================================================

-- ENUM types wajib ada — query JDBC auth Guacamole meng-cast literal ke tipe-tipe ini
-- (mis. "AND permission = $3::guacamole_system_permission_type"). Tanpa CREATE TYPE ini,
-- webapp gagal dengan 500 "Unexpected internal error" begitu ada query yang memakainya
-- (baru ketahuan saat percobaan login/connection sync, bukan saat initdb).
CREATE TYPE guacamole_entity_type AS ENUM('USER', 'USER_GROUP');
CREATE TYPE guacamole_connection_group_type AS ENUM('ORGANIZATIONAL', 'BALANCING');
CREATE TYPE guacamole_object_permission_type AS ENUM('READ', 'UPDATE', 'DELETE', 'ADMINISTER');
CREATE TYPE guacamole_system_permission_type AS ENUM(
  'CREATE_CONNECTION', 'CREATE_CONNECTION_GROUP', 'CREATE_SHARING_PROFILE',
  'CREATE_USER', 'CREATE_USER_GROUP', 'ADMINISTER'
);
CREATE TYPE guacamole_proxy_encryption_method AS ENUM('NONE', 'SSL');

CREATE TABLE IF NOT EXISTS guacamole_connection_group (
  connection_group_id   serial       NOT NULL,
  parent_id             integer,
  connection_group_name varchar(128) NOT NULL,
  type                  guacamole_connection_group_type NOT NULL DEFAULT 'ORGANIZATIONAL',
  max_connections       integer,
  max_connections_per_user integer,
  enable_session_affinity boolean NOT NULL DEFAULT FALSE,
  PRIMARY KEY (connection_group_id),
  CONSTRAINT connection_group_name_parent UNIQUE (connection_group_name, parent_id)
);

CREATE TABLE IF NOT EXISTS guacamole_connection (
  connection_id       serial       NOT NULL,
  connection_name     varchar(128) NOT NULL,
  parent_id           integer,
  protocol            varchar(32)  NOT NULL,
  proxy_port          integer,
  proxy_hostname      varchar(512),
  proxy_encryption_method guacamole_proxy_encryption_method,
  max_connections          integer,
  max_connections_per_user integer,
  connection_weight        integer,
  failover_only            boolean NOT NULL DEFAULT FALSE,
  PRIMARY KEY (connection_id),
  CONSTRAINT connection_name_parent UNIQUE (connection_name, parent_id)
);

CREATE TABLE IF NOT EXISTS guacamole_entity (
  entity_id   serial       NOT NULL,
  name        varchar(128) NOT NULL,
  type        guacamole_entity_type NOT NULL,
  PRIMARY KEY (entity_id),
  CONSTRAINT guacamole_entity_name_scope UNIQUE (type, name)
);

CREATE TABLE IF NOT EXISTS guacamole_user (
  user_id         serial    NOT NULL,
  entity_id       integer   NOT NULL,
  password_hash   bytea     NOT NULL,
  password_salt   bytea,
  password_date   timestamptz NOT NULL,
  disabled        boolean   NOT NULL DEFAULT FALSE,
  expired         boolean   NOT NULL DEFAULT FALSE,
  access_window_start    time,
  access_window_end      time,
  valid_from      date,
  valid_until     date,
  timezone        varchar(64),
  full_name       varchar(256),
  email_address   varchar(256),
  organization    varchar(256),
  organizational_role varchar(256),
  PRIMARY KEY (user_id),
  CONSTRAINT guacamole_user_single_entity UNIQUE (entity_id)
);

CREATE TABLE IF NOT EXISTS guacamole_user_group (
  user_group_id  serial  NOT NULL,
  entity_id      integer NOT NULL,
  disabled       boolean NOT NULL DEFAULT FALSE,
  PRIMARY KEY (user_group_id),
  CONSTRAINT guacamole_user_group_single_entity UNIQUE (entity_id)
);

CREATE TABLE IF NOT EXISTS guacamole_user_group_member (
  user_group_id    integer NOT NULL,
  member_entity_id integer NOT NULL,
  PRIMARY KEY (user_group_id, member_entity_id)
);

CREATE TABLE IF NOT EXISTS guacamole_sharing_profile (
  sharing_profile_id    serial       NOT NULL,
  sharing_profile_name  varchar(128) NOT NULL,
  primary_connection_id integer      NOT NULL,
  PRIMARY KEY (sharing_profile_id),
  CONSTRAINT sharing_profile_name_primary UNIQUE (sharing_profile_name, primary_connection_id)
);

CREATE TABLE IF NOT EXISTS guacamole_connection_parameter (
  connection_id   integer      NOT NULL,
  parameter_name  varchar(128) NOT NULL,
  parameter_value varchar(4096),
  PRIMARY KEY (connection_id, parameter_name)
);

CREATE TABLE IF NOT EXISTS guacamole_sharing_profile_parameter (
  sharing_profile_id integer      NOT NULL,
  parameter_name     varchar(128) NOT NULL,
  parameter_value    varchar(4096) NOT NULL,
  PRIMARY KEY (sharing_profile_id, parameter_name)
);

CREATE TABLE IF NOT EXISTS guacamole_connection_permission (
  entity_id     integer NOT NULL,
  connection_id integer NOT NULL,
  permission    guacamole_object_permission_type NOT NULL,
  PRIMARY KEY (entity_id, connection_id, permission)
);

CREATE TABLE IF NOT EXISTS guacamole_connection_group_permission (
  entity_id            integer NOT NULL,
  connection_group_id  integer NOT NULL,
  permission           guacamole_object_permission_type NOT NULL,
  PRIMARY KEY (entity_id, connection_group_id, permission)
);

CREATE TABLE IF NOT EXISTS guacamole_sharing_profile_permission (
  entity_id          integer NOT NULL,
  sharing_profile_id integer NOT NULL,
  permission         guacamole_object_permission_type NOT NULL,
  PRIMARY KEY (entity_id, sharing_profile_id, permission)
);

CREATE TABLE IF NOT EXISTS guacamole_system_permission (
  entity_id  integer     NOT NULL,
  permission guacamole_system_permission_type NOT NULL,
  PRIMARY KEY (entity_id, permission)
);

CREATE TABLE IF NOT EXISTS guacamole_user_permission (
  entity_id        integer     NOT NULL,
  affected_user_id integer     NOT NULL,
  permission       guacamole_object_permission_type NOT NULL,
  PRIMARY KEY (entity_id, affected_user_id, permission)
);

CREATE TABLE IF NOT EXISTS guacamole_user_group_permission (
  entity_id              integer     NOT NULL,
  affected_user_group_id integer     NOT NULL,
  permission             guacamole_object_permission_type NOT NULL,
  PRIMARY KEY (entity_id, affected_user_group_id, permission)
);

CREATE TABLE IF NOT EXISTS guacamole_connection_history (
  history_id           serial      NOT NULL,
  user_id              integer,
  username             varchar(128) NOT NULL,
  remote_host          varchar(256),
  connection_id        integer,
  connection_name      varchar(128) NOT NULL,
  sharing_profile_id   integer,
  sharing_profile_name varchar(128),
  start_date           timestamptz NOT NULL,
  end_date             timestamptz,
  PRIMARY KEY (history_id)
);

CREATE TABLE IF NOT EXISTS guacamole_user_history (
  history_id  serial       NOT NULL,
  user_id     integer,
  username    varchar(128) NOT NULL,
  remote_host varchar(256),
  start_date  timestamptz  NOT NULL,
  end_date    timestamptz,
  PRIMARY KEY (history_id)
);

CREATE TABLE IF NOT EXISTS guacamole_user_attribute (
  user_id         integer      NOT NULL,
  attribute_name  varchar(128) NOT NULL,
  attribute_value varchar(4096),
  PRIMARY KEY (user_id, attribute_name)
);

CREATE TABLE IF NOT EXISTS guacamole_user_group_attribute (
  user_group_id   integer      NOT NULL,
  attribute_name  varchar(128) NOT NULL,
  attribute_value varchar(4096),
  PRIMARY KEY (user_group_id, attribute_name)
);

CREATE TABLE IF NOT EXISTS guacamole_connection_attribute (
  connection_id   integer      NOT NULL,
  attribute_name  varchar(128) NOT NULL,
  attribute_value varchar(4096),
  PRIMARY KEY (connection_id, attribute_name)
);

CREATE TABLE IF NOT EXISTS guacamole_connection_group_attribute (
  connection_group_id integer      NOT NULL,
  attribute_name      varchar(128) NOT NULL,
  attribute_value     varchar(4096),
  PRIMARY KEY (connection_group_id, attribute_name)
);

CREATE TABLE IF NOT EXISTS guacamole_sharing_profile_attribute (
  sharing_profile_id integer      NOT NULL,
  attribute_name     varchar(128) NOT NULL,
  attribute_value    varchar(4096),
  PRIMARY KEY (sharing_profile_id, attribute_name)
);

-- Foreign keys
ALTER TABLE guacamole_connection DROP CONSTRAINT IF EXISTS guacamole_connection_ibfk_1;
ALTER TABLE guacamole_connection ADD CONSTRAINT guacamole_connection_ibfk_1
  FOREIGN KEY (parent_id) REFERENCES guacamole_connection_group (connection_group_id) ON DELETE CASCADE;

ALTER TABLE guacamole_connection_group DROP CONSTRAINT IF EXISTS guacamole_connection_group_ibfk_1;
ALTER TABLE guacamole_connection_group ADD CONSTRAINT guacamole_connection_group_ibfk_1
  FOREIGN KEY (parent_id) REFERENCES guacamole_connection_group (connection_group_id) ON DELETE CASCADE;

ALTER TABLE guacamole_user DROP CONSTRAINT IF EXISTS guacamole_user_ibfk_1;
ALTER TABLE guacamole_user ADD CONSTRAINT guacamole_user_ibfk_1
  FOREIGN KEY (entity_id) REFERENCES guacamole_entity (entity_id) ON DELETE CASCADE;

ALTER TABLE guacamole_user_group DROP CONSTRAINT IF EXISTS guacamole_user_group_ibfk_1;
ALTER TABLE guacamole_user_group ADD CONSTRAINT guacamole_user_group_ibfk_1
  FOREIGN KEY (entity_id) REFERENCES guacamole_entity (entity_id) ON DELETE CASCADE;

-- Grant ke user guacamole
GRANT ALL ON ALL TABLES IN SCHEMA public TO guacamole;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO guacamole;

-- Default admin: guacadmin / guacadmin
-- Password hash: SHA-256("guacadmin" + salt)
-- Menggunakan nilai dari dokumentasi resmi Guacamole 1.5
INSERT INTO guacamole_entity (name, type) VALUES ('guacadmin', 'USER') ON CONFLICT DO NOTHING;

INSERT INTO guacamole_user (
  entity_id, password_hash, password_salt, password_date,
  full_name, email_address, organization
)
SELECT
  entity_id,
  decode('CA458A7D494E3BE824F5E1E175A1556C0F8EEF2C2D7DF3633BEC4A29C4411960', 'hex'),
  decode('FE24ADC5E11E2B25288D1704ABE67A79E342ECC26064CE69C5B3177795A82264', 'hex'),
  NOW(),
  'Guacamole Admin',
  '',
  'HyperPanel'
FROM guacamole_entity WHERE name = 'guacadmin' AND type = 'USER'
ON CONFLICT DO NOTHING;

INSERT INTO guacamole_system_permission (entity_id, permission)
SELECT entity_id, permission::guacamole_system_permission_type FROM guacamole_entity,
  (VALUES ('CREATE_CONNECTION'), ('CREATE_CONNECTION_GROUP'), ('CREATE_SHARING_PROFILE'),
          ('CREATE_USER'), ('CREATE_USER_GROUP'), ('ADMINISTER')) AS perms(permission)
WHERE name = 'guacadmin' AND type = 'USER'
ON CONFLICT DO NOTHING;

INSERT INTO guacamole_user_permission (entity_id, affected_user_id, permission)
SELECT e.entity_id, u.user_id, p.permission::guacamole_object_permission_type
FROM guacamole_entity e
JOIN guacamole_user u ON u.entity_id = e.entity_id
CROSS JOIN (VALUES ('READ'), ('UPDATE'), ('ADMINISTER')) AS p(permission)
WHERE e.name = 'guacadmin' AND e.type = 'USER'
ON CONFLICT DO NOTHING;

EOSQL

echo "[initdb] Guacamole schema initialized in guacamoledb"
