#!/usr/bin/env bash
# worktree-bootstrap.sh — set up frontend tooling in a spawned worktree.
#
# Problem: the main checkout's frontend/node_modules is owned by root:root (0755)
# because it was installed inside the container as root. A plain symlink to that
# directory lets Vite/Vitest find the packages but fails with EACCES when Vite
# tries to write to node_modules/.vite-temp (config-load temp files) or
# node_modules/.vite (dep-optimizer cache).
#
# Fix: materialise a real, writable node_modules directory in the worktree that
# owns .vite-temp/ and .vite/ locally (writable by the agent user) while
# symlinking every package and .bin from the main checkout.
#
# Usage: bash scripts/worktree-bootstrap.sh   (idempotent — safe to re-run)
set -euo pipefail

WORKTREE_ROOT="$(git rev-parse --show-toplevel)"
NM_TARGET="$WORKTREE_ROOT/frontend/node_modules"

# Find the main checkout via 'git worktree list' (first entry = main worktree)
MAIN_ROOT="$(git worktree list --porcelain | awk 'NR==1 && /^worktree / {print $2}')"
MAIN_NM="$MAIN_ROOT/frontend/node_modules"

if [ ! -d "$MAIN_NM" ]; then
  echo "ERROR: main checkout node_modules not found at $MAIN_NM" >&2
  echo "Run 'npm install' in $MAIN_ROOT/frontend first." >&2
  exit 1
fi

# ── Already bootstrapped check ─────────────────────────────────────────────
# A fully-bootstrapped worktree has a real directory (not a plain symlink)
# with writable .vite-temp and .vite subdirectories.
if [ -d "$NM_TARGET" ] && [ ! -L "$NM_TARGET" ]; then
  # It's a real directory — check that .vite-temp is writable
  if [ -d "$NM_TARGET/.vite-temp" ] && [ -w "$NM_TARGET/.vite-temp" ]; then
    echo "already bootstrapped (writable node_modules at $NM_TARGET)"
    exit 0
  fi
fi

# ── Migrate: remove an old plain symlink if present ────────────────────────
if [ -L "$NM_TARGET" ]; then
  echo "Removing old node_modules symlink (replacing with writable directory)..."
  rm "$NM_TARGET"
fi

# ── Create parent dir if needed (sparse checkout may omit frontend/) ────────
mkdir -p "$WORKTREE_ROOT/frontend"

# ── Create the real local node_modules directory ───────────────────────────
mkdir -p "$NM_TARGET"

# ── Create writable local copies of Vite's temp/cache dirs ─────────────────
# .vite-temp: used by Vite to write per-run .mjs timestamp files while
#             loading vite.config.ts and vitest.config.ts (loadConfigFromBundledFile).
#             Must be writable by the running user; the root-owned copy in the
#             main checkout causes EACCES even after mkdir succeeds (the dir
#             already exists, so mkdir is a no-op, but writeFile then fails).
# .vite:      dep-optimizer cache (node_modules/.vite/deps). Also root-owned
#             in the main checkout; Vite rebuilds it on the first run.
mkdir -p "$NM_TARGET/.vite-temp"
mkdir -p "$NM_TARGET/.vite"

# ── Symlink everything else from the main checkout ──────────────────────────
# This includes all packages (.bin, .package-lock.json, and every package dir/link).
# We skip .vite-temp and .vite because we just created writable local copies.
SKIPPED=0
LINKED=0
while IFS= read -r -d '' entry; do
  name="$(basename "$entry")"
  # Skip the two dirs we own locally
  if [ "$name" = ".vite-temp" ] || [ "$name" = ".vite" ]; then
    SKIPPED=$((SKIPPED + 1))
    continue
  fi
  target="$NM_TARGET/$name"
  if [ ! -e "$target" ] && [ ! -L "$target" ]; then
    ln -s "$entry" "$target"
    LINKED=$((LINKED + 1))
  fi
done < <(find "$MAIN_NM" -maxdepth 1 -mindepth 1 -print0)

echo "node_modules ready: $LINKED symlinks created, $SKIPPED dirs kept local (writable)"
echo "  real dir: $NM_TARGET"
echo "  writable: $NM_TARGET/.vite-temp  $NM_TARGET/.vite"

# ── Emit the PATH hint ──────────────────────────────────────────────────────
NODE_BIN="$NM_TARGET/.bin"
echo ""
echo "Add node and frontend tools to PATH:"
echo "  export PATH=\"\$HOME/.local/share/fnm/node-versions/v24.13.0/installation/bin:$NODE_BIN:\$PATH\""
echo ""
echo "Then invoke frontend tooling via the .bin wrappers directly:"
echo "  $NODE_BIN/tsc --noEmit"
echo "  $NODE_BIN/vitest run"
echo "  $NODE_BIN/eslint frontend/src --max-warnings 0"
echo "  $NODE_BIN/vite build"
