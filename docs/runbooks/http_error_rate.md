# Runbook: ECM HTTP Error Rate

> **Stub** — scaffolded by bd-dl1bd. Reconcile with bd-bwly4 runbook template
> when that lands.

**Alerts that route here:**
- `ECMHTTPError5xxElevated` (ticket) — 5xx rate > 1% for 10m
- `ECMHTTPError5xxCritical` (page) — 5xx rate > 10% for 3m

**SLO:** [SLO-3 HTTP Error Rate](../sre/slos.md#slo-3-http-error-rate)

---

## Symptoms

- Users report "something went wrong" error banners, failed saves, blank pages.
- 5xx rate visible on `/metrics`:
  ```promql
  sum(rate(ecm_http_requests_total{status=~"5.."}[5m]))
  / sum(rate(ecm_http_requests_total[5m]))
  ```

## First 5 minutes

1. **Identify the failing path(s).**
   ```promql
   sum by (path, status) (
     rate(ecm_http_requests_total{status=~"5.."}[5m])
   )
   ```
   Is it concentrated on one endpoint or spread across many? Concentrated = a specific bug or dependency. Spread = infrastructure.

2. **Pull ERROR logs for the offending path.**
   ```bash
   docker logs ecm-ecm-1 --since 15m \
     | grep '"level":"ERROR"' \
     | jq 'select(.path == "/api/<offending-path>")' \
     | head -20
   ```
   Extract one `trace_id` and grep for it across all logs — full request story, ordered.

3. **Check readiness.** If readiness is failing, fix that first; 5xxs often downstream of it.

## Common causes

### Unhandled exception in a router
- Signals: ERROR logs with stack traces; OWASP 500-scrubber middleware redacts user-facing detail but full trace is in the log.
- Mitigation: hotfix or rollback. File bug bead.

### Database session exhaustion
- Signals: Errors cite `sqlalchemy` or `database`; readiness database sub-check also slow/failing.
- Mitigation: restart container (last resort), investigate session leak in the code path (missing `finally: db.close()`).

### Dispatcharr 5xx passthrough
- Signals: Errors on proxying endpoints (streams, EPG sync, channel groups); Dispatcharr-side logs confirm upstream failure.
- Mitigation: no ECM-side fix; upstream recovery. Consider circuit-breaking if sustained.

### Rate-limited (should be 429, not 5xx — investigate if 5xx)
- slowapi limiter on `/api/auth/login`. If 5xx correlates with auth endpoints during a brute-force attempt, investigate whether the limiter is misconfigured to 5xx instead of 429.

## Mitigation

- Rollback is the fastest remedy for recent-deploy-caused 5xx.
- For infra issues (database/disk/network), mitigate the infra before the app.
- Capture a log snapshot (`docker logs ecm-ecm-1 --since 1h > /tmp/incident.log`) **before** restarting — container restart loses in-memory state useful for postmortem.

## See also

- [SLO document](../sre/slos.md)
- OWASP 500-scrubber: `backend/main.py` (search `scrub_500`)
- [Observability docs](../backend_architecture.md#observability)
