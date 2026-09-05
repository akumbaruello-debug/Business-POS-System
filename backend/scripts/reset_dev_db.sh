#!/usr/bin/env bash
# M1 dev script: reset the dev DB to a clean schema and set the Owner password.
# Usage: ./reset_dev_db.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DB_HOST="127.0.0.1"
DB_PORT="5433"
DB_USER="postgres"
DB_NAME="pos_dev"
PG_PSQL="$PROJECT_ROOT/../pg-tmp/pg17/pgsql/bin/psql.exe"

echo "[reset_dev_db] Re-applying canonical migration to $DB_NAME..."
PGPASSWORD= "$PG_PSQL" -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
  -v ON_ERROR_STOP=1 \
  -f "$PROJECT_ROOT/supabase/migrations/20260826000001_initial_schema_baseline.sql" \
  | tail -5

echo "[reset_dev_db] Done. DB: $DB_NAME"
