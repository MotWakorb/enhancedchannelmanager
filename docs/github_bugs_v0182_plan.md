# GitHub Bugs v0.18.2 Batch Plan

## Delivery Boundary

- Worktree: `/tmp/opencode/ecm-github-bugs`.
- Branch: `fix/github-bugs-v0182`; starting commit: `8edf7d0d`.
- Sequential work in the order below, with one final merge for the batch.
- Focused per-bead commits are permitted. The parent owns tracker transitions,
  push, PR, final merge, and issue closure. Keep GH975 in progress until then.
- Run focused tests per bead; run canonical full gates once the batch is complete.
- No live Dispatcharr writes, userdata changes, or shared database cleanup.

## Ordered Batch

| Order | GitHub Issue | Status at Dispatch | Scope |
| --- | --- | --- | --- |
| 1 | #801 | Prior fix verified, per parent handoff | Parent reports 273 backend and 4 MCP tests; closure deferred to final merge. |
| 2 | #975 | Active | Scheduled broad pipeline must not require rule `run_on_refresh`. |
| 3 | #856 | Implemented; focused verification complete | Existing Channel Group Is selects names and serializes integer IDs; assigned-channel semantics retained. Batch typecheck blocker noted below. |
| 4 | #970 | Not reproduced; regression coverage verified | Current code preserves false through save/reopen and persisted merge execution. Reporter-build confirmation remains pending. |
| 5 | #968 | Implemented; focused verification complete | Explicit null clears channel sorting; omitted fields retain prior values. |
| 6 | #962 | Pending | Not investigated in this dispatch. |
| 7 | #858 | Pending | Not investigated in this dispatch. |
| 8 | #969 | Pending | Not investigated in this dispatch. |

## GH968 Frozen Acceptance

Bead: `enhancedchannelmanager-0m06f.2.3.7`. Intake HEAD: `b063cc51`.
Source: https://github.com/MotWakorb/enhancedchannelmanager/issues/968
Read full bead and GitHub body; both have zero comments.

1. Select multiple configured rules, check Apply channel sort, choose No sorting,
   and Apply to selected. Explicit null clears persisted channel sorting (the
   actual API/model field is `sort_field`, not `channel_sort`). API GET and the
   reopened bulk UI must retain No sorting.
2. Unchecked Apply channel sort omits its fields and preserves prior sorting.
   Untouched bulk sections and unselected rules retain their prior values.
   Setting a new sort continues to work.
3. Preserve existing action semantics: clearing sorting changes rule configuration,
   not rule actions or historical execution records. The bulk-update journal must
   report the actual old sort to null change, not a successful no-op.
4. Tests first: reproduce against the real API and isolated SQLite, and exercise
   the actual rendered frontend bulk handler through hook and JSON serialization.
   Demonstrate behavioral red before the minimal backend patch. Distinguish
   explicit None from omission without a broad API redesign or other null changes.
5. Preserve previous commits; do not investigate/fix GH962, GH858, or GH969.
   Focused tests, lint and typecheck here; full canonical gates at batch end.
   No live writes, push, PR, merge, tracker transition, or status/issue closure.

## GH968 Implementation and Verification

Cause confirmed on `b063cc51`: `BulkRuleSettingsModal.handleSubmit` sends
`sort_field: null` for No sorting. The tab's actual bulk handler and rules hook
forward that patch to the API service, whose `JSON.stringify` retains null.
The backend's `model_dump(exclude_unset=True)` and reconstruction of the scalar
request also retain explicit null. `_apply_rule_scalar_updates` then discarded
it with `if request.sort_field is not None`, leaving the old database value.

The production change is one presence check plus its comment: use
`"sort_field" in request.model_fields_set`. This follows the existing nullable
field pattern and retains the existing empty-string clear. The shared PUT path
gets the same correct null behavior. No other nullable field, schema, action,
engine behavior, dependency, or frontend production code changed.

### TDD Evidence

- Before the production patch, six new API cases returned **2 failed, 4 passed**.
  Bulk clear returned `stream_name` and `quality` instead of null; single-rule
  PUT/GET also retained `stream_name`. Replacement and omission cases passed.
- The same six cases passed after the patch. They cross real FastAPI requests,
  request validation/serialization, isolated SQLite commit/reload, and real
  journal persistence. No validator, journal, or database operation is mocked.
