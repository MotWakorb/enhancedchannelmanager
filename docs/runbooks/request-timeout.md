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
| `ECM_REQUEST_TIMEOUT_SECONDS` | `30` | Per-request budget. Requests exceeding this return 504 Gateway Timeout. Applies to `/api/*` except streaming/tasks/backup. |
| `ECM_LIMIT_CONCURRENCY` | `100` | Max simultaneous in-flight requests per uvicorn worker. When exceeded, uvicorn returns 503. |
| `ECM_TIMEOUT_KEEP_ALIVE` | `30` | Seconds to hold an idle keep-alive connection open. |
| `ECM_UVICORN_LOOP` | `asyncio` | Event loop implementation passed to uvicorn's `--loop` (whitelist: `auto`, `asyncio`, `uvloop`; anything else falls back to `asyncio` with a warning). Default is stdlib `asyncio` because uvloop 0.22.1 has open upstream issues — MagicStack/uvloop#645 (responses leaking to the wrong request under load) and #706 (segfault in FastAPI-in-container) — with no fixed release (bead wadu3). Set to `uvloop` to opt back in without a rebuild. |
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
  `/api/channel-pipeline/validate`.
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

### Symptom: `WARNING: Exceeded concurrency limit.` in the logs, only when running behind a reverse proxy

**Meaning**: a burst of requests exceeded `ECM_LIMIT_CONCURRENCY` (default
100) in a single instant. Uvicorn refuses everything past the limit with a
503 and logs one `Exceeded concurrency limit.` warning per refused request.
This is uvicorn's own log line, not something ECM formats — grep for it
literally.

```bash
docker logs ecm-ecm-1 2>&1 | grep "Exceeded concurrency limit"
```

If this shows up correlated with a UI action that failed, looked stuck, or
only partly applied, read the rest of this section before touching
`ECM_LIMIT_CONCURRENCY` — the fix is usually not the limit.

**Why this reproduces behind a proxy and may not reproduce in direct
testing.** It is not that the proxy or HTTP/2 is broken, and switching to
HTTP/2 is not a fix you're missing — it's the opposite. What changes is how
tightly the burst gets spread out before it reaches ECM:

- A browser talking **HTTP/1.1 directly to ECM** caps itself at roughly 6
  connections per origin. A burst of requests queues client-side and
  trickles in a few at a time, so it rarely reaches the concurrency limit
  even when the burst is large — the browser is doing throttling ECM never
  has to do.
- A reverse proxy speaking **HTTP/2** to the browser (nginx, Caddy, Traefik,
  or similar) multiplexes many logical requests over a single TCP
  connection, with no per-origin connection cap. The same burst arrives at
  ECM all at once, uncapped.

Running ECM behind an HTTP/2 proxy is a normal, supported deployment — the
point is only that it changes the concurrency profile ECM sees, so a
proxied deployment can hit `ECM_LIMIT_CONCURRENCY` at a burst size a direct
deployment would never reach, because nothing upstream is spreading the
burst out anymore.

This is exactly what happened in [GitHub #755](https://github.com/MotWakorb/enhancedchannelmanager/issues/755)
(bead `enhancedchannelmanager-hns2y`, fixed in build 0006 — see
`CHANGELOG.md`): copying a Channel Pipeline rule fanned out into one
`PUT /rules/{id}` per rule. On a reproduction instance driven through a
reverse proxy, that put 124 requests in flight in 28ms — 115 came back 503
with a matching `Exceeded concurrency limit` warning each. The identical
action driven directly over HTTP/1.1 succeeded end to end (all 123 requests
OK) — same code, same browser, same instance; only the connection
multiplexing differed. Raising the limit would only have moved the failure
to the next operator with a larger rule set, which is why the real fix
collapsed the fan-out to 1–2 bulk requests instead (see the CHANGELOG entry
for the fix).

Action, in order:
- **Check whether the burst is proportional to something in your data** (rule
  count, channel count, stream count). If a UI action's request count scales
  with your library size instead of staying constant, that is very likely a
  client issuing one request per item instead of using a bulk endpoint —
  file a bead with the endpoint pattern and the item count; it's an ECM bug
  to fix, not a limit to tune around.
- **If the burst is a genuine one-time spike** (a large manual import, a
  bulk operation with no bulk endpoint yet), raise `ECM_LIMIT_CONCURRENCY`
  (see Configuration above) to accommodate it.
- **Do not "fix" this by removing HTTP/2 or the proxy.** That only
  reintroduces the browser's incidental 6-connection throttle as a
  workaround; it does not address the request count, and a large enough
  burst still exceeds the limit even over HTTP/1.1.

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
