"""Regression coverage for stream HTTP/FFmpeg SSRF enforcement (04c0u.6)."""

import ipaddress
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from security.ssrf import SSRFError, SSRFMode
from security.stream_outbound import SSRFPinnedTransport, stream_request


def _target(url: str, ip: str = "93.184.216.34"):
    from security.ssrf import ResolvedTarget

    parsed = httpx.URL(url)
    return ResolvedTarget(
        scheme=parsed.scheme,
        hostname=parsed.host,
        port=parsed.port or (443 if parsed.scheme == "https" else 80),
        ip=ipaddress.ip_address(ip),
        url=url,
    )


@pytest.mark.asyncio
async def test_transport_connects_to_validated_ip_without_second_dns_lookup():
    inner = AsyncMock()
    inner.handle_async_request.return_value = httpx.Response(200, content=b"ok")
    transport = SSRFPinnedTransport(inner=inner, mode=SSRFMode.PUBLIC_ONLY)

    with patch(
        "security.stream_outbound.validate_outbound_url",
        return_value=_target("https://media.example/live.ts"),
    ) as validate:
        response = await transport.handle_async_request(
            httpx.Request("GET", "https://media.example/live.ts")
        )

    assert response.status_code == 200
    validate.assert_called_once_with(
        "https://media.example/live.ts", SSRFMode.PUBLIC_ONLY
    )
    pinned = inner.handle_async_request.await_args.args[0]
    assert pinned.url.host == "93.184.216.34"
    assert pinned.headers["Host"] == "media.example"
    assert pinned.extensions["sni_hostname"] == "media.example"


@pytest.mark.asyncio
async def test_prepared_initial_target_is_not_resolved_again_before_connect():
    inner = AsyncMock()
    inner.handle_async_request.return_value = httpx.Response(200, content=b"ok")
    target = _target("https://media.example/live.ts")
    transport = SSRFPinnedTransport(inner=inner, mode=SSRFMode.PUBLIC_ONLY)

    with patch("security.stream_outbound.validate_outbound_url") as validate:
        async with stream_request(
            "https://media.example/live.ts",
            transport=transport,
            initial_target=target,
        ) as response:
            assert await response.aread() == b"ok"

    validate.assert_not_called()
    pinned = inner.handle_async_request.await_args.args[0]
    assert pinned.url.host == "93.184.216.34"


@pytest.mark.asyncio
async def test_transport_rejects_literal_metadata_address_before_connect():
    inner = AsyncMock()
    transport = SSRFPinnedTransport(inner=inner, mode=SSRFMode.LAN_FRIENDLY)

    with pytest.raises(SSRFError):
        await transport.handle_async_request(
            httpx.Request("GET", "http://169.254.169.254/latest/meta-data")
        )

    inner.handle_async_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_transport_rejects_mixed_dns_answer_before_connect():
    inner = AsyncMock()
    transport = SSRFPinnedTransport(inner=inner, mode=SSRFMode.LAN_FRIENDLY)

    with patch(
        "security.ssrf._resolve",
        return_value=[
            ipaddress.ip_address("93.184.216.34"),
            ipaddress.ip_address("169.254.169.254"),
        ],
    ):
        with pytest.raises(SSRFError):
            await transport.handle_async_request(
                httpx.Request("GET", "http://mixed.example/live.ts")
            )

    inner.handle_async_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_redirect_is_revalidated_and_denied_before_second_connect():
    calls = []

    async def handler(request: httpx.Request):
        calls.append(request.url.host)
        return httpx.Response(
            302,
            headers={"Location": "http://169.254.169.254/latest/meta-data"},
            request=request,
        )

    inner = httpx.MockTransport(handler)
    transport = SSRFPinnedTransport(inner=inner, mode=SSRFMode.LAN_FRIENDLY)

    with patch(
        "security.stream_outbound.validate_outbound_url",
        return_value=_target("http://public.example/live.ts"),
    ):
        with pytest.raises(SSRFError):
            async with stream_request(
                "http://public.example/live.ts", transport=transport
            ):
                pass

    assert calls == ["93.184.216.34"]


