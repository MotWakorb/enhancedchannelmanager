# enhancedchannelmanager-60r8c: Frozen Stale Attachment Contract

Owner: project-engineer. GitHub: #725. Base: `08bbde8b`.
Status: local reviewed implementation; bounded current-run code, DBA and security
reviews completed for the diff from `08bbde8b`. Parent final independent gates
passed. Ruff remains unavailable. Delivery authorized; commit-only handoff in progress.
Authority: PO implementation brief and bead notes, 2026-09-05, followed by explicit
commit, push and merge authorization. Project-engineer owns commit only; parent
owns push, PR, merge and in_progress status. No closure or live upstream writes
are authorized in this commit-only handoff.

## Approved Behavior

The per-rule Event Sync cleanup option is absent/off by default. Cleanup is
limited to attachments proven made by this current rule, with future provenance
bound to rule creation identity, channel UUID and stream account where available.
Already-attached no-ops do not adopt ownership. Old rule IDs alone, expired or
corrupt history, restored/reused identities and uncertain ownership preserve the
attachment with an explanation in preview and persisted history.

The approved first version is cross-account only: require channel
`auto_created` exactly true, valid authoritative `auto_created_by`, and a valid
candidate `m3u_account` different from that owner. Preserve every same-account
or unknown case with reasons. Attachment order, names and `source_stream` are
not parent authority. Dispatcharr native parent attachments are protected.

## Frozen Acceptance Matrix

These are requirements, not claims of implemented guarantees. Tests must prove
the production seams as well as pure decisions before status changes.

| Scenario | Required outcome | Proof layer |
| --- | --- | --- |
| Option absent or off | Existing behavior unchanged | Configuration and execution |
| Native channel renamed in place | Remove this-rule stale cross-account attachment; keep current matches and every owner-account stream | Stateful fake upstream with real SQLite |
| Complete successful evaluation, zero replacements | Stale removal remains possible | Execution lifecycle |
| Manual, preexisting, other-rule membership | Preserve; no ownership adoption | Provenance lifecycle |
| Old proof, rule reuse, expired/corrupt history, uncertain identity | Preserve and explain | SQLite, preview and history |
| Same-account or missing/malformed native/account metadata | Preserve and explain | Adversarial authority tests |
| Truncation, malformed pages, missing groups, read/dependency failure | No detach | Fetch-to-execution integration |
| Ambiguous/failed parsing or unavailable master | Preserve | Existing scorer integration |
| Already-attached filtered out of resolver; attachment cap reached | Neither absence nor cap proves stale; evaluate eligible owned attachments explicitly | Planner/scorer integration |
| Repeat successful run | Idempotent | Stateful execution |
| Attach and detach, then rollback | Surgical inverse preserves unrelated current memberships, with snapshot present or expired | Real SQLite and stateful upstream |
| Journal prewrite failure | No removal | Failure injection |
| Missing entities, uncertain HTTP, partial rollback | Explicit uncertainty/failure; no false success, further cleanup blocked on uncertain mutation | Failure/recovery lifecycle |
| Preview | Same decisions as execute, zero upstream mutations | Router and browser/API seam |
| History | Visible would-detach, detach and skip reasons | Persist/serialize/render |

## Implementation Boundary

Reuse the existing scorer, Event Sync phases, JSON configuration and journal
where feasible. A small shared planner is acceptable for preview/execute parity.
Durable recoverable detach intent must commit before upstream mutation, with
confirmed outcome afterward; journal failure must not degrade to warning-only
removal. Do not hold SQLite transactions across HTTP. Transaction writes need
private connections rather than shared StaticPool DBAPI transactions. Review
schema, query and transaction changes with DBA; review authority and malformed
identities with security. Add an index migration only with concrete query evidence.

Freshly reread and revalidate before a streams-only PATCH; preserve unrelated
current membership and order. Dispatcharr's list-replacement API has no CAS:
this cannot be claimed atomic against concurrent external writers. Document the
remaining read/PATCH race rather than inventing an upstream API guarantee.

Explicit exclusions: same-account cleanup, protected-anchor UI, order/name/source
heuristics, general cleanup engine, retention extension, ownership backfill,
new scheduler, new dependency, matching redesign and unrelated changes.
Review may block contract failures, introduced regressions, security or data loss;
it must not silently expand this freeze. Material scope decisions return to PO.

## Sources and Evidence

