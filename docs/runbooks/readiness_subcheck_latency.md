# Runbook: ECM Readiness Sub-check Latency

> **Stub** — scaffolded by bd-dl1bd. Reconcile with bd-bwly4 runbook template
> when that lands.

**Alerts that route here (warning severity — informational only):**
- `ECMReadinessDatabaseCheckSlow` — database sub-check p95 > 50ms for 10m
- `ECMReadinessDispatcharrCheckSlow` — dispatcharr sub-check p95 > 500ms for 10m
- `ECMReadinessFfprobeCheckSlow` — ffprobe sub-check p95 > 100ms for 10m

**SLO:** [SLO-4 Readiness Sub-check Latency](../sre/slos.md#slo-4-readiness-sub-check-latency-informational)

---

## Why this is a warning, not a page

Readiness sub-check latency is a **leading indicator**, not a user-impacting failure. A slow sub-check is a heads-up that one of the production SLOs (latency or availability) is about to breach. Treat these alerts as "look at this now while you still have time" rather than "wake up at 3am."

If the related production SLO alert (`ECMReadinessDown`, `ECMHTTPLatencyHighP95`) also fires, escalate to that runbook — the warning here was the early signal for that real incident.

## Diagnosis

### database check slow (>50ms)
- Expected shape: sub-check is a cheap `PRAGMA` read, should be <5ms on warm disk.
- Likely cause: file lock contention from a long-running write (bulk auto-creation, digest). Check for `[TASKS]` log lines indicating a running job.
- Check disk: `docker exec ecm-ecm-1 df -h /config` and `iostat 1 5` on the host.

### dispatcharr check slow (>500ms)
- Expected shape: single HTTP HEAD / `/health` to Dispatcharr, should return in tens of ms on LAN.
- Likely cause: network latency, Dispatcharr itself slow, DNS resolution delay.
- Diagnose: `docker exec ecm-ecm-1 curl -w '%{time_total}\n' -so /dev/null <dispatcharr-health-url>`.

### ffprobe check slow (>100ms)
- Expected shape: a spawn + quick probe, typically <50ms.
- Likely cause: host disk I/O saturation, binary relocated, missing codecs causing ffprobe to scan harder.
- Diagnose: `docker exec ecm-ecm-1 which ffprobe && docker exec ecm-ecm-1 ffprobe -version`.

## Mitigation

For a warning-only alert, mitigation is typically "open an investigative ticket, watch the related production SLO." Only escalate to page-level action if:

1. The warning persists beyond one hour.
2. The corresponding production SLO (latency / availability) starts trending toward breach.
3. Multiple sub-checks fire simultaneously (indicates infrastructure-level problem, not dependency-level).

## See also

- [SLO document](../sre/slos.md)
- [`backend/routers/health.py`](../../backend/routers/health.py) — sub-check implementations with `_timed` wrapper
- Production SLO runbooks: [readiness](./readiness_availability.md), [latency](./http_latency.md), [error rate](./http_error_rate.md)
