# Find Out What Changed on a Channel

The Journal is ECM's record of every change it made or observed. This article is the narrow version: how to use it to answer "what happened to *this* channel." For the page itself (all seven categories, the purge control, reading a diff) see [Journal](../journal/index.md).

## Common tasks

### Trace the history of one channel

1. Open **Journal**.
2. Set the **All Categories** dropdown to **Channel**.
3. Type the channel's name into **Search entries...**. The search matches both the **Entity** and the **Description** of each row. Each row records the name the channel had at the time, so if the channel was renamed you will need to search both names to see its whole history.

![The Journal entry list filtered to the Channel category, showing Stream Add, Bulk Commit, Delete and Update rows with their entity names and sources](../../images/user_guide/channels-streams/1-journal-channel-entries.png)

**Result:** You get that channel's rows newest-first, with the **Entity** column showing the channel name and the **Description** column summarising what changed, for example *"Updated channel: cleared EPG mapping"*. Click a row to expand it into **Before** and **After** blocks with the exact field values.

### Understand what you are looking at

Three things about these rows regularly trip operators up.

**The Action dropdown does not list every action ECM records.** It offers Create, Update, Delete, Start, Stop, Refresh, Stream Add, Stream Remove, Stream Reorder and Reorder. Merges and Edit Mode's own commit are recorded under action types that have no entry in the list, so they show up in the table (as **Merge** and **Bulk Commit** badges) but cannot be filtered *to*. If you are hunting for a merge or for the moment a batch was applied, leave the dropdown on **All Actions** and use the search box instead.

**One Edit Mode session produces one Bulk Commit row plus the individual rows.** Applying a batch writes a summary row, for example *"Applied 3 operations in bulk commit"*, alongside the per-channel rows for what was in it. The summary row is the one that tells you when you clicked **Apply All**; the individual rows tell you what that batch contained.

**The Source column separates you from automation.** **UI** means the change came from the ECM interface, **AI** from an MCP agent, **Scheduler** from a scheduled task, and **Channel Pipeline** from a rule run. When a lineup changes overnight and nobody touched the UI, the Source column is the first thing to read.

### Find the rest of a batch

1. Expand a row that was part of a batch. The detail area shows **Batch:** followed by an identifier.

**Result:** Every change applied together shares that identifier. The Journal page displays it but does not let you click it to filter, so to pull the whole batch you either search for something the rows have in common, or query the API directly with `GET /api/journal?batch_id=<id>`. That is the reliable way to reconstruct a large Channel Pipeline run or a big Apply All.

## Going deeper

- [Journal](../journal/index.md): the full page, including the other categories, reading the Before/After diff, and purging old entries.
- [Channel Manager](channels-overview.md): the Edit Mode session that produces the Bulk Commit rows.
- [Bulk Channel Operations](bulk-edit.md): the operations that write straight through, which is what you are usually chasing when a channel changed and there was nothing to undo.
- [`docs/api.md#journal`](https://github.com/MotWakorb/enhancedchannelmanager/blob/dev/docs/api.md#journal): the `/api/journal` endpoints, including the `batch_id` filter.
