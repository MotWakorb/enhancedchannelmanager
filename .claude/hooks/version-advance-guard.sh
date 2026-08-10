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

# One baseline for BOTH halves of this guard: the documentation-only
# classification directly below and the build-advance comparison at the end.
# A single variable so the two halves can never disagree about what the change
# is being measured against.
#
# Base-branch scoping is deliberately kept in step with CI. The CI advance gate
# runs only when `github.base_ref == 'dev'` (.github/workflows/test.yml), and
# this hook reaches this line only for a ship that is not `--base main` (the
# exemption above). If either side ever grows a third base branch, both must
# change together or the hook and CI start disagreeing again.
BASELINE_REF="origin/dev"

# Best-effort refresh of the baseline so we compare against the latest dev.
# Ignore failures (offline, no remote) — the checker soft-passes if the
# baseline is unavailable.
git -C "$PROJECT_DIR" fetch --no-tags --quiet origin dev >/dev/null 2>&1 || true

# ─── Documentation-only exemption (bead enhancedchannelmanager-uf2gh) ─────
# Third in a chain, and the chain is the useful part of the provenance:
#   w9irb  created this guard, and already specified "SCOPE using the
#          EXISTING docs-only classification". Only the CI half of that bead
#          ever implemented the scoping, which is why this block was missing
#          rather than deliberately omitted.
#   yxtuz  narrowed the guard's blast radius once before (worktree-blind root
#          resolution), and recorded the missing shell self-test that this
#          change finally adds.
#   uf2gh  finishes w9irb's scoping on the agent side.
# CI does not apply the build-advance rule to a documentation-only PR:
# .github/workflows/test.yml gates its "Verify build number advances past
# base" step on `needs.detect.outputs.docs_only != 'true'`, because, in that
# workflow's own words, documentation-only PRs "carry no build to advance".
# This hook is the agent-side half of that same gate, so without this block it
# is strictly stricter than the rule it mirrors: it forces a docs change to
# bump three source version touchpoints it has no business editing, and burns
# a build number a concurrent branch may already hold.
#
# ONE definition of "documentation-only" lives in the repo, and it is
# scripts/classify_changed_paths.py, the very script the `detect` job calls.
# Nothing is reimplemented here; this block only decides which file list to
# hand it and how to read the verdict.
#
# FAIL OPEN when classification cannot be performed, exit 0 without blocking.
# This inverts CI's idiom on purpose, and transplanting CI's `!= 'true'`
# comparison here would be a bug. CI fails open TOWARD CODE because its risk is
# a hollow green required check. This hook's risk runs the other way: it cannot
# make anything merge, only stop an agent from opening a PR, and its own header
# above records the standard as "a false block here is a friction bug, not a
# safety gap" precisely because CI re-enforces the same rule server-side against
# the real PR head. So a broken classifier must not wedge shipping, exactly as a
# missing checker already does not (the `exit 0` a few lines above).
#
# The residual cost of that choice is stated plainly for reviewers: while the
# classifier is broken, a genuine non-advancing build on a CODE change stops
# being caught agent-side and is caught only by CI. Every such path therefore
# prints a loud WARNING naming the reason, so a degraded guard is visible rather
# than silent.
#
# The verdict is read BY KEY, not by comparing the classifier's whole stdout.
# The classifier emits GitHub Actions `key=value` lines and is expected to grow
# more of them over time (bead enhancedchannelmanager-t4d5w added
# `docs_site_affected` alongside `docs_only`). An earlier revision of this block
# compared the entire stdout against the literals "docs_only=true" and
# "docs_only=false"; the moment a second line appeared, every invocation matched
# neither, took the fail-open path, and the guard would have warned forever
# while enforcing nothing. Because it fails open, that break would have been
# silent-by-degradation rather than a visible block, which is the worse kind.
# So: extract the value for `docs_only` and ignore every other key.
#
# Three outcomes remain. `true` skips the check, `false` runs it, and a missing
# or unrecognised VALUE FOR THAT KEY is "could not classify" and fails open. An
# unrecognised value is never quietly read as `false`, which keeps a garbled
# classifier from manufacturing a block. What is no longer fatal is the mere
# presence of other keys.
#
# The empty changed-file set is deliberately NOT special-cased here. It is piped
# to the classifier like any other set, and the shared definition answers it
# (`classify([])` returns not-docs-only), so there is exactly one place in the
# repo that decides what counts as documentation.
#
# The path list is a three-dot diff, which reports what the branch changed since
# it left the baseline. That is the set GitHub's pull-request files API returns
# and therefore exactly what the `detect` job classifies. A two-dot diff would
# wrongly fold in commits made on dev since the branch point, and `git status`
# would wrongly fold in uncommitted files that the PR will not contain.
# `core.quotePath=false` keeps a non-ASCII documentation filename readable
# rather than octal-escaped. Git still quotes any path containing a newline
# regardless of that setting, so a crafted filename cannot forge extra entries
# in the list. In any case the manipulation only runs one way: an injected path
# can add a code verdict, never remove one, because docs_only requires EVERY
# path to be documentation.
CLASSIFIER="$PROJECT_DIR/scripts/classify_changed_paths.py"
CLASSIFIER_STDOUT=""
DOCS_ONLY_VALUE=""
CLASSIFY_PROBLEM=""

