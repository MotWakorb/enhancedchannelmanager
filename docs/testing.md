# Testing Guidelines

## Test Infrastructure Overview

This project has comprehensive test coverage at three levels.

> **DBAS round-trip test environment** (ECM ↔ live Dispatcharr): a pinned,
> throwaway Dispatcharr stack + production-shaped seed tooling lives in
> [`tests/dbas-test-env/`](../tests/dbas-test-env/). Strategy and rationale:
> [`docs/testing/dbas-test-env.md`](testing/dbas-test-env.md). Use it to validate
> the round-trip success signal against a real Dispatcharr instead of the
> assumption-encoding mocks in `backend/tests/fixtures/mock_dispatcharr.py`.

## 1. Backend Tests (Python/pytest)

> **Always run backend tests under the project venv**, not a bare system
> `python3`: `.venv/bin/python -m pytest` (or the path-relative equivalent
> from wherever you're running, since the point is the interpreter, not the cwd).
> The project pins `cryptography` at 42+; a bare system `python3` commonly
> resolves an older `cryptography` (e.g. 41.0.7) that is missing
> `x509.Certificate.not_valid_before_utc` / `not_valid_after_utc` (added in
> cryptography 42). That gap produces 7-9 confusing failures in
> `backend/tests/unit/test_tls_storage.py`: assertion failures on subject/
> validity fields, not an obvious `AttributeError`, because the code under
> test catches the exception broadly. Two engineers independently lost time
> to this (bead `enhancedchannelmanager-vol5d`) before the affected tests
> were given a version-gated skip that names the fix in its reason string.
> If you see that skip fire, you're not on the venv interpreter.

Located in `backend/tests/`, run with `cd backend && python -m pytest tests/ -q`

**Router Tests** (`backend/tests/routers/`): Tests for extracted router modules.
- `test_channels.py`, `test_channel_groups.py` - Channel management
- `test_m3u.py`, `test_m3u_digest.py` - M3U account/digest management
- `test_epg.py` - EPG sources, data, grid
- `test_settings.py` - Settings configuration
- `test_tasks.py` - Task engine, cron, schedules
- `test_stream_stats.py` - Stream probing/health
- `test_stream_preview.py` - Stream/channel preview
- `test_channel_pipeline.py` - Channel Pipeline
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
- `test_channel_pipeline_engine.py` - Channel Pipeline engine
- `test_channel_pipeline_evaluator.py` - Channel Pipeline evaluator
- `test_channel_pipeline_executor.py` - Channel Pipeline executor
- `test_channel_pipeline_schema.py` - Channel Pipeline schema
- `test_compute_sort_endpoint.py` - Stream sort computation

**Integration Tests** (`backend/tests/integration/`):
- `test_api_settings.py` - Settings API endpoints
- `test_api_tasks.py` - Task scheduler API endpoints
- `test_api_notifications.py` - Notification API endpoints
- `test_api_alert_methods.py` - Alert methods API endpoints
- `test_api_channel_pipeline.py` - Channel Pipeline API endpoints
- `test_api_stream_preview.py` - Stream preview API
- `test_api_csv.py` - CSV import/export API
- `test_normalize_channel_create.py` - Normalization on create
- `test_router_registration.py` - Route uniqueness validation
- `test_lifecycle.py` - App startup/shutdown lifecycle

## Backend Test Layers: `integration/` vs `routers/`

These two directories are distinct testing layers. They are not duplicates of each other.

### `backend/tests/integration/`: Shallow, mock-DB layer

Files named `test_api_<domain>.py` and other integration-scoped tests.

- **Client**: `fastapi.testclient.TestClient` (synchronous)
- **Database**: `MagicMock()` session injected via `patch("routers.<module>.get_session")`
- **Depth**: Shallow, asserting API shapes, status codes, and routing without touching real SQL
- **When to add tests here**: Verifying API contracts that can be fully expressed by mocking the DB query results; testing how the router reacts to DB-layer exceptions; lightweight smoke checks that don't require real ORM behaviour

### `backend/tests/routers/`: Deep, real-DB layer

Files named `test_<domain>.py`.

- **Client**: `httpx.AsyncClient` via the `async_client` fixture in `conftest.py` (async)
- **Database**: Real in-memory SQLite (`StaticPool`) via the `test_session` fixture: full ORM round-trips
- **Depth**: Deep, inserting real rows, exercising ORM queries, validating constraints and model
  relationships
- **When to add tests here**: Verifying that endpoints interact correctly with actual database state;
  testing model constraints, ordering, pagination, and FK relationships; any scenario where a
  MagicMock DB session would hide a real ORM bug

### Naming inversion note

Despite the directory names, the `integration/` layer is the **shallower, more-mocked** layer and the `routers/` layer is the **deeper, less-mocked** layer.  This naming reflects historical test organisation rather than the standard "integration = real dependencies" convention.  New tests added here should follow the existing pattern in each directory rather than trying to reclassify tests based on name alone.

### Acceptable duplication

A handful of trivially simple cases (`GET /some/endpoint → 404 Not Found`) are
intentionally present in both layers.  This is acceptable because each copy exercises
different machinery (sync+mock-DB vs async+real-DB) and provides independent signal.
Do not consolidate these just to reduce line count.

---

## 2. Frontend Tests (Vitest)

Located in `frontend/src/`, run with `cd frontend && npm test`

**Hook Tests:**
- `hooks/useChangeHistory.test.ts` - Change history tracking hook
- `hooks/useAsyncOperation.test.ts` - Async operation management hook
- `hooks/useSelection.test.ts` - Selection state management hook
- `hooks/useChannelPipelineRules.test.ts` - Channel Pipeline rules hook
- `hooks/useChannelPipelineExecution.test.ts` - Channel Pipeline execution hook

**Service Tests:**
- `services/api.test.ts` - API service layer
- `services/channelPipelineApi.test.ts` - Channel Pipeline API service

**Component Tests:**
- `components/channelPipeline/ChannelPipelineTab.test.tsx` - Channel Pipeline page
- `components/channelPipeline/RuleBuilder.test.tsx` - Rule builder
- `components/channelPipeline/ConditionEditor.test.tsx` - Condition editor
- `components/channelPipeline/ActionEditor.test.tsx` - Action editor
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
- `auto-creation.spec.ts` - Channel Pipeline (spec filename predates the Channel Pipeline rename; not renamed yet, tracked as follow-up enhancedchannelmanager-3udrl)

**Running E2E Tests:**
```bash
npm run test:e2e           # Headless mode (CI/CD)
npm run test:e2e:ui        # Interactive UI mode
npm run test:e2e:headed    # Run in visible browser
npm run test:e2e:debug     # Debug mode with breakpoints
npm run test:e2e:report    # View test report
```

### Rendered-CSS regression guards

Seven specs in `e2e/` are not feature tests. They are guards over *rendered*
CSS, the layer where the project has repeatedly regressed while every unit
test stayed green. They exist because a computed style in a real browser is
the only thing that can prove these claims; jsdom cannot, and neither can a
declaration-level audit.

| Spec | What it pins | Proven red against |
|-|-|-|
| `sr-only-hidden.spec.ts` | `.sr-only` / `.visually-hidden` measure ≤1×1 and are not returned by `elementFromPoint`, **on a cold context with Channel Pipeline never visited** | `.sr-only` moved back into the ChannelPipelineTab chunk (bead `-zncyv`): Dashboard 1004×24, `position: static`, hit-testable |
| `frozen-chrome.spec.ts` | rail 244px, rail label 14px/400, rail icon 20px, header band 45px, route title 20px/700/26px, all measured across ten routes × dark/light/high-contrast × 1600×1000, 1280×800 and 1280×720 | a bare `.primary-sidebar` / `.navigation-label` redeclaration inside `LogoManagerTab.css`: every route visited *after* Logo Manager reported 248px / 15px |
| `route-typography-scale.spec.ts` | every visible text node in a route content pane computes to a P1 size: {20, 15, 13, 11, 10} text, {18, 16, 14, 64} icon | a new 22px site on M3U Manager **and** an allowlisted site silently fixed without deleting its entry (reported as `STALE`) |
| `cross-route-css-leak.spec.ts` | shared-class typography does not depend on route visit order | four historical instances: `.list-header`, `.status-label`, `.group-count`, `.action-btn .material-icons` |
| `contrast-aa.spec.ts` | every visible text node on all eleven routes clears WCAG AA (4.5:1 normal, 3:1 large and non-text glyphs) in dark/light/high-contrast at 1280×720 and 1920×1080, measured as **true composited** contrast (whole ancestor background chain, element and ancestor `opacity`, colours resolved through the compositor rather than parsed) | the three light-theme `--accent-secondary` selected states of bead `-dlavh`: Stats pill 3.68, Settings pill 3.68, Settings rail row 3.25, all against 4.5. It also found two defects nobody had reported: the selected primary-rail row at 4.20 on **every** route, and the Channel Manager probe glyph failing in all three themes |
| `settings-nav-groups.spec.ts` | the Settings drill-in renders the approved six groups in order with `aria-current` and real `#settings/<page>` anchors, and the rail's overflow **contract** holds (it scrolls, Back stays pinned and opaque, the last destination stays reachable) at 1280×720 and 1920×1080 × three themes, expanded and collapsed | the grouped rail at 1099px against a 675px budget at 1280×720 with Back `position: static`: the only exit from Settings scrolled out of view (bead `-70u0r.4`) |
| `control-typeface.spec.ts` | every visible `button`/`input`/`select`/`textarea` on ten routes renders in the SAME resolved face as its nearest text-bearing ancestor (arm 1), and at a size the application chose rather than the user-agent's own control default (arm 2) | arm 1 (bead `-6z299.9`): controls resolving to generic `sans-serif` while surrounding text resolved to `system-ui`, invisible to a `fontFamily` string comparison. Arm 2 (bead `-ul2tp`): 274 of 418 visible controls across the ten routes rendering at Chromium's 13.3333px UA default with arm 1 green throughout |

`frozen-chrome.spec.ts` pins **1280×720 as well as 1280×800** because 1280×720
is the minimum supported viewport, and the height is what the Settings drill-in
strains. Both rows are kept; dropping one from a frozen matrix is not free.

```bash
npm run test:css-guard:sr-only            # builds + serves the source; NO backend needed
npm run test:css-guard:frozen-chrome      # needs a live ECM backend
npm run test:css-guard:type-scale         # needs a live ECM backend
npm run test:css-guard:contrast-aa        # needs a live ECM backend; ~10 min, 66 route walks
npm run test:css-leak                     # needs a live ECM backend
npm run test:css-guards                   # all of the above, against a live backend
npm run test:css-guard:settings-nav       # needs a live ECM backend
npm run test:css-guard:control-typeface   # needs a live ECM backend
```

**CI status: read this before assuming coverage.**

- `sr-only-hidden.spec.ts` **runs in CI**, as the `Screen-Reader-Only
  Rendering Guard` job in `.github/workflows/test.yml`. It uses
  `E2E_START_SERVER=true E2E_EXACT_BUILD=true`, which builds the checked-out
  source and serves it on an isolated preview port with no backend. The shell,
  Dashboard and Settings all mount that way, so the assertion is reachable.
  The job is **not** in the required-check set. Making it required means
  first converting it to the step-gated shape the required jobs use (see
  "One source of truth per required check" below): it currently skips at the
  job level on a documentation-only change, and GitHub counts a skipped job
  as satisfying a required check.
- `frozen-chrome.spec.ts`, `route-typography-scale.spec.ts`,
  `contrast-aa.spec.ts`, `cross-route-css-leak.spec.ts` and
  `control-typeface.spec.ts` are **manual-only**.
  This is not an oversight and not a "wire it up later": all five walk every route, and
  Channel Manager's `.channels-pane` never mounts without an API (measured, on
  a backend-less preview build: `waitForSelector('.channels-pane')` times out
  at 60s). They need a live ECM container, which CI does not have; that is the
  same constraint that defers the rest of `e2e/*.spec.ts`, tracked as bead
  `enhancedchannelmanager-2lw25`. When 2lw25 lands a live-service CI
  environment these five are the first specs that should move into it.
