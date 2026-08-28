"""
Unit tests for ``backend/observability.py``.

Scope of these tests:

- JSON log formatting emits the required field set with a trace id attached
- Bound context leaks into log records and back out through the JSON payload
- Metrics install into their own registry and render as Prometheus text
- The registry is idempotent across ``install_metrics`` calls

Middleware-level tests (request correlation, per-endpoint counters, readiness
gauge) live in ``tests/routers/test_observability_middleware.py`` because they
require the FastAPI client fixture.
"""
import json
import logging
import re
import uuid
from pathlib import Path

import pytest

import observability


@pytest.fixture(autouse=True)
def _reset_observability_state():
    """Wipe observability globals between tests.

    The module keeps a single Prometheus registry in a module-level global
    so the process-wide metrics are deduplicated. Tests need a fresh
    registry per case, and we also want to clear any bound context that a
    failing test left in the contextvars.
    """
    observability.reset_for_tests()
    yield
    observability.reset_for_tests()


class TestTraceIdHelpers:
    def test_generate_trace_id_returns_uuidv4_string(self):
        tid = observability.generate_trace_id()
        # ``uuid.UUID`` accepts strings without dashes — use strict parse.
        parsed = uuid.UUID(tid)
        assert parsed.version == 4

    def test_trace_id_roundtrip(self):
        assert observability.get_trace_id() is None
        token = observability.set_trace_id("abc-123")
        try:
            assert observability.get_trace_id() == "abc-123"
        finally:
            observability.reset_trace_id(token)
        assert observability.get_trace_id() is None

    def test_bind_context_survives_until_reset(self):
        token = observability.bind_context(restore_id=42, phase="validate")
        try:
            ctx = observability._bound_context_var.get()
            assert ctx["restore_id"] == 42
            assert ctx["phase"] == "validate"
        finally:
            observability.reset_context(token)
        assert observability._bound_context_var.get() == {}


class TestJsonFormatter:
    def _emit_record(self, trace_id=None, bound=None, extra=None, level=logging.INFO):
        """Produce one JSON-formatted log line from a synthetic record."""
        observability.install_json_logging(level=level)
        logger = logging.getLogger("observability.test")
        records: list[str] = []

        class _Capture(logging.Handler):
            def emit(self, record):
                records.append(self.format(record))

        capture = _Capture()
        capture.setFormatter(observability.JsonFormatter())
        capture.addFilter(observability._TraceIdFilter())
        logger.addHandler(capture)
        logger.setLevel(level)

        tid_token = None
        ctx_token = None
        try:
            if trace_id is not None:
                tid_token = observability.set_trace_id(trace_id)
            if bound:
                ctx_token = observability.bind_context(**bound)
            logger.info("hello %s", "world", extra=extra or {})
        finally:
            if ctx_token is not None:
                observability.reset_context(ctx_token)
            if tid_token is not None:
                observability.reset_trace_id(tid_token)
            logger.removeHandler(capture)

        assert len(records) == 1
        return json.loads(records[0])

    def test_record_contains_required_fields(self):
        payload = self._emit_record(trace_id="t-1")
        for field in ("ts", "level", "logger", "msg", "trace_id"):
            assert field in payload, f"missing {field} in {payload}"
        assert payload["level"] == "INFO"
        assert payload["logger"] == "observability.test"
        assert payload["msg"] == "hello world"
        assert payload["trace_id"] == "t-1"

    def test_timestamp_is_iso_with_milliseconds(self):
        payload = self._emit_record(trace_id="t-2")
        # Shape: ``YYYY-MM-DDTHH:MM:SS.mmmZ`` (length fixed).
        assert re.match(
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$", payload["ts"]
        ), payload["ts"]

    def test_trace_id_defaults_to_hyphen_when_unset(self):
        payload = self._emit_record(trace_id=None)
        assert payload["trace_id"] == "-"

    def test_bound_context_flows_through_to_json(self):
        payload = self._emit_record(
            trace_id="t-3", bound={"restore_id": 99, "phase": "apply"}
        )
        assert payload["restore_id"] == 99
        assert payload["phase"] == "apply"