if [[ ! -f "$CLASSIFIER" ]]; then
  CLASSIFY_PROBLEM="classifier not found at $CLASSIFIER"
elif ! CHANGED_PATHS="$(git -C "$PROJECT_DIR" -c core.quotePath=false \
  diff --name-only "$BASELINE_REF...HEAD" 2>/dev/null)"; then
  CLASSIFY_PROBLEM="could not diff ${BASELINE_REF}...HEAD in $PROJECT_DIR"
# The path list is piped to the classifier on stdin, never expanded by the
# shell, so a path is data here and can never become command syntax.
elif ! CLASSIFIER_STDOUT="$(printf '%s\n' "$CHANGED_PATHS" |
  python3 "$CLASSIFIER" 2>/dev/null)"; then
  CLASSIFY_PROBLEM="classifier exited non-zero"
else
  # Read the `docs_only` value BY KEY out of the key=value lines, ignoring any
  # other keys. `tr -d '\r'` tolerates CRLF stdout on a Windows checkout. If
  # the key is absent the value is empty; if it somehow appears twice, the
  # value is multi-line and matches neither literal below, so a contradictory
  # classifier lands in "could not classify" rather than picking a winner.
  DOCS_ONLY_VALUE="$(printf '%s\n' "$CLASSIFIER_STDOUT" | tr -d '\r' |
    sed -n 's/^docs_only=\(.*\)$/\1/p')"
  case "$DOCS_ONLY_VALUE" in
    true | false) ;;
    "") CLASSIFY_PROBLEM="classifier emitted no docs_only key" ;;
    *) CLASSIFY_PROBLEM="classifier returned an unrecognised docs_only value" ;;
  esac
fi

if [[ -n "$CLASSIFY_PROBLEM" ]]; then
  echo "version-advance-guard: WARNING, ${CLASSIFY_PROBLEM}. Cannot tell whether this change is documentation-only, so the build-advance check is being skipped rather than risk a false block. CI still enforces the same rule server-side against the PR head. If you are shipping a CODE change, verify the build number advanced by hand: python scripts/check_version_advances.py" >&2
  exit 0
fi

if [[ "$DOCS_ONLY_VALUE" == "true" ]]; then
  # Announce the skip. A silent exit 0 is indistinguishable from a hook that
  # never ran, which is how a guard quietly rots.
  echo "version-advance-guard: SKIPPED the build-advance check. Every path changed against ${BASELINE_REF} is documentation or beads data (scripts/classify_changed_paths.py returned docs_only=true), and CI exempts documentation-only PRs from this gate for the same reason: there is no build to advance. Do NOT bump the version touchpoints for this change." >&2
  exit 0
fi

OUTPUT="$(python3 "$CHECKER" --baseline-ref "$BASELINE_REF" --repo-root "$PROJECT_DIR" 2>&1)"
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