- Until then the browser half of the CSS defence runs only when a human
  remembers. Run `npm run test:css-guards` against the running container
  before shipping any change under `frontend/src/**/*.css`.

**These guards measure the build that is being SERVED, not your working tree.**
The default base URL is the deployed container on `:6100`, so running them
against a container that has not been redeployed since your edit reports the
*old* build's CSS, and the type-scale guard will list the sites you just
fixed as brand-new failures. Observed exactly that: nine sites fixed in the
tree still showed as off-scale on `:6100` because the container predated the
fix.

```bash
# sr-only: builds and serves YOUR tree, no container involved. Always exact.
npm run test:css-guard:sr-only

# the others: deploy your build to the container FIRST, then run.
scripts/deploy-frontend.sh
npm run test:css-guards
```

**Or serve your own tree and skip the deploy entirely.** `vite preview`
inherits `server.proxy` from `frontend/vite.config.ts`, so it *does* proxy
`/api` to `http://localhost:8000`. With a backend answering there, the whole
guard set runs against the working tree with nothing deployed:

```bash
cd frontend && npm run build && npx vite preview --host 127.0.0.1 --port 4173 &
E2E_BASE_URL=http://127.0.0.1:4173 npm run test:css-guards
```

Measured on bead `-70u0r.4`: `sr-only-hidden`, `frozen-chrome`,
`route-typography-scale`, `cross-route-css-leak` and `settings-nav-groups` all
green this way, including `frozen-chrome`'s full ten-route walk with Channel
Manager's `.channels-pane` mounting normally, so a backend-less preview is not
the constraint the paragraph above assumed. `control-typeface` was verified the
same way under bead `-ae3ms`, the pass that wired it into `test:css-guards`:
all 21 tests across the full seven-spec aggregate passed against a `vite
preview` tree with `/api` proxied to the live container. This corrects an earlier claim that preview "proxies
nothing, so `/api` 502s". That was true of a bare static server, not of
`vite preview`. Use it whenever you must measure rendered CSS *before*
committing or deploying; it is the only way to run these guards against an
uncommitted tree. The caveat that survives is the important one: **these
guards measure the build being SERVED.** Rebuild after every edit, or you are
re-measuring the previous bundle.

