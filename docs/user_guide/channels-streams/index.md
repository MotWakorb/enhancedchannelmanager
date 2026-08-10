# Channels & Streams

Core editing (Edit Mode/staging), bulk & dedup workflows, and the deep-dive articles on streams, stream assignment, groups and tags, per-channel history, and logos are all covered below. For the current layout, start with [Find Your Way Around the Operator Workspace](../operator-workspace.md).

## Section purpose

Document the core ECM workflow: viewing channels and streams, editing them, assigning streams to channels, using channel groups and tags, and reading the journal of what changed when. This is the surface most operators spend most of their time on.

## Start here

| I want to… | Go to |
|-|-|
| Understand Edit Mode, staging, and how Undo/Redo/checkpoints work | [Channel Manager](channels-overview.md) |
| Import channels from a CSV, bulk-assign EPG, or fetch Gracenote IDs | [Bulk Channel Operations](bulk-edit.md) |
| Find and merge duplicate channels across many at once | [Bulk Channel Operations](bulk-edit.md#find-and-merge-duplicate-channels) |
| Resolve dedup prompts ECM raises automatically during an M3U refresh | [Stream Deduplication](stream-dedup.md) |
| Play a stream or channel in the browser before committing to it | [Preview Streams and Channels](stream-preview.md) |

## Articles

| Article | Purpose |
|-|-|
| [Channel Manager](channels-overview.md) | The Channel Manager page: Edit Mode, the staging model, Apply/Discard, and Undo/Redo/checkpoints. |
| [Bulk Channel Operations](bulk-edit.md) | CSV import, bulk EPG assignment, Gracenote IDs, Find Duplicates, and manual merge, including which of those are staged vs. immediate. |
| [Stream Deduplication](stream-dedup.md) | The automatic dedup prompt (drag-drop, Create in…, and the Pending Merges queue from M3U refreshes), confidence threshold, and MCP tools. |
| [Streams](streams-overview.md) | The Streams pane: what a stream is, where it came from (M3U source), and how it relates to a channel. |
| [Assign Streams to Channels](assign-streams-to-channels.md) | The matching workflow: manual assignment, the impact of normalization on auto-matching, what happens when a stream's source moves. |
| [Preview Streams and Channels](stream-preview.md) | Playing a stream or channel in the browser: the three playback modes, and the modal's Open in VLC / Download M3U / Copy URL actions. |
| [Channel Groups and Tags](channel-groups-and-tags.md) | When to use channel groups vs. tags, how Dispatcharr consumes them, ordering semantics. |
| [Find Out What Changed on a Channel](the-journal.md) | The Journal page: what changes ECM records, how to filter by entity, how to find the change that broke something. |
| [Put Logos on Channels](logos.md) | The Logo Manager: uploading logos, where they're stored, how Dispatcharr picks them up. |

## Going deeper (for now)

- [`docs/api.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/api.md): the channel and stream API endpoints, when an operator wants to script something.
- [`docs/architecture.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/architecture.md): the data layer (SQLite at `/config/journal.db`) and how channels/streams flow through it.
