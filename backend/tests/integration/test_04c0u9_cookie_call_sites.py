"""Every cookie-emitting auth route honours the TLS transport policy.

Bead enhancedchannelmanager-04c0u.9.

The helper-level unit tests in ``backend/tests/unit/test_04c0u9_session_transport.py``
prove the policy function, and ``backend/tests/unit/test_04c0u9_tls_settings_cache.py``
proves the cache layer beneath it. These prove the property that actually
protects an operator, driven through the real ASGI app over the plain-HTTP
transport the parallel listener serves:

* on a reverse-proxy deployment (an ``https://`` ``public_base_url``, where
  plaintext to ECM is legitimate), NO route that hands a browser a session
  cookie emits one without ``Secure`` — on any of the four call sites; and
* on an instance where ECM terminates TLS itself, those routes REFUSE rather
  than mint a session the browser will silently discard.

The completeness half — that a future call site cannot opt out — moved to
``backend/tests/unit/test_04c0u9_cookie_call_site_guard.py``, which is pure
source analysis with no I/O and now walks the whole backend rather than
``auth/routes.py`` alone.
"""

import re
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import urlsplit

import pytest

import auth.routes as auth_routes
from auth.settings import AuthSettings, DispatcharrAuthSettings, LocalAuthSettings
from auth.providers.dispatcharr import DispatcharrAuthResult


# Angle-bracket synthetic credentials, per docs/pytest_conventions.md
# "Credential Fixtures in Security Tests": the value is arbitrary here, so a
# templated placeholder keeps the secrets ratchet from seeing a candidate.
ADMIN_PASSWORD = "<synthetic-04c0u9-admin-password>"
DISPATCHARR_PASSWORD = "<synthetic-04c0u9-dispatcharr-password>"


def _tls_settings(**overrides):
    values = {
        "enabled": False,
        "allow_http_session_cookies": False,
        "domain": "ecm.test",
        "https_port": 6143,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture(autouse=True)
def _rearm_warn_once():
    auth_routes._reset_session_transport_log_state()
    yield
    auth_routes._reset_session_transport_log_state()


@pytest.fixture
def issued_tls_dir(tmp_path, monkeypatch):
    """Key material where ``tls/https_server.py`` starts the listener from."""
    tls_dir = tmp_path / "tls"
    tls_dir.mkdir()
    (tls_dir / "cert.pem").write_text("certificate fixture")
    (tls_dir / "key.pem").write_text("private-key fixture")
    monkeypatch.setattr(auth_routes, "TLS_DIR", tls_dir)
    return tls_dir


@pytest.fixture
def empty_tls_dir(tmp_path, monkeypatch):
    tls_dir = tmp_path / "tls-empty"
    tls_dir.mkdir()
    monkeypatch.setattr(auth_routes, "TLS_DIR", tls_dir)
    return tls_dir


@pytest.fixture
def behind_https_proxy(empty_tls_dir):
    """A reverse proxy terminates TLS and declared its origin (bead qsqfv).

    This is the configuration where a plaintext request to ECM is LEGITIMATE —
    it is the proxy's back-channel — so the routes serve, and the property under
    test is the cookie attributes. The ECM-terminates-TLS configuration is
    covered by the refusal tests below, where these routes serve nothing at all.
    """
    with patch("auth.routes.get_tls_settings", return_value=_tls_settings()), \
         patch("auth.routes.tls_settings_load_failed", return_value=False), \
         patch("auth.routes.get_public_base_url", return_value="https://ecm.example.test"):
        yield


@pytest.fixture
def ecm_terminates_tls(issued_tls_dir):
    """ECM's own HTTPS listener is serving and break-glass is closed."""
    with patch(
        "auth.routes.get_tls_settings", return_value=_tls_settings(enabled=True)
    ), patch("auth.routes.tls_settings_load_failed", return_value=False), \
         patch("auth.routes.get_public_base_url", return_value=""):
        yield


@pytest.fixture
def break_glass_open(issued_tls_dir):
    """ECM terminates TLS but the operator opened the recovery hatch."""
    with patch(
        "auth.routes.get_tls_settings",
        return_value=_tls_settings(enabled=True, allow_http_session_cookies=True),
    ), patch("auth.routes.tls_settings_load_failed", return_value=False), \
         patch("auth.routes.get_public_base_url", return_value=""):
        yield


@pytest.fixture
def admin_user(test_session):
    from models import User
    from auth.password import hash_password

    user = User(
        username="admin",
        email="admin@example.com",
        password_hash=hash_password(ADMIN_PASSWORD),
        auth_provider="local",
        is_admin=True,
        is_active=True,
    )
    test_session.add(user)
    test_session.commit()
    test_session.refresh(user)
    return user


def _cookie_value(response, name):
    """Read a Set-Cookie value straight off the response.

    Deliberately NOT ``response.cookies``/the client jar: once the fix is in,
    these cookies are ``Secure``, so httpx correctly refuses to store or replay
    them over the plain-HTTP transport. Reading the header keeps the test
    exercising the server rather than re-proving httpx's own rule.
    """
    for header in response.headers.get_list("set-cookie"):
        key, _, rest = header.partition("=")
        if key == name:
            return rest.split(";", 1)[0]
    raise AssertionError(f"no {name} cookie in {response.headers.get_list('set-cookie')}")


def _session_cookies(response):
    """Set-Cookie headers that carry session credentials, not deletions."""
    emitted = [
        header
        for header in response.headers.get_list("set-cookie")
        if header.split("=", 1)[0] in {"access_token", "refresh_token"}
    ]
    return [header for header in emitted if "Max-Age=0" not in header]


def _assert_protected(response, expected_count):
    cookies = _session_cookies(response)
    assert len(cookies) == expected_count, cookies
    for cookie in cookies:
        assert "Secure" in cookie, cookie
        assert "HttpOnly" in cookie, cookie
        assert "samesite=lax" in cookie.lower(), cookie


def _dispatcharr_patches():
    settings = MagicMock(spec=AuthSettings)
    settings.dispatcharr = MagicMock(spec=DispatcharrAuthSettings)
    settings.dispatcharr.enabled = True
    settings.local = MagicMock(spec=LocalAuthSettings)
    settings.local.enabled = True
    settings.oidc = MagicMock(enabled=False)
    settings.saml = MagicMock(enabled=False)
    settings.ldap = MagicMock(enabled=False)
    settings.jwt = MagicMock()
    settings.jwt.refresh_token_expire_days = 7

    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.authenticate = AsyncMock(
        return_value=DispatcharrAuthResult(
            user_id="disp-04c0u9",
            username="dispuser",
            email="dispuser@dispatcharr.local",
        )
    )
    return settings, client


# ---------------------------------------------------------------------------
# Cookie attributes at every call site
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_over_http_emits_protected_cookies(
    async_client, admin_user, behind_https_proxy
):
    """Call site 1: POST /api/auth/login."""
    response = await async_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": ADMIN_PASSWORD},
    )
    assert response.status_code == 200
    _assert_protected(response, 2)


