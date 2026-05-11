# Auto Creation

> **Audience:** Operator who wants ECM to create and update channels automatically as new streams appear in their M3U sources.
>
> **Status:** Mostly stub — most articles below are placeholders. `debugging-rules.md` is complete.

## Section purpose

Cover the Auto Creation tab end-to-end: how rules are structured, what conditions and actions are available, how rules interact with normalization, how to test a rule before enabling it, and how to debug a rule that isn't firing the way you expected.

## Intended audience

- **Operator** scaling beyond manual channel creation.
- **Operator** debugging an auto-creation result that surprised them.

End users do not read this section.

## Planned articles

| Article | Purpose |
|-|-|
| `rules-overview.md` | What an auto-creation rule is, the lifecycle (stream appears → rule evaluates → channel created/updated/skipped), how the engine schedules runs (the task engine context). |
| `conditions.md` | The condition catalogue — name match, group match, source match, etc. — with worked examples. |
| `actions.md` | The action catalogue — create channel, update channel, assign to group, attach EPG, etc. — and what state changes each one produces. |
| `test-a-rule.md` | The dry-run / preview workflow. What's safe to test against production data and what isn't. |
| `bulk-operations.md` | Running rules across an entire source, the cost of a large run, and the bulk-amplification cautions an operator should know about. |
| [`debugging-rules.md`](debugging-rules.md) | "My rule didn't fire" — the diagnostic flow using the rule analyzer: the 7 finding codes in plain language with worked examples, how to run the analyzer (API direct call, debug-bundle upload, `/analyze-rules` agent command), and when to use the analyzer vs. the per-rule dry-run preview. |
| `clone-and-reuse.md` | Duplicating a rule as a starting point, sharing a normalization group across rules. |

## Going deeper (for now)

- [`docs/api.md`](../../api.md) — the `/auto-creation` router endpoints.
- [`docs/normalization.md`](../../normalization.md) — auto-creation rules typically reference a normalization group; understand normalization before authoring complex rules.
- [`debugging-rules.md`](debugging-rules.md) — the rule analyzer: what it checks, the 7 finding codes, and how to run it.
- [`docs/auto_creation_rule_analyzer.md`](../../auto_creation_rule_analyzer.md) — the full technical reference for the rule analyzer (finding-code trigger logic, response schema, implementation notes).
- [`docs/commands/analyze-rules.md`](../../commands/analyze-rules.md) — the `/analyze-rules` agent command, for running the analyzer via an AI assistant.
