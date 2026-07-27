"""
Unit tests for the static MCP API key path in auth dependencies.

TDD SPEC (bd-1wq7z.1): Routes guarded by ``RequireAuthIfEnabled`` /
``RequireAdminIfEnabled`` call ``get_current_user`` -> ``decode_token``,
which rejects the static ``mcp_api_key`` (it is not a 3-part JWT) with
401 "Malformed token". The global ``auth_middleware`` already accepts
this same static key for any ``/api/*`` path, so the route-dependency
layer must honor it too. ``get_current_user`` recognizes the configured
``mcp_api_key`` BEFORE attempting JWT decode and returns an
admin-equivalent, non-persisted service principal.

These tests pin the behavior:
- The static key passes both ``RequireAuthIfEnabled`` and
  ``RequireAdminIfEnabled`` and yields ``is_admin=True``.
- A real admin JWT still works unchanged.
- Malformed / expired / revoked / missing tokens still 401 exactly as
  before.
- An unset/empty ``mcp_api_key`` accepts neither an empty token nor an
  arbitrary token.
- The comparison is constant-time (``hmac.compare_digest``) and correct
  for both match and non-match.
"""
import pytest

from fastapi import HTTPException

from auth import dependencies as deps
from auth.dependencies import (
    get_current_user,
    get_current_active_admin,
    require_auth_if_enabled,
    require_admin_if_enabled,
)
from auth.settings import AuthSettings
from auth.tokens import create_access_token, revoke_token, _get_secret_key
from models import User


# --------------------------------------------------------------------------
# Helpers / fakes
# --------------------------------------------------------------------------

MCP_KEY = "mcp-static-secret-key-abcdef123456"


class _FakeRequest:
    """Minimal stand-in for fastapi.Request that exposes cookies + headers.

    ``get_token_from_request`` only touches ``request.cookies`` and
    ``request.headers``, so a tiny shim is enough and keeps these tests
    fast and dependency-light.
    """

    def __init__(self, *, bearer=None, cookie=None):
        self.cookies = {}
        if cookie is not None:
            self.cookies["access_token"] = cookie
        self.headers = {}
        if bearer is not None:
            self.headers["Authorization"] = f"Bearer {bearer}"


