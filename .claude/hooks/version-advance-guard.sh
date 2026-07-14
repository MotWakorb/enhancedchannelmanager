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
