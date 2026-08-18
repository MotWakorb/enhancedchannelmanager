"""Every cookie-emitting auth route honours the TLS transport policy.

Bead enhancedchannelmanager-04c0u.9. The helper-level unit tests in
``backend/tests/unit/test_04c0u9_session_transport.py`` prove the policy
function. These prove the property that actually protects an operator: with
ECM TLS activated, NO route that hands a browser a session cookie emits one
without ``Secure``, on any of the four call sites, driven through the real
ASGI app over the plain-HTTP transport the parallel listener serves.

The AST guard at the bottom is the completeness half: it fails if a future
call site is added that does not pass the request through, which the four
behavioural tests cannot see.
"""

import ast
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from auth.settings import AuthSettings, DispatcharrAuthSettings, LocalAuthSettings
from auth.providers.dispatcharr import DispatcharrAuthResult


# Angle-bracket synthetic credentials, per docs/pytest_conventions.md
# "Credential Fixtures in Security Tests": the value is arbitrary here, so a
# templated placeholder keeps the secrets ratchet from seeing a candidate.
ADMIN_PASSWORD = "<synthetic-04c0u9-admin-password>"
DISPATCHARR_PASSWORD = "<synthetic-04c0u9-dispatcharr-password>"

AUTH_ROUTES_SOURCE = Path(__file__).resolve().parents[2] / "auth" / "routes.py"
COOKIE_HELPERS = {"_set_access_cookie", "_set_auth_cookies"}


@pytest.fixture
def tls_active(tmp_path):
    """ECM TLS enabled with real key material, break-glass off."""
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.write_text("certificate fixture")
    key.write_text("private-key fixture")
    tls = SimpleNamespace(
        enabled=True,
        allow_http_session_cookies=False,
        cert_path=str(cert),
        key_path=str(key),
    )
    with patch("auth.routes.get_tls_settings", return_value=tls), \
         patch("auth.routes.get_public_base_url", return_value=""):
        yield tls


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
        assert "SameSite=lax" in cookie.replace("SameSite=Lax", "SameSite=lax"), cookie


@pytest.mark.asyncio
async def test_login_over_http_emits_protected_cookies(async_client, admin_user, tls_active):
    """Call site 1: POST /api/auth/login."""
    response = await async_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": ADMIN_PASSWORD},
    )
    assert response.status_code == 200
    _assert_protected(response, 2)


@pytest.mark.asyncio
async def test_refresh_over_http_emits_protected_cookies(async_client, admin_user, tls_active):
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
    async_client, admin_user, test_session, tls_active
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
async def test_dispatcharr_login_over_http_emits_protected_cookies(async_client, tls_active):
    """Call site 4: POST /api/auth/dispatcharr/login."""
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

    with patch("auth.routes.get_auth_settings", return_value=settings), \
         patch("auth.providers.dispatcharr.DispatcharrClient", return_value=client):
        response = await async_client.post(
            "/api/auth/dispatcharr/login",
            json={"username": "dispuser", "password": DISPATCHARR_PASSWORD},
        )

    assert response.status_code == 200
    _assert_protected(response, 2)


def test_every_cookie_helper_call_site_passes_the_request():
    """Completeness guard: no call site may omit the transport context.

    A new route that calls a helper without threading its ``Request`` would
    hand a browser an unprotected cookie. The helpers take ``request`` with
    no default so such a call raises, but a caller could still pass ``None``
    or a literal; this pins the argument to a name.
    """
    tree = ast.parse(AUTH_ROUTES_SOURCE.read_text())
    call_sites = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in COOKIE_HELPERS
    ]
    # Four route call sites plus the internal hop inside _set_auth_cookies.
    assert len(call_sites) == 5, [
        (node.func.id, node.lineno) for node in call_sites
    ]
    for node in call_sites:
        supplied = [
            arg for arg in node.args
            if isinstance(arg, ast.Name) and arg.id == "request"
        ] + [
            kw.value for kw in node.keywords
            if kw.arg == "request"
            and isinstance(kw.value, ast.Name)
            and kw.value.id == "request"
        ]
        assert supplied, (
            f"{node.func.id} at line {node.lineno} does not pass the request"
        )


def test_cookie_helpers_have_no_default_request():
    """A defaulted ``request`` would let a new call site silently opt out."""
    tree = ast.parse(AUTH_ROUTES_SOURCE.read_text())
    checked = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in COOKIE_HELPERS:
            names = [arg.arg for arg in node.args.args]
            assert "request" in names, node.name
            # Defaults bind to the tail of args; request must not be in it.
            defaulted = names[len(names) - len(node.args.defaults):] if node.args.defaults else []
            assert "request" not in defaulted, node.name
            checked.add(node.name)
    assert checked == COOKIE_HELPERS
