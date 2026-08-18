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

What the probe checks is **presence of the file on the default branch**, which
is the condition GitHub documents for registering the triggers. It does not
read the registered triggers back, and it does not validate the cron
expression: a file that reached `main` with its `schedule:` block deleted would
still report ARMED. Reading `repos/<owner>/<repo>/actions/workflows/security-governance-audit.yml`
instead would need an `actions: read` grant that nothing else in the audit
uses, and would still not validate the cron, so the summary line is worded for
the check that actually ran. If the cron is ever edited, confirm the next
quarterly run appears in the Actions run history rather than trusting ARMED.

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

If the credential is absent, empty, expired, or under-scoped, the audit **fails
and says which**, because those are four different owners' problems:

| Symptom | What the audit reports |
|-|-|
| Secret absent or provisioned empty | Named by name, before any read runs. A declared-but-unprovisioned secret expands to the empty string, so `set -u` never fires on it; an explicit emptiness test is what catches it. |
| `HTTP 401` from any endpoint | The credential itself is invalid: expired, revoked, or mistyped. Reissue the fine-grained PAT and replace the secret value. A fine-grained PAT expires, so this is the most likely future failure of this control. |
| `HTTP 403` from an endpoint | The credential is under-scoped. The message names the endpoint and the permission to grant. This is a provisioning defect, never a finding about the control. |
| `HTTP 404` from an endpoint | Usually the control itself is off. But GitHub answers 404 both when a control is disabled and when the credential cannot see it, so every 404 message names both possibilities. |

There is no fallback to the built-in token, because a credential that silently
degrades would turn an unreadable alert list into an apparently clean one.
Rotation is a settings change: replace the secret value, then re-run the audit
and link the green run in the audit bead.

`backend/tests/unit/test_governance_audit_behavior.py` runs each of those
outcomes against the workflow's real shell body with a stubbed `gh`, so the
table above is executable rather than asserted.

The workflow verifies the live control surfaces rather than documentation:

- `main` requires the four test/CodeQL contexts plus `Release Cut Gate`;
- `dev` requires the accepted ADR-001 image, DAST, and Trivy dependency gates
  in addition to its test/CodeQL contexts;
- administrator enforcement is on and force pushes are off on both branches;
- the remote `beads` branch exists as the current board source;
- dependency alerts are enabled, and automated security updates are both
  enabled **and not paused**. GitHub pauses them by itself on repositories
  whose Dependabot pull requests go unactioned, and a paused service still
  reports `enabled: true` while opening no security-update pull requests; and
- every secret-scanning alert has been dispositioned.

It does **not** cover `required_pull_request_reviews` on either branch, which
is the compensating control for "push access to `dev` implies access to this
credential". Treat that as asserted by this document, not checked by the audit.

The Action summary is the verified evidence for each cycle. If a live-setting
change is needed, the owner records the settings change and the successful
manual rerun URL in the audit bead. This supplements the committed protection
snapshots; it does not treat a historical snapshot as proof of current state.

Dependabot checks npm, backend Python, MCP Python, GitHub Actions, and both
Docker build roots weekly. Compatible minor/patch changes may be grouped;
major updates remain separate under ADR-001. All update PRs target `dev`, so
the accepted dependency gates run before merge.

## Bootstrap state

The bootstrap actions this section used to list as outstanding are **done**.
Leaving them listed as pending was itself a hazard: a reviewer read the stale
list and concluded the audit's first post-merge run would be red for unrelated
reasons, which is exactly how a genuinely red run gets waved through.

Verified live against the repository on 2026-08-18 with the `gh` calls the
audit itself makes. Everything in this table is **checked by the audit on
every run**. The values are a snapshot for orientation, not the authority.
Read the run's job summary and annotations for current state.

| Control | Live state on 2026-08-18 |
|-|-|
| `main` protection | `enforce_admins: true`, force pushes off, required contexts `Backend Tests`, `Frontend Tests`, `CodeQL Analysis (python)`, `CodeQL Analysis (javascript-typescript)`, `Release Cut Gate` |
| `dev` protection | `enforce_admins: true`, force pushes off, required contexts include all seven the audit requires |
| Dependabot alerts | `GET /vulnerability-alerts` → `204` (enabled) |
| Automated security fixes | `{"enabled": true, "paused": false}` |
| Open secret-scanning alerts | `0`. Alert #1 was dispositioned as `used_in_tests`; both locations are synthetic Telegram-token-shape fixtures, not an issued credential |
| `beads` branch | present |

Two facts about this repository that the audit does **not** check, and that are
asserted by this document rather than enforced:

- **The repository is public.** Actions logs are therefore world-readable. That
  is why the secret-scanning read projects only the alert number, detector name
  and timestamp: the alert objects carry a plaintext `secret` field, and
  Actions would not redact it, because a leaked third-party credential is not a
  registered Actions secret. `test_the_secret_scanning_projection_never_emits_the_plaintext_secret`
  pins the projection.
- **Neither `main` nor `dev` requires pull-request reviews.**
  `required_pull_request_reviews` is null on both branches as of 2026-08-18.
  That is the compensating control for "push access to `dev` implies access to
  `GOVERNANCE_AUDIT_TOKEN`", and it is currently absent. Tracked separately;
  the audit does not assert it.

Remaining owner action, once this change reaches `dev`: link the first green
`Security Governance Audit` run in bead `enhancedchannelmanager-04c0u.11` as
bootstrap evidence, and confirm the run's summary line reads
`Scheduled cadence: PENDING` until the file reaches `main` at the next release
cut, `ARMED` after.
