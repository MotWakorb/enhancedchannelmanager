# Runbook: Dep-Bump Backend ASGI Regression (fastapi / starlette / uvicorn)

> Post-merge regression caused by PR1 of the v0.16.0 dep-bump epic — the backend
> ASGI triplet (`fastapi`, `starlette`, `uvicorn`) bumped together. This runbook
> assumes the bad image is already running as `ecm-ecm-1` and walks an operator
> — possibly at 3 AM, without a backend engineer on call — through detection,
> triage, rollback, and verification.

- **Severity**: P1 — backend request path is either broken or degraded for every user.
- **Owner**: SRE (runbook owner); Project Engineer (post-rollback root cause).
- **Last reviewed**: 2026-04-24
- **Related beads**: `enhancedchannelmanager-eqmop` (original post-merge rollback runbook), `enhancedchannelmanager-d0pbr` (pre-merge dry-run gate for PR #170 — starlette 1.0 triplet), `enhancedchannelmanager-6rrl5` (dep-bump epic), `enhancedchannelmanager-jpyz4` (PO decision: epic lands on `dev` before v0.16.0 cut), `enhancedchannelmanager-j9xrz` + `-6rrl5.1` + `-6rrl5.2` (the ASGI triplet PR1 — PR #170).
- **Related ADR**: [ADR-001 — Dependency Upgrade Validation Gate](../adr/ADR-001-dependency-upgrade-validation-gate.md).
- **Complementary runbook**: [Dep-Bump Fresh-Image Smoke Test](./dep-bump-smoke-test.md) — generic pre-merge gate; the "Pre-merge dry-run gate" section below is the ASGI-triplet-specific companion that dry-runs the rollback *before* merge.

## Pre-merge dry-run gate (PR #170 and any future ASGI-triplet bump)

This section is the **merge prerequisite** for any PR that bumps
`starlette`, `fastapi`, or `uvicorn` in `backend/requirements.txt`. The
generic smoke test confirms a fresh image boots; this section proves the
rollback works *before* you need it. Do not merge the triplet PR until
every box below is checked.

The risk this section defends against is silent SLI degradation on a major
starlette bump (0.52 → 1.0 is the canonical case, bead `j9xrz`): the
container boots, serves 200s, the generic smoke goes green — and one of
the four `ecm_*` metric families stops emitting. SLOs burn undetected.
Container-up is not sufficient; the four families must be verified.

### 1. Capture the pre-merge `dev` HEAD SHA

```bash
# Run on the PO's / ops' local checkout of dev, BEFORE merging PR #170.
git fetch origin
PREV_DEV_SHA=$(git rev-parse origin/dev)
echo "Pre-merge dev HEAD: ${PREV_DEV_SHA}"
# Record this SHA in the PR comment — it is the rollback target if post-merge
# verification (below) fails.
```

If the GHCR `dev-${PREV_DEV_SHA:0:7}` image tag does not exist
(`docker manifest inspect ghcr.io/motwakorb/enhancedchannelmanager:dev-${PREV_DEV_SHA:0:7}`
returns `manifest unknown`), **hold the merge** — the rollback target is
not pinned in the registry, so Mode A rollback below is unavailable and
Mode B is a rebuild under pressure. Wait for the CI build of the
pre-merge `dev` tip to publish before merging.

### 2. Baseline: all four `ecm_*` families emit under synthetic traffic

Before merging, exercise the **pre-merge** image and capture the baseline
shape of `/metrics`. This is what "good" looks like — you compare against
it after merge.

```bash
# Point curl at the running ecm-ecm-1 (the pre-merge image).
# Drive synthetic traffic so counters/histograms have samples to emit.
for i in $(seq 1 50); do
  curl -sS -o /dev/null http://localhost:${ECM_PORT:-6100}/api/health
  curl -sS -o /dev/null http://localhost:${ECM_PORT:-6100}/api/health/ready
done

# All four ecm_* families must appear with non-zero samples.
curl -s http://localhost:${ECM_PORT:-6100}/metrics \
  | grep -E '^ecm_(http_requests_total|http_request_duration_seconds|health_ready_ok|health_ready_check_duration_seconds)' \
  | tee /tmp/ecm-metrics-baseline-${PREV_DEV_SHA:0:7}.txt

# Sanity: each family should have at least one sample line (HELP/TYPE lines
# don't count — the grep above filters them out).
awk '{print $1}' /tmp/ecm-metrics-baseline-${PREV_DEV_SHA:0:7}.txt \
  | sed -E 's/\{.*//' | sort -u
# Expected output — exactly these four roots (ignore _bucket/_count/_sum
# suffixes which widen the line count but collapse here):
#   ecm_health_ready_check_duration_seconds
#   ecm_health_ready_ok
#   ecm_http_request_duration_seconds
#   ecm_http_requests_total
```

If the baseline does not show all four families, **stop** — the pre-merge
image is already mis-instrumented and the comparison has no baseline. File
a bead against `observability` before merging anything.

### 3. Dry-run the rollback on the PR branch

Simulate the Mode B rollback path locally against the PR #170 branch so
you know `git revert` + rebuild + restart actually restores the baseline.

```bash
# Check out the PR branch and locally merge it into dev (no push).
git fetch origin pull/170/head:pr-170-asgi-triplet
git checkout -b pre-merge-dry-run-170 origin/dev
git merge --no-ff pr-170-asgi-triplet -m "DRY-RUN: merge PR #170"
MERGE_SHA=$(git rev-parse HEAD)

# Revert the merge (the rollback commit you'd push post-merge if it broke).
git revert -m 1 "${MERGE_SHA}" --no-edit
REVERT_SHA=$(git rev-parse HEAD)

# Build and run the reverted image in an isolated container (do NOT touch
# ecm-ecm-1). The smoke-test harness handles isolation for you:
./scripts/smoke_test_dev_container.sh

# After it passes, tear down the throwaway branches/worktree.
git checkout dev && git branch -D pre-merge-dry-run-170 pr-170-asgi-triplet
```

Dry-run passes if: the smoke-test script emits all 7 PASS lines, and the
reverted image's `/metrics` emits all four `ecm_*` families (rerun the
baseline curl from step 2 against the dry-run container's port).

### 4. Pin the previous image tag in compose / registry *before* merging

The Mode A rollback below assumes `ghcr.io/motwakorb/enhancedchannelmanager:dev-${PREV_SHA}`
resolves. Before merging PR #170:

```bash
# Confirm the tag exists on GHCR and record its digest for the PR comment.
docker manifest inspect ghcr.io/motwakorb/enhancedchannelmanager:dev-${PREV_DEV_SHA:0:7} \
  | jq -r '.manifests[0].digest // .config.digest' \
  | tee /tmp/ecm-prev-dev-digest.txt
```

If the operator uses a pinned compose override (`image: ghcr.io/…:dev-<sha>`
rather than `:dev`), update the override to the `PREV_DEV_SHA` tag and
verify `docker compose pull` succeeds — so the rollback is a one-command
`docker compose up -d --force-recreate ecm`, not a hand-rolled
`docker run`.

### 5. Pre-merge checklist

All boxes must be checked in the PR #170 thread before merge:

- [ ] `PREV_DEV_SHA` captured and posted as a PR comment.
- [ ] `ghcr.io/motwakorb/enhancedchannelmanager:dev-${PREV_DEV_SHA:0:7}` manifest resolves; digest posted.
- [ ] Pre-merge `/metrics` baseline captured; all four `ecm_*` families emit non-zero samples under synthetic traffic.
- [ ] Local dry-run (step 3) executed; `git revert` + rebuild + `./scripts/smoke_test_dev_container.sh` all green.
- [ ] Compose / registry override pinned to `PREV_DEV_SHA` so Mode A rollback is one command.
- [ ] Rollback triggers (below, § "Rollback triggers — numeric thresholds") acknowledged by the reviewing engineer.

Once all five are checked, merge is authorized. The post-merge verification
in the rest of this runbook (Diagnosis + Resolution) remains the live
contract — the pre-merge gate does not replace it.

## Alert / Trigger

Manual trigger or one of these symptoms appears after a merge that bumped any
of `fastapi`, `starlette`, or `uvicorn` in `backend/requirements.txt`:

- `ecm_health_ready_ok == 0` sustained >2 minutes (same alert as [Readiness Availability](./readiness_availability.md), but the correlation here is "the image that just deployed contains the ASGI triplet bump").
- `ecm_http_requests_total{status=~"5.."}` / `ecm_http_requests_total` > 1% for 5+ minutes shortly after the triplet merged.
- `ecm_http_request_duration_seconds` p95 > 500ms on `/api/*` routes for 10+ minutes.
- Container restart loop: `docker ps --filter name=ecm-ecm-1` shows repeated `Restarting` state.
- User report: "the app is totally down" or "every save fails" within an hour of a `dev`/`latest` image rollout.

> **No-Prometheus caveat.** ECM does not ship a Prometheus instance. The
> `ecm_*` metrics are only useful if the operator has already wired a scraper
> to `/metrics`. For everyone else, the "no-metrics detection" path below
> (container logs + `curl` on `/api/health`) is the actual detection surface.

### Rollback triggers — numeric thresholds

Alert on any of these post-deploy. Meeting any one is sufficient cause to
execute the Resolution section below; do not wait for a second signal.

| Trigger | Threshold | Window | Baseline source |
|-|-|-|-|
| `ecm_*` family stops emitting | Count of series in any of the four families (`ecm_http_requests_total`, `ecm_http_request_duration_seconds`, `ecm_health_ready_ok`, `ecm_health_ready_check_duration_seconds`) drops below the pre-merge baseline line-count | Any 5-minute window post-deploy | `/tmp/ecm-metrics-baseline-<PREV_SHA>.txt` from Pre-merge § 2 |
| p95 latency delta | > +10% vs pre-merge 5-minute baseline on `/api/*` | Sustained 10 min | Pre-merge `ecm_http_request_duration_seconds` p95, same synthetic traffic pattern |
| 5xx rate delta | > baseline + 0.1 percentage points | Sustained 5 min | Pre-merge `rate(ecm_http_requests_total{status=~"5.."}) / rate(ecm_http_requests_total)` |
| Container-baseline test count regresses | Failure count from beads `vjlzf` (9 known-red) + `pvq6s` (12 known-red, partial overlap) **grows** after the merge | Any post-merge full-suite run | Pre-merge known-red set — the red set should stay constant or shrink; growth = new regression introduced by the bump |
| Readiness flaps | `ecm_health_ready_ok` oscillates 0↔1 — *any* 0 sample on a supposedly-steady-state container | Any single sample | Pre-merge: always 1 |
| Container restart loop | `docker ps --filter name=ecm-ecm-1 --format '{{.Status}}'` shows `Restarting (N)` with N ≥ 1 | Any observation | Pre-merge: "Up X" with N=0 |

The first four are the SLI-level triggers (they tie directly to
[SLO-1 readiness](../sre/slos.md#slo-1-readiness-availability),
[SLO-2 p95 latency](../sre/slos.md#slo-2-http-latency), and
[SLO-3 5xx rate](../sre/slos.md#slo-3-http-error-rate)). The test-count
trigger is a leading indicator that surfaces *before* the SLI metrics
shift, because the test suite exercises contract edges that production
traffic may not hit immediately.

## Symptoms

What the responder sees (choose the column that matches the environment):

| Signal | With Prometheus scrape | Without Prometheus scrape |
|-|-|-|
| 5xx spike | `ecm_http_requests_total{status=~"5.."}` rate climbs | `docker logs ecm-ecm-1 --since 10m \| grep '"level":"ERROR"' \| wc -l` shows a sustained burst |
| Latency regression | `histogram_quantile(0.95, sum by (le) (rate(ecm_http_request_duration_seconds_bucket[5m])))` > 500 ms | `curl -w '%{time_total}\n' -o /dev/null -s http://localhost:${ECM_PORT:-6100}/api/health/ready` returns > 1 s |
| Readiness flaps | `ecm_health_ready_ok` oscillates 0↔1 | `curl -s http://localhost:${ECM_PORT:-6100}/api/health/ready \| jq .status` alternates `"ready"` / `"not_ready"` |
| Startup failure | gauge never reports `1` after deploy | `docker logs ecm-ecm-1 2>&1 \| head -100` shows a traceback before the uvicorn "Application startup complete" line |
| Worker restart loop | Container restart count rising in docker stats | `docker ps --filter name=ecm-ecm-1 --format '{{.Status}}'` shows `Restarting (N)` |

Characteristic log patterns (JSON log format — see `backend/observability.py`):

- `TypeError: …` in starlette middleware chain → starlette contract break.
- `DependencyInjectionError` / `ValueError` raised from `fastapi.dependencies.*` → fastapi DI contract break.
- `RuntimeError: Lifespan protocol…` → ASGI lifespan renegotiation between starlette + uvicorn.
- `asyncio.exceptions.CancelledError` during startup → uvicorn worker-boot regression.
- `ImportError: cannot import name '...' from 'starlette.*'` → removed/renamed starlette API referenced by `main.py` or a router.

## Diagnosis

Ordered. Stop at the first branch that matches — do **not** run rollback steps
before identifying which bump is at fault (rollback is the same regardless,
but the follow-up bead must name the culprit).

### 1. Confirm the suspect image is actually running

```bash
docker inspect ecm-ecm-1 --format '{{.Config.Image}} @ {{.Image}}'
docker exec ecm-ecm-1 env | grep -E 'ECM_VERSION|GIT_COMMIT|RELEASE_CHANNEL'
```

If the `GIT_COMMIT` / `ECM_VERSION` does **not** match the PR1 merge commit, this
runbook is the wrong runbook — fall back to the symptom-specific runbook
([HTTP Error Rate](./http_error_rate.md), [HTTP Latency](./http_latency.md),
[Readiness Availability](./readiness_availability.md)).

### 2. Confirm `/api/health` is reachable at all

```bash
# Liveness (cheap, no subsystem touch)
curl -fsS http://localhost:${ECM_PORT:-6100}/api/health || echo "DOWN"

# Readiness (exercises DB + Dispatcharr + ffprobe sub-checks)
curl -sS http://localhost:${ECM_PORT:-6100}/api/health/ready | jq .
```

- **Liveness returns `DOWN` or times out** → the app is not serving at all. Jump to step 4 (isolate which bump) using `docker logs` only, then go to Resolution.
- **Liveness 200, readiness 503** → app is up, a sub-check is failing. Read `checks.<name>.status` / `.detail` in the JSON; if the detail points at a Python traceback from a starlette/uvicorn frame, continue to step 3.
- **Both 200, but users still broken** → this is probably not an ASGI regression. Run [HTTP Error Rate](./http_error_rate.md) triage by path first.

### 3. Capture the traceback

```bash
# First 150 lines after the most recent container start. Tracebacks on import
# or lifespan errors surface there, before the JSON log stream settles.
docker logs ecm-ecm-1 2>&1 | grep -n "Traceback\|ERROR\|CRITICAL" | head -30
docker logs ecm-ecm-1 --tail 200 2>&1 > /tmp/ecm-asgi-regression.log
```

Keep `/tmp/ecm-asgi-regression.log` — it is the evidence for the follow-up
bead and the postmortem.

### 4. Isolate which bump is responsible (decision tree)

Use the traceback frames to route. The top frame whose module path begins with
one of these directories names the likely culprit:

| Top frame module | Likely culprit | Why |
|-|-|-|
| `starlette/...` (middleware, routing, responses, applications) | **starlette** | Contract change — ASGI callable signature, middleware stack order, or Response API. |
| `fastapi/...` (dependencies, routing, params, encoders) | **fastapi** | DI resolution, route decoration, or Pydantic-integration change. |
| `uvicorn/...` (protocols, lifespan, workers, server) | **uvicorn** | Lifespan protocol, worker boot sequence, or ASGI-3 strictness change. |
| `pydantic/...` but triggered from a fastapi frame | **fastapi** (via pydantic compat) | Fastapi pinned a pydantic range that the new release tightens. |
| `asgiref` / `h11` / `httptools` | **uvicorn** (transport layer) | Uvicorn's bundled protocol parser regressed. |
| ECM code (`main.py`, `routers/...`) raises because it uses a removed symbol | Whichever package owns the removed symbol | Read the `ImportError`/`AttributeError` name; search `backend/requirements.txt` for the package. |

Two other isolation signals:

- **Rollback candidate A** — revert only the `fastapi` line in `requirements.txt`, rebuild, and retest. If the error changes or clears, fastapi is involved.
- **Rollback candidate B** — revert only the `starlette` line. Same test. Note that fastapi pins a narrow starlette range, so reverting starlette without reverting fastapi may resolve to an incompatible pair — if pip fails to resolve, that confirms the triplet must be rolled back together.
- In practice, the ADR-001 PR-grouping rule is **"one major bump per PR"** but PR1 bundles three because fastapi's pins force the triplet. Plan to roll back the triplet together; use the isolation above only to name the root cause in the follow-up bead.

### 5. Regression modes specific to starlette 1.0 (and uvicorn 0.46)

The table in step 4 routes by *frame*; this section routes by *behavior*.
Starlette 1.0 is a major bump; these are the contract surfaces most likely
to shift silently — no traceback, just a changed response shape or a
swallowed exception. Run through the list when the symptoms are present
but step-4 tracebacks are absent.

| Regression mode | What breaks | Verification command / check |
|-|-|-|
| **`BaseHTTPMiddleware` exception propagation** — ECM uses `@app.middleware("http")` (the decorator form of `BaseHTTPMiddleware`). Starlette 4.x let unhandled exceptions bubble; 1.0 may swallow, re-raise wrapped, or route them through a different handler. | A downstream handler exception that used to surface as 500 with `ecm_http_requests_total{status="500"}` incremented now surfaces as 200 with a partial body, or vice-versa. 5xx SLI stops tracking real errors. | Run `pytest backend/tests/integration/test_exception_propagation.py -v` (or any test that asserts a deliberate 500 path). If the expected 500s now return 200 or 422, exception propagation has shifted. Cross-check `/metrics` after a known-bad request: `ecm_http_requests_total{status="500"}` must increment by 1. |
| **Middleware ordering** — `backend/main.py` stacks five `@app.middleware("http")` decorators (outermost → innermost as FastAPI executes them): `security_headers` → `observability` (trace-id + Prometheus) → `request_timeout` → `auth` → `request_timing`. Starlette 1.0 is known to rework the `Middleware` stack internals; order-sensitive behavior can regress silently. | Trace-id contextvar unset when metrics middleware runs (observability can't label samples); auth middleware runs *before* request_timeout so a 504 path bypasses the auth context — wrong in both directions. | After a synthetic request: (1) every log line for the request carries the same `trace_id` field, (2) the `X-Request-ID` response header echoes the inbound value when provided, (3) a request to a slow route exceeds `ECM_REQUEST_TIMEOUT_SECONDS` and returns 504 (not a 502 or a hung connection). The order-preserving integration test is `backend/tests/integration/test_event_loop_responsiveness.py::TestRequestTimeoutMiddleware` — note this test is in the `vjlzf` known-red set in the container baseline; a **net** count shift is the signal, not any single pass/fail. |
| **ASGI scope mutation** — `backend/main.py:223` and `backend/observability.py` read `request.scope["route"].path` to label metrics by route pattern (bounds cardinality). Starlette 1.0 may change when `scope["route"]` is populated, populate it with a different object shape, or move it to `scope["endpoint"]`. | Metrics labels collapse to raw paths (cardinality bomb — `/api/channels/1`, `/api/channels/2`, …) or blank out (one `{path=""}` series instead of many patterns). | After synthetic traffic mix that hits parameterized routes: `curl -s /metrics \| grep 'ecm_http_requests_total{' \| awk -F'path="' '{print $2}' \| awk -F'"' '{print $1}' \| sort -u`. Expected: route *patterns* (e.g. `/api/channels/{channel_id}`), not concrete IDs. If you see `/api/channels/1`, `/api/channels/2`, … the scope contract has regressed. |
| **`request.state` access paths** — `app.state.limiter` (slowapi), any `request.state.<x>` set by a middleware and read by a handler. Starlette 1.0 has tightened `State` access semantics in prior minors; the 1.0 cut is where residual AttributeError divergences land. | Rate-limit middleware stops applying (every request gets full budget) or raises AttributeError on a previously-set attr. | `curl` the same endpoint at a rate known to trigger slowapi's 429 (usually >10 req/s for the default limiter). Pre-merge: 429 after threshold. Post-merge: all 200s → limiter contract broke. |
| **uvicorn 0.46 event-loop / cancellation** — `backend/main.py:361 request_timeout_middleware` wraps `call_next` in `asyncio.wait_for(..., timeout=ECM_REQUEST_TIMEOUT_SECONDS)` and catches `asyncio.TimeoutError` to return 504. uvicorn 0.46 changes cancellation propagation at the transport layer; if `wait_for`'s cancellation does not reach the inner task the same way, the 504 path either doesn't fire (request hangs) or fires twice (ASGI protocol error). | Slow endpoints hang past the configured timeout instead of returning 504; or the connection resets mid-response with a `RuntimeError: Response content longer than Content-Length` in logs. | `curl -w '%{http_code} %{time_total}\n' -o /dev/null -s -m 60 http://localhost:${ECM_PORT:-6100}/api/_slow_test_endpoint` (if available) — the code must be 504 and `time_total` within 1.5× of `ECM_REQUEST_TIMEOUT_SECONDS`. If it returns 200 after a long wait, or the curl times out at -m 60 with no response, cancellation regressed. |
| **Lifespan protocol renegotiation** — starlette 1.0 may reject the `@app.on_event("startup")` / `@app.on_event("shutdown")` decorators (deprecated since 0.28) in favor of the `lifespan=` context manager. ECM still uses `@app.on_event` per the PR #170 audit. | Startup never completes (uvicorn logs "Waiting for application startup" but never "Application startup complete"); or shutdown hangs and SIGKILL is needed. | `docker logs ecm-ecm-1 2>&1 \| grep -E "Application (startup \|shutdown) complete"` — both must appear within 30s of boot/stop. If `startup complete` is missing, lifespan regressed. Migrate to `lifespan=` in a follow-up bead if starlette 1.0 has *hard*-removed `@app.on_event`. |
| **`StreamingResponse` contract** — `backend/routers/auto_creation.py:20` imports `StreamingResponse` from `starlette.responses`. Starlette 1.0 may tighten what an async generator body can yield (bytes-only, no strings). | Streaming endpoints (DBAS import streaming, any SSE) break with `TypeError: expected bytes, got str`. | Exercise the DBAS import streaming endpoint; verify the response streams and completes. If `docker logs` shows a `TypeError` in a StreamingResponse frame, the contract regressed. |

If any row above confirms a regression, treat it the same as a
step-4 decision-tree hit — route to the Resolution section and roll the
triplet back. Record which row matched in the follow-up bead so the
re-cut PR can add a targeted regression test.

### Escalate instead of continuing if

- Multiple containers or hosts are affected and the rollback target is unclear.
- The GHCR previous-release image cannot be pulled (network, credentials, GHCR outage).
- Rolling back requires destructive git operations on `main` — this runbook only covers rolling back the `dev` branch + `ecm-ecm-1` container. A tagged-release rollback is [v0.16.0 Hard Rollback](./v0.16.0-rollback.md)'s scope.
- The failure is not present in the ASGI layer (tracebacks originate entirely from ECM code without any starlette/fastapi/uvicorn frames in the stack) — that is a different regression class; page the Project Engineer.

## Resolution

**Mitigate first, root-cause after.** Get the previous image running, then
file a bead for the follow-up investigation.

The rollback has two modes depending on where the bad image is:

- **Mode A — bad image is the `dev` tag on GHCR** (most common after a `dev` merge). Pull the previous `dev-<sha>` image and restart. No git work required to stop the bleeding.
- **Mode B — bad image was built locally and loaded into `ecm-ecm-1`**. Rebuild from the pre-triplet commit. This is the path when an operator was testing the triplet locally.

### Mode A — GHCR rollback to the previous `dev-<sha>` image

1. **Identify the previous-good `dev-<sha>` tag.**

   ```bash
   # Find the SHA of the commit immediately before the triplet merge.
   git log --oneline --merges origin/dev -n 20
   # Locate the PR1 merge commit (subject line mentions fastapi/starlette/uvicorn).
   # Capture the SHA of the commit BEFORE it.
   PREV_SHA=<first 7 chars of pre-triplet commit>
   ```

   The GHCR tag convention (per `.github/workflows/build.yml`) is
   `dev-<short-sha>` for branch builds. Confirm the tag exists:

   ```bash
   docker manifest inspect ghcr.io/motwakorb/enhancedchannelmanager:dev-${PREV_SHA}
   # Expected: valid manifest JSON. "manifest unknown" means the image was
   # never pushed for that SHA — step back one more commit and retry.
   ```

2. **Pull the previous-good image.**

   ```bash
   docker pull ghcr.io/motwakorb/enhancedchannelmanager:dev-${PREV_SHA}
   ```

   If the pull fails with `unauthorized`, the image is private for this fork
   and you need a GHCR read token — escalate; do not improvise credentials.

3. **Swap the running container.**

   ```bash
   # Snapshot current container config for restore-on-failure.
   docker inspect ecm-ecm-1 > /tmp/ecm-ecm-1-inspect.json

   # Capture recent logs before destroying the container.
   docker logs ecm-ecm-1 --tail 500 2>&1 > /tmp/ecm-asgi-regression.log

   # Stop + remove; the ecm-config named volume is preserved (per docker-compose.yml).
   docker stop ecm-ecm-1
   docker rm ecm-ecm-1

   # Re-run on the previous image, same port + volume bindings as docker-compose.yml.
   docker run -d \
     --name ecm-ecm-1 \
     -p ${ECM_PORT:-6100}:${ECM_PORT:-6100} \
     -p ${ECM_HTTPS_PORT:-6143}:${ECM_HTTPS_PORT:-6143} \
     -v ecm-config:/config \
     --add-host=host.docker.internal:host-gateway \
     ghcr.io/motwakorb/enhancedchannelmanager:dev-${PREV_SHA}
   ```

   Operators using `docker compose up -d` should instead edit their compose
   override to pin `image: ghcr.io/motwakorb/enhancedchannelmanager:dev-${PREV_SHA}`
   and run `docker compose up -d --force-recreate ecm` — same result, without
   hand-rolling the `docker run`.

4. **Verify the rollback took (see "Verify" below). If verification fails, stop and escalate — do not start a second rollback attempt on top of the first.**

### Mode B — Rebuild from the pre-triplet commit

Only use this path when Mode A is unavailable (no GHCR access, local test
build, air-gapped deploy).

1. **Check out the pre-triplet commit.**

   ```bash
   git fetch origin
   git checkout ${PREV_SHA}
   # Confirm requirements.txt pins are the previous-good set.
   grep -E '^(fastapi|starlette|uvicorn)==' backend/requirements.txt
   ```

2. **Rebuild the image.**

   ```bash
   docker build -t ecm-rollback:${PREV_SHA} \
     --build-arg GIT_COMMIT=${PREV_SHA} \
     .
   ```

   Multi-arch rebuild is not required for a local-host rollback — the local
   host's architecture matches what ARM64/AMD64 CI would produce.

3. **Swap the container** (same commands as Mode A step 3, substituting
   `ecm-rollback:${PREV_SHA}` for the GHCR tag).

4. **Verify.**

### Verify (both modes)

All of the following must pass before declaring the rollback complete:

```bash
# Container is up, not restarting.
docker ps --filter name=ecm-ecm-1 --format 'table {{.Names}}\t{{.Status}}'
# Expected: "Up X seconds/minutes" — NOT "Restarting".

# Running image matches the rollback target.
docker inspect ecm-ecm-1 --format '{{.Config.Image}}'
# Expected: contains the PREV_SHA you rolled back to.

# ECM_VERSION env matches.
docker exec ecm-ecm-1 env | grep -E 'ECM_VERSION|GIT_COMMIT'
# Expected: the pre-triplet version/commit.

# Liveness.
curl -fsS http://localhost:${ECM_PORT:-6100}/api/health
# Expected: 200 with JSON {"status":"healthy",...}.

# Readiness.
curl -sS http://localhost:${ECM_PORT:-6100}/api/health/ready | jq '.status'
# Expected: "ready". Any sub-check reporting "fail" means the rollback
# did not resolve the failure mode — go to Escalation.

# 5xx rate (if a Prometheus scrape is wired).
#   sum(rate(ecm_http_requests_total{status=~"5.."}[5m]))
#   / sum(rate(ecm_http_requests_total[5m]))
# Expected: drops toward 0 over the next 5 minutes. If it doesn't, rollback
# didn't fix the root cause — escalate.

# All four ecm_* families emit non-zero samples — the "silent SLI degradation"
# check. Container-up is NOT sufficient.
for i in $(seq 1 20); do
  curl -sS -o /dev/null http://localhost:${ECM_PORT:-6100}/api/health
  curl -sS -o /dev/null http://localhost:${ECM_PORT:-6100}/api/health/ready
done
curl -s http://localhost:${ECM_PORT:-6100}/metrics \
  | grep -E '^ecm_(http_requests_total|http_request_duration_seconds|health_ready_ok|health_ready_check_duration_seconds)' \
  | awk '{print $1}' | sed -E 's/\{.*//' | sort -u
# Expected: exactly four roots (ecm_health_ready_check_duration_seconds,
# ecm_health_ready_ok, ecm_http_request_duration_seconds, ecm_http_requests_total).
# If any are missing, rollback is incomplete — escalate.

# Compare to the pre-merge baseline captured in Pre-merge § 2. The line counts
# per family should match (within sample-noise); missing lines = missing series.
diff <(sort /tmp/ecm-metrics-baseline-${PREV_DEV_SHA:0:7}.txt | awk '{print $1}' | sed -E 's/\{.*//' | sort -u) \
     <(curl -s http://localhost:${ECM_PORT:-6100}/metrics \
         | grep -E '^ecm_(http_requests_total|http_request_duration_seconds|health_ready_ok|health_ready_check_duration_seconds)' \
         | awk '{print $1}' | sed -E 's/\{.*//' | sort -u)
# Expected: no diff output.

# Log-side confirmation (no Prometheus).
docker logs ecm-ecm-1 --since 2m 2>&1 | grep -c '"level":"ERROR"'
# Expected: low / zero. A non-trivial count after rollback means escalate.
```

### Notify

Once verification passes, notify the release-notes Discord channel per
[`docs/discord_release_notes.md`](../discord_release_notes.md): post the
rollback commit/tag, the SLI symptom that triggered it, and a pointer to
this runbook. Users hit by the bad bundle need to know when the rollback
took so they can retry without reporting duplicate incidents.

## Escalation

Stop and page the Project Engineer if:

- Mode A rollback completes but readiness still returns 503 — the rollback target is also bad or the failure is not ASGI-layer.
- Mode B rebuild fails (docker build error) — this may be an environmental issue that the runbook cannot fix.
- Multiple rollback attempts leave the container in an ambiguous state (running an image you cannot identify, volume state possibly corrupted). Do **not** start a third attempt; capture the inspect/logs and escalate.
- The regression surfaces on `main` (tagged release) — the scope is bigger; use [v0.16.0 Hard Rollback](./v0.16.0-rollback.md) with PO authorization.

Provide to the engineer: the incident start time, the suspected culprit from
the Diagnosis decision tree, the `/tmp/ecm-asgi-regression.log` snapshot, and
the rollback target SHA you used.

## Post-incident

- [ ] Open a **P1 bead** documenting the break with enough evidence to un-red before re-attempt: the Diagnosis decision-tree row that matched, the starlette-1.0 regression-mode row (if any), the `/tmp/ecm-asgi-regression.log` snapshot, the pre- and post-merge `/metrics` diffs, and the rollback target SHA. File it under `enhancedchannelmanager-6rrl5` (dep-bump epic). Labels: `dep-bump`, `roadmap:v0.16.0`, `backend`, `sre`.
- [ ] **Pin `backend/requirements.txt` back to the previous-good versions** in a follow-up PR to `dev` so a fresh `docker build` on `dev` does not re-introduce the regression. The PR should explicitly pin `fastapi`, `starlette`, and `uvicorn` to the pre-triplet versions.
- [ ] Re-cut process: once the root cause is fixed, the dep-bump PR can be re-proposed. Follow ADR-001 — one-major-per-PR grouping rule, fresh-image smoke must be green ([dep-bump-smoke-test runbook](./dep-bump-smoke-test.md)), and the triplet stays bundled (fastapi's pin of starlette forces this).
- [ ] If `ecm_http_requests_total` 5xx did **not** trigger an alert before users reported the issue, file a bead on alerting: either no Prometheus scrape is wired (infrastructure gap) or the alert threshold missed it (tuning gap). Reference [SLO-3 HTTP Error Rate](../sre/slos.md#slo-3-http-error-rate).
- [ ] Schedule a blameless postmortem via the `/postmortem` skill. Per [SLO error-budget policy](../sre/slos.md#error-budget-policy), if the 30-day error budget burn is >10% this is mandatory.
- [ ] Update this runbook with anything that was ambiguous in the execution — especially any traceback-to-culprit mapping that was missing from the decision tree.

## References

- [ADR-001 — Dependency Upgrade Validation Gate](../adr/ADR-001-dependency-upgrade-validation-gate.md) — defines the pre-merge gate that was supposed to catch this; a successful runbook execution implies an ADR-001 gap worth capturing.
- [Dep-Bump Fresh-Image Smoke Test](./dep-bump-smoke-test.md) — pre-merge counterpart; run it on the re-cut PR before re-proposing.
- [SLOs](../sre/slos.md) — SLO-1 (readiness), SLO-2 (p95 latency), SLO-3 (5xx rate) are the SLIs this regression burns against.
- `backend/observability.py` — registered metric names: `ecm_http_requests_total`, `ecm_http_request_duration_seconds`, `ecm_health_ready_ok`, `ecm_health_ready_check_duration_seconds` (all four families gated by the Pre-merge § 2 baseline and the Verify block).
- `backend/main.py` — the five-middleware stack (`security_headers` → `observability` → `request_timeout` → `auth` → `request_timing`) verified by the starlette-1.0 regression-mode table.
- [`docs/discord_release_notes.md`](../discord_release_notes.md) — notification convention used by the post-rollback Notify step.
- Beads `enhancedchannelmanager-vjlzf` (9 known-red container-baseline tests) + `enhancedchannelmanager-pvq6s` (12 known-red, partial overlap with vjlzf) — the stable red set used by the "test count regresses" rollback trigger.
- `backend/entrypoint.sh` — exact uvicorn invocation (`uvicorn main:app --host 0.0.0.0 --port ${ECM_PORT} --limit-concurrency ${ECM_LIMIT_CONCURRENCY} --timeout-keep-alive ${ECM_TIMEOUT_KEEP_ALIVE}`); one worker, no `--workers` flag.
- `backend/requirements.txt` — the pins rolled back by this runbook.
- `docker-compose.yml` — container config (`ecm-config` volume, port mapping).
- `.github/workflows/build.yml` — GHCR tag scheme (`dev`, `dev-<short-sha>`, semver tags on `main`).
- Bead `enhancedchannelmanager-jpyz4` — PO decision: the full dep-bump epic lands on `dev` before v0.16.0 is cut.
