# Runbook: Dep-Bump Backend ASGI Regression (fastapi / starlette / uvicorn)

> Post-merge regression caused by PR1 of the v0.16.0 dep-bump epic — the backend
> ASGI triplet (`fastapi`, `starlette`, `uvicorn`) bumped together. This runbook
> assumes the bad image is already running as `ecm-ecm-1` and walks an operator
> — possibly at 3 AM, without a backend engineer on call — through detection,
> triage, rollback, and verification.

- **Severity**: P1 — backend request path is either broken or degraded for every user.
- **Owner**: SRE (runbook owner); Project Engineer (post-rollback root cause).
- **Last reviewed**: 2026-04-24
- **Related beads**: `enhancedchannelmanager-eqmop` (this runbook), `enhancedchannelmanager-6rrl5` (dep-bump epic), `enhancedchannelmanager-jpyz4` (PO decision: epic lands on `dev` before v0.16.0 cut), `enhancedchannelmanager-j9xrz` + `-6rrl5.1` + `-6rrl5.2` (the ASGI triplet PR1).
- **Related ADR**: [ADR-001 — Dependency Upgrade Validation Gate](../adr/ADR-001-dependency-upgrade-validation-gate.md).
- **Complementary runbook**: [Dep-Bump Fresh-Image Smoke Test](./dep-bump-smoke-test.md) — pre-merge gate; this runbook covers the post-merge case the pre-merge gate missed.

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
- In practice, the ADR-001 cadence policy is **"one major bump per PR"** but PR1 bundles three because fastapi's pins force the triplet. Plan to roll back the triplet together; use the isolation above only to name the root cause in the follow-up bead.

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

# Log-side confirmation (no Prometheus).
docker logs ecm-ecm-1 --since 2m 2>&1 | grep -c '"level":"ERROR"'
# Expected: low / zero. A non-trivial count after rollback means escalate.
```

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

- [ ] File a bead under `enhancedchannelmanager-6rrl5` for the root-cause fix. Label: `dep-bump`, `roadmap:v0.16.0`, `backend`. Reference the traceback evidence.
- [ ] **Pin `backend/requirements.txt` back to the previous-good versions** in a follow-up PR to `dev` so a fresh `docker build` on `dev` does not re-introduce the regression. The PR should explicitly pin `fastapi`, `starlette`, and `uvicorn` to the pre-triplet versions.
- [ ] Re-cut process: once the root cause is fixed, the dep-bump PR can be re-proposed. Follow ADR-001 — one-major-per-PR cadence, fresh-image smoke must be green ([dep-bump-smoke-test runbook](./dep-bump-smoke-test.md)), and the triplet stays bundled (fastapi's pin of starlette forces this).
- [ ] If `ecm_http_requests_total` 5xx did **not** trigger an alert before users reported the issue, file a bead on alerting: either no Prometheus scrape is wired (infrastructure gap) or the alert threshold missed it (tuning gap). Reference [SLO-3 HTTP Error Rate](../sre/slos.md#slo-3-http-error-rate).
- [ ] Schedule a blameless postmortem via the `/postmortem` skill. Per [SLO error-budget policy](../sre/slos.md#error-budget-policy), if the 30-day error budget burn is >10% this is mandatory.
- [ ] Update this runbook with anything that was ambiguous in the execution — especially any traceback-to-culprit mapping that was missing from the decision tree.

## References

- [ADR-001 — Dependency Upgrade Validation Gate](../adr/ADR-001-dependency-upgrade-validation-gate.md) — defines the pre-merge gate that was supposed to catch this; a successful runbook execution implies an ADR-001 gap worth capturing.
- [Dep-Bump Fresh-Image Smoke Test](./dep-bump-smoke-test.md) — pre-merge counterpart; run it on the re-cut PR before re-proposing.
- [SLOs](../sre/slos.md) — SLO-1 (readiness), SLO-2 (p95 latency), SLO-3 (5xx rate) are the SLIs this regression burns against.
- `backend/observability.py` — registered metric names (`ecm_http_requests_total`, `ecm_http_request_duration_seconds`, `ecm_health_ready_ok`).
- `backend/entrypoint.sh` — exact uvicorn invocation (`uvicorn main:app --host 0.0.0.0 --port ${ECM_PORT} --limit-concurrency ${ECM_LIMIT_CONCURRENCY} --timeout-keep-alive ${ECM_TIMEOUT_KEEP_ALIVE}`); one worker, no `--workers` flag.
- `backend/requirements.txt` — the pins rolled back by this runbook.
- `docker-compose.yml` — container config (`ecm-config` volume, port mapping).
- `.github/workflows/build.yml` — GHCR tag scheme (`dev`, `dev-<short-sha>`, semver tags on `main`).
- Bead `enhancedchannelmanager-jpyz4` — PO decision: the full dep-bump epic lands on `dev` before v0.16.0 is cut.
