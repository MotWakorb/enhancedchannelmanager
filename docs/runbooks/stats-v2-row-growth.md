# Runbook: Stats v2 Row-Count Growth Anomaly

> `session_telemetry` per-cycle row count is growing > 2× the 7-day baseline. Leading indicator of disk pressure — act before disk fills.

- **Severity**: P3 warning
- **Owner**: SRE
- **Last reviewed**: 2026-05-13
- **Related beads**: `enhancedchannelmanager-skqln.11`, `enhancedchannelmanager-skqln.12`, `enhancedchannelmanager-7i2vv` (rollup tables — future)

**Alerts that route here:**

- `ECMStatsRowCountGrowthAnomaly` (warning) — 24h average per-cycle row count > 2× 7d rolling average

**SLO:** Not tied to a numbered SLO. This is a capacity-planning alert.

---

## What this is

`ecm_session_telemetry_row_count` is a gauge — its value is the number of rows written in the **most recent** BandwidthTracker poll cycle (one cycle per minute by default), NOT the total table size. A sustained doubling of this gauge means the writer is committing twice as many rows per cycle as it did a week ago.

That's a leading indicator for disk pressure: each row is small, but at 60 polls/hour × N channels × 24h × N days the cumulative growth becomes operator-visible. The alert fires at 2× growth so we have time to act before the disk fills.

## Why this matters

Two things scale row count per cycle:

1. **Active channel count.** Each currently-streaming channel writes one row per poll cycle. More channels = more rows. This is legitimate growth.
2. **Writer regression.** A bug that double-emits rows (e.g., a refactor that calls `_write_session_telemetry` twice) doubles per-cycle row count for the same channel population. This is a bug.

Both look the same to this alert; the diagnosis tree distinguishes them.

**Future-state note:** When the rollup tables in bd-7i2vv ship, raw `session_telemetry` will be aggregated into longer-window summaries and the raw table can be retention-pruned more aggressively. Until then, raw rows accumulate at the cadence this alert tracks, and the alert remains the primary capacity-planning signal.

## Symptoms

- `ecm_session_telemetry_row_count` (current value) is > 2× the 7-day average.
- Disk usage on `/config` is climbing faster than expected.
- No user-visible impact yet — this is a leading indicator, not an outage.

## First 10 minutes

1. **Read the current and baseline values:**
   ```promql
   # Current per-cycle row count.
   ecm_session_telemetry_row_count

   # 7-day rolling baseline (recording rule).
   ecm:session_telemetry_row_count:avg7d

   # Ratio.
   avg_over_time(ecm_session_telemetry_row_count[24h])
   / ecm:session_telemetry_row_count:avg7d
   ```
   Expected if real: ratio > 2.0.

2. **Check disk headroom:**
   ```bash
   docker exec ecm-ecm-1 df -h /config
   docker exec ecm-ecm-1 du -sh /config/journal.db
   ```
   If `/config` is > 80% full, treat this as urgent (escalate to manual prune); otherwise it's a capacity-planning conversation.

3. **Count rows in the table to compare against the per-cycle gauge:**
   ```bash
   docker exec ecm-ecm-1 sqlite3 /config/journal.db \
     "SELECT COUNT(*) FROM session_telemetry;"
   ```
   And rows added in the last 24h:
   ```bash
   docker exec ecm-ecm-1 sqlite3 /config/journal.db \
     "SELECT COUNT(*) FROM session_telemetry \
      WHERE polled_at > datetime('now', '-24 hours');"
   ```
   If the 24h rate is wildly higher than (per-cycle gauge × cycles/24h), the gauge is under-reporting and the writer may be emitting outside the instrumented path — file a bead.

## Diagnosis

### Case 1: Active channel count has grown

**Verify:** count distinct channels touched in the last 24h:
```bash
docker exec ecm-ecm-1 sqlite3 /config/journal.db \
  "SELECT COUNT(DISTINCT channel_id) FROM session_telemetry \
   WHERE polled_at > datetime('now', '-24 hours');"
```
Compare against the same query for the 7-day window. If the count is 2× higher in the recent window, the operator has added channels and the alert is legitimate growth.