@pytest.mark.asyncio
async def test_request_time_dns_rebinding_is_denied_before_connect():
    inner = AsyncMock()
    transport = SSRFPinnedTransport(inner=inner, mode=SSRFMode.LAN_FRIENDLY)

    with patch(
        "security.ssrf._resolve",
        return_value=[ipaddress.ip_address("169.254.169.254")],
    ):
        with pytest.raises(SSRFError):
            await transport.handle_async_request(
                httpx.Request("GET", "http://rebound.example/live.ts")
            )

    inner.handle_async_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_https_to_http_redirect_is_denied_before_second_connect():
    calls = []

    async def handler(request: httpx.Request):
        calls.append(request.url.host)
        return httpx.Response(
            302,
            headers={"Location": "http://public.example/next.ts"},
            request=request,
        )

    transport = SSRFPinnedTransport(
        inner=httpx.MockTransport(handler), mode=SSRFMode.PUBLIC_ONLY
    )
    with patch(
        "security.stream_outbound.validate_outbound_url",
        return_value=_target("https://media.example/live.ts"),
    ):
        with pytest.raises(SSRFError, match="downgrades"):
            async with stream_request(
                "https://media.example/live.ts", transport=transport
            ):
                pass

    assert calls == ["93.184.216.34"]


@pytest.mark.asyncio
async def test_cross_origin_redirect_strips_authorization_header():
    requests = []

    async def handler(request: httpx.Request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                302,
                headers={"Location": "https://cdn.example/next.ts"},
                request=request,
            )
        return httpx.Response(200, content=b"media", request=request)

    targets = {
        "https://media.example/live.ts": _target("https://media.example/live.ts"),
        "https://cdn.example/next.ts": _target(
            "https://cdn.example/next.ts", "93.184.216.35"
        ),
    }

    def validate_redirect(_from, to, _mode):
        return targets[to]

    transport = SSRFPinnedTransport(
        inner=httpx.MockTransport(handler), mode=SSRFMode.PUBLIC_ONLY
    )
    with patch(
        "security.stream_outbound.validate_outbound_url",
        return_value=targets["https://media.example/live.ts"],
    ), patch(
        "security.stream_outbound.validate_redirect", side_effect=validate_redirect
    ):
        async with stream_request(
            "https://media.example/live.ts",
            headers={"Authorization": "Bearer synthetic-test-token"},
            transport=transport,
        ) as response:
            assert await response.aread() == b"media"

    assert requests[0].headers["Authorization"] == "Bearer synthetic-test-token"
    assert "Authorization" not in requests[1].headers


@pytest.mark.asyncio
async def test_redirect_chain_is_bounded():
    calls = 0

    async def handler(request: httpx.Request):
        nonlocal calls
        calls += 1
        return httpx.Response(302, headers={"Location": "/again"}, request=request)

    transport = SSRFPinnedTransport(
        inner=httpx.MockTransport(handler), mode=SSRFMode.PUBLIC_ONLY
    )
    target = _target("https://media.example/again")
    with patch(
        "security.stream_outbound.validate_outbound_url", return_value=target
    ), patch("security.stream_outbound.validate_redirect", return_value=target):
        with pytest.raises(SSRFError, match="Redirect chain exceeded"):
            async with stream_request(
                "https://media.example/live.ts", transport=transport
            ):
                pass

    assert calls == 6


@pytest.mark.asyncio
async def test_lan_friendly_policy_preserves_private_iptv_target():
    inner = AsyncMock()
    inner.handle_async_request.return_value = httpx.Response(200, content=b"ok")
    transport = SSRFPinnedTransport(inner=inner, mode=SSRFMode.LAN_FRIENDLY)

    response = await transport.handle_async_request(
        httpx.Request("GET", "http://192.168.50.20:8080/live.ts")
    )

    assert response.status_code == 200
    pinned = inner.handle_async_request.await_args.args[0]
    assert pinned.url.host == "192.168.50.20"