**Their allowlist cannot rot.** `route-typography-scale.spec.ts` carries an
allowlist because eight of ten routes have off-scale sites today, and it is
bidirectional on purpose, following the same discipline as TIER 2 of
`frontend/src/cssAudits/sharedClassChunkLeak.audit.test.ts`. A **new**
off-scale site fails; a **stale** entry (its site now renders on-scale) also
fails, with "delete this entry"; an entry whose selector matches nothing fails
unless it is marked `dataDependent`. Fixing an allowlisted site therefore
*requires* deleting its entry in the same commit. Every entry names the bead
that owns it.

**Shared plumbing lives in `e2e/fixtures/css-guard.ts`,** and three things in
it are load-bearing rather than convenience:

1. **One login per spec file.** `backend/auth/routes.py` rate-limits login at
   `5/minute`; these guards open many contexts and a login per context blows
   that budget, surfacing as `Login failed: Too Many Requests`, a flake that
   reads like a broken assertion.
2. **Hash navigation, never `page.reload()`.** Every route tab is a lazy chunk
   whose stylesheet is appended to `<head>` on first visit and never removed.
   A reload discards all of them, resetting the exact state these guards
   observe and silently turning a real failure into a pass.
3. **An explicit `waitFor` on the login gate.** `isVisible({ timeout })` is a
   no-op in Playwright (the option is ignored), so a still-rendering login
   form reads as "not a login page" and the run proceeds into a blank shell.

