"""Shared selection reaches provider HTTP requests in every probe path."""

import asyncio
import ipaddress
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from config import get_settings
from security import stream_outbound
from stream_prober import StreamProber


@pytest.mark.asyncio
async def test_full_probe_and_retry_send_account_ua_at_provider_boundary(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "stream_user_agent", "dispatcharr")
    client = AsyncMock()
    client.get_stream.return_value = {"m3u_account": 8, "stream_profile_id": 999}
    client.get_m3u_account.return_value = {"user_agent": 2}
    client.get_user_agents.return_value = [{"id": 2, "user_agent": "Account/7", "is_active": False}]
    prober = StreamProber(client, use_resdet_for_resolution=True, black_screen_detection_enabled=True,
                          _resdet_lock_path=tmp_path / "resdet.lock")
    prober.probe_retry_delay = 0
    prober._save_probe_result = MagicMock(return_value={"probe_status": "success"})
    prober._push_stats_to_dispatcharr = AsyncMock()
    seen = []
    responses = []

    async def handler(request):
        seen.append(request)
        response = httpx.Response(200, content=b"media", request=request)
        responses.append(response)
        return response

    attempts = 0

    async def spawn(*cmd, **kwargs):
        nonlocal attempts
        process = MagicMock(returncode=0)
        process.wait = AsyncMock(return_value=0)
        if cmd[0] == "ffprobe":
            attempts += 1
            if attempts == 1:
                process.returncode = 1
                output, error = b"", b"Connection reset"
            else:
                output, error = json.dumps({"streams": [{"codec_type": "video"}]}).encode(), b""
        elif "-frames:v" in cmd:
            output, error = b"YUV4MPEG2 W2 H2 F25:1 Ip A1:1 C420jpeg\nFRAME\n" + b"\x10" * 6, b""
        elif "/usr/local/bin/resdet" in cmd:
            output, error = b"2 2", b""
        else:
            output, error = b"", b"lavfi.signalstats.YAVG=16"
        process.communicate = AsyncMock(return_value=(output, error))
        process.stdout = asyncio.StreamReader()
        process.stdout.feed_data(output)
        process.stdout.feed_eof()
        return process

    transport_class = stream_outbound.SSRFPinnedTransport
    with patch.object(stream_outbound, "SSRFPinnedTransport", side_effect=lambda **kwargs: transport_class(
        inner_factory=lambda: httpx.MockTransport(handler), **kwargs
    )), patch("security.ssrf._resolve", return_value=[ipaddress.ip_address("93.184.216.34")]), patch(
        "stream_prober.asyncio.create_subprocess_exec", side_effect=spawn
    ):
        result = await prober.probe_stream(42, "https://provider.example/live.ts")

    assert result == {"probe_status": "success"}
    assert len(seen) == 5  # ffprobe twice, resdet frame, bitrate, blackscreen
    assert {request.headers["User-Agent"] for request in seen} == {"Account/7"}
    assert {request.url.host for request in seen} == {"93.184.216.34"}
    assert all(response.is_closed for response in responses)
    client.get_stream.assert_awaited_once_with(42)
    client.get_m3u_account.assert_awaited_once_with(8)
    client.get_user_agents.assert_awaited_once()
    client.get_core_settings.assert_not_awaited()


@pytest.mark.asyncio
async def test_standalone_blackscreen_resolves_the_stream_account(monkeypatch):
    monkeypatch.setattr(get_settings(), "stream_user_agent", "dispatcharr")
    client = AsyncMock()
    client.get_stream.return_value = {"m3u_account": 11}
    client.get_m3u_account.return_value = {"user_agent": 4}
    client.get_user_agents.return_value = [{"id": 4, "user_agent": "Standalone/4"}]
    prober = StreamProber(client)
    # Stop at the outbound boundary, after resolving the standalone stream.
    with patch("stream_prober.validated_subprocess_input", side_effect=RuntimeError("boundary")) as outbound:
        with pytest.raises(RuntimeError, match="boundary"):
            await prober._detect_black_screen("https://provider.example/live.ts", stream_id=52)
    assert outbound.call_args.kwargs["headers"] == {"User-Agent": "Standalone/4"}
    client.get_stream.assert_awaited_once_with(52)
