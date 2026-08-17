"""Network-level regression tests for the malformed-Host auth bypass.

These tests deliberately use Uvicorn's h11 HTTP implementation and a raw TCP
request.  Higher-level clients normalize or reject the poisoned ``Host`` value
before it reaches Starlette and therefore cannot reproduce CVE-2026-48710.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_until_listening(process: subprocess.Popen, port: int) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(
                f"Uvicorn exited before listening ({process.returncode})\n"
                f"stdout:\n{stdout}\nstderr:\n{stderr}"
            )
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return
        except OSError:
            time.sleep(0.05)
    raise AssertionError("Uvicorn did not listen within 10 seconds")


def _raw_request(port: int, request: bytes) -> bytes:
    chunks: list[bytes] = []
    with socket.create_connection(("127.0.0.1", port), timeout=2) as sock:
        sock.sendall(request)
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    return b"".join(chunks)


def _status_code(response: bytes) -> int:
    status_line = response.split(b"\r\n", 1)[0]
    return int(status_line.split(b" ", 2)[1])


@pytest.fixture
def uvicorn_h11_server(tmp_path: Path):
    port = _free_port()
    marker = tmp_path / "tool-dispatched"
    env = {
        **os.environ,
        "ECM_MCP_DISPATCH_MARKER": str(marker),
        "ECM_MCP_TEST_API_KEY": "integration-secret",
        "PYTHONPATH": str(Path(__file__).parents[1]),
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "tests.fixtures.host_security_app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--http",
            "h11",
            "--log-level",
            "warning",
        ],
        cwd=Path(__file__).parents[1],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    _wait_until_listening(process, port)
    try:
        yield port, marker
    finally:
        process.terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)


def test_poisoned_host_cannot_exempt_mcp_or_dispatch_tools(uvicorn_h11_server):
    port, marker = uvicorn_h11_server
    response = _raw_request(
        port,
        b"POST /mcp HTTP/1.1\r\n"
        b"Host: localhost/health?ignored=\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: 2\r\n"
        b"Connection: close\r\n\r\n{}",
    )

    assert _status_code(response) == 401, response
    assert marker.exists() is False, "poisoned request reached MCP tool dispatch"


def test_health_remains_public_over_uvicorn_h11(uvicorn_h11_server):
    port, _marker = uvicorn_h11_server
    response = _raw_request(
        port,
        b"GET /health HTTP/1.1\r\n"
        b"Host: localhost\r\n"
        b"Connection: close\r\n\r\n",
    )

    assert _status_code(response) == 200, response


@pytest.mark.parametrize(
    "host",
    [
        b"localhost/health?ignored=",
        b"attacker.invalid",
        b"localhost:not-a-port",
        b"localhost:70000",
        b"::1",
    ],
)
def test_invalid_host_with_valid_key_never_reaches_tool_dispatch(
    uvicorn_h11_server, host: bytes
):
    port, marker = uvicorn_h11_server
    response = _raw_request(
        port,
        b"POST /mcp HTTP/1.1\r\n"
        + b"Host: " + host + b"\r\n"
        + b"Authorization: Bearer integration-secret\r\n"
        + b"Content-Type: application/json\r\n"
        + b"Content-Length: 2\r\n"
        + b"Connection: close\r\n\r\n{}",
    )

    assert _status_code(response) in {400, 421}, response
    assert marker.exists() is False, "untrusted Host reached MCP tool dispatch"


@pytest.mark.parametrize("host", [b"localhost:6101", b"[::1]:6101", b"ecm-mcp"])
def test_valid_default_host_with_key_reaches_dispatch(uvicorn_h11_server, host: bytes):
    port, marker = uvicorn_h11_server
    response = _raw_request(
        port,
        b"POST /mcp HTTP/1.1\r\n"
        + b"Host: " + host + b"\r\n"
        + b"Authorization: Bearer integration-secret\r\n"
        + b"Content-Type: application/json\r\n"
        + b"Content-Length: 2\r\n"
        + b"Connection: close\r\n\r\n{}",
    )

    assert _status_code(response) == 200, response
    assert marker.read_text() == "dispatched"


def test_poisoned_host_cannot_use_public_health_route(uvicorn_h11_server):
    port, _marker = uvicorn_h11_server
    response = _raw_request(
        port,
        b"GET /health HTTP/1.1\r\n"
        b"Host: localhost/health?ignored=\r\n"
        b"Connection: close\r\n\r\n",
    )

    assert _status_code(response) in {400, 421}, response
