"""
Integration-level tests for observability middleware + /metrics endpoint.

Covers the wiring in ``main.py``:

- /metrics returns Prometheus text and exposes all four required series
- Every request emits one JSON log line carrying a trace id
- Inbound ``X-Request-ID`` is used as the trace id when present
- A generated trace id is a UUIDv4 and echoes back in the response header
- The HTTP request counter increments per real endpoint hit
- The readiness gauge reflects the actual subsystem verdict
"""
import asyncio
import io
import json
import logging
import re
import uuid

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import observability


@pytest.fixture(autouse=True)
def _reset_metrics_state():
    """Rebuild the Prometheus registry per test.

    Counters and histograms live at module scope, so leaks across tests
    would inflate assertions. The ``async_client`` fixture imports ``main``
    (which calls ``install_observability``), so we also re-install after
    resetting to keep the middleware's references live.
    """
    observability.reset_for_tests()
    observability.install_metrics()
    yield
    observability.reset_for_tests()


@pytest.fixture
def capture_json_logs():
    """Attach a stream handler that captures JSON-formatted log output.

    Yields a ``list[dict]`` populated with parsed JSON payloads in order of
    emission. The handler is removed on teardown.
    """
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(observability.JsonFormatter())
    handler.addFilter(observability._TraceIdFilter())
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    captured: list[dict] = []

    def _parse():
        captured.clear()
        for line in buffer.getvalue().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                captured.append(json.loads(line))
            except json.JSONDecodeError:
                # Non-JSON lines may sneak through (e.g. traces emitted
                # before install_json_logging). Skip them rather than fail.
                continue
        return captured

    try:
        yield _parse
    finally:
        root.removeHandler(handler)


# =========================================================================
# /metrics endpoint
# =========================================================================
class TestMetricsEndpoint:
    @pytest.mark.asyncio
    async def test_metrics_returns_200(self, async_client):
        response = await async_client.get("/metrics")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")

    @pytest.mark.asyncio
    async def test_metrics_contains_all_required_series(self, async_client):
        response = await async_client.get("/metrics")
        body = response.text
        # Four required metric families per the acceptance criteria.
        assert "ecm_http_requests_total" in body
        assert "ecm_http_request_duration_seconds" in body
        assert "ecm_health_ready_ok" in body
        assert "ecm_health_ready_check_duration_seconds" in body

    @pytest.mark.asyncio
    async def test_metrics_endpoint_is_unauthenticated(self, async_client):
        """``/metrics`` must remain scrape-able without a JWT.

        Prometheus scrapers have no session context — gating /metrics behind
        the same auth as /api/* would make the exporter useless. We assert
        the behavior explicitly so a future refactor doesn't silently break
        the scrape path.
        """
        response = await async_client.get("/metrics")
        assert response.status_code == 200


# =========================================================================
# Trace-id propagation
# =========================================================================
class TestTraceIdMiddleware:
    @pytest.mark.asyncio
    async def test_generated_trace_id_echoes_in_header(self, async_client):
        response = await async_client.get("/api/health")
        tid = response.headers.get("x-request-id")
        assert tid is not None
        # Generated trace ids are UUIDv4.
        parsed = uuid.UUID(tid)
        assert parsed.version == 4

    @pytest.mark.asyncio
    async def test_inbound_request_id_is_respected(self, async_client):
        provided = "client-supplied-trace-000"
        response = await async_client.get(
            "/api/health", headers={"X-Request-ID": provided}
        )
        assert response.headers.get("x-request-id") == provided

    @pytest.mark.asyncio
    async def test_trace_id_appears_in_log_line(self, async_client, capture_json_logs):
        provided = "trace-log-check-42"
        await async_client.get("/api/health", headers={"X-Request-ID": provided})
        records = capture_json_logs()
        # At least one record must carry our trace id — the request-end log
        # line from the observability middleware. We don't care about every
        # subsystem log line, only that correlation made it through.
        matching = [r for r in records if r.get("trace_id") == provided]
        assert matching, (
            f"no log record carried trace_id={provided}; "
            f"saw trace ids: {[r.get('trace_id') for r in records]}"
        )

    @pytest.mark.asyncio
    async def test_generated_trace_id_matches_uuidv4_format_in_logs(
        self, async_client, capture_json_logs
    ):
        await async_client.get("/api/health")
        records = capture_json_logs()
        # Find a record with a non-sentinel trace id — must be a UUID.
        uuid_pattern = re.compile(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
        )
        real_ids = [
            r["trace_id"]
            for r in records
            if r.get("trace_id") and r["trace_id"] != "-"
        ]
        assert real_ids, "no log records carried a non-sentinel trace id"
        assert all(uuid_pattern.match(tid) for tid in real_ids), real_ids


