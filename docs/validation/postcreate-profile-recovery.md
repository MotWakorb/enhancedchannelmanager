# Post-create profile recovery — `0m06f.3.4.1`

## Scope and contract

This candidate repairs App's post-create membership handling, not selection-bar
reporting or the staged operation executor. Committed channel identities come
from `useEditMode`'s temp-ID map, not channel-number lookup. Only mapped creates
enter the post-commit queue; Undo and partial-commit behavior remain with the
existing staged ledger.

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
  differing from channel numbers, partial creation outcomes, and Undo. The
  original unfixed implementation produced three intended missing-feedback
  failures with two existing passes; no import/setup failures.
- **Service logic:** `services/createdChannelProfiles.test.ts` exercises complete
  selected/unselected visibility, explicit empty selections, missing/malformed
  profiles, concurrent changes before final read, and serial 100-ID slices.
- **Lifecycle:** `hooks/useCreatedChannelProfiles.test.ts` covers reload without
  replay, operator/destination isolation, late completion, serialization, storage
  failures, sanitized destination keys and discard.
- **Consumer HTTP integration:** `services/createdChannelProfiles.http.test.ts`
  uses production service/API/fetch code against an isolated loopback socket
  peer. It covers ignored/rejected writes, failed readback, and a landed write
  followed by a dropped response. It is not a real upstream or backend test.
- **Rendered Chromium:** `e2e/post-create-profiles.spec.ts` drives the normal
  manual-create dialog and asynchronous bulk-commit polling contract against
  a private routed peer. It checks reload, membership-only retry, server-assigned
  numbering, lost replies and compact notice layout. Each unresolved case
  produces `unresolved-profile-intent.png` in the Playwright output directory.

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
```

## Limits

Fresh enabled-only membership proves visible state at read time, not global CAS,
durability, or disabled-row existence. Operator/destination changes stop
subsequent requests and suppress old completion updates; already-sent writes
cannot be withdrawn. The saved queue is tab-local, not a durable server journal.
Creation results that never reach App's commit callback remain governed by the
existing bulk-commit flow; this repair does not redefine unknown creation outcomes.
No new endpoint, MCP tool, dependency, or production-service mutation is required.
