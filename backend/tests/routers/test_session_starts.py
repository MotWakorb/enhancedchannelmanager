"""
Tests for the session-start router (bd-arp3o, spike bd-1tl01).

Covers happy path, idempotent dedup, TTL expiry, schema validation,
size cap, and Prometheus counter / gauge emission.
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest


def _valid_session_id() -> str:
    """Return a fresh UUIDv4 string."""
    return str(uuid.uuid4())


def _reset_session_state() -> None:
    """Clear dedup state between tests."""
    from routers import session_starts as ss
    ss._reset_dedup_for_tests()


def _get_metric_sample(metric_name: str, **labels) -> float:
    """Return the sample value for ``metric_name`` with ``labels``."""
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


@pytest.fixture(autouse=True)
def _reset_between_tests():
    """Clear session-start in-memory state before every test in this module."""
    _reset_session_state()
    yield
    _reset_session_state()


# ---------------------------------------------------------------------------
# Happy path — counter increment, dedup contract
# ---------------------------------------------------------------------------
class TestHappyPath:

    @pytest.mark.asyncio
    async def test_first_sighting_returns_200_not_deduplicated(self, async_client):
        sid = _valid_session_id()
        response = await async_client.post(
            "/api/session-start", json={"session_id": sid}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body == {"deduplicated": False}

    @pytest.mark.asyncio
    async def test_first_sighting_increments_counter(self, async_client):
        before = _get_metric_sample("ecm_session_starts_total")
        sid = _valid_session_id()
        response = await async_client.post(
            "/api/session-start", json={"session_id": sid}
        )
        assert response.status_code == 200
        after = _get_metric_sample("ecm_session_starts_total")
        assert after - before == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Dedup — same session_id twice in TTL = no double-count
# ---------------------------------------------------------------------------
class TestDedup:

    @pytest.mark.asyncio
    async def test_same_session_id_twice_returns_deduplicated(self, async_client):
        sid = _valid_session_id()
        first = await async_client.post(
            "/api/session-start", json={"session_id": sid}
        )
        assert first.status_code == 200
        assert first.json() == {"deduplicated": False}

        second = await async_client.post(
            "/api/session-start", json={"session_id": sid}
        )
        assert second.status_code == 200
        assert second.json() == {"deduplicated": True}

    @pytest.mark.asyncio
    async def test_same_session_id_twice_only_one_counter_increment(self, async_client):
        sid = _valid_session_id()
        before = _get_metric_sample("ecm_session_starts_total")
        await async_client.post("/api/session-start", json={"session_id": sid})
        await async_client.post("/api/session-start", json={"session_id": sid})
        await async_client.post("/api/session-start", json={"session_id": sid})
        after = _get_metric_sample("ecm_session_starts_total")
        # Three POSTs of the same session_id → exactly one counter bump.
        assert after - before == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_two_different_session_ids_two_increments(self, async_client):
        before = _get_metric_sample("ecm_session_starts_total")
        await async_client.post(
            "/api/session-start", json={"session_id": _valid_session_id()}
        )
        await async_client.post(
            "/api/session-start", json={"session_id": _valid_session_id()}
        )
        after = _get_metric_sample("ecm_session_starts_total")
        assert after - before == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# TTL expiry — same session_id after TTL = re-counted
# ---------------------------------------------------------------------------
class TestTTLExpiry:

    @pytest.mark.asyncio
    async def test_dedup_entry_expires_after_ttl(self, async_client, monkeypatch):
        """After 24h, a previously-seen session_id is treated as new again."""
        from routers import session_starts as ss

        # Anchor monotonic time at a known starting value.
        fake_now = [1000.0]

        def fake_monotonic() -> float:
            return fake_now[0]

        monkeypatch.setattr(ss.time, "monotonic", fake_monotonic)

        sid = _valid_session_id()
        before = _get_metric_sample("ecm_session_starts_total")

        # First sighting at t=1000 → counts (+1).
        first = await async_client.post(
            "/api/session-start", json={"session_id": sid}
        )
        assert first.json() == {"deduplicated": False}

        # Advance time past the TTL boundary (24h + 1s).
        fake_now[0] += ss.DEDUP_TTL_SECONDS + 1.0

        # Same session_id, but the dedup entry has expired → counts again (+1).
        second = await async_client.post(
            "/api/session-start", json={"session_id": sid}
        )
        assert second.json() == {"deduplicated": False}

        after = _get_metric_sample("ecm_session_starts_total")
        assert after - before == pytest.approx(2.0)

    @pytest.mark.asyncio
    async def test_dedup_holds_inside_ttl(self, async_client, monkeypatch):
        """Inside the TTL window, the same session_id remains deduplicated."""
        from routers import session_starts as ss

        fake_now = [1000.0]
        monkeypatch.setattr(ss.time, "monotonic", lambda: fake_now[0])

        sid = _valid_session_id()
        first = await async_client.post(
            "/api/session-start", json={"session_id": sid}
        )
        assert first.json() == {"deduplicated": False}

        # Advance time but stay well inside the 24h window.
        fake_now[0] += ss.DEDUP_TTL_SECONDS / 2

        second = await async_client.post(
            "/api/session-start", json={"session_id": sid}
        )
        assert second.json() == {"deduplicated": True}


# ---------------------------------------------------------------------------
# Schema validation — UUIDv4 required, extra fields forbidden
# ---------------------------------------------------------------------------
class TestSchemaValidation:

    @pytest.mark.asyncio
    async def test_non_uuid_session_id_returns_422(self, async_client):
        response = await async_client.post(
            "/api/session-start", json={"session_id": "not-a-uuid"}
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_empty_session_id_returns_422(self, async_client):
        response = await async_client.post(
            "/api/session-start", json={"session_id": ""}
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_uuid_v1_rejected(self, async_client):
        # UUIDv1 has version=1 in the third group; the regex requires v4.
        response = await async_client.post(
            "/api/session-start",
            json={"session_id": str(uuid.uuid1())},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_extra_fields_rejected(self, async_client):
        response = await async_client.post(
            "/api/session-start",
            json={
                "session_id": _valid_session_id(),
                "user_id": "leak-attempt",
            },
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_missing_session_id_returns_422(self, async_client):
        response = await async_client.post("/api/session-start", json={})
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_invalid_json_body_returns_422(self, async_client):
        response = await async_client.post(
            "/api/session-start",
            content=b"not-json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# Size cap — oversized body is rejected before parsing
# ---------------------------------------------------------------------------
class TestSizeCap:

    @pytest.mark.asyncio
    async def test_oversized_body_returns_413(self, async_client):
        from routers import session_starts as ss

        # Build a body that exceeds MAX_REQUEST_BYTES even though it would
        # otherwise be valid JSON shape.
        oversized = b'{"session_id": "' + b"x" * (ss.MAX_REQUEST_BYTES + 100) + b'"}'
        response = await async_client.post(
            "/api/session-start",
            content=oversized,
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 413

    @pytest.mark.asyncio
    async def test_oversized_body_does_not_increment_counter(self, async_client):
        from routers import session_starts as ss

        before = _get_metric_sample("ecm_session_starts_total")
        oversized = b'{"session_id": "' + b"x" * (ss.MAX_REQUEST_BYTES + 100) + b'"}'
        await async_client.post(
            "/api/session-start",
            content=oversized,
            headers={"Content-Type": "application/json"},
        )
        after = _get_metric_sample("ecm_session_starts_total")
        assert after == pytest.approx(before)


# ---------------------------------------------------------------------------
# Dedup-set-size gauge — exposes operational visibility
# ---------------------------------------------------------------------------
class TestDedupGauge:

    @pytest.mark.asyncio
    async def test_dedup_size_gauge_reflects_set_size(self, async_client):
        # Reset confirms gauge starts at 0.
        assert _get_metric_sample("ecm_session_dedup_set_size") == pytest.approx(0.0)

        await async_client.post(
            "/api/session-start", json={"session_id": _valid_session_id()}
        )
        await async_client.post(
            "/api/session-start", json={"session_id": _valid_session_id()}
        )
        await async_client.post(
            "/api/session-start", json={"session_id": _valid_session_id()}
        )

        # Three distinct session_ids → gauge reads 3.
        assert _get_metric_sample("ecm_session_dedup_set_size") == pytest.approx(3.0)

    @pytest.mark.asyncio
    async def test_dedup_size_gauge_reflects_pruning(self, async_client, monkeypatch):
        """After TTL expiry + a new request, the gauge drops the expired entries."""
        from routers import session_starts as ss

        fake_now = [1000.0]
        monkeypatch.setattr(ss.time, "monotonic", lambda: fake_now[0])

        # Seed two sessions.
        await async_client.post(
            "/api/session-start", json={"session_id": _valid_session_id()}
        )
        await async_client.post(
            "/api/session-start", json={"session_id": _valid_session_id()}
        )
        assert _get_metric_sample("ecm_session_dedup_set_size") == pytest.approx(2.0)

        # Advance past TTL so both entries are stale.
        fake_now[0] += ss.DEDUP_TTL_SECONDS + 1.0

        # A third request prunes the two stale entries before inserting itself,
        # leaving the gauge at exactly 1.
        await async_client.post(
            "/api/session-start", json={"session_id": _valid_session_id()}
        )
        assert _get_metric_sample("ecm_session_dedup_set_size") == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Auth posture — unauthenticated POSTs MUST be accepted (bd-m3vej)
# ---------------------------------------------------------------------------
class TestUnauthenticatedAccess:
    """The endpoint is in ``AUTH_EXEMPT_PATHS`` (bd-m3vej, follow-up to
    bd-arp3o). Pre-auth sessions MUST count toward the SLO-6 denominator;
    requiring a JWT here would silently exclude login-page sessions and
    re-introduce the SLI bias the spike (docs/sre/spike-slo-6-session-semantics.md
    §3.1) warned about.
    """

    @pytest.mark.asyncio
    async def test_unauthenticated_post_is_accepted_when_auth_enabled(
        self, async_client
    ):
        """Even with ``require_auth=True`` and ``setup_complete=True``,
        the global ``auth_middleware`` must let an unauthenticated POST
        reach the handler — exemption is enforced by inclusion in
        ``AUTH_EXEMPT_PATHS`` in main.py.
        """
        class _FakeAuthSettings:
            require_auth = True
            setup_complete = True

        with patch("main.get_auth_settings", return_value=_FakeAuthSettings()):
            response = await async_client.post(
                "/api/session-start",
                json={"session_id": _valid_session_id()},
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body == {"deduplicated": False}

    @pytest.mark.asyncio
    async def test_unauthenticated_post_increments_counter(self, async_client):
        """Unauthenticated POSTs still bump the SLO-6 denominator —
        otherwise pre-auth sessions silently fall out of the SLI base."""
        class _FakeAuthSettings:
            require_auth = True
            setup_complete = True

        before = _get_metric_sample("ecm_session_starts_total")
        with patch("main.get_auth_settings", return_value=_FakeAuthSettings()):
            response = await async_client.post(
                "/api/session-start",
                json={"session_id": _valid_session_id()},
            )
        assert response.status_code == 200
        after = _get_metric_sample("ecm_session_starts_total")
        assert after - before == pytest.approx(1.0)

    def test_session_start_path_is_in_auth_exempt_paths(self):
        """Belt-and-suspenders structural check — the constant in main.py
        must list this endpoint. Catches accidental removal during refactors.
        """
        from main import AUTH_EXEMPT_PATHS
        assert "/api/session-start" in AUTH_EXEMPT_PATHS


# ---------------------------------------------------------------------------
# Telemetry-disabled gate — operator toggle short-circuits the endpoint
# ---------------------------------------------------------------------------
class TestTelemetryDisabledGate:

    @pytest.mark.asyncio
    async def test_disabled_telemetry_skips_counter_bump(self, async_client, monkeypatch):
        """When telemetry_client_errors_enabled is False, no counter bump."""
        from config import get_settings

        # Read the current settings, flip telemetry off, write back.
        original = get_settings()
        # The settings object is a Pydantic model; rebuild with override.
        import config as cfg_mod

        class _StubSettings:
            telemetry_client_errors_enabled = False

        monkeypatch.setattr(cfg_mod, "get_settings", lambda: _StubSettings())

        before = _get_metric_sample("ecm_session_starts_total")
        response = await async_client.post(
            "/api/session-start",
            json={"session_id": _valid_session_id()},
        )
        assert response.status_code == 200
        assert response.json() == {"deduplicated": False}
        after = _get_metric_sample("ecm_session_starts_total")
        # No counter bump when the operator has disabled telemetry.
        assert after == pytest.approx(before)
        # Sanity — original settings still readable post-test fixture cleanup.
        assert original is not None