- Researcher upstream evidence supplied by the parent (not independently reverified yet):
  [Dispatcharr 0.28.2 channel fields](https://github.com/Dispatcharr/Dispatcharr/blob/v0.28.2/apps/channels/models.py#L365-L376),
  [native auto-sync map](https://github.com/Dispatcharr/Dispatcharr/blob/v0.28.2/apps/m3u/tasks.py#L2294-L2314)
  and orphan cleanup at lines 3019-3042. Native ownership is account-based;
  no singular origin FK. The PO reports the same targeted predicates in
  0.28.0/.1 and 0.29/.30; ECM's tested series is 0.28, not a broader guarantee.
- Available recorded channel response: `backend/tests/dbas/test_channels_importer.py`
  (PO identifies lines 353-387 as captured from 0.28.2). At least one new test
  must parse this actual shape. Survey other supported fixtures before coding.
- `backend/tests/fixtures/dispatcharr_stream_provider_channel_number.json` has
  no explicit capture provenance according to the PO. Treat it as a shape fixture,
  not independently verified live evidence. No live distribution/capture or
  production mutation is authorized by this plan.
- Investigation pointers to reread before implementation: resolver first-stream
  name extraction and already-attached filtering; executor merge journal and
  no-op branches; engine fetch completeness, dependency fallbacks, summary and
  surgical rollback; journal buffering and cleanup retention. These pointers
  are inherited evidence, not proof that remedies already exist.

## Verification and Handoff

Write regression tests first, demonstrate red, then implement. Use real SQLite
and a stateful fake upstream lifecycle, plus recorded-shape parsing and a rendered
browser flow on the exact build. No live Dispatcharr writes. Run targeted tests,
`scripts/backend-gate.sh`, frontend lint/typecheck/test:coverage/build,
isolated-port exact-build Playwright and strict MkDocs. Install root/frontend
lockfile dependencies independently in this worktree, without changing locks.

Backend environment: `ECM_PYTHON=/home/lecaptainc/ecm/enhancedchannelmanager/.venv/bin/python`,
`ECM_TEST_CONFIG_ROOT=/tmp/opencode/ecm-60r8c/.test-config`,
`TMPDIR=/tmp/opencode/ecm-60r8c`, `PYTHONDONTWRITEBYTECODE=1`.
Own resources exclusively; do not concurrently clean/reset a running proof.
All waits remain foreground and synchronous through terminal gate results.
Final report names exact files, commands/results, evidence limitations, remaining
resources and review gaps. Parent independently verifies; no self-review signoff.

## Intake Findings and Pending Boundary

Verified in this worktree at `08bbde8b`, before tests or product edits:

- The merge/no-op, resolver filtering, fetch fallbacks, buffered journal and
  surgical rollback paths identified by the PO exist as described. The source
  modules are top-level `backend/channel_pipeline_engine.py` and
  `backend/channel_pipeline_executor.py`, not a `channel_pipeline/` package.
- The channel fixture's capture comment explicitly identifies Dispatcharr 0.28.2
  and 2026-07-27, with representative names/IDs and verbatim structure. Its
  `streams` is empty while `source_stream` is populated; do not manufacture a
  native-parent relation from that derived field. The stream shape fixture has
  integer `m3u_account` and `channel_group`, without a capture-provenance comment.
- Filename survey under `backend/tests/fixtures/` found additional recorded
  OpenAPI, settings, EPG, DVR, recording, account and profile fixtures. This is
  a fixture inventory, not a live field-value distribution or validation of
  every fixture's content. The recorded OpenAPI and account fixtures have not
  yet been inspected for this feature.
- A second execution surface needs an explicit scope disposition:
  `backend/routers/channel_pipeline.py:1765-1803` runs the real engine against
  `PlanningDispatcharrClient` with `plan_only=True`. The commit route at
  lines 1957-2030 stores snapshot-based evidence, replays generic writes and
  rebuilds journal entries afterward. This is not the ordinary executor's
  per-merge journal lifecycle.
- `backend/services/pipeline_write_plan.py:225-284` compensates completed
  channel updates with their full preimage after replay failure. Its journal
  reconstruction at lines 288-356 uses category `auto_creation`, does not carry
  Event Sync rule provenance, and represents stream removals as `remove_stream`.
  Routing new cleanup through this mechanism unchanged would not meet the
  frozen durable-detach and surgical-recovery requirements. This conclusion is
  source-traced, not yet demonstrated by a regression test.

PO decision, 2026-09-05 continuation: **Support both paths (Recommended)**.
Include narrowly scoped Event Sync provenance and surgical compensation in
prepared execution so normal and prepared runs honor identical cleanup guarantees.
Planning/dry-run performs no real mutation or durable intent write. Persist intent
at real replay, not fake planning. Partial-write recovery remains surgical. Other
pipeline semantics remain unchanged; no generic prepared-pipeline rewrite.
This decision was approved before tests. Local implementation and verification
are recorded below; the approved behavior and exclusions remain the boundary.

At implementation intake, independent DBA and security review required parent
dispatch. Those reviews are now completed as recorded in Final Delivery State;
the implementation evidence below is not self-review signoff.

## Local Implementation

- `detach_stale_streams` is a strict per-rule boolean, absent/off by default.
  Existing attach behavior remains selected when off. Future ordinary merge
  provenance carries rule creation time, channel UUID and stream account where
  available; it is not silently promoted to confirmed durable ownership proof.
- `services/event_sync_cleanup.py` shares cleanup planning across preview,
  normal execution and prepared planning/replay. It explicitly evaluates owned
  attached streams through the existing resolver, including master-group streams
  that the ordinary attach path filters out. Attach caps are not stale evidence.
- Cleanup reads validate pagination, IDs, configured-group presence, dependencies,
  rule creation identity, channel UUID, native owner and candidate account. The
  native guard protects every same-account attachment. Current-rule matching can
  remove stale cross-account attachments with zero replacements.
- Cleanup-enabled attaches and detaches use existing journal JSON with a
  versioned operation and `intent`, `confirmed`, `cancelled`, `uncertain` or
  `reverted` state. A private NullPool connection commits intent before PATCH;
  no database transaction spans HTTP. The final reread checks channel, stream,
  native authority and order-derived matching identity, and rebases membership
  against unrelated current IDs. A known pre-PATCH cancellation is distinct from
  an uncertain PATCH outcome. An unresolved intent prevents further writes on
  that channel. Retention is not extended and old ownership is not backfilled.
- Prepared planning records semantic Event Sync operations without writing
  intents or mutating real upstream state. Real replay commits intents and
  confirms outcomes. The existing generic journal reconstruction skips these
  already-journaled operations. Confirmed Event Sync writes use surgical
  compensation; unrelated generic replay behavior is not redesigned.
- Engine rollback uses the journal's inverse against current memberships with
  or without a snapshot. Missing/partial history, missing entities and partial
  rollback do not report success. Mixed non-stream rollback and overlapping
  generic prepared compensation refuse a whole-preimage restore instead of
  overwriting unrelated current memberships; this is an explicit recovery
  limitation, not a claim of automatic mixed-run recovery.
- The editor persists the option, marks the cleanup risk, and includes saved-rule
  context (`cleanup_rule_id`) for inline cleanup previews. Preview and execution
  details render would-detach, detached and preserve reasons. No protected-anchor
  UI, scheduler, dependency, schema migration or retention extension was added.

## Verification Evidence

Verified on the uncommitted isolated worktree at base `08bbde8b`, not deployed:

| Layer | Command | Final result |
| --- | --- | --- |
| Cleanup logic, SQLite lifecycle, executor, preview router, prepared commit and rollback | `scripts/backend-gate.sh --subset tests/services/test_event_sync_cleanup.py` | 45 passed |
| Canonical backend gate | `scripts/backend-gate.sh` | 12,915 passed; 3 documented skips; 2 deselected; 82.33% coverage; 756.33 seconds |
| Frontend lint | `npm run lint` in `frontend/` | Passed |
| Frontend types | `npm run typecheck` in `frontend/` | Passed |
| Frontend coverage | `npm run test:coverage` in `frontend/` | 259 files, 3,688 passed; 59.82% statements |
| Frontend build | `npm run build` in `frontend/` | Passed; existing large-chunk warning |
| Exact-build browser | Command below | 1 Chromium test passed; desktop and 390px screenshots inspected |
| Documentation | `python -m mkdocs build --strict --site-dir /tmp/opencode/ecm-60r8c/.60r8c-docs-site` | Passed; internal handoff notes are excluded by existing MkDocs configuration |
| Diff whitespace | `git diff --check` | Passed |
| Python Ruff | Project interpreter `-m ruff check` on the new Python files | Unavailable: venv has no Ruff module; no environment/dependency changes made to install it |

TDD evidence: missing implementation initially failed collection; the executor
rename regression then failed with `[1, 2, 3]` instead of `[1, 3]`; prepared replay
and option validation failed before wiring. Frontend tests failed for the absent
toggle and cleanup results. Disabling the same-account guard deliberately caused
two tests to fail, including an actual would-detach of the protected account;
the guard was restored before final gates. Browser inspection exposed missing
group-settings authority; its regression was demonstrated red before the fix.
The last-read/order-derived matching-identity regression also failed before its
guard. Final backend and frontend gates were rerun after those fixes.

The browser test uses the actual rule CRUD and preview/history routers with
file-backed SQLite. A dedicated fixture endpoint invokes the actual executor
synchronously against a stateful fake upstream, then persists the history row;
it does not exercise the production queue/scheduler. Backend tests separately
exercise prepared commit/journaling/rollback, using a real executor-generated
plan and a scoped planning-traversal adapter. The native response test parses
the relevant JSON field subset from the recorded 0.28.2 channel response. The
stateful lifecycle data and provider-group settings are synthetic, not a newly
captured live distribution. No live Dispatcharr write or production check ran.

The first browser fixtures had incomplete unrelated API stubs and a missing
router prefix; those were corrected without application changes. A teardown
race was fixed by awaiting the actual execution-details response and draining
route handlers before stopping the fixture. Every verification command returned
synchronously; no background gate watcher was armed.

### Reproduction

Workdir `/tmp/opencode/ecm-60r8c`; Node installed independently from the root
and frontend lockfiles using `npm ci`, with the Node 24.13.0 installation on PATH.
Locks and shared node dependencies were not modified. Backend environment is
the one recorded above. Reuse it for the browser fixture too:

```sh
export PATH=/home/lecaptainc/.local/share/fnm/node-versions/v24.13.0/installation/bin:$PATH
export ECM_PYTHON=/home/lecaptainc/ecm/enhancedchannelmanager/.venv/bin/python
export ECM_TEST_CONFIG_ROOT=/tmp/opencode/ecm-60r8c/.test-config
export TMPDIR=/tmp/opencode/ecm-60r8c
export PYTHONDONTWRITEBYTECODE=1
scripts/backend-gate.sh
E2E_START_SERVER=true E2E_EXACT_BUILD=true PLAYWRIGHT_HTML_OPEN=never npm run test:e2e -- e2e/event-sync-cleanup.spec.ts --project=chromium --workers=1 --retries=0
```

The browser owns preview port 4173 exclusively and its API fixture binds a
separate ephemeral loopback port. Both stop before the test command returns.
Run frontend gates sequentially in `frontend/`. All final gate logs are retained
at the worktree root: `backend-gate-60r8c.log`, `frontend-gates-60r8c.log`,
`browser-gate-60r8c.log`, `docs-gate-60r8c.log`. Screenshots and the browser API log are under
`test-results/` and the Playwright report.

### Exact Product File Manifest

```text
backend/channel_pipeline_engine.py
backend/channel_pipeline_executor.py
backend/channel_pipeline_schema.py
backend/routers/channel_pipeline.py
backend/services/event_sync_cleanup.py
backend/services/event_sync_resolver.py
backend/services/pipeline_write_plan.py
backend/tests/fixtures/event_sync_cleanup_server.py
backend/tests/services/test_event_sync_cleanup.py
backend/tests/unit/test_channel_pipeline_engine.py
backend/tests/unit/test_selected_pipeline_lifecycle.py
docs/60r8c_stale_event_streams_plan.md
docs/event_sync.md
e2e/event-sync-cleanup.spec.ts
frontend/src/components/channelPipeline/ChannelPipelineTab.tsx
frontend/src/components/channelPipeline/EventSyncPreviewPanel.test.tsx
frontend/src/components/channelPipeline/EventSyncPreviewPanel.tsx
frontend/src/components/channelPipeline/EventSyncRuleEditor.test.tsx
frontend/src/components/channelPipeline/EventSyncRuleEditor.tsx
frontend/src/types/channelPipeline.ts
frontend/src/types/eventSync.ts
```

### Remaining Resources and Review

Do not stage generated resources: root/frontend `node_modules`, `.npm-cache/`,
`.test-config/`, `.60r8c-docs-site/`, `pytest-of-lecaptainc/`, `node-compile-cache/`,
`playwright-transform-cache-3000/`, `ecm-debug-bundle-*.tar.gz`, logs, coverage,
frontend build output and browser reports. They are dedicated to this worktree.
No live server, watcher, container or upstream resource is needed for handoff.

Independent parent verification and bounded code, DBA transaction/query and
security authority reviews are completed; see Final Delivery State. Ruff was not
run successfully. The upstream
no-CAS concurrency limitation remains; neither passing tests nor this report
claim atomicity against external writers. No commit, push, PR, merge, deployment
or issue-status change was performed. The parent retains the in-progress bead.

## Four-Defect Remediation Evidence

2026-09-05, fresh project-engineer remediation in `/tmp/opencode/ecm-60r8c`.
This section records only the four demonstrated introduced defects supplied by
the parent. It does not change the approved scope, acceptance matrix, exclusions,
or no-CAS limitation. Prior verification above predates these fixes.

### Changes This Run

- Unknown PATCH outcome: `UncertainMutationError` distinguishes a started PATCH
  whose outcome is unknown from a known pre-PATCH cancellation. The ordinary
  executor persists `uncertain`, not `preserve`, for response loss. The shared
  preview/history renderer counts uncertain rows separately and no longer claims
  that uncertain attachments were preserved. Prepared commit's existing failure
  path is pinned: HTTP 502, failed execution, uncertain journal and recovery
  error, no fabricated successful cleanup summary.
- E2E collection: replace the spec's import-time throw with a static Playwright
  skip annotation using the same three prerequisites. Unsupported modes collect
  without aborting and skip before page/fixture setup or mutations. No general
  E2E configuration or framework change.
- Stream group drift: planned detach inputs now include `stream_group`. Both
  immediate and post-durable-intent reads compare it. A return from out-of-scope
  group 30 to matching group 20 cancels before PATCH on normal and prepared paths.
- Missing ownership preimage: ownership requires an explicit list of unique,
  positive, signed-64-bit integer IDs. Missing/NULL membership, booleans, strings,
  zero and duplicate IDs cannot establish ownership. Missing/invalid preimages
  explain preservation; invalid JSON still fails closed. Prepared replay
  revalidates this proof and refuses previously planned detaches after corruption.

Product/test files edited this run, relative to the worktree:

```text
backend/channel_pipeline_executor.py
backend/services/event_sync_cleanup.py
backend/tests/services/test_event_sync_cleanup.py
e2e/event-sync-cleanup.spec.ts
frontend/src/components/channelPipeline/EventSyncPreviewPanel.test.tsx
frontend/src/components/channelPipeline/EventSyncPreviewPanel.tsx
frontend/src/types/eventSync.ts
docs/60r8c_stale_event_streams_plan.md
```

### Regression And Gate Results

Tests were added before product fixes. Red evidence demonstrated both group-drift
paths proceeding instead of cancelling, NULL/empty preimages authorizing detach,
response-lost history claiming `preserve`, and the renderer claiming uncertain
attachments were preserved. Non-isolated `--list` failed with the top-level throw.
The initial backend test run also exposed two prepared-error assertions matching
the underlying error instead of the existing `PartialReplayError` wrapper; those
test expectations were corrected, not product behavior.

| Check | Command | Terminal result this run |
| --- | --- | --- |
| All cleanup feature tests | `scripts/backend-gate.sh --subset tests/services/test_event_sync_cleanup.py` | 67 passed |
| Preview/history and editor tests | `npm exec vitest run src/components/channelPipeline/EventSyncPreviewPanel.test.tsx src/components/channelPipeline/EventSyncRuleEditor.test.tsx` in frontend | 108 passed |
| Canonical backend | `scripts/backend-gate.sh` | 12,937 passed, 3 documented skips, 2 deselected; 82.36% coverage; 752.29 seconds |
| Frontend lint | `npm run lint` in frontend | Passed |
| Frontend types | `npm run typecheck` in frontend | Passed |
| Frontend coverage | `npm run test:coverage` in frontend | 259 files, 3,689 passed; 59.82% statements |
| Frontend build | `npm run build` in frontend | Passed; existing large-chunk warning |
| Exact browser positive control | Exact-build command in Reproduction above | 1 Chromium test passed, no retries; 5.2 seconds |
| Diff whitespace | `git diff --check` | Passed before this evidence append |
| Python Ruff | Project interpreter `-m ruff check` on the three changed Python files | Unavailable: no Ruff module; no dependencies installed |

Collection proof used private scratch config
`.test-config/remediation-playwright.cjs`: only this spec, list reporter, private
output directory, no web server. With `ECM_PYTHON` and `E2E_EXACT_BUILD` unset,
`E2E_BASE_URL=http://127.0.0.1:49199`, both external mode (`E2E_START_SERVER`
unset) and dev mode (`E2E_START_SERVER=true`) returned one collected test using
`npm run test:e2e -- --config=.test-config/remediation-playwright.cjs --list`.
Executing the external-mode private configuration with `--workers=1 --retries=0`
returned one skipped test without starting fixtures or connecting to that URL.
The exact positive control used the repository configuration, not this private
collection config.

Environment remained the project Python 3.12.3 interpreter and backend variables
recorded above, plus Node 24.13.0 on PATH and the existing independent root/frontend
lockfile dependencies. No dependency or lockfile changes. Each gate waited in the
foreground until its terminal result; no concurrent cleanup or background watcher.
Backend tests use isolated temporary SQLite and fake upstream state. Browser
preview and fixture servers terminated with the successful test command.

Full backend and frontend coverage command output is retained by the tool runner
at `/home/lecaptainc/.local/share/opencode/tool-output/tool_072b0feef001gRSA9yA9AQLkWj`
and `/home/lecaptainc/.local/share/opencode/tool-output/tool_072b2365a001FjETDHZLEPZ9F9`.
Scratch config, generated test/build/coverage artifacts and logs are not product
changes. No commits, push, PR, merge, git-status command, issue-status changes,
live Dispatcharr writes, other-worktree mutations or resource cleanup performed.
Parent-owned code/DBA/security delta reviews are now completed as recorded below;
this remediation section is regression and gate evidence, not self-signoff.
The remediation-time strict docs result is recorded below.

Strict documentation gate: project interpreter `-m mkdocs build --strict
--site-dir /tmp/opencode/ecm-60r8c/.60r8c-docs-site` passed in 1.08 seconds.
Existing MkDocs configuration excludes internal handoff notes from the site.

## Final Delivery State

2026-09-05 handoff, based on the parent's supplied final review and independent
verification results. Review was bounded to code from the current run, the diff
from `08bbde8b`: exactly four initial findings, fixed in one remediation round,
followed by delta-only final review. No remaining findings, broader review scope,
new goalposts or acceptance changes.

| Final review | Session | Bounded result |
| --- | --- | --- |
| Code | `ses_f8d3aaeb2ffe8ECvavrblv2IN0` | Approved: uncertain-status reporting and E2E collection fixes |
| DBA | `ses_f8d3aaea2ffeerf9ZO54QHPk3Q` | Pass: 67 independently run real-SQLite tests |
| Security | `ses_f8d3aae92ffeWl4JjB06Zku0Dm` | SEC1 pass: 63 independent cases |

Parent final independent verification supersedes the earlier gate snapshots:

| Gate | Parent final result |
| --- | --- |
| Full `scripts/backend-gate.sh` | 12,937 passed; 3 skips; 2 deselected; 82.36% coverage |
| Frontend coverage, lint, typecheck and build | 3,689 tests passed; all listed gates passed |
| Actual exact-build Chromium E2E | 1 passed; 5.3 seconds |
| `git diff --check` | Passed before this docs-only handoff; no product changes after verification |

These are parent-supplied results, not test reruns by this docs-only handoff.
Ruff remains unavailable as previously documented; no dependency installation
and no new blocker. The no-CAS limitation and existing evidence limitations remain.

Delivery proceeds on `feat/60r8c-stale-event-streams` in
`/tmp/opencode/ecm-60r8c` under explicit PO commit, push and merge authorization.
Project-engineer performs the commit only; parent owns push, PR and merge.
Scope stays frozen to normal
and prepared execution, cross-account cleanup only. Bead `60r8c` remains
`in_progress`, preserving its original description, acceptance criteria and prior
notes. GitHub #725 remains open pending eventual merge; this commit-only handoff
does not authorize deployment or closure. The unrelated closed
parent `u0ko6` / #775 and its successful #982 post-merge result are not changed.
