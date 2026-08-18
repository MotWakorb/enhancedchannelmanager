"""HSTS is restricted to transport the application can authenticate itself."""

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
async def test_hsts_is_not_added_from_untrusted_forwarded_proto():
    response = await security_headers_middleware(
        _request("http", b"https"), AsyncMock(return_value=Response())
    )
    assert "strict-transport-security" not in response.headers