## 4. Modal harness (dev-only dialog measurement)

`frontend/modal-harness.html` force-renders **every dialog in the app** with
stubbed data, including the many that cannot be reached against a real
instance (no pending merges, empty review queues, no probe results, banner
conditions unmet). It exists so a CSS change touching modals can be verified
against all of them instead of the subset today's data happens to allow.
Introduced by bead `enhancedchannelmanager-xhldy.1` for the P1 type-scale
work; reusable for any later restyle of the same surfaces.

```bash
# Capture / re-capture the baseline (builds the harness, walks all dialogs)
node scripts/measure-modal-typography.mjs

# After a CSS change: what moved?
node scripts/measure-modal-typography.mjs --diff

# Poke at one dialog by hand
cd frontend && npx vite --config vite.harness.config.ts
# -> http://127.0.0.1:5273/modal-harness.html            (index of all dialogs)
# -> http://127.0.0.1:5273/modal-harness.html?dialog=edit-channel
# -> ...&theme=light   ...&live=1 (talk to a real backend instead of the stub)
```

Baseline artefact:
`frontend/src/devHarness/baseline/modal-typography.baseline.json`.

**Every capture is animation-frozen, and captures made before that was true
have untrustworthy GEOMETRY.** `ModalBase.css` opens each dialog with
`modal-container-slide-in` (0.2s, `scale(0.98)` to `scale(1)`), and a capture
taken mid-flight multiplies every box in the dialog by a run-dependent scale
factor. Measured on bead `enhancedchannelmanager-iotbh`: one unfrozen run
reported the 32px close button at seven different sizes across the 81 dialogs,
and a change that could only shrink type by 0.33px moved 214 of 281 boxes by 1
to 11px. The script now suppresses animations and transitions before it
measures, with no flag to turn that off, and stamps `animationsFrozen: true`
into the payload. A capture without that field predates the fix. Two
consecutive frozen runs over an unchanged tree now move 0 of 411 geometry rows.
Typography rows were never affected, since `font-size` and `font-weight` are
not scaled by a transform.

**It is not in the production bundle, and cannot be.** `vite.config.ts` has a
single entry (`index.html`); the harness is built only by the separate
`vite.harness.config.ts` into the gitignored `.modal-harness-dist/`.
`src/devHarness/harnessIsolation.test.ts` fails if any app file imports harness
code or if `vite.config.ts` grows a second entry.

