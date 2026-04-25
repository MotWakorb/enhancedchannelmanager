# Branch Protection Audit Snapshots

Per ADR-005 §Audit Cadence Scope C, the Security Engineer captures the
GitHub branch-protection JSON for `main` and `dev` at each audit cycle
(monthly for the first quarter post-Phase 3, then quarterly).

Snapshots are produced by `scripts/audit-branch-protection-snapshot.sh capture`
and live under `<branch>/<UTC-date>.json`. They are committed — the snapshot
history IS the audit trail.

## What gets reviewed

Each audit diffs the newest snapshot against the previous one and surfaces:

- `allow_force_pushes.enabled` flips (must be `false` at audit time per ADR-004 §Interaction with ADR-005 item 3 — see also `docs/runbooks/v0.16.0-rollback.md` Option 2 TOCTOU note).
- `enforce_admins.enabled` flips (must be `true` per ADR-005 §Decision item 4 — disabling admin bypass).
- `required_status_checks.contexts` shortening (must include `Backend Tests`, `Frontend Tests`, `CodeQL Analysis (python)`, `CodeQL Analysis (javascript-typescript)`).

A flip is "authorized" only if a corresponding rollback bead exists in `bd` (e.g.,
`bd-vgm4l`); unauthorized flips become P1 incident beads.

## How to run

```bash
# At the start of each audit cycle:
scripts/audit-branch-protection-snapshot.sh capture
scripts/audit-branch-protection-snapshot.sh diff
```

Exit code is non-zero on drift, so the helper can be wired into CI in Phase 2.

## Phase 2 upgrade path

If/when the repo migrates to a GitHub org, the live audit-log API
(`/orgs/{org}/audit-log?phrase=action:protected_branch.update`) provides
every flip event with timestamps and actors and supersedes this snapshot
strategy. The snapshot-diff approach is the Phase 1 substitute because the
audit-log API is org-only and `MotWakorb/enhancedchannelmanager` is hosted
under a personal account (returns 404 — verified 2026-04-24).
