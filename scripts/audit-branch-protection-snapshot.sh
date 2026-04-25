#!/usr/bin/env bash
# audit-branch-protection-snapshot.sh — ADR-005 Audit Cadence Scope C helper.
#
# Captures and diffs branch-protection JSON for `main` and `dev`, supporting
# the Security Engineer's monthly/quarterly audit per ADR-005 §Audit Cadence
# Scope C (`allow_force_pushes` flip-event detection without org audit-log API).
#
# Background — the GitHub `/orgs/{org}/audit-log` API is org-only and returns 404
# for personal-account repos. This helper is the Phase 1 substitute: snapshot the
# protection state at each audit, diff against the prior snapshot, and surface any
# changes (especially `allow_force_pushes`, `enforce_admins`, required-checks
# shortening) for the Security Engineer to triage.
#
# Usage:
#   scripts/audit-branch-protection-snapshot.sh capture       # snapshot today's state
#   scripts/audit-branch-protection-snapshot.sh diff          # diff vs. previous snapshot
#   scripts/audit-branch-protection-snapshot.sh diff <date>   # diff vs. specific snapshot
#   scripts/audit-branch-protection-snapshot.sh ls            # list snapshots
#
# Snapshots are written under `.audit/branch-protection/<branch>/<UTC-date>.json`.
# Snapshots are committed — they are the audit trail.

set -euo pipefail

REPO="${REPO:-MotWakorb/enhancedchannelmanager}"
BRANCHES=("main" "dev")
SNAPSHOT_ROOT="${SNAPSHOT_ROOT:-.audit/branch-protection}"

require_gh() {
  command -v gh >/dev/null 2>&1 || {
    echo "ERROR: gh CLI not installed" >&2
    exit 2
  }
}

require_jq() {
  command -v jq >/dev/null 2>&1 || {
    echo "ERROR: jq not installed" >&2
    exit 2
  }
}

cmd_capture() {
  require_gh
  require_jq
  local today
  today=$(date -u +%Y-%m-%d)
  local any_written=0

  for branch in "${BRANCHES[@]}"; do
    local dir="${SNAPSHOT_ROOT}/${branch}"
    mkdir -p "${dir}"
    local out="${dir}/${today}.json"

    # Pull protection JSON; pretty-print for diff readability.
    if ! gh api "/repos/${REPO}/branches/${branch}/protection" \
        | jq --sort-keys '.' > "${out}.tmp"; then
      rm -f "${out}.tmp"
      echo "ERROR: failed to fetch protection for ${branch}" >&2
      exit 3
    fi
    mv "${out}.tmp" "${out}"
    echo "captured: ${out}"
    any_written=1
  done

  [ "${any_written}" = "1" ] || {
    echo "ERROR: no snapshots written" >&2
    exit 4
  }
}

# Echoes the second-most-recent snapshot date for a branch, or the date the
# user pinned via $2. Errors out if no prior snapshot exists.
prior_snapshot() {
  local branch="$1"
  local pinned="${2:-}"
  local dir="${SNAPSHOT_ROOT}/${branch}"

  if [ -n "${pinned}" ]; then
    if [ -f "${dir}/${pinned}.json" ]; then
      echo "${dir}/${pinned}.json"
      return 0
    else
      echo "ERROR: pinned snapshot ${dir}/${pinned}.json not found" >&2
      return 1
    fi
  fi

  # Find second-most-recent snapshot (the most recent is "today" we're diffing against).
  local prior
  prior=$(ls -1 "${dir}" 2>/dev/null | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}\.json$' | sort | tail -n 2 | head -n 1)
  if [ -z "${prior}" ]; then
    echo "ERROR: no prior snapshot for ${branch} in ${dir}" >&2
    return 1
  fi
  echo "${dir}/${prior}"
}

current_snapshot() {
  local branch="$1"
  local dir="${SNAPSHOT_ROOT}/${branch}"
  ls -1 "${dir}" 2>/dev/null | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}\.json$' | sort | tail -n 1 | awk -v dir="${dir}" '{print dir"/"$0}'
}

cmd_diff() {
  local pinned="${1:-}"
  local diff_count=0

  for branch in "${BRANCHES[@]}"; do
    local current
    current=$(current_snapshot "${branch}")
    if [ -z "${current}" ]; then
      echo "WARN: ${branch}: no current snapshot — run 'capture' first" >&2
      continue
    fi

    local prior
    if ! prior=$(prior_snapshot "${branch}" "${pinned}"); then
      echo "WARN: ${branch}: no prior snapshot to diff against" >&2
      continue
    fi

    if [ "${current}" = "${prior}" ]; then
      echo "${branch}: only one snapshot (${current}) — nothing to diff yet"
      continue
    fi

    echo "============================================================"
    echo "${branch}: diff ${prior##*/} -> ${current##*/}"
    echo "============================================================"
    if diff -u "${prior}" "${current}"; then
      echo "(no changes)"
    else
      diff_count=$((diff_count + 1))
    fi
    echo
  done

  if [ "${diff_count}" -gt 0 ]; then
    echo "AUDIT: ${diff_count} branch(es) show protection drift since the last snapshot."
    echo "Required actions per ADR-005 §Audit Cadence Scope C:"
    echo "  1. For each diff line on allow_force_pushes.enabled, find a rollback bead."
    echo "  2. For each diff line on enforce_admins.enabled, escalate to PO."
    echo "  3. For shortened required_status_checks.contexts, escalate to PO."
    exit 1
  fi

  echo "AUDIT: no drift detected."
}

cmd_ls() {
  for branch in "${BRANCHES[@]}"; do
    echo "${branch}:"
    ls -1 "${SNAPSHOT_ROOT}/${branch}" 2>/dev/null | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}\.json$' | sed 's/^/  /' || echo "  (no snapshots)"
  done
}

usage() {
  cat <<'EOF'
Usage: audit-branch-protection-snapshot.sh <command> [args]

Commands:
  capture           Snapshot current protection for main and dev (writes .audit/branch-protection/<branch>/<UTC-date>.json)
  diff              Diff most-recent snapshot against the previous one
  diff <YYYY-MM-DD> Diff most-recent snapshot against the snapshot pinned to that date
  ls                List snapshots per branch

Environment overrides:
  REPO=<owner/repo>          Default: MotWakorb/enhancedchannelmanager
  SNAPSHOT_ROOT=<path>       Default: .audit/branch-protection
EOF
}

main() {
  local cmd="${1:-}"
  case "${cmd}" in
    capture) cmd_capture ;;
    diff) shift || true; cmd_diff "$@" ;;
    ls) cmd_ls ;;
    -h|--help|help|"") usage ;;
    *)
      echo "ERROR: unknown command: ${cmd}" >&2
      usage
      exit 2
      ;;
  esac
}

main "$@"
