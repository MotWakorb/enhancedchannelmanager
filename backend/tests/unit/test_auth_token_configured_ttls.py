"""bd-suuoh — the token issuer must honor the CONFIGURED JWT lifetimes.

``AuthSettings.jwt.access_token_expire_minutes`` and
``.refresh_token_expire_days`` are persisted in ``auth_settings.json`` and are
already consumed by three places:

* ``_set_access_cookie`` / ``_set_auth_cookies`` -> cookie ``max_age``
* the login/setup handlers -> ``UserSession.expires_at``
* ``_access_token_lifetime_seconds`` -> the ``access_token_expires_in``
  metadata the frontend uses to schedule its proactive refresh (bd-3ymo4)

but ``create_access_token`` / ``create_refresh_token`` ignored both and baked
in the module constants instead. An operator who edits either value gets a
half-applied change, and one direction of the refresh mismatch reproduces the
bead's own 401: when the configured lifetime is SHORTER than the baked-in 7
days, ``UserSession.expires_at`` lands before the JWT's ``exp``,
``_cleanup_expired_sessions`` deletes the row while the cookie still decodes,
and the next refresh answers 401 "Session not found or revoked" with a token
the client had every reason to believe was good.

The module constants stay as the fallback for the case ``_get_secret_key``
already handles: settings genuinely unreadable (ImportError/OSError).
"""
from datetime import timedelta

import pytest

from auth import tokens


class _StubJwt:
    def __init__(self, access_minutes: int, refresh_days: int):
        # get_jwt_secret_key() reads the same settings object the lifetimes
        # come from, so the stub has to carry a signing key too.
        self.secret_key = "bd-suuoh-unit-test-signing-key"
        self.access_token_expire_minutes = access_minutes
        self.refresh_token_expire_days = refresh_days


class _StubSettings:
    def __init__(self, access_minutes: int, refresh_days: int):
        self.jwt = _StubJwt(access_minutes, refresh_days)


@pytest.fixture
def configured_ttls(monkeypatch):
    """Point the token issuer at explicit, non-default configured lifetimes."""

    def _apply(access_minutes: int, refresh_days: int):
        import auth.settings as auth_settings_module

        monkeypatch.setattr(
            auth_settings_module,
            "get_auth_settings",
            lambda: _StubSettings(access_minutes, refresh_days),
        )

    return _apply


def _lifetime_seconds(token: str) -> int:
    claims = tokens.decode_token(token)
    return int(claims["exp"] - claims["iat"])


class TestConfiguredAccessTokenLifetime:
    def test_access_token_uses_configured_minutes(self, configured_ttls):
        configured_ttls(access_minutes=90, refresh_days=7)
        token = tokens.create_access_token(user_id=1, username="operator")
        assert _lifetime_seconds(token) == 90 * 60

    def test_explicit_expires_delta_still_wins(self, configured_ttls):
        """Callers that pass a delta keep full control (used by tests)."""
        configured_ttls(access_minutes=90, refresh_days=7)
        token = tokens.create_access_token(
            user_id=1, username="operator", expires_delta=timedelta(minutes=5)
        )
        assert _lifetime_seconds(token) == 5 * 60

    def test_falls_back_to_constant_when_settings_unreadable(self, monkeypatch):
        import auth.settings as auth_settings_module

        def _boom():
            raise OSError("config directory unreadable")

        monkeypatch.setattr(auth_settings_module, "get_auth_settings", _boom)
        token = tokens.create_access_token(user_id=1, username="operator")
        assert _lifetime_seconds(token) == tokens.ACCESS_TOKEN_EXPIRE_MINUTES * 60


class TestConfiguredRefreshTokenLifetime:
    def test_refresh_token_uses_configured_days(self, configured_ttls):
        configured_ttls(access_minutes=30, refresh_days=30)
        token = tokens.create_refresh_token(user_id=1)
        assert _lifetime_seconds(token) == 30 * 24 * 60 * 60

    def test_shorter_configured_lifetime_is_applied(self, configured_ttls):
        """The direction that reproduces bd-suuoh's 401 if left unapplied.

        With a 1-day configured lifetime the session row expires after a day.
        The JWT must expire with it — if the JWT outlived the row the client
        would present a decodable token against a deleted session and get
        401 "Session not found or revoked" instead of a clean re-login.
        """
        configured_ttls(access_minutes=30, refresh_days=1)
        token = tokens.create_refresh_token(user_id=1)
        assert _lifetime_seconds(token) == 24 * 60 * 60

    def test_falls_back_to_constant_when_settings_unreadable(self, monkeypatch):
        import auth.settings as auth_settings_module

        def _boom():
            raise OSError("config directory unreadable")

        monkeypatch.setattr(auth_settings_module, "get_auth_settings", _boom)
        token = tokens.create_refresh_token(user_id=1)
        assert _lifetime_seconds(token) == tokens.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60


class TestRotationCarriesTheRealUsername:
    """``rotate_refresh_token`` fabricated ``username="user_<id>"``.

    That claim is not used for authorization (``get_current_user`` resolves
    the caller from ``sub``), but ``main.py``'s deprecated-admin-router
    warning logs it verbatim as the acting operator. Before this fix, every
    request made after a token refresh was attributed to "user_2" in that
    security log rather than to the real account.
    """

    def test_rotated_access_token_keeps_the_real_username(self):
        original = tokens.create_refresh_token(user_id=7)
        access_token, _ = tokens.rotate_refresh_token(original, username="operator")
        assert tokens.decode_token(access_token)["username"] == "operator"

    def test_rotation_without_username_is_still_supported(self):
        original = tokens.create_refresh_token(user_id=8)
        access_token, refresh_token = tokens.rotate_refresh_token(original)
        assert tokens.decode_token(access_token)["sub"] == 8
        assert tokens.decode_token(refresh_token)["type"] == "refresh"
