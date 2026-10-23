"""Bounded, real dependency regressions for P1 enhancedchannelmanager-xyfpa.

Official advisories (no external network is used by these tests):
https://github.com/certifi/python-certifi/security/advisories/GHSA-6q48-6c7w-9gw2
https://github.com/aio-libs/aiohttp/security/advisories/GHSA-5wr8-gcqf-gqf3
https://github.com/aio-libs/aiohttp/security/advisories/GHSA-9xw5-w5jj-pvcg
https://github.com/aio-libs/aiohttp/security/advisories/GHSA-2g87-pp6c-29x5

The first two aiohttp advisories are CVE-2026-111064 and CVE-2026-111066.
Only tiny synthetic payloads and ephemeral IPv4 loopback listeners are used.
"""

import asyncio
import hashlib
import ssl
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aiohttp
import certifi
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

# Fingerprints from the upstream removal/restoration, not PEM comment matching:
# https://github.com/certifi/python-certifi/commit/5cb238caeeb6c30ec1a7309ad3e16a5aa7b10ce8
# https://github.com/certifi/python-certifi/commit/662df63d7906df66f1f07c0751a3dfe9da14d914
_COMPROMISED_G3 = "86a1ecba089c4a8d3bbe2734c612ba341d813e043cf9e8a862cd5c57a36bbe6b"
_UNAFFECTED_G1 = "40f6af0346a99aa1cd1d555a4e9cce62c7f9634603ee406615833dc8c8d00367"
_IO_TIMEOUT = 5


def _trusted_ca_fingerprints() -> set[str]:
    context = ssl.create_default_context(cafile=certifi.where())
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname
    return {
        hashlib.sha256(certificate).hexdigest()
        for certificate in context.get_ca_certs(binary_form=True)
    }


def test_certifi_does_not_trust_compromised_emsign_g3() -> None:
    assert _COMPROMISED_G3 not in _trusted_ca_fingerprints()


def test_certifi_still_trusts_unaffected_emsign_g1() -> None:
    # Positive control: 2026.10.18 removed too much; 2026.10.19 restores G1.
    assert _UNAFFECTED_G1 in _trusted_ca_fingerprints()


@asynccontextmanager
async def _loopback_server(
    app: web.Application, *, keepalive_timeout: float = 1
) -> AsyncIterator[TestServer]:
    server = TestServer(app, host="127.0.0.1")
    try:
        async with asyncio.timeout(_IO_TIMEOUT):
            # Runner options belong to start_server(), not TestServer.__init__.
            await server.start_server(
                keepalive_timeout=keepalive_timeout, shutdown_timeout=0.5
            )
            yield server
    finally:
        async with asyncio.timeout(_IO_TIMEOUT):
            await server.close()


@pytest.mark.asyncio
async def test_aiohttp_closes_idle_connection_before_first_request() -> None:
    handled = []

    async def handler(request: web.Request) -> web.Response:
        handled.append(request.path)
        return web.Response(text="ok")

    app = web.Application()
    app.router.add_get("/", handler)
    async with _loopback_server(app, keepalive_timeout=0.2) as server:
        reader, writer = await asyncio.open_connection(server.host, server.port)
        try:
            # A vulnerable server holds this fresh connection open indefinitely.
            assert await asyncio.wait_for(reader.read(), timeout=2) == b""
            assert handled == []
        finally:
            writer.close()
            await asyncio.wait_for(writer.wait_closed(), timeout=_IO_TIMEOUT)


@pytest.mark.asyncio
async def test_aiohttp_first_request_deadline_allows_normal_get() -> None:
    handled = []

    async def handler(request: web.Request) -> web.Response:
        handled.append(request.path)
        return web.Response(text="synthetic normal response")

    app = web.Application()
    app.router.add_get("/", handler)
    async with (
        _loopback_server(app, keepalive_timeout=1) as server,
        aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=_IO_TIMEOUT), trust_env=False
        ) as session,
        session.get(server.make_url("/")) as response,
    ):
        assert response.status == 200
        assert await response.text() == "synthetic normal response"
    assert handled == ["/"]


def _empty_multipart_files() -> bytes:
    return (
        b"".join(
            b'--b\r\nContent-Disposition: form-data; name="f%d"; '
            b'filename="synthetic.txt"\r\n\r\n\r\n' % index
            for index in range(55)
        )
        + b"--b--\r\n"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body_limit", "expected_status"),
    [(1, 413), (64 * 1024, 200)],
    ids=["multipart-overhead-is-limited", "ordinary-empty-files-still-parse"],
)
async def test_aiohttp_multipart_size_counts_empty_file_overhead(
    body_limit: int, expected_status: int
) -> None:
    async def handler(request: web.Request) -> web.Response:
        fields = await request.post()
        assert list(fields) == [f"f{index}" for index in range(55)]
        for field in fields.values():
            assert isinstance(field, web.FileField)
            assert field.filename == "synthetic.txt"
            assert field.file.read() == b""
        return web.Response(text="55 empty files parsed")

    body = _empty_multipart_files()
    assert 1 < len(body) < 64 * 1024
    app = web.Application(client_max_size=body_limit)
    app.router.add_post("/", handler)
    async with (
        _loopback_server(app) as server,
        aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=_IO_TIMEOUT), trust_env=False
        ) as session,
        session.post(
            server.make_url("/"),
            data=body,
            headers={"Content-Type": "multipart/form-data; boundary=b"},
        ) as response,
    ):
        assert response.status == expected_status
        text = await response.text()
        if expected_status == 200:
            assert text == "55 empty files parsed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cookie_count", "parsed_count"),
    [(1, 1), (51, 50)],
    ids=["normal-cookie-control", "response-cookie-budget"],
)
async def test_aiohttp_response_cookie_parsing_is_bounded_but_headers_survive(
    cookie_count: int, parsed_count: int
) -> None:
    cookie_headers = [f"cookie{index}=value" for index in range(cookie_count)]

    async def handler(request: web.Request) -> web.Response:
        return web.Response(
            text="synthetic cookie response",
            headers=[("Set-Cookie", value) for value in cookie_headers],
        )

    app = web.Application()
    app.router.add_get("/", handler)
    async with (
        _loopback_server(app) as server,
        aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=_IO_TIMEOUT), trust_env=False
        ) as session,
        session.get(server.make_url("/")) as response,
    ):
        assert response.status == 200
        assert await response.text() == "synthetic cookie response"
        assert response.headers.getall("Set-Cookie") == cookie_headers
        assert [
            value.decode("ascii")
            for name, value in response.raw_headers
            if name.lower() == b"set-cookie"
        ] == cookie_headers
        assert len(response.cookies) == parsed_count
        assert set(response.cookies) == {
            f"cookie{index}" for index in range(parsed_count)
        }
        assert all(cookie.value == "value" for cookie in response.cookies.values())
