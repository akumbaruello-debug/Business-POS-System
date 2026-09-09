#!/usr/bin/env bash
# M1 dev script: reset the dev DB to a clean schema and set the Owner password.
# Usage: ./reset_dev_db.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
if command -v cygpath >/dev/null 2>&1; then
  REPO_ROOT_WIN="$(cygpath -w "$REPO_ROOT")"
  SCRIPT_DIR_WIN="$(cygpath -w "$SCRIPT_DIR")"
else
  REPO_ROOT_WIN="$REPO_ROOT"
  SCRIPT_DIR_WIN="$SCRIPT_DIR"
fi
DB_HOST="127.0.0.1"
DB_PORT="5433"
DB_USER="postgres"
DB_NAME="pos_dev"
PG_PSQL="$REPO_ROOT_WIN/pg-tmp/pg17/pgsql/bin/psql.exe"

echo "[reset_dev_db] Dropping and recreating public schema in $DB_NAME..."
PGPASSWORD= "$PG_PSQL" -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
  -v ON_ERROR_STOP=1 \
  -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public; GRANT ALL ON SCHEMA public TO $DB_USER;"

echo "[reset_dev_db] Re-applying canonical migration to $DB_NAME..."
PGPASSWORD= "$PG_PSQL" -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
  -v ON_ERROR_STOP=1 \
  -f "$REPO_ROOT_WIN/supabase/migrations/20260826000001_initial_schema_baseline.sql" \
  | tail -5

echo "[reset_dev_db] Setting owner password..."
python "$SCRIPT_DIR_WIN/seed_owner_password.py" --username owner --password 'OwnerPass123!' || true

echo "[reset_dev_db] Done. DB: $DB_NAME"
