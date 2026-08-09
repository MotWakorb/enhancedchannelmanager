# Bulk Channel Operations

Bulk operations save time, but they multiply mistakes at the same rate they multiply changes. Before you run anything here at scale, read **[Understand what's reversible](#understand-whats-reversible)** below. Some of these actions live inside [Edit Mode's](channels-overview.md) staging model and can be discarded; others write to Dispatcharr the moment you confirm them, staged session or not.

## Common tasks

### Understand what's reversible

Row checkboxes only exist in **Edit Mode**: turn it on first. With Edit Mode on, select two or more channels in the Channels panel (their row checkboxes) to reveal a floating selection toolbar at the bottom of the panel: **Delete**, **Probe**, **Find Duplicates**, **Renumber**, **Assign EPG**, **Merge**, and a **More** menu (grouped into MOVE / SELECTION / PROFILES headers) with Move to group, Normalize Names, **Set Logo from M3U**, **Set Logo from EPG**, Sort Streams, **Fetch Gracenote IDs**, and **Profile visibility**.

![Floating selection toolbar reading "2 selected" with Delete, Probe, Find Duplicates, Renumber, Assign EPG, Merge, More, and Clear controls](../../images/user_guide/channels-streams/1-bulk-selection-toolbar.png)

These actions fall into two groups, verified directly against this build:

| Reversible while in Edit Mode | Immediate: writes to Dispatcharr right away |
|-|-|
| **Delete** (bulk toolbar): its own confirmation dialog says *"Changes can be undone while in edit mode."* | **Merge**: via either the toolbar's **Merge** button or **Find Duplicates**' merge action |
| **Assign EPG** (bulk EPG matching), **Fetch Gracenote IDs** | **Import Channels from CSV** |

**Create new channel group** used to belong in the right-hand column; it is staged now, alongside every other Edit Mode toolbar action. See [Channel Manager](channels-overview.md).

The right-hand column is not covered by **Cancel** or **Discard**. If you merge two channels or import a CSV by mistake, Edit Mode's undo stack has nothing to give back. You're correcting the result directly in Channel Manager (or Dispatcharr), not reverting a stage. Keep this table in mind through the rest of this page.

### Import channels from a CSV file

1. Enter **Edit Mode**, then open **More actions** (⋮) above the Channels panel and choose **Import CSV**.
2. If you don't already have a file, use **CSV Template** in the same menu first. It downloads a starter file with the required `name` column and the optional columns (`channel_number`, `group_name`, `tvg_id`, `gracenote_id`, `logo_url`, `stream_urls`, semicolon-separated for multiple streams).
3. Drop your `.csv` file onto the dialog, or click it to browse.

**Result:** ECM parses the file and shows a preview table (name, channel number, group, TVG ID) before anything is created, along with the row count in the confirm button (e.g., "Import 2 Channels").

![Import Channels from CSV dialog showing a preview table with two rows before import is confirmed](../../images/user_guide/channels-streams/7-csv-import-preview.png)

4. Review the preview, then click **Import N Channels**.

**Result:** The channels are created in Dispatcharr immediately. This is one of the actions from the table above that is *not* staged. Closing the dialog or clicking **Cancel** in Edit Mode afterward will not undo an import that already ran; if you imported the wrong file, delete the resulting channels directly.

### Assign EPG to multiple channels at once

1. Select the channels you want to match (row checkboxes), then click **Assign EPG** in the selection toolbar.
2. Choose which EPG sources to match against (**Select All**, or pick specific sources), then click **Match N Channels**.

**Result:** ECM analyzes every selected channel against the chosen sources and sorts the outcome into three buckets: **matched** (a single confident match), **need review** (more than one plausible match, shown with a confidence percentage), and **unmatched**.

![Bulk EPG Assignment results showing 0 matched, 1 need review at 22 percent, and 1 unmatched, with Review Changes and Accept Best Guesses options](../../images/user_guide/channels-streams/4-epg-conflict-summary.png)

3. For channels that need review, choose **Review Changes** to decide per channel, or **Accept Best Guesses** to take ECM's top-ranked candidate for every conflict in one click.
4. In **Review Changes**, each channel shows its match candidates ranked by confidence, labeled with the source they came from (for example, "EPG guru US Gracenote") and a percentage score. Pick a candidate, or **Skip this channel** to leave it unmatched, then step through with **Next**.

