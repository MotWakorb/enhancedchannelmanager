# Finding & fixing mis-linked channels

> **Audience:** Operator whose channel is showing the wrong programme listings —
> especially West / Pacific feeds that display an East-coast schedule.

## The problem this catches

Every channel can be linked to exactly one EPG row (its `epg_data_id`). When two
channels are linked to the **same** EPG row, they show identical listings — even
though they are different feeds.

The classic case: a **West** feed (e.g. *USA Network West*) gets linked to its
**East** counterpart's EPG row. The guide then shows the East schedule three
hours early. Nothing looks broken — the channel *has* a guide — so the mis-link
is easy to miss until a viewer notices the times are wrong.

This is a **linkage** problem, not a normalization problem: the channel names
and streams are fine; only the EPG pointer is wrong.

## Run the audit

The audit is **read-only** — it inspects your channels and reports, it never
changes anything.

- **In the EPG match preview:** when you run an EPG auto-match, the result now
  includes a **"shared EPG links"** summary listing any channels in the matched
  set that already point at the same EPG row.
- **On demand (whole fleet):** ask the assistant to *"audit EPG duplicates"*
  (MCP tool `audit_epg_duplicates`), or call
  `GET /api/epg/audit-duplicates` directly.

Each reported group tells you:

- the shared **EPG row** (its id, name, and `tvg_id`),
- how many channels share it, and
- **which channels** (id + name) are affected.

Channels with **no** EPG link are *not* reported — they are unlinked, not
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

1. Find the correct EPG entry for that feed — for a West feed, look for a
   Pacific guide entry (e.g. `USANetwork(Pacific)(USAP).us`). Use the EPG match
   preview to see the candidates.
2. **Re-link** the channel to the correct entry — in the UI via the channel's
   EPG picker, or with the assistant's `link_channel_epg` tool (supply the
   channel and the chosen `tvg_id` or `epg_data_id`).
3. Re-run the audit to confirm the group is gone.

> **Tip:** If the correct Pacific entry doesn't rank well in auto-match, that's a
> separate matcher-scoring issue (tracked under the timezone-aware scoring work).
> The audit's job is to *find* the mis-links; re-linking is the fix.
