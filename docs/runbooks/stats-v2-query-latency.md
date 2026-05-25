# Runbook: Stats v2 Query Latency

> Stats v2 query latency has breached the SLO-9 target. The `/api/stats/*` endpoints are responding slowly, causing visible stalls on the Stats tab.

- **Severity**: Warning (non-paging; SLO-9 is warn-only — stats panels are admin tooling, not a user-facing outage)
- **Owner**: SRE
- **Last reviewed**: 2026-05-25
- **Related beads**: `enhancedchannelmanager-skqln.11`, `enhancedchannelmanager-zppgx`

**Alerts that route here:**

- `ECMStatsQueryLatencyHigh` (warning) — p95 > 800ms sustained 15m
- `ECMStatsQueryLatencyP99High` (warning) — p99 > 2s sustained 15m

**SLO:** [SLO-9 Stats v2 Query Latency](../sre/slos.md#slo-9-stats-v2-query-latency)

---

## Symptoms

- `ECMStatsQueryLatencyHigh` or `ECMStatsQueryLatencyP99High` alert fires.
- Stats tab in the UI takes noticeably long to render (>1s spinner).
- `ecm_stats_query_duration_seconds` p95 or p99 histograms exceed thresholds when queried by endpoint.
- No SLO-7 write-failure alert is co-firing (if it is, start with the [write-failures runbook](./stats-v2-write-failures.md) first).

## First 5 minutes

1. **Confirm the alert is real.** Query the histogram directly:
   ```promql
   # p95 by endpoint (identifies which endpoint is slow)
   histogram_quantile(
     0.95,
     sum by (le, endpoint) (
       rate(ecm_stats_query_duration_seconds_bucket[5m])
     )
   )
   ```
   If all endpoints are slow → system-wide SQLite pressure (Branch A or B). If one endpoint is slow → isolated index miss or N+1 (Branch C).

2. **Check for co-firing write-failure alert.** If `ECMStatsTelemetryWriteFailing` is also active, a slow write path (SQLite lock, WAL checkpoint stall) is cascading into query latency. Go to [stats-v2-write-failures.md](./stats-v2-write-failures.md) first.

3. **Check readiness.** If `ecm_health_ready_ok == 0` with a failing `database` sub-check, a broader DB outage is root cause. Go to [readiness runbook](./readiness_availability.md) first.

4. **Identify the slow endpoint.** Use the `endpoint`-grouped PromQL above or scan logs:
   ```bash
   docker logs ecm-ecm-1 --since 15m \
     | grep '\[STATS\]' \
     | grep -iE 'slow|timeout|latency|took [0-9]{4,}' \
     | tail -30
   ```

## Diagnosis tree

### Branch A: `session_telemetry` table grown past index-friendly size

**When:** All stats endpoints are slow; the table has been running without `bd-7i2vv` rollup tables shipping.

**Verify:**
```bash
docker exec ecm-ecm-1 sqlite3 /config/journal.db \
  "SELECT COUNT(*) FROM session_telemetry;"
```
If > 5,000,000 rows: the raw-row growth is outpacing the existing indexes.

```bash
docker exec ecm-ecm-1 sqlite3 /config/journal.db \
  "EXPLAIN QUERY PLAN SELECT * FROM session_telemetry \
   WHERE channel_id = 1 ORDER BY start_time DESC LIMIT 100;"
```
Look for `SCAN` (no index) vs `SEARCH` (index used). A `SCAN` on a large table is the problem.

**Recovery:**
1. The structural fix is `bd-7i2vv` (rollup tables + retention policy) — if not yet shipped, open a priority bump bead.
2. Interim: run the nightly rollup task manually to prune old raw rows (if rollup task exists):
   ```bash
   docker exec ecm-ecm-1 curl -s -X POST http://localhost:<port>/api/tasks/stats_v2_rollup/run
   ```
3. After pruning, monitor `ecm_session_telemetry_row_count` gauge to confirm reduction and re-check p95 latency.

### Branch B: SQLite WAL checkpoint stalling

**When:** Latency spikes are transient (episodic, not sustained), and correlate with the nightly rollup task or a bulk auto-creation run.

**Verify:**
```bash
docker logs ecm-ecm-1 --since 30m \
  | grep -iE 'wal|checkpoint|locked' \
  | tail -30
```
A WAL file growing large (`ecm_database_wal_size_bytes` > 200 MB per the database-size alert) means `wal_autocheckpoint` is not firing fast enough.

**Recovery:**
1. Force a manual checkpoint (takes an exclusive lock briefly — do during low-traffic window):
   ```bash
   docker exec ecm-ecm-1 sqlite3 /config/journal.db "PRAGMA wal_checkpoint(TRUNCATE);"
   ```
2. Confirm WAL shrank:
   ```bash
   docker exec ecm-ecm-1 ls -lh /config/journal.db-wal
   ```
3. If the WAL re-grows immediately, a long-running reader is preventing checkpointing — see [database-size-warn runbook](./database-size-warn.md) for the WAL-vs-body triage table.

### Branch C: Missing or unused index on a specific endpoint

**When:** Only one endpoint is slow (e.g., `/api/stats/watch-time/{user_id}` but not `/api/stats/channel-bandwidth`).

**Verify — identify the query:**
```bash
docker logs ecm-ecm-1 --since 30m \
  | grep '\[STATS\]' \
  | grep -i 'watch-time\|watch_time\|slow\|[0-9]\{4,\}ms' \
  | tail -20
```
Then run the relevant query with `EXPLAIN QUERY PLAN` in sqlite3 to confirm whether the expected index is in use.

**Recovery:**
1. If the index exists but is not used: SQLite query planner may prefer a full scan when table statistics are stale. Run:
   ```bash
   docker exec ecm-ecm-1 sqlite3 /config/journal.db "ANALYZE;"
   ```
   Then re-run `EXPLAIN QUERY PLAN` — ANALYZE refreshes the statistics that guide the planner.
2. If the index is genuinely missing: file a bead against the missing index. Do not add ad-hoc indexes to production — changes go through Alembic migrations. Document the missing index as a backlog candidate.
3. Cross-reference: the in-CI benchmark gate (`skqln.10`) is supposed to catch missing-index regressions before they reach production. If the gate didn't catch it, the bench fixture may not cover the affected table state — note this in the bead.

### Branch D: N+1 query pattern from a frontend Stats panel

**When:** Latency is episodic and correlates with a specific user action (opening a panel, switching granularity). Multiple short queries, not one long one.

**Verify:**
```bash
docker logs ecm-ecm-1 --since 15m \
  | grep '\[STATS\]' \
  | grep 'GET /api/stats' \
  | awk '{print $5}' \
  | sort | uniq -c | sort -rn | head -20
```
If the same endpoint appears dozens of times in a short window: a frontend component is calling it in a loop. Check network tab in browser devtools for rapid sequential `/api/stats/*` calls.

**Recovery:**
1. Identify the frontend component making repeated calls and file a bead. The fix is usually batching or memoization on the frontend side.
2. Interim: the query itself is not inherently slow; the volume is the problem. No backend change is needed until the frontend loop is fixed.

## Mitigation summary

- **All endpoints slow, sustained**: Branch A (table size) → prune or rollup; or Branch B (WAL stall) → checkpoint.
- **One endpoint slow**: Branch C (index miss) → ANALYZE + file bead for structural fix.
- **Episodic, correlates with user actions**: Branch D (N+1) → frontend bead.
- **Write failures co-firing**: start with [stats-v2-write-failures.md](./stats-v2-write-failures.md) — query slowness is likely downstream of the write-side root cause.
- **Never skip the rollup task as a "fix"**: if `bd-7i2vv` retention is not yet shipped, manual pruning is a stopgap, not a solution. File the priority bump and track it.

## Escalation

SLO-9 is warn-only. This alert does not require 3 AM paging. Triage during business hours:

- Provide: alert start time, slow endpoint(s) from PromQL, row count from sqlite3, branch from diagnosis tree that matched.
- Escalate to P2 if query latency is causing users to report the Stats tab as unusable (subjective threshold: > 5 s on a panel load that was previously < 1 s).

## Post-incident

- [ ] Record which branch applied and what the recovery action was.
- [ ] If Branch A: confirm `bd-7i2vv` is on the backlog with correct priority.
- [ ] If Branch C: confirm a bead exists for the missing index migration.
- [ ] If the in-CI perf benchmark (`skqln.10`) did not catch the regression: file a bead to extend the bench fixture.

## See also

- [SLO-9: Stats v2 Query Latency](../sre/slos.md#slo-9-stats-v2-query-latency)
- [`backend/routers/stats.py`](../../backend/routers/stats.py) — Stats endpoint implementations
- [`backend/observability.py`](../../backend/observability.py) — `ecm_stats_query_duration_seconds` histogram registration
- [stats-v2-write-failures.md](./stats-v2-write-failures.md) — write-side context (Branch B overlap)
- [database-size-warn.md](./database-size-warn.md) — WAL checkpoint triage (Branch B)
- [stats-v2-row-growth.md](./stats-v2-row-growth.md) — storage growth context (Branch A)
