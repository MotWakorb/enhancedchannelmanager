# Profile follow-up validation

Tracks `enhancedchannelmanager-0m06f.3.4` and `enhancedchannelmanager-3js2z`.
These are bounded validation findings, not additional fixes or deployment proof.

## Post-creation membership: confirmed defect

On combined candidate `85b60808`, the real App/useEditMode creation/commit flow
was exercised with scripted API responses and the recorded enabled-only profile
shape. Two cases fail the required operator-feedback assertion:

1. Membership write resolves successfully but destination membership is absent.
2. Membership write rejects with a synthetic destination failure.

Both first pass the creation, actual membership-write and terminal profile-refresh
preconditions. Apply closes, destination membership remains absent, and no
unresolved/failed-profile message is rendered. Source inspection shows the
postcommit loop catches individual failures and clears its pending assignment
ref unconditionally. The narrow implementation child is
`enhancedchannelmanager-0m06f.3.4.1`; no fix is claimed by this document.

The [manual reproducer patch](postcreate-membership-reproducer.patch) preserves
the desired RED guards for that child. Apply it only in a disposable checkout
of this candidate, then from `frontend/` run:

```bash
npm test -- --run src/App.bulkCreateStaging.test.tsx \
  --testNamePattern='keeps post-create profile intent' --maxWorkers=1
```

Expected on the unrepaired candidate: two intended failures, zero import/setup
errors. The patch is diagnostic evidence, not a disabled passing regression or
an assertion that the defect is repaired. Promote the guards to automatic tests
when the implementation is added. Tests use component/API mocks, not live
Dispatcharr, a complete backend/browser stack, or production credentials.

## Selection-bar risk: distinguish reachable paths

Normal App wiring always supplies `stageSetProfileMembership` through the
required ChannelManagerTab prop to ChannelsPane. The selection bar renders only
in Edit Mode, so its membership operation stages in the deployed path. The
response-counting immediate fallback can be exercised only by omitting that
staging prop in a standalone component configuration. Do not label this source
risk as a reproduced normal-App defect or rewrite staged commit/undo reporting
without separate evidence. Existing staged-immediate tests pin this distinction.

## Escape failure: not reproduced in bounded contention checks

The unchanged `Decide later, close, and Escape` case was run 12 times on Node
22.23.3 with coverage: four concurrent test processes pinned to one allowed CPU,
one Vitest worker per process. Every selected Escape assertion passed; each run
has zero test failures/errors and 14 cases deselected by the name filter.

All selected-run processes exit 1 because whole-tree coverage thresholds remain
enabled (about 0.5% coverage from one case). These are not full-gate passes. The
combined 3,812-test frontend coverage suite also passes, but neither observation
identifies the cause of the earlier unbounded-run failure. No assertion, timeout,
focus handling or production code was changed. Keep `3js2z` open pending a
reproducible failure or a separately justified disposition; do not invent a fix.
