# Troubleshooting

This section covers common failure modes, how to read ECM's logs and banners, and what to gather before asking for help — roughly the order the articles below follow, from general triage to escalation and recovery.

## Section purpose

Be the first place an operator turns when something is wrong. Cover the common failure modes (Dispatcharr connection lost, Channel Pipeline not firing, EPG mismatched, restore reported conflicts), explain how to read ECM's logs, and tell an operator what information to gather before asking for help on Discord or filing an issue.

This section is **referenced** by every other section. Every "going deeper" or "things look wrong" pointer eventually lands here.

## Articles

| Article | Purpose |
|-|-|
| [Common Issues](common-issues.md) | Top failure modes by category (connection, Channel Pipeline, normalization, EPG, restore), with the first-three-things-to-check for each. |
| [Read the Logs](read-the-logs.md) | Where ECM logs to, what severity levels mean, how to grep effectively, the `[SAFE_REGEX]` and other tagged messages an operator might encounter. Cross-references the `logs` skill. |
| [UI Banners and Warnings](ui-banners-and-warnings.md) | Catalogue of the warning banners ECM may surface and what each one means. |
| [Gather Support Information](gather-support-information.md) | What to capture before asking for help: version ([`docs/versioning.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/versioning.md) for context), recent journal entries, relevant log slice, Dispatcharr version, browser if it's a UI bug. Focused on making the support loop short. |
| [Escalation Paths](escalation-paths.md) | Where to ask for help: Discord, GitHub issues, and (for self-hosted operators with on-call) the runbooks tree. |
| [Recovery Patterns](recovery-patterns.md) | "I made a change I want to undo": the journal, undo/redo, restore from backup, when to use which. |

## Going deeper

- [`docs/runbooks/`](https://github.com/MotWakorb/enhancedchannelmanager/tree/main/docs/runbooks): incident-grade runbooks. Operator-adjacent but written for the on-call responder under pressure rather than the configuring operator. Use these when a troubleshooting situation has escalated into "something is actively broken at scale."
- [`docs/versioning.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/versioning.md): understanding which version you're on, which matters for support requests.
- The `logs` skill in `.claude/`: automated log analysis when manual triage is slow.