**The dialog list is derived from source, not hand-maintained.** Every
non-test `.tsx` under `src/` is scanned for `modal-container` / `ModalOverlay`
/ `role="dialog"` / `role="alertdialog"`, and
`src/devHarness/harnessCoverage.test.ts` goes RED when a file matching those
markers has no entry in `dialogCatalog.ts`. A dialog added later is therefore
covered automatically, or it breaks the build. It cannot be silently missed.

Adding a dialog to the harness:

1. Add an entry to `src/devHarness/dialogCatalog.ts` (`status: 'stubbed'`, or
   `'gap'` with a reason if it genuinely cannot be force-rendered).
2. Add a recipe to `src/devHarness/dialogRenderers.tsx`. `tsc --noEmit` fails
   until you do. Either render the component directly with stub props, or
   render its host and list the `open` clicks that bring the dialog up.
3. Add stub responses to `src/devHarness/apiStub.ts` if it fetches on mount.

Never change a component to suit the harness. If a dialog cannot be reached
without editing it, record it as a gap.

## Coverage ratchet cadence

Coverage is enforced in CI as a **one-way ratchet**: the current floor is the
baseline measured 2026-04-20 during bead `enhancedchannelmanager-nmlxi`, minus
a small regression buffer. Crossing below those numbers fails the CI job.

### Current thresholds

| Suite | Metric | Measured 2026-04-20 | Threshold | Buffer | Where enforced |
|-|-|-|-|-|-|
| Backend (pytest + coverage.py) | lines | 58% | 56% | 2 pts | `backend/pytest.ini` (`--cov-fail-under=56`), paths in `backend/.coveragerc` |
| Frontend (vitest + v8) | statements | 15.17% | 13% | 2 pts | `frontend/vitest.config.ts` `thresholds.statements` |
| Frontend (vitest + v8) | branches | 14.13% | 12% | 2 pts | `frontend/vitest.config.ts` `thresholds.branches` |
| Frontend (vitest + v8) | functions | 15.28% | 13% | 2 pts | `frontend/vitest.config.ts` `thresholds.functions` |
| Frontend (vitest + v8) | lines | 15.46% | 13% | 2 pts | `frontend/vitest.config.ts` `thresholds.lines` |

Backend measurement: `docker exec ecm-ecm-1 sh -c 'cd /app && python -m pytest
--ignore=tests/e2e -m "not slow" --cov-config=/tmp/.coveragerc --cov=.
--cov-report=term'` with the three known-drift deselects from the flake
section above. 2427 tests, 3 deselected.

Frontend measurement: `cd frontend && npm run test:coverage`. 1118 tests across
44 files.

### Rationale for buffer choice

The ideal methodology (from bead `enhancedchannelmanager-nmlxi`) is to wait
~1 week after the CI test-gate landed (`enhancedchannelmanager-t8xw3`) so we
can observe real per-PR coverage numbers rather than the full-suite snapshot.
We didn't have that window. t8xw3 closed the day this bead landed. The PO
approved a single full-suite snapshot with a 2-point buffer as a pragmatic
baseline. Expect slightly churny CI on PRs that touch low-coverage modules
until the first re-ratchet.

### Re-ratchet policy

- **Cadence**: review the thresholds **2-4 weeks after this bead lands**,
  once real PR coverage data exists. Thereafter, review quarterly (aligned
  with the flake sweep).
- **Raise criterion**: if every PR merged in the review window held coverage
  comfortably (≥ threshold + 3 points) on every metric, raise that metric's
  threshold by **~5 points**. Never raise by more than 5 points in one
  review. This gives authors time to respond before the ratchet tightens further.
- **Lower prohibition**: thresholds are **one-way**. Lowering requires
  explicit PO approval and a one-line rationale in the commit message. Do
  not lower "because my PR didn't quite make it". Add tests instead.
- **Per-metric independence**: frontend has four metrics (lines, branches,
  functions, statements). They ratchet independently. A PR that lifts
  function coverage to 20% should raise the function threshold to 15%.
  It does not have to wait for statements to also move.
- **Scope creep guard**: this bead's predecessor (`t8xw3`) explicitly
  excludes retroactively force-testing low-coverage modules. The ratchet
  exists to prevent regression, not to force a coverage sprint.

### Next-iteration upgrade: diff-coverage

The bead scope flagged **diff-coverage** (coverage of CHANGED lines only)
as a likely better gate for a 61K-line codebase: whole-codebase coverage
is noisy for small PRs. This is out of scope for the current ratchet bead
and should be filed as a follow-up. Candidate tools:

- Python: `diff-cover` (PyPI) integrates cleanly with coverage.xml.
- JavaScript/TypeScript: `diff-cover` also consumes v8/lcov output.

