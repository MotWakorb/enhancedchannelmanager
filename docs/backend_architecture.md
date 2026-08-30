# Backend Architecture Patterns

**Modular routers**: `backend/routers/` has 20+ domain-focused modules (channels, m3u, epg, settings, etc.)

**Router registry**: `routers/__init__.py` has `all_routers` list; `main.py` includes them via `app.include_router()`

**main.py** retains: app lifecycle, middleware, auth, startup/shutdown

**Mock patches**: When testing router endpoints, patch `routers.<module>.X` not `main.X`

**Why:** The v0.13.0 refactor moved endpoints from main.py into routers/. Mock targets must match where the name is looked up at runtime.

**How to apply:** Always check which module owns the endpoint before writing test patches.

## Observability

The observability substrate lives in `backend/observability.py`. It is wired into the app by a single call from `main.py` (`install_observability()`), which installs both the JSON log formatter and the Prometheus metric registry. Every request flowing through FastAPI picks up a correlation id and contributes counter/histogram samples; the readiness probe publishes a gauge plus per-check histograms. The design philosophy is *substrate, not platform*: this code only produces signal. Dashboards, SLOs, alertmanager, and Prometheus/Grafana deployment are separate concerns.

### Structured logging

Every log line is one JSON document with the fields `ts`, `level`, `logger`, `msg`, `trace_id`. It is emitted to stdout and to the process-safe rotating set at `CONFIG_DIR/logs/ecm.log`. Before any bytes reach that set, the handler applies the shared credential-pattern scrubber and an additive process-lifetime registry of settings, provider, and session credential values, including raw, percent-encoded, and serialized forms. Retired values stay registered until restart. A record larger than one generation is replaced by valid NDJSON containing its post-scrub byte count and SHA-256 digest, so every retained generation remains within its configured bound.

The file handler uses a dedicated `flock` file because the HTTP and HTTPS processes share the set. It holds directory, lock, and active-file descriptors; validates regular-file, hardlink, and inode identity before writes; and refuses symlinked directories or non-regular files. A scrub-version marker separates safe files from logs created before pre-persistence redaction existed. Startup discards an unmarked regular-file epoch before writing, while snapshot collection refuses an unknown or unsafe epoch. The default policy is a 10 MiB active file plus four backups; changes to `backend_log_file_max_bytes` or `backend_log_file_backup_count` are applied on process restart. Storage or identity failure disables only persistent logging, leaving stdout and the in-memory ring available.

Debug bundles read fixed-size file descriptors from oldest to newest. Descriptor capture is serialized with rotation, but bulk reads happen after the lock is released and outside the event loop. Bundle ingestion retains at most the newest 50 MiB even when the configured on-disk set is larger. `manifest.json` records the applied rotation policy, source files, timestamps, byte/line counts, parse failures, and whether bytes were omitted or rotation had actually discarded an older file. The ring is used when persistence is unavailable and is included separately when the persistent snapshot is degraded. Bundle jobs are bound to the initiating human-admin user id, retain completed artifacts in private temporary files, and expire on an independent timer.

The correlation middleware reads `X-Request-ID` from the inbound headers (truncated to 128 chars as a defensive cap) or generates a UUIDv4, stores it in a `contextvars.ContextVar`, and echoes it back as the `X-Request-ID` response header. Any log line emitted during the request carries that id automatically. No threading of arguments is required.

**Call style (unchanged):**
```python
import logging
logger = logging.getLogger(__name__)

logger.info("[CHANNELS] Created channel id=%s name=%s", channel_id, name)
logger.warning("[CHANNELS] Failed to update: %s", e)
```
Prefix format stays `[UPPERCASE-MODULE]`; lazy `%s` formatting stays mandatory (prevents log-injection CWE-117 bypass and keeps DEBUG-level interpolation cheap).

**Adding structured context to a long-running task:**
```python
from observability import bind_context, reset_context

token = bind_context(restore_id=restore.id, phase="validate")
try:
    run_validation()
    logger.info("[RESTORE] Validation complete")   # ← gets restore_id + phase
finally:
    reset_context(token)
```
The key/values attach to every log record emitted inside the `with`/`try` block. Keys that collide with built-in `LogRecord` attributes (`message`, `asctime`, etc.) are silently skipped. Namespace your fields (`restore_id`, `sync_id`, `probe_stream_id`).

**Per-request access log:** one `ecm.access` INFO line fires after every request with `method`, `path`, `status`, `duration_ms`. Grep on `"logger":"ecm.access"` to audit traffic; filter on `trace_id` to trace a single request across modules.

### Metrics

