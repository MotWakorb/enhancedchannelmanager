"""Stats v2 nightly rollup + prune task (ADR-007 D3-D6, bd-7i2vv).

Implements the nightly job that materialises the two ADR-007 D4/D5 daily
rollup tables from ``session_telemetry`` and then prunes raw rows past
the D1 30-day retention window.

Operation order per run (ADR-007 D3):

1. Discover the set of UTC days that still need rolling up — from the
   stored ``last_completed_day`` forward, up to (but excluding) the
   current UTC day. The current day is intentionally skipped because the
   raw stream is still landing rows for it; the rollup only ever covers
   *complete* days.
2. For each named rollup (``user_daily``, ``provider_daily``), aggregate
   the discovered days into the destination table using an INSERT-OR-
   REPLACE upsert keyed on the rollup PK. Idempotent across re-runs and
   self-healing when a prior partial run left a half-populated row.
3. Persist ``telemetry_rollup_state`` for each named rollup — the new
   ``last_completed_day`` + run status + error (NULL on success).
4. **Only if every named rollup succeeded**: prune raw
   ``session_telemetry`` rows older than 30 days, in 50k-row batches.
   Failure mode 5 (ADR-007 D6): never prune what you couldn't roll up.
5. Update metrics (ADR-007 D6): the staleness gauge, the duration
   histogram, the days-processed gauge, the prune counter, and the
   error counter.

Cardinality / metric posture:

* ``rollup_name`` is a bounded {user_daily, provider_daily} enum — extend
  it consciously when a new rollup lands.
* ``phase`` on the error counter is {rollup, prune, marker, sanity_check}.
* No user_id, channel_id, or session_id are ever emitted as labels (SRE
  veto, ADR-007 D6).

Time discipline:

* Tests stub the clock by setting ``task.now_utc`` to a fixed
  ``datetime`` before invoking ``execute()``. Production uses
  ``datetime.utcnow()`` at run time.

The rollup helpers ``_rollup_user_daily`` and ``_rollup_provider_daily``
live at module scope (not as instance methods) so tests can patch them
without subclassing — the failure-path tests patch them with a raising
side effect to exercise the prune-skipped-on-rollup-failure guard.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import text

from database import get_session
from observability import get_metric
from task_registry import register_task
from task_scheduler import ScheduleConfig, ScheduleType, TaskResult, TaskScheduler


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

ROLLUP_NAME_USER_DAILY = "user_daily"
ROLLUP_NAME_PROVIDER_DAILY = "provider_daily"

# ADR-007 D1: raw rows pruned at 30 days of age.
DEFAULT_RAW_RETENTION_DAYS = 30

# ADR-007 D3 batch size: prune DELETEs split into 50k-row chunks to avoid a
# single long write-lock event.
PRUNE_BATCH_SIZE = 50_000

# Catch-up safety cap: a stat-v2 deploy where the job hasn't run for many
# days will catch up on the next invocation, but bound the work per single
# run to keep the night within budget. Higher than 30 (the prune horizon)
# so the catch-up budget is never tighter than the retention window — but
# capped so a misconfigured clock doesn't try to roll up centuries.
MAX_DAYS_PER_RUN = 60


# ---------------------------------------------------------------------------
# Module-level helpers (patchable from tests)
# ---------------------------------------------------------------------------

def _rollup_user_daily(session, day: date) -> int:
    """Aggregate ``session_telemetry`` into ``session_telemetry_user_daily``
    for the given UTC day. Returns the number of rollup rows written.

    Excludes raw NULL ``user_id`` rows — no behavioral subject to attribute.
    Uses the DISTINCT-(channel_id, observed_at) collapse to avoid inflating
    watch_seconds by per-poll-per-client multiplicity (same pattern
    ``channel_watch_stats_v`` uses, skqln.3 step (b)).

    Idempotent via INSERT OR REPLACE: re-running on the same day updates
    in place rather than creating duplicates.
    """
    day_start_ms = int(
        datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc).timestamp() * 1000
    )
    day_end_ms = int(
        datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
        .timestamp() * 1000
    ) + 24 * 3600 * 1000

    # Two-step CTE-style query: first DISTINCT-fy per (channel, poll) so
    # client-count multiplicity collapses; then GROUP BY (user, channel) to
    # sum watch_seconds and count distinct sessions.
    sql = text(
        """
        INSERT OR REPLACE INTO session_telemetry_user_daily
            (user_id, channel_id, day, watch_seconds, session_count)
        SELECT
            user_id,
            channel_id,
            :day AS day,
            CAST(SUM(poll_interval_ms) / 1000 AS INTEGER) AS watch_seconds,
            COUNT(DISTINCT session_id) AS session_count
        FROM (
            -- DISTINCT-fy by (user, channel, observed_at) so concurrent
            -- clients in one poll contribute one poll-interval, not N.
            SELECT
                user_id,
                channel_id,
                observed_at,
                MAX(poll_interval_ms) AS poll_interval_ms,
                MIN(session_id) AS session_id
            FROM session_telemetry
            WHERE user_id IS NOT NULL
              AND observed_at >= :day_start_ms
              AND observed_at <  :day_end_ms
            GROUP BY user_id, channel_id, observed_at
        ) AS per_poll
        GROUP BY user_id, channel_id
        """
    )
    result = session.execute(sql, {
        "day": day.isoformat(),
        "day_start_ms": day_start_ms,
        "day_end_ms": day_end_ms,
    })
    return result.rowcount or 0


def _rollup_provider_daily(session, day: date) -> int:
    """Aggregate ``session_telemetry`` into
    ``session_telemetry_provider_daily`` for the given UTC day. Returns the
    number of rollup rows written.

    NULL ``provider_id`` rows are coalesced to the literal string
    ``'unknown'`` per ADR-007 §line 109 — never silently dropped. The
    rollup PK includes provider_id as TEXT NOT NULL, so the CAST has to
    happen at write time.

    Uses the same DISTINCT-(channel_id, observed_at) collapse the user
    rollup uses, for ``watch_seconds``. ``bytes_delta_sum``,
    ``buffer_event_count``, ``reconnect_event_count``, ``error_event_count``,
    and ``switch_event_count`` are summed across ALL raw rows (not collapsed)
    — those are per-poll-per-client measurements, not per-channel
    state, so the multiplicity is the truth.

    bd-d0ha9: extended to SUM the three per-type channel-event counters
    added by migration 0013 (bd-ov5vb) into the three new rollup columns
    added by migration 0014 (bd-d0ha9).
    """
    day_start_ms = int(
        datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc).timestamp() * 1000
    )
    day_end_ms = day_start_ms + 24 * 3600 * 1000

    # Two scans, UNION-coalesced:
    #   * watch_seconds — DISTINCT-(provider_bucket, channel, observed_at)
    #     collapse, then SUM(poll_interval).
    #   * bytes_delta_sum + all four event counters — SUM over all raw rows.
    #     Per-poll-per-client samples — no DISTINCT collapse needed (the
    #     multiplicity is the truth for these columns).
    # Done as a single CTE-style query so the upsert is one statement.
    # bd-d0ha9: extended projection to include reconnect/error/switch event
    # counts alongside the pre-existing buffer_event_count.
    sql = text(
        """
        INSERT OR REPLACE INTO session_telemetry_provider_daily
            (provider_id, channel_id, day, watch_seconds,
             bytes_delta_sum, buffer_event_count,
             reconnect_event_count, error_event_count, switch_event_count)
        SELECT
            ws.provider_id,
            ws.channel_id,
            :day AS day,
            ws.watch_seconds,
            COALESCE(bb.bytes_delta_sum, 0)         AS bytes_delta_sum,
            COALESCE(bb.buffer_event_count, 0)      AS buffer_event_count,
            COALESCE(bb.reconnect_event_count, 0)   AS reconnect_event_count,
            COALESCE(bb.error_event_count, 0)       AS error_event_count,
            COALESCE(bb.switch_event_count, 0)      AS switch_event_count
        FROM (
            -- DISTINCT-collapsed watch_seconds per (provider_bucket, channel).
            SELECT
                provider_bucket AS provider_id,
                channel_id,
                CAST(SUM(poll_interval_ms) / 1000 AS INTEGER) AS watch_seconds
            FROM (
                SELECT
                    COALESCE(CAST(provider_id AS TEXT), 'unknown') AS provider_bucket,
                    channel_id,
                    observed_at,
                    MAX(poll_interval_ms) AS poll_interval_ms
                FROM session_telemetry
                WHERE observed_at >= :day_start_ms
                  AND observed_at <  :day_end_ms
                GROUP BY provider_bucket, channel_id, observed_at
            ) AS per_poll
            GROUP BY provider_bucket, channel_id
        ) AS ws
        LEFT JOIN (
            -- Multi-client-aware sums: bytes_delta and all four event
            -- counters are per-poll-per-client samples, so they DON'T
            -- get the DISTINCT collapse.
            SELECT
                COALESCE(CAST(provider_id AS TEXT), 'unknown') AS provider_bucket,
                channel_id,
                SUM(bytes_delta)             AS bytes_delta_sum,
                SUM(buffer_event_count)      AS buffer_event_count,
                SUM(reconnect_event_count)   AS reconnect_event_count,
                SUM(error_event_count)       AS error_event_count,
                SUM(switch_event_count)      AS switch_event_count
            FROM session_telemetry
            WHERE observed_at >= :day_start_ms
              AND observed_at <  :day_end_ms
            GROUP BY provider_bucket, channel_id
        ) AS bb
          ON bb.provider_bucket = ws.provider_id
         AND bb.channel_id      = ws.channel_id
        """
    )
    result = session.execute(sql, {
        "day": day.isoformat(),
        "day_start_ms": day_start_ms,
        "day_end_ms": day_end_ms,
    })
    return result.rowcount or 0


def _read_marker(session, rollup_name: str) -> Optional[date]:
    """Return the ``last_completed_day`` for the named rollup, or None."""
    row = session.execute(text(
        "SELECT last_completed_day FROM telemetry_rollup_state "
        "WHERE rollup_name = :n"
    ), {"n": rollup_name}).fetchone()
    if row is None or row[0] is None:
        return None
    # SQLite returns the DATE as a string in ISO format.
    if isinstance(row[0], str):
        return date.fromisoformat(row[0])
    return row[0]


def _write_marker(
    session,
    rollup_name: str,
    last_completed_day: Optional[date],
    last_run_at_ms: int,
    last_run_status: str,
    last_run_error: Optional[str],
) -> None:
    """Upsert the rollup marker row."""
    session.execute(text(
        "INSERT OR REPLACE INTO telemetry_rollup_state "
        "  (rollup_name, last_completed_day, last_run_at_ms, "
        "   last_run_status, last_run_error) "
        "VALUES (:n, :day, :ms, :status, :err)"
    ), {
        "n": rollup_name,
        "day": last_completed_day.isoformat() if last_completed_day else None,
        "ms": last_run_at_ms,
        "status": last_run_status,
        "err": last_run_error,
    })


def _prune_raw_rows(session, cutoff_ms: int, batch_size: int) -> int:
    """Delete raw session_telemetry rows older than cutoff_ms in batches.

    SQLite has no parallel DELETE; one big DELETE holds the write lock
    for the duration. Batching keeps each lock short so writer traffic
    (BandwidthTracker polling) can interleave.
    """
    total = 0
    while True:
        # SQLite's DELETE doesn't accept LIMIT directly — wrap with a
        # subquery against the synthetic id PK.
        result = session.execute(text(
            "DELETE FROM session_telemetry "
            "WHERE id IN ("
            "  SELECT id FROM session_telemetry "
            "  WHERE observed_at < :cutoff "
            "  LIMIT :batch"
            ")"
        ), {"cutoff": cutoff_ms, "batch": batch_size})
        deleted = result.rowcount or 0
        total += deleted
        session.commit()
        if deleted < batch_size:
            break
    return total


# ---------------------------------------------------------------------------
# Task class
# ---------------------------------------------------------------------------

@register_task
class StatsV2RollupTask(TaskScheduler):
    """Nightly rollup of session_telemetry + prune of aged raw rows.

    Default schedule is daily at 03:30 UTC — ADR-007 D3's "low-traffic
    window". The container's TZ env var is irrelevant; the task itself
    works exclusively in UTC for day-boundary correctness across
    daylight-savings transitions.

    Configuration (mutable via the standard task config update API):

    * ``raw_retention_days`` — D1 window. Defaults to 30; ADR-007's exit
      path step 1 allows raising it to 45/60 without a schema change.
    * ``max_days_per_run`` — catch-up safety cap. A normal run processes
      exactly one day (yesterday); after extended downtime the cap
      bounds the work per single invocation.
    * ``prune_batch_size`` — D3 step-3 batch size (default 50k).
    """

    task_id = "stats_v2_rollup"
    task_name = "Stats v2 Rollup & Prune"
    task_description = (
        "Aggregate yesterday's session_telemetry into the per-user and "
        "per-provider daily rollup tables, then prune raw rows older "
        "than the retention window (ADR-007)."
    )

    def __init__(self, schedule_config: Optional[ScheduleConfig] = None):
        if schedule_config is None:
            schedule_config = ScheduleConfig(
                schedule_type=ScheduleType.CRON,
                cron_expression="30 3 * * *",  # 03:30 UTC daily
                timezone="UTC",
            )
        super().__init__(schedule_config)

        # Defaults; override-able via update_config.
        self.raw_retention_days: int = DEFAULT_RAW_RETENTION_DAYS
        self.max_days_per_run: int = MAX_DAYS_PER_RUN
        self.prune_batch_size: int = PRUNE_BATCH_SIZE

        # ``now_utc`` is for test-time clock stubbing; production callers
        # leave it None and the task reads datetime.utcnow() at execute()
        # entry.
        self.now_utc: Optional[datetime] = None

    def get_config(self) -> dict:
        return {
            "raw_retention_days": self.raw_retention_days,
            "max_days_per_run": self.max_days_per_run,
            "prune_batch_size": self.prune_batch_size,
        }

    def update_config(self, config: dict) -> None:
        if "raw_retention_days" in config:
            self.raw_retention_days = int(config["raw_retention_days"])
        if "max_days_per_run" in config:
            self.max_days_per_run = int(config["max_days_per_run"])
        if "prune_batch_size" in config:
            self.prune_batch_size = int(config["prune_batch_size"])

    async def execute(self) -> TaskResult:
        """Run one rollup-then-prune cycle. Idempotent + catch-up capable."""
        started_at = datetime.utcnow()
        run_start = time.monotonic()

        now_utc = self.now_utc or datetime.utcnow().replace(tzinfo=timezone.utc)
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=timezone.utc)
        now_ms = int(now_utc.timestamp() * 1000)
        today_utc = now_utc.date()

        rollup_outcomes: dict[str, dict] = {
            ROLLUP_NAME_USER_DAILY: {"status": "pending", "days": 0, "error": None},
            ROLLUP_NAME_PROVIDER_DAILY: {"status": "pending", "days": 0, "error": None},
        }
        rolled_up_through: dict[str, Optional[date]] = {
            ROLLUP_NAME_USER_DAILY: None,
            ROLLUP_NAME_PROVIDER_DAILY: None,
        }

        # ---------------------------------------------------------------
        # PASS 1 — for each named rollup, find the gap to today and roll
        # up the days inside the catch-up budget.
        # ---------------------------------------------------------------
        for rollup_name, helper in (
            (ROLLUP_NAME_USER_DAILY, _rollup_user_daily),
            (ROLLUP_NAME_PROVIDER_DAILY, _rollup_provider_daily),
        ):
            phase_start = time.monotonic()
            session = get_session()
            try:
                last_done = _read_marker(session, rollup_name)
                # The first day to process is one after the marker (or, on
                # first-run, the oldest day that still has raw rows).
                start_day = (
                    last_done + timedelta(days=1)
                    if last_done is not None
                    else self._first_day_with_raw_rows(session)
                )
                if start_day is None:
                    # No raw rows yet → nothing to roll up.
                    _write_marker(
                        session,
                        rollup_name,
                        last_completed_day=None,
                        last_run_at_ms=now_ms,
                        last_run_status="success",
                        last_run_error=None,
                    )
                    session.commit()
                    rollup_outcomes[rollup_name]["status"] = "success"
                    self._observe_duration(
                        rollup_name, time.monotonic() - phase_start
                    )
                    continue

                days_to_process = self._days_between(
                    start_day, today_utc, cap=self.max_days_per_run
                )
                if not days_to_process:
                    # Already caught up.
                    _write_marker(
                        session,
                        rollup_name,
                        last_completed_day=last_done,
                        last_run_at_ms=now_ms,
                        last_run_status="success",
                        last_run_error=None,
                    )
                    session.commit()
                    rollup_outcomes[rollup_name]["status"] = "success"
                    rolled_up_through[rollup_name] = last_done
                    self._observe_duration(
                        rollup_name, time.monotonic() - phase_start
                    )
                    continue

                # Roll up each pending day.
                processed = 0
                for day in days_to_process:
                    helper(session, day)
                    session.commit()
                    processed += 1

                latest = days_to_process[-1]
                _write_marker(
                    session,
                    rollup_name,
                    last_completed_day=latest,
                    last_run_at_ms=now_ms,
                    last_run_status="success",
                    last_run_error=None,
                )
                session.commit()
                rollup_outcomes[rollup_name]["status"] = "success"
                rollup_outcomes[rollup_name]["days"] = processed
                rolled_up_through[rollup_name] = latest
                self._observe_duration(
                    rollup_name, time.monotonic() - phase_start
                )
            except Exception as exc:
                logger.exception(
                    "[%s] rollup %s failed: %s",
                    self.task_id, rollup_name, exc,
                )
                rollup_outcomes[rollup_name]["status"] = "failure"
                rollup_outcomes[rollup_name]["error"] = str(exc)
                # Record the failure marker on a fresh session so the
                # broken transaction doesn't get rolled back implicitly.
                session.rollback()
                try:
                    last_done = _read_marker(session, rollup_name)
                except Exception:
                    last_done = None
                try:
                    _write_marker(
                        session,
                        rollup_name,
                        last_completed_day=last_done,
                        last_run_at_ms=now_ms,
                        last_run_status="failure",
                        last_run_error=str(exc),
                    )
                    session.commit()
                except Exception:
                    logger.exception(
                        "[%s] also failed to persist failure marker for %s",
                        self.task_id, rollup_name,
                    )
                    session.rollback()
                self._increment_error("rollup")
                self._observe_duration(
                    rollup_name, time.monotonic() - phase_start
                )
            finally:
                session.close()

        # ---------------------------------------------------------------
        # PASS 2 — prune only if EVERY named rollup succeeded.
        # ---------------------------------------------------------------
        every_rollup_ok = all(
            rollup_outcomes[name]["status"] == "success"
            for name in (ROLLUP_NAME_USER_DAILY, ROLLUP_NAME_PROVIDER_DAILY)
        )
        rows_pruned = 0
        if every_rollup_ok:
            cutoff_dt = now_utc - timedelta(days=self.raw_retention_days)
            cutoff_ms = int(cutoff_dt.timestamp() * 1000)
            session = get_session()
            try:
                rows_pruned = _prune_raw_rows(
                    session, cutoff_ms, self.prune_batch_size
                )
                # Update the existing ecm_session_telemetry_row_count
                # gauge with the post-prune total table size — that's the
                # signal SRE's storage-growth alert needs (the writer's
                # per-batch update is for write-health, not size).
                total_after = session.execute(text(
                    "SELECT COUNT(*) FROM session_telemetry"
                )).scalar() or 0
                try:
                    get_metric("session_telemetry_row_count").set(int(total_after))
                except Exception:
                    logger.debug(
                        "[%s] failed to update session_telemetry_row_count gauge",
                        self.task_id,
                        exc_info=True,
                    )
            except Exception as exc:
                logger.exception(
                    "[%s] prune step failed: %s", self.task_id, exc,
                )
                self._increment_error("prune")
                session.rollback()
            finally:
                session.close()

            # Counter increment for what we successfully pruned.
            try:
                get_metric("telemetry_raw_rows_pruned").inc(rows_pruned)
            except Exception:
                logger.debug(
                    "[%s] failed to increment telemetry_raw_rows_pruned",
                    self.task_id,
                    exc_info=True,
                )

        # ---------------------------------------------------------------
        # PASS 3 — finalize metrics + TaskResult.
        # ---------------------------------------------------------------
        for rollup_name in (ROLLUP_NAME_USER_DAILY, ROLLUP_NAME_PROVIDER_DAILY):
            try:
                get_metric("telemetry_rollup_days_processed").labels(
                    rollup_name=rollup_name
                ).set(rollup_outcomes[rollup_name]["days"])
            except Exception:
                logger.debug(
                    "[%s] failed to emit days_processed for %s",
                    self.task_id, rollup_name,
                    exc_info=True,
                )

            if rollup_outcomes[rollup_name]["status"] == "success":
                try:
                    get_metric(
                        "telemetry_rollup_last_success_timestamp"
                    ).labels(rollup_name=rollup_name).set(now_utc.timestamp())
                except Exception:
                    logger.debug(
                        "[%s] failed to update last_success_timestamp for %s",
                        self.task_id, rollup_name,
                        exc_info=True,
                    )

        completed_at = datetime.utcnow()
        duration = time.monotonic() - run_start

        if every_rollup_ok:
            return TaskResult(
                success=True,
                message=(
                    f"Rolled up "
                    f"user_daily={rollup_outcomes[ROLLUP_NAME_USER_DAILY]['days']} "
                    f"provider_daily="
                    f"{rollup_outcomes[ROLLUP_NAME_PROVIDER_DAILY]['days']} "
                    f"days; pruned {rows_pruned} raw rows."
                ),
                started_at=started_at,
                completed_at=completed_at,
                total_items=2,
                success_count=2,
                details={
                    "rollup_outcomes": rollup_outcomes,
                    "rows_pruned": rows_pruned,
                    "duration_seconds": duration,
                },
            )

        # At least one rollup failed → fail-safe: skip prune, surface error.
        failed_names = [
            n for n in rollup_outcomes
            if rollup_outcomes[n]["status"] != "success"
        ]
        first_error = next(
            (rollup_outcomes[n]["error"] for n in failed_names
             if rollup_outcomes[n]["error"]),
            "unknown",
        )
        return TaskResult(
            success=False,
            message=(
                f"Rollup failed for {failed_names}; prune skipped to avoid "
                f"data loss. First error: {first_error}"
            ),
            error=first_error,
            started_at=started_at,
            completed_at=completed_at,
            total_items=2,
            success_count=2 - len(failed_names),
            failed_count=len(failed_names),
            details={
                "rollup_outcomes": rollup_outcomes,
                "rows_pruned": 0,
                "duration_seconds": duration,
            },
        )

    # ---------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------

    def _first_day_with_raw_rows(self, session) -> Optional[date]:
        """Return the earliest UTC day represented in session_telemetry.

        Used on first-run only (the marker is NULL). Returns None when
        the raw table is empty — the task then writes a NULL-day success
        marker and exits cleanly.
        """
        row = session.execute(text(
            "SELECT MIN(observed_at) FROM session_telemetry"
        )).fetchone()
        if row is None or row[0] is None:
            return None
        first_ms = int(row[0])
        first_dt = datetime.fromtimestamp(first_ms / 1000, tz=timezone.utc)
        return first_dt.date()

    @staticmethod
    def _days_between(start: date, end_exclusive: date, cap: int) -> list[date]:
        """Inclusive range [start, end_exclusive) as a list of UTC dates.

        Capped at ``cap`` entries; if the gap is wider than the cap, the
        earliest ``cap`` days are returned. The next run picks up the
        remainder (each run advances the marker by however many days it
        processed).
        """
        days: list[date] = []
        current = start
        while current < end_exclusive and len(days) < cap:
            days.append(current)
            current = current + timedelta(days=1)
        return days

    @staticmethod
    def _observe_duration(rollup_name: str, seconds: float) -> None:
        try:
            get_metric("telemetry_rollup_duration_seconds").labels(
                rollup_name=rollup_name
            ).observe(max(0.0, float(seconds)))
        except Exception:
            logger.debug(
                "[stats_v2_rollup] failed to observe duration for %s",
                rollup_name,
                exc_info=True,
            )

    @staticmethod
    def _increment_error(phase: str) -> None:
        try:
            get_metric("telemetry_rollup_errors_total").labels(
                phase=phase
            ).inc()
        except Exception:
            logger.debug(
                "[stats_v2_rollup] failed to increment errors_total %s",
                phase,
                exc_info=True,
            )
