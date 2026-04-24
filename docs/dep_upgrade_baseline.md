# Pre-Dependency-Bump Baseline (6rrl5 epic)

**Status:** Reference baseline. Frozen until 6rrl5 epic closes.
**Owner:** SRE / Project Engineer (rotating with whoever lands a 6rrl5 child).
**Bead:** [enhancedchannelmanager-6rrl5.3](https://github.com/MotWakorb/enhancedchannelmanager) (P1, gating).
**Captured from SHA:** `3d97433a847fa335d80ddde37a69303b14935d27` (dev tip — `Merge pull request #148 from MotWakorb/chore/v0.16.0-0056`).
**App version:** `0.16.0-0056` (matches `frontend/package.json` and `backend/main.py`).
**Captured:** 2026-04-23 (UTC 2026-04-24T02:58:49Z).
**Capture host:** Ubuntu 24.04 LTS, x86_64, 16 vCPU, 60 GiB RAM (worktree dev box).

## Why this exists

The 6rrl5 epic bumps every major frontend dep at once (TS 5→6, Vite 7→8, React 18→19, plugin-react 5→6, ESLint 9→10, jsdom 24→29, @dnd-kit 6→10) plus follow-on backend bumps (starlette 1.0, fastapi 0.137+, uvicorn). Each child PR needs an objective answer to *"did this bump regress build size, runtime, or test speed?"* — not a vibe check.

This document captures the reference values **before any bump lands** and defines the regression-check procedure each child PR runs against this baseline. Without it, every "looks fine to me" review of a 6rrl5 child is a guess.

The post-merge comparison procedure is in [§ Per-bump regression check](#per-bump-regression-check).

## Pinned dependency versions at capture

These are the versions every metric below was measured against. When a 6rrl5 child re-runs the captures, only the bumped dep should differ.

| Dep | Version | Source |
|---|---|---|
| TypeScript | 5.9.3 | `frontend/package.json` |
| Vite | 7.3.2 | `frontend/package.json` |
| React | 18.3.1 | `frontend/package.json` |
| `@vitejs/plugin-react` | 5.1.4 | `frontend/package.json` |
| ESLint | 9.39.2 | `frontend/package.json` |
| jsdom | 24.1.3 | `frontend/package.json` |
| `@dnd-kit/core` | 6.3.1 | `frontend/package.json` |
| FastAPI | 0.136.0 | `backend/requirements.txt` |
| Starlette | 0.52.1 | `backend/requirements.txt` |
| Uvicorn | 0.44.0 | `backend/requirements.txt` |
| Pydantic | 2.13.3 | `backend/requirements.txt` |
| SQLAlchemy | 2.0.49 | `backend/requirements.txt` |
| httpx | 0.28.1 | `backend/requirements.txt` |

---

## Metric 1 — Frontend bundle size

**Why this matters:** Vite/React/plugin-react bumps are the most likely to silently bloat the JS bundle. Larger bundles = slower first paint on the LAN/customer side.

**Capture method:**
```bash
cd frontend && npm ci && rm -rf dist && npm run build
```
Sizes are read from Vite's own `transforming` output. Gzipped totals computed with `gzip -9 -c`.

**Top-level totals:**

| Metric | Value | Source |
|---|---|---|
| `dist/` total (raw, includes html + favicons + chunks) | **4.5 MiB** | `du -sh dist` |
| `dist/assets/` total (raw, JS+CSS+image chunks) | **2.6 MiB** (2,640,483 bytes) | `du -ab dist/assets` |
| All JS chunks gzipped (`gzip -9`) | **528.3 KiB** (540,990 bytes) | `find dist/assets -name '*.js' -exec gzip -9 -c {} +` |
| All CSS chunks gzipped (`gzip -9`) | **74.2 KiB** (75,956 bytes) | `find dist/assets -name '*.css' -exec gzip -9 -c {} +` |
| Modules transformed by Vite | **909** | Vite build output |
| Build wall-clock (warm `node_modules`) | **3.09s** (Vite reported), 3.35s (`time`) | `vite build` + `/usr/bin/time -v` |

**Per-chunk (raw + gzipped, from Vite output):**

| File | Raw | Gzipped |
|---|---|---|
| `dist/index.html` | 0.71 KB | 0.40 KB |
| `dist/assets/ECMLogo-CZKhLpfA.png` | 18.00 KB | (binary) |
| `dist/assets/JournalTab-BrOsdVeQ.css` | 6.28 KB | 1.58 KB |
| `dist/assets/GuideTab--u2vECDc.css` | 7.76 KB | 1.72 KB |
| `dist/assets/M3UChangesTab-BiUDm80e.css` | 7.96 KB | 1.75 KB |
| `dist/assets/ExportTab-BSPVUBB-.css` | 9.40 KB | 1.90 KB |
| `dist/assets/LogoManagerTab-r-l0r99D.css` | 10.17 KB | 2.10 KB |
| `dist/assets/M3UManagerTab-0bGb7gR2.css` | 22.49 KB | 3.68 KB |
| `dist/assets/FFMPEGBuilderTab-BU4qPxxI.css` | 24.60 KB | 3.64 KB |
| `dist/assets/AutoCreationTab-Cl-LwVQu.css` | 30.83 KB | 5.13 KB |
| `dist/assets/StatsTab-mR2vPSXt.css` | 35.03 KB | 4.82 KB |
| `dist/assets/EPGManagerTab-DSHdI09a.css` | 40.72 KB | 6.15 KB |
| `dist/assets/SettingsTab-BLj5a9C7.css` | 84.75 KB | 11.66 KB |
| `dist/assets/index-CrJ5p0cX.css` | 232.43 KB | 31.96 KB |
| `dist/assets/useAsyncOperation-CQw_wkbo.js` | 0.38 KB | 0.28 KB |
| `dist/assets/autoCreationApi-DTxlyfM0.js` | 1.62 KB | 0.55 KB |
| `dist/assets/formatting-cV93jlRm.js` | 2.51 KB | 0.94 KB |
| `dist/assets/JournalTab-CkK9XtMJ.js` | 8.52 KB | 2.33 KB |
| `dist/assets/M3UChangesTab-Dx4vRHVF.js` | 11.58 KB | 2.80 KB |
| `dist/assets/LogoManagerTab-Bio666_0.js` | 13.55 KB | 3.66 KB |
| `dist/assets/GuideTab-Cv3zbznU.js` | 23.82 KB | 7.94 KB |
| `dist/assets/ExportTab-C7mcwhjF.js` | 38.53 KB | 8.44 KB |
| `dist/assets/M3UManagerTab-DDGxcNtd.js` | 64.69 KB | 14.81 KB |
| `dist/assets/FFMPEGBuilderTab-Bh6tHQQg.js` | 100.18 KB | 26.40 KB |
| `dist/assets/AutoCreationTab-BU67OEGC.js` | 100.64 KB | 23.10 KB |
| `dist/assets/EPGManagerTab-Y542ClZo.js` | 133.00 KB | 32.62 KB |
| `dist/assets/SettingsTab-uK8MmMLG.js` | 269.35 KB | 58.98 KB |
| `dist/assets/StatsTab-BBEmCzS6.js` | 431.65 KB | 123.06 KB |
| `dist/assets/index-CErS0nCi.js` | 910.07 KB | 238.38 KB |

**Pre-existing warning at baseline:** Vite emits a `(!) Some chunks are larger than 500 kB after minification` warning for `index-CErS0nCi.js` (910 KB raw / 238 KB gzip) and `StatsTab-BBEmCzS6.js` (431 KB raw / 123 KB gzip). This is **not** a 6rrl5 regression — it pre-dates this epic. A bump only regresses when the warning gains a new chunk or the existing chunks grow past their thresholds below.

**Regression thresholds (per-bump):**

| Signal | Threshold for "regression" | Action |
|---|---|---|
| Total JS gzipped | > +10% (i.e. > 581 KiB) | Block bump, investigate |
| Total CSS gzipped | > +10% (i.e. > 81 KiB) | Block bump, investigate |
| Any individual chunk gzipped | > +20% vs baseline row | Investigate; may merge with PO sign-off if justified |
| New chunk over 500 KB raw appearing | Any | Investigate; document in PR |
| Vite build wall-clock | > +50% (i.e. > 5.0s) | Investigate; build-perf regressions block |

---

## Metric 2 — Docker image size

**Why this matters:** Backend dep bumps (uvicorn, starlette, fastapi) and Python base image changes can quietly add hundreds of MB to the deploy image, slowing every CI build, push, and pull. The frontend `dist/` ships *inside* this image too, so frontend bumps are also captured here.

**Capture method:**
```bash
docker build -f Dockerfile -t ecm-baseline:6rrl5 .
docker images ecm-baseline:6rrl5 --format '{{.Size}}'
```
Built fresh from the captured SHA on the worktree host (no shared `ecm-ecm-1` container touched — image tag is `ecm-baseline:6rrl5` to keep isolation).

**Result:**

| Metric | Value |
|---|---|
| `ecm-baseline:6rrl5` final image disk usage | **711 MB** |
| Image SHA | `sha256:ad987b1bb1aad45da95d6fbb467e35565eefd36137c0675ba82125fa36dbfaa0` |
| Stages | `frontend-builder` (node:20-alpine) → `python-builder` (python:3.12-slim) → final (python:3.12-slim) |

**Regression thresholds (per-bump):**

| Signal | Threshold | Action |
|---|---|---|
| Final image size | > +10% (i.e. > 782 MB) | Investigate; +50 MB is the typical "should I be worried?" line for a single-dep bump |
| Final image size | > +25% (i.e. > 889 MB) | Block bump unless justified (e.g., security-mandated base image change) |

---

## Metric 3 — Backend cold-start time

**Why this matters:** Uvicorn / Starlette / FastAPI bumps directly affect ASGI startup. Cold-start is what the Dockerfile `HEALTHCHECK` waits on after `docker restart ecm-ecm-1` and what determines deploy-window length.

**Capture method:** Loop 5 cold runs of the freshly-built image, timing from `docker run` invocation (process start) to first 200 from `GET /api/health`. Container internally listens on port 6100 (per `Dockerfile EXPOSE 6100 6143`) — host mapped to 6109 to avoid clobbering any local ECM container. Probe script: `/tmp/cold_start_probe.sh` (see below for inline copy).

```bash
docker rm -f ecm-baseline-coldstart || true
start=$(date +%s%N)
docker run -d --name ecm-baseline-coldstart \
  -p 6109:6100 \
  -e CONFIG_DIR=/config \
  -e ADMIN_USER=admin -e ADMIN_PASS=admin \
  -e SECRET_KEY=baseline-test-key-not-for-prod-32chars \
  -e DISPATCHARR_URL=http://127.0.0.1:9999 -e DISPATCHARR_USER=u -e DISPATCHARR_PASS=p \
  ecm-baseline:6rrl5
# poll until 200, then end=$(date +%s%N); echo $(( (end-start) / 1000000 ))ms
```

**Results (5 consecutive cold starts):**

| Run | Time-to-first-200 (ms) |
|---|---|
| 1 | 5135 |
| 2 | 5082 |
| 3 | 5103 |
| 4 | 5076 |
| 5 | 5152 |
| **min** | **5076** |
| **max** | **5152** |
| **median** | **5103** (~5.1s) |

**Note on isolation:** The container's `/api/health` is the cheap liveness probe (does NOT depend on Dispatcharr). The richer `/api/health/ready` is intentionally NOT used for cold-start measurement here because it would couple the metric to the Dispatcharr stub's reachability — which makes the number a network test, not a startup test. See `backend/routers/health.py:196` for the contract.

**Regression thresholds (per-bump):**

| Signal | Threshold | Action |
|---|---|---|
| Median cold-start | > +20% (i.e. > 6.1s) | Investigate; ASGI bumps in particular |
| Median cold-start | > +50% (i.e. > 7.7s) | Block bump |
| Any single run | > 60s | Block bump (script TIMEOUT) |

---

## Metric 4 — Backend test-suite runtime

**Why this matters:** Pytest runtime tracks both raw test count growth and per-test slowdown from dep bumps (starlette TestClient changes, httpx response shape changes, pydantic validation cost).

**Capture method:** Same flags CI uses (`.github/workflows/test.yml`). `--no-cov` added here to isolate pure test runtime from the coverage tracer cost (CI keeps coverage; the per-bump check should run both).

```bash
mkdir -p /tmp/ecm_baseline_test_config
rm -f /tmp/ecm-baseline-test.db
python3 -c "import sqlite3; sqlite3.connect('/tmp/ecm-baseline-test.db').close()"
cd backend
CONFIG_DIR=/tmp/ecm_baseline_test_config \
RATE_LIMIT_ENABLED=0 \
ECM_CI_DB_PATH=/tmp/ecm-baseline-test.db \
/usr/bin/time -v .venv/bin/python -m pytest \
  --ignore=tests/e2e -m 'not slow' \
  --tb=short --no-header -p no:warnings \
  --durations=10 --no-cov
```

**Results:**

| Metric | Value |
|---|---|
| Tests collected & passed | **3035** |
| Pytest reported runtime | **69.08s** |
| Wall-clock (`/usr/bin/time`) | **72.77s** (1:12.77) |
| User CPU | 60.42s |
| Max RSS | 287 MB |
| Failures / errors | 0 |

**Slowest 10 tests (from `--durations=10`):**

| Test | Time |
|---|---|
| `tests/unit/test_failed_stream_reprobe.py::TestFailedStreamReprobeTask::test_scopes_to_last_probe_groups` | 1.01s |
| `tests/unit/test_failed_stream_reprobe.py::TestFailedStreamReprobeTask::test_no_scope_reprobes_all` | 1.00s |
| `tests/unit/test_failed_stream_reprobe.py::TestFailedStreamReprobeTask::test_reprobes_failed_and_timeout` | 1.00s |
| `tests/integration/test_event_loop_responsiveness.py::TestRequestTimeoutMiddleware::test_auto_creation_crud_subject_to_timeout` | 1.00s |
| `tests/integration/test_event_loop_responsiveness.py::TestRequestTimeoutMiddleware::test_slow_handler_returns_504` | 1.00s |
| `tests/unit/test_auto_creation_executor.py::TestVerifyEpgAssignments::test_skips_when_already_persisted` | 1.00s |
| `tests/unit/test_auto_creation_executor.py::TestVerifyEpgAssignments::test_retries_on_mismatch` | 1.00s |
| `tests/unit/test_auto_creation_executor.py::TestVerifyEpgAssignments::test_handles_get_failure` | 1.00s |
| `tests/integration/test_event_loop_responsiveness.py::TestHealthRespondsDuringCpuBoundWork::test_health_fast_during_xmltv_generate` | 0.81s |
| `tests/integration/test_event_loop_responsiveness.py::TestHealthRespondsDuringCpuBoundWork::test_health_under_500ms_while_normalize_batch_runs` | 0.80s |

**Note:** All slowest tests cluster around 1.0s, suggesting they hit deliberate `asyncio.sleep`/timeout boundaries — that's a fixed cost, not load-dependent. A 6rrl5 bump that pushes one of these significantly past 1.0s without an explicit sleep change is suspicious.

**Regression thresholds (per-bump):**

| Signal | Threshold | Action |
|---|---|---|
| Total pytest runtime | > +30% (i.e. > 90s reported, > 95s wall) | Investigate; backend dep bumps especially |
| Any single test moves into top-10 with > 2.0s | New entry over 2s | Investigate |
| Test count drops below 3035 | Any drop without explanation in PR | Block — silent skips are P1 |
| Test count grows but runtime grows >2× new-test count × 1s | Investigate | Bump may be slowing existing tests |

---

## Metric 5 — Frontend test-suite runtime

**Why this matters:** Vitest/jsdom bumps directly affect this. React 19 may also change render timing in tests.

**Capture method:**
```bash
cd frontend && /usr/bin/time -v npm test
# `npm test` → `vitest run` (per frontend/package.json)
```

**Results:**

| Metric | Value |
|---|---|
| Test files | **48** |
| Tests passed | **1142** |
| Vitest reported `Duration` | **5.77s** |
| Vitest reported breakdown | transform 4.97s · setup 10.01s · import 11.97s · tests 28.37s · environment 22.10s |
| Wall-clock (`/usr/bin/time`) | **6.05s** |
| User CPU | 67.18s |
| Max RSS | 307 MB |

**Note:** Vitest's per-phase breakdown sums to far more than wall-clock because phases run in parallel across worker threads. The actionable numbers are **wall-clock** (6.05s) and **`Duration`** (5.77s).

**Pre-existing warning at baseline:** A `BulkRuleSettingsModal` test emits a React `act(...)` warning (not a failure). This is **not** a 6rrl5 regression — it pre-dates this epic. A bump that causes new `act` warnings in other suites IS a regression signal worth investigating.

**Regression thresholds (per-bump):**

| Signal | Threshold | Action |
|---|---|---|
| Total wall-clock | > +30% (i.e. > 7.9s) | Investigate |
| Total wall-clock | > +100% (i.e. > 12s) | Block bump |
| Test count drops below 1142 | Any drop without explanation in PR | Block — silent skips are P1 |
| New `act(...)` warnings in unrelated suites | Any | Investigate React 19 / testing-library compat |

---

## Metric 6 — CI pipeline end-to-end duration

**Why this matters:** CI runtime is the per-PR feedback loop. A 30% bump-induced slowdown across every PR is a real productivity tax.

**Capture method:**
```bash
gh run list --workflow=build.yml --branch=dev --limit=10 \
  --json conclusion,startedAt,updatedAt,databaseId,createdAt,status
gh run list --workflow=test.yml --branch=dev --limit=10 \
  --json conclusion,startedAt,updatedAt,databaseId,createdAt,status
# Compute: duration = updatedAt - startedAt for last 5 successful runs.
# Median + min + max.
```

**`build.yml` (Docker image build & push, last 5 successful runs on dev):**

| Run | Duration |
|---|---|
| `24868947697` | 265s (4.42m) |
| `24868262770` | 302s (5.03m) |
| `24868012960` | 338s (5.63m) |
| `24867894064` | 256s (4.27m) |
| `24867768421` | 241s (4.02m) |
| **Median** | **265s (4.42m)** |
| Min | 241s (4.02m) |
| Max | 338s (5.63m) |
| Mean | 280s (4.67m) |

**`test.yml` (backend pytest + frontend vitest + lint + typecheck + semgrep, last 5 successful runs on dev):**

| Run | Duration |
|---|---|
| `24868947693` | 203s (3.38m) |
| `24868262760` | 193s (3.22m) |
| `24868012972` | 172s (2.87m) |
| `24867894062` | 186s (3.10m) |
| `24867768415` | 185s (3.08m) |
| **Median** | **186s (3.10m)** |
| Min | 172s (2.87m) |
| Max | 203s (3.38m) |
| Mean | 188s (3.13m) |

**Combined PR feedback loop median (build.yml || test.yml run in parallel):** ~265s = ~4.4 minutes (gated by the slower build.yml).

**Regression thresholds (per-bump):**

| Signal | Threshold | Action |
|---|---|---|
| `build.yml` median | > +20% (i.e. > 318s ≈ 5.3m) | Investigate; large dep installs are typical culprits |
| `test.yml` median | > +30% (i.e. > 242s ≈ 4.0m) | Investigate |
| Either workflow median | > +50% | Block bump |
| Any single 6rrl5-child PR run hits > 10 min | Investigate before merge | |

**Caveat:** GitHub Actions runners are noisy. A single-bump observation needs ≥3 runs of the bumped PR's workflows to compare against this median, not 1.

---

## Per-bump regression check

Every 6rrl5 child PR (e.g. enhancedchannelmanager-6rrl5.1 fastapi 0.137+, enhancedchannelmanager-v28b8 TS 6.0, etc.) runs this procedure **before merging** and posts the table in the PR description.

### Procedure

1. Check out the PR branch (which has the bump applied).
2. Re-run all six metric captures **on the same worktree host** as this baseline (Ubuntu 24.04, 16 vCPU). Different hardware invalidates the comparison.
3. Use a fresh, unique image tag — never `ecm-ecm-1`, never `ecm-baseline:6rrl5` (that's the reference). Suggested: `ecm-bump-<bead-id>:test`.
4. For Metric 6 (CI pipelines), wait until the PR has produced at least 3 green runs of `build.yml` and `test.yml`. Take the median of those, not a single run.
5. Fill in the comparison table below in the PR body.
6. If any threshold is breached, the PR is blocked unless the PO signs off with documented justification (e.g., security-mandated bump that has no smaller version available).

### Comparison table template (copy into bump PR body)

```markdown
## Regression check vs docs/dep_upgrade_baseline.md

| Metric | Baseline | This bump | Δ | Threshold | Status |
|---|---|---|---|---|---|
| JS gzipped total | 528.3 KiB | <X> | <±%> | +10% | ✅ / ⚠️ / ❌ |
| CSS gzipped total | 74.2 KiB | <X> | <±%> | +10% | ✅ / ⚠️ / ❌ |
| Docker image size | 711 MB | <X> | <±%> | +10% | ✅ / ⚠️ / ❌ |
| Cold-start median (5 runs) | 5103 ms | <X> | <±%> | +20% | ✅ / ⚠️ / ❌ |
| Backend pytest runtime | 69.08s reported / 72.77s wall | <X> | <±%> | +30% | ✅ / ⚠️ / ❌ |
| Backend pytest count | 3035 | <X> | <±> | drop blocks | ✅ / ❌ |
| Frontend vitest wall-clock | 6.05s | <X> | <±%> | +30% | ✅ / ⚠️ / ❌ |
| Frontend vitest count | 1142 | <X> | <±> | drop blocks | ✅ / ❌ |
| `build.yml` median (3+ runs) | 265s | <X> | <±%> | +20% | ✅ / ⚠️ / ❌ |
| `test.yml` median (3+ runs) | 186s | <X> | <±%> | +30% | ✅ / ⚠️ / ❌ |
```

### When to update this baseline

This baseline is **frozen** until the 6rrl5 epic closes. After the epic closes (all six dep bumps merged to dev and a `main` cut), re-capture all six metrics in a follow-up bead and replace this document. The post-6rrl5 baseline becomes the reference for whatever epic comes next.

Do **not** update this document mid-epic. Its only job is to be the stable reference point against which every bump is measured.

---

## Capture command appendix (copy-paste reproducibility)

All commands assume `cwd = repo root`, fresh checkout of the captured SHA (`3d97433a`), and Docker / `uv` / Node 22 available on the host.

```bash
# Metric 1 — frontend bundle
cd frontend && npm ci && rm -rf dist && /usr/bin/time -v npm run build
du -sh dist dist/assets
find dist/assets -name '*.js'  -exec gzip -9 -c {} + | wc -c
find dist/assets -name '*.css' -exec gzip -9 -c {} + | wc -c

# Metric 2 — Docker image
docker build -f Dockerfile -t ecm-baseline:6rrl5 .
docker images ecm-baseline:6rrl5

# Metric 3 — cold-start probe (loop x5; see /tmp/cold_start_probe.sh in PR)
# Internally docker-runs ecm-baseline:6rrl5 on host port 6109 → container 6100,
# polls GET /api/health until 200, records ms, repeats 5x, reports min/max/median.

# Metric 4 — backend pytest
cd backend && uv venv .venv && uv pip install --python .venv/bin/python -r requirements.txt \
  && uv pip install --python .venv/bin/python pytest pytest-asyncio pytest-cov httpx
mkdir -p /tmp/ecm_baseline_test_config && rm -f /tmp/ecm-baseline-test.db
python3 -c "import sqlite3; sqlite3.connect('/tmp/ecm-baseline-test.db').close()"
CONFIG_DIR=/tmp/ecm_baseline_test_config RATE_LIMIT_ENABLED=0 \
  ECM_CI_DB_PATH=/tmp/ecm-baseline-test.db \
  /usr/bin/time -v .venv/bin/python -m pytest \
  --ignore=tests/e2e -m 'not slow' --tb=short --no-header -p no:warnings \
  --durations=10 --no-cov

# Metric 5 — frontend vitest
cd frontend && /usr/bin/time -v npm test

# Metric 6 — CI medians
gh run list --workflow=build.yml --branch=dev --limit=10 \
  --json conclusion,startedAt,updatedAt,databaseId,createdAt,status
gh run list --workflow=test.yml --branch=dev --limit=10 \
  --json conclusion,startedAt,updatedAt,databaseId,createdAt,status
# Compute duration = updatedAt - startedAt per run; median of last 5 successful.
```
