#!/usr/bin/env bash
# =============================================================================
# load-snapshot.sh — load the production-shaped seed into the throwaway stack
# =============================================================================
#
# Restores tests/dbas-test-env/seed/dispatcharr-seed.sql.gz into the throwaway
# Dispatcharr Postgres, then (optionally) applies the edge-case supplement so
# the round-trip test exercises the hard cases too.
#
# Order matters: the test stack MUST be up (db healthy) before loading, and
# the web/celery services should be RESTARTED after a raw DB load so they pick
# up the restored schema/data.
#
#   docker compose -p dbas-testenv -f docker-compose.dbas-test.yml up -d
#   ./load-snapshot.sh                # loads snapshot + edge supplement
#   ./load-snapshot.sh --no-edge      # snapshot only
#
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SEED_DIR="${SCRIPT_DIR}/seed"
COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.dbas-test.yml"
PROJECT="${DBAS_TEST_PROJECT:-dbas-testenv}"
SNAP="${SEED_DIR}/dispatcharr-seed.sql.gz"

WITH_EDGE=1
[ "${1:-}" = "--no-edge" ] && WITH_EDGE=0

if [ ! -f "${SNAP}" ]; then
  echo "[load] ERROR: ${SNAP} not found. Run ./capture-snapshot.sh first." >&2
  exit 1
fi

echo "[load] waiting for db to be healthy..."
for i in $(seq 1 30); do
  if docker compose -p "${PROJECT}" -f "${COMPOSE_FILE}" exec -T dispatcharr-db \
       pg_isready -U dispatch -d dispatcharr >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

echo "[load] restoring ${SNAP} ..."
gunzip -c "${SNAP}" | docker compose -p "${PROJECT}" -f "${COMPOSE_FILE}" \
  exec -T dispatcharr-db psql -U dispatch -d dispatcharr -v ON_ERROR_STOP=0

if [ "${WITH_EDGE}" = "1" ]; then
  echo "[load] applying edge-case supplement ..."
  "${SCRIPT_DIR}/seed/edge-supplement.sh"
fi

echo "[load] restarting web + celery to pick up restored data ..."
docker compose -p "${PROJECT}" -f "${COMPOSE_FILE}" restart dispatcharr-web dispatcharr-celery

echo "[load] DONE."
