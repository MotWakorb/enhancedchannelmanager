#!/usr/bin/env bash
# Canonical policy and interpreter selection live in gate_runner.py.
# With no arguments: the backend CI selection, with coverage unchanged.
# --subset disables coverage (--no-cov): whole-tree --cov-fail-under makes
# coverage on a selected subset misleading. A subset is NOT the gate.
# Excluded: tests/e2e needs a live container on localhost:6100;
# tests/performance belongs to perf-benchmarks. See docs/testing.md.
set -euo pipefail
SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
# This is only a bootstrap. The runner refuses ambient Python for tests,
# derives worktree environments via git, and checks every current backend pin.
BOOTSTRAP="${ECM_PYTHON:-$SCRIPT_DIR/../.venv/bin/python}"
if [ -z "${ECM_PYTHON:-}" ] && [ ! -x "$BOOTSTRAP" ]; then
    BOOTSTRAP=python3
fi
exec "$BOOTSTRAP" "$SCRIPT_DIR/gate_runner.py" "$@"
