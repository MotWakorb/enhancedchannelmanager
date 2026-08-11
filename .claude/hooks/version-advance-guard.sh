#!/usr/bin/env bash
# PreToolUse guard (beads enhancedchannelmanager-w9irb and
# enhancedchannelmanager-uf2gh).
#
# The outer matcher is deliberately the broad `Bash` tool matcher. This script
# performs one monotonic candidate test: whitespace-separated `gh pr create`
# anywhere in the raw command text. It does not interpret quotes, heredocs,
# substitutions, tokens, or command positions, and nothing narrows a match.
# Prose and inert data may therefore run the repository-state check. That
# accepted false-positive costs friction; the opposite mistake silently removes
# the guard. Diagnostics say "possible" and never claim the text will execute.
#
# The hook input's `cwd` is the execution context Claude Code reports. Resolve
# that directory through Git rather than inferring `cd` or `git -C` from command
# text. The supported contract is to invoke `gh pr create` from the target
# checkout/session cwd. An inline `cd elsewhere && gh pr create` has not run yet,
# so its post-cd directory cannot be resolved from hook input and is unsupported.
#
# Exit 2 blocks and speaks on stderr. Exit 0 diagnostics must be JSON because
# exit-0 stderr reaches only the hook debug log.
set -uo pipefail

emit_notice() {
  printf '%s\n' "$1" >&2
  ECM_GUARD_NOTICE="$1" python3 -c '
import json, os, sys
message = os.environ.get("ECM_GUARD_NOTICE", "")
json.dump(
    {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": message,
        },
        "systemMessage": message,
    },
    sys.stdout,
)
sys.stdout.write("\n")
' 2>/dev/null || true
}

STDIN_JSON="$(cat)"
COMMAND="$(printf '%s' "$STDIN_JSON" | python3 -c '
import json, sys
try:
    value = (json.load(sys.stdin).get("tool_input") or {}).get("command", "")
except Exception:
    value = ""
print(value if isinstance(value, str) else "")
' 2>/dev/null)"

# One conservative raw-literal test. Do not add a parser or a second-stage
# exclusion: candidate presence must never be narrowed.
if ! printf '%s' "$COMMAND" | grep -Eq 'gh[[:space:]]+pr[[:space:]]+create([^[:alnum:]_]|$)'; then
  exit 0
fi

HOOK_CWD="$(printf '%s' "$STDIN_JSON" | python3 -c '
import json, sys
try:
    value = json.load(sys.stdin).get("cwd", "")
except Exception:
    value = ""
print(value if isinstance(value, str) else "")
' 2>/dev/null)"

if [[ -z "$HOOK_CWD" ]]; then
  emit_notice "version-advance-guard: WARNING, possible PR creation command detected, but hook input supplied no cwd, so the build-advance check is being skipped. CI still enforces the rule against the PR head."
  exit 0
fi

PROJECT_DIR="$(git -C "$HOOK_CWD" rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$PROJECT_DIR" ]]; then
  emit_notice "version-advance-guard: WARNING, possible PR creation command detected, but hook cwd is not inside a Git checkout, so the build-advance check is being skipped. CI still enforces the rule against the PR head."
  exit 0
fi

CHECKER="$PROJECT_DIR/scripts/check_version_advances.py"
CLASSIFIER="$PROJECT_DIR/scripts/classify_changed_paths.py"
if [[ ! -f "$CHECKER" || ! -f "$CLASSIFIER" ]]; then
  emit_notice "version-advance-guard: WARNING, possible PR creation command detected, but the repository-state checkers were not found under $PROJECT_DIR, so the build-advance check is being skipped. CI still enforces the rule against the PR head."
  exit 0
fi

BASELINE_REF="origin/dev"
git -C "$PROJECT_DIR" fetch --no-tags --quiet origin dev >/dev/null 2>&1 || true

CHANGED_PATHS_FILE="$(mktemp)" || {
  emit_notice "version-advance-guard: WARNING, possible PR creation command detected, but could not allocate changed-path transport, so the build-advance check is being skipped. CI still enforces the rule against the PR head."
  exit 0
}
trap 'rm -f -- "$CHANGED_PATHS_FILE"' EXIT
git -C "$PROJECT_DIR" diff --name-only --no-renames -z \
  "$BASELINE_REF...HEAD" >"$CHANGED_PATHS_FILE" 2>/dev/null
DIFF_STATUS=$?
if [[ $DIFF_STATUS -ne 0 ]]; then
  emit_notice "version-advance-guard: WARNING, possible PR creation command detected, but could not diff $BASELINE_REF...HEAD, so the build-advance check is being skipped. CI still enforces the rule against the PR head."
  exit 0
fi

CLASSIFIER_STDOUT="$(python3 "$CLASSIFIER" --input-format nul <"$CHANGED_PATHS_FILE" 2>/dev/null)"
CLASSIFIER_STATUS=$?
if [[ $CLASSIFIER_STATUS -ne 0 ]]; then
  emit_notice "version-advance-guard: WARNING, possible PR creation command detected, but changed-path classification exited non-zero, so the build-advance check is being skipped. CI still enforces the rule against the PR head."
  exit 0
fi

mapfile -t CODE_PATHS_CHANGED_LINES < <(printf '%s\n' "$CLASSIFIER_STDOUT" | sed -n 's/^code_paths_changed=\(.*\)$/\1/p' | tr -d '\r')
if [[ ${#CODE_PATHS_CHANGED_LINES[@]} -ne 1 ]]; then
  emit_notice "version-advance-guard: WARNING, possible PR creation command detected, but changed-path classification returned no unique code_paths_changed key, so the build-advance check is being skipped. CI still enforces the rule against the PR head."
  exit 0
fi

case "${CODE_PATHS_CHANGED_LINES[0]}" in
  false)
    emit_notice "version-advance-guard: possible PR creation command detected; SKIPPED the build-advance check because code_paths_changed=false. This change carries no build to advance. Do NOT bump the version touchpoints."
    exit 0
    ;;
  true) ;;
  *)
    emit_notice "version-advance-guard: WARNING, possible PR creation command detected, but changed-path classification returned an unrecognised code_paths_changed value, so the build-advance check is being skipped. CI still enforces the rule against the PR head."
    exit 0
    ;;
esac

CHECK_OUTPUT="$(cd "$PROJECT_DIR" && python3 "$CHECKER" --baseline-ref "$BASELINE_REF" 2>&1)"
CHECK_STATUS=$?
if [[ $CHECK_STATUS -ne 0 ]]; then
  printf '%s\n' "$CHECK_OUTPUT" >&2
  printf '\nBLOCKED by version-advance-guard: a possible PR creation command was detected, and this checkout has no advancing build number. Bump frontend/package.json (and backend/main.py + backend/routers/backup.py in lockstep) before shipping. See docs/shipping.md step 3.\n' >&2
  exit 2
fi

emit_notice "version-advance-guard: possible PR creation command detected; PASSED the build-advance check. $CHECK_OUTPUT"
exit 0
