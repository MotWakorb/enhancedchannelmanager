# ECM User Guide

> Welcome to Enhanced Channel Manager. This guide is for **operators** — the people who install ECM, connect it to Dispatcharr, and use it to keep their channels and EPG tidy.

## Status

This guide is being filled in section by section. Pages marked **Stub** describe what the section will cover but don't have the detailed articles yet. Pages marked **In progress** are partially written. Pages with no marker are complete.

If a topic you need is missing, check the cross-references at the bottom — much of ECM is documented in the developer-facing tree (`docs/*.md`) too. The user guide will eventually summarise and link those for an operator audience.

## Sections

### 1. [Getting Started](getting-started/index.md) — Stub

Install ECM, connect it to Dispatcharr, and verify the connection is healthy. Start here on day one.

### 2. [Channels & Streams](channels-streams/index.md) — Stub

The day-to-day surface: managing channels, assigning streams, working with the journal of changes. The model that everything else operates on.

### 3. [Auto Creation](auto-creation/index.md) — Stub

Define rules that automatically create channels from incoming streams. Conditions, actions, bulk operations, and how to debug a rule that isn't firing.

### 4. [Normalization](normalization/index.md) — Stub

Clean up noisy stream names so your channels have the names you actually want. Includes the *Apply to existing channels* flow for one-time bulk renames. (For the deep technical reference, see `docs/normalization.md`.)

### 5. [EPG](epg/index.md) — Stub

EPG sources, EPG matching, and the dummy EPG template engine for channels that don't have upstream EPG data.

### 6. [Stats](stats/index.md) — Placeholder

The Stats tab. Currently a placeholder; the v0.17.0 Stats v2 work (bd-skqln.9) will fill in the metric glossary and operator interpretation guide.

### 7. [Backup & Restore](backup-restore/index.md) — Placeholder

Backing up your ECM configuration and restoring it on a new install. Currently a placeholder; the v0.18.0 epic (bd-0i2vt) and the immediate import work (bd-gb5r5.3) will fill in the operator workflow.

### 8. [Troubleshooting](troubleshooting/index.md) — Stub

Common problems, how to read ECM's logs, and what to gather before asking for help.

## Conventions

- **In-UI labels are authoritative.** When this guide refers to a tab, button, or setting, it uses the exact label you'll see in ECM's UI.
- **"Operator" vs. "end user."** Operators run ECM. End users watch the streams ECM produces and rarely open the UI. Almost all of this guide is operator-focused.
- **Going deeper.** Most sections end with a *Going deeper* block linking to developer documentation for operators who want to understand the underlying behaviour.

## Reference

| Looking for… | Try… |
|-|-|
| The HTTP API | [`docs/api.md`](../api.md) |
| System architecture | [`docs/architecture.md`](../architecture.md) |
| On-call runbooks (incident response) | [`docs/runbooks/`](../runbooks/) |
| Service-level objectives | [`docs/sre/slos.md`](../sre/slos.md) |
| Release notes | Discord release-notes channel (see [`docs/discord_release_notes.md`](../discord_release_notes.md)) |