class TestInstallJsonLogging:
    """bd-cng0d: ``install_json_logging`` must leave exactly ONE console
    handler on the root logger.

    Regression: the plain ``logging.basicConfig`` StreamHandler was kept
    (merely re-formatted to JSON) *alongside* the newly added JSON handler,
    so every record was emitted twice — duplicate JSON access-log lines in
    docker logs.
    """

    @pytest.fixture(autouse=True)
    def _preserve_root_logger(self):
        """Detach and restore the real root handlers/filters/level."""
        root = logging.getLogger()
        saved_handlers = root.handlers[:]
        saved_filters = root.filters[:]
        saved_level = root.level
        for h in saved_handlers:
            root.removeHandler(h)
        for f in saved_filters:
            root.removeFilter(f)
        yield
        for h in root.handlers[:]:
            root.removeHandler(h)
        for f in root.filters[:]:
            root.removeFilter(f)
        for h in saved_handlers:
            root.addHandler(h)
        for f in saved_filters:
            root.addFilter(f)
        root.setLevel(saved_level)

    @staticmethod
    def _console_handlers() -> list[logging.Handler]:
        """Exactly-StreamHandler instances on root (the console emitters)."""
        return [
            h
            for h in logging.getLogger().handlers
            if type(h) is logging.StreamHandler
        ]

    def test_basicconfig_handler_is_replaced_not_kept(self):
        """After install, exactly one console handler remains — ours."""
        # Simulate main.py's logging.basicConfig(...) console handler.
        plain = logging.StreamHandler()  # defaults to sys.stderr like basicConfig
        plain.setFormatter(logging.Formatter("%(message)s"))
        logging.getLogger().addHandler(plain)

        observability.install_json_logging()

        console = self._console_handlers()
        assert len(console) == 1, [type(h).__name__ for h in console]
        assert getattr(console[0], "_ecm_json_handler", False)

    def test_install_is_idempotent(self):
        """Repeat installs never accumulate handlers."""
        plain = logging.StreamHandler()
        logging.getLogger().addHandler(plain)

        observability.install_json_logging()
        observability.install_json_logging()

        assert len(self._console_handlers()) == 1

    def test_each_record_emitted_exactly_once(self):
        """One log call -> one JSON line on the (single) console handler."""
        import io

        plain = logging.StreamHandler()
        logging.getLogger().addHandler(plain)
        observability.install_json_logging()

        # Point every surviving console handler at one shared buffer so any
        # duplicate emission shows up as a second line.
        buffer = io.StringIO()
        for handler in self._console_handlers():
            handler.setStream(buffer)

        logging.getLogger("ecm.access").info(
            "GET /api/x -> 401 in 0.1ms",
            extra={"event": "http_request", "status": "401"},
        )

        lines = [ln for ln in buffer.getvalue().splitlines() if ln.strip()]
        assert len(lines) == 1, lines
        payload = json.loads(lines[0])
        assert payload["logger"] == "ecm.access"
        assert payload["event"] == "http_request"

    def test_non_console_handlers_are_preserved(self):
        """File-like and custom handlers must survive the install sweep."""
        import io

        root = logging.getLogger()
        stringio_handler = logging.StreamHandler(io.StringIO())  # test capture
        custom_handler = logging.NullHandler()  # stand-in for RingBufferHandler
        root.addHandler(stringio_handler)
        root.addHandler(custom_handler)

        observability.install_json_logging()

        assert stringio_handler in root.handlers
        assert custom_handler in root.handlers

    def test_real_persistent_json_handler_is_preserved(self, tmp_path: Path):
        """Reinstalling console JSON logging must not detach file persistence."""
        import log_utils

        handler = log_utils.install_persistent_json_logging(
            tmp_path, max_bytes=1024, backup_count=2
        )
        assert handler is not None

        observability.install_json_logging()

        assert handler in logging.getLogger().handlers
        assert getattr(handler, "_ecm_persistent_json_handler", False)
        handler.close()
        logging.getLogger().removeHandler(handler)


