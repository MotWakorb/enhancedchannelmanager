# Channels & Streams

> **Audience:** Operator doing day-to-day work in ECM. You have a working Dispatcharr connection and want to manage what's in your lineup.
>
> **Status:** Stub — articles below are placeholders.

## Section purpose

Document the core ECM workflow: viewing channels and streams, editing them, assigning streams to channels, using channel groups and tags, and reading the journal of what changed when. This is the surface most operators spend most of their time on.

## Intended audience

- **Operator** curating a channel lineup.
- **Operator** investigating "where did this channel go?" or "why is this stream attached?"

End users do not read this section.

## Planned articles

| Article | Purpose |
|-|-|
| `channels-overview.md` | The Channel Manager tab — columns, filters, what an "edit mode" session is, how undo/redo works. |
| `streams-overview.md` | The Streams pane — what a stream is, where it came from (M3U source), and how it relates to a channel. |
| `assign-streams-to-channels.md` | The matching workflow: manual assignment, the impact of normalization on auto-matching, what happens when a stream's source moves. |
| `channel-groups-and-tags.md` | When to use channel groups vs. tags, how Dispatcharr consumes them, ordering semantics. |
| `bulk-edit.md` | Selecting many channels at once, the bulk-edit pane, the latency-amplification gotchas (see engineering-discipline note on "bulk operations multiply latent severity"). |
| `the-journal.md` | The Journal tab — what changes ECM records, how to filter by entity, how to find the change that broke something. |
| `logos.md` | The Logo Manager — uploading logos, where they're stored, how Dispatcharr picks them up. |

## Going deeper (for now)

- [`docs/api.md`](../../api.md) — the channel and stream API endpoints, when an operator wants to script something.
- [`docs/architecture.md`](../../architecture.md) — the data layer (SQLite at `/config/journal.db`) and how channels/streams flow through it.
