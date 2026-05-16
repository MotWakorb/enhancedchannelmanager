"""Unit tests for the task-scheduler-health gauges (bd-qxi02).

Two gauges + two helpers live in ``backend/observability.py``:

- ``ecm_task_schedule_last_success_timestamp{task_id}`` — stamped by
  ``record_task_success(task_id)``.
- ``ecm_task_schedule_next_run_null_count`` — set by
  ``update_task_schedule_null_count(count=...)``.

These tests cover the SRE-recommended surface from the bd-p5b8i spike:

- Gauges exist after ``install_metrics``.
- ``record_task_success`` stamps the right ``task_id`` label and pins
  the value to the override timestamp.
- ``update_task_schedule_null_count`` accepts an explicit count and
  reflects it on the gauge (no DB needed for that path).
- The label cardinality contract: ``task_id`` is the only label on
  the per-task gauge, and the count gauge is label-free.
- Defensive: helpers do not raise on bad input / missing registry.
"""
import re

import pytest

import observability


@pytest.fixture(autouse=True)
def _reset_observability_state():
    """Wipe registry between tests so each test starts from a clean slate."""
    observability.reset_for_tests()
    yield
    observability.reset_for_tests()


class TestGaugesAreInstalled:
    def test_install_metrics_creates_task_scheduler_gauges(self):
        metrics = observability.install_metrics()
        assert "task_schedule_last_success_timestamp" in metrics
        assert "task_schedule_next_run_null_count" in metrics

    def test_render_metrics_exposes_task_scheduler_help_text(self):
        observability.install_metrics()
        # Stamp at least one label so the per-task gauge actually
        # appears in the exposition (Prometheus omits unlabeled series).
        observability.record_task_success("stats_v2_rollup", timestamp=1_700_000_000.0)
        # Also pin the null-count gauge so it shows up.
        observability.update_task_schedule_null_count(count=0)

        body = observability.render_metrics().decode("utf-8")

        assert "# HELP ecm_task_schedule_last_success_timestamp" in body
        assert "# TYPE ecm_task_schedule_last_success_timestamp gauge" in body
        assert "# HELP ecm_task_schedule_next_run_null_count" in body
        assert "# TYPE ecm_task_schedule_next_run_null_count gauge" in body


class TestRecordTaskSuccess:
    def test_stamps_the_labeled_gauge_with_provided_timestamp(self):
        observability.install_metrics()
        observability.record_task_success("stats_v2_rollup", timestamp=1_700_000_123.0)

        gauge = observability.get_metric("task_schedule_last_success_timestamp")
        sample = gauge.labels(task_id="stats_v2_rollup")
        assert sample._value.get() == 1_700_000_123.0

    def test_uses_current_time_when_timestamp_omitted(self, monkeypatch):
        observability.install_metrics()
        # Pin time.time at a known value so the assertion is deterministic.
        monkeypatch.setattr(observability.time, "time", lambda: 1_800_000_000.5)

        observability.record_task_success("cleanup")

        gauge = observability.get_metric("task_schedule_last_success_timestamp")
        sample = gauge.labels(task_id="cleanup")
        assert sample._value.get() == 1_800_000_000.5

    def test_records_independent_values_per_task_id(self):
        observability.install_metrics()
        observability.record_task_success("stats_v2_rollup", timestamp=1.0)
        observability.record_task_success("cleanup", timestamp=2.0)
        observability.record_task_success("stream_probe", timestamp=3.0)

        gauge = observability.get_metric("task_schedule_last_success_timestamp")
        assert gauge.labels(task_id="stats_v2_rollup")._value.get() == 1.0
        assert gauge.labels(task_id="cleanup")._value.get() == 2.0
        assert gauge.labels(task_id="stream_probe")._value.get() == 3.0

    def test_repeated_calls_overwrite_previous_value(self):
        """The gauge represents the LATEST success timestamp, not a counter."""
        observability.install_metrics()
        observability.record_task_success("stats_v2_rollup", timestamp=100.0)
        observability.record_task_success("stats_v2_rollup", timestamp=200.0)
        observability.record_task_success("stats_v2_rollup", timestamp=150.0)

        gauge = observability.get_metric("task_schedule_last_success_timestamp")
        sample = gauge.labels(task_id="stats_v2_rollup")
        # Last write wins — even if it's not monotonically increasing.
        assert sample._value.get() == 150.0

    def test_never_raises_when_metrics_unavailable(self):
        """Observability instrumentation must not break the task engine."""
        # Don't call install_metrics; record_task_success should still
        # work (it auto-installs) — but more importantly, even if the
        # helper internals were to fail, the function must not raise.
        observability.reset_for_tests()
        # No assertion needed beyond "this doesn't raise" — the test
        # name is the contract.
        observability.record_task_success("stats_v2_rollup", timestamp=1.0)


