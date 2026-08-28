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


def test_allowlist_matches_only_profile_mutating_and_task_routes():
    assert _should_forward("PATCH", "/api/m3u/accounts/5/group-settings")
    assert _should_forward("POST", "/api/m3u/refresh/5")            # GAP A
    assert _should_forward("POST", "/api/channel-pipeline/run")
    assert _should_forward("POST", "/api/channel-pipeline/rules/7/run")
    # Finding 1a: channel_pipeline_router is dual-mounted; the DEPRECATED alias
    # /api/auto-creation runs the SAME reconcile handler, so it must forward too.
    assert _should_forward("POST", "/api/auto-creation/run")
    assert _should_forward("POST", "/api/auto-creation/rules/7/run")
    assert _should_forward("POST", "/api/tasks/m3u_change_monitor/run")  # GAP B (non-numeric id)
    assert _should_forward("POST", "/api/tasks/channel_pipeline/run")
    # Finding 2: a task CANCEL must hit the same (main) engine as its run.
    assert _should_forward("POST", "/api/tasks/m3u_change_monitor/cancel")
    assert _should_forward("POST", "/api/profile-conflict-reviews/42/accept")
    # Not allowlisted:
    assert not _should_forward("GET", "/api/m3u/accounts/5/group-settings")
    assert not _should_forward("PATCH", "/api/m3u/accounts")
    assert not _should_forward("POST", "/api/channels")
    assert not _should_forward("POST", "/api/m3u/refresh")          # bulk refresh (no poll) stays local
    assert not _should_forward("GET", "/api/tasks/m3u_change_monitor/run")
    assert not _should_forward("POST", "/api/tasks/x/run/extra")    # anchored — no sub-path
    assert not _should_forward("GET", "/api/profile-conflict-reviews/42/accept")


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
async def test_subprocess_forwards_m3u_refresh_to_main_not_local():
    """GAP A: the single-account M3U refresh (which spawns the post-refresh poll
    -> full reconcile sweep) is FORWARDED to main; the local handler/poll does
    NOT run in the subprocess."""
    factory, http_client = _fake_httpx(status=200, content=b'{"ok":true}')
    call_next = AsyncMock()  # local refresh handler + poll spawn — must NOT run

    with patch("tls.https_server.is_https_subprocess", return_value=True), \
         patch("config.get_http_port", return_value=6100), \
         patch("tls.subprocess_proxy.httpx.AsyncClient", factory):
        resp = await subprocess_proxy_middleware(
            _request("POST", "/api/m3u/refresh/5"), call_next
        )

    call_next.assert_not_awaited()
    assert http_client.request.await_args.args[0] == "POST"
    assert "127.0.0.1:6100/api/m3u/refresh/5" in http_client.request.await_args.args[1]
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_subprocess_forwards_task_run_to_main_not_local():
    """GAP B: a task run (m3u_change_monitor / channel_pipeline execute inline)
    is FORWARDED to main; the local run_task does NOT execute in the subprocess."""
    factory, http_client = _fake_httpx(status=202, content=b'{"queued":true}')
    call_next = AsyncMock()  # local run_task — must NOT execute

    with patch("tls.https_server.is_https_subprocess", return_value=True), \
         patch("config.get_http_port", return_value=6100), \
         patch("tls.subprocess_proxy.httpx.AsyncClient", factory):
        resp = await subprocess_proxy_middleware(
            _request("POST", "/api/tasks/m3u_change_monitor/run"), call_next
        )

    call_next.assert_not_awaited()
    assert "127.0.0.1:6100/api/tasks/m3u_change_monitor/run" in http_client.request.await_args.args[1]
    assert resp.status_code == 202


@pytest.mark.parametrize("path", [
    "/api/auto-creation/run",
    "/api/auto-creation/rules/7/run",
])
@pytest.mark.asyncio
async def test_subprocess_forwards_auto_creation_alias_to_main(path):
    """Finding 1a: the deprecated /api/auto-creation alias of the channel-pipeline
    router runs the SAME assign-profile handler, so it MUST forward to main — not
    run its membership write locally in the subprocess."""
    factory, http_client = _fake_httpx(status=200, content=b'{"ok":true}')
    call_next = AsyncMock()  # local pipeline handler — must NOT run

    with patch("tls.https_server.is_https_subprocess", return_value=True), \
         patch("config.get_http_port", return_value=6100), \
         patch("tls.subprocess_proxy.httpx.AsyncClient", factory):
        resp = await subprocess_proxy_middleware(_request("POST", path), call_next)

    call_next.assert_not_awaited()
    assert f"127.0.0.1:6100{path}" in http_client.request.await_args.args[1]
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_subprocess_forwards_task_cancel_to_main_not_local():
    """Finding 2: a task CANCEL must reach the SAME (main) engine as the run —
    a forwarded run tracked in main's _active_tasks can only be cancelled there
    (the subprocess has a DIFFERENT per-process _active_tasks)."""
    factory, http_client = _fake_httpx(status=200, content=b'{"cancelled":true}')
    call_next = AsyncMock()  # local cancel — must NOT run

    with patch("tls.https_server.is_https_subprocess", return_value=True), \
         patch("config.get_http_port", return_value=6100), \
         patch("tls.subprocess_proxy.httpx.AsyncClient", factory):
        resp = await subprocess_proxy_middleware(
            _request("POST", "/api/tasks/m3u_change_monitor/cancel"), call_next
        )

    call_next.assert_not_awaited()
    assert "127.0.0.1:6100/api/tasks/m3u_change_monitor/cancel" in http_client.request.await_args.args[1]
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_proxy_imposes_no_timeout_ceiling_on_forwarded_run():
    """Finding 2: the proxy imposes NO timeout ceiling (httpx timeout=None) — main's
    own request-timeout middleware is authoritative and EXEMPTS long task runs, so
    the proxy must NOT cut a legitimately long forwarded run to a false 502."""
    factory, _ = _fake_httpx(status=202, content=b'{"queued":true}')
    call_next = AsyncMock()

    with patch("tls.https_server.is_https_subprocess", return_value=True), \
         patch("config.get_http_port", return_value=6100), \
         patch("tls.subprocess_proxy.httpx.AsyncClient", factory):
        await subprocess_proxy_middleware(
            _request("POST", "/api/tasks/m3u_change_monitor/run"), call_next
        )

    # The AsyncClient was constructed with NO ceiling — main decides the budget.
    assert factory.call_args.kwargs.get("timeout", "MISSING") is None


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
