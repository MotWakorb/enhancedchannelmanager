# Migrate channel guides between IPTV and Gracenote

> For administrators moving existing channel guide assignments to another
> XMLTV or Schedules Direct source without overwriting uncertain matches.

ECM uses the station identifier in XMLTV `<gnid>` (or legacy `<lcn>`) to find
the corresponding row in the target EPG. Migration changes only a channel's
guide assignment; it does not rename, renumber, or replace the channel.

## Preview a migration

1. Open **EPG Manager** and select **Migrate Guides**.
2. Choose the XMLTV or Gracenote source that should become the target.
3. Select **Preview migration**. ECM reads the current assignments and shows
   one status for every channel:
   - **Ready** — exactly one target row has the same station identifier.
   - **Already on target** — no change is needed.
   - **No guide assigned** — there is no current assignment to translate.
   - **LCN not found** — the current XMLTV channel has no usable station ID.
   - **No target match** — the target contains no matching station ID.
   - **Ambiguous target** — more than one target row uses that station ID.

**Result:** You can inspect the exact ready count and every unresolved channel
before ECM writes anything.

## Apply the ready assignments

1. Review the channel, current source, LCN, target, and status columns.
2. Check the confirmation box for the exact ready count.
3. Select **Apply N migrations** (the button repeats the exact ready count).

**Result:** ECM changes only rows marked **Ready**. Missing and ambiguous rows
remain untouched. If another user changes a guide after the preview, ECM skips
that channel instead of overwriting the newer assignment. The completion
message reports updated, skipped, and failed counts; successful changes are
also written to the Journal.

## Limits and recovery

A preview is bounded to 1,000 channels and 50,000 EPG rows. If an instance is
larger, ECM stops before mutation. Apply can partially succeed if Dispatcharr
rejects an individual update; rerun Preview to see the current state and retry
only the remaining ready rows.

ECM does not provide an automatic undo for a migration. To reverse it, choose
the former source as the target, preview the reverse mapping, and confirm the
ready rows.