class _ExplodingSession:
    """A session whose ``.query`` blows up.

    The service-principal path must NOT touch the database — if it does,
    these tests fail loudly rather than silently inserting/loading a row.
    """

    def query(self, *args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError(
            "service principal must not query the database"
        )


def _auth_enabled():
    return AuthSettings(setup_complete=True, require_auth=True)


@pytest.fixture
def mcp_key_configured(monkeypatch):
    """Configure a non-empty static MCP key on the config settings object."""
    class _Settings:
        mcp_api_key = MCP_KEY

    monkeypatch.setattr(deps, "get_settings", lambda: _Settings(), raising=False)
    return MCP_KEY


@pytest.fixture
def mcp_key_unset(monkeypatch):
    """Configure an EMPTY static MCP key (the not-configured state)."""
    class _Settings:
        mcp_api_key = ""

    monkeypatch.setattr(deps, "get_settings", lambda: _Settings(), raising=False)


@pytest.fixture
def auth_enabled(monkeypatch):
    """Force auth ON so the *_if_enabled guards actually enforce."""
    monkeypatch.setattr(deps, "get_auth_settings", _auth_enabled)


# --------------------------------------------------------------------------
# 1 + 2. Static MCP key passes both guards as an admin service principal
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mcp_key_returns_admin_service_principal(mcp_key_configured):
    """get_current_user(static key) returns an admin-equivalent principal."""
    req = _FakeRequest(bearer=MCP_KEY)
    user = await get_current_user(req, session=_ExplodingSession())

    assert isinstance(user, User)
    assert user.is_admin is True
    assert user.is_active is True
    assert user.username == "mcp-service"
    # An id must be present (callers expect .id) and must not be a real row.
    assert user.id is not None


@pytest.mark.asyncio
async def test_mcp_key_passes_require_admin_if_enabled(
    mcp_key_configured, auth_enabled
):
    """RequireAdminIfEnabled accepts the static MCP key -> admin principal."""
    check_admin = require_admin_if_enabled()
    req = _FakeRequest(bearer=MCP_KEY)

    user = await check_admin(req, session=_ExplodingSession())

    assert user is not None
    assert user.is_admin is True
    assert user.username == "mcp-service"


@pytest.mark.asyncio
async def test_mcp_key_passes_require_auth_if_enabled(
    mcp_key_configured, auth_enabled
):
    """RequireAuthIfEnabled accepts the static MCP key."""
    check_auth = require_auth_if_enabled()
    req = _FakeRequest(bearer=MCP_KEY)

    user = await check_auth(req, session=_ExplodingSession())

    assert user is not None
    assert user.is_admin is True


@pytest.mark.asyncio
async def test_mcp_key_passes_get_current_active_admin(mcp_key_configured):
    """get_current_active_admin admits the service principal (is_admin)."""
    req = _FakeRequest(bearer=MCP_KEY)
    principal = await get_current_user(req, session=_ExplodingSession())
    admin = await get_current_active_admin(principal)
    assert admin.is_admin is True


@pytest.mark.asyncio
async def test_mcp_key_via_cookie_also_accepted(mcp_key_configured):
    """The static key is honored from the access_token cookie too.

    ``get_token_from_request`` checks cookie first, then bearer; the MCP
    path should not depend on which transport delivered the token.
    """
    req = _FakeRequest(cookie=MCP_KEY)
    user = await get_current_user(req, session=_ExplodingSession())
    assert user.username == "mcp-service"


# --------------------------------------------------------------------------
# 3. A real admin JWT still works, unchanged (DB-backed identity)
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_real_admin_jwt_still_works(mcp_key_configured, test_session):
    """A valid JWT for a real admin loads the real DB user, not the principal."""
    user = User(
        username="real-admin",
        email="real-admin@example.com",
        auth_provider="local",
        is_admin=True,
        is_active=True,
    )
    test_session.add(user)
    test_session.commit()
    test_session.refresh(user)

    token = create_access_token(user_id=user.id, username=user.username)
    req = _FakeRequest(bearer=token)

    loaded = await get_current_user(req, session=test_session)
    assert loaded.id == user.id
    assert loaded.username == "real-admin"
    # Proves we went down the DB path, not the synthetic principal.
    assert loaded.username != "mcp-service"


# --------------------------------------------------------------------------
# 4. Malformed / expired / revoked / missing tokens still 401
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_malformed_token_still_401(mcp_key_configured, test_session):
    """A garbage (non-JWT, non-MCP-key) token still 401s."""
    req = _FakeRequest(bearer="not-a-real-token")
    with pytest.raises(HTTPException) as exc:
        await get_current_user(req, session=test_session)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_missing_token_still_401(mcp_key_configured, test_session):
    """No token at all still 401s 'Not authenticated'."""
    req = _FakeRequest()
    with pytest.raises(HTTPException) as exc:
        await get_current_user(req, session=test_session)
    assert exc.value.status_code == 401
    assert "Not authenticated" in exc.value.detail


@pytest.mark.asyncio
async def test_expired_jwt_still_401(mcp_key_configured, test_session):
    """An expired JWT still 401s 'Token has expired' (unchanged behavior)."""
    from datetime import timedelta

    expired = create_access_token(
        user_id=1, username="x", expires_delta=timedelta(seconds=-10)
    )
    req = _FakeRequest(bearer=expired)
    with pytest.raises(HTTPException) as exc:
        await get_current_user(req, session=test_session)
    assert exc.value.status_code == 401
    assert "expired" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_revoked_jwt_still_401(mcp_key_configured, test_session):
    """A revoked refresh-style token still 401s (revocation unchanged)."""
    from auth.tokens import create_refresh_token, decode_token

    refresh = create_refresh_token(user_id=1)
    jti = decode_token(refresh)["jti"]
    revoke_token(jti)

    req = _FakeRequest(bearer=refresh)
    with pytest.raises(HTTPException) as exc:
        await get_current_user(req, session=test_session)
    assert exc.value.status_code == 401
    assert "revoked" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_wrong_token_does_not_match_mcp_key(mcp_key_configured, test_session):
    """A token that is NOT the MCP key falls through to JWT decode (401)."""
    req = _FakeRequest(bearer=MCP_KEY + "-tampered")
    with pytest.raises(HTTPException) as exc:
        await get_current_user(req, session=test_session)
    assert exc.value.status_code == 401


# --------------------------------------------------------------------------
# 5. Unset/empty mcp_api_key accepts neither empty nor arbitrary tokens
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_empty_mcp_key_rejects_empty_token(mcp_key_unset, test_session):
    """With mcp_api_key unset, an empty bearer token must NOT be accepted."""
    req = _FakeRequest(bearer="")
    with pytest.raises(HTTPException) as exc:
        await get_current_user(req, session=test_session)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_empty_mcp_key_rejects_arbitrary_token(mcp_key_unset, test_session):
    """With mcp_api_key unset, an arbitrary token must NOT be accepted."""
    req = _FakeRequest(bearer="anything-at-all")
    with pytest.raises(HTTPException) as exc:
        await get_current_user(req, session=test_session)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_empty_mcp_key_does_not_accept_literal_empty_match(
    mcp_key_unset, test_session
):
    """Guard against ``"" == ""`` letting an empty token through.

    With an unset key, even an empty-string token (which equals the
    empty configured key under ``==``) must be rejected. This is the
    regression the constant-time guard with an emptiness check prevents.
    """
    # Empty bearer -> get_token_from_request returns None -> "Not authenticated"
    req = _FakeRequest(bearer="")
    with pytest.raises(HTTPException) as exc:
        await get_current_user(req, session=test_session)
    assert exc.value.status_code == 401


# --------------------------------------------------------------------------
# 6. Constant-time comparison is used and correct for match/non-match
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# 7. kgz3k / bead 6n76m — reject_mcp_service_principal variant
#    (RequireHumanAdminIfEnabled): denies the MCP principal, admits humans.
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reject_mcp_variant_denies_mcp_principal(
    mcp_key_configured, auth_enabled
):
    """require_admin_if_enabled(reject_mcp_service_principal=True) 403s the MCP key.

    The static MCP principal carries is_admin=True but must be denied on the
    settings-write (backup-restore) path — this is the kgz3k carve-out extended
    to restore. The 403 detail names the MCP principal, not the generic message.
    """
    check_admin = require_admin_if_enabled(reject_mcp_service_principal=True)
    req = _FakeRequest(bearer=MCP_KEY)

    with pytest.raises(HTTPException) as exc:
        await check_admin(req, session=_ExplodingSession())
    assert exc.value.status_code == 403
    assert "MCP service principal" in exc.value.detail


@pytest.mark.asyncio
async def test_reject_mcp_variant_admits_real_admin(
    mcp_key_configured, auth_enabled, test_session
):
    """The reject-MCP variant still ADMITS a real (DB-backed) admin."""
    user = User(
        username="human-admin",
        email="human-admin@example.com",
        auth_provider="local",
        is_admin=True,
        is_active=True,
    )
    test_session.add(user)
    test_session.commit()
    test_session.refresh(user)

    token = create_access_token(user_id=user.id, username=user.username)
    check_admin = require_admin_if_enabled(reject_mcp_service_principal=True)
    req = _FakeRequest(bearer=token)

    admitted = await check_admin(req, session=test_session)
    assert admitted is not None
    assert admitted.username == "human-admin"
    assert admitted.is_admin is True


@pytest.mark.asyncio
async def test_reject_mcp_variant_denies_non_admin_generic_message(
    mcp_key_configured, auth_enabled, test_session
):
    """A real NON-admin still gets the generic 'Admin access required' message —
    the MCP-specific reason is only disclosed to an admin-equivalent caller."""
    user = User(
        username="human-user",
        email="human-user@example.com",
        auth_provider="local",
        is_admin=False,
        is_active=True,
    )
    test_session.add(user)
    test_session.commit()
    test_session.refresh(user)

    token = create_access_token(user_id=user.id, username=user.username)
    check_admin = require_admin_if_enabled(reject_mcp_service_principal=True)
    req = _FakeRequest(bearer=token)

    with pytest.raises(HTTPException) as exc:
        await check_admin(req, session=test_session)
    assert exc.value.status_code == 403
    assert exc.value.detail == "Admin access required"


@pytest.mark.asyncio
async def test_default_variant_still_admits_mcp_principal(
    mcp_key_configured, auth_enabled
):
    """Regression guard: the DEFAULT factory (no reject flag) STILL admits the
    MCP principal — every non-restore admin route the MCP key legitimately
    reaches (channel management, etc.) is unchanged by the 6n76m fix."""
    check_admin = require_admin_if_enabled()  # reject_mcp_service_principal=False
    req = _FakeRequest(bearer=MCP_KEY)

    user = await check_admin(req, session=_ExplodingSession())
    assert user is not None
    assert user.username == "mcp-service"
    assert user.is_admin is True


@pytest.mark.asyncio
async def test_reject_mcp_variant_setup_mode_allows(mcp_key_configured, monkeypatch):
    """In setup mode (auth disabled) the reject-MCP variant returns None, exactly
    like the base RequireAdminIfEnabled — behaviour is unchanged pre-setup."""
    monkeypatch.setattr(
        deps, "get_auth_settings",
        lambda: AuthSettings(setup_complete=False, require_auth=False),
    )
    check_admin = require_admin_if_enabled(reject_mcp_service_principal=True)
    req = _FakeRequest(bearer=MCP_KEY)

    result = await check_admin(req, session=_ExplodingSession())
    assert result is None


def test_uses_constant_time_compare(monkeypatch):
    """get_current_user must compare via hmac.compare_digest, not ``==``.

    We assert by behavior: patch hmac.compare_digest to a sentinel that
    records its call and proves the static-key branch routes through it.
    """
    import hmac as hmac_module

    calls = []
    real_compare = hmac_module.compare_digest

    def _spy(a, b):
        calls.append((a, b))
        return real_compare(a, b)

    monkeypatch.setattr(deps.hmac, "compare_digest", _spy)

    class _Settings:
        mcp_api_key = MCP_KEY

    monkeypatch.setattr(deps, "get_settings", lambda: _Settings(), raising=False)

    import asyncio

    req = _FakeRequest(bearer=MCP_KEY)
    user = asyncio.get_event_loop().run_until_complete(
        get_current_user(req, session=_ExplodingSession())
    )
    assert user.username == "mcp-service"
    assert calls, "hmac.compare_digest was not used for the MCP key compare"
    # The compared values were the presented token and the configured key.
    assert (MCP_KEY, MCP_KEY) in calls