When we file the follow-up, the gate becomes "changed lines must hit X%
coverage" with X set conservatively (≥ 80% seems reasonable given the base
rates above) and the whole-codebase thresholds stay as a floor.

### Running coverage locally

```bash
# Backend — inside the container (matches the CI invocation).
docker exec ecm-ecm-1 sh -c 'cd /app && python -m pytest \
  --ignore=tests/e2e -m "not slow" --no-header -p no:warnings'
# Coverage is auto-enabled via pytest.ini addopts. To disable for a quick
# single-file run: add --no-cov.

# Frontend — from the host.
cd frontend && npm run test:coverage
```

If a local run drops below threshold, fix the root cause (add a test, remove
dead code, or adjust .coveragerc omit if the file is genuinely non-runtime).
Do **not** lower the threshold in the config.

## When to Run Tests

- **Backend tests**: MANDATORY for any backend code changes
- **Frontend tests**: MANDATORY for any frontend code changes
- **E2E tests**: Run on merge to main only (CI/CD pipeline)

## One source of truth per required check

`dev` branch protection requires seven status checks: `Backend Tests`,
`Frontend Tests`, `MCP Server Tests`, `Semgrep Lint`, `Version Consistency`
(all from `.github/workflows/test.yml`), and `CodeQL Analysis (python)` /
`CodeQL Analysis (javascript-typescript)` (the `codeql-analysis` matrix in
`.github/workflows/build.yml`).

**Invariant: each of those names is emitted by exactly one job, and that job
runs on every pull request.**

### What went wrong before (bead `enhancedchannelmanager-5rwzy`)

`test.yml` triggered on `paths-ignore: ['**.md', '.beads/**']` and a sentinel
workflow, `docs-only-pass.yml`, triggered on `paths: ['**.md', '.beads/**']`.
Those two filters look like complements and are not. A pull request touching
both code and Markdown matches both, so every required context existed twice:
once real, once a job whose only step was an `echo`.

Every shipped change carries a `CHANGELOG.md` entry, so nearly every pull
request in this repo has that mixed shape. On PR #797 the duplicate went
live: `Backend Tests` reported **`failure` and `success` on the same commit**,
and the failure was a genuine test defect, not a flake. It was caught only
because the reviewer read every instance of the context individually instead
of trusting the aggregate.

### The shape that replaced it

1. Neither `test.yml` nor `build.yml` has a path filter. Both run on every
   push and pull request to `main` and `dev`.
2. A `detect` job in each classifies the changed file set by calling
   `scripts/classify_changed_paths.py`. That script holds the single
   classifier's explicit inert-path allowlist, which
   previously lived in three `paths` blocks that drifted apart.
3. Every job whose name is a required context **always runs**, so the context
   is emitted exactly once, and gates its expensive **steps** on
   `needs.detect.outputs.code_paths_changed`. On an inert machine-state change the job
   does a cheap no-op and passes honestly.
4. `docs-only-pass.yml` is deleted.

### Two rules when editing these workflows

- **Never give a required-context job a path filter or a job-level `if:` that
  can evaluate false.** GitHub counts a **skipped** job as satisfying a
  required status check, so skipping is the same hole in a new place. Gate the
  steps instead.
- **Never make a duplicate emitter fail instead.** A second check-run
  reporting failure alongside a real one that passes blocks every mixed pull
  request. The answer is one emitter, not a louder second one.

Jobs that are **not** required contexts (`Fake-Test Guard`, `Visual
Regression`, `Operator Workspace Release Matrix`, `Screen-Reader-Only
Rendering Guard`, the image builds) do skip at the job level on a
inert-only change. Promoting any of them to a required check means
converting it to the step-gated shape first.

`backend/tests/unit/test_classify_changed_paths.py` enforces all of this: it
pins the classifier's accept/reject boundary and its fail-open behaviour, and
it fails the pull request if a required context ever gains a second emitting
job or if `test.yml` / `build.yml` regain a path filter.

### Fail-open, on purpose

The gate expression is always `needs.detect.outputs.code_paths_changed != 'false'`. If
the `detect` job dies its output is empty, the comparison is true, and the
real work runs. Classifying code as documentation is the dangerous direction,
because it turns a required check green without running the work it is named
for. Classifying documentation as code only costs runner minutes.

### The second verdict: `docs_site_affected`

The same classifier emits a second, independent output. `docs_site_affected`
is true when a changed path is one the published user-guide site is built
from: anything under `docs/user_guide/` or `docs/images/user_guide/`, plus
`docs/index.md`, `mkdocs.yml`, `docs/requirements-docs.txt`, and
`.github/workflows/docs-pages.yml`. That list used to live in the `paths:`
filter of `docs-pages.yml`, where nothing could see it drift out of step with
the mkdocs nav. It lives in the classifier now, and `docs-pages.yml` reads it.

