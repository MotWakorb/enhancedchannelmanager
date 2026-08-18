"""SSRF-safe outbound helpers for stream preview and probing.

HTTP callers connect to the address returned by the shared SSRF validator and
manually follow redirects so every hop is validated and pinned independently.
Subprocess callers cannot inject a custom resolver into FFmpeg, so
``validate_stream_subprocess_url`` provides the bounded alternative documented
in ``docs/security/stream_outbound_ssrf.md``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, Mapping
from urllib.parse import urljoin
from urllib.parse import urlsplit

import httpx

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
        mode: SSRFMode | None = None,
        verify: bool = True,
    ) -> None:
        self._inner = inner or httpx.AsyncHTTPTransport(verify=verify)
        self._mode = mode

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
                yield response
            finally:
                await response.aclose()
            return


def validate_stream_subprocess_url(url: str) -> None:
    """Fail closed immediately before an FFmpeg/ffprobe subprocess starts.

    FFmpeg owns DNS and redirect handling internally, so it cannot consume the
    pinned transport above. This validates literal addresses and every current
    DNS answer under the configured LAN policy at each process start/retry.
    See the bounded-design document for the residual race and redirect window.
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
    return parsed.scheme.lower(), (parsed.hostname or "").lower(), parsed.port
