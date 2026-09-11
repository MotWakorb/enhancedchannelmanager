"""SEC-995-1: actual process logging setup must not emit provider URL tokens."""

import asyncio
from contextlib import redirect_stderr
import io
import ipaddress
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import httpx
import pytest


@pytest.mark.parametrize("log_level", ["startup", "INFO", "DEBUG"])
@pytest.mark.parametrize("upstream_status", [200, 403])
def test_allowed_preview_redirect_hides_url_from_actual_console(log_level, upstream_status, tmp_path):
    (tmp_path / "mcp").mkdir(mode=0o700)
    env = {
        **os.environ,
        "CONFIG_DIR": str(tmp_path),
        "MCP_SECRETS_DIR": str(tmp_path / "mcp"),
        "DATABASE_URL": "sqlite:///:memory:",
        "LOG_LEVEL": "INFO",
    }
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), log_level, str(upstream_status)],
        env=env, capture_output=True, text=True, timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    proof = json.loads(result.stdout)
    assert proof == {"status": upstream_status, "hops": 2, "console_token_leaks": 0}


async def console_proof(log_level, upstream_status):
    # Import main once in a fresh process; no reloading or per-request logger changes.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    output = io.StringIO()
    with redirect_stderr(output):
        import main
        from fastapi import HTTPException
        from routers import stream_preview as preview
        from security import stream_outbound

        if log_level != "startup":
            main.set_log_level(log_level)
            main.install_observability(level=getattr(logging, log_level))

        sentinels = ["private-portal-995", "private-edge-995", "private-query-995"]
        edge_url = f"http://edge.example/{sentinels[1]}/serve?token={sentinels[2]}"
        requests = []

        async def provider(request):
            requests.append(request)
            if request.url.scheme == "https":
                return httpx.Response(302, headers={"Location": edge_url}, request=request)
            return httpx.Response(upstream_status, content=b"media", request=request)

        transport_class = stream_outbound.SSRFPinnedTransport
        with patch.object(stream_outbound, "SSRFPinnedTransport", side_effect=lambda **kwargs: transport_class(
            inner_factory=lambda: httpx.MockTransport(provider), **kwargs
        )), patch("security.ssrf._resolve", return_value=[ipaddress.ip_address("93.184.216.34")]):
            try:
                response = await preview._preview_response(
                    f"https://portal.example/{sentinels[0]}/live.ts", "passthrough",
                    {"User-Agent": "Provider/9"}, provider_media=True,
                )
            except HTTPException as exc:
                assert upstream_status == 403
                assert exc.status_code == 502
                assert exc.detail == "Upstream stream returned HTTP 403"
            else:
                assert upstream_status == 200
                assert b"".join([chunk async for chunk in response.body_iterator]) == b"media"

        assert len(requests) == 2
        assert requests[1].url.scheme == "http"
        assert requests[1].url.path == f"/{sentinels[1]}/serve"
        assert requests[1].url.params["token"] == sentinels[2]
        console = output.getvalue()
        assert not any(secret in console for secret in sentinels), console
        if upstream_status == 403:
            assert "[PREVIEW] Startup failed: Upstream stream returned HTTP 403" in console
        assert any(getattr(handler, "_ecm_json_handler", False) for handler in logging.getLogger().handlers)
    print(json.dumps({"status": upstream_status, "hops": len(requests), "console_token_leaks": 0}))


if __name__ == "__main__":
    asyncio.run(console_proof(sys.argv[1], int(sys.argv[2])))
