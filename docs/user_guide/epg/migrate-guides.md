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
   - **Ready**: exactly one target row has the same station identifier.
   - **Already on target**: no change is needed.
   - **No guide assigned**: there is no current assignment to translate.
   - **LCN not found**: the current XMLTV channel has no usable station ID.
   - **No target match**: the target contains no matching station ID.
   - **Ambiguous target**: more than one target row uses that station ID.
   - **Unsupported source type**: the current assignment belongs to a dummy
     or unknown source. Only XMLTV and Schedules Direct origins are eligible.

**Result:** You can inspect the exact ready count and every unresolved channel
before ECM writes anything.

## Apply the ready assignments

1. Review the channel, current source, LCN, target, and status columns.
2. Check the confirmation box for the exact ready count.
3. Select **Apply N migrations** (the button repeats the exact ready count).

**Result:** ECM changes only rows marked **Ready**. Missing and ambiguous rows
remain untouched. The signed preview expires after five minutes and is bound to
the current administrator, ECM instance, target source, LCN, and exact current
and target EPG identities. If the source mapping, EPG row, or channel assignment
changes after preview, ECM skips that channel instead of overwriting the newer
state. Apply is accepted as a background job, and the dialog polls its progress
without an arbitrary time limit so slow multi-channel runs continue outside the
request timeout. Transient polling failures retain the batch ID and latest
partial progress and are retried while the dialog remains open. Closing the
dialog stops client polling but does not cancel the accepted server job.
Results stay open in the dialog and report every updated, skipped, failed, or
updated-but-not-audited row.

## Limits and recovery

A preview is bounded to 1,000 channels and 50,000 EPG rows. Bounded EPG reads
also stream behind a response-byte ceiling before JSON decoding, including on
Dispatcharr versions that return one flat list. If an instance is larger, ECM
stops before mutation. Apply can partially succeed if Dispatcharr rejects an
individual update; rerun Preview to see the current state and retry only the
remaining ready rows.

ECM does not provide an automatic undo for a migration. To reverse it, choose
the former source as the target, preview the reverse mapping, and confirm the
ready rows.

Preview tokens are intentionally short-lived but are not stored in a one-time
token database. Replaying one after a successful apply is idempotent: the
apply-time current-assignment check skips already changed channels. ECM
serializes migration applies. Inside that serialized operation it acquires one
bounded target/source mapping snapshot, proves each signed target is still the
sole candidate, and refetches each channel and its current/target EPG rows
immediately before PATCH. Dispatcharr does not expose a mapping revision or a
compare-and-swap channel update, so a non-migration writer can still change the
source mapping after the snapshot, or the channel in the small interval between
its refetch and PATCH.

Each attempted audit uses a shared batch ID: 128 random bits displayed as 32
lowercase hexadecimal characters. Polling state is process-local, is never
expired while running, and remains available for 30 minutes after the job
becomes terminal. Only the same immutable account identity (provider plus
numeric/synthetic ID) that accepted the job can poll it; renaming that account
does not break access, and foreign jobs look identical to missing jobs.
Authorization is not continuously rechecked while the job runs.

A restart removes the progress envelope. Dispatcharr and ECM's Journal are
separate systems and cannot commit atomically. If the channel PATCH succeeds
but the Journal write fails, ECM reports **updated_audit_failed** and does not
claim a clean audited success or retry automatically. Cancellation, restart,
an indeterminate Dispatcharr response, or interruption between PATCH and
Journal can also leave upstream state without a Journal row. After any
interruption, create a fresh preview, verify affected channels directly in
Dispatcharr, and reconcile their current assignments before retrying.

XMLTV downloads use ECM's established outbound SSRF policy: HTTP(S) only,
resolve-and-connect by validated IP, and every redirect is revalidated.
Link-local/cloud-metadata destinations are always blocked; RFC1918 and loopback
follow the instance's global LAN-friendly/public-only outbound setting.
