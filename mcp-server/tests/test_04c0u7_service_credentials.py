import json
import base64
import hashlib
import hmac
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from auth_claim import claim_context, request_claim_headers
from auth_claim import SidecarBackendAuth
from config import get_mcp_backend_credentials, get_mcp_backend_credentials_status


def test_sidecar_reads_internal_projection_not_external_client_key(tmp_path: Path):
    (tmp_path / "settings.json").write_text(
        json.dumps({"mcp_api_key": "<EXTERNAL_MCP_CLIENT_KEY>"})
    )
    (tmp_path / "mcp-service.json").write_text(json.dumps({
        "backend_key": "<INTERNAL_BACKEND_KEY>",
        "confirmation_key": "<CONFIRMATION_SIGNING_KEY>",
    }))
    with patch("config.MCP_SERVICE_FILE", tmp_path / "mcp-service.json"):
        credentials = get_mcp_backend_credentials()
    assert credentials == (
        "<INTERNAL_BACKEND_KEY>",
        "<CONFIRMATION_SIGNING_KEY>",
    )


def test_claims_bind_method_path_body_and_are_scoped_to_tool_invocation(tmp_path: Path):
    projection = tmp_path / "mcp-service.json"
    projection.write_text(json.dumps({
        "backend_key": "<INTERNAL_BACKEND_KEY>",
        "confirmation_key": "<CONFIRMATION_SIGNING_KEY>",
    }))
    with patch("config.MCP_SERVICE_FILE", projection):
        assert request_claim_headers("POST", "/api/x", {"id": 1}) == {}
        with claim_context("delete_channel", "destructive", confirmed=True):
            headers = request_claim_headers("POST", "/api/x", {"id": 1})
    assert headers["Authorization"] == "Bearer <INTERNAL_BACKEND_KEY>"
    assert headers["X-ECM-MCP-Claim"].startswith("v1.")
    assert "<INTERNAL_BACKEND_KEY>" not in headers["X-ECM-MCP-Claim"]


def test_backend_projection_readiness_is_strict_and_non_secret(tmp_path: Path):
    projection = tmp_path / "mcp-service.json"
    projection.write_text(json.dumps({
        "backend_key": "b" * 48,
        "confirmation_key": "c" * 48,
    }))
    projection.chmod(0o600)
    with patch("config.MCP_SERVICE_FILE", projection):
        assert get_mcp_backend_credentials_status() == "ok"
        projection.chmod(0o644)
        assert get_mcp_backend_credentials_status() == "insecure_permissions"
        projection.chmod(0o600)
        projection.write_text('{"backend_key":"short"}')
        assert get_mcp_backend_credentials_status() == "invalid_schema"
    assert "b" * 48 not in get_mcp_backend_credentials_status.__doc__


def test_compose_identity_can_read_backend_owned_projection(tmp_path: Path):
    """Model the shared PUID contract without weakening owner-only mode."""
    projection = tmp_path / "mcp-service.json"
    projection.write_text(json.dumps({
        "backend_key": "b" * 48,
        "confirmation_key": "c" * 48,
    }))
    projection.chmod(0o600)
    backend_uid = projection.stat().st_uid
    with patch("config.MCP_SERVICE_FILE", projection), patch(
        "config.os.geteuid", return_value=backend_uid
    ):
        assert get_mcp_backend_credentials_status() == "ok"
    with patch("config.MCP_SERVICE_FILE", projection), patch(
        "config.os.geteuid", return_value=backend_uid + 1
    ):
        assert get_mcp_backend_credentials_status() == "wrong_owner"


def test_image_and_compose_pin_matching_non_root_identity():
    repo = Path(__file__).resolve().parents[2]
    dockerfile = (repo / "mcp-server" / "Dockerfile").read_text()
    compose = (repo / "docker-compose.mcp.yml").read_text()
    assert "USER appuser:appgroup" in dockerfile
    assert 'user: "${PUID:-1000}:${PGID:-1000}"' in compose


@pytest.mark.asyncio
async def test_async_auth_signs_multipart_json_and_query_requests(tmp_path: Path):
    projection = tmp_path / "mcp-service.json"
    projection.write_text(json.dumps({
        "backend_key": "b" * 48,
        "confirmation_key": "c" * 48,
    }))
    projection.chmod(0o600)
    observed = []

    async def backend(request: httpx.Request) -> httpx.Response:
        body = await request.aread()
        version, timestamp, nonce, encoded = request.headers[
            "X-ECM-MCP-Claim"
        ].split(".", 3)
        canonical = body
        try:
            canonical = json.dumps(
                json.loads(body), sort_keys=True, separators=(",", ":")
            ).encode()
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
        payload = b"\0".join((
            timestamp.encode(),
            nonce.encode(),
            request.method.encode(),
            request.url.raw_path,
            hashlib.sha256(canonical).hexdigest().encode(),
        ))
        expected = hmac.new(b"c" * 48, payload, hashlib.sha256).digest()
        supplied = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        assert version == "v1"
        assert hmac.compare_digest(supplied, expected)
        observed.append((request.url.raw_path, body, dict(request.headers)))
        return httpx.Response(200, json={"ok": True})

    with patch("config.MCP_SERVICE_FILE", projection):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(backend),
            base_url="http://backend",
            auth=SidecarBackendAuth(),
        ) as client:
            with claim_context("import_channels_csv", "destructive", confirmed=True):
                response = await client.post(
                    "/api/channels/import-csv?mode=replace",
                    files={"file": ("channels.csv", b"name,number\nOne,1\n", "text/csv")},
                )
            with claim_context("json_regression", "read-only", confirmed=True):
                await client.post("/api/example?dry_run=true", json={"id": 7})
    assert response.json() == {"ok": True}
    assert b"channels.csv" in observed[0][1]
    assert observed[0][0] == b"/api/channels/import-csv?mode=replace"
    assert observed[1][0] == b"/api/example?dry_run=true"
    for _path, _body, headers in observed:
        assert headers["authorization"] == f"Bearer {'b' * 48}"
        assert headers["x-ecm-mcp-claim"].startswith("v1.")
