# ECM User Guide

> Welcome to Enhanced Channel Manager. This guide is for **operators** — the people who install ECM, connect it to Dispatcharr, and use it to keep their channels and EPG tidy.

## Status

This guide is being filled in section by section. Pages marked **Stub** describe what the section will cover but don't have the detailed articles yet. Pages marked **In progress** are partially written. Pages with no marker are complete.

If a topic you need is missing, check the cross-references at the bottom — much of ECM is documented in the developer-facing tree (`docs/*.md`) too. The user guide will eventually summarise and link those for an operator audience.

New to the current layout? Start with **[Find Your Way Around the Operator
Workspace](operator-workspace.md)** for the grouped sidebar, Dashboard
drill-down, Settings safeguards, Channel Manager mental model, keyboard
instructions, screenshots, and legacy URL behavior.

## Sections

### 1. [Getting Started](getting-started/index.md) — In progress

Install ECM, connect it to Dispatcharr, and verify the connection is healthy. Start here on day one.

- **[Set Up Your First Channels](getting-started/your-first-channels.md)** — end-to-end workflow tutorial: add an M3U account, add an EPG source, choose which stream groups to sync, refresh, then create channels, channel groups, and stream assignments in Channel Manager.

### 2. [Channels & Streams](channels-streams/index.md) — Stub

The day-to-day surface: managing channels, assigning streams, working with the journal of changes. The model that everything else operates on.

### 3. [Channel Pipeline](channel-pipeline/index.md) — Stub

Define rules that automatically create channels from incoming streams. Conditions, actions, bulk operations, and how to debug a rule that isn't firing.

### 4. [Normalization](normalization/index.md) — Stub

Clean up noisy stream names so your channels have the names you actually want. Includes the *Apply to existing channels* flow for one-time bulk renames. (For the deep technical reference, see `docs/normalization.md`.)

### 5. [EPG](epg/index.md) — Stub

EPG sources, EPG matching, and the dummy EPG template engine for channels that don't have upstream EPG data.

### 6. [Notifications & Alert Methods](notifications/index.md) — In progress

Configure SMTP, Discord webhooks, and Telegram bots so scheduled-task alerts (M3U refresh failures, EPG warnings, probe results) reach you outside the UI. Covers the Email Alert Recipients list and how per-task toggles gate dispatch.

### 7. [Stats](stats/index.md) — Stats v2 (v0.17.0)

The Stats page, including the Stats v2 features shipped in v0.17.0.

- **[Users panel](stats/users-panel.md)** — per-user watch-time totals, per-user channel breakdowns, date-range selector. Admin-only.
- **[Metric glossary](stats/metric-glossary.md)** — definitions for every Stats v2 number: watch time, session count, last watched, buffer events, provider attribution, bytes delta, and bitrate.
- **[History cutover note](stats/stats-v2-history-cutover.md)** — what happens to historical stats data at the v0.17.0 cutover; why metrics start on deploy day.

### 8. [Integrations](integrations/index.md) — Media Server Integrations & MCP (v0.17.1)

Connect ECM to Emby, Plex, and/or Jellyfin so the Stats page shows viewer
usernames instead of raw IP addresses. Also covers the full MCP / Claude AI
connection reference — the mcp-remote bridge (Claude Desktop) and the Claude
Code `.mcp.json` path, both using the static `?api_key=` authentication method.

- **[Emby Integration](integrations/emby.md)** — full Emby walkthrough:
  prerequisites, getting a server-local API key, configuration, what
  attribution looks like in Stats, network requirements, and troubleshooting.
- **[MCP Integration](integrations/mcp.md)** — step-by-step mcp-remote bridge
  setup, Claude Code `.mcp.json`, key rotation, and troubleshooting.

### 9. [Backup & Restore](backup-restore/index.md) — In progress

Backing up your ECM configuration and restoring it on a new install.

- **[Cross-Instance Sync](backup-restore/cross-instance-sync.md)** (v0.18.1) — One-way A→B config replication for DR standbys and multi-instance setups. Covers setup, the two load-bearing semantics (one-way, credentials not synced), and troubleshooting.

Earlier DBAS articles (backup, restore, migration) are planned for a follow-on release.

### 10. [Troubleshooting](troubleshooting/index.md) — Stub

Common problems, how to read ECM's logs, and what to gather before asking for help.

## By workspace destination

Looking for the tutorial for a specific ECM destination? This table follows
the grouped sidebar order and links straight to the
section that owns (or will own) its "Common tasks" tutorials.

| Group | Destination | Tutorials live in |
|-|-|-|
| Overview | Dashboard | [Operator Workspace](operator-workspace.md) |
| Operations | Channel Manager | [Channels & Streams](channels-streams/index.md) |
| Operations | Guide | `guide/` — **Planned** |
| Operations | M3U Manager | `m3u-manager/` — **Planned** |
| Operations | EPG Manager | [EPG](epg/index.md) |
| Operations | Logo Manager | `logo-manager/` — **Planned** |
| Automation | Channel Pipeline | [Channel Pipeline](channel-pipeline/index.md) |
| Automation | M3U Changes | `m3u-changes/` — **Planned** |
| Insights | Stats | [Stats](stats/index.md) |
| Insights | Journal | `journal/` — **Planned** |
| System | Settings | `settings/` — **Planned** |

Settings is one primary destination with many pages. Some subsections already have a
home in an existing section — Normalization ([`normalization/`](normalization/index.md)),
Backup & Restore ([`backup-restore/`](backup-restore/index.md)), and MCP
Integration ([`integrations/mcp.md`](integrations/mcp.md)) — and are
cross-linked from the Settings landing page once it ships rather than
duplicated there.

## Conventions

- **In-UI labels are authoritative.** When this guide refers to a destination, button, or setting, it uses the exact label you'll see in ECM's UI.
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
| Error telemetry & how to opt out | [`error-telemetry-opt-out.md`](error-telemetry-opt-out.md) |
