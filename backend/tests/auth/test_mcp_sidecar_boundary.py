"""Regression tests for the MCP client/sidecar/backend credential boundary."""
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from httpx import ASGITransport, AsyncClient

from auth.dependencies import _is_mcp_service_token, get_current_user
from auth.mcp_service import (
    MCP_CLAIM_HEADER,
    ensure_mcp_service_credentials,
    issue_test_claim,
    load_mcp_service_credentials,
    reset_mcp_projection_failure_log_latch,
    rotate_mcp_service_credentials,
    verify_mcp_service_claim,
)
from config import DispatcharrSettings, save_settings
from database import get_session


def test_internal_credentials_are_distinct_private_and_not_in_settings(tmp_path: Path):
    settings = tmp_path / "settings.json"
    external_key = "<EXTERNAL_MCP_CLIENT_KEY>"
    settings.write_text(json.dumps({"mcp_api_key": external_key}))
    projection = tmp_path / "mcp-service.json"

    credentials = ensure_mcp_service_credentials(projection)

    assert credentials.backend_key != external_key
    assert credentials.confirmation_key != credentials.backend_key
    assert projection.stat().st_mode & 0o777 == 0o600
    assert "backend_key" not in settings.read_text()

    rotated = rotate_mcp_service_credentials(projection)
    assert rotated != credentials
    assert ensure_mcp_service_credentials(projection) == rotated
    assert projection.stat().st_mode & 0o777 == 0o600


def test_external_key_rotation_projects_settings_atomically(tmp_path: Path):
    target = tmp_path / "settings.json"
    target.write_text('{"mcp_api_key":"<OLD_MCP_CLIENT_KEY>"}')
    settings = DispatcharrSettings(mcp_api_key="<NEW_MCP_CLIENT_KEY>")
    with patch("config.CONFIG_FILE", target), patch("config.ensure_config_dir"):
        save_settings(settings)
    assert json.loads(target.read_text())["mcp_api_key"] == "<NEW_MCP_CLIENT_KEY>"
    assert target.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob(".*.tmp"))


@pytest.mark.asyncio
async def test_claim_is_bound_to_request_and_single_use(tmp_path: Path):
    projection = tmp_path / "mcp-service.json"
    credentials = ensure_mcp_service_credentials(projection)
    app = FastAPI()

    @app.post("/write")
    async def write(request: Request):
        await verify_mcp_service_claim(request, credentials)
        return {"ok": True}

    body = {"ids": [2, 1]}
    claim = issue_test_claim(credentials, "POST", "/write", body)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/write", json=body, headers={MCP_CLAIM_HEADER: claim})
        replay = await client.post("/write", json=body, headers={MCP_CLAIM_HEADER: claim})
        drift = await client.post(
            "/write", json={"ids": [1]},
            headers={MCP_CLAIM_HEADER: issue_test_claim(credentials, "POST", "/write", body)},
        )

    assert first.status_code == 200
    assert replay.status_code == 403
    assert drift.status_code == 403


class TestUnwritableProjectionDegradesInsteadOfKillingECM:
    """…-04c0u.8: the projection must never be able to take ECM down.

    ``ensure_mcp_service_credentials`` is reachable from three liveness paths
    — the FastAPI startup handler, the auth middleware (every non-exempt
    request), and ``auth.dependencies._is_mcp_service_token`` inside
    ``get_current_user`` (every token-bearing request on a dependency-guarded
    route). An exception out of a startup handler aborts the ASGI lifespan
    (uvicorn logs "Application startup failed. Exiting."), and an exception out
    of either request path is a 500. All three go through
    ``load_mcp_service_credentials``, which reports the failure and returns
    ``None`` — no sidecar principal, ordinary 401 — rather than raising. The
    dependency seam is covered by
    ``TestBrokenProjectionAtTheRouteDependencySeam`` below.
    """

    def test_an_unwritable_projection_directory_returns_none(self, tmp_path: Path):
        projection_dir = tmp_path / "ecm-mcp"
        projection_dir.mkdir()
        projection_dir.chmod(0o500)
        try:
            with pytest.raises(PermissionError):
                ensure_mcp_service_credentials(projection_dir / "mcp-service.json")

            assert (
                load_mcp_service_credentials(projection_dir / "mcp-service.json")
                is None
            )
        finally:
            projection_dir.chmod(0o700)

    def test_a_malformed_projection_returns_none(self, tmp_path: Path):
        projection = tmp_path / "mcp-service.json"
        projection.write_text("not json at all")

        with pytest.raises(RuntimeError):
            ensure_mcp_service_credentials(projection)

        assert load_mcp_service_credentials(projection) is None

    def test_a_healthy_projection_is_returned_unchanged(self, tmp_path: Path):
        projection = tmp_path / "mcp-service.json"

        created = load_mcp_service_credentials(projection)

        assert created is not None
        assert created == ensure_mcp_service_credentials(projection)

    def test_a_symlinked_projection_is_refused_rather_than_chmodded(
        self, tmp_path: Path
    ):
        """finding 10 — the re-chmod must not follow a link to its target."""
        target = tmp_path / "unrelated.json"
        target.write_text(json.dumps({"backend_key": "a" * 40, "confirmation_key": "b" * 40}))
        target.chmod(0o644)
        projection = tmp_path / "mcp-service.json"
        projection.symlink_to(target)

        assert load_mcp_service_credentials(projection) is None
        assert target.stat().st_mode & 0o777 == 0o644


