# Runbooks

Operational playbooks for on-call and incident response. Read during incidents, written for the person who is stressed, tired, and possibly not the one who built the system.

## Conventions

- Every runbook follows `template.md`: **Alert/Trigger → Symptoms → Diagnosis → Resolution → Post-incident**.
- Commands are exact and copy-pasteable. No "run the usual deploy" — spell it out.
- Decision points are `if / then`, not paragraphs of context.
- Every alert that pages a human must have a runbook listed here. An alert without a runbook is a fire alarm without an exit map.
- Update the runbook during the post-incident review — a runbook that survived an incident without edits is a runbook that wasn't followed, or wasn't needed.

## Index

| Runbook | Scope | Last Exercised |
|-|-|-|
| [v0.16.0 Hard Rollback](./v0.16.0-rollback.md) | Post-release rollback of a tagged version across git, GitHub Release, and GHCR | 2026-04-20 (real incident) |

## Adding a runbook

1. Copy `template.md` to `<alert-or-scenario-slug>.md`.
2. Fill every section. If a section does not apply, write `N/A — <reason>`; do not delete the heading.
3. Add a row to the index above.
4. Open a PR. Runbooks are reviewed by the Technical Writer (clarity) and the SRE (operational accuracy). Both approvals required.
