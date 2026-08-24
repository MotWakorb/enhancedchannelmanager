"""Regression coverage for stream HTTP/FFmpeg SSRF enforcement (04c0u.6)."""

import asyncio
from contextlib import asynccontextmanager
import ipaddress
from pathlib import Path
import re
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from aiohttp import web
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from security.ssrf import SSRFError, SSRFMode
from security.stream_outbound import (
    _LocalStreamRelay,
    _MAX_HLS_MANIFEST_BYTES,
    _MAX_RELAY_RESOURCES,
    SSRFPinnedTransport,
    stream_request,
    validated_subprocess_input,
)


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


def test_relay_tokens_are_random_128_bit_values_and_deduplicate():
    relay = _LocalStreamRelay("https://media.example/root.m3u8", None, None)
    first = relay._initial_token
    second = relay._register("https://media.example/segment.ts")

    assert re.fullmatch(r"[0-9a-f]{32}", first)
    assert re.fullmatch(r"[0-9a-f]{32}", second)
    assert first != second
    assert second != "1"
    assert relay._register("https://media.example/segment.ts") == second


def test_relay_resource_cap_accepts_1024_unique_urls_and_rejects_1025th():
    relay = _LocalStreamRelay("https://media.example/root.m3u8", None, None)
    for index in range(_MAX_RELAY_RESOURCES - 1):
        relay._register(f"https://media.example/segment-{index}.ts")

    assert len(relay._targets) == 1024
    with pytest.raises(SSRFError, match="resource limit"):
        relay._register("https://media.example/one-too-many.ts")


def test_build_0120_docs_match_pinned_relay_and_direct_transport_residual():
    root = Path(__file__).resolve().parents[3]
    changelog = (root / "CHANGELOG.md").read_text()
    entry = changelog.split("build 0120", 1)[1].split("\n\n", 1)[0]
    security_doc = (root / "docs/security/stream_outbound_ssrf.md").read_text()

    assert "ephemeral pinned loopback relay" in entry
    assert "HTTP(S) therefore has no subprocess-owned provider DNS or redirect residual" in entry
    assert "only those direct transports retain" in entry
    assert "`http,tcp,crypto`" in security_doc
    assert "only remaining resolution race is on direct UDP, RTP, and RTMP" in security_doc
    assert "pipe:0" not in security_doc


class _ChunkedManifestResponse:
    def __init__(self, chunks, content_length=None):
        self._chunks = chunks
        self.headers = {}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)
        self.chunks_read = 0

    async def aiter_bytes(self, chunk_size):
        assert chunk_size == 65536
        for chunk in self._chunks:
            self.chunks_read += 1
            yield chunk


@pytest.mark.asyncio
async def test_manifest_reader_accepts_exact_two_mib_limit():
    relay = _LocalStreamRelay("https://media.example/root.m3u8", None, None)
    response = _ChunkedManifestResponse([b"a" * _MAX_HLS_MANIFEST_BYTES])

    body = await relay._read_hls_manifest(response)

    assert len(body) == _MAX_HLS_MANIFEST_BYTES


@pytest.mark.asyncio
async def test_manifest_reader_stops_at_limit_plus_one_without_reading_tail():
    relay = _LocalStreamRelay("https://media.example/root.m3u8", None, None)
    response = _ChunkedManifestResponse(
        [b"a" * (_MAX_HLS_MANIFEST_BYTES + 1), b"unread-tail"]
    )

    with pytest.raises(web.HTTPRequestEntityTooLarge):
        await relay._read_hls_manifest(response)

    assert response.chunks_read == 1


