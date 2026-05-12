# Troubleshooting

> **Audience:** Operator with a problem. ECM is doing something they didn't expect, or not doing something they did expect.
>
> **Status:** Stub — articles below are placeholders.

## Section purpose

Be the first place an operator turns when something is wrong. Cover the common failure modes (Dispatcharr connection lost, auto-creation not firing, EPG mismatched, restore reported conflicts), explain how to read ECM's logs, and tell an operator what information to gather before asking for help on Discord or filing an issue.

This section is **referenced** by every other section — every "going deeper" or "things look wrong" pointer eventually lands here.

## Intended audience

- **Operator** debugging a current problem.
- **Operator** preparing a support request.

End users do not read this section directly, but their operator does on their behalf.

## Planned articles

| Article | Purpose |
|-|-|
| `common-issues.md` | Top failure modes by category — connection, auto-creation, normalization, EPG, restore — with the first-three-things-to-check for each. |
| `read-the-logs.md` | Where ECM logs to, what severity levels mean, how to grep effectively, the `[SAFE_REGEX]` and other tagged messages an operator might encounter. Cross-references the `logs` skill. |
| `ui-banners-and-warnings.md` | Catalogue of the warning banners ECM may surface and what each one means. |
| `gather-support-information.md` | What to capture before asking for help: version (`docs/versioning.md` for context), recent journal entries, relevant log slice, Dispatcharr version, browser if it's a UI bug. Focused on making the support loop short. |
| `escalation-paths.md` | Where to ask for help: Discord, GitHub issues, and (for self-hosted operators with on-call) the runbooks tree. |
| `recovery-patterns.md` | "I made a change I want to undo" — the journal, undo/redo, restore from backup, when to use which. |

## Going deeper (for now)

- [`docs/runbooks/`](../../runbooks/) — incident-grade runbooks. Operator-adjacent but written for the on-call responder under pressure rather than the configuring operator. Use these when a troubleshooting situation has escalated into "something is actively broken at scale."
- [`docs/versioning.md`](../../versioning.md) — understanding which version you're on, which matters for support requests.
- The `logs` skill in `.claude/` — automated log analysis when manual triage is slow.
