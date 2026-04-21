# Testing Guidelines

## Test Infrastructure Overview

This project has comprehensive test coverage at three levels.

## 1. Backend Tests (Python/pytest)

Located in `backend/tests/`, run with `cd backend && python -m pytest tests/ -q`

**Router Tests** (`backend/tests/routers/`): Tests for extracted router modules.
- `test_channels.py`, `test_channel_groups.py` - Channel management
- `test_m3u.py`, `test_m3u_digest.py` - M3U account/digest management
- `test_epg.py` - EPG sources, data, grid
- `test_settings.py` - Settings configuration
- `test_tasks.py` - Task engine, cron, schedules
- `test_ffmpeg.py` - FFMPEG builder, profiles
- `test_stream_stats.py` - Stream probing/health
- `test_stream_preview.py` - Stream/channel preview
- `test_auto_creation.py` - Auto-creation pipeline
- `test_notifications.py` - Notification system
- `test_alert_methods.py` - Alert methods
- `test_stats.py` - Stats and monitoring
- `test_tags.py` - Tag groups and engine
- `test_profiles.py` - Profile management
- `test_normalization.py` - Normalization rules
- `test_journal.py` - Activity journal
- `test_health.py` - Health checks
- `test_streams.py` - Stream listing/providers

**Unit Tests** (`backend/tests/unit/`):
- `test_journal.py` - Journal logging system
- `test_cache.py` - Caching mechanisms
- `test_schedule_calculator.py` - Schedule calculations
- `test_cron_parser.py` - Cron expression parsing
- `test_alert_methods.py` - Alert method logic
- `test_auto_creation_engine.py` - Auto-creation engine
- `test_auto_creation_evaluator.py` - Auto-creation evaluator
- `test_auto_creation_executor.py` - Auto-creation executor
- `test_auto_creation_schema.py` - Auto-creation schema
- `test_compute_sort_endpoint.py` - Stream sort computation

**Integration Tests** (`backend/tests/integration/`):
- `test_api_settings.py` - Settings API endpoints
- `test_api_tasks.py` - Task scheduler API endpoints
- `test_api_notifications.py` - Notification API endpoints
- `test_api_alert_methods.py` - Alert methods API endpoints
- `test_api_auto_creation.py` - Auto-creation API endpoints
- `test_api_stream_preview.py` - Stream preview API
- `test_api_ffmpeg.py` - FFMPEG builder API
- `test_api_csv.py` - CSV import/export API
- `test_normalize_channel_create.py` - Normalization on create
- `test_router_registration.py` - Route uniqueness validation
- `test_lifecycle.py` - App startup/shutdown lifecycle

## 2. Frontend Tests (Vitest)

Located in `frontend/src/`, run with `cd frontend && npm test`

**Hook Tests:**
- `hooks/useChangeHistory.test.ts` - Change history tracking hook
- `hooks/useAsyncOperation.test.ts` - Async operation management hook
- `hooks/useSelection.test.ts` - Selection state management hook
- `hooks/useAutoCreationRules.test.ts` - Auto-creation rules hook
- `hooks/useAutoCreationExecution.test.ts` - Auto-creation execution hook

**Service Tests:**
- `services/api.test.ts` - API service layer
- `services/autoCreationApi.test.ts` - Auto-creation API service

**Component Tests:**
- `components/autoCreation/AutoCreationTab.test.tsx` - Auto-creation tab
- `components/autoCreation/RuleBuilder.test.tsx` - Rule builder
- `components/autoCreation/ConditionEditor.test.tsx` - Condition editor
- `components/autoCreation/ActionEditor.test.tsx` - Action editor
- `components/tabs/BandwidthPanel.test.tsx` - Bandwidth panel
- `components/tabs/EnhancedStatsPanel.test.tsx` - Enhanced stats panel
- `components/tabs/PopularityPanel.test.tsx` - Popularity panel
- `components/tabs/WatchHistoryPanel.test.tsx` - Watch history panel

## 3. E2E Tests (Playwright)

Located in `e2e/`, run with `npm run test:e2e` from root

