# Runbook: Request Timeouts, Concurrency Limits, and CPU-Bound Offload

Owner: SRE. Source: bd-w3z4h (under epic bd-eio04).

## What this runbook covers

ECM enforces three layers of defense against runaway or slow requests:

1. **Per-request timeout middleware** — returns HTTP 504 after N seconds.
2. **Uvicorn concurrency limit** — caps in-flight requests per worker.
3. **CPU-bound thread-pool offload** — keeps the event loop responsive while
   sync CPU-heavy work (regex, XML generation, template rendering) runs.

This runbook explains the knobs, the failure modes you might see, and the
recovery procedure.

## Configuration

All values are environment variables, overridable at container runtime.

| Variable | Default | Meaning |
|-|-|-|
| `ECM_REQUEST_TIMEOUT_SECONDS` | `30` | Per-request budget. Requests exceeding this return 504 Gateway Timeout. Applies to `/api/*` except streaming/ffmpeg/tasks/backup. |
| `ECM_LIMIT_CONCURRENCY` | `100` | Max simultaneous in-flight requests per uvicorn worker. When exceeded, uvicorn returns 503. |
| `ECM_TIMEOUT_KEEP_ALIVE` | `30` | Seconds to hold an idle keep-alive connection open. |
| `ECM_CPU_POOL_WORKERS` | `min(32, 2 * cpu_count)` | Size of the thread pool used by `run_cpu_bound`. |

Change values by setting env vars in `docker-compose.yml` or your container
runtime. No image rebuild required.

## Architecture (why these exist)

- ECM runs **one uvicorn worker** (single process). Everything shares one
  event loop. A sync CPU-heavy function called directly inside an async
  handler blocks every concurrent request — including `/api/health`.
- Several user-reachable endpoints call sync CPU code: `/api/normalization/*`,
  `/api/channels` (with `normalize=true`), `/api/dummy-epg/preview*`,
  `/api/dummy-epg/xmltv*`, `/api/dummy-epg/generate`, and
  `/api/auto-creation/validate`.
- These endpoints are now wrapped in `backend/concurrency.py::run_cpu_bound`,
  which dispatches the sync call to a bounded thread-pool executor so the
  loop stays free.
- The timeout middleware is a secondary defense: if something does slip
  through (future code change, recursive regex), a 30s cap prevents one
  request from holding a worker slot indefinitely.
- The uvicorn concurrency limit is a tertiary defense: under load, it caps
  memory growth and forces the surplus traffic to retry instead of queueing
  indefinitely.

## Symptoms → Diagnosis → Action

### Symptom: users see HTTP 504 "Gateway Timeout"

**Meaning**: a request exceeded `ECM_REQUEST_TIMEOUT_SECONDS`.

Check:
```bash
docker logs ecm-ecm-1 2>&1 | grep "\[TIMEOUT\]"
```

You'll see lines like:
```
[TIMEOUT] POST /api/normalization/test-batch exceeded 30.0s budget — returning 504
```

Action:
- If the endpoint is expected to be slow (XMLTV generation for 500+
  channels), add its prefix to `_TIMEOUT_EXEMPT_PREFIXES` in `main.py`.
- If the endpoint was fast and is now slow, check for a pathological regex
  or a degraded Dispatcharr backend. Grep logs for `[SLOW-REQUEST]`.
- If many endpoints are timing out simultaneously, the event loop is
  likely blocked — see the next section.

### Symptom: users see HTTP 503, or /api/health is slow to respond

**Meaning**: either uvicorn concurrency is exhausted, or the event loop is
blocked by sync CPU work.

Check:
```bash
# 1. Request rate across all endpoints (built-in diagnostic)
curl -s http://localhost:6100/api/debug/request-rates | jq

# 2. Current healthcheck latency
time curl -s http://localhost:6100/api/health > /dev/null

# 3. Python thread state (what's the loop/threads doing?)
docker exec ecm-ecm-1 sh -c 'pid=$(pgrep -f "uvicorn main:app"); cat /proc/$pid/status | grep -E "State|Threads"'
```

Interpretation:
- `request-rates` shows a single endpoint hammering the server → a client is
  polling. Check the rate-limiter (slowapi is applied to `/test-batch`).
- `/api/health` takes > 500ms consistently → event loop is blocked. Check for
  a new call site that calls a sync CPU function without `run_cpu_bound`.
- Threads count has climbed to `ECM_CPU_POOL_WORKERS + N` and isn't
  dropping → CPU pool is saturated; the sync work is genuinely slow and
  backed up.

Action:
- Under acute load: restart the container (`docker restart ecm-ecm-1`).
  Drops in-flight work, clears the pool, and reloads settings.
- For a sustained issue, increase `ECM_LIMIT_CONCURRENCY` and
  `ECM_CPU_POOL_WORKERS` together. Doubling both is a safe starting point;
  monitor memory afterwards.
- File a bead if a new endpoint is blocking the loop (should use
  `run_cpu_bound`).

### Symptom: `/api/dummy-epg/xmltv` returns 504 every time

**Meaning**: XMLTV generation for a large catalog legitimately exceeds 30s.

Action: the short-term fix is to exempt the prefix in `main.py`:
```python
_TIMEOUT_EXEMPT_PREFIXES = (..., "/api/dummy-epg/xmltv")
```

The correct long-term fix is to move XMLTV generation into a background
task that writes to the cache — the HTTP endpoint returns the cached blob
and never computes inline. File a bead.

### Symptom: regressions after bd-w3z4h deploy

**Meaning**: a handler's `await run_cpu_bound(...)` didn't get wrapped right,
or a mock-based test broke because patch targets shifted.

Action:
- Tests: module-level imports at the router means patching
  `"normalization_engine.get_normalization_engine"` still works; functions
  imported inside the handler body (e.g. `from dummy_epg_engine import
  generate_xmltv`) must be patched at `dummy_epg_engine.generate_xmltv`.
- `run_cpu_bound` is the canonical import — `from concurrency import
  run_cpu_bound`.

## Verifying the fix is live

```bash
# 1. /api/health stays fast during a slow call
# Fire a slow rule-stats computation in the background, then curl health.
curl -s -X POST http://localhost:6100/api/normalization/test-batch \
  -H 'Content-Type: application/json' \
  -d '{"texts":["<1000 pathological strings>"]}' &
# While that's running (watch progress in another shell):
time curl -s http://localhost:6100/api/health
# Expected: ~10ms, not 10s.

# 2. Concurrency limit shape
docker exec ecm-ecm-1 sh -c 'ps -o pid,args -C uvicorn' | grep limit-concurrency
# Expected: --limit-concurrency 100 --timeout-keep-alive 30

# 3. Request-timeout middleware active
# Hit an intentionally slow debug endpoint (none exists in prod); or inspect
# logs after any real slow request:
docker logs ecm-ecm-1 2>&1 | grep "\[TIMEOUT\]"
```

## Related beads

- **bd-w3z4h** — this work (audit + thread pool + timeout + uvicorn limits).
- **bd-eio04.5** — `safe_regex` utility with 100ms ReDoS timeout.
- **bd-eio04.14–.17** — migrating regex call sites off `re` onto
  `safe_regex`. With bd-w3z4h in place, their 100ms timeout actually
  protects the event loop (without it, a 100ms block is still a 100ms
  freeze for every concurrent request).
