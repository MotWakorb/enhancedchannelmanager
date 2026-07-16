#!/usr/bin/env bash
# PreToolUse guard (bead enhancedchannelmanager-w9irb).
#
# Blocks a Claude Code *ship* action (`gh pr create`) when the build number in
# frontend/package.json did not advance past origin/dev — the agent-side half
# of the build-advance gate (the other half is the CI version-consistency
# job). This directly addresses THIS session's failure mode: the agent
# shipping source PRs to dev without bumping the build.
#
# Scoped to `gh pr create` ONLY — deliberately NOT `gh pr merge`. The guard
# checks the LOCAL WORKING TREE's version, which is correct for `create`
# (the working tree IS the branch being PR'd) but unsound for `merge`: at
# merge time the working tree may be parked on any branch (often `dev`), so a
# `gh pr merge <#>` for a PR whose branch DID bump would false-block. Merge is
# already covered server-side: the CI required-check enforces advancement
# against the PR head before the merge button works.
#
# Matcher is the tool NAME "Bash" (see .claude/settings.json), so this runs on
# EVERY Bash command. It must be cheap and inert for non-ship commands: it
# reads the command off stdin, exits 0 immediately unless the command is a
# real `gh pr create` INVOCATION, and only then runs the shared checker. A
# command that merely MENTIONS the string (e.g. `echo 'gh pr create'`, a grep,
# or a doc edit) must NOT trigger — the matcher is anchored to a command
# boundary (start of line or after ; && || | ( {) so the text appearing as an
# argument to some other command does not fire the guard.
#
# Block mechanism (per Claude Code hooks docs): exit code 2 blocks the tool
# call and feeds stderr back to Claude. Exit 0 lets the command proceed; any
# stderr (e.g. the soft CHANGELOG warning) is shown but does not block.
#
# Worktree-aware root resolution (bead enhancedchannelmanager-yxtuz, follow-up
# to PR #666): CLAUDE_PROJECT_DIR is always the MAIN checkout, even when the
# `gh pr create` being validated runs from a git worktree on a different,
# further-advanced branch (e.g. `cd <worktree> && gh pr create ...`). Checking
# CLAUDE_PROJECT_DIR's version state in that case validates the WRONG
# checkout and can false-block a legitimately-advanced worktree branch (this
# happened in PR #666 — the engineer had to bypass via `gh api`; CI's
# server-side version-consistency job re-enforces the same rule against the
# actual PR head, so a false block here is a friction bug, not a safety gap).
#
# Fixed below by resolving the checkout root to validate from the command
# itself (a leading `cd <path> &&` or a `git -C <path>` in the `gh pr create`
# command), with an `ECM_VERSION_GUARD_ROOT` env override for cases the
# command text can't express. Falls back to the CLAUDE_PROJECT_DIR/script-
# location default when neither applies, so the plain main-checkout path
# (no cd, no -C, no override) is byte-for-byte the pre-fix behavior.
set -uo pipefail

# Project root: Claude Code exports CLAUDE_PROJECT_DIR; fall back to the repo
# this script lives in (two levels up from .claude/hooks/).
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-}"
if [[ -z "$PROJECT_DIR" ]]; then
  PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi

# Read the tool call JSON from stdin and pull out the Bash command string.
STDIN_JSON="$(cat)"
COMMAND="$(printf '%s' "$STDIN_JSON" | python3 -c \
  'import json,sys
try:
    d=json.load(sys.stdin)
except Exception:
    print(""); sys.exit(0)
print((d.get("tool_input") or {}).get("command","") or "")' 2>/dev/null)"

# Not a real `gh pr create` invocation? Do nothing — never interfere with
# normal git/bash, and never fire on a command that merely mentions the string
# (echo/grep/doc-edit). The leading (^|[;&|({]) anchor requires `gh` to sit at
# a command boundary — start of the line, or right after a ; && || | ( or {
# separator — so `echo 'gh pr create'` (gh preceded by a quote/word) does NOT
# match, while `git commit && gh pr create --base dev` does.
if ! printf '%s' "$COMMAND" | grep -Eq '(^|[;&|({])[[:space:]]*gh[[:space:]]+pr[[:space:]]+create([[:space:]]|$)'; then
  exit 0