**Test Coverage:**
- `smoke.spec.ts` - Basic smoke tests
- `channels.spec.ts` - Channel management workflows
- `channel-filters.spec.ts` - Channel filter functionality
- `m3u-manager.spec.ts` - M3U playlist management
- `epg-manager.spec.ts` - EPG data management
- `logo-manager.spec.ts` - Logo management
- `guide.spec.ts` - TV guide functionality
- `tasks.spec.ts` - Scheduled tasks
- `settings.spec.ts` - Application settings
- `journal.spec.ts` - Journal/logging
- `stats.spec.ts` - Statistics and analytics
- `alert-methods.spec.ts` - Alert notification methods
- `auto-creation.spec.ts` - Auto-creation pipeline

**Running E2E Tests:**
```bash
npm run test:e2e           # Headless mode (CI/CD)
npm run test:e2e:ui        # Interactive UI mode
npm run test:e2e:headed    # Run in visible browser
npm run test:e2e:debug     # Debug mode with breakpoints
npm run test:e2e:report    # View test report
```

## When to Run Tests

- **Backend tests**: MANDATORY for any backend code changes
- **Frontend tests**: MANDATORY for any frontend code changes
- **E2E tests**: Run on merge to main only (CI/CD pipeline)

## Quality Gate Commands

```bash
# Backend
python -m py_compile backend/main.py && cd backend && python -m pytest tests/ -q

# Frontend
cd frontend && npm test && npm run build
```

## Mock Patch Targets

When endpoints move from `main.py` to `routers/<module>.py`, test mock patches must be updated:
- `patch("main.get_client")` → `patch("routers.<module>.get_client")`
- `patch("main.get_settings")` → `patch("routers.<module>.get_settings")`
- `patch("main.journal")` → `patch("routers.<module>.journal")`
- Same for `get_session`, `get_prober`, `asyncio`, etc.

## Flake Triage Policy

Flaky tests — tests that pass and fail non-deterministically without code changes
— are treated as **P1 bugs** (per the QA hard rules). The baseline established in
bead `enhancedchannelmanager-tp681` (2026-04-20): 3 consecutive BE + FE runs on
`dev` tip produced zero true flakes.

### What counts as a flake

A test is **flaky** if it changes outcome (pass → fail or fail → pass) across
identical re-runs without any code or data change. Common causes:

- **Timing / ordering**: races, `await asyncio.sleep(...)` assumptions,
  wall-clock comparisons.
- **Shared state**: module-level globals leaking between tests, DB rows not
  rolled back, singleton clients caching values.
- **Environmental**: test expects a file, binary, or network endpoint that is
  only sometimes present. These are **not true flakes** — they are environment
  drift and should be fixed by making the test defensive, not by re-running.

If a test fails identically every run for the same reason, it is **deterministically
broken** — repair the test or the code. Do not mark it `flaky`.

### Re-run policy (CI & local)

| Scenario | Allowed re-runs |
|----------|-----------------|
| PR check fails on one test, passes on re-run | Re-run **once** to confirm flake. If flaky, file a `flaky`-labelled bead before merge. |
| PR check fails on same test twice in a row | Treat as deterministic break — do not merge. |
| Local `pytest` / `vitest` reports intermittent failure | Re-run **up to twice**. If it recurs, open a bead rather than silently re-running. |

**Never** use `pytest-rerunfailures`, `vitest --retry`, or equivalent as an
automatic safety net. Retries hide flakes. They are only acceptable as a
temporary mitigation while a bead is open.

### Marking a test as a known flake

1. File a bead (`bd create enhancedchannelmanager "<test path>: flaky — <symptom>"`)
   and add the `flaky` label.
2. If the test blocks the suite, mark it with
   `@pytest.mark.skip(reason="flaky, see bead <id>")` or
   `test.fixme(...)` in vitest. Cite the bead ID in the reason string.
3. Do **not** leave `@pytest.mark.xfail` on flaky tests — xfail masks real
   regressions once the code is fixed.

### Quarterly flake sweep

Every quarter (tracked via recurring beads), the QA persona (or on-call
engineer in its absence) runs the 3-run cadence from bead `tp681`:

1. Pull the current `flaky`-labelled beads list.
2. Execute BE (`pytest tests/ --ignore=tests/e2e -m "not slow"`) and FE
   (`npx vitest run`) three consecutive times on `dev` tip.
3. Any test that fails in exactly one of the three runs → new `flaky`-labelled
   bead (or comment on the existing one if already known).
4. Any test that fails in all three runs → it is a real regression; escalate
   to a P0/P1 bug bead in the relevant domain.
5. Revisit the open `flaky` bead list and close anything that is now passing
   three runs cleanly without code change.

### Flake baseline gate for PR reviews

The reviewer SHOULD reject a PR when the CI failure signature includes a test
in the **flagged-in-last-30-runs** list — those are known-flaky and the PR
needs a clean re-run (or an explicit note that the flake is unrelated to the
change).

Until automation tracks the 30-run window directly (see follow-up bead), use
this manual process:

1. Pull the list of `flaky`-labelled open beads: `bd list --label flaky`.
2. If the failing test is in that list → re-run once. If still fails →
   investigate; probably unrelated to the PR but do not merge until the next
   CI run is green.
3. If the failing test is **not** in the flaky list → treat as deterministic
   and block the merge until fixed.

### Known baseline flakes (as of 2026-04-20)

**Frontend (vitest):** zero flakes. 1118/1118 tests passed in three consecutive
runs on commit `a35d4f5e`.

**Backend (pytest, `--ignore=tests/e2e -m "not slow"`):** two flaky tests under
`tests/routers/test_observability_middleware.py::TestTraceIdMiddleware`:
- `test_trace_id_appears_in_log_line`
- `test_generated_trace_id_matches_uuidv4_format_in_logs`

Both pass in isolation and fail when run after the second half of
`tests/integration/`. Root cause is contextvar / logging-handler leakage from
an integration test into the observability middleware's capture fixture.
Tracked in bead **enhancedchannelmanager-hhsz0** (`flaky` label, P1).

**Not flakes, but deterministic environment drift (three BE tests):**
- `tests/integration/test_api_tasks.py::TestRunTaskWithSchedule::test_run_task_with_schedule_id`
  — references a POST route that was removed from `routers/tasks.py`.
- `tests/integration/test_router_registration.py::TestRoutePrefixes::test_all_routes_under_api`
  — fails because the SPA fallback route `/{full_path:path}` registers only
    when `backend/static/` exists (present in prod image, absent on CI).
- `tests/unit/test_ffmpeg_execution.py::TestExecutionSafety::test_validates_output_path_writable`
  — the code under test never implements the output-writability check its
    docstring promises.

These pass on CI (`backend/` workdir, no `static/`, stubbed ffmpeg mocks) but
fail in `ecm-ecm-1`. Tracked in bead **enhancedchannelmanager-0gcu9** as
test-authorship repair. Until that bead lands, the container-side 3-run
cadence uses `--deselect` for these three tests.

### Full-suite 3-run cadence command

The exact command used for the `tp681` baseline and the quarterly sweep:

```bash
# BE — from inside ecm-ecm-1
python -m pytest tests/ --ignore=tests/e2e \
  --deselect tests/integration/test_api_tasks.py::TestRunTaskWithSchedule::test_run_task_with_schedule_id \
  --deselect tests/integration/test_router_registration.py::TestRoutePrefixes::test_all_routes_under_api \
  --deselect tests/unit/test_ffmpeg_execution.py::TestExecutionSafety::test_validates_output_path_writable \
  --deselect tests/routers/test_observability_middleware.py::TestTraceIdMiddleware::test_trace_id_appears_in_log_line \
  --deselect tests/routers/test_observability_middleware.py::TestTraceIdMiddleware::test_generated_trace_id_matches_uuidv4_format_in_logs \
  -p no:cacheprovider --tb=line -q

# FE — from host (ecm-ecm-1 has no Node tooling)
cd frontend && npx vitest run --reporter=default
```

Remove the relevant `--deselect` once a flake/drift bead closes.