class TestUpdateTaskScheduleNullCount:
    def test_explicit_count_sets_gauge_value(self):
        observability.install_metrics()
        observability.update_task_schedule_null_count(count=7)

        gauge = observability.get_metric("task_schedule_next_run_null_count")
        assert gauge._value.get() == 7.0

    def test_count_of_zero_sets_gauge_to_zero(self):
        """Healthy state is count=0; the gauge MUST reflect that explicitly."""
        observability.install_metrics()
        # First simulate a broken scheduler, then a heal.
        observability.update_task_schedule_null_count(count=15)
        gauge = observability.get_metric("task_schedule_next_run_null_count")
        assert gauge._value.get() == 15.0

        observability.update_task_schedule_null_count(count=0)
        assert gauge._value.get() == 0.0

    def test_never_raises_on_bad_db_query(self, monkeypatch):
        """When count is None and the DB query fails, the helper logs and returns."""
        observability.install_metrics()

        # Force the implicit DB-query path by passing count=None, then
        # make the import explode. The helper must swallow this.
        def _fake_import(*args, **kwargs):
            raise ImportError("database module unavailable in this test")

        # We can't easily monkeypatch the local import without injecting
        # into builtins; instead patch get_session to raise. The helper
        # catches all exceptions, so either path proves the contract.
        import database
        monkeypatch.setattr(database, "get_session", lambda: (_ for _ in ()).throw(
            RuntimeError("DB unreachable")
        ))

        # The gauge starts at 0 (Prometheus default for Gauge).
        gauge = observability.get_metric("task_schedule_next_run_null_count")
        gauge.set(99.0)  # baseline so we can check it WASN'T touched

        observability.update_task_schedule_null_count()  # count=None

        # Gauge value untouched — helper failed gracefully.
        assert gauge._value.get() == 99.0


class TestCardinalityContract:
    """Lock the bounded label set per SRE pre-clearance."""

    def test_task_id_is_the_only_label_on_per_task_gauge(self):
        """``task_id`` is bounded by the task_registry — code constants only."""
        observability.install_metrics()
        gauge = observability.get_metric("task_schedule_last_success_timestamp")
        # Inspect the label names directly from the Prometheus
        # collector. ``_labelnames`` is a public-API tuple on the
        # Gauge family.
        assert gauge._labelnames == ("task_id",)

    def test_null_count_gauge_has_no_labels(self):
        """Single-process scheduler — there is exactly one count."""
        observability.install_metrics()
        gauge = observability.get_metric("task_schedule_next_run_null_count")
        assert gauge._labelnames == ()


class TestRenderedOutput:
    def test_per_task_label_appears_in_exposition(self):
        observability.install_metrics()
        observability.record_task_success("stats_v2_rollup", timestamp=1_700_000_000.0)
        observability.record_task_success("cleanup", timestamp=1_700_000_500.0)

        body = observability.render_metrics().decode("utf-8")
        # The Prometheus text format puts label values in double quotes.
        # Numeric values may render in scientific notation (e.g. 1.7e+09)
        # for large floats — match the labeled prefix and ANY numeric
        # value so this test is robust to client_python's formatter.
        assert re.search(
            r'ecm_task_schedule_last_success_timestamp\{task_id="stats_v2_rollup"\} [0-9.e+-]+',
            body,
        ), body
        assert re.search(
            r'ecm_task_schedule_last_success_timestamp\{task_id="cleanup"\} [0-9.e+-]+',
            body,
        ), body

    def test_null_count_appears_in_exposition(self):
        observability.install_metrics()
        observability.update_task_schedule_null_count(count=3)
        body = observability.render_metrics().decode("utf-8")
        assert re.search(
            r"ecm_task_schedule_next_run_null_count 3\.0", body
        ), body
