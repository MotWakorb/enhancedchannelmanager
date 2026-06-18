#!/usr/bin/env bash
# Thin wrapper: applies the edge-case supplement against the running throwaway
# Dispatcharr via its API (not raw SQL — the API is the stable contract; the
# internal DB schema is not). Delegates to edge_supplement.py.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${SCRIPT_DIR}/edge_supplement.py" "$@"
