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
| 3 | #856 | Pending | Not investigated in this dispatch. |
| 4 | #970 | Pending | Not investigated in this dispatch. |
| 5 | #968 | Pending | Not investigated in this dispatch. |
| 6 | #962 | Pending | Not investigated in this dispatch. |
| 7 | #858 | Pending | Not investigated in this dispatch. |
| 8 | #969 | Pending | Not investigated in this dispatch. |

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