fi

# Release-cut / hotfix `gh pr create` targets main — the build-advance rule
# does not apply there (the version legitimately drops its -BUILD suffix).
# Skip if the create command explicitly targets main. (The checker also
# exempts no-suffix versions, so this is belt-and-suspenders.)
if printf '%s' "$COMMAND" | grep -Eq -- '--base[[:space:]=]+main'; then
  exit 0
fi

# ─── Resolve the checkout the ship command actually runs from ─────────────
# Two ways to point the guard at a non-default checkout, checked in order:
#   1. ECM_VERSION_GUARD_ROOT env override — wins unconditionally when set.
#   2. Parse COMMAND for a leading `cd <path> &&` or a `git -C <path>`.
# Either way, the candidate is only adopted if it resolves to a real
# directory that looks like an ECM checkout (has scripts/check_version_advances.py).
# Otherwise PROJECT_DIR stays at its CLAUDE_PROJECT_DIR/script-location
# default from above — this keeps the normal main-checkout path unchanged.
RESOLVED_DIR=""
if [[ -n "${ECM_VERSION_GUARD_ROOT:-}" ]]; then
  RESOLVED_DIR="$ECM_VERSION_GUARD_ROOT"
else
  RESOLVED_DIR="$(printf '%s' "$COMMAND" | python3 -c '
import re, sys
cmd = sys.stdin.read()
# `cd <path> &&` (or start-of-command / after ;&|({) — quoted or bare.
m = re.search(r"(?:^|[;&|({])\s*cd\s+([\"\x27]?)([^\"\x27;&|]+?)\1\s*(?:&&|;|$)", cmd)
if m:
    print(m.group(2).strip())
    sys.exit(0)
# `git -C <path>` anywhere in the command.
m = re.search(r"\bgit\s+-C\s+([\"\x27]?)([^\"\x27\s]+)\1", cmd)
if m:
    print(m.group(2).strip())
    sys.exit(0)
' 2>/dev/null)"
fi

if [[ -n "$RESOLVED_DIR" ]]; then
  # Relative paths resolve against the default PROJECT_DIR (matches the
  # shell semantics of a bare `cd <relative>` run from that directory).
  if [[ "$RESOLVED_DIR" != /* ]]; then
    RESOLVED_DIR="$PROJECT_DIR/$RESOLVED_DIR"
  fi
  RESOLVED_DIR="$(cd "$RESOLVED_DIR" 2>/dev/null && pwd)"
  if [[ -n "$RESOLVED_DIR" && -f "$RESOLVED_DIR/scripts/check_version_advances.py" ]]; then
    PROJECT_DIR="$RESOLVED_DIR"
  fi
fi

CHECKER="$PROJECT_DIR/scripts/check_version_advances.py"
if [[ ! -f "$CHECKER" ]]; then
  # Checker missing — do not block a ship on a broken guard; warn only.
  echo "version-advance-guard: $CHECKER not found; skipping build-advance check." >&2
  exit 0
fi

# Best-effort refresh of the baseline so we compare against the latest dev.
# Ignore failures (offline, no remote) — the checker soft-passes if the
# baseline is unavailable.
git -C "$PROJECT_DIR" fetch --no-tags --quiet origin dev >/dev/null 2>&1 || true

OUTPUT="$(python3 "$CHECKER" --baseline-ref origin/dev --repo-root "$PROJECT_DIR" 2>&1)"
RC=$?

# Relay the checker's diagnostic (includes the next-build suggestion on fail
# and the soft CHANGELOG warning) to stderr so Claude sees it either way.
printf '%s\n' "$OUTPUT" >&2

if [[ "$RC" -ne 0 ]]; then
  echo "" >&2
  echo "BLOCKED by version-advance-guard: bump the build number in frontend/package.json (and backend/main.py + backend/routers/backup.py in lockstep) before shipping. See docs/shipping.md step 3." >&2
  exit 2
fi

exit 0
