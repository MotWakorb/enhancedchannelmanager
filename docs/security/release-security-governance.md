# Release Security Governance

**Owner:** Security Engineer persona. **Backup owner:** Project Engineer persona.
The owner runs and dispositions the `Security Governance Audit` workflow on
the first day of January, April, July, and October. A failed scheduled run is
a release blocker until its evidence is explained or the control is restored.

## When the quarterly cadence actually arms

The audit currently runs on **every `dev` push**, invoked as a reusable
workflow from `.github/workflows/test.yml`. That is the only path executing it
today, and it is not a required status check.

The quarterly `schedule:` and the `workflow_dispatch:` button in
`.github/workflows/security-governance-audit.yml` are **inert until that file
reaches the default branch (`main`)**. GitHub registers those two triggers only
for workflow files present on the default branch, so
`gh api repos/<owner>/<repo>/actions/workflows/security-governance-audit.yml`
answers `404` while the file lives only on `dev`. Nothing in the workflow needs
editing: both triggers arm by themselves the moment the file lands on `main` at
the next release cut.

This is checked rather than assumed. Every audit run probes the default branch
for its own file and records **Scheduled cadence: ARMED** or **PENDING** in the
job summary, and emits a `::warning::` annotation while it is PENDING. Read
that line before treating a quarter as covered by the schedule: while it says
PENDING, quarterly coverage depends on the owner dispatching the audit from a
branch that carries the file, or on the `dev`-push runs, not on the cron.

## The elevated audit credential

Four of the controls below are repository *administration* surfaces, and the
built-in `GITHUB_TOKEN` cannot read any of them: the workflow `permissions:`
key has no `administration` scope, and `security-events` covers code scanning
only. GitHub documents that secret-scanning alerts cannot be read with that
permission either. The audit therefore reads those endpoints with
**`GOVERNANCE_AUDIT_TOKEN`**, a repository secret holding a read-only
fine-grained personal access token scoped to this repository alone, granting
`Administration` (read) and `Secret scanning alerts` (read).

`.github/workflows/test.yml` hands that secret over through an explicit
one-entry `secrets:` mapping. `secrets: inherit` is deliberately not used: the
audit receives exactly this one named secret, so adding an unrelated repository
secret later cannot widen what the call passes.

The remaining check, reading the `beads` branch ref, needs only
`contents: read` and stays on the built-in token.

If the secret is absent, empty, expired, or under-scoped, the audit **fails and
says which**. A missing secret is named in the error; a `403` from an endpoint
names that endpoint and the permission to grant; a `404` is reported as the
control itself being off. There is no fallback to the built-in token, because a
credential that silently degrades would turn an unreadable alert list into an
apparently clean one. Rotation is a settings change: replace the secret value,
then re-run the audit and link the green run in the audit bead.

The workflow verifies the live control surfaces rather than documentation:

- `main` requires the four test/CodeQL contexts plus `Release Cut Gate`;
- `dev` requires the accepted ADR-001 image, DAST, and Trivy dependency gates
  in addition to its test/CodeQL contexts;
- administrator enforcement is on and force pushes are off on both branches;
- the remote `beads` branch exists as the current board source;
- dependency alerts and automated security updates are enabled; and
- every secret-scanning alert has been dispositioned.

The Action summary is the verified evidence for each cycle. If a live-setting
change is needed, the owner records the settings change and the successful
manual rerun URL in the audit bead. This supplements the committed protection
snapshots; it does not treat a historical snapshot as proof of current state.

Dependabot checks npm, backend Python, MCP Python, GitHub Actions, and both
Docker build roots weekly. Compatible minor/patch changes may be grouped;
major updates remain separate under ADR-001. All update PRs target `dev`, so
the accepted dependency gates run before merge.

## Bootstrap actions after this change lands

These are repository settings and are intentionally performed separately by
the PO/SRE after reviewing this PR:

1. Publish the current board export on the configured `beads` sync branch and
   keep `bd sync` in the board-change landing workflow. The release gate fails
   closed while that remote branch is absent.
2. Add `Release Cut Gate` to `main` required status checks.
3. Add `Build Docker Image (AMD64)`, `DAST Security Scan`, and
   `Container Security Scan (Trivy)` to `dev` required status checks.
4. Enable Dependabot alerts (automated security fixes are already enabled).
5. Resolve secret-scanning alert #1 as `used_in_tests`: both locations are
   synthetic Telegram-token-shape fixtures, not an issued credential. Preserve
   that rationale in the alert comment.
6. Manually run `Security Governance Audit`; link the green run in bead
   `enhancedchannelmanager-04c0u.11` as bootstrap evidence.
