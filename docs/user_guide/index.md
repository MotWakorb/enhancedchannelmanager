# ECM User Guide

> Enhanced Channel Manager sits between your provider playlists and EPG
> sources on one side and Dispatcharr on the other. It's how you build and
> maintain the channel lineup Dispatcharr serves: importing M3U streams,
> matching EPG data, defining channels, and keeping all of it correct as
> providers change things underneath you.
>
> This guide is for **operators**: the people who install ECM, connect it to
> Dispatcharr, and use it to keep their channels and EPG tidy. It is not
> written for the end users who just watch the streams ECM produces.

If a topic isn't covered here yet, check ECM's developer-facing docs
(`docs/*.md`); the Reference table at the bottom of this page links to the
most useful ones.

## Start here

| If you're... | Go to |
|-|-|
| Doing a brand new install | [Getting Started](getting-started/index.md) |
| Getting oriented in the current layout: sidebar, Dashboard drill-down, keyboard shortcuts | [Find Your Way Around the Operator Workspace](operator-workspace.md) |
| Doing day-to-day channel work | [Channels & Streams](channels-streams/index.md) |

## Build and maintain the lineup

### [Getting Started](getting-started/index.md)

Install ECM, connect it to Dispatcharr, and verify the connection is healthy. Start here on day one.

- **[Set Up Your First Channels](getting-started/your-first-channels.md)**: end-to-end workflow tutorial: add an M3U account, add an EPG source, choose which stream groups to sync, refresh, then create channels, channel groups, and stream assignments in Channel Manager.

### [Channels & Streams](channels-streams/index.md)

The day-to-day surface: managing channels, assigning streams, bulk operations, and stream deduplication. The model that everything else operates on.

### [M3U Manager](m3u-manager/index.md)

Add, refresh, and configure every provider playlist ECM pulls streams from: account setup, filters, priority, linked accounts, and choosing which stream groups sync.

### [Channel Pipeline](channel-pipeline/index.md)

Define rules that automatically create channels from incoming streams. Conditions, actions, bulk operations, testing a rule before enabling it, and how to debug a rule that isn't firing.

### [Normalization](normalization/index.md)

