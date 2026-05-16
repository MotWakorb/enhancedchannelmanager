# Runbook: ECM Database File Size

> The on-disk SQLite database (`journal.db`) or its WAL (`journal.db-wal`) has grown past a capacity-planning threshold. Leading indicator of disk pressure — act before the weekly VACUUM stops keeping up.

- **Severity**: P3 warning (size threshold + growth anomaly), P2 page (size > 2 GB)
- **Owner**: SRE
- **Last reviewed**: 2026-05-15
- **Related beads**: `enhancedchannelmanager-ygoqr` (this runbook + metrics + CleanupTask CRON flip), `enhancedchannelmanager-ej995` (WAL checkpoint at startup — in-flight), `enhancedchannelmanager-7i2vv` (Stats v2 retention), `enhancedchannelmanager-dmu8w` (journal-entry retention)

**Alerts that route here:**

- `ECMDatabaseSizeWarn` (warning) — body > 500 MB for 30 minutes
- `ECMDatabaseSizePage` (page) — body > 2 GB for 10 minutes
- `ECMDatabaseWALSizeWarn` (warning) — WAL > 200 MB for 15 minutes
- `ECMDatabaseSizeGrowthAnomaly` (warning) — week-over-week delta > 200 MB for 24 hours

**SLO:** Not tied to a numbered SLO. Capacity-planning class — disk-pressure failure modes are operator-environment-dependent and ECM does not make a portable commitment about them.

---

## What this is

`ecm_database_size_bytes` and `ecm_database_wal_size_bytes` are gauges sampled by `observability.update_database_size_metrics` (see `backend/observability.py`). They are emitted at exactly two points:

1. Process startup, after `database._perform_maintenance` runs the boot-time VACUUM.
2. After the weekly `CleanupTask` VACUUM completes (or fails — the gauges are still updated).

So the value lags the on-disk reality by at most "one weekly cron interval + one container lifetime." For acute investigation, run `du -sh /config/journal.db /config/journal.db-wal` to read the current truth from the filesystem.

The recording rule `ecm:database_size_bytes:weekly_delta` is the week-over-week growth used by `ECMDatabaseSizeGrowthAnomaly`.

## Why this matters

A growing SQLite file is a slow-burn failure mode that the weekly VACUUM normally bounds:

- **Steady state (healthy):** 50-200 MB body, < 50 MB WAL on a long-running install with bd-dmu8w (90-day journal-entry retention) and bd-7i2vv (30-day Stats v2 raw-row retention) applied weekly.
- **Drifted (warn):** > 500 MB body usually means a contributor table has lost its retention boundary OR the operator has the `CleanupTask` set to MANUAL (bd-ygoqr default flipped to CRON for fresh installs, but pre-existing operators keep their persisted choice).
- **Disruptive (page):** > 2 GB body means the weekly VACUUM holds an exclusive lock for seconds-to-minutes, which is operator-visible during streaming. The weekly cleanup may also start failing if the lock duration exceeds the read/write timeout window of concurrent operations.
- **WAL-stalled (warn):** > 200 MB WAL means SQLite's auto-checkpoint is not firing — usually a hung writer holding a transaction open. Distinct from "the working set grew."

## WAL vs body — triage table

The first decision after any of these alerts: is the size in the body, the WAL, or both? They have completely different root causes.

| Body size | WAL size | Most likely cause |
|-|-|-|
| Large | Small | A contributor table is large. Run the table-attribution query below. |
| Small | Large | Checkpointing is stalled. Run the manual checkpoint below. |
| Large | Large | Both — start with the manual checkpoint (cheap), then re-evaluate body size. |
| Healthy | Spiking | Transient writer activity (an auto-creation run, a backup restore). Wait for the next scrape; if it persists, treat as "large WAL." |

## First 10 minutes

1. **Read the current values from the filesystem (ground truth):**
   ```bash
   docker exec ecm-ecm-1 ls -lh /config/journal.db /config/journal.db-wal /config/journal.db-shm 2>&1
   docker exec ecm-ecm-1 df -h /config
   ```
   Compare against the last metric scrape — if there's a wide gap, the metric is stale (no recent VACUUM); the filesystem is what to act on.

2. **Read the gauges (if Prometheus is configured):**
   ```promql
   ecm_database_size_bytes
   ecm_database_wal_size_bytes
   ecm:database_size_bytes:weekly_delta
   ```

3. **Decide WAL-vs-body** using the triage table above.

## Manual WAL checkpoint (WAL-large case)

Cheapest action; safe to run any time. Forces SQLite to flush WAL frames into the body file and truncate the WAL.

