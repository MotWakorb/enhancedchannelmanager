"""B4 (MAIN IS THE SOLE WRITER): the HTTPS subprocess forwards the profile-
mutating route allowlist to the main process; non-mutating (or main-process)
requests are served locally.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tls.subprocess_proxy import subprocess_proxy_middleware, _should_forward


def _request(method: str, path: str, query: str = ""):
    from starlette.requests import Request

    async def _receive():
        return {"type": "http.request", "body": b'{"x":1}', "more_body": False}

    scope = {
        "type": "http", "method": method, "path": path,
        "query_string": query.encode(), "headers": [
            (b"authorization", b"Bearer tok"),
            (b"content-type", b"application/json"),
            (b"host", b"example:443"),
        ],
    }
    return Request(scope, _receive)


def _fake_httpx(status=200, content=b'{"ok":true}'):
    """Patchable httpx.AsyncClient whose .request returns a canned response."""
    upstream = MagicMock()
    upstream.status_code = status
    upstream.content = content
    upstream.headers = {"content-type": "application/json"}
    client = MagicMock()
    client.request = AsyncMock(return_value=upstream)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=ctx)
    return factory, client


def test_allowlist_matches_only_profile_mutating_routes():
    assert _should_forward("PATCH", "/api/m3u/accounts/5/group-settings")
    assert _should_forward("POST", "/api/channel-pipeline/run")
    assert _should_forward("POST", "/api/channel-pipeline/rules/7/run")
    # Not allowlisted:
    assert not _should_forward("GET", "/api/m3u/accounts/5/group-settings")
    assert not _should_forward("PATCH", "/api/m3u/accounts")
    assert not _should_forward("POST", "/api/channels")


@pytest.mark.asyncio
async def test_subprocess_forwards_mutating_route_to_main_not_local():
    """In the subprocess, an allowlisted profile-mutating request is FORWARDED to
    main (localhost) and the LOCAL handler does NOT run."""
    factory, http_client = _fake_httpx(status=503, content=b'{"detail":"nope"}')
    call_next = AsyncMock()  # local handler — must NOT be called

    with patch("tls.https_server.is_https_subprocess", return_value=True), \
         patch("config.get_http_port", return_value=6100), \
         patch("tls.subprocess_proxy.httpx.AsyncClient", factory):
        resp = await subprocess_proxy_middleware(
            _request("PATCH", "/api/m3u/accounts/5/group-settings"), call_next
        )

    call_next.assert_not_awaited()  # local write handler bypassed
    # Forwarded to main's local HTTP port, same method + path, and the status is
    # relayed verbatim (503 stays 503).
    assert http_client.request.await_args.args[0] == "PATCH"
    assert "127.0.0.1:6100/api/m3u/accounts/5/group-settings" in http_client.request.await_args.args[1]
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_subprocess_serves_non_mutating_route_locally():
    """A non-allowlisted route is served LOCALLY even in the subprocess."""
    factory, http_client = _fake_httpx()
    call_next = AsyncMock(return_value="LOCAL")

    with patch("tls.https_server.is_https_subprocess", return_value=True), \
         patch("tls.subprocess_proxy.httpx.AsyncClient", factory):
        result = await subprocess_proxy_middleware(
            _request("GET", "/api/channels"), call_next
        )

    call_next.assert_awaited_once()
    http_client.request.assert_not_awaited()
    assert result == "LOCAL"


@pytest.mark.asyncio
async def test_main_process_never_forwards():
    """In the MAIN process a mutating request is served locally (no forward)."""
    factory, http_client = _fake_httpx()
    call_next = AsyncMock(return_value="LOCAL")

    with patch("tls.https_server.is_https_subprocess", return_value=False), \
         patch("tls.subprocess_proxy.httpx.AsyncClient", factory):
        result = await subprocess_proxy_middleware(
            _request("PATCH", "/api/m3u/accounts/5/group-settings"), call_next
        )

    call_next.assert_awaited_once()
    http_client.request.assert_not_awaited()
    assert result == "LOCAL"
