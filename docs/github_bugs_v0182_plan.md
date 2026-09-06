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
| 4 | #970 | Pending | Not investigated in this dispatch. |
| 5 | #968 | Pending | Not investigated in this dispatch. |
| 6 | #962 | Pending | Not investigated in this dispatch. |
| 7 | #858 | Pending | Not investigated in this dispatch. |
| 8 | #969 | Pending | Not investigated in this dispatch. |

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
