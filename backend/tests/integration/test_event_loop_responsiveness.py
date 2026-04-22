"""
Event-loop responsiveness integration tests (bd-w3z4h).

Before this bead, sync CPU-heavy code (normalization_engine.normalize,
dummy_epg_engine.generate_xmltv, etc.) was called directly inside async
FastAPI handlers, which blocks the single-worker event loop for every
concurrent request.

These tests assert the fix: /api/health stays responsive (<500ms) while a
pathological sync CPU load is in flight at another endpoint. They use the
full ASGI app + async test client, so they exercise the real middleware
stack and dependency graph.
"""
import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest


class _SlowEngine:
    """Stand-in for NormalizationEngine that blocks synchronously."""

    def __init__(self, sleep_seconds: float = 0.8):
        self.sleep_seconds = sleep_seconds

    def test_rules_batch(self, texts):
        time.sleep(self.sleep_seconds)
        # Return shape that matches the router's expectations
        return []

    def test_rule(self, **kwargs):
        time.sleep(self.sleep_seconds)
        return {"matched": False, "transformed": kwargs.get("text", "")}


class TestHealthRespondsDuringCpuBoundWork:
    """Core reliability claim — health stays fast while another handler is
    offloading a slow sync call to the CPU pool."""

    @pytest.mark.asyncio
    async def test_health_under_500ms_while_normalize_batch_runs(self, async_client):
        """Fire a slow /normalize POST and concurrently poll /api/health.
        The health response must return in under 500ms even though the
        normalize handler is still blocked on sync work.

        To faithfully exercise event-loop blocking, we start the slow POST,
        yield to let it enter run_cpu_bound (which offloads to a thread), then
        fire /api/health and measure wall-clock until it returns. With the fix
        in place, health returns fast because the blocking work is on a thread.
        Without the fix, health would not respond until the slow POST released
        the loop.
        """
        slow_engine = _SlowEngine(sleep_seconds=0.8)

        with patch(
            "normalization_engine.get_normalization_engine",
            return_value=slow_engine,
        ):
            # Kick off slow request and yield so it enters the sync offload
            slow_task = asyncio.create_task(
                async_client.post("/api/normalization/normalize", json={"texts": ["x"]})
            )
            # Yield a few ticks so the slow_task's handler reaches run_cpu_bound
            # and begins blocking (on a thread if fix in place, on the loop if not)
            for _ in range(10):
                await asyncio.sleep(0)

            # Measure wall-clock from here to health completion
            start = time.monotonic()
            health_response = await async_client.get("/api/health")
            health_elapsed = time.monotonic() - start

            # Drain the slow task
            slow_response = await slow_task

        assert health_response.status_code == 200
        assert health_response.json()["status"] == "healthy"
        assert health_elapsed < 0.5, (
            f"/api/health took {health_elapsed:.3f}s while a "
            f"{slow_engine.sleep_seconds}s sync CPU call was in flight — "
            "event loop was blocked (CPU work not offloaded to threadpool)"
        )
        # Slow request should still have completed successfully
        assert slow_response.status_code == 200

    @pytest.mark.asyncio
    async def test_health_fast_during_xmltv_generate(self, async_client, test_session):
        """Same guarantee for dummy-epg generate_xmltv — another heavy sync
        offload path.
        """
        from models import DummyEPGProfile

        # Create a minimal profile so the route has something to iterate
        profile = DummyEPGProfile(
            name="Test",
            enabled=True,
            title_pattern="",
            time_pattern="",
            date_pattern="",
            title_template="",
            description_template="",
            event_timezone="UTC",
            output_timezone="UTC",
            program_duration=30,
        )
        test_session.add(profile)
        test_session.commit()

        def slow_generate(profile_data, channel_map):
            time.sleep(0.8)
            return "<tv></tv>"

        async def empty_channels(*args, **kwargs):
            return {}

        mock_client = MagicMock()
        # Use threading.Event so it can be set from a worker thread without
        # requiring the event loop to schedule it.
        import threading
        in_sync_call = threading.Event()

        def slow_generate_signal(profile_data, channel_map):
            """Signal once the sync call has been entered, then block."""
            in_sync_call.set()
            time.sleep(0.8)
            return "<tv></tv>"

        async def wait_for_sync_call(timeout: float):
            """Poll the threading.Event from the event loop."""
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if in_sync_call.is_set():
                    return
                await asyncio.sleep(0.01)
            raise AssertionError("XMLTV handler never reached generate_xmltv")

        with patch(
            "dummy_epg_engine.generate_xmltv", side_effect=slow_generate_signal
        ), patch(
            "routers.dummy_epg._fetch_all_channels", side_effect=empty_channels
        ), patch(
            "routers.dummy_epg.get_client", return_value=mock_client
        ), patch(
            "routers.dummy_epg.cache"
        ) as mock_cache:
            mock_cache.get.return_value = None

            slow_task = asyncio.create_task(async_client.get("/api/dummy-epg/xmltv"))
            # Wait until the sync call has actually started.
            await wait_for_sync_call(timeout=2.0)

            start = time.monotonic()
            health_response = await async_client.get("/api/health")
            health_elapsed = time.monotonic() - start

            await slow_task

        assert health_response.status_code == 200
        assert health_elapsed < 0.5, (
            f"/api/health took {health_elapsed:.3f}s during XMLTV generation — "
            "event loop blocked by synchronous generate_xmltv()"
        )


class TestRequestTimeoutMiddleware:
    """bd-w3z4h timeout middleware: handlers exceeding the budget must get 504."""

    @pytest.mark.asyncio
    async def test_fast_request_passes_through(self, async_client):
        """A sub-second handler should not be affected by the middleware."""
        response = await async_client.get("/api/health")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_slow_handler_returns_504(self, async_client, monkeypatch):
        """If a handler stalls longer than the budget, the middleware returns 504."""
        # Override the timeout to 0.2s for the duration of this test
        import main as main_module

        monkeypatch.setattr(main_module, "_REQUEST_TIMEOUT_SECONDS", 0.2)

        # Register a deliberately slow route on the app for this test
        from main import app

        @app.get("/api/_test_stall_for_timeout", include_in_schema=False)
        async def _stall_route():
            await asyncio.sleep(1.0)
            return {"unreachable": True}

        try:
            response = await async_client.get("/api/_test_stall_for_timeout")
            assert response.status_code == 504
            body = response.json()
            assert body["detail"] == "Gateway Timeout"
        finally:
            # Remove the test route so it doesn't leak to other tests
            app.routes[:] = [
                r for r in app.routes
                if getattr(r, "path", None) != "/api/_test_stall_for_timeout"
            ]