![Review Changes screen for one channel showing two ranked candidates, "WEEK" marked Recommended at 22% and "WEEK-DT" at 22%, both from EPG guru US Gracenote](../../images/user_guide/channels-streams/5-epg-review-candidate.png)

5. Click **Assign N Channel(s)** to finish.

**Result:** This action *is* staged. This was verified by checking the channel's stored EPG ID immediately after assigning and again after discarding: the assignment only reaches Dispatcharr when you exit Edit Mode with **Apply All**. Discarding (or clicking **Cancel**) throws the match away exactly like a staged channel creation.

### Fetch Gracenote IDs

**Gracenote ID** (Dispatcharr's `tvc-guide-stationid`) is a station identifier some guide providers use for lineup matching. ECM can fetch it automatically, but only for channels that already have an EPG match (a TVG-ID). Fetching reads the `<gnid>` (or `<lcn>` as a fallback) from that channel's matched XMLTV guide entry.

1. Select one or more channels that already have a TVG-ID assigned (see the previous task if they don't yet).
2. Open **More** in the selection toolbar and choose **Fetch Gracenote IDs**.

**Result:** ECM reports how many channels it found an ID for, how many it could not find one for, and how many were skipped because they have no TVG-ID yet:

![Fetch Gracenote IDs dialog showing 0 found, 0 not found, and 2 no TVG-ID, listing two channels that need a TVG-ID assigned first](../../images/user_guide/channels-streams/6-gracenote-id-gate.png)

If a channel already has a *different* Gracenote ID stored, what happens next depends on **Settings → Appearance → Gracenote ID Conflict Handling**: **Ask me what to do** (the default, which shows a conflict dialog per channel), **Skip channels with existing IDs**, or **Automatically overwrite existing IDs**.

### Find and merge duplicate channels

1. Select the channels you suspect are duplicates (or select a whole group), then click **Find Duplicates** in the selection toolbar.
2. Optionally check **Ignore spacing differences** if the duplicates only differ by whitespace.

**Result:** ECM groups channels that resolve to the same name and shows which one it would **KEEP** by default (you can change the selection per group), or reports "No duplicate channels found" if nothing matched.

![Find Duplicate Channels dialog showing one group of two duplicate channels named "ZZZ Docs Demo Duplicate", with #9902 selected to keep over #9901](../../images/user_guide/channels-streams/2-find-duplicates.png)

3. Review the KEEP choice for each group, then click **Merge N Group(s)**.

**Result:** ECM merges every duplicate group immediately. This is *not* staged, even if you're inside an active Edit Mode session. The kept channel absorbs the other channel's streams; the other channel is deleted.

### Merge specific channels manually

Use this when you want to combine channels that **Find Duplicates** wouldn't flag on name alone. For example: two differently-named channels carrying the same event.

1. Select the channels to combine, then click **Merge** in the selection toolbar.
2. In the **Merge N Channels** dialog, review the channel name, channel number, channel group, and stream profile the merged channel will use (all editable) and the combined stream list.
3. Click **Merge N Channels** to confirm.

![Merge 2 Channels dialog showing the two source channels, editable Channel Name/Number/Group/Stream Profile fields, and a Merged Streams list](../../images/user_guide/channels-streams/3-merge-channels.png)

**Result:** ECM creates one merged channel with the streams from every source channel and deletes the sources. This happens immediately, regardless of Edit Mode. There is no undo for this from inside ECM; recovering from a bad merge means recreating the split manually. Treat the confirmation dialog as your last checkpoint, the same way [Stream Deduplication](stream-dedup.md#resolving-merges-in-bulk) describes for the Pending Merges queue's **Merge all**.

## Resolving pending merges from an M3U refresh

If your provider's stream names are close enough to an existing channel that ECM flags them automatically during an M3U refresh, that's a different, larger workflow with its own queue, confidence threshold, and bulk controls. See **[Stream Deduplication](stream-dedup.md)**, which covers the **Pending Merges** page in full, including its own bulk **Merge all** / **Clear all** and per-row error recovery.

## Going deeper

- [Channel Manager](channels-overview.md): Edit Mode, staging, and the undo/redo/checkpoint model these bulk actions plug into.
- [Stream Deduplication](stream-dedup.md): the Pending Merges queue, confidence threshold, and MCP tools for the automatic (M3U-refresh-triggered) dedup path.
- [`docs/api.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/api.md) (in the repository, not part of this published guide): the channel import, EPG assignment, and merge endpoints behind these dialogs.