@pytest.mark.asyncio
async def test_manifest_content_length_rejects_before_reading_body():
    relay = _LocalStreamRelay("https://media.example/root.m3u8", None, None)
    response = _ChunkedManifestResponse(
        [b"must-not-be-read"], content_length=_MAX_HLS_MANIFEST_BYTES + 1
    )

    with pytest.raises(web.HTTPRequestEntityTooLarge):
        await relay._read_hls_manifest(response)

    assert response.chunks_read == 0


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
        inner_factory=lambda: httpx.MockTransport(handler),
        mode=SSRFMode.PUBLIC_ONLY,
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

    def validate_redirect(_from, to, _mode, **_policy):
        # **_policy absorbs scheme_downgrade (bead enhancedchannelmanager-iyvl9);
        # this double is exercising header stripping, not redirect policy.
        return targets[to]

    transport = SSRFPinnedTransport(
        inner_factory=lambda: httpx.MockTransport(handler),
        mode=SSRFMode.PUBLIC_ONLY,
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
async def test_equivalent_default_https_port_retains_authorization_header():
    requests = []

    async def handler(request: httpx.Request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                302,
                headers={"Location": "https://media.example:443/next.ts"},
                request=request,
            )
        return httpx.Response(200, content=b"media", request=request)

    target = _target("https://media.example/next.ts")
    transport = SSRFPinnedTransport(
        inner=httpx.MockTransport(handler), mode=SSRFMode.PUBLIC_ONLY
    )
    with patch(
        "security.stream_outbound.validate_outbound_url", return_value=target
    ), patch("security.stream_outbound.validate_redirect", return_value=target):
        async with stream_request(
            "https://media.example/live.ts",
            headers={"Authorization": "Bearer synthetic-test-token"},
            transport=transport,
        ) as response:
            assert await response.aread() == b"media"

    assert requests[1].headers["Authorization"] == "Bearer synthetic-test-token"


@pytest.mark.asyncio
async def test_cross_hostname_same_ip_uses_distinct_tls_identity():
    """A logical-origin redirect must not inherit an existing TLS pool."""

    identities = []

    class TLSIdentityTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            identity = request.extensions["sni_hostname"]
            identities.append(identity)
            return httpx.Response(
                302 if identity == "media.example" else 200,
                headers={"Location": "https://cdn.example/next.ts"}
                if identity == "media.example"
                else {},
                content=b"media" if identity == "cdn.example" else b"",
                request=request,
            )

    created = []

    def transport_factory():
        transport = TLSIdentityTransport()
        created.append(transport)
        return transport

    targets = {
        "https://media.example/live.ts": _target("https://media.example/live.ts"),
        "https://cdn.example/next.ts": _target("https://cdn.example/next.ts"),
    }
    transport = SSRFPinnedTransport(
        inner_factory=transport_factory, mode=SSRFMode.PUBLIC_ONLY
    )
    with patch(
        "security.stream_outbound.validate_outbound_url",
        return_value=targets["https://media.example/live.ts"],
    ), patch(
        "security.stream_outbound.validate_redirect",
        return_value=targets["https://cdn.example/next.ts"],
    ):
        async with stream_request(
            "https://media.example/live.ts", transport=transport
        ) as response:
            assert await response.aread() == b"media"

    assert identities == ["media.example", "cdn.example"]
    assert len(created) == 2


@pytest.mark.asyncio
async def test_same_origin_reuses_transport_pool():
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(
            302 if calls == 1 else 200,
            headers={"Location": "/next.ts"} if calls == 1 else {},
            content=b"media" if calls == 2 else b"",
            request=request,
        )

    created = []

    def transport_factory():
        transport = httpx.MockTransport(handler)
        created.append(transport)
        return transport

    target = _target("https://media.example/live.ts")
    transport = SSRFPinnedTransport(
        inner_factory=transport_factory, mode=SSRFMode.PUBLIC_ONLY
    )
    with patch(
        "security.stream_outbound.validate_outbound_url", return_value=target
    ), patch("security.stream_outbound.validate_redirect", return_value=target):
        async with stream_request(
            "https://media.example/live.ts", transport=transport
        ) as response:
            assert await response.aread() == b"media"

    assert len(created) == 1


@pytest.mark.asyncio
async def test_ipv6_pinned_request_uses_bracketed_host_header():
    inner = AsyncMock()
    inner.handle_async_request.return_value = httpx.Response(200, content=b"ok")
    transport = SSRFPinnedTransport(inner=inner, mode=SSRFMode.LAN_FRIENDLY)

    with patch(
        "security.stream_outbound.validate_outbound_url",
        return_value=_target("http://[2001:4860:4860::8888]:8080/live.ts", "2001:4860:4860::8888"),
    ):
        await transport.handle_async_request(
            httpx.Request("GET", "http://[2001:4860:4860::8888]:8080/live.ts")
        )

    pinned = inner.handle_async_request.await_args.args[0]
    assert pinned.headers["Host"] == "[2001:4860:4860::8888]:8080"


@pytest.mark.asyncio
async def test_http_subprocess_input_is_loopback_relay_and_keeps_bearer_in_parent():
    seen = {}

    @asynccontextmanager
    async def fake_request(url, **kwargs):
        seen.update(url=url, kwargs=kwargs)
        response = MagicMock()
        response.raise_for_status = MagicMock()
        yield response

    with patch("security.stream_outbound.stream_request", fake_request):
        async with validated_subprocess_input(
            "https://media.example/live.ts",
            headers={"Authorization": "Bearer synthetic-test-token"},
        ) as subprocess_input:
            assert subprocess_input.argument.startswith("http://127.0.0.1:")
            assert subprocess_input.is_http_relay

    assert seen["kwargs"]["headers"] == {
        "Authorization": "Bearer synthetic-test-token"
    }


@pytest.mark.asyncio
async def test_hls_relay_rewrites_segments_and_keys_with_origin_scoped_auth():
    manifest_url = "https://media.example/live/master.m3u8"
    calls = []
    bodies = {
        manifest_url: (
            b'#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\n'
            b"segment.ts\nhttps://cdn.example/cross.ts\n"
        ),
        "https://media.example/live/key.bin": b"key",
        "https://media.example/live/segment.ts": b"segment",
        "https://cdn.example/cross.ts": b"cross",
    }

    @asynccontextmanager
    async def fake_request(url, **kwargs):
        calls.append((url, dict(kwargs.get("headers") or {})))
        content_type = (
            "application/vnd.apple.mpegurl" if url == manifest_url else "video/mp2t"
        )
        yield httpx.Response(
            200,
            content=bodies[url],
            headers={"Content-Type": content_type},
            request=httpx.Request("GET", url),
        )

    with patch("security.stream_outbound.stream_request", fake_request):
        async with validated_subprocess_input(
            manifest_url,
            headers={"Authorization": "Bearer synthetic-test-token"},
        ) as subprocess_input:
            async with httpx.AsyncClient() as client:
                manifest = (await client.get(subprocess_input.argument)).text
                resource_urls = [
                    line for line in manifest.splitlines() if line.startswith("http://")
                ]
                key_url = manifest.split('URI="', 1)[1].split('"', 1)[0]
                for resource_url in [key_url, *resource_urls]:
                    assert (await client.get(resource_url)).status_code == 200

    same_origin_calls = {url: headers for url, headers in calls if "media.example" in url}
    assert all(
        headers.get("Authorization") == "Bearer synthetic-test-token"
        for headers in same_origin_calls.values()
    )
    cross_headers = dict(calls)["https://cdn.example/cross.ts"]
    assert "Authorization" not in cross_headers


@pytest.mark.asyncio
async def test_encrypted_aes128_hls_manifest_demux_and_decrypts_via_relay():
    manifest_url = "https://media.example/live/encrypted.m3u8"
    key_url = "https://media.example/live/key.bin"
    segment_url = "https://media.example/live/segment.ts"
    key = bytes(range(16))
    iv = bytes(range(16, 32))
    packet = b"\x47" + (b"\x00" * 187)
    plaintext = packet * 2
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    encrypted_segment = encryptor.update(padded) + encryptor.finalize()
    manifest = (
        "#EXTM3U\n#EXT-X-VERSION:3\n"
        f'#EXT-X-KEY:METHOD=AES-128,URI="key.bin",IV=0x{iv.hex()}\n'
        "#EXTINF:1.0,\nsegment.ts\n#EXT-X-ENDLIST\n"
    ).encode()
    bodies = {manifest_url: manifest, key_url: key, segment_url: encrypted_segment}

    @asynccontextmanager
    async def fake_request(url, **_kwargs):
        yield httpx.Response(
            200,
            content=bodies[url],
            headers={
                "Content-Type": "application/vnd.apple.mpegurl"
                if url == manifest_url
                else "application/octet-stream"
            },
            request=httpx.Request("GET", url),
        )

    with patch("security.stream_outbound.stream_request", fake_request):
        async with validated_subprocess_input(manifest_url) as subprocess_input:
            async with httpx.AsyncClient() as client:
                rewritten = (await client.get(subprocess_input.argument)).text
                local_key_url = rewritten.split('URI="', 1)[1].split('"', 1)[0]
                local_segment_url = next(
                    line for line in rewritten.splitlines() if line.startswith("http://")
                )
                relayed_key = (await client.get(local_key_url)).content
                relayed_segment = (await client.get(local_segment_url)).content

    decryptor = Cipher(algorithms.AES(relayed_key), modes.CBC(iv)).decryptor()
    padded_plaintext = decryptor.update(relayed_segment) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    demuxed = unpadder.update(padded_plaintext) + unpadder.finalize()
    assert demuxed == plaintext
    assert demuxed[0] == demuxed[188] == 0x47


@pytest.mark.asyncio
async def test_hls_relay_denies_metadata_resource_without_fetching_it():
    manifest_url = "https://media.example/live/master.m3u8"
    attempted = []

    @asynccontextmanager
    async def fake_request(url, **_kwargs):
        attempted.append(url)
        if url == "http://169.254.169.254/latest/meta-data":
            raise SSRFError("denied before connect")
        yield httpx.Response(
            200,
            content=b"#EXTM3U\nhttp://169.254.169.254/latest/meta-data\n",
            headers={"Content-Type": "application/vnd.apple.mpegurl"},
            request=httpx.Request("GET", url),
        )

    with patch("security.stream_outbound.stream_request", fake_request):
        async with validated_subprocess_input(manifest_url) as subprocess_input:
            async with httpx.AsyncClient() as client:
                manifest = (await client.get(subprocess_input.argument)).text
                metadata_relay_url = next(
                    line for line in manifest.splitlines() if line.startswith("http://")
                )
                response = await client.get(metadata_relay_url)

    assert response.status_code == 502
    assert attempted == [manifest_url, "http://169.254.169.254/latest/meta-data"]


@pytest.mark.asyncio
async def test_direct_relay_streams_bounded_chunks_and_closes_on_client_cancel():
    requested_chunk_sizes = []
    upstream_closed = False

    class StreamingResponse:
        status_code = 200
        headers = {"Content-Type": "video/mp2t"}
        extensions = {}

        def raise_for_status(self):
            return None

        async def aiter_bytes(self, chunk_size):
            requested_chunk_sizes.append(chunk_size)
            yield b"a" * chunk_size
            await asyncio.Event().wait()

    @asynccontextmanager
    async def fake_request(_url, **_kwargs):
        nonlocal upstream_closed
        try:
            yield StreamingResponse()
        finally:
            upstream_closed = True

    with patch("security.stream_outbound.stream_request", fake_request):
        async with validated_subprocess_input(
            "https://media.example/live.ts"
        ) as subprocess_input:
            async with httpx.AsyncClient() as client:
                async with client.stream("GET", subprocess_input.argument) as response:
                    async for _chunk in response.aiter_bytes():
                        break

    assert requested_chunk_sizes == [65536]
    assert upstream_closed


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