@pytest.mark.asyncio
async def test_refresh_over_http_emits_protected_cookies(
    async_client, admin_user, behind_https_proxy
):
    """Call site 2: POST /api/auth/refresh, current-token path."""
    login = await async_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": ADMIN_PASSWORD},
    )
    assert login.status_code == 200

    refreshed = await async_client.post(
        "/api/auth/refresh",
        cookies={"refresh_token": _cookie_value(login, "refresh_token")},
    )
    assert refreshed.status_code == 200
    _assert_protected(refreshed, 2)


@pytest.mark.asyncio
async def test_predecessor_refresh_over_http_emits_protected_cookie(
    async_client, admin_user, test_session, behind_https_proxy
):
    """Call site 3: the predecessor branch of POST /api/auth/refresh."""
    from models import UserSession

    login = await async_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": ADMIN_PASSWORD},
    )
    assert login.status_code == 200
    pre_rotation = _cookie_value(login, "refresh_token")

    rotated = await async_client.post(
        "/api/auth/refresh", cookies={"refresh_token": pre_rotation}
    )
    assert rotated.status_code == 200

    test_session.expire_all()
    row = (
        test_session.query(UserSession)
        .filter(UserSession.user_id == admin_user.id)
        .one()
    )
    row.rotated_at = datetime.utcnow() - timedelta(hours=6)
    test_session.commit()

    from_predecessor = await async_client.post(
        "/api/auth/refresh", cookies={"refresh_token": pre_rotation}
    )
    assert from_predecessor.status_code == 200
    # This branch re-mints the access token only; the refresh token is not
    # rotated again, so exactly one session cookie is emitted.
    _assert_protected(from_predecessor, 1)


@pytest.mark.asyncio
async def test_dispatcharr_login_over_http_emits_protected_cookies(
    async_client, behind_https_proxy
):
    """Call site 4: POST /api/auth/dispatcharr/login."""
    settings, client = _dispatcharr_patches()

    with patch("auth.routes.get_auth_settings", return_value=settings), \
         patch("auth.providers.dispatcharr.DispatcharrClient", return_value=client):
        response = await async_client.post(
            "/api/auth/dispatcharr/login",
            json={"username": "dispuser", "password": DISPATCHARR_PASSWORD},
        )

    assert response.status_code == 200
    _assert_protected(response, 2)


