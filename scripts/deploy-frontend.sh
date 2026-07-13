#!/usr/bin/env bash
# Deploy the frontend SPA to the running ECM container in one atomic operation.
#
# WHY THIS SCRIPT EXISTS: `docker cp` only ADDS files, it never removes them. If
# you copy a fresh Vite build over a running container without first clearing
# /app/static/assets/, the old content-hash bundles linger and the browser can
# load a mismatched mix of new and stale CSS/JS chunks (intermittent, hard to
# debug). The clean step is easy to skip when the deploy is a hand-run sequence,
# so this wrapper bakes it in: build -> clean stale assets -> copy.
#
# Usage:
#   scripts/deploy-frontend.sh            # build + deploy to $ECM_CONTAINER (default ecm-ecm-1)
#   scripts/deploy-frontend.sh --no-build # skip the build, deploy the existing frontend/dist
#   ECM_CONTAINER=my-container scripts/deploy-frontend.sh
#
# Run from the repo root.
set -euo pipefail

CONTAINER="${ECM_CONTAINER:-ecm-ecm-1}"
BUILD=1
for arg in "$@"; do
  case "$arg" in
    --no-build) BUILD=0 ;;
    -h|--help)
      # Print only the leading header comment block (stop at the first non-# line after the shebang).
      awk 'NR==1{next} /^#/{sub(/^# ?/,""); print; next} {exit}' "$0"
      exit 0 ;;
    *)
      echo "deploy-frontend.sh: unknown argument '$arg' (try --help)" >&2
      exit 2 ;;
  esac
done

# Resolve repo root so the script works regardless of the caller's cwd.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if ! docker inspect -f '{{.State.Running}}' "$CONTAINER" >/dev/null 2>&1; then
  echo "deploy-frontend.sh: container '$CONTAINER' is not running (set ECM_CONTAINER to override)" >&2
  exit 1
fi

if [ "$BUILD" -eq 1 ]; then
  echo "==> Building frontend (npm run build)"
  ( cd frontend && npm run build )
fi

if [ ! -d frontend/dist ]; then
  echo "deploy-frontend.sh: frontend/dist not found — run without --no-build first" >&2
  exit 1
fi

echo "==> Clearing stale assets in $CONTAINER:/app/static/assets/"
docker exec "$CONTAINER" sh -c 'rm -rf /app/static/assets/*'

echo "==> Copying frontend/dist -> $CONTAINER:/app/static/"
docker cp frontend/dist/. "$CONTAINER:/app/static/"

echo "==> Frontend deployed to $CONTAINER."