- The three bulk cases compare every serialized rule field except `updated_at`,
  including actions, conditions, stream sorting, probing, and rule options.
  They verify an unselected rule and an existing execution-history row remain
  unchanged, exact journal before/after payloads share a batch ID, and repeated
  identical changes produce no additional journal entries.
- Three rendered jsdom tests exercise actual tab selection, modal controls,
  `handleBulkRuleSettingsApply`, hook, and API JSON serialization at MSW. They
  cover clear, replacement, and changing then unchecking Apply channel sort;
  assert exact request fields and selection reset; then remount the tab, fetch
  GET data, and reopen the bulk modal to verify its label. The HTTP boundary is
  mocked here, not a browser-to-Python end-to-end test. All three passed without
  frontend production edits after correcting a test-only orphan-checkbox label.
- Typecheck caught an unsupported test query `exact` option; removed it, then
  reran typecheck, lint, and the complete focused frontend set successfully.

### Final Focused Checks

From the worktree root:

```bash
env TMPDIR=/tmp/opencode ECM_PYTHON=/home/lecaptainc/ecm/enhancedchannelmanager/.venv/bin/python scripts/backend-gate.sh --subset tests/routers/test_channel_pipeline.py -k gh968
env TMPDIR=/tmp/opencode ECM_PYTHON=/home/lecaptainc/ecm/enhancedchannelmanager/.venv/bin/python scripts/backend-gate.sh --subset tests/routers/test_channel_pipeline.py tests/unit/test_channel_pipeline_sort.py tests/unit/test_channel_pipeline_engine.py
```

Results: **6 passed in 1.51s**, then **462 passed in 13.23s**. Each run used
private configuration created by the test harness under `/tmp/opencode` and
isolated SQLite. Backend runs were strictly sequential; no live writes occurred.

From `frontend/`, with
`/home/lecaptainc/.local/share/fnm/node-versions/v24.13.0/installation/bin` on PATH:

```bash
npx vitest run src/components/channelPipeline/ChannelPipelineTab.test.tsx src/components/channelPipeline/BulkRuleSettingsModal.test.tsx src/hooks/useChannelPipelineRules.test.ts src/services/channelPipelineApi.test.ts --silent
npm run typecheck
npx eslint src/components/channelPipeline/ChannelPipelineTab.test.tsx --max-warnings 0
```

Results: **188 passed in 9.11s**, typecheck **exit 0**, ESLint **exit 0**.
`/home/lecaptainc/ecm/enhancedchannelmanager/.venv/bin/python -m ruff check backend/routers/channel_pipeline.py backend/tests/routers/test_channel_pipeline.py`
could not run: **No module named ruff**. No tooling installation was attempted.

Full canonical gates remain at ordered-batch completion. Deployed browser and
reporter-build verification, independent review, shipping, and closure remain
pending with the parent. All invoked checks reached terminal status synchronously.
Prior commits were preserved; no later bug work or tracker/GitHub mutation occurred.

## GH970 Frozen Acceptance

Bead: `enhancedchannelmanager-0m06f.2.3.5`. Intake base: `d7ecf085`.
Source: https://github.com/MotWakorb/enhancedchannelmanager/issues/970
Read the full bead and GitHub body; GitHub returned zero comments.
GH975 fixture-only prerequisite is commit `51e82a94`: legacy null is isolated
with the existing unknown-cast test pattern without changing API types.
ScheduleEditor: 38 tests passed; frontend typecheck and file ESLint exited 0.

1. Render the real RuleBuilder, navigate to Targeting, uncheck scope, click Next
   and Save, and reopen from the serialized API response. Explicit false must
   remain in the request and the reopened checkbox must remain unchecked.
2. Cross real API create/update/GET and isolated SQLite persistence. False must
   survive creation and updates; omitted updates must not reset it. New rules
   with omitted scope retain the scoped default.
3. Execute persisted rules in preview and live modes against mocked Dispatcharr
   boundaries. Explicit false alone permits all-group lookup; scoped defaults
   retain target-group lookup. Preserve the existing pinned-group contract:
   a pin wins while scoped, is ignored while off, and the editor clears it on save.
4. Tests precede production changes. If current code already satisfies the report,
   record that evidence and add regression coverage, not an invented fix or red
   failure. No new scope policy, default, migration, dependency, or refactor.
5. Preserve GH975/GH856 production changes; do not investigate or fix GH968,
   GH962, GH858, or GH969. Run focused tests, frontend typecheck, and changed-file
   lint here; canonical full gates remain at the end of the ordered batch.
   No live writes, main-tree edits, push, PR, merge, or tracker/GitHub mutations.

