# Runbook: ECM Task Scheduler Stalled

> The ECM task scheduler subsystem is structurally broken or a specific scheduled task hasn't completed in too long. Caught by the `ecm_task_scheduler` Prometheus rules group (bd-qxi02, recommendation from the bd-p5b8i SRE spike).

- **Severity**: P2 page (`ECMTaskSchedulerNextRunNull`), P3 warning (per-task staleness alerts)
- **Owner**: SRE
- **Last reviewed**: 2026-05-15
- **Related beads**: `enhancedchannelmanager-qxi02` (this runbook + metrics + alerts), `enhancedchannelmanager-p5b8i` (SRE spike that surfaced the silent-stall gap), `enhancedchannelmanager-ifmr5` (Bundle H — existing-operator schedule heal)

**Alerts that route here:**

- `ECMTaskSchedulerNextRunNull` (**page**) — one or more enabled non-MANUAL `task_schedules` rows has `next_run_at = NULL`. Scheduler loop will never pick them up. This is the bd-p5b8i disease vector.
- `ECMTaskScheduleStaleStatsRollup` (warning) — `stats_v2_rollup` last success > 25h ago.
- `ECMTaskScheduleStaleCleanup` (warning) — `cleanup` last success > 8d ago.
- `ECMTaskScheduleStaleM3UMonitor` (warning) — `m3u_change_monitor` last success > 12h ago.
- `ECMTaskScheduleStaleStreamProbe` (warning) — `stream_probe` last success > 48h ago.

**SLO:** Not tied to a numbered SLO. Capacity-planning / operational-health class — task cadence is operator-configurable, so ECM cannot make a portable commitment about it. See `docs/sre/slos.md` → "Capacity planning: Task scheduler health (bd-qxi02)".

---

## What this is

ECM's scheduled work (M3U refresh, stream probe, stats rollup, journal/history cleanup, etc.) runs through a single `TaskEngine` that polls `task_schedules` for rows with `next_run_at <= now()` and dispatches them. The two failure modes the `ecm_task_scheduler` alert group catches:

1. **`next_run_at IS NULL` on an enabled non-MANUAL row.** The scheduler loop is filtering for `next_run_at <= now()`, and SQL comparison against NULL evaluates to NULL (neither true nor false). The row is never picked up. The task appears "scheduled" in Settings → Tasks but never runs. The bd-p5b8i incident: every non-MANUAL row had this property for 39+ days on the PO's install (4 months across all operators) before manual investigation found it.
2. **A specific task hasn't succeeded in too long.** The scheduler is picking the row up, but the task is failing repeatedly OR running so slowly that the cadence has slipped past its budget. `ecm_task_schedule_last_success_timestamp{task_id}` is updated only on a `success=True` `TaskResult`, so a task that's running but failing on every execution ages here.

## Why this matters

The original bd-p5b8i disease silently disabled every scheduled task on every operator's install for ~4 months. Symptoms the operator finally noticed:

- Stats v2 Users / Providers panels were missing the most recent day's data → `stats_v2_rollup` not running.
- Journal entries and task-execution history were growing unbounded → `cleanup` not running.
- Auto-creation rules weren't picking up new M3U streams → `m3u_change_monitor` not running.

No alert fired. No journal entry surfaced the disease. The signal was "absence of expected work" — historically the hardest class of failure to detect.

---

## First 10 minutes

### If the alert is `ECMTaskSchedulerNextRunNull`

1. **Confirm the symptom from the database:**
   ```bash
   docker exec ecm-ecm-1 python3 -c "
   import sqlite3
   c = sqlite3.connect('/config/journal.db')
   rows = c.execute('''
       SELECT task_id, schedule_type, enabled, cron_expression, schedule_time, next_run_at
       FROM task_schedules
       WHERE next_run_at IS NULL AND enabled = 1 AND schedule_type != 'manual'
       ORDER BY task_id
   ''').fetchall()
   for r in rows:
       print(r)
   print(f'TOTAL NULL: {len(rows)}')
   "
   ```
   The list is the canonical "what's broken" inventory. Match it against the alert's `$value` (the alert fires on the same count).

2. **Check whether Bundle H's heal has been deployed.** The heal in `backend/task_registry.py` (bd-ifmr5 family) recomputes `next_run_at` on every `sync_from_database` for enabled non-MANUAL rows whose computed `next_run_at` is missing:
   ```bash
   docker exec ecm-ecm-1 grep -n "recompute.*next_run_at\|sync_from_database" /app/task_registry.py | head
   # Then check the image version:
   docker exec ecm-ecm-1 sh -c 'echo $ECM_VERSION'
   ```
   If the running build pre-dates the heal: upgrading the image is the fix. Restart the container; on the next `init_db → TaskRegistry.sync_from_database` cycle, the heal runs and the count drops to 0.

