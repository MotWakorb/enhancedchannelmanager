"""Security contract for MCP credential transport (bd-04c0u.3)."""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest
import server
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

_KEY = "<synthetic-04c0u3-key>"
_INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "security-test", "version": "1"},
    },
}
_HEADERS = {"Accept": "application/json, text/event-stream"}


def test_query_credentials_are_rejected_even_when_correct(client):
    with patch("server.get_mcp_api_key", return_value=_KEY):
        response = client.post(
            f"/mcp?api_key={_KEY}", headers=_HEADERS, json=_INITIALIZE
        )

    assert response.status_code == 400
    assert _KEY not in response.text
    assert "authorization" in response.json()["error"].lower()


def test_bearer_rotation_and_revocation_take_effect_without_restart(client):
    active_key = [_KEY]
    with patch("server.get_mcp_api_key", side_effect=lambda: active_key[0]):
        old_headers = {**_HEADERS, "Authorization": f"Bearer {_KEY}"}
        assert client.post("/mcp", headers=old_headers, json=_INITIALIZE).status_code == 200

        active_key[0] = "<synthetic-rotated-04c0u3-key>"
        assert client.post("/mcp", headers=old_headers, json=_INITIALIZE).status_code == 401
        new_headers = {
            **_HEADERS,
            "Authorization": f"Bearer {active_key[0]}",
        }
        assert client.post("/mcp", headers=new_headers, json=_INITIALIZE).status_code == 200

        active_key[0] = ""
        assert client.post("/mcp", headers=new_headers, json=_INITIALIZE).status_code == 503


def test_untrusted_browser_origin_is_rejected_before_auth(client):
    headers = {
        **_HEADERS,
        "Authorization": f"Bearer {_KEY}",
        "Origin": "https://attacker.invalid",
    }
    with patch("server.get_mcp_api_key", return_value=_KEY):
        response = client.post("/mcp", headers=headers, json=_INITIALIZE)

    assert response.status_code == 403


def test_required_https_rejects_direct_http_and_accepts_tls_scope():
    async def endpoint(_request):
        return PlainTextResponse("dispatched")

    protected = Starlette(
        routes=[Route("/mcp", endpoint, methods=["POST"])],
        middleware=[
            Middleware(
                server.MCPTransportSecurityMiddleware,
                allowed_origins=("https://mcp.example.home",),
                require_https=True,
            )
        ],
    )
    with TestClient(protected, base_url="http://mcp.example.home") as http_client:
        denied = http_client.post("/mcp")
    with TestClient(protected, base_url="https://mcp.example.home") as https_client:
        admitted = https_client.post("/mcp")

    assert denied.status_code == 400
    assert admitted.status_code == 200
    assert admitted.text == "dispatched"


def test_default_compose_publishes_only_on_loopback():
    compose = (Path(__file__).parents[2] / "docker-compose.mcp.yml").read_text()
    dockerfile = (Path(__file__).parents[1] / "Dockerfile").read_text()
    assert '127.0.0.1:${MCP_PORT:-6101}:${MCP_PORT:-6101}' in compose
    assert '"${MCP_PORT:-6101}:${MCP_PORT:-6101}"' not in compose
    assert "ENV MCP_BIND_ADDRESS=127.0.0.1" in dockerfile
    assert "MCP_BIND_ADDRESS=0.0.0.0" in compose


def test_remote_overlay_requires_https_and_explicit_proxy_trust():
    overlay = (
        Path(__file__).parents[2] / "docker-compose.mcp.remote.yml"
    ).read_text()
    assert "MCP_REQUIRE_HTTPS=true" in overlay
    assert "MCP_TRUSTED_PROXY_IPS" in overlay
    assert "0.0.0.0:${MCP_PORT:-6101}:${MCP_PORT:-6101}" in overlay


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _production_request(
    tmp_path: Path,
    request: bytes,
    *,
    trusted_proxies: str = "127.0.0.1",
    require_https: bool = False,
) -> tuple[bytes, str]:
    port = _free_port()
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "api-key").write_text(f"{_KEY}\n")
    env = {
        **os.environ,
        "MCP_SECRETS_DIR": str(tmp_path),
        "MCP_BIND_ADDRESS": "127.0.0.1",
        "MCP_PORT": str(port),
        "MCP_TRUSTED_PROXY_IPS": trusted_proxies,
        "MCP_REQUIRE_HTTPS": str(require_https).lower(),
        "PYTHONUNBUFFERED": "1",
    }
    process = subprocess.Popen(
        [sys.executable, "server.py"],
        cwd=Path(__file__).parents[1],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1) as sock:
                    sock.sendall(request)
                    response = b""
                    while chunk := sock.recv(65536):
                        response += chunk
                break
            except OSError:
                if process.poll() is not None:
                    break
                time.sleep(0.05)
        else:
            raise AssertionError("production MCP server did not listen")
    finally:
        process.terminate()
        output, _ = process.communicate(timeout=5)

    return response, output


@pytest.mark.parametrize(
    ("security_headers", "expected_status"),
    [
        ("Host: localhost\r\n", 401),
        (
            f"Host: invalid.example\r\nAuthorization: Bearer {_KEY}\r\n",
            400,
        ),
        (
            "Host: localhost\r\nOrigin: https://attacker.invalid\r\n",
            403,
        ),
    ],
    ids=("unauthenticated", "invalid-host", "invalid-origin"),
)
def test_production_access_log_classifies_attacker_target_without_recording_it(
    tmp_path: Path,
    security_headers: str,
    expected_status: int,
):
    injected_path = f"/not-a-route/%0d%0aFORGED-LOG-{_KEY}?credential={_KEY}"
    response, output = _production_request(
        tmp_path,
        (
            f"GET {injected_path} HTTP/1.1\r\n"
            f"{security_headers}"
            "Connection: close\r\n\r\n"
        ).encode(),
    )

    assert _KEY not in output
    assert "FORGED-LOG" not in output
    assert "/not-a-route" not in output
    assert str(expected_status).encode() in response.split(b"\r\n", 1)[0]
    assert (
        f"request method=GET route=other status={expected_status}" in output
    )


def test_production_uvicorn_accepts_forwarded_https_only_from_trusted_proxy(
    tmp_path: Path,
):
    request = (
        b"POST /mcp HTTP/1.1\r\n"
        b"Host: localhost\r\n"
        b"X-Forwarded-Proto: https\r\n"
        b"Content-Length: 0\r\nConnection: close\r\n\r\n"
    )
    untrusted_response, _ = _production_request(
        tmp_path / "untrusted",
        request,
        trusted_proxies="192.0.2.10",
        require_https=True,
    )
    trusted_response, _ = _production_request(
        tmp_path / "trusted",
        request,
        trusted_proxies="127.0.0.1",
        require_https=True,
    )

    assert b"400 Bad Request" in untrusted_response
    assert b"HTTPS is required" in untrusted_response
    assert b"401 Unauthorized" in trusted_response
