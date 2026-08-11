"""
Unit tests for JWT secret-key handling (bd-0gt2i / GH #546 latent bugs).

Covers:
- auth.tokens._get_secret_key: narrow exception handling — only
  ImportError/OSError fall back to the per-process random secret (loudly);
  unexpected exceptions propagate instead of silently invalidating sessions.
- auth.settings: the load/save/generate paths are synchronized so
  concurrent callers can never observe two different generated secrets.
"""
import logging
import threading

import pytest

import auth.settings as auth_settings
import auth.tokens as auth_tokens
from auth.settings import AuthSettings


@pytest.fixture
def isolated_auth_settings(tmp_path, monkeypatch):
    """Point auth settings at a temp dir and reset all module caches."""
    monkeypatch.setattr(auth_settings, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(auth_settings, "AUTH_CONFIG_FILE", tmp_path / "auth_settings.json")
    monkeypatch.setattr(auth_settings, "_cached_auth_settings", None)
    monkeypatch.setattr(auth_settings, "_cached_auth_settings_signature", None)
    monkeypatch.setattr(auth_settings, "_durable_user_exists", lambda: False)
    monkeypatch.setattr(auth_tokens, "_fallback_logged", False)
    yield auth_settings


class TestGetSecretKeyFallback:
    """auth.tokens._get_secret_key exception narrowing and loudness."""

    def test_returns_settings_key_when_available(self, isolated_auth_settings, monkeypatch):
        monkeypatch.setattr(
            auth_settings, "get_jwt_secret_key", lambda: "settings-secret"
        )
        assert auth_tokens._get_secret_key() == "settings-secret"

    @pytest.mark.parametrize("exc", [ImportError("no module"), OSError("disk gone")])
    def test_expected_errors_fall_back_to_default_secret(
        self, isolated_auth_settings, monkeypatch, caplog, exc
    ):
        def raiser():
            raise exc

        monkeypatch.setattr(auth_settings, "get_jwt_secret_key", raiser)

        with caplog.at_level(logging.ERROR, logger="auth.tokens"):
            key = auth_tokens._get_secret_key()

        assert key == auth_tokens._DEFAULT_SECRET_KEY
        # The fallback must be loud: ERROR level, names the exception, and
        # states the consequence (sessions invalidated).
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 1
        msg = errors[0].getMessage()
        assert type(exc).__name__ in msg
        assert "RANDOM" in msg
        assert "sessions" in msg

    def test_fallback_logs_error_once_then_warning(
        self, isolated_auth_settings, monkeypatch, caplog
    ):
        def raiser():
            raise OSError("disk gone")

        monkeypatch.setattr(auth_settings, "get_jwt_secret_key", raiser)

        with caplog.at_level(logging.WARNING, logger="auth.tokens"):
            auth_tokens._get_secret_key()
            auth_tokens._get_secret_key()

        levels = [r.levelno for r in caplog.records]
        assert levels.count(logging.ERROR) == 1
        assert levels.count(logging.WARNING) == 1

    def test_unexpected_exception_propagates(self, isolated_auth_settings, monkeypatch):
        def raiser():
            raise RuntimeError("bug in settings layer")

        monkeypatch.setattr(auth_settings, "get_jwt_secret_key", raiser)

        with pytest.raises(RuntimeError, match="bug in settings layer"):
            auth_tokens._get_secret_key()


class TestSecretKeyGeneration:
    """auth.settings.get_jwt_secret_key basic contracts."""

    def test_generates_and_persists_secret_on_first_run(self, isolated_auth_settings):
        key = auth_settings.get_jwt_secret_key()
        assert key
        # Persisted to the config file.
        assert auth_settings.AUTH_CONFIG_FILE.exists()
        assert key in auth_settings.AUTH_CONFIG_FILE.read_text()
        # Stable across calls.
        assert auth_settings.get_jwt_secret_key() == key

    def test_generates_secret_when_cached_settings_have_empty_key(
        self, isolated_auth_settings, monkeypatch
    ):
        settings = AuthSettings()  # jwt.secret_key defaults to ""
        monkeypatch.setattr(auth_settings, "_cached_auth_settings", settings)

        key = auth_settings.get_jwt_secret_key()
        assert key
        assert settings.jwt.secret_key == key


class TestSecretKeyConcurrency:
    """Concurrent callers must all observe ONE secret (bd-0gt2i latent bug:
    unsynchronized generate-and-save produced divergent secrets => 401s)."""

    N_THREADS = 16

    def _run_threads(self, fn):
        barrier = threading.Barrier(self.N_THREADS)
        results = []
        errors = []

        def worker():
            barrier.wait()
            try:
                results.append(fn())
            except Exception as e:  # pragma: no cover — failure diagnostics
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(self.N_THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors, f"worker exceptions: {errors}"
        assert len(results) == self.N_THREADS
        return results

    def test_concurrent_get_jwt_secret_key_returns_single_secret(
        self, isolated_auth_settings, monkeypatch
    ):
        # Cached settings with an empty secret — the exact race window.
        monkeypatch.setattr(auth_settings, "_cached_auth_settings", AuthSettings())

        results = self._run_threads(auth_settings.get_jwt_secret_key)

        assert len(set(results)) == 1
        # And the persisted file agrees with what every caller saw.
        assert results[0] in auth_settings.AUTH_CONFIG_FILE.read_text()

    def test_concurrent_first_load_yields_single_settings_instance(
        self, isolated_auth_settings
    ):
        results = self._run_threads(auth_settings.load_auth_settings)

        assert len({id(s) for s in results}) == 1
        secrets_seen = {s.jwt.secret_key for s in results}
        assert len(secrets_seen) == 1
        assert secrets_seen != {""}