3. **Manual recovery (only if the heal isn't available):**
   ```bash
   docker exec ecm-ecm-1 python3 -c "
   import sqlite3
   from datetime import datetime, timedelta
   c = sqlite3.connect('/config/journal.db')
   # Coarse heal — set next_run_at to (now + 1 minute) for every NULL row.
   # The next scheduler poll picks them up and the task_engine's own
   # post-execution next_run_at recomputation takes over from there.
   target = (datetime.utcnow() + timedelta(minutes=1)).isoformat()
   n = c.execute('''
       UPDATE task_schedules
       SET next_run_at = ?
       WHERE next_run_at IS NULL
       AND enabled = 1
       AND schedule_type != 'manual'
   ''', (target,)).rowcount
   c.commit()
   print(f'Healed {n} rows; next scheduler poll will pick them up.')
   "
   ```
   This is a one-shot recovery. The structural fix is to upgrade to a build containing the Bundle H heal so the disease cannot recur on the next container restart.

### If the alert is one of the per-task staleness alerts

1. **Check task execution history from the journal:**
   ```bash
   docker exec ecm-ecm-1 python3 -c "
   import sqlite3
   c = sqlite3.connect('/config/journal.db')
   rows = c.execute('''
       SELECT started_at, status, success, error, duration_seconds
       FROM task_executions
       WHERE task_id = ?
       ORDER BY started_at DESC
       LIMIT 20
   ''', ('stats_v2_rollup',)).fetchall()  # change task_id as needed
   for r in rows:
       print(r)
   "
   ```
   - **No rows recently:** the task is not being scheduled. Cross-check `task_schedules` for that `task_id` — is `enabled=1`? Is `next_run_at` set? If it's NULL, see the `ECMTaskSchedulerNextRunNull` section above.
   - **Rows recently but `status='failed'`:** the task is running but failing. Read the `error` column for the last few rows; that's your root cause.
   - **Rows recently with `status='completed'` but old:** schedule cadence is too sparse for the budget. Either the operator changed the schedule or the budget is too tight for this install — adjust per Settings → Tasks or tune the alert.

2. **Cross-reference the container log:**
   ```bash
   docker logs ecm-ecm-1 --since 25h 2>&1 | grep -i "stats_v2_rollup\|\[TASKS\]\|\[TASK-ENGINE\]" | tail -50
   ```
   Look for `[<task_id>] Task failed`, `[<task_id>] Task execution failed`, or unexpected silence (no `[<task_id>] Starting task execution` lines at all).

3. **Confirm the Prometheus value matches the database truth:**
   ```bash
   docker exec ecm-ecm-1 curl -s localhost:8000/metrics | grep ecm_task_schedule
   ```
   The gauge values in `/metrics` should reflect the latest scrape. If `ecm_task_schedule_last_success_timestamp` is missing for a `task_id` you expect to see, the task has never completed successfully since the metric was added — that's a different signal from "completed long ago."

---

## Root cause: the bd-p5b8i family

The bd-p5b8i SRE spike attributed the silent stall to schedule rows being written with `next_run_at = NULL` at install time and never being computed (a missing post-insert hook in an earlier migration). The disease is:

- **Symptom:** every non-MANUAL enabled row in `task_schedules` has `next_run_at IS NULL`.
- **Effect:** scheduler loop never picks them up. No executions, no successes, no failures.
- **Detection gap:** existing observability (`ecm_telemetry_rollup_last_success_timestamp` from bd-7i2vv) only fired for rollup-specific tasks AND no alert rule consumed it AND no Prometheus deployment was ingesting the metric. Three layers of "the signal exists, nobody sees it."

bd-qxi02 closes the detection gap with `ecm_task_schedule_next_run_null_count` + the `ECMTaskSchedulerNextRunNull` alert (page-severity, 5m window). Bundle H (bd-ifmr5 family) closes the disease itself by recomputing `next_run_at` on every `TaskRegistry.sync_from_database`.

---

## Recovery

- **Automatic on container restart**, once a build containing both Bundle H (the heal) and bd-qxi02 (the detection) is deployed. The heal runs at startup; the `next_run_at` count drops to 0 within one scrape interval after startup.
- **Manual recovery** is the SQL block in the "First 10 minutes" section above for the `ECMTaskSchedulerNextRunNull` path. For per-task staleness alerts, manual recovery depends on the underlying error — fix the cause, then either wait for the next scheduled run or trigger a manual run via Settings → Tasks → \[task name\] → Run Now.

---

## Validation after fix

```bash
# 1. Confirm the gauge is now 0.
docker exec ecm-ecm-1 curl -s localhost:8000/metrics | grep ecm_task_schedule_next_run_null_count

# 2. Confirm task_schedules has computed next_run_at for every non-MANUAL row.
docker exec ecm-ecm-1 python3 -c "
import sqlite3
c = sqlite3.connect('/config/journal.db')
null_count = c.execute(\"\"\"
    SELECT COUNT(*) FROM task_schedules
    WHERE next_run_at IS NULL AND enabled=1 AND schedule_type != 'manual'
\"\"\").fetchone()[0]
print(f'NULL next_run_at on enabled non-MANUAL rows: {null_count}')
"

# 3. Wait one scheduler poll interval (default ~60s) and confirm at
#    least one task_execution row was created since the heal.
docker exec ecm-ecm-1 python3 -c "
import sqlite3
from datetime import datetime, timedelta
c = sqlite3.connect('/config/journal.db')
cutoff = (datetime.utcnow() - timedelta(minutes=5)).isoformat()
rows = c.execute('SELECT task_id, started_at, status FROM task_executions WHERE started_at >= ? ORDER BY started_at DESC LIMIT 10', (cutoff,)).fetchall()
for r in rows: print(r)
"
```

If step 1 returns 0, step 2 returns 0, and step 3 shows recent task_executions, the recovery is confirmed.

---

## What this runbook does NOT cover

- **Per-task root-cause diagnosis** for the `cleanup` failure mode (disk full, VACUUM blocked, etc.) is in `database-size-warn.md`.
- **Stats v2 rollup-specific root causes** (rollup job sanity checks, prune failures) are in `stats-v2-row-growth.md` and the alert rules in the `ecm_stats_v2_storage` group.
- **The bd-p5b8i fix itself** (the heal logic in `TaskRegistry.sync_from_database`) is documented in the Bundle H bead and the CHANGELOG entry that ships it.
