#!/bin/sh
set -eu

psql --set=ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=migration_password="$RGA_MIGRATION_PASSWORD" \
  --set=runtime_password="$RGA_RUNTIME_PASSWORD" <<'SQL'
CREATE ROLE rga_migrator LOGIN PASSWORD :'migration_password' NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE ROLE rga_runtime LOGIN PASSWORD :'runtime_password' NOSUPERUSER NOCREATEDB NOCREATEROLE;
ALTER DATABASE reliable_guest_agent_dev OWNER TO rga_migrator;
GRANT CONNECT ON DATABASE reliable_guest_agent_dev TO rga_runtime;
SQL

psql --set=ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  --set=migration_password="$RGA_MIGRATION_PASSWORD" <<'SQL'
CREATE DATABASE reliable_guest_agent_test OWNER rga_migrator;
GRANT CONNECT ON DATABASE reliable_guest_agent_test TO rga_runtime;
SQL