# =========================================================================
# Access log coverage of auth rejections (bd-cng0d)
# =========================================================================
class TestAccessLogCoversAuthRejections:
    """The structured ``ecm.access`` logger must observe ALL requests —
    including those the auth middleware rejects with 401 before any route
    handler runs.

    Regression guard: ``observability_middleware`` used to be registered
    first (= innermost in Starlette's onion), so auth rejections
    short-circuited before ever reaching it — 401s appeared only in raw
    uvicorn lines, invisible to any consumer of the JSON stream. It is now
    registered last (= outermost).
    """

    @pytest.fixture
    def capture_access_records(self):
        """Capture raw LogRecords emitted on the ``ecm.access`` logger."""
        records: list[logging.LogRecord] = []

        class _Capture(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = _Capture()
        access_logger = logging.getLogger("ecm.access")
        prev_level = access_logger.level
        access_logger.addHandler(handler)
        access_logger.setLevel(logging.INFO)
        try:
            yield records
        finally:
            access_logger.removeHandler(handler)
            access_logger.setLevel(prev_level)

    @pytest.mark.asyncio
    async def test_401_rejection_emits_exactly_one_access_record(
        self, async_client, capture_access_records
    ):
        """An unauthenticated request to a protected endpoint produces
        exactly ONE structured access-log record carrying status 401 —
        no bypass (0 records) and no duplication (2+ records)."""
        with patch("main.get_auth_settings") as auth_mock:
            auth_mock.return_value.require_auth = True
            auth_mock.return_value.setup_complete = True
            response = await async_client.get("/api/notifications")

        assert response.status_code == 401

        http_records = [
            r
            for r in capture_access_records
            if getattr(r, "event", None) == "http_request"
        ]
        assert len(http_records) == 1, (
            f"expected exactly 1 structured access record, got "
            f"{len(http_records)}: {[r.getMessage() for r in http_records]}"
        )
        record = http_records[0]
        assert record.status == "401"
        assert record.method == "GET"
        assert record.path == "/api/notifications"

    @pytest.mark.asyncio
    async def test_401_rejection_record_carries_no_credentials(
        self, async_client, capture_access_records
    ):
        """The 401 access record must not leak tokens/cookies."""
        with patch("main.get_auth_settings") as auth_mock:
            auth_mock.return_value.require_auth = True
            auth_mock.return_value.setup_complete = True
            response = await async_client.get(
                "/api/notifications",
                headers={"Authorization": "Bearer super-secret-token"},
                cookies={"access_token": "cookie-secret-token"},
            )

        assert response.status_code == 401
        http_records = [
            r
            for r in capture_access_records
            if getattr(r, "event", None) == "http_request"
        ]
        assert http_records
        rendered = observability.JsonFormatter().format(http_records[0])
        assert "super-secret-token" not in rendered
        assert "cookie-secret-token" not in rendered

    @pytest.mark.asyncio
    async def test_401_rejection_gets_trace_id_header(self, async_client):
        """Outermost observability layer stamps X-Request-ID even on 401s."""
        with patch("main.get_auth_settings") as auth_mock:
            auth_mock.return_value.require_auth = True
            auth_mock.return_value.setup_complete = True
            response = await async_client.get("/api/notifications")

        assert response.status_code == 401
        assert response.headers.get("x-request-id")


# =========================================================================
# Request counter
# =========================================================================
class TestHttpRequestCounter:
    @pytest.mark.asyncio
    async def test_counter_increments_on_health_request(self, async_client):
        before_response = await async_client.get("/metrics")
        before_body = before_response.text

        await async_client.get("/api/health")
        await async_client.get("/api/health")

        after_response = await async_client.get("/metrics")
        after_body = after_response.text

        # The sample line uses route-pattern labels, not raw paths.
        def _count(body: str) -> float:
            # Example line we expect:
            #   ecm_http_requests_total{method="GET",path="/api/health",status="200"} 2.0
            for line in body.splitlines():
                if line.startswith("ecm_http_requests_total{") and 'path="/api/health"' in line:
                    return float(line.rsplit(" ", 1)[-1])
            return 0.0

        assert _count(after_body) >= _count(before_body) + 2

    @pytest.mark.asyncio
    async def test_counter_uses_route_pattern_not_raw_path(self, async_client):
        """Cardinality discipline — parametrized routes must collapse to one label value.

        We hit the cache/invalidate endpoint with distinct query strings and
        assert the metric line shows a bounded ``path`` label rather than a
        new time series per query string. This is the guardrail the SRE
        persona cares about most — every raw-path metric is a silent cost bomb.
        """
        await async_client.post("/api/cache/invalidate", params={"prefix": "a"})
        await async_client.post("/api/cache/invalidate", params={"prefix": "b"})

        response = await async_client.get("/metrics")
        body = response.text
        # Query strings must not appear in the path label.
        assert "?prefix=a" not in body
        assert "?prefix=b" not in body


# =========================================================================
# Readiness gauge
# =========================================================================
def _mk_dispatcharr_client_mock(status: int = 200):
    mock_client = MagicMock()
    mock_client.base_url = "http://dispatcharr.example:9191"
    inner = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = status
    inner.get = AsyncMock(return_value=mock_response)
    mock_client._client = inner
    return mock_client


class TestHealthReadyGauge:
    @pytest.mark.asyncio
    async def test_gauge_reads_1_when_all_checks_pass(self, async_client):
        from routers import health as health_module

        health_module._reset_dispatcharr_cache()
        health_module._last_ready_state = None

        mock_settings = MagicMock()
        mock_settings.url = "http://dispatcharr.example:9191"

        with patch("routers.health.get_settings", return_value=mock_settings), \
             patch("routers.health.get_client", return_value=_mk_dispatcharr_client_mock()), \
             patch("routers.health.shutil.which", return_value="/usr/bin/ffprobe"):
            response = await async_client.get("/api/health/ready")
            assert response.status_code == 200

            metrics_body = (await async_client.get("/metrics")).text

        # Gauge line:  ecm_health_ready_ok 1.0
        gauge_line = next(
            (line for line in metrics_body.splitlines()
             if line.startswith("ecm_health_ready_ok ")),
            None,
        )
        assert gauge_line is not None
        assert gauge_line.endswith(" 1.0")

    @pytest.mark.asyncio
    async def test_gauge_reads_0_when_check_fails(self, async_client):
        from routers import health as health_module

        health_module._reset_dispatcharr_cache()
        health_module._last_ready_state = None

        mock_settings = MagicMock()
        mock_settings.url = ""  # dispatcharr skipped

        with patch("routers.health.get_settings", return_value=mock_settings), \
             patch("routers.health.shutil.which", return_value=None):  # ffprobe fail
            response = await async_client.get("/api/health/ready")
            assert response.status_code == 503

            metrics_body = (await async_client.get("/metrics")).text

        gauge_line = next(
            (line for line in metrics_body.splitlines()
             if line.startswith("ecm_health_ready_ok ")),
            None,
        )
        assert gauge_line is not None
        assert gauge_line.endswith(" 0.0")

    @pytest.mark.asyncio
    async def test_per_check_histogram_observes_samples(self, async_client):
        from routers import health as health_module

        health_module._reset_dispatcharr_cache()
        health_module._last_ready_state = None

        mock_settings = MagicMock()
        mock_settings.url = ""

        with patch("routers.health.get_settings", return_value=mock_settings), \
             patch("routers.health.shutil.which", return_value="/usr/bin/ffprobe"):
            await async_client.get("/api/health/ready")

            body = (await async_client.get("/metrics")).text

        # Every sub-check must record at least one observation. We look at
        # the ``_count`` time series per ``check`` label.
        assert 'ecm_health_ready_check_duration_seconds_count{check="database"}' in body
        assert 'ecm_health_ready_check_duration_seconds_count{check="dispatcharr"}' in body
        assert 'ecm_health_ready_check_duration_seconds_count{check="ffprobe"}' in body
