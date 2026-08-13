# Channel Pipeline

## Section purpose

Cover the Channel Pipeline page end-to-end: how rules are structured, what conditions and actions are available, how rules interact with normalization, how to test a rule before enabling it, and how to debug a rule that isn't firing the way you expected.

## Articles

| Article | Purpose |
|-|-|
| [Rules Overview](rules-overview.md) | What a Channel Pipeline rule is, creating your first Standard rule, and the Logic / Targeting / Output & Run tabs. |
| [Build Conditions and Actions](conditions-and-actions.md) | The condition and action catalogue (every field, operator, and action type) with a worked example. |
| [Test a Rule (Dry Run)](test-a-rule.md) | The per-rule dry-run ("Test") workflow: what it checks, how to read the result, and how it differs from the rule analyzer and a live Run. |
| [Bulk-Edit Multiple Rules](bulk-rule-settings.md) | Selecting multiple rules and applying a setting change to all of them at once via Bulk edit. |
| [Duplicate a Rule](clone-a-rule.md) | Duplicating a rule as a starting point: the Copy is created disabled so you can review before enabling it. |
| [Event Sync Quick Start](event-sync-quickstart.md) | Getting from the Channel Pipeline page to a first Event Sync preview; defers to [`docs/event_sync.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/event_sync.md) for the full guide. |
| [Channel Sort vs. Channel Numbering](sort-vs-numbering.md) | Why "Channel Sort" doesn't renumber channels by itself, the `channel_number: auto` gotcha that silently skips rule-level renumbering, and why `sort_group` is the action built for "keep my channels alphabetically numbered." |
| [Runaway Safety Cap](runaway-safety-cap.md) | The per-run channel cap (the GH #473 safety valve): why a run gets "capped", that the Channel Pipeline is idempotent so you can just re-run, and how to view/raise/disable the cap (and its sibling log-entries cap) from **Settings → Channel Pipeline** (admin-only). |
| [Debugging Rules](debugging-rules.md) | How to diagnose "my rule didn't fire" using the rule analyzer: the 8 finding codes in plain language with worked examples, how to run the analyzer (API direct call, debug-bundle upload, `/analyze-rules` agent command), and when to use the analyzer vs. the per-rule dry-run preview. |
| [Fuzzy Matching for Local / OTA Channels](fuzzy-locals-matching.md) | Scored fuzzy matching for OTA / Local channels: when to use it, how to preview before writing, the callsign safety gate, and the dry-run / rollback guarantees. |

## Going deeper (for now)

- [`rules-overview.md`](rules-overview.md): start here if you haven't
  created a Channel Pipeline rule before.
- [`conditions-and-actions.md`](conditions-and-actions.md): the full
  condition/action catalogue.
- [`sort-vs-numbering.md`](sort-vs-numbering.md): the `sort_field` vs. `sort_group` distinction and the Auto-numbering gotcha behind "I set Channel Sort but my channels aren't numbered alphabetically."
- [`docs/api.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/api.md): the `/channel-pipeline` router endpoints (the old `/auto-creation` path still works as a deprecated alias).
- [`docs/normalization.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/normalization.md): Channel Pipeline rules typically reference a normalization group; understand normalization before authoring complex rules.
- [`debugging-rules.md`](debugging-rules.md): the rule analyzer: what it checks, the 8 finding codes, and how to run it.
- [`runaway-safety-cap.md`](runaway-safety-cap.md): what to do when a run is "capped", and how to adjust the per-run safety cap.
- [`docs/channel_pipeline_rule_analyzer.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/channel_pipeline_rule_analyzer.md): the full technical reference for the rule analyzer (finding-code trigger logic, response schema, implementation notes).
- [`docs/commands/analyze-rules.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/commands/analyze-rules.md): the `/analyze-rules` agent command, for running the analyzer via an AI assistant.
- [`docs/event_sync.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/event_sync.md): the full Event Sync guide; [`event-sync-quickstart.md`](event-sync-quickstart.md) is the thin wrapper that gets you there from the Channel Pipeline page.