class TestBrokenProjectionAtTheRouteDependencySeam:
    """…-04c0u.8 follow-up: the *dependency* seam must degrade as well.

    Degrading only ``main.auth_middleware`` and the startup handler left the
    third liveness call site raising. ``auth.dependencies._is_mcp_service_token``
    runs inside ``get_current_user`` — before the JWT decode, on every
    token-bearing request, for every route that depends on it — so an operator
    with a perfectly good JWT met a 500 from the route dependency while the
    middleware had already degraded correctly, ``/api/health`` and
    ``/api/health/ready`` (both in ``AUTH_EXEMPT_PATHS``) stayed green, and the
    container's ``HEALTHCHECK`` reported healthy.

    The invariant, not the reproduction: **no reachable call site may raise out
    of a request or startup path on an unusable projection, on any branch.**
    Enforced here at the seam that survived the first round; the middleware and
    startup branches are enforced by
    ``TestUnwritableProjectionDegradesInsteadOfKillingECM`` above.
    """

    @staticmethod
    def _guarded_app() -> FastAPI:
        app = FastAPI()

        @app.get("/guarded")
        async def guarded(user=Depends(get_current_user)):  # pragma: no cover
            return {"username": user.username}

        # get_current_user resolves its session dependency before its body
        # runs; nothing under test touches the database.
        app.dependency_overrides[get_session] = lambda: None
        return app

    async def _get(self, projection: Path, token: str, *, mcp_api_key: str = "") -> int:
        app = self._guarded_app()
        with (
            patch("auth.dependencies.MCP_SERVICE_FILE", projection),
            patch(
                "auth.dependencies.get_settings",
                return_value=SimpleNamespace(mcp_api_key=mcp_api_key),
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/guarded", headers={"Authorization": f"Bearer {token}"}
                )
        return response.status_code

    @pytest.mark.asyncio
    async def test_an_unwritable_projection_directory_401s_rather_than_500s(
        self, tmp_path: Path
    ):
        projection_dir = tmp_path / "ecm-mcp"
        projection_dir.mkdir()
        projection_dir.chmod(0o500)
        try:
            status = await self._get(
                projection_dir / "mcp-service.json", "not-a-jwt-at-all"
            )
        finally:
            projection_dir.chmod(0o700)

        assert status == 401

    @pytest.mark.asyncio
    async def test_a_malformed_projection_401s_rather_than_500s(self, tmp_path: Path):
        projection = tmp_path / "mcp-service.json"
        projection.write_text("not json at all")

        assert await self._get(projection, "not-a-jwt-at-all") == 401

    @pytest.mark.asyncio
    async def test_a_symlinked_projection_401s_rather_than_500s(self, tmp_path: Path):
        target = tmp_path / "unrelated.json"
        target.write_text(
            json.dumps({"backend_key": "a" * 40, "confirmation_key": "b" * 40})
        )
        projection = tmp_path / "mcp-service.json"
        projection.symlink_to(target)

        assert await self._get(projection, "a" * 40) == 401

    @pytest.mark.asyncio
    async def test_an_absent_credential_never_authenticates_by_emptiness(
        self, tmp_path: Path
    ):
        """The degraded branch must not turn "no credential" into a match.

        A ``""`` private key that reached ``hmac.compare_digest`` unguarded
        would authenticate an empty bearer token as the admin-equivalent MCP
        service principal. Empty on both sides, and an empty *token*, all fall
        through to the ordinary 401.
        """
        projection_dir = tmp_path / "ecm-mcp"
        projection_dir.mkdir()
        projection_dir.chmod(0o500)
        try:
            with (
                patch(
                    "auth.dependencies.MCP_SERVICE_FILE",
                    projection_dir / "mcp-service.json",
                ),
                patch(
                    "auth.dependencies.get_settings",
                    return_value=SimpleNamespace(mcp_api_key=""),
                ),
            ):
                assert _is_mcp_service_token("") is False
                assert _is_mcp_service_token(" ") is False
                assert _is_mcp_service_token("anything") is False
        finally:
            projection_dir.chmod(0o700)

    @pytest.mark.asyncio
    async def test_a_healthy_projection_still_authenticates_the_principal(
        self, tmp_path: Path
    ):
        """The degraded path must not cost the healthy one its principal."""
        projection = tmp_path / "mcp-service.json"
        credentials = ensure_mcp_service_credentials(projection)

        assert await self._get(projection, credentials.backend_key) == 200


class TestDegradedModeDoesNotLogPerRequest:
    """…-04c0u.8 follow-up: degraded mode must not become a log amplifier.

    ``load_mcp_service_credentials`` sits on paths the auth middleware and
    ``get_current_user`` run for *every* non-exempt request. Logging a full
    traceback from there means an unauthenticated caller can drive unbounded
    stack traces at whatever rate it likes — and the likeliest cause of the
    broken state in the first place is a disk problem, which more logging makes
    worse. One traceback per unhealthy episode is the contract; subsequent
    failures drop to DEBUG until the projection recovers.
    """

    def test_a_persistent_failure_logs_one_traceback_not_one_per_call(
        self, tmp_path: Path, caplog
    ):
        projection_dir = tmp_path / "ecm-mcp"
        projection_dir.mkdir()
        projection_dir.chmod(0o500)
        projection = projection_dir / "mcp-service.json"
        reset_mcp_projection_failure_log_latch()
        try:
            with caplog.at_level(logging.DEBUG, logger="auth.mcp_service"):
                for _ in range(25):
                    assert load_mcp_service_credentials(projection) is None
        finally:
            projection_dir.chmod(0o700)
            reset_mcp_projection_failure_log_latch()

        with_traceback = [
            record
            for record in caplog.records
            if record.name == "auth.mcp_service" and record.exc_info
        ]
        assert len(with_traceback) == 1, (
            f"{len(with_traceback)} tracebacks for 25 degraded calls"
        )
        assert with_traceback[0].levelno == logging.ERROR

    def test_recovery_rearms_the_latch_so_a_new_episode_is_reported(
        self, tmp_path: Path, caplog
    ):
        projection_dir = tmp_path / "ecm-mcp"
        projection_dir.mkdir()
        projection = projection_dir / "mcp-service.json"
        reset_mcp_projection_failure_log_latch()
        try:
            with caplog.at_level(logging.DEBUG, logger="auth.mcp_service"):
                projection_dir.chmod(0o500)
                assert load_mcp_service_credentials(projection) is None
                assert load_mcp_service_credentials(projection) is None
                projection_dir.chmod(0o700)
                assert load_mcp_service_credentials(projection) is not None
                projection.unlink()
                projection_dir.chmod(0o500)
                assert load_mcp_service_credentials(projection) is None
        finally:
            projection_dir.chmod(0o700)
            reset_mcp_projection_failure_log_latch()

        tracebacks = [
            record
            for record in caplog.records
            if record.name == "auth.mcp_service" and record.exc_info
        ]
        assert len(tracebacks) == 2


class TestCredentialRotationRefusesLoudlyButCleanly:
    """The admin rotate/revoke endpoints are the one caller that must NOT degrade.

    ``routers.settings`` calls ``rotate_mcp_service_credentials`` from
    ``POST``/``DELETE /api/settings/mcp-api-key``. Those are explicit operator
    writes whose whole purpose is to replace the projection, so returning a
    quiet success on an unwritable mount would leave the operator believing a
    superseded credential was dead — strictly worse than failing. It stays
    fail-loud; what changes is that it no longer raises ``OSError`` out of the
    request path (…-04c0u.8 invariant) but refuses with a 503 that names the
    projection and the repair.

    It names them by ``MCP_SECRETS_DIR`` and filename rather than by resolved
    path: the path is derived from that environment read and reaches a logger
    on the same branch, which CodeQL flags as clear-text logging of sensitive
    data. ``test_04c0u8_projection_paths_are_not_logged.py`` pins the property;
    what this class pins is that the refusal stayed loud and repairable.
    """

    def test_an_unwritable_projection_is_a_named_503_not_a_raw_oserror(
        self, tmp_path: Path
    ):
        import routers.settings as settings_router

        projection_dir = tmp_path / "ecm-mcp"
        projection_dir.mkdir()
        projection_dir.chmod(0o500)
        try:
            with patch.object(
                settings_router, "MCP_SERVICE_FILE", projection_dir / "mcp-service.json"
            ):
                with pytest.raises(HTTPException) as raised:
                    settings_router._rotate_private_projection_or_503()
        finally:
            projection_dir.chmod(0o700)

        assert raised.value.status_code == 503
        assert str(projection_dir) not in raised.value.detail
        assert "MCP_SECRETS_DIR" in raised.value.detail
        assert "mcp-service.json" in raised.value.detail
        assert "not rotated" in raised.value.detail
        assert "PUID/PGID" in raised.value.detail

    def test_a_writable_projection_still_rotates(self, tmp_path: Path):
        import routers.settings as settings_router

        projection = tmp_path / "mcp-service.json"
        before = ensure_mcp_service_credentials(projection)

        with patch.object(settings_router, "MCP_SERVICE_FILE", projection):
            settings_router._rotate_private_projection_or_503()

        assert ensure_mcp_service_credentials(projection) != before
