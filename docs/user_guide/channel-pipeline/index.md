# Channel Pipeline

> **Audience:** Operator who wants ECM to create and update channels automatically as new streams appear in their M3U sources.
>
> **Status:** Mostly stub — most articles below are placeholders. `debugging-rules.md` is complete.

## Section purpose

Cover the Channel Pipeline tab end-to-end: how rules are structured, what conditions and actions are available, how rules interact with normalization, how to test a rule before enabling it, and how to debug a rule that isn't firing the way you expected.

## Intended audience

- **Operator** scaling beyond manual channel creation.
- **Operator** debugging a Channel Pipeline result that surprised them.

End users do not read this section.

## Planned articles

| Article | Purpose |
|-|-|
| `rules-overview.md` | What a Channel Pipeline rule is, the lifecycle (stream appears → rule evaluates → channel created/updated/skipped), how the engine schedules runs (the task engine context). |
| `conditions.md` | The condition catalogue — name match, group match, source match, etc. — with worked examples. |
| `actions.md` | The action catalogue — create channel, update channel, assign to group, attach EPG, etc. — and what state changes each one produces. |
| [`sort-vs-numbering.md`](sort-vs-numbering.md) | Why "Channel Sort" doesn't renumber channels by itself, the `channel_number: auto` gotcha that silently skips rule-level renumbering, and why `sort_group` is the action built for "keep my channels alphabetically numbered." |
| `test-a-rule.md` | The dry-run / preview workflow. What's safe to test against production data and what isn't. |
| `bulk-operations.md` | Running rules across an entire source, the cost of a large run, and the bulk-amplification cautions an operator should know about. |
| [`runaway-safety-cap.md`](runaway-safety-cap.md) | The per-run channel cap (the GH #473 safety valve): why a run gets "capped", that the Channel Pipeline is idempotent so you can just re-run, and how to view/raise/disable the cap (and its sibling log-entries cap) from **Settings → Channel Pipeline** (admin-only). |
| [`debugging-rules.md`](debugging-rules.md) | "My rule didn't fire" — the diagnostic flow using the rule analyzer: the 7 finding codes in plain language with worked examples, how to run the analyzer (API direct call, debug-bundle upload, `/analyze-rules` agent command), and when to use the analyzer vs. the per-rule dry-run preview. |
| [`fuzzy-locals-matching.md`](fuzzy-locals-matching.md) | Scored fuzzy matching for OTA / Local channels: when to use it, how to preview before writing, the callsign safety gate, and the dry-run / rollback guarantees (v0.17.3-0006). |
| `clone-and-reuse.md` | Duplicating a rule as a starting point, sharing a normalization group across rules. |

## Going deeper (for now)

- [`sort-vs-numbering.md`](sort-vs-numbering.md) — "I set Channel Sort but my channels aren't numbered alphabetically": the `sort_field` vs. `sort_group` distinction and the Auto-numbering gotcha.
- [`docs/api.md`](../../api.md) — the `/channel-pipeline` router endpoints (the old `/auto-creation` path still works as a deprecated alias).
- [`docs/normalization.md`](../../normalization.md) — Channel Pipeline rules typically reference a normalization group; understand normalization before authoring complex rules.
- [`debugging-rules.md`](debugging-rules.md) — the rule analyzer: what it checks, the 7 finding codes, and how to run it.
- [`runaway-safety-cap.md`](runaway-safety-cap.md) — what to do when a run is "capped", and how to adjust the per-run safety cap.
- [`docs/channel_pipeline_rule_analyzer.md`](../../channel_pipeline_rule_analyzer.md) — the full technical reference for the rule analyzer (finding-code trigger logic, response schema, implementation notes).
- [`docs/commands/analyze-rules.md`](../../commands/analyze-rules.md) — the `/analyze-rules` agent command, for running the analyzer via an AI assistant.
