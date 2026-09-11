"""Transient resolver recovery must retain reject-if-any-address-denied."""

import ipaddress
import socket
import threading
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from security.ssrf import DNSResolutionError, SSRFError, SSRFMode
from security.stream_outbound import SSRFPinnedTransport


@pytest.mark.asyncio
async def test_transient_dns_retries_off_loop_then_pins_the_validated_address():
    thread_ids = []
    answers = iter([socket.gaierror(socket.EAI_AGAIN, "temporary"), [ipaddress.ip_address("93.184.216.34")]])

    def resolve(*args):
        thread_ids.append(threading.get_ident())
        result = next(answers)
        if isinstance(result, Exception):
            raise result
        return result

    inner = AsyncMock()
    inner.handle_async_request.return_value = httpx.Response(200)
    transport = SSRFPinnedTransport(inner=inner, mode=SSRFMode.PUBLIC_ONLY)
    with patch("security.ssrf._resolve", side_effect=resolve):
        response = await transport.handle_async_request(httpx.Request("GET", "https://provider.example/live.ts"))
    assert response.status_code == 200
    assert len(thread_ids) == 2
    assert threading.get_ident() not in thread_ids
    assert inner.handle_async_request.await_args.args[0].url.host == "93.184.216.34"


@pytest.mark.asyncio
async def test_retry_does_not_cherry_pick_a_public_address():
    inner = AsyncMock()
    transport = SSRFPinnedTransport(inner=inner, mode=SSRFMode.PUBLIC_ONLY)
    with patch("security.ssrf._resolve", side_effect=[
        socket.gaierror(socket.EAI_AGAIN, "temporary"),
        [ipaddress.ip_address("93.184.216.34"), ipaddress.ip_address("169.254.169.254")],
    ]) as resolve:
        with pytest.raises(SSRFError):
            await transport.handle_async_request(httpx.Request("GET", "https://provider.example/live.ts"))
    assert resolve.call_count == 2
    inner.handle_async_request.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("error,count", [
    (socket.gaierror(socket.EAI_AGAIN, "transient"), 3),
    (socket.gaierror(socket.EAI_NONAME, "permanent"), 1),
    (OSError("arbitrary failure"), 1),
])
async def test_dns_failure_is_bounded_and_typed(error, count):
    inner = AsyncMock()
    transport = SSRFPinnedTransport(inner=inner, mode=SSRFMode.PUBLIC_ONLY)
    with patch("security.ssrf._resolve", side_effect=error) as resolve:
        with pytest.raises(DNSResolutionError):
            await transport.handle_async_request(httpx.Request("GET", "https://provider.example/live.ts"))
    assert resolve.call_count == count
    inner.handle_async_request.assert_not_awaited()