`/metrics` serves Prometheus text exposition. It is unauthenticated by design. Prometheus scrapers have no session context, and the endpoint exposes no user data. The deployment assumption is that the network surface (LAN, reverse proxy, tailnet) is trusted. If that assumption stops holding, the follow-up is an IP allowlist at the proxy (simplest, no code change) or a bearer-token scrape credential validated in the handler. Both are future beads.

**Minimum metric set (shipped in this substrate):**

| Metric | Type | Labels | Purpose |
|-|-|-|-|
| `ecm_http_requests_total` | Counter | method, path, status | RED rate + errors |
| `ecm_http_request_duration_seconds` | Histogram | method, path | RED duration |
| `ecm_health_ready_ok` | Gauge | None | 1/0 readiness verdict |
| `ecm_health_ready_check_duration_seconds` | Histogram | check | Per sub-check latency |

**Metric naming convention:** `ecm_<subsystem>_<name>_<unit>`: snake_case, unit suffix (`_seconds`, `_bytes`, `_total` for counters). The `ecm_` namespace is non-negotiable; it keeps ECM's series distinct when scraped into a shared Prometheus instance.

**Cardinality rules (enforced at metric-emit time, reviewed in PRs):**
- Labels must have bounded cardinality. HTTP methods, status codes, route patterns, and a fixed vocabulary of check names are fine. User ids, channel ids, stream ids, UUIDs, raw URLs, and email addresses are not. They belong in traces or logs, never in metric labels.
- For HTTP metrics we label by the matched FastAPI route pattern (`request.scope["route"].path`, e.g. `/api/channels/{channel_id}`), never the raw URL. The middleware does this automatically; custom middleware must follow suit.
- Every new histogram gets buckets tuned to the signal: sub-millisecond floors for in-process calls, multi-second ceilings for network work. Default buckets rarely fit.

**Adding a new metric:**
```python
from prometheus_client import Counter
from observability import REGISTRY

# Register against the shared ECM registry so it shows up on /metrics.
restore_failures_total = Counter(
    "ecm_restore_failures_total",
    "Restore attempts that failed, labeled by failure category.",
    ["category"],  # bounded vocabulary: {"schema", "validation", "apply"}
    registry=REGISTRY,
)
```
Put the registration near the subsystem that owns the metric (e.g. `restore.py`, not `observability.py`) so code review can evaluate the label set alongside the code that emits samples.

**Writing a test for a new metric:** reset the registry in a fixture (`observability.reset_for_tests(); observability.install_metrics()`), exercise the code path, then parse `/metrics` output or assert against `counter._value.get()`. Example patterns live in `backend/tests/routers/test_observability_middleware.py`.

### Readiness-probe instrumentation pattern

`/api/health/ready` writes the `ecm_health_ready_ok` gauge on every call (1 when all sub-checks pass or are deliberately skipped, 0 otherwise) and records a duration sample per sub-check on `ecm_health_ready_check_duration_seconds`. The sub-check duration wrapper (`_timed` / `_timed_sync` in `routers/health.py`) is the template for any future probe:

```python
start = time.perf_counter()
try:
    return await probe()
finally:
    get_metric("health_ready_check_duration_seconds").labels(
        check=check_name,  # bounded vocabulary
    ).observe(time.perf_counter() - start)
```

Metric emission is wrapped in try/except so a misbehaving collector can never fail a request. This is the invariant: observability is a side effect, not a dependency.

### SLOs and alert rules

See [`docs/sre/slos.md`](./sre/slos.md) (bd-dl1bd) for the initial SLOs built on top of this substrate: readiness availability, HTTP p95 latency, HTTP 5xx error rate, and informational readiness sub-check latency. Corresponding Prometheus alert rules live in [`docs/sre/prometheus_rules.yaml`](./sre/prometheus_rules.yaml); runbooks referenced by each alert are under [`docs/runbooks/`](./runbooks/). Targets are intentionally conservative until 30+ days of production metrics exist. They will be recalibrated after that baseline, tracked as a follow-up bead.

### Out of scope for this substrate

Explicit non-goals, handed off to future beads:

- **Alertmanager / Prometheus / Grafana deployment**: this substrate only exposes the endpoint and ships rules-as-code (see SLO section above); no scrape target is deployed with ECM.
- **Distributed tracing (OpenTelemetry / Jaeger / Tempo)**: the `trace_id` is a correlation id, not a W3C TraceContext id. A future bead will adopt OTel if and when we have a downstream tracer to emit to.
- **Log aggregation (Loki / ELK)**: JSON on stdout is the substrate; a pipeline is its own bead.
