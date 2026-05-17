#!/usr/bin/env bash
# worktree-bootstrap.sh — symlink main checkout's node_modules into a spawned worktree.
# Fixes the missing frontend/node_modules problem in .claude/worktrees/agent-* worktrees.
# Usage: bash scripts/worktree-bootstrap.sh   (idempotent — safe to re-run)
set -euo pipefail

WORKTREE_ROOT="$(git rev-parse --show-toplevel)"
NM_TARGET="$WORKTREE_ROOT/frontend/node_modules"

# Check if already bootstrapped
if [ -L "$NM_TARGET" ]; then
  echo "already bootstrapped (symlink exists at $NM_TARGET)"
  exit 0
fi
if [ -d "$NM_TARGET" ] && [ "$(ls -A "$NM_TARGET" 2>/dev/null | wc -l)" -gt 0 ]; then
  echo "already bootstrapped (node_modules present and non-empty at $NM_TARGET)"
  exit 0
fi

# Find the main checkout via 'git worktree list' (first entry = main worktree)
MAIN_ROOT="$(git worktree list --porcelain | awk 'NR==1 && /^worktree / {print $2}')"
MAIN_NM="$MAIN_ROOT/frontend/node_modules"

if [ ! -d "$MAIN_NM" ]; then
  echo "ERROR: main checkout node_modules not found at $MAIN_NM" >&2
  echo "Run 'npm install' in $MAIN_ROOT/frontend first." >&2
  exit 1
fi

# Create parent dir if needed (sparse checkout may omit frontend/)
mkdir -p "$WORKTREE_ROOT/frontend"

# Symlink
ln -s "$MAIN_NM" "$NM_TARGET"
echo "Symlinked: $NM_TARGET -> $MAIN_NM"

# Emit the PATH hint
NODE_BIN="$MAIN_NM/.bin"
echo ""
echo "Add node to PATH (adjust fnm/nvm path for your install):"
echo "  export PATH=\"\$(fnm env --use-on-cd 2>/dev/null | grep PATH | cut -d= -f2- | tr -d '\"'):$NODE_BIN:\$PATH\""
echo ""
echo "Or for a quick one-liner:"
echo "  export PATH=\"$NODE_BIN:\$PATH\"  # gives access to vitest, eslint, tsc, vite"
echo "  # Also ensure 'node' is on PATH via fnm/nvm before running tools."
