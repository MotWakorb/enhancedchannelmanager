# Runbook: ECM HTTP Latency

> **Stub** — scaffolded by bd-dl1bd. Reconcile with bd-bwly4 runbook template
> when that lands.

**Alerts that route here:**
- `ECMHTTPLatencyHighP95` (ticket) — p95 > 500ms for 10m
- `ECMHTTPLatencyCriticalP95` (page) — p95 > 2s for 5m

**SLO:** [SLO-2 HTTP Request Latency](../sre/slos.md#slo-2-http-request-latency)

---

## Symptoms

- Users report slow page loads, spinners that never resolve, saved settings "not sticking" (actually slow roundtrip).
- `histogram_quantile(0.95, sum by (le) (rate(ecm_http_request_duration_seconds_bucket[5m])))` > 500ms.

## First 5 minutes

1. **Identify the slow route(s).** Group p95 by `path` label to find the offender:
   ```promql
   histogram_quantile(
     0.95,
     sum by (le, path) (
       rate(ecm_http_request_duration_seconds_bucket[5m])
     )
   )
   ```
   Sort descending; one or two routes usually dominate.

2. **Check readiness.** If `ecm_health_ready_ok == 0`, the latency is a downstream effect — go to [readiness runbook](./readiness_availability.md) first.

3. **Grep access logs for slow requests.** Every request emits a structured `ecm.access` line with `duration_ms`:
   ```bash
   docker logs ecm-ecm-1 --since 15m \
     | grep '"logger":"ecm.access"' \
     | jq 'select(.duration_ms > 1000)' \
     | head -50
   ```
   The `trace_id` on each entry correlates to every other log line from that request — follow the thread to find what was slow.

## Common causes

### Database contention
- Signals: `path` labels concentrated on write-heavy routes (`/api/channels`, `/api/auto-creation-rules`); readiness `database` sub-check latency also climbing.
- Mitigation: pause bulk operations (auto-creation, digest), identify the long transaction via logs.

### Dispatcharr upstream slow
- Signals: readiness `dispatcharr` sub-check latency alert also firing; slow routes are ones that proxy Dispatcharr (streams, EPG).
- Mitigation: confirm Dispatcharr health independently; no ECM-side fix beyond timeout tuning.

### N+1 frontend loop
- Signals: high `ecm_http_requests_total` rate from a single path pattern, each individually fast but aggregated slow.
- Mitigation: usually a frontend bug; file bead and (short-term) rate-limit the offending view.

### Cold cache / first request after restart
- Signals: Transient — resolves within a few minutes without intervention. Don't page over this.

## Mitigation

- If a specific route is the offender and recently changed: consider rolling back the backend image.
- If infrastructure (disk I/O, CPU saturation on the host): check host metrics before blaming ECM.
- If unexplained and sustained: escalate, capture log snapshot for postmortem.

## See also

- [SLO document](../sre/slos.md)
- [`backend/main.py`](../../backend/main.py) — observability middleware (search `_metric_path_label`)
- [Observability docs](../backend_architecture.md#observability)
