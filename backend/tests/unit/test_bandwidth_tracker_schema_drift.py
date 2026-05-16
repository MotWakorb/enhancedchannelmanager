"""Tests for runtime schema-drift detection in ``_write_session_telemetry``
(bd-8axhi defense-in-depth).

Disease background — the hot-deploy hazard the bd-zaaey loud-fail at
``init_db`` cannot cover:

  1. A developer ``docker cp``'s a new alembic migration AND new writer
     code that references the new column into a running container.
  2. The boot-time ``_assert_schema_matches_models`` already passed on
     the previous restart (when the model was older).
  3. The new writer code now executes against the still-unmigrated DB
     schema. Each write raises ``OperationalError: no such column``,
     swallowed by the existing try/except as WARN.
  4. Telemetry silently drops rows until someone restarts the container
     (which runs ``alembic upgrade head`` from ``init_db``).

bd-8axhi adds a runtime drift detector on top of the existing
defensive try/except. The first such error escalates to ERROR-level
with an actionable recovery path; subsequent errors fall back to WARN
so the operator log is not flooded while the operator repairs the
container.
"""
from __future__ import annotations

import logging

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

from bandwidth_tracker import (
    BandwidthTracker,
    _is_schema_drift_error,
)


# ─── _is_schema_drift_error unit tests ──────────────────────────────────


class TestIsSchemaDriftError:
    def test_matches_no_such_column(self):
        exc = OperationalError("SELECT", {}, Exception("no such column: stream_id"))
        assert _is_schema_drift_error(exc) is True

    def test_matches_no_such_table(self):
        exc = OperationalError("SELECT", {}, Exception("no such table: session_telemetry"))
        assert _is_schema_drift_error(exc) is True

    def test_case_insensitive(self):
        exc = OperationalError("SELECT", {}, Exception("NO SUCH COLUMN: stream_id"))
        assert _is_schema_drift_error(exc) is True

    def test_does_not_match_unrelated_operational_error(self):
        exc = OperationalError("SELECT", {}, Exception("database is locked"))
        assert _is_schema_drift_error(exc) is False

    def test_walks_exception_chain(self):
        # Simulate the SQLAlchemy wrapping pattern: a higher-level error
        # whose __cause__ carries the sqlite3 schema-drift message.
        inner = Exception("no such column: stream_id")
        wrapped = RuntimeError("commit failed")
        wrapped.__cause__ = inner
        assert _is_schema_drift_error(wrapped) is True

    def test_does_not_loop_on_self_referential_chain(self):
        # Defensive — a malformed exception chain that points back to
        # itself must not infinite-loop the matcher.
        exc = RuntimeError("benign")
        exc.__cause__ = exc  # self-referential
        assert _is_schema_drift_error(exc) is False


# ─── _write_session_telemetry escalation behavior ───────────────────────


def _make_tracker():
    """Build a BandwidthTracker with no real client — only the alarm
    flag and the helper's exception-handling path are exercised here."""
    return BandwidthTracker(client=object(), poll_interval=10)


def _trigger_write_with_session_factory_raising(tracker, exc, caplog):
    """Drive ``_write_session_telemetry`` so the supplied ``exc`` is the
    failure observed inside the try block.

    Simplest sabotage point: monkeypatch ``database.get_session`` so the
    very first call raises. The helper's try/except catches at the top
    of its logic, exercising the same except clause we want to verify.
    """
    import bandwidth_tracker as bt

    def _raise(*_a, **_kw):
        raise exc

    original = bt.get_session
    bt.get_session = _raise
    try:
        tracker._write_session_telemetry(
            channel_snapshot=[
                {
                    "channel_uuid": "test-channel-uuid",
                    "client_ips": ["127.0.0.1"],
                    "client_user_map": {},
                    "channel_bytes_delta": 1024,
                }
            ],
            observed_at_ms=1_700_000_000_000,
        )
    finally:
        bt.get_session = original


class TestSchemaDriftEscalation:
    def test_first_drift_error_logs_at_error_level(self, caplog):
        tracker = _make_tracker()
        assert tracker._schema_drift_alarm_armed is True

        exc = OperationalError("INSERT", {}, Exception("no such column: stream_id"))
        with caplog.at_level(logging.DEBUG, logger="bandwidth_tracker"):
            _trigger_write_with_session_factory_raising(tracker, exc, caplog)

        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert error_records, "expected an ERROR record on first schema drift"
        msg = error_records[0].message
        assert "SCHEMA DRIFT" in msg
        assert "docker restart ecm-ecm-1" in msg
        assert "bd-zaaey" in msg
        # Alarm is now disarmed so subsequent failures don't spam ERROR.
        assert tracker._schema_drift_alarm_armed is False

    def test_second_drift_error_falls_back_to_warn(self, caplog):
        tracker = _make_tracker()
        # Pre-disarm to skip straight to the suppressed-second-occurrence
        # path. Equivalent to "we already raised the alarm earlier; the
        # operator is on it".
        tracker._schema_drift_alarm_armed = False

        exc = OperationalError("INSERT", {}, Exception("no such column: stream_id"))
        with caplog.at_level(logging.DEBUG, logger="bandwidth_tracker"):
            _trigger_write_with_session_factory_raising(tracker, exc, caplog)

        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        warn_records = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING and "session_telemetry write failed" in r.message
        ]
        assert not error_records, "second drift event must NOT log at ERROR"
        assert warn_records, "second drift event must still log at WARN"

    def test_non_drift_error_stays_at_warn_and_does_not_disarm(self, caplog):
        tracker = _make_tracker()
        assert tracker._schema_drift_alarm_armed is True

        # A non-drift OperationalError (e.g., database is locked) — must
        # NOT escalate to ERROR and must NOT consume the one-shot alarm.
        exc = OperationalError("INSERT", {}, Exception("database is locked"))
        with caplog.at_level(logging.DEBUG, logger="bandwidth_tracker"):
            _trigger_write_with_session_factory_raising(tracker, exc, caplog)

        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        warn_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert not error_records
        assert warn_records
        # Alarm preserved for the real schema-drift event.
        assert tracker._schema_drift_alarm_armed is True

    def test_unrelated_runtime_error_is_warn_not_error(self, caplog):
        # A plain RuntimeError (e.g. helper internal sabotage — see
        # test_helper_internal_failure_is_swallowed in
        # test_bandwidth_tracker_session_telemetry.py) is the existing
        # bd-skqln.12 WARN path. The bd-8axhi changes must not regress
        # this — only schema-drift OperationalErrors escalate.
        tracker = _make_tracker()
        exc = RuntimeError("helper internal sabotage")
        with caplog.at_level(logging.DEBUG, logger="bandwidth_tracker"):
            _trigger_write_with_session_factory_raising(tracker, exc, caplog)

        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert not error_records
        assert tracker._schema_drift_alarm_armed is True