## GH970 Investigation and Verification

No production fix was needed on intake HEAD `d7ecf085`. The reported v0.18.1
failure was not reproduced; its historical/deployed root cause is not established.
Do not treat this result as reporter confirmation or close the issue from it.

The traced path already distinguishes false from absence:

- `RuleBuilder` initializes scope with `?? true`, reads `event.target.checked`,
  and includes the boolean in `buildConfig`. Switching off serializes a null pin.
- `ChannelPipelineTab.handleSaveRule` passes the config to `useChannelPipelineRules`;
  the hook forwards it to the API service and retains the returned rule unchanged.
- `channelPipelineApi` JSON-serializes the POST/PUT body without dropping false.
- The backend create request defaults to true only when omitted; scalar updates
  use `is not None`, so false is assigned and committed. `to_dict` retains false.
- The engine passes the persisted boolean and pin to the executor. Off disables
  the lookup group filter; on retains the action-derived or explicit pinned group.

### Regression Evidence

Tests were added before any production edits. Initial unmodified-code probes
passed: 2 UI cases and 13 backend cases. UI coverage was then expanded to four
create/edit and pinned/Auto cases; backend execution now includes a real PUT from
true to false before loading and running the persisted rule.

The UI tests render the actual RuleBuilder in jsdom, use Next/Save, call the real
API service, capture JSON at MSW, and reopen from GET. They verify unchecked scope
and a cleared pin on reload and a second save. The HTTP server is mocked in this
layer; this is not a browser-to-Python end-to-end test.

Backend coverage crosses real FastAPI POST/PUT/GET, validation, isolated SQLite
commits/reloads, pipeline rule loading, and real executor lookup. Thirteen cases
cover create omission/false/true, update false, unrelated rename, pin clearing,
and preview/live lookup against an existing same-name channel outside the action
target group. Scoped default/true does not merge across groups; false does. A pin
overrides the action group while on and is ignored while off. Live-mode writes
are asserted against mocked Dispatcharr methods only; preview does not call those
write methods. No live instance or user data was touched.

Mutation checks establish that the regression tests can fail, not that the intake
code was broken:

- Temporarily replacing UI `?? true` with `|| true` caused **4 failures** at the
  reopened checkbox assertion.
- Temporarily replacing backend `is not None` with a truthiness check caused
  **6 failures, 7 passes**: true remained in responses/persisted rows after PUT false.
- Both mutations were restored with explicit patches. `git diff --exit-code`
  confirmed no production diff before the final green runs.

### Final Focused Checks

From the worktree root:

```bash
env TMPDIR=/tmp/opencode/ecm-gh970-tmp ECM_PYTHON=/home/lecaptainc/ecm/enhancedchannelmanager/.venv/bin/python scripts/backend-gate.sh --subset tests/routers/test_channel_pipeline.py tests/unit/test_channel_pipeline_engine.py tests/unit/test_channel_pipeline_executor.py tests/unit/test_g0uuf_duplicate_name_scoped_lookup.py
```

Result: **828 passed in 16.56s**. Private test configuration was created under
the dedicated TMPDIR; only one backend verification ran at a time.

From `frontend/`, with Node 24.13.0 on PATH:

```bash
npx vitest run src/components/channelPipeline/RuleBuilder.test.tsx src/components/channelPipeline/ConditionEditor.test.tsx src/hooks/useChannelPipelineRules.test.ts src/services/channelPipelineApi.test.ts src/components/ScheduleEditor.test.tsx --silent
npm run typecheck
npx eslint src/components/channelPipeline/RuleBuilder.test.tsx src/components/ScheduleEditor.test.tsx --max-warnings 0
```

Results: **253 passed in 8.32s**, frontend typecheck **exit 0**, ESLint **exit 0**.
Existing worktree-local dependencies/cache directories were used without changes
to dependencies, lockfiles, shared tooling, or global configuration.

Python lint remains unavailable: the supplied virtualenv reports `No module named
ruff`. Canonical full gates remain deferred to ordered-batch completion, as above.
No deployed browser/reporter-build acceptance or independent review was performed.
All invoked checks finished synchronously. No push, PR, merge, tracker transition,
GitHub mutation, or later-bug work occurred.

## GH856 Frozen Acceptance

Bead: `enhancedchannelmanager-0m06f.2.3.3`. Base: `90bf87f2` (GH975 preserved).
Source: https://github.com/MotWakorb/enhancedchannelmanager/issues/856
Intake read the full body, zero comments, and the attached screenshot. The
screenshot reports `channel_in_group requires a group ID (integer)`.