The two verdicts are independent, and all four combinations occur: editing
`docs/user_guide/index.md` and `mkdocs.yml` are code-gated **and**
site-affecting, `docs/testing.md` is code-gated and **not** site-affecting,
and `.beads/issues.jsonl` is inert to both.

`docs_site_affected` fails open in the **opposite** direction, to `true`. Its
dangerous verdict is a wrong `false`, which skips the rebuild and leaves the
published site stale behind merged content, so `docs-pages.yml` gates on
`!= 'false'`. A test derives the check from `mkdocs.yml` itself: every page in
the nav must be a path the classifier recognises, so adding a published page
outside the known prefixes fails the pull request rather than quietly
disabling its deploy.

### What a green required check actually ran

Because six of the seven required checks gate their real work on
`code_paths_changed`, the check **name** is the same whether a suite ran or not.
`Backend Tests` reads as "the backend tests ran" either way.

So every required job writes one line to `$GITHUB_STEP_SUMMARY` naming what it
did, and the three that produce a JUnit report run
`scripts/ci_junit_summary.py` to put the real test count in that line rather
than a claim:

```
Backend Tests: ran the backend pytest suite. 2147 tests, 0 failed, 0 errored.
Backend Tests: inert machine-state change, the pytest suite was NOT run.
```

The summary changes no conclusion and gates nothing; it makes the rollup
readable without opening each job log.

Two properties keep it from becoming a second source of untruth. Each line
reports the real `steps.<id>.outcome` of the step that does the work, so a job
that died at lint does not claim vitest ran; an outcome that is empty because
the step was never reached renders as `did not run`. And every summary step
carries `continue-on-error: true`, because it runs inside required contexts
and a cosmetic writer must never be the reason a passing suite reports red.
`ci_junit_summary.py` returns 0 on every runtime path, including a missing or
unparseable report. Argparse is the one exception: a malformed invocation
exits 2 before the script's own code runs, which is a wiring bug rather than a
runtime condition, and `continue-on-error` absorbs it either way.

`mkdocs build --strict` runs on every pull request as a step in the
**Operator Docs** job (bead `enhancedchannelmanager-pb2s4`). It used to run
only after the merge, in `docs-pages.yml`, so a broken user-guide link merged
green and surfaced as a failed Pages deploy on `dev`. It checks something
disjoint from `npm run docs:check`: pb2s4 records a broken link that the
first passed and the second caught. `Operator Docs` is deliberately ungated
and deliberately **not** a required context, so adding the step changes no
branch-protection surface.

**That last point bounds what this buys.** Because `Operator Docs` is not
required, a broken site build does **not** block the merge. It turns the PR's
`mergeStateStatus` to `UNSTABLE` and stays there. The enforcement is the
shipper reading the rollup and refusing to merge past it, per
`docs/shipping.md` section "When `UNSTABLE` is the terminal state" - not
branch protection. Making it required is a Phase 2 branch-protection change
and is deliberately out of scope: a required name that fails to appear on a
PR wedges that PR permanently, and `enforce_admins` is true on `dev`.

## Container Freshness Check

**Before triaging any "test failure" bead that reports failures from
`ecm-ecm-1`, verify the container is actually running current `dev`
HEAD.** This pattern (engineer files a "tests failing on dev" bead;
investigation reveals tests pass locally and the container is stale)
recurred enough times (beads `5dug8`, `0gcu9`, others) that it
deserves its own check (bd-h0wfu).

The container reports its source SHA in two places, populated from
Docker build args at image-build time (`Dockerfile`: `ARG GIT_COMMIT`):

```bash
# Method A — JSON endpoint (no auth required, /api/version is exempt)
curl -s http://localhost:6100/api/version | jq -r .git_commit

# Method B — Prometheus metric label
curl -s http://localhost:6100/metrics | grep ecm_app_info
# ecm_app_info{git_sha="<sha>",release_channel="latest",version="<ver>"} 1.0
```

Compare against `origin/dev`:

```bash
git fetch origin dev
git rev-parse origin/dev
```

**If the SHAs match**, the container is current. Investigate the test
failure as real. **If they don't match**, the container is stale; redeploy
current dev HEAD before triaging:

```bash
# Backend
docker cp backend/main.py ecm-ecm-1:/app/main.py
docker cp backend/routers/. ecm-ecm-1:/app/routers/
docker restart ecm-ecm-1

# Frontend
cd frontend && npm run build
docker exec ecm-ecm-1 sh -c 'rm -rf /app/static/assets/*'
docker cp dist/. ecm-ecm-1:/app/static/
```

