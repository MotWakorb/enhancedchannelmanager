"""Transport timeout policy (6imr3), using only new synthetic inputs.

MockTransport inspects real HTTPX request extensions. The loopback server below
uses real TCP/HTTP and a bounded 0.2s delay; no recorded payloads or live services.
"""

import asyncio
from contextlib import asynccontextmanager
from time import monotonic
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from config import DispatcharrSettings
from dispatcharr_client import DispatcharrClient


_PATHS = ["/api/channels/channels/", "/api/epg/grid/"]
_PHASE_TIMEOUT = httpx.Timeout(connect=1.1, read=2.2, write=3.3, pool=4.4)
_POLICIES = [
    pytest.param({}, None, id="omitted"),
    pytest.param({"timeout": 0.7}, httpx.Timeout(0.7).as_dict(), id="float"),
    pytest.param({"timeout": _PHASE_TIMEOUT}, _PHASE_TIMEOUT.as_dict(), id="phases"),
    pytest.param({"timeout": None}, httpx.Timeout(None).as_dict(), id="unbounded"),
]


@asynccontextmanager
async def _client(*, handler=None, url="http://timeout.synthetic.invalid", retry=False):
    client = DispatcharrClient(DispatcharrSettings(
        url=url,
        auth_method="password" if retry else "api_key",
        dispatcharr_api_key="synthetic-timeout-key",
    ))
    client.access_token = "synthetic-old-access"
    client.refresh_token = "synthetic-refresh"
    timeout = client._client.timeout
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        timeout=timeout,
        transport=httpx.MockTransport(handler) if handler else None,
        trust_env=False,
    )
    try:
        yield client
    finally:
        await client.close()


def _responder(requests, *, retry):
    def handle(request):
        requests.append(request)
        if request.url.path == "/api/accounts/token/refresh/":
            return httpx.Response(200, json={"access": "synthetic-new-access"})
        if retry and len(requests) == 1:
            return httpx.Response(401)
        return httpx.Response(200, json={"version": "0.28.2", "timestamp": None})
    return handle


@pytest.mark.parametrize("retry", [False, True], ids=["initial", "jwt-retry"])
@pytest.mark.parametrize("path", _PATHS, ids=["ordinary", "epg-grid"])
@pytest.mark.parametrize("kwargs,expected", _POLICIES)
async def test_effective_timeout_on_initial_request_and_jwt_retry(path, kwargs, expected, retry):
    requests = []
    async with _client(handler=_responder(requests, retry=retry), retry=retry) as client:
        with patch.object(client._client, "request", AsyncMock(wraps=client._client.request)) as send:
            response = await client._request("GET", path, **kwargs)
        assert response.status_code == 200
        data_requests = [request for request in requests if request.url.path == path]
        assert len(data_requests) == (2 if retry else 1)
        if not kwargs:
            expected = httpx.Timeout(120.0 if path == "/api/epg/grid/" else 30.0).as_dict()
        assert [request.extensions["timeout"] for request in data_requests] == [
            expected
        ] * len(data_requests)
        if isinstance(kwargs.get("timeout"), httpx.Timeout):
            data_calls = [call for call in send.await_args_list if call.args[0] == "GET"]
            assert len(data_calls) == len(data_requests)
            assert all(call.kwargs["timeout"] is kwargs["timeout"] for call in data_calls)
        if retry:
            assert [request.url.path for request in requests] == [
                path, "/api/accounts/token/refresh/", path,
            ]
            assert data_requests[0].headers["Authorization"] == "Bearer synthetic-old-access"
            assert data_requests[1].headers["Authorization"] == "Bearer synthetic-new-access"


@pytest.mark.parametrize("retry", [False, True], ids=["initial", "jwt-retry"])
async def test_omitted_timeout_inherits_configured_client_phases(retry):
    requests = []
    async with _client(handler=_responder(requests, retry=retry), retry=retry) as client:
        client._client.timeout = _PHASE_TIMEOUT
        await client._request("GET", _PATHS[0])
    assert len(requests) == (3 if retry else 1)
    assert all(request.extensions["timeout"] == _PHASE_TIMEOUT.as_dict() for request in requests)


@pytest.mark.parametrize("retry", [False, True], ids=["initial", "jwt-retry"])
@pytest.mark.parametrize("kwargs,expected", _POLICIES)
async def test_get_version_default_and_nullable_timeout_policy(kwargs, expected, retry):
    requests = []
    async with _client(handler=_responder(requests, retry=retry), retry=retry) as client:
        assert await client.get_version(**kwargs) == {"version": "0.28.2", "timestamp": None}
    data_requests = [r for r in requests if r.url.path == "/api/core/version/"]
    assert len(data_requests) == (2 if retry else 1)
    if not kwargs:
        expected = httpx.Timeout(30.0).as_dict()
    assert [r.extensions["timeout"] for r in data_requests] == [expected] * len(data_requests)


