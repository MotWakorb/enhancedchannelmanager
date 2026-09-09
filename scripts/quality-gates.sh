#!/usr/bin/env bash
# Local quality checks; SKIP_E2E=1 is explicitly partial verification.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/backend-gate.sh" --quality "$@"