@pytest.mark.asyncio
async def test_logout_deletion_mirrors_issue_time_attributes(
    async_client, admin_user, behind_https_proxy
):
    """Nothing covered logout cookie attributes at all before this.

    Starlette's ``delete_cookie`` defaults to ``secure=False, httponly=False``.
    Deletion matches on (name, domain, path) so it worked, but RFC 6265bis 8.6
    says a non-secure origin cannot clear a ``Secure`` cookie, and the asymmetry
    is a trap for the next change here.
    """
    login = await async_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": ADMIN_PASSWORD},
    )
    assert login.status_code == 200

    response = await async_client.post(
        "/api/auth/logout",
        cookies={"refresh_token": _cookie_value(login, "refresh_token")},
    )
    assert response.status_code == 200

    deletions = [
        header
        for header in response.headers.get_list("set-cookie")
        if header.split("=", 1)[0] in {"access_token", "refresh_token"}
    ]
    assert len(deletions) == 2, deletions
    for cookie in deletions:
        assert "Max-Age=0" in cookie, cookie
        assert "Secure" in cookie, cookie
        assert "HttpOnly" in cookie, cookie
        assert "samesite=lax" in cookie.lower(), cookie


# ---------------------------------------------------------------------------
# Refusing to mint a session over cleartext (PO-authorised behaviour change)
# ---------------------------------------------------------------------------


def _sign_in_url(detail: str):
    """The recovery address the refusal message directs the operator to.

    Parsed rather than substring-matched, for the reason given on the twin
    helper in ``backend/tests/unit/test_04c0u9_session_transport.py``: a
    substring assertion on the URL also passes for a wrong port
    (``:61430``) and for an attacker-suffixed host.
    """
    match = re.search(r"Sign in at (\S+) instead\.", detail)
    assert match is not None, f"no sign-in directive in refusal message: {detail!r}"
    return urlsplit(match.group(1))


@pytest.mark.asyncio
async def test_plaintext_login_is_refused_when_ecm_terminates_tls(
    async_client, admin_user, test_session, ecm_terminates_tls
):
    """This used to answer 200 and create a session row.

    The browser then discarded the ``Secure`` cookie (RFC 6265bis 5.6), the SPA
    resolved on the 200 and fired ``onLoginSuccess``, the first API call 401'd,
    and the operator was bounced back to the login form with no error shown —
    so the natural response was to retry and ship the cleartext password again.
    """
    from models import UserSession

    response = await async_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": ADMIN_PASSWORD},
    )

    assert response.status_code == 403
    detail = response.json()["detail"]
    sign_in = _sign_in_url(detail)
    assert (sign_in.scheme, sign_in.netloc) == ("https", "ecm.test:6143")
    assert "ECM_ALLOW_HTTP_SESSION_COOKIES" in detail
    assert not _session_cookies(response)
    # Refused BEFORE the password is read, so no session row is created.
    assert test_session.query(UserSession).count() == 0


@pytest.mark.asyncio
async def test_plaintext_refresh_is_refused_when_ecm_terminates_tls(
    async_client, admin_user, ecm_terminates_tls
):
    response = await async_client.post(
        "/api/auth/refresh", cookies={"refresh_token": "irrelevant"}
    )
    assert response.status_code == 403
    assert not _session_cookies(response)


@pytest.mark.asyncio
async def test_plaintext_dispatcharr_login_is_refused_when_ecm_terminates_tls(
    async_client, ecm_terminates_tls
):
    settings, client = _dispatcharr_patches()

    with patch("auth.routes.get_auth_settings", return_value=settings), \
         patch("auth.providers.dispatcharr.DispatcharrClient", return_value=client):
        response = await async_client.post(
            "/api/auth/dispatcharr/login",
            json={"username": "dispuser", "password": DISPATCHARR_PASSWORD},
        )

    assert response.status_code == 403
    assert not _session_cookies(response)
    # Refused before the upstream is contacted at all.
    client.authenticate.assert_not_awaited()


@pytest.mark.asyncio
async def test_break_glass_restores_plaintext_login(
    async_client, admin_user, break_glass_open
):
    """The escape hatch must actually recover an operator, end to end."""
    response = await async_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": ADMIN_PASSWORD},
    )

    assert response.status_code == 200
    cookies = _session_cookies(response)
    assert len(cookies) == 2
    assert all("Secure" not in cookie for cookie in cookies), cookies


@pytest.mark.asyncio
async def test_environment_break_glass_restores_plaintext_login(
    async_client, admin_user, ecm_terminates_tls, monkeypatch
):
    monkeypatch.setenv("ECM_ALLOW_HTTP_SESSION_COOKIES", "true")

    response = await async_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": ADMIN_PASSWORD},
    )

    assert response.status_code == 200
    assert all("Secure" not in cookie for cookie in _session_cookies(response))
