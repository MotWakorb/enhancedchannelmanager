"""SSRF-safe outbound helpers for stream preview and probing.

HTTP callers connect to the address returned by the shared SSRF validator and
manually follow redirects so every hop is validated and pinned independently.
HTTP subprocess inputs use ECM's tokenized loopback relay; direct UDP/RTP/RTMP
inputs use ``validate_stream_subprocess_url`` as documented in
``docs/security/stream_outbound_ssrf.md``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
import re
import secrets
from typing import AsyncIterator, Callable, Mapping
from urllib.parse import urljoin
from urllib.parse import urlsplit

import httpx
from aiohttp import web

from security.ssrf import (
    SSRFError,
    SSRFMode,
    ResolvedTarget,
    check_redirect_depth,
    get_ssrf_mode,
    validate_outbound_url,
    validate_redirect,
)


class SSRFPinnedTransport(httpx.AsyncBaseTransport):
    """Validate each request and connect to its validated IP address."""

    def __init__(
        self,
        *,
        inner: httpx.AsyncBaseTransport | None = None,
        inner_factory: Callable[[], httpx.AsyncBaseTransport] | None = None,
        mode: SSRFMode | None = None,
        verify: bool = True,
    ) -> None:
        if inner is not None and inner_factory is not None:
            raise ValueError("Pass inner or inner_factory, not both")
        self._inner_factory = inner_factory or (
            None if inner is not None else lambda: httpx.AsyncHTTPTransport(verify=verify)
        )
        self._inner = inner or self._inner_factory()
        self._mode = mode
        self._origin: tuple[str, str, int] | None = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        original_url = str(request.url)
        mode = self._mode or get_ssrf_mode()
        prepared_target = request.extensions.get("ssrf_prepared_target")
        from_url = request.extensions.get("ssrf_from_url")
        if prepared_target is not None:
            target = prepared_target
        elif from_url:
            target = validate_redirect(str(from_url), original_url, mode)
        else:
            target = validate_outbound_url(original_url, mode)

        target_origin = (target.scheme, target.hostname.lower(), target.port)
        if self._origin is not None and target_origin != self._origin:
            if self._inner_factory is None:
                # An injected transport is generally a test double. It has no
                # safe recreation contract, so fail closed instead of risking
                # a TLS pool keyed only by the pinned IP address.
                raise SSRFError("Cross-origin request requires an isolated transport")
            await self._inner.aclose()
            self._inner = self._inner_factory()
        self._origin = target_origin

        host = f"[{target.ip}]" if target.ip.version == 6 else str(target.ip)
        pinned_request = httpx.Request(
            method=request.method,
            url=request.url.copy_with(host=host, port=target.port),
            headers=request.headers,
            stream=request.stream,
            extensions={
                **request.extensions,
                "sni_hostname": target.hostname,
            },
        )
        pinned_request.headers["Host"] = target.host_header
        return await self._inner.handle_async_request(pinned_request)

    async def aclose(self) -> None:
        await self._inner.aclose()


@asynccontextmanager
async def stream_request(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: httpx.Timeout | float | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    initial_target: ResolvedTarget | None = None,
) -> AsyncIterator[httpx.Response]:
    """Open a pinned streaming GET, revalidating and capping redirects."""

    pinned_transport = transport or SSRFPinnedTransport()
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
        transport=pinned_transport,
    ) as client:
        current_url = url
        current_headers = dict(headers or {})
        from_url: str | None = None
        depth = 0
        while True:
            request = client.build_request("GET", current_url, headers=current_headers)
            if from_url is None and initial_target is not None:
                request.extensions["ssrf_prepared_target"] = initial_target
            elif from_url is not None:
                request.extensions["ssrf_from_url"] = from_url
            response = await client.send(request, stream=True)

            location = response.headers.get("location")
            if response.is_redirect and location:
                await response.aclose()
                depth += 1
                check_redirect_depth(depth)
                next_url = urljoin(current_url, location)
                if _origin(current_url) != _origin(next_url):
                    current_headers = {
                        name: value
                        for name, value in current_headers.items()
                        if name.lower() != "authorization"
                    }
                from_url, current_url = current_url, next_url
                continue

            try:
                response.extensions["ssrf_logical_url"] = current_url
                yield response
            finally:
                await response.aclose()
            return


@dataclass(frozen=True)
class ValidatedSubprocessInput:
    """A subprocess input that cannot perform its own HTTP request."""

    argument: str
    is_http_relay: bool = False


_HLS_URI_RE = re.compile(r'URI="([^"]+)"')
_MAX_HLS_MANIFEST_BYTES = 2 * 1024 * 1024
_MAX_RELAY_RESOURCES = 1024


class _LocalStreamRelay:
    """Loopback-only token relay whose every upstream fetch uses ``stream_request``."""

    def __init__(self, url: str, headers: Mapping[str, str] | None, timeout) -> None:
        self._initial_url = url
        self._headers = dict(headers or {})
        self._timeout = timeout
        self._targets: dict[str, str] = {}
        self._tokens_by_target: dict[str, str] = {}
        self._runner: web.AppRunner | None = None
        self._initial_context = None
        self._initial_response = None
        self._initial_token = self._register(url)

    def _register(self, url: str) -> str:
        existing = self._tokens_by_target.get(url)
        if existing is not None:
            return existing
        if len(self._targets) >= _MAX_RELAY_RESOURCES:
            raise SSRFError("Stream manifest exceeds the relay resource limit")
        token = secrets.token_hex(16)
        while token in self._targets:
            token = secrets.token_hex(16)
        self._targets[token] = url
        self._tokens_by_target[url] = token
        return token

    async def _read_hls_manifest(self, response: httpx.Response) -> bytes:
        content_length = response.headers.get("content-length") or response.headers.get(
            "Content-Length"
        )
        if content_length is not None and int(content_length) > _MAX_HLS_MANIFEST_BYTES:
            raise web.HTTPRequestEntityTooLarge(
                max_size=_MAX_HLS_MANIFEST_BYTES, actual_size=int(content_length)
            )

        body = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=65536):
            remaining = _MAX_HLS_MANIFEST_BYTES + 1 - len(body)
            body.extend(chunk[:remaining])
            if len(body) > _MAX_HLS_MANIFEST_BYTES:
                raise web.HTTPRequestEntityTooLarge(
                    max_size=_MAX_HLS_MANIFEST_BYTES, actual_size=len(body)
                )
        return bytes(body)

    async def start(self) -> str:
        # Resolve the initial redirect chain before any subprocess can start.
        self._initial_context = stream_request(
            self._initial_url, headers=self._headers, timeout=self._timeout
        )
        self._initial_response = await self._initial_context.__aenter__()
        self._initial_response.raise_for_status()

        app = web.Application()
        app.router.add_get("/resource/{token}", self._handle)
        self._runner = web.AppRunner(app, access_log=None, shutdown_timeout=1.0)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}/resource/{self._initial_token}"

    async def close(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
        if self._initial_context is not None:
            await self._initial_context.__aexit__(None, None, None)
            self._initial_context = None

    def _headers_for(self, url: str) -> dict[str, str]:
        if _origin(url) == _origin(self._initial_url):
            return dict(self._headers)
        return {
            name: value
            for name, value in self._headers.items()
            if name.lower() != "authorization"
        }

    def _local_url(self, request: web.Request, upstream: str) -> str:
        token = self._register(upstream)
        return f"{request.scheme}://{request.host}/resource/{token}"

    def _rewrite_hls(self, request: web.Request, body: bytes, base_url: str) -> bytes:
        text = body.decode("utf-8-sig")
        lines = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                line = self._local_url(request, urljoin(base_url, stripped))
            elif "URI=\"" in line:
                line = _HLS_URI_RE.sub(
                    lambda match: f'URI="{self._local_url(request, urljoin(base_url, match.group(1)))}"',
                    line,
                )
            lines.append(line)
        return ("\n".join(lines) + "\n").encode()

    async def _serve(self, request: web.Request, response: httpx.Response, url: str):
        content_type = response.headers.get("content-type", "")
        is_hls = urlsplit(url).path.lower().endswith(".m3u8") or "mpegurl" in content_type.lower()
        if is_hls:
            body = await self._read_hls_manifest(response)
            base_url = str(response.extensions.get("ssrf_logical_url", url))
            rewritten = self._rewrite_hls(request, body, base_url)
            return web.Response(body=rewritten, content_type="application/vnd.apple.mpegurl")

        downstream = web.StreamResponse(
            status=response.status_code,
            headers={"Content-Type": content_type or "application/octet-stream"},
        )
        await downstream.prepare(request)
        async for chunk in response.aiter_bytes(chunk_size=65536):
            await downstream.write(chunk)
        await downstream.write_eof()
        return downstream

    async def _handle(self, request: web.Request):
        token = request.match_info["token"]
        url = self._targets.get(token)
        if url is None:
            raise web.HTTPNotFound()
        if token == self._initial_token and self._initial_response is not None:
            response, self._initial_response = self._initial_response, None
            return await self._serve(request, response, url)
        try:
            async with stream_request(
                url, headers=self._headers_for(url), timeout=self._timeout
            ) as response:
                response.raise_for_status()
                return await self._serve(request, response, url)
        except (SSRFError, httpx.HTTPError) as exc:
            raise web.HTTPBadGateway(text="Upstream stream resource denied") from exc


@asynccontextmanager
async def validated_subprocess_input(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: httpx.Timeout | float | None = None,
) -> AsyncIterator[ValidatedSubprocessInput]:
    """Resolve redirects before spawn and expose HTTP through a loopback relay.

    Direct IPTV transports remain subprocess-owned after address validation.
    For HTTP(S), ECM owns DNS, redirects, TLS identity and credentials for the
    lifetime of every resource; FFmpeg receives only opaque loopback URLs.
    """

    scheme = urlsplit(url).scheme.lower()
    if scheme not in {"http", "https"}:
        validate_stream_subprocess_url(url)
        yield ValidatedSubprocessInput(argument=url)
        return

    relay = _LocalStreamRelay(url, headers, timeout)
    try:
        relay_url = await relay.start()
        yield ValidatedSubprocessInput(
            argument=relay_url, is_http_relay=True
        )
    finally:
        await relay.close()




def validate_stream_subprocess_url(url: str) -> None:
    """Validate a direct non-HTTP FFmpeg/ffprobe input before process start.

    HTTP(S) inputs use :func:`validated_subprocess_input` and the pinned relay.
    This function preserves direct UDP/RTP/RTMP compatibility by validating
    literal addresses and every current DNS answer at each process start/retry.
    Those direct transports retain a DNS validation-to-connect race.
    """

    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    mode = get_ssrf_mode()
    if scheme in {"http", "https"}:
        validate_outbound_url(url, mode)
        return

    # FFmpeg also supports direct IPTV transports. Reuse the shared address
    # policy through a synthetic HTTP authority; only the scheme is synthetic,
    # while hostname/port and every resolved record are the real destination.
    if scheme not in {"udp", "rtp", "rtmp"}:
        raise SSRFError(f"Disallowed stream URL scheme '{scheme or '(none)'}'")
    if not parsed.hostname:
        raise SSRFError("Stream URL has no hostname")
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    try:
        port = parsed.port
    except ValueError as exc:
        raise SSRFError(f"Invalid port in stream URL: {exc}") from exc
    authority = f"{host}:{port}" if port is not None else host
    validate_outbound_url(f"http://{authority}/", mode)


def prepare_stream_http_url(url: str) -> ResolvedTarget:
    """Validate an HTTP stream once for a later pinned ``stream_request``."""

    return validate_outbound_url(url, get_ssrf_mode())


def _origin(url: str) -> tuple[str, str, int | None]:
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    port = parsed.port
    if port is None:
        port = 443 if scheme == "https" else 80 if scheme == "http" else None
    return scheme, (parsed.hostname or "").lower(), port
