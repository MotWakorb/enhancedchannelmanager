"""
Integration tests for First-Run Setup API endpoints.

TDD SPEC: These tests define expected first-run setup behavior.
They will FAIL initially - implementation makes them pass.

Test Spec: First-Run Setup (v6dxf.8.5)
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker


def _configure_auth(monkeypatch, tmp_path, *, require_auth: bool):
    from auth import settings as auth_settings

    settings = auth_settings.AuthSettings(
        setup_complete=False,
        require_auth=require_auth,
    )
    monkeypatch.setattr(auth_settings, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(
        auth_settings, "AUTH_CONFIG_FILE", tmp_path / "auth_settings.json"
    )
    monkeypatch.setattr(auth_settings, "_cached_auth_settings", settings)
    assert auth_settings.save_auth_settings(settings)
    return auth_settings, settings


@pytest.fixture(autouse=True)
def isolated_auth_settings(monkeypatch, tmp_path):
    """Keep auth file/cache state local to each setup integration test."""
    _configure_auth(monkeypatch, tmp_path, require_auth=True)


class TestSetupDetection:
    """Tests for setup status detection."""

    @pytest.mark.asyncio
    async def test_setup_required_returns_true_when_no_users(self, async_client):
        """GET /api/auth/setup-required returns {required: true} when no users exist."""
        # With empty database, setup should be required
        response = await async_client.get("/api/auth/setup-required")
        assert response.status_code == 200
        data = response.json()
        assert data["required"] is True

    @pytest.mark.asyncio
    async def test_setup_required_returns_false_when_users_exist(self, async_client):
        """GET /api/auth/setup-required returns {required: false} when users exist."""
        # First create a user via setup
        await async_client.post(
            "/api/auth/setup",
            json={
                "username": "admin",
                "email": "admin@example.com",
                "password": "SecurePass123!",
            },
        )

        # Now setup should not be required
        response = await async_client.get("/api/auth/setup-required")
        assert response.status_code == 200
        data = response.json()
        assert data["required"] is False

    @pytest.mark.asyncio
    async def test_setup_required_endpoint_is_public(self, async_client):
        """GET /api/auth/setup-required does not require authentication."""
        # Should work without any auth token
        response = await async_client.get("/api/auth/setup-required")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_protected_endpoints_reachable_before_setup(self, async_client):
        """Before first-run setup, protected endpoints are reachable (200), NOT gated.

        By design (docs/auth_middleware.md, RequireAuthIfEnabled semantics) the
        global auth middleware SKIPS enforcement while ``setup_complete`` is
        False — i.e. when no users exist — so a first-run operator can reach the
        app to complete setup. Setup-needed is signalled to the client via the
        dedicated public endpoint GET /api/auth/setup-required (see
        test_setup_required_returns_true_when_no_users), NOT via a 401/403 on
        protected routes.

        The old assertion accepted (200, 401, 403, 503) — almost any outcome —
        so it could never catch a regression. Assert the exact 200 the design
        guarantees: this fails if the middleware ever started gating routes
        before setup (which would lock a first-run operator out) or if the
        endpoint began 500/503-ing on a clean install.
        """
        response = await async_client.get("/api/settings")
        assert response.status_code == 200
        # Reachable and serving the real settings payload, not an error body.
        assert "configured" in response.json()


class TestAdminCreation:
    """Tests for initial admin creation via setup."""

    def test_setup_attempts_are_serialized_across_processes(
        self, monkeypatch, tmp_path
    ):
        """A peer process cannot race a second first-admin transaction."""
        from fastapi import HTTPException
        from auth import routes as auth_routes
        from auth import settings as auth_settings

        monkeypatch.setattr(auth_settings, "CONFIG_DIR", tmp_path)
        first = auth_routes._serialize_initial_setup()
        next(first)
        second = auth_routes._serialize_initial_setup()
        with pytest.raises(HTTPException) as error:
            next(second)
        assert error.value.status_code == 409
        first.close()

    def test_real_subprocess_contender_is_rejected(self, monkeypatch, tmp_path):
        from fastapi import HTTPException
        from auth import routes as auth_routes
        from auth import settings as auth_settings

        monkeypatch.setattr(auth_settings, "CONFIG_DIR", tmp_path)
        probe = """
