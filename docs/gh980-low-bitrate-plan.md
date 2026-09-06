# GH980 Low-Bitrate Frozen Contract

Scope: GH980 / enhancedchannelmanager-8gmk8.4 only, first feature on
`feat/low-bitrate-yaml-editor`, base `066d340d`. GH971 follows serially in the
same worktree and eventual combined PR. This document records the PO-approved
review boundary, not a claim that implementation or verification is complete.

## Approved Behavior

- Classify low iff fresh bitrate in bits/second is strictly below
  `width * height * low_bitrate_threshold`. FPS does not enter the formula.
- The operator threshold defaults to 1.0 bit/pixel/second and must be positive
  and finite. No arbitrary upper bound; finite-arithmetic guards are allowed.
- Prefer fresh measured throughput, then fresh video metadata, then fresh
  overall format metadata. Use effective current dimensions, including the
  opt-in resolution detector's replacement dimensions. Do not reuse stale DB
  bitrate or dimensions. Missing, zero, negative, or nonfinite inputs clear
  the flag. Probe failure and timeout also clear it.
- Classification is computed and visible regardless of sorting toggles.
  False means not classified low, not guaranteed healthy. Probe success and
  a low-bitrate flag can coexist.
- `deprioritize_low_bitrate` defaults to false and remains subject to the
  existing global `deprioritize_failed_streams` gate in Priority mode.
- `low_bitrate` follows `low_fps` in default order and overlapping-category
  precedence. Preserve saved custom order and append the new category when
  missing, rather than resetting old settings.
- Points mode exposes an explicit `low_bitrate` true/false condition only:
  no implicit penalty and no injected default rule.
- Existing numeric bitrate ranking and direct single-field sorting remain
  unchanged. Threshold changes affect future probes only; toggle/order changes
  affect future sorts. No retroactive DB rewrite or reclassification.
- Additive JSON settings updates preserve the new values when older clients
  omit them.

## Delivery Surfaces

- Persist `StreamStats.is_low_bitrate`, serialize it, and migrate existing rows
  to false. Alembic head was verified as `0055`; new revision is `0056`.
  Upgrade/downgrade must accommodate metadata bootstrap patterns.
- Probe badges, progress, history, result lists/counts, and the generic MCP
  `get_probe_results` bucket expose the classification analogous to low FPS.
- Settings expose threshold, toggle, category order, and Points selector.
- Thread constructors and reloads through main, task, scheduled/manual probe,
  pipeline and event-sync entry points. Both Smart Sort fact adapters agree.

## Verification Plan

Tests are written before the corresponding implementation and proven red.
Focused tests cover:

- Pure formula below/equal/above at SD, HD, and UHD; FPS independence; invalid
  values, freshness versus persisted state, failure/timeout/unknown reset.
- Fresh metadata bit_rate, BPS/BPS-eng tags, format fallback, throughput
  precedence, and resolution-detector effective dimensions.
- Always-on classification; global/per-category gates; overlapping flags;
  old custom orders and omitted settings; explicit true/false Points rules;
  shared/pipeline adapter parity and unchanged direct/numeric sorting.
- SQLite migration upgrade/downgrade and metadata bootstrap, persisted API
  serialization, settings API live application, generic MCP bucket rendering.
- Rendered Settings components, plus a local real-API browser seam if feasible.
  Any environment gap is reported explicitly, not represented as a pass.

Capture actual ffprobe JSON from a finite locally generated ffmpeg video before
writing metadata integration tests. Label it generated local test media, not
customer or production evidence. It verifies available fields/units, not a
calibrated visual-quality model. No production captures or Dispatcharr writes.

## Operator Semantics

Fixture provenance: `backend/tests/fixtures/gh980_generated_local_ffprobe.json`
was captured from disposable network-disabled local image
`ecm-resdet:609f20fc`, not a running service. Generation command:
`ffmpeg -hide_banner -loglevel error -f lavfi -i testsrc2=size=640x480:rate=25 -t 1 -c:v mpeg4 -b:v 400k /tmp/generated-local.mp4`.
Capture command: `ffprobe -v error -show_entries stream=codec_type,width,height,bit_rate,r_frame_rate:format=bit_rate,duration -of json /tmp/generated-local.mp4`.
The observed video bit_rate was 1514264 bps and format bit_rate 1522296 bps;
the encoder target 400k is not an asserted measured rate. BPS tag variants
are synthetic parsing cases, not fields observed in this capture.