Clean up noisy stream names so your channels have the names you actually want. Includes the *Apply to existing channels* flow for one-time bulk renames. (For the deep technical reference, see [`docs/normalization.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/normalization.md).)

### [EPG](epg/index.md)

EPG sources (including Schedules Direct), channel-to-EPG matching, troubleshooting, and the dummy EPG template engine for channels that don't have upstream EPG data.

### [Logo Manager](logo-manager/index.md)

The central library of channel artwork: finding, adding, matching to channels, and cleaning up logos nothing is using. All of it lives on this single page, unlike sections such as Channels & Streams or Stats that split their material into separate per-task articles.

## See what's happening

### [Guide](guide/index.md)

The read-only, TV-guide-style grid: checking whether EPG data is reaching a channel, fixing a channel's EPG match without leaving the grid, and printing a copy of the guide. All of it lives on this single page, unlike sections such as Channels & Streams or Stats that split their material into separate per-task articles.

### [M3U Changes](m3u-changes/index.md)

A read-only log of what a provider added or removed since ECM's last refresh, so you can decide whether a playlist change needs action back in M3U Manager. All of it lives on this single page, unlike sections such as Channels & Streams or Stats that split their material into separate per-task articles.

### [Stats](stats/index.md)

The Stats page, including the Stats v2 features shipped in v0.17.0.

- **[Overview, Top Watched & Channel Drill-Down](stats/overview-top-watched.md)**, **[Bandwidth](stats/bandwidth.md)**, **[Enhanced Statistics](stats/enhanced-statistics.md)**, **[Popularity Rankings](stats/popularity.md)**, **[Watch History](stats/watch-history.md)**, and **[Providers](stats/providers.md)**: per-panel walkthroughs.
- **[Users panel](stats/users-panel.md)**: per-user watch-time totals, per-user channel breakdowns, date-range selector. Admin-only.
- **[Metric glossary](stats/metric-glossary.md)**: definitions for every Stats v2 number: watch time, session count, last watched, buffer events, provider attribution, bytes delta, and bitrate.
- **[History cutover note](stats/stats-v2-history-cutover.md)**: what happens to historical stats data at the v0.17.0 cutover; why metrics start on deploy day.

### [Journal](journal/index.md)

ECM's forensic record of every channel, EPG, M3U, watch, task, Channel Pipeline, and Event Sync change: how to filter down to the change you're chasing, read a before/after diff, and purge old entries. All of it lives on this single page, unlike sections such as Channels & Streams or Stats that split their material into separate per-task articles.

### [Notifications & Alert Methods](notifications/index.md)

Configure SMTP, Discord webhooks, Telegram bots, and ntfy targets so scheduled-task alerts (M3U refresh failures, EPG warnings, probe results) reach you outside the UI. Covers the Email Alert Recipients list, per-task gates, and ntfy's alert-method routing.

### [Integrations](integrations/index.md)

Connect ECM to Emby, Plex, and/or Jellyfin so the Stats page shows viewer
usernames instead of raw IP addresses. Also covers the full MCP / Claude AI
connection reference: the mcp-remote bridge (Claude Desktop) and the Claude
Code `.mcp.json` path, both using a static Bearer header.

- **[Emby Integration](integrations/emby.md)**: full Emby walkthrough:
  prerequisites, getting a server-local API key, configuration, what
  attribution looks like in Stats, network requirements, and troubleshooting.
- **[MCP Integration](integrations/mcp.md)**: step-by-step mcp-remote bridge
  setup, Claude Code `.mcp.json`, key rotation, and troubleshooting.

## Configure and recover

### [Settings](settings/index.md)

Configuring how ECM connects to other systems, processes channels, sends notifications, stays healthy, and (for admins) controls access: the drill-in navigation with six grouped destinations.

### [Backup & Restore](backup-restore/index.md)

Backing up your ECM configuration and restoring it on a new install.

- **[Cross-Instance Sync](backup-restore/cross-instance-sync.md)**: One-way A→B config replication for DR standbys and multi-instance setups. Covers setup, the two load-bearing semantics (one-way, provider credentials sent on every cycle), and troubleshooting.

### [Troubleshooting](troubleshooting/index.md)

Common problems, how to read ECM's logs, and what to gather before asking for help.

## By workspace destination

Looking for the tutorial for a specific ECM destination? This table follows
the grouped sidebar order and links straight to the
section that owns (or will own) its "Common tasks" tutorials.

| Group | Destination | Tutorials live in |
|-|-|-|
| Overview | Dashboard | [Operator Workspace](operator-workspace.md) |
| Operations | Channel Manager | [Channels & Streams](channels-streams/index.md) |
| Operations | M3U Manager | [M3U Manager](m3u-manager/index.md) |
| Operations | EPG Manager | [EPG](epg/index.md) |
| Operations | Logo Manager | [Logo Manager](logo-manager/index.md) |
| Automation | Channel Pipeline | [Channel Pipeline](channel-pipeline/index.md) |
| Insights | Guide | [Guide](guide/index.md) |
| Insights | M3U Changes | [M3U Changes](m3u-changes/index.md) |
| Insights | Stats | [Stats](stats/index.md) |
| Insights | Journal | [Journal](journal/index.md) |
| System | Settings | [Settings](settings/index.md) |

Settings is one primary destination with many pages. Some subsections already have a
home in an existing section: Normalization ([`normalization/`](normalization/index.md)),
Backup & Restore ([`backup-restore/`](backup-restore/index.md)), and MCP
Integration ([`integrations/mcp.md`](integrations/mcp.md)). These are
cross-linked from the Settings landing page once it ships rather than
duplicated there.

## Conventions

- **In-UI labels are authoritative.** When this guide refers to a destination, button, or setting, it uses the exact label you'll see in ECM's UI.
- **"Operator" vs. "end user."** Operators run ECM. End users watch the streams ECM produces and rarely open the UI. Almost all of this guide is operator-focused.
- **Going deeper.** Most sections end with a *Going deeper* block linking to developer documentation for operators who want to understand the underlying behaviour.

## Reference

| Looking for… | Try… |
|-|-|
| The HTTP API | [`docs/api.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/api.md) |
| System architecture | [`docs/architecture.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/architecture.md) |
| On-call runbooks (incident response) | [`docs/runbooks/`](https://github.com/MotWakorb/enhancedchannelmanager/tree/main/docs/runbooks) |
| Service-level objectives | [`docs/sre/slos.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/sre/slos.md) |
| Release notes | Discord release-notes channel (see [`docs/discord_release_notes.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/discord_release_notes.md)) |
| Error telemetry & how to opt out | [`error-telemetry-opt-out.md`](error-telemetry-opt-out.md) |
