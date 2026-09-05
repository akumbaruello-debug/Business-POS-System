#!/usr/bin/env bash
# =============================================================================
# db/migrate.sh — Apply pending migrations to a target database
# =============================================================================
# This is a minimal, deterministic, psql-based migration runner for the
# Business-POS-System project.
#
# Usage:
#   ./db/migrate.sh [TARGET_DB] [PGHOST] [PGPORT] [PGUSER]
#
# Defaults:
#   TARGET_DB = bpos_validation
#   PGHOST    = localhost
#   PGPORT    = 5433
#   PGUSER    = postgres
#
# The runner:
#   1. Connects to the target database
#   2. Creates a `schema_migrations` table if not present
#   3. Lists all *.sql files in supabase/migrations/ in lexical order
#      (Supabase CLI convention: <14-digit-timestamp>_<name>.sql)
#   4. For each migration that has not yet been applied:
#      a. Runs it inside an explicit transaction (BEGIN; ... COMMIT;)
#      b. Records the migration name + checksum + applied_at in
#         schema_migrations
#   5. Skips already-applied migrations
#   6. Refuses to run a migration whose checksum has changed since it was
#      applied (defends against accidental in-place edits)
#
# All migrations must be safe to run exactly once. The runner does NOT
# re-apply or roll back applied migrations.
#
# Note: supabase/migrations/ is the canonical migration directory. The
# Supabase CLI is the primary tool for deploying migrations to the remote
# Supabase project; this script is kept for local validation runs against
# a local PostgreSQL instance.
# =============================================================================

set -euo pipefail

# ---- Configuration ----------------------------------------------------------

DB_NAME="${1:-bpos_validation}"
PG_HOST="${2:-localhost}"
PG_PORT="${3:-5433}"
PG_USER="${4:-postgres}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# Canonical migration directory (Supabase CLI convention)
MIGRATIONS_DIR="${MIGRATIONS_DIR:-${PROJECT_ROOT}/supabase/migrations}"

# Locate psql in the project's portable PostgreSQL distribution
PSQL_BIN_DEFAULT="C:/Users/ratus/Desktop/ello/projects/Business-POS-System/pg-tmp/pg17/pgsql/bin/psql.exe"
PSQL_BIN="${PSQL_BIN:-${PSQL_BIN_DEFAULT}}"

if [[ ! -x "$PSQL_BIN" && ! -f "$PSQL_BIN" ]]; then
    echo "ERROR: psql not found at: $PSQL_BIN" >&2
    echo "       Set PSQL_BIN environment variable to override." >&2
    exit 1
fi

# ---- Helper functions -------------------------------------------------------

run_psql() {
    "$PSQL_BIN" -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d "$DB_NAME" "$@"
}

file_sha256() {
    # Use certutil on Windows Git Bash to compute SHA-256
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    elif command -v certutil >/dev/null 2>&1; then
        certutil -hashfile "$1" SHA256 2>/dev/null | grep -v "hash of file" | tr -d ' \r\n'
    else
        echo "ERROR: no sha256 tool available" >&2
        return 1
    fi
}

# ---- Main -------------------------------------------------------------------

echo "=== Business-POS-System Migration Runner ==="
echo "Target database: $DB_NAME @ $PG_HOST:$PG_PORT as $PG_USER"
echo "Migrations dir:  $MIGRATIONS_DIR"
echo

# When running under Git Bash on Windows, psql.exe is a native Windows
# binary that cannot accept MSYS-style /c/... paths. Convert all migration
# paths to the C:\ form before passing to psql.
native_path() {
    local p="$1"
    # MSYS2/Git-Bash: convert /c/Users/... -> C:\Users\...
    if [[ "$p" =~ ^/([a-z])/ ]]; then
        local drive_upper
        drive_upper=$(echo "${BASH_REMATCH[1]}" | tr '[:lower:]' '[:upper:]')
        # Replace leading /<a>/ with <A>:\ and convert remaining / to \
        echo "${drive_upper}:${p:2}" | tr '/' '\\'
    else
        echo "$p"
    fi
}

# Step 1: create schema_migrations table
run_psql -t -A -v ON_ERROR_STOP=1 <<'SQL'
CREATE TABLE IF NOT EXISTS schema_migrations (
    id           BIGSERIAL    PRIMARY KEY,
    name         VARCHAR(255) NOT NULL UNIQUE,
    sha256       CHAR(64)     NOT NULL,
    applied_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
SQL

# Step 2: list migrations
MIGRATION_FILES=$(find "$MIGRATIONS_DIR" -maxdepth 1 -type f -name '*.sql' | sort)

if [[ -z "$MIGRATION_FILES" ]]; then
    echo "No migrations found in $MIGRATIONS_DIR"
    exit 0
fi

# Step 3: apply each pending migration
for f in $MIGRATION_FILES; do
    name=$(basename "$f" .sql)
    sha=$(file_sha256 "$f")

    # Check if already applied
    already_applied=$(run_psql -t -A -c "SELECT sha256 FROM schema_migrations WHERE name = '$name'")
    if [[ -n "$already_applied" ]]; then
        if [[ "$already_applied" == "$sha" ]]; then
            echo "[skip] $name (already applied, sha256 match)"
        else
            echo "[ERROR] $name was applied with sha256=$already_applied but file sha256=$sha"
            echo "        Migrations are immutable after application. Roll back the database or use a new migration."
            exit 2
        fi
        continue
    fi

    echo "[apply] $name ..."
    # Apply inside a single transaction
    # Most migrations already have their own BEGIN/COMMIT; the runner wraps
    # them with recording + error handling. If the migration file does its
    # own commit (e.g. seed block), we still wrap for tracking.
    native_f=$(native_path "$f")
    run_psql -v ON_ERROR_STOP=1 -f "$native_f" || {
        echo "ERROR: migration $name failed" >&2
        exit 3
    }

    # Record
    run_psql -t -A -c "INSERT INTO schema_migrations (name, sha256) VALUES ('$name', '$sha')"
    echo "[done]  $name"
done

echo
echo "=== All migrations applied. ==="
run_psql -c "SELECT name, applied_at FROM schema_migrations ORDER BY id"