```bash
docker exec ecm-ecm-1 sqlite3 /config/journal.db "PRAGMA wal_checkpoint(TRUNCATE);"
```

Expected output: three integers — `(busy, log_pages, checkpointed_pages)`. `busy=0` means the checkpoint completed; `busy=1` means a writer was holding the lock and the checkpoint partially completed. Re-run if `busy=1`.

After the checkpoint, re-read the WAL size:
```bash
docker exec ecm-ecm-1 ls -lh /config/journal.db-wal
```

If the WAL keeps re-growing past 200 MB within an hour, the root cause is a hung writer — see "Hung-writer diagnostic" below.

## Per-table size attribution (body-large case)

SQLite doesn't expose per-table size directly; estimate via `dbstat`:

```bash
docker exec ecm-ecm-1 sqlite3 /config/journal.db <<'SQL'
SELECT name, SUM(pgsize) AS bytes
FROM dbstat
GROUP BY name
ORDER BY bytes DESC
LIMIT 15;
SQL
```

Typical large contributors and the action for each:

| Table | If it's large, ask | Action |
|-|-|-|
| `journal_entries` | Is the cleanup task running? Is `journal_days` config still 90? | Re-enable / shorten retention; trigger manual cleanup task run from Settings → Tasks. |
| `task_executions` | Same as above; default retention is 30d. | Same as above. |
| `session_telemetry` | Is the StatsV2RollupTask running and pruning? Read `ecm_telemetry_raw_rows_pruned`. | Investigate per `stats-v2-row-growth.md`. ADR-007 D1 caps raw rows at 30d. |
| `bandwidth_daily` | This table has 1-year retention, so size > 100 MB is unusual. | Check polling cadence; usually a misconfiguration. |
| `stream_stats` | Probe history. Default retention is 30d. | Trigger manual cleanup task. |

If a non-obvious table dominates, file a bead for retention investigation rather than ad-hoc deleting rows.

## Hung-writer diagnostic (WAL keeps re-growing)

When the manual checkpoint truncates the WAL but it grows back within minutes, a writer is holding a transaction open longer than expected. Common causes:

- A backup-restore task in progress (legitimate; let it complete).
- A misbehaving migration or maintenance script (less common post-bd-zaaey).
- A long-running stats query not committing its read transaction (rare; reads don't block WAL checkpoint, but write transactions do).

Inspect ECM's task status:
```bash
curl -s http://localhost:${ECM_PORT}/api/tasks/status | jq '.[] | select(.status == "running")'
```

Cross-reference the timestamps of WAL re-growth bursts against task start times in the logs:
```bash
docker logs ecm-ecm-1 --since 1h 2>&1 | grep -E "Starting task|Task completed|Task failed"
```

If nothing in ECM accounts for the held transaction, the writer is likely the SQLite engine itself executing a long migration — check `docker logs ecm-ecm-1 | grep -E "ALEMBIC|migration|VACUUM"` to confirm.

## Cleanup task is MANUAL when it shouldn't be

If `ECMDatabaseSizeGrowthAnomaly` keeps firing despite the operator believing the cleanup runs automatically, verify the schedule via the API:

```bash
curl -s http://localhost:${ECM_PORT}/api/tasks/status | jq '.[] | select(.task_id == "cleanup")'
```

Look at `.schedule.schedule_type` — if it's `"manual"`, the operator is on a pre-bd-ygoqr persisted config (the bd-ygoqr CRON default applies only to fresh installs that have no `ScheduledTask` row yet). To enable scheduling without losing the operator's other config:

1. Open Settings → Tasks → Database Cleanup in the UI.
2. Change schedule type from "Manual" to "Cron" with expression `0 2 * * 0` (Sunday 02:00 UTC).
3. Save. The next weekly run will VACUUM and emit the gauge.

The bd-ygoqr CHANGELOG entry for v0.17.0-0039 documents this explicitly so operators can find the rationale.

## After resolution

- Update `ecm:database_size_bytes:weekly_delta` after the next weekly cleanup completes — the alert auto-clears once the delta drops below 200 MB.
- If the resolution involved retention-tightening on a contributor table, file a bead noting the new retention target and a follow-up to revisit after 30 days of data.
- If the resolution involved manual SQL deletion of rows, file an incident postmortem — that's a recovery action that should never be ad-hoc.

## Related runbooks

- [`stats-v2-row-growth.md`](./stats-v2-row-growth.md) — companion alert when the `session_telemetry` table specifically is the contributor.
- [`readiness_availability.md`](./readiness_availability.md) — when DB size is large enough that readiness probes start timing out, cascade into SLO-1.
