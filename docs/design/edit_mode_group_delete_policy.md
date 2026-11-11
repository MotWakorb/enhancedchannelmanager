# Edit Mode group deletion protection

Bead: `enhancedchannelmanager-jtrlf`.

## Policy and boundaries

The immediate group-delete route and Edit Mode bulk consumer call
`channel_group_delete.hide_associated_group` before reparenting or deleting a
group. Membership in `get_all_m3u_group_settings()` protects the group, regardless
of its `enabled` or `auto_channel_sync` flags. Protected groups use the existing
`HiddenChannelGroup` store; they keep their upstream channels and group. An
association lookup failure fails the operation before a membership write.

Repeated hides are successful no-ops. A competing SQLite insert is treated as an
existing hide only after rollback and confirmation that the hidden row exists.
Bulk journals a newly inserted hide as `group_hide`, not `group_delete`.
List filtering, stale-name pruning and restore retain their existing behavior.

For unassociated groups, the existing reparent-before-delete helper, per-move
journaling and operation ledger remain in use. Explicit channel-delete operations
already staged before the group operation still execute: hiding the group does
not undo or bypass those separately confirmed deletions. Successful operations
and moves stay applied when a later DELETE fails. There is no added upstream
retry or rollback.

Group failures include the group ID and fixed, actionable guidance. Recognized
Dispatcharr channel-association and M3U-association validation reasons are mapped
to fixed prose. Unknown JSON fields, validation messages, HTML, server bodies and
HTTPX exception URLs are not relayed. A reparent failure or later DELETE failure
also names the number of channels confirmed moved before failure. That count
comes from the same per-write callback as journaling, not the final return value
of a helper that may raise halfway through.

The existing browser consumer displays the error string without a new frontend
production path. Global error/log disclosure policy is outside this change;
integration with overlapping draft PR #1050 requires normal review.

## Recorded evidence, not historical attribution

The 2026-11-08 debug bundle records 642 per-account group-setting rows, all with
integer `channel_group` IDs: 355 enabled / 287 disabled, 26 auto-sync enabled /
616 disabled. The collapsed representation has 551 groups: 265 enabled / 286
disabled, 24 auto-sync enabled / 527 disabled. Groups 207 and 599 are associated
with account 11 with both flags enabled; the group diagnostic counts 32 and 50
channels respectively.

`backend/tests/fixtures/jtrlf/recorded_group_settings.json` retains only
allowlisted numeric/boolean fields for those two recorded producer rows. Account
names, credentials, URLs and custom properties are omitted. The test reconstructs
a synthetic account envelope around those rows to exercise the real client
extractor. It does not claim the envelope or controlled 400s are raw responses
captured from the reporter's Dispatcharr deployment.

Reference source at Dispatcharr commit
`1241b3f7ffb2e64c8d1157ecd5e3b199af1ae430`,
`apps/channels/api_views.py:1017-1035`, checks effective channels first, then M3U
associations, returning the two recognized `error` strings. This does not identify
the reporter's deployed Dispatcharr version or the missing historical batch.

## Controlled regression checks

`backend/tests/routers/test_jtrlf_group_delete_policy.py` exercises the actual
Dispatcharr client HTTP/parser, bulk background job, poll serialization, SQLite
hide/restore and journal path with only upstream HTTP substituted. It covers:

- Direct/bulk parity with auto-sync on and off, repeated hide, list and restore.
- An unavailable association lookup causing no upstream mutation.
- Explicit channel deletion followed by protected group hides.
- Manual nonempty and empty group deletion, plus a genuine controlled 400.
- A five-operation mixed batch with four applied, one failed and one partially
  applied operation; two moves remain after the rejected DELETE, attempted once.
- Fixed validation vocabulary and non-disclosure of unknown bodies/URLs/secrets.
- A second real SQLite writer winning between the initial query and insert.

The initial controlled regression produced seven failures and two direct-route
positive controls. The bulk guard defect was reproduced: the member moved before
the provider association caused DELETE to fail. This is separate from, and does
not establish, the historical report of 82 applied / two failed operations.

The mixed test writes its actual serialized outcome and downstream state to its
private pytest directory for a disposable browser smoke. The smoke replays that
JSON through real `fetchJson`, `bulkCommit` polling, `useEditMode` and the rendered
`EditModeExitDialog`. Its small harness copies App's failure-to-message mapping;
it is not full App bootstrap or a deployed/operator-container test. Delivery and
deployed acceptance remain the parent session's responsibility.