1. Keep the existing Channel Group / Is field and `channel_in_group` condition.
   Replace its misleading group-name text input with the existing searchable
   channel-group selector pattern: display names/counts, submit integer IDs.
2. Preserve the existing populated-group choices used for channel matching.
   An unselected value remains invalid; do not silently select a group.
3. Create, persist, reload, and edit the numeric condition without changing its
   meaning. Names and string IDs submitted directly to the API retain its clear
   integer-ID validation error.
4. Verify preview and live-mode evaluation through the existing pipeline:
   match the stream's assigned channel group, reject other groups and unassigned
   streams, and do not confuse provider stream groups with channel membership.
5. Prove the UI regression red before production edits. Add API persistence and
   engine coverage without mocking the validator/evaluator. Mock Dispatcharr
   boundaries only; no live writes. No new condition, engine changes, refactor,
   dependency, migration, or unrelated bug fix.
6. Run focused tests, frontend types, and changed-file lint. Canonical full gates
   remain deferred to batch completion. Parent owns all tracker/shipping actions.

## GH856 Implementation Evidence

Root cause: `ConditionEditor` rendered a text input with `Enter group name` and
passed `event.target.value` unchanged. Both `Sports` and typed `42` therefore
became JSON strings. The existing schema requires an integer, and the evaluator
compares that ID against the stream's assigned channel group. The existing
pipeline enriches channel associations from channel stream lists before matching.

Production change is four editor lines: select value type, reuse group options,
group placeholder, and searchability. The existing select handler already converts
IDs to numbers. No backend production change, new condition, or migration.

### Red and Baseline

- Before production edits, `npx vitest run src/components/channelPipeline/ConditionEditor.test.tsx -t GH856`
  returned **1 failed, 33 skipped**: the Channel Group value selector was absent.
- After correcting the test fixture's Log Match payload (message is a top-level
  action field, not nested `params`), the six new backend cases passed on the
  unchanged backend. They reproduce the exact error for names, string IDs, empty
  strings, and null; integer IDs create/reload successfully and match in both
  preview and live mode. Fixture-setup failures are not counted as bug red proof.
- The first green UI attempt reached search and exposed jsdom's missing
  `scrollIntoView`; a test-only stub follows the existing CustomSelect test pattern.

### Final Focused Checks

Backend, from the worktree root:

```bash
env TMPDIR=/tmp/opencode/ecm-gh856-tmp ECM_PYTHON=/home/lecaptainc/ecm/enhancedchannelmanager/.venv/bin/python scripts/backend-gate.sh --subset tests/routers/test_channel_pipeline.py tests/unit/test_channel_pipeline_evaluator.py tests/unit/test_channel_pipeline_schema.py tests/unit/test_channel_pipeline_engine.py
```

Result: **659 passed in 12.34s**. The test harness creates its own private config
under the dedicated TMPDIR and isolated SQLite databases. The new tests cross real
HTTP creation/GET, SQL persistence, rule loading, stream fetching/enrichment,
evaluation, and Log Match execution. Only Dispatcharr calls and the creation
journal are mocked; the engine uses a test-database session factory. Assertions
pin stream 101 as the sole action recipient, preview output, and read-only mock
client calls. No live Dispatcharr writes occurred.

Frontend, from `frontend/`, with Node 24.13.0 on PATH:

```bash
npx vitest run src/components/channelPipeline/ConditionEditor.test.tsx src/components/channelPipeline/RuleBuilder.test.tsx --silent
npx eslint src/components/channelPipeline/ConditionEditor.tsx src/components/channelPipeline/ConditionEditor.test.tsx --max-warnings 0
npx tsc --ignoreConfig --noEmit --target ES2020 --useDefineForClassFields --lib ES2020,ES2022.Error,DOM,DOM.Iterable --module ESNext --skipLibCheck --moduleResolution bundler --allowImportingTsExtensions --resolveJsonModule --isolatedModules --jsx react-jsx --strict --noUnusedLocals --noUnusedParameters --noFallthroughCasesInSwitch --types @testing-library/jest-dom/vitest src/vite-env.d.ts src/components/channelPipeline/ConditionEditor.tsx src/components/channelPipeline/ConditionEditor.test.tsx
```

