# Runbook: Stats v2 Telemetry Write Failures

> `session_telemetry` writes are failing at a rate that breaches SLO-7. The poll loop survives — failures are wrapped — but the data layer is degrading.

- **Severity**: P2 ticket → P1 page if sustained 30m+
- **Owner**: SRE
- **Last reviewed**: 2026-05-13
- **Related beads**: `enhancedchannelmanager-skqln.11`, `enhancedchannelmanager-skqln.12`, `enhancedchannelmanager-skqln.3`

**Alerts that route here:**

- `ECMStatsTelemetryWriteFailing` (page) — failure ratio > 5% sustained 30m
- `ECMStatsTelemetryWriteFailingWarn` (ticket) — failure ratio > 5% over 5m
- `ECMStatsQueryLatencyHigh` / `ECMStatsQueryLatencyP99High` (warning) — when the failure root cause is a stalled writer or migration mismatch that also slows queries, this runbook applies

**SLO:** [SLO-7 Stats v2 Telemetry Write Success Rate](../sre/slos.md#slo-7-stats-v2-telemetry-write-success-rate)

---

## Symptoms

- `ecm_session_telemetry_writes_total{result="failure"}` rate climbs and exceeds 5% of total writes.
- New `session_telemetry` rows stop appearing or appear intermittently; Stats v2 panels go stale.
- Frontend Stats tab still renders, but data is hours behind real time — the user-visible signal is "the chart hasn't updated since X."
- Logs contain `[STATS_V2]` ERROR or WARNING lines (the helper logs the swallowed exception before incrementing the failure counter).

## First 5 minutes

1. **Confirm the alert is real.** Read the failure ratio directly:
   ```promql
   sum(rate(ecm_session_telemetry_writes_total{result="failure"}[5m]))
   / sum(rate(ecm_session_telemetry_writes_total[5m]))
   ```
   Expected if real: > 0.05. Expected if div-by-zero (idle): NaN — alert shouldn't have fired (the rule guards `> 0.001`).

2. **Pull the error lines.** Failures log before they increment the counter:
   ```bash
   docker logs ecm-ecm-1 --since 15m \
     | grep '\[STATS_V2\]' \
     | grep -iE 'error|exception|failed' \
     | tail -50
   ```
   The first error line usually tells you which branch of the diagnosis tree below applies. Capture a `trace_id` from one entry if present and grep across all logs.

3. **Check readiness.** If `ecm_health_ready_ok == 0` and the database sub-check is failing, the write failures are downstream of a broader DB outage — go to [readiness runbook](./readiness_availability.md) first.

## Diagnosis tree

Pick the branch that matches your error signature. If unclear, run all four checks in order.

### Branch A: Dispatcharr unreachable

**Verify:**
```bash
docker logs ecm-ecm-1 --since 15m | grep -iE 'dispatcharr.*(timeout|refused|unreachable|connection)'
```
If present: Dispatcharr is the upstream root cause. The resolver call inside `_write_session_telemetry` raised; the write helper caught and incremented `result="failure"`.

**Recovery:**
1. Confirm Dispatcharr is down independently: `curl -sS http://<dispatcharr-host>:<port>/api/health || echo DOWN`.
2. If down, this is a Dispatcharr incident; ECM recovers automatically when Dispatcharr returns.
3. If up, investigate network between ECM and Dispatcharr (Docker bridge, host firewall, DNS).

### Branch B: SQLite database locked

**Verify:**
```bash
docker logs ecm-ecm-1 --since 15m | grep -iE 'database is locked|database locked|OperationalError.*locked'
```
If present: SQLite WAL contention. Most common cause is a concurrent bulk operation (auto-creation, M3U digest, channel-group batch update) holding a long transaction.

**Recovery:**
1. Identify the offending bulk operation in logs (the heavy writer will be in the same time window):
   ```bash
   docker logs ecm-ecm-1 --since 30m \
     | grep -iE '\[(AUTO-CREATION|M3U|CHANNELS)\]' \
     | tail -50
   ```
2. If a long-running task is mid-flight, let it finish — interrupting it makes things worse. The session_telemetry writer self-heals once the lock clears.
3. If the lock has persisted > 10m, a transaction has leaked. Last-resort: `docker restart ecm-ecm-1`. **Capture log snapshot first**:
   ```bash
   docker logs ecm-ecm-1 --since 1h > /tmp/incident-stats-v2-write.log
   docker restart ecm-ecm-1
   ```
4. After restart, monitor `ecm_session_telemetry_writes_total{result="failure"}` for 10m to confirm recovery.

### Branch C: Disk full

**Verify:**
```bash
docker exec ecm-ecm-1 df -h /config
```
If `/config` is > 95% full or shows ENOSPC errors in logs (`grep -i 'no space\|ENOSPC' /tmp/incident*.log`): disk pressure is the root cause.

**Recovery:**
1. **Stop the bleeding first** — prune old `session_telemetry` rows per ADR-007 retention policy (typically 30d raw + indefinite rollups; if rollup tables from bd-7i2vv haven't shipped, only raw rows exist):
   ```bash
   # Inspect retention policy first — do NOT run blind. The retention
   # script / endpoint is the project-defined mechanism; coordinate with
   # the operator before running. See docs/database_migrations.md for
   # the canonical session_telemetry retention command.
   ```
2. If no automated retention exists yet (likely true pre-bd-7i2vv), open a ticket against bd-7i2vv as a priority bump and engage the project engineer for a manual prune procedure.
3. Investigate the growth — go to [stats-v2-row-growth runbook](./stats-v2-row-growth.md) for the storage-growth angle.

### Branch D: Migration mismatch

**Verify:**
```bash
docker logs ecm-ecm-1 --since 15m | grep -iE 'no such column|no such table.*session_telemetry|OperationalError.*column'
```
Also check the deployed schema is what the writer expects:
```bash
docker exec ecm-ecm-1 sqlite3 /config/journal.db ".schema session_telemetry" | head -20
```
If columns are missing relative to the current code's expectations: Alembic migrations did not run, OR an older container is running against a newer schema (rare, but possible).

**Recovery:**
1. Compare the Alembic head version against the database:
   ```bash
   docker exec ecm-ecm-1 alembic current
   docker exec ecm-ecm-1 alembic heads
   ```
   If `current` < `heads`: migrations are pending. Apply them:
   ```bash
   docker exec ecm-ecm-1 alembic upgrade head
   ```
2. If `current` > `heads`: a newer schema is running against older code (uncommon — usually the result of a manual `alembic upgrade` followed by a code rollback). Either re-deploy the matching code, or `alembic downgrade <prior-rev>` and restart.
3. Reference: [`docs/database_migrations.md`](../database_migrations.md). Migration ordering matters — see also [stats-v2-deployment-safety runbook](./stats-v2-deployment-safety.md).

## Mitigation summary

- **Rollback is rarely the right tool here** — the writer's try/except means rolling back the *code* won't help if the root cause is infrastructure (disk, lock, migration). Diagnose first.
- **Container restart** clears in-memory lock state but loses logs useful for postmortem. Always capture `docker logs --since 1h` to a file before restarting.
- **Do not** disable the writer by toggling `ECM_SESSION_TELEMETRY_WRITE_ENABLED` — that env var is **retired** as of skqln.3 step (d) and has no effect.

## Escalation

If failure ratio remains > 5% after running the matching diagnosis branch:

- Page the SRE persona via the operator's chosen channel (no rotation defined yet — pages route to `curt@lecaptain.org` until on-call exists).
- Provide: alert start time, branch from diagnosis tree that matched, recovery steps attempted, current `ecm_session_telemetry_writes_total{result="failure"}` 5m rate.

## Post-incident

- [ ] Capture `docker logs ecm-ecm-1 --since 2h > /tmp/postmortem-stats-v2-write.log` before any container restart that wasn't already captured.
- [ ] Open a bead for root-cause investigation if not yet identified.
- [ ] If the diagnosis tree above didn't cover the root cause, append a new branch with the verification grep and recovery action.
- [ ] If sustained > 30m (page-tier), schedule a blameless postmortem via `/postmortem`.

## See also

- [SLO-7: Stats v2 Telemetry Write Success Rate](../sre/slos.md#slo-7-stats-v2-telemetry-write-success-rate)
- [`backend/bandwidth_tracker.py`](../../backend/bandwidth_tracker.py) — `_write_session_telemetry` helper and try/except wrapper
- [`backend/observability.py`](../../backend/observability.py) — `ecm_session_telemetry_writes_total` counter registration
- [stats-v2-deployment-safety runbook](./stats-v2-deployment-safety.md) — migration order and post-deploy verification
- [stats-v2-row-growth runbook](./stats-v2-row-growth.md) — storage-side context if Branch C applies
