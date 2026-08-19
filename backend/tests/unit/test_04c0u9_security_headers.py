"""HSTS is asserted only from the request scheme this process was handed."""

from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request
from starlette.responses import Response

from main import security_headers_middleware


def _request(scheme: str, forwarded: bytes | None = None) -> Request:
    headers = [] if forwarded is None else [(b"x-forwarded-proto", forwarded)]
    return Request({
        "type": "http", "method": "GET", "path": "/", "scheme": scheme,
        "server": ("ecm.test", 6143), "headers": headers,
    })


@pytest.mark.asyncio
async def test_hsts_is_added_on_direct_https():
    response = await security_headers_middleware(
        _request("https"), AsyncMock(return_value=Response())
    )
    assert response.headers["strict-transport-security"] == "max-age=31536000"


@pytest.mark.asyncio
async def test_hsts_carries_no_includesubdomains_and_no_preload():
    """Both would extend the pin past what ECM can promise for this host.

    ``max-age`` itself is a deliberate one-year pin (PO decision). Per RFC 6797
    section 8.3 that pin is HOST-scoped and PORT-AGNOSTIC, so visiting the HTTPS
    listener also force-upgrades ``http://<host>:6100`` for a year — the
    interaction is documented in README.md under "Port Configuration", together
    with how to reach break-glass once a pin exists.
    """
    response = await security_headers_middleware(
        _request("https"), AsyncMock(return_value=Response())
    )
    value = response.headers["strict-transport-security"]
    assert "includeSubDomains" not in value
    assert "preload" not in value


@pytest.mark.asyncio
async def test_hsts_reads_the_request_scope_scheme_not_a_forwarded_header():
    """ECM adds no ``X-Forwarded-Proto`` trust of its own on top of uvicorn's.

    NARROW CLAIM, deliberately. This constructs the ASGI scope directly, which
    is DOWNSTREAM of uvicorn's ``ProxyHeadersMiddleware`` — enabled by default
    (``proxy_headers=True``, ``forwarded_allow_ips`` defaulting to
    ``127.0.0.1``), living outside this application, and measured to rewrite
    ``scope['scheme']`` to ``https`` for a loopback client sending this header.
    Neither launcher passes ``--no-proxy-headers``
    (``backend/entrypoint.sh``, ``backend/tls/https_server.py``), and
    ``tls/subprocess_proxy.py`` relays client headers verbatim over loopback.

    So this test cannot fail while that is true, and it does not claim to: it
    pins that ``security_headers_middleware`` decides from the scheme in the
    scope it receives and never reads the header itself. The exposure is nil in
    any case — the header can only push the scheme toward ``https``, no
    cookie-emitting route is reachable through ``_FORWARD_ALLOWLIST``, and HSTS
    asserted over genuine cleartext is ignored by the client (RFC 6797 8.1).
    """
    response = await security_headers_middleware(
        _request("http", b"https"), AsyncMock(return_value=Response())
    )
    assert "strict-transport-security" not in response.headers