Re-run the failing tests. If they now pass, the bead was deploy drift,
not a code defect. Close it without filing a code bead. The
container-first development workflow (per `CLAUDE.md`) means agents
`docker cp` specific files when iterating, so the shared `ecm-ecm-1`
container can lag origin/dev when nobody re-deploys after a merge to
`dev`. The freshness check above is a one-line cure for the entire
class of fake test-failure beads.

The same SHA labels also drive container-drift dashboards in Grafana:
`max by (git_sha) (ecm_app_info)` shows the running build identity, and
an alert can fire when it diverges from the `origin/dev` SHA published by
the build pipeline.

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

Flaky tests are tests that pass and fail non-deterministically without code changes.
They are treated as **P1 bugs** (per the QA hard rules). The baseline established in
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
  only sometimes present. These are **not true flakes**. They are environment
  drift and should be fixed by making the test defensive, not by re-running.

If a test fails identically every run for the same reason, it is **deterministically
broken**. Repair the test or the code. Do not mark it `flaky`.

### Re-run policy (CI & local)

| Scenario | Allowed re-runs |
|----------|-----------------|
| PR check fails on one test, passes on re-run | Re-run **once** to confirm flake. If flaky, file a `flaky`-labelled bead before merge. |
| PR check fails on same test twice in a row | Treat as deterministic break. Do not merge. |
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
3. Do **not** leave `@pytest.mark.xfail` on flaky tests: xfail masks real
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
in the **flagged-in-last-30-runs** list. Those are known-flaky, and the PR
needs a clean re-run (or an explicit note that the flake is unrelated to the
change).

The `Flake List PR Comment` workflow
(`.github/workflows/flake-pr-comment.yml`, bead xq19y) automates this: on PR
open / sync it walks the last 30 `Tests` workflow runs on the PR's base
branch, parses the `junit-backend` and `junit-frontend` artifacts, and posts
or updates a single PR comment listing every test that failed in at least
one of those runs. The comment is identified by a hidden marker and updated
in place, avoiding a comment-storm on rebased branches. The comment is
informational only; it does not gate merge.

Reviewer workflow:

1. Open the PR. Read the **Flake list (last 30 runs on base branch)** comment.
2. If the failing test on this PR appears in that list → re-run once. If
   still fails → investigate; probably unrelated to the PR but do not merge
   until the next CI run is green.
3. If the failing test is **not** in that list → treat as deterministic and
   block the merge until fixed.

Manual fallback (if the automation is offline): pull the list of
`flaky`-labelled open beads with `bd list --label flaky` and apply the same
rule.

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

**Not flakes, but deterministic environment drift (cleared in bead 0gcu9):**

The original three BE tests covered by `enhancedchannelmanager-0gcu9` were:
- `tests/integration/test_api_tasks.py::TestRunTaskWithSchedule::test_run_task_with_schedule_id`:
  referenced a POST route that was removed from `routers/tasks.py`. **Test
    deleted.**
- `tests/integration/test_router_registration.py::TestRoutePrefixes::test_all_routes_under_api`:
  failed because the SPA fallback route `/{full_path:path}` registers only
    when `backend/static/` exists (present in prod image, absent on CI). **Fixed
    by adding the SPA fallback path to `NON_API_ROUTES`.**
- `tests/unit/test_ffmpeg_execution.py::TestExecutionSafety::test_validates_output_path_writable`:
  the code under test promised an output-writability check its docstring
    described. **Resolved by deleting `ffmpeg_builder/execution.py` and the
    whole `test_ffmpeg_execution.py` file: the module was dead code (zero live
    callers; ECM builds ffmpeg command configs but never executes ffmpeg).**

None of these tests need deselection any longer; the 3-run cadence command
below still references the two `test_observability_middleware` flakes tracked
under `enhancedchannelmanager-hhsz0`.

### Full-suite 3-run cadence command

The exact command used for the `tp681` baseline and the quarterly sweep:

```bash
# BE — from inside ecm-ecm-1
python -m pytest tests/ --ignore=tests/e2e \
  --deselect tests/routers/test_observability_middleware.py::TestTraceIdMiddleware::test_trace_id_appears_in_log_line \
  --deselect tests/routers/test_observability_middleware.py::TestTraceIdMiddleware::test_generated_trace_id_matches_uuidv4_format_in_logs \
  -p no:cacheprovider --tb=line -q

# FE — from host (ecm-ecm-1 has no Node tooling)
cd frontend && npx vitest run --reporter=default
```

Remove the relevant `--deselect` once a flake/drift bead closes.
