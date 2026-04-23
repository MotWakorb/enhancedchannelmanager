# Runbook: ECM Readiness Availability

> **Stub** — scaffolded by bd-dl1bd alongside the SLOs. Will be reconciled
> with the runbook template produced by sibling bead bd-bwly4 when that
> lands; if bwly4 merges first, re-flow this content into their template.

**Alerts that route here:**
- `ECMReadinessDown` (page) — `ecm_health_ready_ok == 0` sustained 2m+
- `ECMReadinessFlapping` (ticket) — >4 transitions in 10m
- `ECMReadiness30dBudgetBurn` (ticket) — 6h availability < 95%

**SLO:** [SLO-1 Readiness Availability](../sre/slos.md#slo-1-readiness-availability)

---

## Symptoms

- Readiness endpoint (`/api/health/ready`) returns `503 Not Ready`.
- `ecm_health_ready_ok` gauge reads `0` in Prometheus / `/metrics`.
- Users report ECM "not responding" or "loading forever" on first page load.

## First 5 minutes

1. **Confirm the alert.** Curl `/api/health/ready` directly — the JSON payload names the failing sub-check:
   ```bash
   curl -s http://<host>/api/health/ready | jq .
   ```
   Look for `checks.<name>.status == "fail"` and read `checks.<name>.detail`.

2. **Check container state.**
   ```bash
   docker ps --filter name=ecm-ecm-1
   docker logs --tail 100 ecm-ecm-1 | grep -E '"level":"(ERROR|WARN)"'
   ```

3. **Correlate with recent deploys.** Was there a `docker restart` or image rollout in the last hour? If yes, suspect that first.

## Diagnosis by failing sub-check

### `database` fails
- SQLite file lock: `/config/journal.db` may be write-locked by a long-running transaction. Check for stuck auto-creation runs.
- Disk full: `docker exec ecm-ecm-1 df -h /config`.
- Schema drift: compare `/api/health/schema` output to expected Alembic head.

### `dispatcharr` fails
- Network: can the container reach Dispatcharr? `docker exec ecm-ecm-1 curl -sv <dispatcharr-url>/health`.
- Credentials: settings UI → Dispatcharr → verify token hasn't expired.
- Dispatcharr itself down: check Dispatcharr's own health before blaming ECM.

### `ffprobe` fails (or skipped)
- Binary missing: `docker exec ecm-ecm-1 which ffprobe`.
- Permissions: should be +x for the container user.
- Note: `ffprobe` sub-check failure degrades features but is treated as OK for readiness if marked `skipped` — re-read the health.py logic if the status is ambiguous.

## Mitigation

- Database lock: identify the blocking query via logs (`grep trace_id` of the request that kicked it off), cancel if safe.
- Dispatcharr: if Dispatcharr is down, there is no ECM-side mitigation — set user expectation, wait for upstream.
- Rollback: if a recent deploy caused the failure, `docker restart ecm-ecm-1` first; if still failing, revert to the previous image.

## Post-incident

- If the error budget burn was material (>10% of 30d budget in one event), file a postmortem via the `postmortem` skill.
- Update this runbook with the specific failure mode and fix so the next page is faster.

## See also

- [SLO document](../sre/slos.md)
- [`backend/routers/health.py`](../../backend/routers/health.py) — readiness sub-check implementations
- [`backend/observability.py`](../../backend/observability.py) — metric registration
