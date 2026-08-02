# Find Out What Changed on a Channel

The Journal is ECM's record of every change it made or observed. This article is the narrow version: how to use it to answer "what happened to *this* channel." For the page itself (all seven categories, the purge control, reading a diff) see [Journal](../journal/index.md).

## Common tasks

### Trace the history of one channel

**This isn't possible today.** The Journal's Channel category holds only **Bulk Commit** summary rows, one per Edit Mode session applied via **Apply All**. No per-channel rows are written for the individual changes inside that session, so no row anywhere carries a channel's name, and searching for one returns nothing.

1. Open **Journal**.
2. Set the **All Categories** dropdown to **Channel**.
3. Type a channel's name into **Search entries...**.

**Result:** "No journal entries." Clearing the search shows the Channel category's actual contents: **Bulk Commit** rows only, each with **Entity** and **Action** both reading **Bulk Commit** and a **Description** like *"Applied 3 operations in bulk commit"*. Expanding a row shows aggregate counters (`operations_applied`, `channels_created`, and so on) and the **Batch:** identifier, but no per-channel before/after values.

Tracing a specific channel's history by name is tracked as an open product defect: bead `enhancedchannelmanager-r9py9`. Until it's fixed, the Journal can confirm *that* an Edit Mode session was applied and roughly what it contained in aggregate, but not the field-level history of one channel.

### Understand what you are looking at

Three things about these rows regularly trip operators up.

**The Action dropdown does not list every action ECM records.** It offers Create, Update, Delete, Start, Stop, Refresh, Stream Add, Stream Remove, Stream Reorder and Reorder. Merges and Edit Mode's own commit are recorded under action types that have no entry in the list, so they show up in the table (as **Merge** and **Bulk Commit** badges) but cannot be filtered *to*. If you are hunting for a merge or for the moment a batch was applied, leave the dropdown on **All Actions** and use the search box instead.

**One Edit Mode session produces one Bulk Commit row, and today, only that row.** Applying a batch writes a summary row, for example *"Applied 3 operations in bulk commit"*, that tells you when you applied the batch and how many operations it contained in aggregate. It does not yet write a per-channel row for each change inside that batch; see [Trace the history of one channel](#trace-the-history-of-one-channel) above.

**The Source column separates you from automation.** **UI** means the change came from the ECM interface, **AI** from an MCP agent, **Scheduler** from a scheduled task, and **Channel Pipeline** from a rule run. When a lineup changes overnight and nobody touched the UI, the Source column is the first thing to read.

### Find the rest of a batch

1. Expand a row that was part of a batch. The detail area shows **Batch:** followed by an identifier.

**Result:** Every change applied together shares that identifier, but the Journal page only displays it: it is not a link, and there is no batch filter in the filter row (Category, Action, Source, and free-text search only). From the UI, the closest you can get is searching for something the rows have in common, such as a shared description fragment or time window. Making the Batch id clickable, or adding a batch filter, is tracked as an open product defect: bead `enhancedchannelmanager-d2kdg`. Reconstructing a batch precisely by its id currently requires API access (`GET /api/journal?batch_id=<id>`), outside the ECM web UI.

## Going deeper

- [Journal](../journal/index.md): the full page, including the other categories, reading the Before/After diff, and purging old entries.
- [Channel Manager](channels-overview.md): the Edit Mode session that produces the Bulk Commit rows.
- [Bulk Channel Operations](bulk-edit.md): the operations that write straight through, which is what you are usually chasing when a channel changed and there was nothing to undo.
- [`docs/api.md#journal`](https://github.com/MotWakorb/enhancedchannelmanager/blob/dev/docs/api.md#journal): the `/api/journal` endpoints, including the `batch_id` filter.
