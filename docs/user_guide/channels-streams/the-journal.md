# Find Out What Changed on a Channel

The Journal is ECM's record of every change it made or observed. This article is the narrow version: how to use it to answer "what happened to *this* channel." For the page itself (all seven categories, the purge control, reading a diff) see [Journal](../journal/index.md).

## Common tasks

### Trace the history of one channel

**This depends on how the channel was changed.** Per-channel rows exist and are searchable by name, but only for changes made through an MCP agent (**Source: AI**). Changes committed through Edit Mode in the UI (**Source: UI**) write only a single **Bulk Commit** summary row per session, with no per-channel rows underneath it. If a channel has only ever been touched through the ECM interface, its individual history isn't in the Journal today.

1. Open **Journal**.
2. Set the **All Categories** dropdown to **Channel**.
3. Type the channel's name into **Search entries...**. The search matches both the **Entity** and the **Description** of each row. Each row records the name the channel had at the time, so if the channel was renamed you will need to search both names to see its whole history.

![The Journal entry list filtered to the Channel category. The Stream Add, Delete and Update rows for individual channels all read Source: AI; the single Source: UI row is the Bulk Commit summary for an Edit Mode session, with no per-channel rows of its own](../../images/user_guide/channels-streams/1-journal-channel-entries.png)

**Result, for a channel changed via MCP:** you get that channel's rows newest-first, with the **Entity** column showing the channel name and the **Description** column summarising what changed, for example *"Updated channel: cleared EPG mapping"*. Click a row to expand it into **Before** and **After** blocks with the exact field values.

**Result, for a channel changed only through Edit Mode in the UI:** "No journal entries." The Channel category still contains a **Bulk Commit** row for the session that touched it, but that row's **Entity** and **Action** both read **Bulk Commit**, not the channel's name, so the search can't find it. Expanding it shows only aggregate counters (`operations_applied`, `channels_created`, and so on) and the **Batch:** identifier: no per-channel before/after values.

Tracing a UI-driven channel's history by name is tracked as an open product defect: bead `enhancedchannelmanager-r9py9`. The per-channel recording itself works; it's specifically the Edit Mode commit path that doesn't write to it.

### Understand what you are looking at

Three things about these rows regularly trip operators up.

**The Action dropdown does not list every action ECM records.** It offers Create, Update, Delete, Start, Stop, Refresh, Stream Add, Stream Remove, Stream Reorder and Reorder. Merges and Edit Mode's own commit are recorded under action types that have no entry in the list, so they show up in the table (as **Merge** and **Bulk Commit** badges) but cannot be filtered *to*. If you are hunting for a merge or for the moment a batch was applied, leave the dropdown on **All Actions** and use the search box instead.

**Whether you get one row or many depends on how the change was made.** An Edit Mode session applied through the UI writes exactly one **Bulk Commit** summary row, for example *"Applied 3 operations in bulk commit"*, that tells you when you applied the batch and how many operations it contained in aggregate; today it does not also write a per-channel row for each change inside that batch. A change made through an MCP agent writes its own per-channel row instead, with no Bulk Commit summary alongside it. See [Trace the history of one channel](#trace-the-history-of-one-channel) above for what this means when you're hunting for one channel's history.

**The Source column separates you from automation.** **UI** means the change came from the ECM interface, **AI** from an MCP agent, **Scheduler** from a scheduled task, and **Channel Pipeline** from a rule run. When a lineup changes overnight and nobody touched the UI, the Source column is the first thing to read.

### Find the rest of a batch

1. Expand a row that was part of a batch. The detail area shows **Batch:** followed by an identifier.

**Result:** Every change applied together shares that identifier, but the Journal page only displays it: it is not a link, and there is no batch filter in the filter row (Category, Action, Source, and free-text search only). From the UI, the closest you can get is searching for something the rows have in common, such as a shared description fragment or time window. Making the Batch id clickable, or adding a batch filter, is tracked as an open product defect: bead `enhancedchannelmanager-d2kdg`. Reconstructing a batch precisely by its id currently requires API access (`GET /api/journal?batch_id=<id>`), outside the ECM web UI.

## Going deeper

- [Journal](../journal/index.md): the full page, including the other categories, reading the Before/After diff, and purging old entries.
- [Channel Manager](channels-overview.md): the Edit Mode session that produces the Bulk Commit rows.
- [Bulk Channel Operations](bulk-edit.md): the operations that write straight through, which is what you are usually chasing when a channel changed and there was nothing to undo.
- [`docs/api.md#journal`](https://github.com/MotWakorb/enhancedchannelmanager/blob/dev/docs/api.md#journal): the `/api/journal` endpoints, including the `batch_id` filter.