At threshold 1.0, 640x480 has a 307,200 bps floor, 1920x1080 has a
2,073,600 bps floor, and 3840x2160 has an 8,294,400 bps floor. Equality is not
low. At threshold 0.5 these floors halve. This resolution-based heuristic is
not a watchability guarantee: content, encoding, and transport vary. Measured
throughput includes connection time in the existing probe measurement.

## Exclusions And Execution Boundary

No codec adaptation, threshold profiles, new dependencies, sort rewrite,
GH971 implementation, or bonus feature acceptance criteria. No production
service changes. Whole canonical backend/frontend gates may run after GH971;
this dispatch runs focused feature verification and reports exact coverage.
Per-feature commit is authorized; no push, PR creation, merge, tracker status
change or closure. Preserve this worktree for the serial follow-up.

## Implementation Verification And Handoff

GH980 implementation evidence (2026-09-06; not independent review approval):

- Initial 51 classification/settings/Points tests failed before implementation,
  then passed. Subsequent API/adapter tests failed on missing wiring; migration
  test failed on missing revision 0056 after correcting its legacy-row fixture.
  Rendered Settings tests failed on missing controls before implementation.
- Final expanded backend subset: 312 passed, no warnings, 144.68 seconds.
  It covers GH980, group-scope/scheduled and failed reprobes, black-screen
  regression, both sorting modes, bulk probe envelopes, event probes and stats
  routes. This is not the canonical whole-backend coverage gate.
- Additional settings/restart, pipeline, resdet and event-probe regression run:
  369 passed. Four unawaited-AsyncMock warnings occurred in existing settings
  rebuild tests; this is not represented as a warning-free run.
- Five rendered component test files: 83 passed. Full frontend ESLint and
  TypeScript checks passed. The exact-source Vite build and three Chromium
  tests passed, including real settings API persistence/reload, the explicit
  Points selector, a 390x844 mobile threshold render, and existing manual sort.
- MCP generic probe-results rendering: three tests passed. No production MCP
  renderer change was needed. Python compilation and `git diff --check` passed.
  Alembic reports 0056 as the sole head; upgrade/downgrade/bootstrap tests pass.
- A broader subset initially timed out at 120 seconds and exposed an incomplete
  scheduled-prober test double. The new counter was added to that fixture.
  GH980 lifecycle history was moved to per-test temporary files after a combined
  run exposed history contamination. The final 312-test run supersedes both.

Reproduction from the worktree root (Node 24.13.0; root and frontend each have
their own `npm ci` dependencies, no main-checkout dependency symlinks):

```bash
ECM_TEST_CONFIG_ROOT=/tmp/opencode TMPDIR=/tmp/opencode scripts/backend-gate.sh --subset tests/unit/test_low_bitrate.py tests/routers/test_low_bitrate_settings.py tests/integration/test_low_bitrate_migration.py tests/unit/test_stream_probe_group_scope.py tests/unit/test_stream_probe_task_smart_sort.py tests/unit/test_failed_stream_reprobe.py tests/unit/test_black_screen_detection.py tests/unit/test_smart_sort_point_settings.py tests/unit/test_sort_criteria.py tests/unit/test_smart_sort_evaluator.py tests/unit/test_stream_prober_bulk.py tests/unit/test_epg_event_probe.py tests/routers/test_stream_stats.py
TMPDIR=/tmp/opencode PYTHON=/home/lecaptainc/ecm/enhancedchannelmanager/.venv/bin/python E2E_BASE_URL=http://127.0.0.1:1 npx playwright test e2e/smart-sort-points.spec.ts --project=chromium --workers=1 --retries=0 --reporter=list
```

The browser spec starts its own nonce-checked backend and preview on dynamic
loopback ports; the base URL above is intentionally unusable. Its temporary
database/build directories are owned and cleaned by the harness. Test config
is dynamically allocated beneath `/tmp/opencode`. Keep waits synchronous;
allow at least 600 seconds for the expanded subset tool call (the group-scope
file alone took 132 seconds). The shared main-checkout Python interpreter is
used read-only; no Python package installation was performed.

Remaining verification: canonical whole-backend and whole-frontend unit gates
are reserved for the combined branch after GH971. Ruff is absent from both the
supplied Python environment and PATH; no backend Ruff pass is claimed. No new
dependency was added. No production probe/service capture, Dispatcharr write,
deployment, external quality calibration, or independent review was performed.
All started test commands reached a terminal result; no watcher remains.