class TestMetrics:
    def test_install_metrics_creates_required_series(self):
        metrics = observability.install_metrics()
        # The four metrics listed in the acceptance criteria must exist.
        assert "http_requests_total" in metrics
        assert "http_request_duration_seconds" in metrics
        assert "health_ready_ok" in metrics
        assert "health_ready_check_duration_seconds" in metrics
        # bd-h0wfu: build identity gauge must also be installed.
        assert "app_info" in metrics

    def test_install_metrics_is_idempotent(self):
        first = observability.install_metrics()
        second = observability.install_metrics()
        # Same object returned — don't rebuild the registry.
        assert first["http_requests_total"] is second["http_requests_total"]

    def test_render_metrics_emits_prometheus_text(self):
        observability.install_metrics()
        # Increment a series so the exposition has at least one sample.
        observability.get_metric("http_requests_total").labels(
            method="GET", path="/api/health", status="200"
        ).inc()
        body = observability.render_metrics().decode("utf-8")

        assert "# HELP ecm_http_requests_total" in body
        assert "# TYPE ecm_http_requests_total counter" in body
        assert "ecm_http_request_duration_seconds" in body
        assert "ecm_health_ready_ok" in body
        assert "ecm_health_ready_check_duration_seconds" in body

    def test_http_counter_increments(self):
        observability.install_metrics()
        counter = observability.get_metric("http_requests_total")
        counter.labels(method="GET", path="/api/health", status="200").inc()
        counter.labels(method="GET", path="/api/health", status="200").inc()
        # Use the internal ``_value`` accessor since labels() returns a
        # child metric with a ``get()``-friendly counter value.
        sample = counter.labels(method="GET", path="/api/health", status="200")
        assert sample._value.get() == 2.0

    def test_health_ready_ok_gauge(self):
        observability.install_metrics()
        gauge = observability.get_metric("health_ready_ok")
        gauge.set(1)
        assert gauge._value.get() == 1.0
        gauge.set(0)
        assert gauge._value.get() == 0.0


class TestAppInfoGauge:
    """Tests for the ``ecm_app_info`` build-identity gauge (bd-h0wfu).

    The gauge follows the standard Prometheus "info" pattern: value is
    always 1.0; the meaningful payload is in the labels. Operators query
    the labels (``version``, ``git_sha``, ``release_channel``) to detect
    when the running container has drifted from ``origin/dev`` HEAD.
    """

    def test_app_info_is_published_with_env_labels(self, monkeypatch):
        """install_metrics stamps ecm_app_info with env-var labels."""
        monkeypatch.setenv("ECM_VERSION", "0.16.0-test")
        monkeypatch.setenv("GIT_COMMIT", "cafebabe1234")
        monkeypatch.setenv("RELEASE_CHANNEL", "dev")

        observability.install_metrics()
        body = observability.render_metrics().decode("utf-8")

        assert "# HELP ecm_app_info" in body
        assert "# TYPE ecm_app_info gauge" in body
        assert 'version="0.16.0-test"' in body
        assert 'git_sha="cafebabe1234"' in body
        assert 'release_channel="dev"' in body

    def test_app_info_falls_back_to_unknown(self, monkeypatch):
        """Missing env vars resolve to 'unknown' / 'latest', never crash."""
        monkeypatch.delenv("ECM_VERSION", raising=False)
        monkeypatch.delenv("GIT_COMMIT", raising=False)
        monkeypatch.delenv("RELEASE_CHANNEL", raising=False)

        observability.install_metrics()
        body = observability.render_metrics().decode("utf-8")

        assert 'version="unknown"' in body
        assert 'git_sha="unknown"' in body
        assert 'release_channel="latest"' in body

    def test_app_info_value_is_one(self, monkeypatch):
        """The info-pattern gauge is always 1.0 — labels carry the payload."""
        monkeypatch.setenv("ECM_VERSION", "v")
        monkeypatch.setenv("GIT_COMMIT", "g")
        monkeypatch.setenv("RELEASE_CHANNEL", "r")

        observability.install_metrics()
        gauge = observability.get_metric("app_info")
        sample = gauge.labels(version="v", git_sha="g", release_channel="r")
        assert sample._value.get() == 1.0
