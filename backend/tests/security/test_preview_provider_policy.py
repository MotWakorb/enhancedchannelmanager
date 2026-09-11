"""GH995 provider-media policy across preview modes and later HLS resources."""

import ipaddress
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import ClientDisconnect

from routers import stream_preview as preview
from security import stream_outbound
from security.ssrf import DNSResolutionError, SchemeDowngrade


@pytest.fixture
def provider_transport():
    requests = []
    responses = []

    async def handler(request):
        requests.append(request)
        if request.url.scheme == "https":
            response = httpx.Response(302, headers={"Location": "http://edge.example/live.ts"}, request=request)
        else:
            response = httpx.Response(200, content=b"media", request=request)
        responses.append(response)
        return response

    transport_class = stream_outbound.SSRFPinnedTransport
    with patch.object(stream_outbound, "SSRFPinnedTransport", side_effect=lambda **kwargs: transport_class(
        inner_factory=lambda: httpx.MockTransport(handler), **kwargs
    )), patch("security.ssrf._resolve", return_value=[ipaddress.ip_address("93.184.216.34")]):
        yield requests, responses


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["passthrough", "transcode", "video_only"])
async def test_preview_mode_uses_provider_ua_through_downgrade(mode, provider_transport):
    requests, responses = provider_transport
    client = AsyncMock()
    client.get_stream.return_value = {"url": "https://provider.example/live.ts"}
    process = MagicMock()
    process.stdout.read.return_value = b""
    with patch.object(preview, "get_client", return_value=client), patch.object(preview, "get_settings", return_value=SimpleNamespace(
        stream_preview_mode=mode, stream_user_agent="tivimate"
    )), patch.object(preview.subprocess, "Popen", return_value=process):
        response = await preview.stream_preview(42)
        assert len(requests) == 2  # upstream ready before response is returned
        assert {request.headers["User-Agent"] for request in requests} == {"TiviMate/5.1.6 (Android 12)"}
        await response.resources.close()
    assert all(response.is_closed for response in responses)
    if mode != "passthrough":
        process.terminate.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["passthrough", "transcode", "video_only"])
async def test_disconnect_before_first_body_read_closes_opened_resources(mode, provider_transport):
    requests, responses = provider_transport
    client = AsyncMock()
    client.get_stream.return_value = {"url": "https://provider.example/live.ts"}
    process = MagicMock()
    with patch.object(preview, "get_client", return_value=client), patch.object(preview, "get_settings", return_value=SimpleNamespace(
        stream_preview_mode=mode, stream_user_agent="vlc"
    )), patch.object(preview.subprocess, "Popen", return_value=process):
        response = await preview.stream_preview(42)
        with pytest.raises(ClientDisconnect):
            await response({"type": "http", "asgi": {"spec_version": "2.4"}}, AsyncMock(), AsyncMock(side_effect=OSError("disconnected")))
    assert len(requests) == 2
    assert all(response.is_closed for response in responses)
    process.stdout.read.assert_not_called()
    if mode != "passthrough":
        process.terminate.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["passthrough", "transcode", "video_only"])
async def test_channel_proxy_refuses_downgrade_before_bearer_can_escape(mode, provider_transport):
    requests, responses = provider_transport
    client = AsyncMock(access_token="test-bearer")
    client.get_channel.return_value = {"uuid": "channel"}
    with patch.object(preview, "get_client", return_value=client), patch.object(preview, "get_settings", return_value=SimpleNamespace(
        stream_preview_mode=mode, stream_user_agent="tivimate", url="https://dispatcharr.example"
    )):
        with pytest.raises(HTTPException) as error:
            await preview.channel_preview(42)
    assert error.value.status_code == 403
    assert len(requests) == 1
    assert requests[0].headers["Authorization"] == "Bearer test-bearer"
    assert all(response.is_closed for response in responses)
    client.get_user_agents.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["passthrough", "transcode", "video_only"])
async def test_dns_failure_returns_502_in_every_mode(mode):
    client = AsyncMock()
    client.get_stream.return_value = {"url": "https://provider.example/live.ts"}
    with patch.object(preview, "get_client", return_value=client), patch.object(preview, "get_settings", return_value=SimpleNamespace(
        stream_preview_mode=mode, stream_user_agent="vlc"
    )), patch("security.stream_outbound.validate_outbound_url", side_effect=DNSResolutionError()):
        with pytest.raises(HTTPException) as error:
            await preview.stream_preview(1)
    assert error.value.status_code == 502
    assert error.value.detail == "Stream DNS resolution failed from ECM"


@pytest.mark.asyncio
@pytest.mark.parametrize("policy,status", [(SchemeDowngrade.ALLOW_STREAM_PREVIEW, 200), (SchemeDowngrade.REFUSE, 502)])
async def test_later_hls_resource_keeps_ua_policy_and_strips_cross_origin_auth(policy, status):
    requests = []

    async def handler(request):
        requests.append(request)
        if request.url.path == "/root.m3u8":
            return httpx.Response(200, content=b"#EXTM3U\nhttps://cdn.example/segment.ts\n", request=request)
        if request.url.scheme == "https":
            return httpx.Response(302, headers={"Location": "http://edge.example/segment.ts"}, request=request)
        return httpx.Response(200, content=b"segment", request=request)

    transport_class = stream_outbound.SSRFPinnedTransport
    with patch.object(stream_outbound, "SSRFPinnedTransport", side_effect=lambda **kwargs: transport_class(
        inner_factory=lambda: httpx.MockTransport(handler), **kwargs
    )), patch("security.ssrf._resolve", return_value=[ipaddress.ip_address("93.184.216.34")]):
        async with stream_outbound.validated_subprocess_input(
            "https://provider.example/root.m3u8", headers={"User-Agent": "Provider/9", "Authorization": "Bearer local"},
            scheme_downgrade=policy,
        ) as source:
            async with httpx.AsyncClient() as downstream:
                manifest = await downstream.get(source.argument)
                segment_url = manifest.text.splitlines()[1]
                segment = await downstream.get(segment_url)
                assert segment.status_code == status
                if status == 200:
                    assert segment.content == b"segment"
    assert requests[0].headers["Authorization"] == "Bearer local"
    assert all(request.headers["User-Agent"] == "Provider/9" for request in requests)
    assert all("Authorization" not in request.headers for request in requests[1:])
