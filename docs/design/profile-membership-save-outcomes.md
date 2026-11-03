# Confirmed Channel Profile membership saves

Tracks `enhancedchannelmanager-71imf`.

Manage Channels' immediate Save Changes and Apply to Selected paths use
`frontend/src/services/profileMembership.ts`. It snapshots the complete explicit
intent, preflights a fresh read, sends serial homogeneous batches of at most 100,
and reads membership after requests settle. Request fulfillment alone is not
proof that an enabled/disabled state landed.

Every read reconciles the **original** submitted intent, not only the unresolved
IDs from the previous read. This catches membership changed after an earlier
confirmation during a legacy fallback. The UI displays confirmed membership
counts separately from desired pending toggles. Failed final readback is shown
as unknown and retains the original intent for a safe retry.

Retries read before writing so a lost reply does not by itself repeat an already
landed change. Older update-only bulk endpoints receive one serial individual
fallback for acknowledged-but-unapplied explicit IDs; rejected bulk requests do
not trigger an automatic replay storm. All calls keep the existing authenticated
API client. Fresh membership reads use `cache: no-store`.

Editing, navigation and both submission entry points are guarded while saving
and checking. Apply to Selected does not submit unrelated local toggles. Edit
Mode keeps staging its established bulk-commit operations without immediate
destination writes. Readback is a best-effort snapshot, not a global transaction
or guarantee against another actor changing membership after the last read.

## Verification and limits

- Component-to-production-client HTTP tests prove large batches, partial writes,
  failed/lost responses, failed readback, original-intent reconciliation for both
  enables and disables, retries, reversals, partial selections and staged edits.
  Regressions were demonstrated against the base, held candidate and exact
  prematurely-pruned-intent mutant.
- Existing real recorded Dispatcharr enabled-only membership data is parsed by
  production API code; absence means not enabled, not proof of a disabled row.
- Backend ASGI-to-real-DispatcharrClient-to-loopback HTTP tests exercise adapter
  translation, upstream authentication and modern/update-only compatibility.
- Chromium rendered the built App and exercised four isolated cases, including
  the reported 214-change scenario: actual membership 198 of 316, 96 unresolved
  changes, then 102 of 316 after retry. Other cases cover lost responses, unknown
  final reads and terminal readback contradicting an earlier confirmation.

Browser and backend peers are synthetic, independently exercised seams. This is
not one browser-to-live-Dispatcharr stack or verification of a running production
installation. No production credentials or upstream services were accessed.

Neighboring selection-bar, post-creation and staged-commit reporting is not
silently claimed fixed. Source-level response-versus-outcome risks on those
surfaces are recorded for separate validation in `enhancedchannelmanager-0m06f.3.4`.
Other profile-CRUD dialogs do not perform this membership fan-out operation.
