#!/usr/bin/env bash
# =============================================================================
# capture-snapshot.sh — capture a production-shaped Dispatcharr seed snapshot
# =============================================================================
#
# Captures a Dispatcharr PostgreSQL snapshot to tests/dbas-test-env/seed/.
# The snapshot is the SOURCE of production-shaped seed data for the DBAS
# round-trip test: real stream cardinality/duplication (so the .14 4-tier
# matcher is actually exercised) and real logo volume (so the .15 streaming/
# OOM path is exercised). This is NOT hand-crafted fixtures.
#
# TWO capture sources are supported:
#
#   1. From a LIVE/operator Dispatcharr (PREFERRED — real production shape):
#        DBAS_SNAPSHOT_PGHOST=<operator-db-host> \
#        DBAS_SNAPSHOT_PGPORT=5432 \
#        DBAS_SNAPSHOT_PGUSER=dispatch \
#        DBAS_SNAPSHOT_PGPASSWORD=<pw> \
#        DBAS_SNAPSHOT_PGDATABASE=dispatcharr \
#        ./capture-snapshot.sh live
#
#      No PII concerns in this data (per QA test-data policy), so the snapshot
#      is captured directly — no anonymization step. If the operator's DB is
#      only reachable via the running container, run pg_dump inside that
#      container and copy the file out instead (see README).
#
#   2. From the THROWAWAY test stack (after you've populated it by hand or by
#      running M3U/EPG refreshes against real upstreams):
#        ./capture-snapshot.sh testenv
#
# Output: tests/dbas-test-env/seed/dispatcharr-seed.sql.gz
#         tests/dbas-test-env/seed/SNAPSHOT_MANIFEST.txt  (row counts + meta)
#
# The manifest stamps the Dispatcharr version READ from the running instance
# (GET /api/core/version/, via DBAS_SNAPSHOT_BASE_URL / DBAS_TEST_BASE_URL, else
# http://localhost:${DBAS_TEST_HTTP_PORT:-9591}). This directory tracks
# `dispatcharr:latest`, so there is nothing to pin; if the endpoint cannot be
# read the manifest says "unknown" rather than stamping a guess.
#
# NOTE: the .sql.gz snapshot is intentionally NOT committed (see seed/.gitignore)
# — it is large and machine-local. The MANIFEST and the loader scripts ARE the
# committed, reviewable artifacts. Regenerate the snapshot on demand.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SEED_DIR="${SCRIPT_DIR}/seed"
COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.dbas-test.yml"
PROJECT="${DBAS_TEST_PROJECT:-dbas-testenv}"
OUT="${SEED_DIR}/dispatcharr-seed.sql.gz"
MANIFEST="${SEED_DIR}/SNAPSHOT_MANIFEST.txt"

mkdir -p "${SEED_DIR}"
MODE="${1:-testenv}"

dump_via_psql_env() {
  # pg_dump straight from a reachable Postgres using DBAS_SNAPSHOT_* env vars.
  PGPASSWORD="${DBAS_SNAPSHOT_PGPASSWORD:-}" pg_dump \
    --host="${DBAS_SNAPSHOT_PGHOST:?set DBAS_SNAPSHOT_PGHOST}" \
    --port="${DBAS_SNAPSHOT_PGPORT:-5432}" \
    --username="${DBAS_SNAPSHOT_PGUSER:-dispatch}" \
    --dbname="${DBAS_SNAPSHOT_PGDATABASE:-dispatcharr}" \
    --no-owner --no-privileges --clean --if-exists
}

dump_via_testenv_container() {
  # pg_dump executed INSIDE the throwaway db container — no host pg client needed.
  docker compose -p "${PROJECT}" -f "${COMPOSE_FILE}" exec -T dispatcharr-db \
    pg_dump -U dispatch -d dispatcharr --no-owner --no-privileges --clean --if-exists
}

# Read the Dispatcharr version from the RUNNING instance. This directory tracks
# `dispatcharr:latest` (PO decision, bead enhancedchannelmanager-xvuk1), so there
# is no version to hardcode — a stamped guess would mislabel every snapshot.
# Prints the version, or the literal string "unknown (could not read <url>)" so
# that "did not run" stays distinguishable from a real value. Never invents one.
read_dispatcharr_version() {
  local url="${DBAS_SNAPSHOT_BASE_URL:-${DBAS_TEST_BASE_URL:-http://localhost:${DBAS_TEST_HTTP_PORT:-9591}}}"
  url="${url%/}/api/core/version/"
  local body version
  if ! body="$(curl -fsS --max-time 10 "${url}" 2>/dev/null)"; then
    echo "unknown (could not read ${url})"
    return 0
  fi
  version="$(printf '%s' "${body}" \
    | python3 -c 'import json,sys
try:
    p = json.load(sys.stdin)
except Exception:
    sys.exit(1)
v = p.get("version") if isinstance(p, dict) else (p if isinstance(p, str) else None)
if not isinstance(v, str) or not v.strip():
    sys.exit(1)
print(v.strip())' 2>/dev/null)" || {
    echo "unknown (no version field at ${url})"
    return 0
  }
  printf '%s\n' "${version}"
}

echo "[capture] mode=${MODE} -> ${OUT}"
case "${MODE}" in
  live)     dump_via_psql_env       | gzip -9 > "${OUT}" ;;
  testenv)  dump_via_testenv_container | gzip -9 > "${OUT}" ;;
  *) echo "usage: $0 [live|testenv]" >&2; exit 2 ;;
esac

# Build a reviewable manifest (row counts of the round-trip-relevant tables).
# Table names below are best-effort; adjust to the running schema on first run.
DISPATCHARR_VERSION_READ="$(read_dispatcharr_version)"
echo "[capture] dispatcharr version (read from the instance): ${DISPATCHARR_VERSION_READ}"
{
  echo "# Dispatcharr DBAS seed snapshot manifest"
  echo "# captured: $(date -u +%Y-%m-%dT%H:%M:%SZ)  mode=${MODE}"
  echo "# dispatcharr version (READ from the running instance, not a pin): ${DISPATCHARR_VERSION_READ}"
  echo "# file: $(basename "${OUT}")  size: $(du -h "${OUT}" | cut -f1)"
  echo "#"
  echo "# Row counts (the categories the round-trip must reproduce):"
} > "${MANIFEST}"

if [ "${MODE}" = "testenv" ]; then
  docker compose -p "${PROJECT}" -f "${COMPOSE_FILE}" exec -T dispatcharr-db \
    psql -U dispatch -d dispatcharr -At -c "
      SELECT relname || E'\t' || n_live_tup
      FROM pg_stat_user_tables
      ORDER BY n_live_tup DESC;" >> "${MANIFEST}" 2>/dev/null \
    || echo "# (could not collect row counts — db not running?)" >> "${MANIFEST}"
fi

echo "[capture] wrote ${OUT}"
echo "[capture] wrote ${MANIFEST}"
echo "[capture] DONE. Load it with ./load-snapshot.sh"
