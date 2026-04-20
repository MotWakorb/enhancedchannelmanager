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