Results: **123 passed in 6.67s**, ESLint **exit 0**, scoped types **exit 0**.
The scoped type command mirrors `tsconfig.json` compiler options and includes
the Vitest matcher augmentation. UI evidence is rendered jsdom component testing
with MSW group responses, not a deployed-browser end-to-end run. The test checks
field discovery, empty validation, name search, integer serialization, JSON
round-trip label, editing the selected group, and connector preservation.

### Parent Handoff / Verification Gaps

- `npm run typecheck` **failed** at the existing
  `frontend/src/components/ScheduleEditor.test.tsx:73`: TS2322, `parameters: null`
  is not assignable to `Record<string, unknown>`. Confirmed present in `90bf87f2`
  using `git show HEAD:frontend/src/components/ScheduleEditor.test.tsx`. GH975
  commit/files were not changed; parent must resolve this before full batch gates.
- Python Ruff is not installed in the project virtualenv and neither Ruff nor
  Flake8 is on PATH; Python lint was not run. No tooling dependency was added.
- Full canonical gates, deployed-browser acceptance, independent review, and
  shipping remain with the parent. No push, PR, merge, tracker transition, or
  GitHub closure was performed. No later batch bug was investigated or changed.

## GH975 Frozen Acceptance

Bead: `enhancedchannelmanager-0m06f.2.3.4`.
Source: https://github.com/MotWakorb/enhancedchannelmanager/issues/975
Read the full issue body and comments on intake; no comments were returned.
The initial sentence contradicts the title and steps. The dispatch explicitly
resolves this: schedules must not require enabled rules to opt into refresh.

1. A due broad schedule runs the full enabled rule set without requiring
   `run_on_refresh`, matching the pipeline-page broad run scope.
2. Manual Run Now of that scheduled task has the same broad scope.
3. Disabled rules remain excluded.
4. Real post-M3U-refresh execution remains refresh-filtered, with its existing
   watermark, circuit-breaker, active-window, and Event Sync opt-in guards.
5. Selected-rule schedules retain exact scope, canonical ordering, validation,
   fail-closed behavior, and existing reordering semantics.
6. Write regression tests and demonstrate red before minimal production edits.
7. No new dependencies, scheduler redesign, or unrelated refactors.
   Change scoped contract documentation only where needed by the actual fix.

## Routing Constraint Found Before Implementation

`ChannelPipelineTask.execute()` routes every non-selected invocation through
the post-refresh guard. `TaskEngine._execute_task_with_schedules()` supplies
the schedule ID and parameters, but not a distinct refresh-poll trigger.
`TaskRegistry._create_default_task_schedule` seeds the 60-second cadence without
parameters. This cadence is the current production consumer of the refresh
watermark; M3U refresh itself only advances the watermark.

The fix must not merely route every schedule ID to an unfiltered run: that
would turn the existing refresh poll into an every-minute broad execution and
remove the production refresh-filtered path. Nor may it preserve filtering
for every parameterless schedule, which would retain GH975.

## Approved Routing Decision

The PO approved preserving the built-in 60-second refresh-only poll and adding
an explicit distinction for broad operator schedules. Selected scope is unchanged.
Persisted compatibility migrations and modest existing-pattern configuration UI
changes are authorized if needed.

Implementation representation: a schedule with `run_all_rules: true` explicitly
requests all enabled rules, both when due and when manually fired. A schedule
with `rule_ids` retains exact selected scope; combining both scopes is rejected.
Parameterless schedules retain their existing refresh-only meaning. This includes
freshly seeded and installed default polls, and avoids guessing provenance from
editable names or intervals. Existing operator schedules can explicitly select
the broad option in the editor; no existing schedule is silently reclassified.
No database migration is needed because the existing parameters JSON carries
the distinction, and existing parameterless rows retain their meaning.

The initial four red reproductions used parameterless schedules before this
decision. They will now explicitly request broad scope, and complementary tests
will pin parameterless poll compatibility and exact selected scope.

## GH975 Evidence Before Fix

- `scripts/backend-gate.sh --subset tests/tasks/test_gh975_broad_pipeline_schedule.py`
  produced **4 failed in 1.03s** after correcting missing required fixture
  columns in an initial setup-only failure. Each final failure is
  `Expected run_pipeline to have been awaited once. Awaited 0 times.`
  The task-boundary reproduction covers scheduled/manual triggers with both
  absent and pending refresh watermarks, using isolated SQLite rules and a
  mocked external pipeline boundary. It does not yet prove the full Run Now
  API or due-schedule lifecycle, nor downstream disabled-rule exclusion.
