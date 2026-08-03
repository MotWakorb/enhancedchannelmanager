# Finding & fixing mis-linked channels

## The problem this catches

Every channel can be linked to exactly one EPG row (its `epg_data_id`). When two
channels are linked to the **same** EPG row, they show identical listings, even
though they are different feeds.

The classic case: a **West** feed (e.g. *USA Network West*) gets linked to its
**East** counterpart's EPG row. The guide then shows the East schedule three
hours early. Nothing looks broken (the channel *has* a guide), so the mis-link
is easy to miss until a viewer notices the times are wrong.

This is a **linkage** problem, not a normalization problem: the channel names
and streams are fine; only the EPG pointer is wrong.

## Run the audit

The audit is **read-only**. It inspects your channels and reports; it never
changes anything.

- **On demand (whole fleet):** there is currently no way to run this audit from
  the web UI. There is no equivalent button in the EPG Manager or Channel
  Manager UI.

> **Correction (2026-07-31):** an earlier version of this page said the
> [Bulk EPG Assignment](channel-to-epg-matching.md) result "now includes a
> shared EPG links summary" inline in the match preview. That's not what
> ships: the backend computes the same shared-link data as part of its match
> response, but the frontend never reads or displays it. There is no
> in-preview summary to look at. Use the on-demand audit above instead.

Each reported group tells you:

- the shared **EPG row** (its id, name, and `tvg_id`),
- how many channels share it, and
- **which channels** (id + name) are affected.

Channels with **no** EPG link are *not* reported. They are unlinked, not
mis-linked. Only genuine sharing (two or more channels on one EPG row) surfaces.

**Example output**

```
Found 1 set of channels sharing one EPG link (2 channels affected, 148 total):

  EPG row id=500 — USA Network East, tvg_id=USANetwork.us → 2 channels share it:
    • channel 10: USA Network
    • channel 55: USA Network West
```

Here `USA Network West` (channel 55) is wrongly pointed at the East EPG row.

## Fix a flagged channel

For each channel that is on the **wrong** EPG row:

1. Find the correct EPG entry for that feed. For a West feed, look for a
   Pacific guide entry (e.g. `USANetwork(Pacific)(USAP).us`). Use the EPG match
   preview to see the candidates.
2. **Re-link** the channel to the correct entry: in the UI via the channel's
   EPG picker, or with the assistant's `link_channel_epg` tool (supply the
   channel and the chosen `tvg_id` or `epg_data_id`).
3. Re-run the audit to confirm the group is gone.

> **Tip:** The matcher is now [timezone/region-aware](channel-to-epg-matching.md#timezone-and-region-awareness).
> Re-matching a West channel today should rank its Pacific entry correctly in
> most cases, so re-matching (instead of manually re-linking) is often enough to
> clear a flagged group. If a regional entry still doesn't rank well after
> re-matching, that's a matcher-scoring gap worth reporting. Either way, the
> audit's job is to *find* mis-links; re-linking (or re-matching) is the fix.