**Action:**
- Capacity-plan the new disk footprint: rough estimate = `current_table_size × (current_active_channels / 7d_active_channels)` extrapolated forward.
- If projected disk usage will exceed safe headroom within the next 30 days, schedule a manual prune per ADR-007 retention policy OR prioritize bd-7i2vv (rollup tables) to compress historical data.
- Re-tune the 2× alert threshold upward if this growth is the new normal.

### Case 2: Writer regression (double-emit)

**Verify:** for a single channel, count rows per minute over the last hour — should be exactly 1 per poll cycle:
```bash
docker exec ecm-ecm-1 sqlite3 /config/journal.db \
  "SELECT strftime('%Y-%m-%d %H:%M', polled_at) AS minute, COUNT(*) AS rows \
   FROM session_telemetry \
   WHERE channel_id = '<sample-channel>' \
     AND polled_at > datetime('now', '-1 hour') \
   GROUP BY minute ORDER BY minute DESC LIMIT 30;"
```
Expected: `rows = 1` per minute (or 0 if the channel wasn't active). If `rows > 1` consistently, the writer is double-emitting.

**Action:**
- File a P2 bead with the per-minute count output above. This is a regression in the writer.
- Engage the project engineer to identify the offending commit (`git log -p backend/bandwidth_tracker.py` since the last known-good).
- Short-term mitigation: rollback the offending commit if it's recent and isolated.

### Case 3: Retention policy not running

If session_telemetry rows older than the retention window (typically 30 days per ADR-007) still exist, the prune mechanism has stalled or was never configured.

**Verify:**
```bash
docker exec ecm-ecm-1 sqlite3 /config/journal.db \
  "SELECT MIN(polled_at), MAX(polled_at), COUNT(*) FROM session_telemetry;"
```
If `MIN(polled_at)` is older than the retention floor (e.g., > 30 days for the default policy), prune is not running.

**Action:**
- Until bd-7i2vv ships, automated retention may not exist. Check `docs/database_migrations.md` and the task registry (`backend/task_registry.py`) for a `session_telemetry_prune` task or equivalent.
- If no automated prune exists yet, this is a backlog item — file a bead against bd-7i2vv as a priority bump.
- A manual one-off prune is possible via direct SQL but requires operator coordination (do not run blind):
  ```sql
  -- COORDINATE WITH OPERATOR FIRST. This deletes data.
  DELETE FROM session_telemetry WHERE polled_at < datetime('now', '-30 days');
  VACUUM;
  ```

## Resolution

- Case 1 (legitimate growth): tune alert threshold, plan capacity, no immediate action.
- Case 2 (writer regression): rollback or hotfix.
- Case 3 (retention not running): file priority bump on bd-7i2vv; manual prune only with operator authorization.

## Escalation

If disk is > 90% full and Case 1 is the diagnosis:

- This becomes urgent (P2) — capacity planning is no longer "next week's work."
- Engage the operator for an immediate retention decision (cut from 30d to 7d? Manual prune now?).
- If the operator authorizes a manual prune, capture a backup of `journal.db` before the DELETE.

## Post-incident

- [ ] Update this runbook if a new growth-cause class appeared.
- [ ] If Case 2 (regression) was the cause, schedule a postmortem — a double-emit bug shipped past the perf-benchmark gate (skqln.10) is worth understanding.
- [ ] If Case 3 (retention) was the cause, the priority bump on bd-7i2vv is the postmortem outcome.
- [ ] Re-baseline the 7-day average after the incident clears; the alert's 2× threshold may need tuning if the new steady state is materially different.

## See also

- [`backend/observability.py`](../../backend/observability.py) — `ecm_session_telemetry_row_count` gauge registration
- [`backend/bandwidth_tracker.py`](../../backend/bandwidth_tracker.py) — `_write_session_telemetry` (the writer that sets the gauge)
- [`docs/database_migrations.md`](../database_migrations.md) — session_telemetry schema and retention policy reference
- [`docs/security/threat_model_stats_v2.md`](../security/threat_model_stats_v2.md) — privacy reasoning behind retention (shorter is strictly safer)
- [stats-v2-write-failures runbook](./stats-v2-write-failures.md) — when growth causes the writer to fail with ENOSPC, this is the follow-on runbook