- `scripts/backend-gate.sh --subset tests/unit/test_decouple_refresh_channel_pipeline.py tests/tasks/test_scheduled_selected_pipeline.py`
  produced **29 passed in 1.02s**, establishing the existing refresh guard and
  selected-schedule baseline before production edits.
- These are focused subset runs, not canonical full-gate results. No live
  Dispatcharr operations were performed. The implementation and remaining
  acceptance tests were pending the routing decision at that point.

## GH975 Implementation Evidence

The approved distinction is implemented. `run_all_rules: true` is accepted by
the schedule API and exposed by the existing editor. Due broad runs identify
themselves as `scheduled_all`, including at the Event Sync trigger gate; manual
Run Now uses `manual`. Neither broad invocation consumes a refresh watermark.
The built-in poll, exact selection, and default-disabled task setting remain
separate. Existing parameterless schedules are not reclassified on upgrade or
when renamed in the editor. No migration or new dependency was introduced.

### Additional Red Proofs

- Explicit-scope backend reproduction:
  `scripts/backend-gate.sh --subset tests/tasks/test_gh975_broad_pipeline_schedule.py tests/routers/test_tasks.py -k 'broad or installed_parameterless'`
  returned **7 failed, 4 passed, 88 deselected** before implementation. This
  included the four skipped broad invocations, rejected API creation, and
  validation failures. The conflicting-scope case reached the old selected
  loader instead of being rejected at the parameter boundary.
- Event Sync parity:
  `scripts/backend-gate.sh --subset tests/tasks/test_gh975_broad_pipeline_schedule.py -k explicit_broad_event`
  returned **4 failed, 16 deselected** before admitting the explicit broad
  trigger. Generic `scheduled` and unknown triggers remain denied.
- Editor broad selection: `npx vitest run src/components/ScheduleEditor.test.tsx -t 'saves explicit broad'`
  returned **1 failed, 36 skipped** because Save remained blocked. An initial
  accessible-name selector error was corrected before this behavioral proof.
- Installed poll editor: `npx vitest run src/components/ScheduleEditor.test.tsx -t 'keeps an installed'`
  returned **1 failed, 37 skipped** because unchanged poll scope blocked Save.
- Selected-scope audit regression caught during implementation:
  `scripts/backend-gate.sh --subset tests/tasks/test_scheduled_selected_pipeline.py -k due_schedule_seam`
  returned **1 failed, 1 passed, 16 deselected** with an unchecked broad flag.
  Selected validation was restored to its existing boundary before final checks.

### Final Focused Verification

From the worktree root:

```bash
scripts/backend-gate.sh --subset tests/tasks/test_gh975_broad_pipeline_schedule.py tests/tasks/test_scheduled_selected_pipeline.py tests/unit/test_decouple_refresh_channel_pipeline.py tests/unit/test_selected_pipeline_lifecycle.py tests/unit/test_channel_pipeline_engine.py tests/unit/test_event_sync_engine_wiring.py tests/unit/test_channel_pipeline_default_disabled.py tests/routers/test_tasks.py
```

Result: **358 passed in 7.83s**. The script selected the shared project Python
3.12 virtualenv and private test configuration. Coverage is disabled for this
focused subset; this is not the canonical backend gate.

From `frontend/`, with Node 24.13.0:

```bash
env PATH="/home/lecaptainc/.local/share/fnm/node-versions/v24.13.0/installation/bin:$PATH" npx vitest run src/components/ScheduleEditor.test.tsx src/components/ScheduledTasksSection.runNowNotifications.test.tsx
env PATH="/home/lecaptainc/.local/share/fnm/node-versions/v24.13.0/installation/bin:$PATH" npx eslint src/components/ScheduleEditor.tsx src/components/ScheduleEditor.test.tsx --max-warnings 0
```

Results: **41 passed in 1.45s**; changed-file ESLint **exit 0**. Dependencies
were installed locally from the unchanged lockfile using `npm ci --no-audit --no-fund`.

The new persisted-schedule tests cross the real TaskEngine due-dispatch and
Run Now paths, task invocation, SQLite rule selection, and task history. They
replace external pipeline processing/writes, not the enabled/priority query.
The broad selection matches the engine's page-run rule loader. Refresh remains
filtered; selected scope remains canonical and fail-closed. Frontend evidence
is rendered component testing, not a live-browser or deployed acceptance test.
Canonical full gates and independent review remain with the parent at batch
completion; no push, PR, merge, issue closure, or tracker transition occurred.
