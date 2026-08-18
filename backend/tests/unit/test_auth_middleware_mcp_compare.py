"""
Unit tests for the global ``auth_middleware`` static MCP-key acceptance path.

bd-1wq7z.24 (a): The global ``auth_middleware`` in ``main.py`` accepts the
static ``mcp_api_key`` as an alternative to a JWT for any ``/api/*`` path. The
compare must be CONSTANT-TIME (``hmac.compare_digest``) to avoid a timing oracle
on the static key, with the existing truthiness guards on both operands so
``compare_digest`` never receives ``None`` (it raises on ``None``) and an empty
configured key is never accepted.

These tests pin the behavior at the middleware boundary:
- Correct key -> request passes through (``call_next`` called).
- Wrong key -> falls through to JWT check -> 401.
- The static-key compare routes through ``hmac.compare_digest``.
- Empty configured key never accepts an arbitrary token.
"""
import hmac

import pytest

import main


MCP_KEY = "mcp-static-secret-middleware-abcdef0123456789"


class _AuthOn:
    """auth_settings stand-in with auth enforced."""

    require_auth = True
    setup_complete = True


class _Settings:
    def __init__(self, mcp_api_key):
        self.mcp_api_key = mcp_api_key


class _Request:
    """Minimal request: middleware only reads ``.url.path``."""

    def __init__(self, path, method="GET"):
        self.url = type("U", (), {"path": path})()
        self.method = method
        self.scope = {"type": "http", "path": path, "method": method}


async def _call_next_sentinel(request):
    """A ``call_next`` that records it was reached and returns a marker."""
    return "PASSED_THROUGH"


def _patch_common(monkeypatch, *, mcp_api_key, token):
    monkeypatch.setattr(main, "get_auth_settings", lambda: _AuthOn())
    monkeypatch.setattr(main, "get_settings", lambda: _Settings(mcp_api_key))
    monkeypatch.setattr(main, "get_token_from_request", lambda request: token)
    # Any token that isn't the MCP key must be treated as an invalid JWT so the
    # path lands on the 401 branch deterministically.
    monkeypatch.setattr(main, "decode_token_safe", lambda token: None)


@pytest.mark.asyncio
async def test_correct_mcp_key_passes_through(monkeypatch):
    """A request bearing the configured MCP key is allowed through."""
    _patch_common(monkeypatch, mcp_api_key=MCP_KEY, token=MCP_KEY)
    result = await main.auth_middleware(_Request("/api/channels"), _call_next_sentinel)
    assert result == "PASSED_THROUGH"


@pytest.mark.asyncio
async def test_correct_mcp_key_is_forbidden_on_unlisted_route(monkeypatch):
    """Authentication with the service key is not blanket /api authority."""
    _patch_common(monkeypatch, mcp_api_key=MCP_KEY, token=MCP_KEY)
    result = await main.auth_middleware(
        _Request("/api/auth/admin/users", "GET"), _call_next_sentinel
    )
    assert result != "PASSED_THROUGH"
    assert getattr(result, "status_code", None) == 403


@pytest.mark.asyncio
async def test_correct_mcp_key_cannot_change_outbound_destination(monkeypatch):
    _patch_common(monkeypatch, mcp_api_key=MCP_KEY, token=MCP_KEY)
    result = await main.auth_middleware(
        _Request("/api/cloud-targets", "POST"), _call_next_sentinel
    )
    assert result != "PASSED_THROUGH"
    assert getattr(result, "status_code", None) == 403


@pytest.mark.asyncio
async def test_human_jwt_is_not_subject_to_service_capability_matrix(monkeypatch):
    _patch_common(monkeypatch, mcp_api_key=MCP_KEY, token="human.jwt.token")
    monkeypatch.setattr(main, "decode_token_safe", lambda token: {"sub": 42})
    result = await main.auth_middleware(
        _Request("/api/cloud-targets", "POST"), _call_next_sentinel
    )
    assert result == "PASSED_THROUGH"


@pytest.mark.asyncio
async def test_wrong_mcp_key_falls_through_to_401(monkeypatch):
    """A wrong key is not accepted and (no valid JWT) yields a 401."""
    _patch_common(monkeypatch, mcp_api_key=MCP_KEY, token=MCP_KEY + "-tampered")
    result = await main.auth_middleware(_Request("/api/channels"), _call_next_sentinel)
    # JSONResponse, not the pass-through sentinel.
    assert result != "PASSED_THROUGH"
    assert getattr(result, "status_code", None) == 401


@pytest.mark.asyncio
async def test_static_key_compare_is_constant_time(monkeypatch):
    """The MCP-key branch routes through hmac.compare_digest (no ``==``)."""
    _patch_common(monkeypatch, mcp_api_key=MCP_KEY, token=MCP_KEY)

    calls = []
    real = hmac.compare_digest

    def _spy(a, b):
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(main.hmac, "compare_digest", _spy)

    result = await main.auth_middleware(_Request("/api/channels"), _call_next_sentinel)
    assert result == "PASSED_THROUGH"
    assert (MCP_KEY, MCP_KEY) in calls


@pytest.mark.asyncio
async def test_empty_configured_key_rejects_arbitrary_token(monkeypatch):
    """An unset/empty mcp_api_key must never accept a presented token."""
    _patch_common(monkeypatch, mcp_api_key="", token="anything")
    result = await main.auth_middleware(_Request("/api/channels"), _call_next_sentinel)
    assert result != "PASSED_THROUGH"
    assert getattr(result, "status_code", None) == 401


@pytest.mark.asyncio
async def test_empty_configured_key_with_empty_token_does_not_passthrough(monkeypatch):
    """``"" `` key + ``""`` token must NOT pass through (the ``==`` foot-gun)."""
    _patch_common(monkeypatch, mcp_api_key="", token="")
    result = await main.auth_middleware(_Request("/api/channels"), _call_next_sentinel)
    assert result != "PASSED_THROUGH"
    assert getattr(result, "status_code", None) == 401