import sys
from auth.routes import _serialize_initial_setup
guard = _serialize_initial_setup()
next(guard)
print('LOCKED', flush=True)
sys.stdin.readline()
guard.close()
"""
        env = os.environ.copy()
        env["CONFIG_DIR"] = str(tmp_path)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
        process = subprocess.Popen(
            [sys.executable, "-c", probe],
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            assert process.stdout.readline().strip() == "LOCKED"
            contender = auth_routes._serialize_initial_setup()
            with pytest.raises(HTTPException) as error:
                next(contender)
            assert error.value.status_code == 409
        finally:
            process.stdin.write("release\n")
            process.stdin.flush()
            assert process.wait(timeout=10) == 0
        released = auth_routes._serialize_initial_setup()
        next(released)
        released.close()

    def test_lock_cleanup_closes_fd_even_when_unlock_fails(
        self, monkeypatch, tmp_path
    ):
        from auth import routes as auth_routes
        from auth import settings as auth_settings

        monkeypatch.setattr(auth_settings, "CONFIG_DIR", tmp_path)
        real_flock = auth_routes.fcntl.flock
        real_close = auth_routes.os.close
        closed = []

        def fail_unlock(fd, operation):
            if operation == auth_routes.fcntl.LOCK_UN:
                raise OSError()
            return real_flock(fd, operation)

        def record_close(fd):
            closed.append(fd)
            return real_close(fd)

        monkeypatch.setattr(auth_routes.fcntl, "flock", fail_unlock)
        monkeypatch.setattr(auth_routes.os, "close", record_close)
        guard = auth_routes._serialize_initial_setup()
        next(guard)
        guard.close()
        assert len(closed) == 1

    @pytest.mark.asyncio
    async def test_setup_creates_first_admin_user(self, async_client):
        """POST /api/auth/setup creates first admin user."""
        response = await async_client.post(
            "/api/auth/setup",
            json={
                "username": "firstadmin",
                "email": "admin@example.com",
                "password": "SecurePass123!",
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert "user" in data
        assert data["user"]["username"] == "firstadmin"
        assert data["user"]["is_admin"] is True

    @pytest.mark.asyncio
    async def test_setup_immediately_closes_anonymous_api_window(
        self, async_client, monkeypatch, tmp_path
    ):
        """POST setup alone engages auth; no status read or reload repairs it."""
        auth_settings, _ = _configure_auth(
            monkeypatch, tmp_path, require_auth=True
        )

        setup_response = await async_client.post(
            "/api/auth/setup",
            json={
                "username": "firstadmin",
                "email": "admin@example.com",
                "password": "SecurePass123!",
            },
        )

        assert setup_response.status_code == 201
        persisted = json.loads(auth_settings.AUTH_CONFIG_FILE.read_text())
        assert persisted["setup_complete"] is True
        response = await async_client.get("/api/settings")
        assert response.status_code == 401
        backup_response = await async_client.get("/api/backup/create")
        assert backup_response.status_code == 401
        assert backup_response.headers.get("content-type") != "application/zip"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("failure_mode", ["invalid-json", "unreadable"])
    async def test_cold_process_with_durable_user_and_bad_settings_fails_closed(
        self, async_client, test_engine, monkeypatch, tmp_path, failure_mode
    ):
        """Cold-load failure cannot reopen an instance with durable ownership."""
        import database
        from auth import settings as auth_settings

        monkeypatch.setattr(database, "_SessionLocal", sessionmaker(bind=test_engine))
        assert (await async_client.post(
            "/api/auth/setup",
            json={
                "username": "firstadmin",
                "email": "admin@example.com",
                "password": "SecurePass123!",
            },
        )).status_code == 201

        if failure_mode == "invalid-json":
            auth_settings.AUTH_CONFIG_FILE.write_text("{")
        else:
            auth_settings.AUTH_CONFIG_FILE.unlink()
            auth_settings.AUTH_CONFIG_FILE.mkdir()
        monkeypatch.setattr(auth_settings, "_cached_auth_settings", None)
        monkeypatch.setattr(auth_settings, "_cached_auth_settings_signature", None)

        assert (await async_client.get("/api/settings")).status_code == 401
        backup = await async_client.get("/api/backup/create")
        assert backup.status_code == 401
        assert backup.headers.get("content-type") != "application/zip"

    @pytest.mark.asyncio
    async def test_cold_fresh_install_with_invalid_settings_can_safely_retry_setup(
        self, async_client, test_engine, monkeypatch, tmp_path
    ):
        """Proven absence keeps first-run setup reachable despite corrupt config."""
        import database
        from auth import settings as auth_settings

        monkeypatch.setattr(database, "_SessionLocal", sessionmaker(bind=test_engine))
        auth_settings.AUTH_CONFIG_FILE.write_text("{")
        monkeypatch.setattr(auth_settings, "_cached_auth_settings", None)
        monkeypatch.setattr(auth_settings, "_cached_auth_settings_signature", None)

        assert (await async_client.get("/api/settings")).status_code == 200
        setup = await async_client.post(
            "/api/auth/setup",
            json={
                "username": "firstadmin",
                "email": "admin@example.com",
                "password": "SecurePass123!",
            },
        )
        assert setup.status_code == 201
        assert (await async_client.get("/api/backup/create")).status_code == 401

    @pytest.mark.asyncio
    async def test_cold_process_with_durable_user_and_missing_settings_fails_closed(
        self, async_client, test_engine, monkeypatch, tmp_path
    ):
        """A missing settings file cannot erase durable ownership authority."""
        import database
        from auth import settings as auth_settings

        monkeypatch.setattr(database, "_SessionLocal", sessionmaker(bind=test_engine))
        assert (await async_client.post(
            "/api/auth/setup",
            json={
                "username": "firstadmin",
                "email": "admin@example.com",
                "password": "SecurePass123!",
            },
        )).status_code == 201
        auth_settings.AUTH_CONFIG_FILE.unlink()
        monkeypatch.setattr(auth_settings, "_cached_auth_settings", None)
        monkeypatch.setattr(auth_settings, "_cached_auth_settings_signature", None)

        assert (await async_client.get("/api/settings")).status_code == 401
        backup = await async_client.get("/api/backup/create")
        assert backup.status_code == 401
        assert backup.headers.get("content-type") != "application/zip"
        assert not auth_settings.AUTH_CONFIG_FILE.exists()

    @pytest.mark.asyncio
    async def test_cold_fresh_install_with_missing_settings_initializes_via_setup(
        self, async_client, test_engine, monkeypatch, tmp_path
    ):
        """Definitive user absence keeps setup reachable and repairs the file."""
        import database
        from auth import settings as auth_settings

        monkeypatch.setattr(database, "_SessionLocal", sessionmaker(bind=test_engine))
        auth_settings.AUTH_CONFIG_FILE.unlink()
        monkeypatch.setattr(auth_settings, "_cached_auth_settings", None)
        monkeypatch.setattr(auth_settings, "_cached_auth_settings_signature", None)

        assert (await async_client.get("/api/settings")).status_code == 200
        setup = await async_client.post(
            "/api/auth/setup",
            json={
                "username": "firstadmin",
                "email": "admin@example.com",
                "password": "SecurePass123!",
            },
        )
        assert setup.status_code == 201
        assert auth_settings.AUTH_CONFIG_FILE.is_file()
        assert (await async_client.get("/api/backup/create")).status_code == 401

    @pytest.mark.asyncio
    async def test_setup_preserves_intentionally_open_mode(
        self, async_client, monkeypatch, tmp_path
    ):
        """Completing setup never flips the operator's require_auth choice."""
        auth_settings, _ = _configure_auth(
            monkeypatch, tmp_path, require_auth=False
        )

        setup_response = await async_client.post(
            "/api/auth/setup",
            json={
                "username": "firstadmin",
                "email": "admin@example.com",
                "password": "SecurePass123!",
            },
        )

        assert setup_response.status_code == 201
        persisted = json.loads(auth_settings.AUTH_CONFIG_FILE.read_text())
        assert persisted["setup_complete"] is True
        assert persisted["require_auth"] is False
        response = await async_client.get("/api/settings")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_settings_persistence_failure_rolls_back_admin_creation(
        self, async_client, test_session, monkeypatch, tmp_path
    ):
        """A 201 can never create a user while setup_complete remains false."""
        from auth import routes as auth_routes
        from models import User

        auth_settings, settings = _configure_auth(
            monkeypatch, tmp_path, require_auth=True
        )
        real_save = auth_routes.save_auth_settings
        calls = 0

        def fail_once(candidate):
            nonlocal calls
            calls += 1
            return False if calls == 1 else real_save(candidate)

        monkeypatch.setattr(auth_routes, "save_auth_settings", fail_once)

        response = await async_client.post(
            "/api/auth/setup",
            json={
                "username": "firstadmin",
                "email": "admin@example.com",
                "password": "SecurePass123!",
            },
        )

        assert response.status_code == 500
        assert test_session.query(User).count() == 0
        persisted = json.loads(auth_settings.AUTH_CONFIG_FILE.read_text())
        assert persisted["setup_complete"] is False
        assert settings.setup_complete is False
        retry = await async_client.post(
            "/api/auth/setup",
            json={
                "username": "firstadmin",
                "email": "admin@example.com",
                "password": "SecurePass123!",
            },
        )
        assert retry.status_code == 201
        assert (await async_client.get("/api/backup/create")).status_code == 401

    @pytest.mark.asyncio
    async def test_settings_persistence_exception_rolls_back_admin_creation(
        self, async_client, test_session, monkeypatch, tmp_path, caplog
    ):
        """An unexpected settings write error fails setup without an admin."""
        from auth import routes as auth_routes
        from models import User

        auth_settings, settings = _configure_auth(
            monkeypatch, tmp_path, require_auth=True
        )

        real_save = auth_routes.save_auth_settings
        calls = 0

        def fail_once(candidate):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("synthetic settings failure")
            return real_save(candidate)

        monkeypatch.setattr(auth_routes, "save_auth_settings", fail_once)
        response = await async_client.post(
            "/api/auth/setup",
            json={
                "username": "firstadmin",
                "email": "admin@example.com",
                "password": "SecurePass123!",
            },
        )

        assert response.status_code == 500
        assert response.json()["detail"] == "Internal server error"
        assert "synthetic settings failure" not in caplog.text
        assert test_session.query(User).count() == 0
        persisted = json.loads(auth_settings.AUTH_CONFIG_FILE.read_text())
        assert persisted["setup_complete"] is False
        assert settings.setup_complete is False
        retry = await async_client.post(
            "/api/auth/setup",
            json={
                "username": "firstadmin",
                "email": "admin@example.com",
                "password": "SecurePass123!",
            },
        )
        assert retry.status_code == 201
        assert (await async_client.get("/api/backup/create")).status_code == 401

    @pytest.mark.asyncio
    async def test_database_commit_failure_restores_setup_flag(
        self, async_client, test_session, monkeypatch, tmp_path
    ):
        """A failed user transaction cannot leave an enabled gate with no admin."""
        from models import User

        auth_settings, settings = _configure_auth(
            monkeypatch, tmp_path, require_auth=True
        )

        real_commit = test_session.commit
        calls = 0

        def fail_once():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("synthetic database failure")
            return real_commit()

        monkeypatch.setattr(test_session, "commit", fail_once)
        response = await async_client.post(
            "/api/auth/setup",
            json={
                "username": "firstadmin",
                "email": "admin@example.com",
                "password": "SecurePass123!",
            },
        )

        assert response.status_code == 500
        assert test_session.query(User).count() == 0
        persisted = json.loads(auth_settings.AUTH_CONFIG_FILE.read_text())
        assert persisted["setup_complete"] is False
        assert settings.setup_complete is False
        retry = await async_client.post(
            "/api/auth/setup",
            json={
                "username": "firstadmin",
                "email": "admin@example.com",
                "password": "SecurePass123!",
            },
        )
        assert retry.status_code == 201
        assert (await async_client.get("/api/backup/create")).status_code == 401

    @pytest.mark.asyncio
    async def test_commit_that_succeeds_then_raises_keeps_gate_fail_closed(
        self, async_client, test_session, monkeypatch, tmp_path
    ):
        """An ambiguous commit outcome never reopens an admin-bearing instance."""
        from models import User

        auth_settings, _ = _configure_auth(monkeypatch, tmp_path, require_auth=True)
        real_commit = test_session.commit

        def commit_then_raise():
            real_commit()
            raise RuntimeError("synthetic ambiguous commit")

        monkeypatch.setattr(test_session, "commit", commit_then_raise)
        response = await async_client.post(
            "/api/auth/setup",
            json={
                "username": "firstadmin",
                "email": "admin@example.com",
                "password": "SecurePass123!",
            },
        )

        assert response.status_code == 500
        assert test_session.query(User).count() == 1
        persisted = json.loads(auth_settings.AUTH_CONFIG_FILE.read_text())
        assert persisted["setup_complete"] is True
        assert (await async_client.get("/api/backup/create")).status_code == 401

    @pytest.mark.asyncio
    async def test_failed_compensation_stays_closed_reloads_and_can_retry(
        self, async_client, test_session, monkeypatch, tmp_path
    ):
        """A failed false-write keeps durable true authority and remains recoverable."""
        from auth import routes as auth_routes
        from auth import settings as auth_settings_module
        from models import User

        auth_settings, _ = _configure_auth(monkeypatch, tmp_path, require_auth=True)
        stale_signature = auth_settings_module._cached_auth_settings_signature
        real_save = auth_routes.save_auth_settings
        save_calls = 0

        def fail_compensation(candidate):
            nonlocal save_calls
            save_calls += 1
            if save_calls == 2:
                return False
            return real_save(candidate)

        real_commit = test_session.commit
        commit_calls = 0

        def fail_commit_once():
            nonlocal commit_calls
            commit_calls += 1
            if commit_calls == 1:
                raise RuntimeError("synthetic pre-commit failure")
            return real_commit()

        monkeypatch.setattr(auth_routes, "save_auth_settings", fail_compensation)
        monkeypatch.setattr(test_session, "commit", fail_commit_once)
        response = await async_client.post(
            "/api/auth/setup",
            json={
                "username": "firstadmin",
                "email": "admin@example.com",
                "password": "SecurePass123!",
            },
        )

        assert response.status_code == 500
        assert test_session.query(User).count() == 0
        assert json.loads(auth_settings.AUTH_CONFIG_FILE.read_text())["setup_complete"] is True

        # Model the HTTPS peer's stale first-run cache. The changed inode must
        # force a reload before that process serves another request.
        monkeypatch.setattr(
            auth_settings_module,
            "_cached_auth_settings",
            auth_settings_module.AuthSettings(setup_complete=False, require_auth=True),
        )
        monkeypatch.setattr(
            auth_settings_module,
            "_cached_auth_settings_signature",
            stale_signature,
        )
        assert auth_settings_module.get_auth_settings().setup_complete is True

        retry = await async_client.post(
            "/api/auth/setup",
            json={
                "username": "firstadmin",
                "email": "admin@example.com",
                "password": "SecurePass123!",
            },
        )
        assert retry.status_code == 201
        assert (await async_client.get("/api/backup/create")).status_code == 401

    @pytest.mark.asyncio
    async def test_verification_query_failure_stays_closed_and_can_retry(
        self, async_client, test_session, monkeypatch, tmp_path
    ):
        """Unknown commit state never authorizes anonymous access."""
        from auth import routes as auth_routes

        _configure_auth(monkeypatch, tmp_path, require_auth=True)
        real_commit = test_session.commit
        commit_calls = 0

        def fail_once():
            nonlocal commit_calls
            commit_calls += 1
            if commit_calls == 1:
                raise RuntimeError("synthetic commit failure")
            return real_commit()

        class UnverifiableSession:
            def __init__(self, **_kwargs):
                raise RuntimeError("synthetic verification failure")

        monkeypatch.setattr(test_session, "commit", fail_once)
        monkeypatch.setattr(auth_routes, "Session", UnverifiableSession)
        response = await async_client.post(
            "/api/auth/setup",
            json={
                "username": "firstadmin",
                "email": "admin@example.com",
                "password": "SecurePass123!",
            },
        )
        assert response.status_code == 500
        assert (await async_client.get("/api/backup/create")).status_code == 401

        monkeypatch.undo()
        # Re-isolate after undo restored module paths/cache and retry normally.
        _configure_auth(monkeypatch, tmp_path, require_auth=True)
        retry = await async_client.post(
            "/api/auth/setup",
            json={
                "username": "firstadmin",
                "email": "admin@example.com",
                "password": "SecurePass123!",
            },
        )
        assert retry.status_code == 201
        assert (await async_client.get("/api/backup/create")).status_code == 401

    @pytest.mark.asyncio
    async def test_setup_only_works_when_no_users_exist(self, async_client):
        """POST /api/auth/setup only works when no users exist."""
        # First setup succeeds
        response1 = await async_client.post(
            "/api/auth/setup",
            json={
                "username": "admin",
                "email": "admin@example.com",
                "password": "SecurePass123!",
            },
        )
        assert response1.status_code == 201

        # Second setup should fail
        response2 = await async_client.post(
            "/api/auth/setup",
            json={
                "username": "anotheradmin",
                "email": "another@example.com",
                "password": "AnotherPass123!",
            },
        )
        assert response2.status_code == 403

    @pytest.mark.asyncio
    async def test_setup_returns_403_if_users_already_exist(self, async_client):
        """POST /api/auth/setup returns 403 if users already exist."""
        # Create first user
        await async_client.post(
            "/api/auth/setup",
            json={
                "username": "existingadmin",
                "email": "existing@example.com",
                "password": "ExistingPass123!",
            },
        )

        # Try setup again
        response = await async_client.post(
            "/api/auth/setup",
            json={
                "username": "newadmin",
                "email": "new@example.com",
                "password": "NewPass123!",
            },
        )
        assert response.status_code == 403
        assert "already" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_first_user_is_automatically_admin(self, async_client):
        """First user is automatically is_admin=True."""
        response = await async_client.post(
            "/api/auth/setup",
            json={
                "username": "thefirstuser",
                "email": "first@example.com",
                "password": "FirstPass123!",
            },
        )
        assert response.status_code == 201
        data = response.json()
        # First user via setup MUST be admin
        assert data["user"]["is_admin"] is True

    @pytest.mark.asyncio
    async def test_after_setup_normal_auth_works(self, async_client):
        """After setup, normal auth flow works."""
        # Complete setup
        setup_response = await async_client.post(
            "/api/auth/setup",
            json={
                "username": "setupadmin",
                "email": "setup@example.com",
                "password": "SetupPass123!",
            },
        )
        assert setup_response.status_code == 201

        # Now login should work
        login_response = await async_client.post(
            "/api/auth/login",
            json={
                "username": "setupadmin",
                "password": "SetupPass123!",
            },
        )
        assert login_response.status_code == 200
        assert "access_token" in login_response.cookies

    @pytest.mark.asyncio
    async def test_setup_validates_password_strength(self, async_client):
        """POST /api/auth/setup validates password strength."""
        response = await async_client.post(
            "/api/auth/setup",
            json={
                "username": "admin",
                "email": "admin@example.com",
                "password": "weak",
            },
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_setup_requires_all_fields(self, async_client):
        """POST /api/auth/setup requires username, email, and password."""
        # Missing username
        response = await async_client.post(
            "/api/auth/setup",
            json={
                "email": "admin@example.com",
                "password": "ValidPass123!",
            },
        )
        assert response.status_code == 422

        # Missing email
        response = await async_client.post(
            "/api/auth/setup",
            json={
                "username": "admin",
                "password": "ValidPass123!",
            },
        )
        assert response.status_code == 422

        # Missing password
        response = await async_client.post(
            "/api/auth/setup",
            json={
                "username": "admin",
                "email": "admin@example.com",
            },
        )
        assert response.status_code == 422
