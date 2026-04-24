"""
Tests for the client-errors router (ADR-006 / bd-i6a1m).

Covers happy path, auth, size caps, schema validation, rate limiting,
stack-path scrubbing, and Prometheus counter emission.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest


VALID_UA_HASH = hashlib.sha256(b"Mozilla/5.0 (Test)").hexdigest()
VALID_RELEASE = "0.16.0-0003"


def _valid_payload(**overrides) -> dict:
    """Return a minimally valid payload matching the Pydantic schema."""
    base = {
        "kind": "boundary",
        "message": "TypeError: undefined is not a function",
        "stack": "at handleClick (/app/static/assets/bundle-abc.js:42:17)\n"
                 "at onClick (/app/static/assets/bundle-abc.js:10:5)",
        "release": VALID_RELEASE,
        "route": "/channels",
        "user_agent_hash": VALID_UA_HASH,
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    base.update(overrides)
    return base


def _reset_client_errors_state() -> None:
    """Clear rate-limit + release-LRU state between tests."""
    from routers import client_errors as ce
    ce._reset_rate_limits_for_tests()
    ce._reset_release_cache_for_tests()


def _get_metric_sample(metric_name: str, **labels) -> float:
    """Return the sample value for ``metric_name`` with ``labels``.

    ``metric_name`` is the full exposition name (e.g.
    ``ecm_client_errors_total``). The prometheus_client library stores
    counters under a family name with the ``_total`` suffix stripped,
    so we iterate every family and match on sample name instead.
    Returns 0.0 when no sample matches — the counter hasn't been touched.
    """
    from observability import REGISTRY
    if REGISTRY is None:
        return 0.0
    for metric in REGISTRY.collect():
        for sample in metric.samples:
            if sample.name != metric_name:
                continue
            if all(sample.labels.get(k) == v for k, v in labels.items()):
                return float(sample.value)
    return 0.0


def _get_histogram_count(metric_name: str) -> float:
    """Return the _count value of a histogram metric (total observations)."""
    from observability import REGISTRY
    if REGISTRY is None:
        return 0.0
    target = metric_name + "_count"
    for metric in REGISTRY.collect():
        for sample in metric.samples:
            if sample.name == target:
                return float(sample.value)
    return 0.0


@pytest.fixture(autouse=True)
def _reset_between_tests():
    """Clear client-errors in-memory state before every test in this module."""
    _reset_client_errors_state()
    yield
    _reset_client_errors_state()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
class TestHappyPath:

    @pytest.mark.asyncio
    async def test_valid_payload_returns_204(self, async_client):
        response = await async_client.post("/api/client-errors", json=_valid_payload())
        assert response.status_code == 204, response.text
        assert response.content == b""

    @pytest.mark.asyncio
    async def test_valid_payload_increments_counter(self, async_client):
        before = _get_metric_sample(
            "ecm_client_errors_total", kind="boundary", release=VALID_RELEASE,
        )
        response = await async_client.post("/api/client-errors", json=_valid_payload())
        assert response.status_code == 204
        after = _get_metric_sample(
            "ecm_client_errors_total", kind="boundary", release=VALID_RELEASE,
        )
        assert after - before == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_valid_payload_observes_histogram(self, async_client):
        before = _get_histogram_count("ecm_client_error_reports_bytes")
        response = await async_client.post("/api/client-errors", json=_valid_payload())
        assert response.status_code == 204
        after = _get_histogram_count("ecm_client_error_reports_bytes")
        assert after - before == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Schema enforcement
# ---------------------------------------------------------------------------
class TestSchemaValidation:

    @pytest.mark.asyncio
    async def test_missing_required_field_returns_422(self, async_client):
        """Drop 'kind' — endpoint must reject."""
        payload = _valid_payload()
        del payload["kind"]
        response = await async_client.post("/api/client-errors", json=payload)
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_unknown_kind_returns_422(self, async_client):
        response = await async_client.post(
            "/api/client-errors", json=_valid_payload(kind="not_a_real_kind"),
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_oversized_message_returns_422(self, async_client):
        response = await async_client.post(
            "/api/client-errors",
            json=_valid_payload(message="x" * 600),  # cap is 512
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_extra_field_is_rejected(self, async_client):
        """Deny-by-default allowlist: extra fields must not be accepted."""
        payload = _valid_payload()
        payload["referrer"] = "https://secret.example/admin?token=abc"
        response = await async_client.post("/api/client-errors", json=payload)
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_invalid_ua_hash_returns_422(self, async_client):
        response = await async_client.post(
            "/api/client-errors",
            json=_valid_payload(user_agent_hash="not-a-hash"),
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_invalid_schema_bumps_dropped_counter(self, async_client):
        before = _get_metric_sample(
            "ecm_client_errors_dropped_total", reason="invalid_schema",
        )
        await async_client.post("/api/client-errors", json={"kind": "boundary"})
        after = _get_metric_sample(
            "ecm_client_errors_dropped_total", reason="invalid_schema",
        )
        assert after - before >= 1.0


# ---------------------------------------------------------------------------
# Oversized body
# ---------------------------------------------------------------------------
class TestOversizedPayload:

    @pytest.mark.asyncio
    async def test_body_over_8kb_returns_413(self, async_client):
        """Raw request over 8 KB returns 413 before Pydantic even runs."""
        # Build a 10 KB JSON string. We bypass json= (httpx would still send
        # it) and post raw bytes with the right content-type.
        big_payload = _valid_payload(stack="x" * 9000)
        raw = json.dumps(big_payload).encode("utf-8")
        assert len(raw) > 8192
        response = await async_client.post(
            "/api/client-errors",
            content=raw,
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 413

    @pytest.mark.asyncio
    async def test_oversized_bumps_dropped_counter(self, async_client):
        before = _get_metric_sample(
            "ecm_client_errors_dropped_total", reason="oversized",
        )
        big_payload = _valid_payload(stack="x" * 9000)
        raw = json.dumps(big_payload).encode("utf-8")
        await async_client.post(
            "/api/client-errors",
            content=raw,
            headers={"Content-Type": "application/json"},
        )
        after = _get_metric_sample(
            "ecm_client_errors_dropped_total", reason="oversized",
        )
        assert after - before == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Rate limit
# ---------------------------------------------------------------------------
class TestRateLimit:

    @pytest.mark.asyncio
    async def test_exceeds_rate_limit_returns_429(self, async_client):
        """11th request from the same bucket in <60s must return 429."""
        from routers import client_errors as ce
        payload = _valid_payload()
        # Send 10 successful reports.
        for _ in range(ce.RATE_LIMIT_EVENTS):
            response = await async_client.post("/api/client-errors", json=payload)
            assert response.status_code == 204
        # 11th must be rejected.
        response = await async_client.post("/api/client-errors", json=payload)
        assert response.status_code == 429

    @pytest.mark.asyncio
    async def test_rate_limit_bumps_dropped_counter(self, async_client):
        from routers import client_errors as ce
        payload = _valid_payload()
        for _ in range(ce.RATE_LIMIT_EVENTS):
            await async_client.post("/api/client-errors", json=payload)
        before = _get_metric_sample(
            "ecm_client_errors_dropped_total", reason="rate_limited",
        )
        await async_client.post("/api/client-errors", json=payload)
        after = _get_metric_sample(
            "ecm_client_errors_dropped_total", reason="rate_limited",
        )
        assert after - before == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Stack-path scrubbing
# ---------------------------------------------------------------------------
class TestStackPathScrubbing:

    def test_absolute_paths_stripped_from_stack(self):
        """The Pydantic validator rewrites absolute paths to basenames."""
        from routers.client_errors import ClientErrorPayload
        stack = (
            "at handleClick (/Users/operator/projects/ecm/bundle.js:42:17)\n"
            "at onClick (C:\\Users\\operator\\bundle.js:10:5)\n"
            "at next (file:///app/static/assets/chunk-xyz.js:5:1)"
        )
        model = ClientErrorPayload.model_validate(_valid_payload(stack=stack))
        # Basenames only — no remaining directory separators in path-like
        # runs.
        assert "/Users/operator" not in model.stack
        assert "C:\\Users" not in model.stack
        assert "file://" not in model.stack
        # Filenames + line/col preserved.
        assert "bundle.js:42:17" in model.stack
        assert "bundle.js:10:5" in model.stack
        assert "chunk-xyz.js:5:1" in model.stack

    def test_route_query_string_stripped(self):
        from routers.client_errors import ClientErrorPayload
        model = ClientErrorPayload.model_validate(_valid_payload(
            route="/channels?filter=foo&token=secret#hash"
        ))
        assert model.route == "/channels"


# ---------------------------------------------------------------------------
# UA hash determinism
# ---------------------------------------------------------------------------
class TestUserAgentHash:

    def test_ua_hash_is_deterministic(self):
        """Same UA produces the same hash across two calls."""
        ua = "Mozilla/5.0 (X11; Linux x86_64) Gecko/20100101 Firefox/120.0"
        a = hashlib.sha256(ua.encode("utf-8")).hexdigest()
        b = hashlib.sha256(ua.encode("utf-8")).hexdigest()
        assert a == b
        assert len(a) == 64

    @pytest.mark.asyncio
    async def test_ua_hash_roundtrips_through_endpoint(self, async_client):
        """Hashing on the client and echoing it through the endpoint preserves it."""
        ua = "Mozilla/5.0 (TestBrowser)"
        ua_hash = hashlib.sha256(ua.encode("utf-8")).hexdigest()
        response = await async_client.post(
            "/api/client-errors", json=_valid_payload(user_agent_hash=ua_hash),
        )
        assert response.status_code == 204


# ---------------------------------------------------------------------------
# Auth gating
# ---------------------------------------------------------------------------
class TestAuthGating:
    """The global ``auth_middleware`` in main.py gates /api/* when
    ``require_auth`` and ``setup_complete`` are both true. The default
    test fixture runs with auth DISABLED (the conftest auth settings are
    unset), so here we explicitly enable auth and verify the middleware
    rejects the unauthenticated request before it reaches the handler.
    """

    @pytest.mark.asyncio
    async def test_missing_jwt_returns_401_when_auth_enabled(self, async_client):
        class _FakeAuthSettings:
            require_auth = True
            setup_complete = True

        with patch("main.get_auth_settings", return_value=_FakeAuthSettings()):
            response = await async_client.post(
                "/api/client-errors", json=_valid_payload(),
            )
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# Release label cardinality cap
# ---------------------------------------------------------------------------
class TestReleaseLabel:

    def test_label_release_preserves_first_three(self):
        from routers.client_errors import _label_release, _reset_release_cache_for_tests
        _reset_release_cache_for_tests()
        assert _label_release("v1") == "v1"
        assert _label_release("v2") == "v2"
        assert _label_release("v3") == "v3"
        # v1, v2, v3 are all still in the LRU.
        assert _label_release("v1") == "v1"

    def test_label_release_empty_rolls_up_to_stale(self):
        from routers.client_errors import _label_release
        assert _label_release("") == "stale"
