#!/usr/bin/env bash
#
# THE backend gate. One invocation, so there cannot be two.
# Bead enhancedchannelmanager-c9lb9.
#
# WHY THIS EXISTS
# ---------------
# Two different backend gate invocations were circulating, differing by 72
# collected tests:
#
#   documented in backend/CLAUDE.md   pytest tests/                  11304 collected
#   actually run by CI                pytest --ignore=... -m ...     11232 collected
#
# Nothing was failing, so this was an instrument gap rather than a live
# defect — but it is the false-green class. An agent could report "backend
# gate green" having never executed 72 tests, and the figure looked
# authoritative because every handover repeated it. Flags spelled out in prose
# get copied and mutated; a script does not.
#
# WHICH ONE WON, AND WHY
# ----------------------
# This script mirrors CI, because CI is what decides the PR. A local gate that
# runs a *different* set than the required check is not a gate, whichever
# direction the difference runs. The 72 extra tests the prose command
# collected are not extra coverage:
#
#   tests/e2e/          (10 files) needs a live ECM container on localhost:6100.
#                       Without one it self-skips; with one it exercises
#                       whatever that container happens to be running. A check
#                       whose result depends on unrelated local state cannot
#                       gate anything. Deferred to bead
#                       enhancedchannelmanager-2lw25.
#   tests/performance/  (2 files)  seeds 250k rows; runs in the dedicated
#                       perf-benchmarks workflow (bd-skqln.10).
#
# `backend/tests/unit/test_backend_gate_contract.py` asserts this script's
# invocation still matches .github/workflows/test.yml flag for flag, so the
# two cannot drift apart again.
#
# USAGE
#   scripts/backend-gate.sh                 # the gate
#   scripts/backend-gate.sh -k some_filter  # the gate, narrowed (still the gate's flags)
#   scripts/backend-gate.sh --subset tests/unit/test_foo.py
#                                           # NOT the gate: coverage off, see below
#
# ENVIRONMENT
#   ECM_PYTHON   interpreter override. Otherwise the repo venv is used, and if
#                this is a git worktree (which has no .venv of its own) the
#                main checkout's venv is derived from git-common-dir.
#
set -uo pipefail

# --------------------------------------------------------------------------
# Repo root, derived from this script's own location so it is correct in a
# worktree, from any cwd, and through a symlink.
# --------------------------------------------------------------------------
SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
REPO_ROOT="$(cd "$(dirname "$SCRIPT_PATH")/.." && pwd)"

die() { printf '%s\n' "$*" >&2; exit 2; }

[ -d "$REPO_ROOT/backend" ] || die "backend-gate: no backend/ under $REPO_ROOT"

# --------------------------------------------------------------------------
# Interpreter selection.
#
# "The Environment Is Part of the Gate": a run under the ambient interpreter
# resolves an older `cryptography` and *self-skips* 9 TLS tests instead of
# failing, so the run still reports success. That gap is invisible unless you
# happen to compare skip counts. Selecting the interpreter here rather than
# leaving it to tribal knowledge in a doc is the durable fix.
# --------------------------------------------------------------------------
PY=""
PY_SOURCE=""
if [ -n "${ECM_PYTHON:-}" ]; then
    PY="$ECM_PYTHON"
    PY_SOURCE="ECM_PYTHON override"
elif [ -x "$REPO_ROOT/.venv/bin/python" ]; then
    PY="$REPO_ROOT/.venv/bin/python"
    PY_SOURCE="repo venv"
else
    # Worktrees do not get their own .venv. Derive the main checkout from
    # git-common-dir (…/main/.git) rather than guessing at a path.
    COMMON_DIR="$(git -C "$REPO_ROOT" rev-parse --git-common-dir 2>/dev/null || true)"
    if [ -n "$COMMON_DIR" ]; then
        case "$COMMON_DIR" in
            /*) ;;
            *) COMMON_DIR="$REPO_ROOT/$COMMON_DIR" ;;
        esac
        MAIN_ROOT="$(cd "$COMMON_DIR/.." && pwd)"
        if [ -x "$MAIN_ROOT/.venv/bin/python" ]; then
            PY="$MAIN_ROOT/.venv/bin/python"
            PY_SOURCE="main checkout venv (this tree is a worktree)"
        fi
    fi
fi

[ -n "$PY" ] || die "backend-gate: no project interpreter found.
  Looked for: \$ECM_PYTHON, $REPO_ROOT/.venv/bin/python, and the main
  checkout's .venv if this is a worktree.
  Do NOT fall back to ambient python: it resolves an older cryptography and
  silently self-skips 9 TLS tests, so the gate reports success having not run
  them. Set ECM_PYTHON to the project interpreter instead."

"$PY" -c 'import pytest' 2>/dev/null \
    || die "backend-gate: $PY cannot import pytest ($PY_SOURCE)."

# --------------------------------------------------------------------------
# Mode
# --------------------------------------------------------------------------
SUBSET=0
if [ "${1:-}" = "--subset" ]; then
    SUBSET=1
    shift
fi

cd "$REPO_ROOT/backend" || die "backend-gate: cannot cd to $REPO_ROOT/backend"

if [ "$SUBSET" -eq 1 ]; then
    cat >&2 <<'EOF'
=== SUBSET RUN — THIS IS NOT THE GATE ===
Coverage is disabled (--no-cov) for this run.

  Why: pytest.ini sets --cov=. with --cov-fail-under=56, and coverage is
  measured over the whole tree, not over what you selected. So ANY subset run
  fails on coverage even when every test in it passes — a bare --collect-only
  reports "Required test coverage of 56% not reached. Total coverage: 18.39%".
  That failure is an artifact of the selection, not a defect, and reading it
  as real has sent agents off to "fix" a healthy tree.

Do not report a subset run as the backend gate. Run scripts/backend-gate.sh
with no arguments for that.
EOF
    exec "$PY" -m pytest --no-cov --tb=short --no-header -p no:warnings "$@"
fi

cat >&2 <<EOF
=== BACKEND GATE ===
interpreter : $PY ($PY_SOURCE)
tree        : $REPO_ROOT
excluded    : tests/e2e         needs a live container on :6100 (bead 2lw25)
              tests/performance runs in perf-benchmarks (bd-skqln.10)
deselected  : the 2 tests marked 'slow' — named in docs/testing.md
              § "What the backend gate excludes, and why"
EOF

# ---- THE INVOCATION. Mirrors .github/workflows/test.yml "Run pytest". ----
# Kept in sync by backend/tests/unit/test_backend_gate_contract.py.
exec "$PY" -m pytest \
    --ignore=tests/e2e \
    --ignore=tests/performance \
    -m "not slow" --tb=short --no-header -p no:warnings \
    "$@"
