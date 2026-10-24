"""Stop identifiers must stay a literal URL segment through the real client."""

from unittest.mock import patch

import httpx
import pytest
from config import DispatcharrSettings
from dispatcharr_client import DispatcharrClient


@pytest.fixture
async def stop_client():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(204)

    transport_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with patch("dispatcharr_client.httpx.AsyncClient", return_value=transport_client):
        client = DispatcharrClient(
            DispatcharrSettings(
                url="http://dispatcharr.invalid",
                auth_method="api_key",
                dispatcharr_api_key="synthetic-stop-key",
            )
        )
    try:
        yield client, seen
    finally:
        await transport_client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["stop_channel", "stop_client"])
@pytest.mark.parametrize(
    "channel_id",
    [
        "",
        ".",
        "..",
        "..?synthetic=1",
        "42?synthetic=1",
        "42#synthetic",
        "../other",
        "42/other",
        "42\\other",
        "%2f",
        "%252f",
        "%5c",
        "%255c",
        "42%other",
        "42.other",
        "42\x00other",
        "42\nother",
        "42\rother",
        "42\tother",
        "42\x7fother",
        " 42",
        "42 ",
        "é",
        "K",
        None,
        True,
        42.5,
    ],
)
async def test_invalid_stop_identifier_is_rejected_before_any_request(
    stop_client, method, channel_id
):
    client, seen = stop_client
    with patch.object(client, "_request", wraps=client._request) as request:
        with pytest.raises(ValueError, match="Invalid stop identifier"):
            await getattr(client, method)(channel_id)
        request.assert_not_awaited()
    assert seen == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path", [("stop_channel", "stop"), ("stop_client", "stop_client")]
)
@pytest.mark.parametrize(
    "channel_id",
    [42, "42", "12345678-1234-1234-1234-123456789abc", "legacy_Channel-42", "A" * 1024],
)
async def test_safe_stop_identifier_preserves_exact_path_and_bodyless_success(
    stop_client, method, path, channel_id
):
    client, seen = stop_client
    result = await getattr(client, method)(channel_id)

    assert result == {"success": True}
    assert len(seen) == 1
    request = seen[0]
    assert request.method == "POST"
    assert request.url.scheme == "http"
    assert request.url.host == "dispatcharr.invalid"
    assert request.url.path == f"/proxy/ts/{path}/{channel_id}"
    assert request.url.query == b""
    assert request.url.fragment == ""
    assert request.headers["X-API-Key"] == "synthetic-stop-key"