@pytest.mark.parametrize("api_key", [False, True], ids=["jwt-opt-out", "api-key"])
async def test_terminal_401_does_not_refresh_or_retry(api_key):
    requests = []

    def reject(request):
        requests.append(request)
        return httpx.Response(401)

    async with _client(handler=reject, retry=not api_key) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await client.get_version(retry_on_401=api_key)
    assert len(requests) == 1
    assert requests[0].url.path == "/api/core/version/"
    assert requests[0].extensions["timeout"] == httpx.Timeout(30.0).as_dict()


@pytest.mark.parametrize("path", _PATHS, ids=["ordinary", "epg-grid"])
@pytest.mark.parametrize("kwargs,expected", _POLICIES)
async def test_timeout_policy_survives_interleaved_rate_limit_and_token_refresh(path, kwargs, expected):
    requests = []
    statuses = iter([429, 401, 429, 200])

    def respond(request):
        requests.append(request)
        if request.url.path == "/api/accounts/token/refresh/":
            return httpx.Response(200, json={"access": "synthetic-new-access"})
        return httpx.Response(next(statuses), headers={"Retry-After": "0"}, json={})

    async with _client(handler=respond, retry=True) as client:
        with patch("dispatcharr_client._sleep", new_callable=AsyncMock) as delay:
            response = await client._request("GET", path, **kwargs)
    assert response.status_code == 200
    assert delay.await_count == 2
    data_requests = [request for request in requests if request.url.path == path]
    assert len(data_requests) == 4
    if not kwargs:
        expected = httpx.Timeout(120.0 if path == "/api/epg/grid/" else 30.0).as_dict()
    assert [request.extensions["timeout"] for request in data_requests] == [expected] * 4
    assert [request.headers["Authorization"] for request in data_requests] == [
        "Bearer synthetic-old-access", "Bearer synthetic-old-access",
        "Bearer synthetic-new-access", "Bearer synthetic-new-access",
    ]


@asynccontextmanager
async def _delayed_http_server(*, retry):
    """Own an ephemeral loopback port and await every handler during teardown."""
    handlers = []
    requests = []
    sent = asyncio.Event()

    async def respond(reader, writer):
        try:
            header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 1.0)
            path = header.split(b" ", 2)[1].decode("ascii")
            requests.append(path)
            if path == "/api/accounts/token/refresh/":
                body = b'{"access":"synthetic-new-access"}'
                status = b"200 OK"
            elif retry and len(requests) == 1:
                body = b"{}"
                status = b"401 Unauthorized"
            else:
                await asyncio.sleep(0.2)
                body = b'{"version":"0.28.2","timestamp":null}'
                status = b"200 OK"
                sent.set()
            writer.write(
                b"HTTP/1.1 " + status + b"\r\nContent-Type: application/json\r\n"
                b"Content-Length: " + str(len(body)).encode("ascii")
                + b"\r\nConnection: close\r\n\r\n" + body
            )
            await asyncio.wait_for(writer.drain(), 1.0)
        finally:
            writer.close()
            await asyncio.wait_for(writer.wait_closed(), 1.0)

    def connected(reader, writer):
        handlers.append(asyncio.create_task(respond(reader, writer)))

    server = await asyncio.start_server(connected, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}", requests, sent
    finally:
        server.close()
        await server.wait_closed()
        if handlers:
            await asyncio.wait_for(asyncio.gather(*handlers), 2.0)
        assert all(task.done() for task in handlers)


@pytest.mark.parametrize("retry", [False, True], ids=["initial", "jwt-retry"])
@pytest.mark.parametrize("unbounded", [False, True], ids=["omitted", "explicit-none"])
async def test_real_http_short_default_interrupts_but_explicit_none_completes(retry, unbounded):
    async with _delayed_http_server(retry=retry) as (url, requests, sent):
        async with _client(url=url, retry=retry) as client:
            # Real HTTPX transport: generous connect/write/pool budgets isolate
            # the read-inactivity timeout from scheduling/socket setup latency.
            client._client.timeout = httpx.Timeout(1.0, read=0.03)
            started = monotonic()
            async with asyncio.timeout(2.0):  # Test hang guard, not product policy.
                if unbounded:
                    result = await client.get_version(timeout=None)
                    assert result == {"version": "0.28.2", "timestamp": None}
                    assert sent.is_set()
                else:
                    with pytest.raises(httpx.ReadTimeout):
                        await client.get_version()
                    assert not sent.is_set(), "timeout must interrupt before the delayed response"
            elapsed = monotonic() - started
            expected_paths = ["/api/core/version/"]
            if retry:
                expected_paths += ["/api/accounts/token/refresh/", "/api/core/version/"]
            assert requests == expected_paths
            print(f"real-http retry={retry} unbounded={unbounded} elapsed={elapsed:.4f}s")
