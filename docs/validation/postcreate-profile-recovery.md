# Post-create profile recovery — `0m06f.3.4.1`

## Scope and contract

Candidate `0.18.2-0072` covers the three frozen PR #1071 blockers and the related
regression evidence. Committed channel identities come from `useEditMode`'s
temp-ID map, not channel-number lookup. On receipt, App saves membership intent
and the staged ledger retires those creates before later phases or channel
refresh. A failed sibling and unrelated updates retain their own outcome.
The picker, pre-Apply staged-intent restore, and accessibility follow-ups are
out of scope.

Settings supply a non-authorizing destination identifier. The profile catalog
used for staging is read from that destination; App keeps the identifier with
the staged profile IDs. Apply rejects mismatched selections locally, and the
queued worker compares the expected destination to the client it actually
selected, before writing anything. A mismatch returns an unapplied result and
does not produce a map or recovery binding.

The create worker issues an opaque binding from the requesting ECM operator
and the verified Dispatcharr client that produced the map. Recovery-only profile
routes require it on every read and mutation and use the same verified client
for the operation. The normal profile routes are unchanged. No credentials or
raw connection URL are added to the receipt. A stale callback saves only in its
original local queue; the queue key uses the settings this tab loaded, but
only the server-issued binding determines where a request may run. A stale
tab cannot redirect a recovery mutation. Older saved intent runs only on
explicit retry; an absent selected profile blocks the affected channel, not
its neighbors.

The recorded Dispatcharr response at
`backend/tests/fixtures/y3m6o1/dispatcharr_channel_profiles_mixed_membership.json`
contains nine `{id, name, channels}` profiles. `channels` is an enabled-ID list,
not a membership-row list. For probe channel `12418`, profiles 1–5, 7 and 8 are
enabled; 6 and 9 are absent. Profile channel counts in recorded order
`[1,2,3,5,6,7,8,9,4]` are `[51,1,1,1,0,1,1,0,1]`.
The service and HTTP tests parse these recorded bytes. Synthetic created IDs
and scripted faults are identified as such, not represented as live Dispatcharr.

## Evidence layers and repeatable checks

- **Actual App/useEditMode/component seam:** `App.bulkCreateStaging.test.tsx`
  exercises the normal manual-create callback, post-commit rejection, ignored
  writes, final read failure, and lost replies. Bulk tests cover committed IDs
  differing from channel numbers, and Undo. The mixed-create regression has
  an unmapped rejected sibling (not just a failed stream assignment), an
  unrelated update, and either failed channel refresh or a lost later batch
  reply. Tests also cover late operator handoff, a missing create result,
  and the actionable missing-profile notice on explicit retry. The mixed
  response retries only the rejected sibling and rejected update, even when
  another update was confirmed in the same response. A settings refresh does
  not re-label a staged selection. A rejected rename whose original number
  was superseded retries the name only, even after failed refresh. Partial
  range retirement preserves the original positions around a settled channel
  placement or deletion.
- **Service logic:** `services/createdChannelProfiles.test.ts` exercises complete
  selected/unselected visibility, explicit empty selections, missing/malformed
  profiles, a mixed missing/healthy batch, concurrent changes before final read,
  and serial 100-ID slices.
- **Lifecycle:** `hooks/useCreatedChannelProfiles.test.ts` covers reload without
  replay even when more channels are created, operator/destination isolation,
  late starts and completions, serial draining of new-only work, storage failures,
  sanitized destination keys and discard.
- **Consumer HTTP integration:** `services/createdChannelProfiles.http.test.ts`
  uses production service/API/fetch code against an isolated loopback socket
  peer. It covers ignored/rejected writes, failed readback, and a landed write
  followed by a dropped response. It is not a real upstream or backend test.
- **Backend route and client:** `test_profile_membership_http.py` covers
  binding provenance through the actual asynchronous create result, stale
  destination or upstream credentials, actual JWT operator changes with auth
  optional, and a swap while a bound mutation is reading its body. A real
  Dispatcharr adapter supplies membership traffic to a private synthetic peer.
  The async worker test pauses after queueing but before client selection,
  swaps to a peer with overlapping IDs, and checks that the worker refuses
  before creating a channel or issuing a binding. The bulk-commit router
  test verifies retirement indexes after updates are merged and reordered.
  The numbering receipt reports settled placements separately from wholly
  settled operations, and does not report compensated numbers as settled.
- **Rendered Chromium:** `e2e/post-create-profiles.spec.ts` drives the normal
  manual-create dialog and asynchronous bulk-commit polling contract against
  a private routed peer. It checks reload, membership-only retry, server-assigned
  numbering, lost replies, a scope change between verification and mutation,
  failed channel refresh after a successful write, and compact notice layout. Each unresolved case
  produces `unresolved-profile-intent.png` in the Playwright output directory.

### RED and sensitivity evidence

- The original pre-recovery App run showed three missing-feedback failures and
  two existing passes, not a fourth lost-reply failure.
- On PR #1071's review baseline, five added frontend expectations failed:
  known creates after failed refresh, stale callback writes, replay of
  restored work, failure to drain just-new work, and a missing profile blocking
  the healthy channel. The backend comparison against the same baseline
  reached the completed create and valid map but failed because the binding
  was absent.
- A temporary local mutation reused the pre-write profile snapshot in place
  of the final complete GET. Both the App and loopback HTTP landed-lost-reply
  tests failed because verified intent stayed pending. With the fresh read
  restored, both passed. The mutation was not retained.
- On the first remediation commit the queued-worker test returned a valid ID
  map from the replacement connection, and the failed-refresh test found the
  staged ledger missing. Both checks now pass: the worker refuses the stale
  scope and the ledger retains only the unresolved work.
- On `d09d386a`, the staged Apply/retry test resent superseded `#70` with
  the rejected rename after `#80` settled; the range test resent the settled
  middle member. With component-level receipts, the retry sends the rename
  alone and resumes the two rejected ranges at their original offsets.

```bash
cd frontend
npm test -- src/App.bulkCreateStaging.test.tsx \
  src/services/createdChannelProfiles.test.ts \
  src/services/createdChannelProfiles.http.test.ts \
  src/hooks/useCreatedChannelProfiles.test.ts \
  src/components/CreatedChannelProfilesNotice.test.tsx
npm run typecheck
npm run lint
cd ..
TMPDIR=/tmp/opencode npx playwright test \
  --config playwright.verification.config.ts e2e/post-create-profiles.spec.ts
scripts/backend-gate.sh --subset tests/routers/test_profile_membership_http.py \
  tests/routers/test_profiles.py tests/routers/test_channels.py \
  tests/auth/test_mcp_sidecar_boundary.py
```

## Limits

Fresh enabled-only membership proves visible state at read time, not global CAS,
durability, or disabled-row existence. A request already sent cannot be
withdrawn. The binding fences the configured connection, not replacement
upstreams indistinguishable at the same URL and credentials. A rotated ECM
signing secret or Dispatcharr credential can invalidate saved bindings; no
late refresh issues a replacement binding for old IDs. The saved queue is
tab-local, not a durable server journal. Unknown create results remain governed
by the existing bulk-commit flow. The browser and server regressions use
synthetic writes only; they are not production, publication, or deployment
evidence.
