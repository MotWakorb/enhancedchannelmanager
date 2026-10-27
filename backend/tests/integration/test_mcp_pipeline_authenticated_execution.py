"""GH #1033: real middleware/dependencies, plans, replay, and durable results.

Only decision computation and the upstream transport are scripted; credentials
are disposable, authentication is not overridden, and no production is contacted.
"""

import asyncio
import json
import os
import socket
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from auth.mcp_service import (
    MCP_CLAIM_HEADER,
    ensure_mcp_service_credentials,
    issue_test_claim,
)
from auth.tokens import create_access_token
from models import ChannelPipelineExecution, User
from services.mutation_plan_store import MutationPlanStore

from tests.integration.test_gh1009_commit_partial_failure_evidence import (
    Upstream,
    _client,
    _payload,
)

PREFIX = "/api/channel-pipeline/run"
PUBLIC_KEY = "synthetic-inbound-client-key-gh1033"


@pytest.fixture
def mcp_auth(tmp_path):
    projection = tmp_path / "mcp-service.json"
    credentials = ensure_mcp_service_credentials(projection)
    settings = SimpleNamespace(require_auth=True, setup_complete=True)
    with (
        patch("main.get_auth_settings", return_value=settings),
        patch("auth.dependencies.get_auth_settings", return_value=settings),
        patch(
            "main.get_settings", return_value=SimpleNamespace(mcp_api_key=PUBLIC_KEY)
        ),
        patch(
            "auth.dependencies.get_settings",
            return_value=SimpleNamespace(mcp_api_key=PUBLIC_KEY),
        ),
        patch("main.MCP_SERVICE_FILE", projection),
        patch("auth.dependencies.MCP_SERVICE_FILE", projection),
        patch("services.mutation_plan_store.mutation_plan_store", MutationPlanStore()),
    ):
        yield credentials, settings


def signed_headers(credentials, path, body):
    return {
        "Authorization": f"Bearer {credentials.backend_key}",
        MCP_CLAIM_HEADER: issue_test_claim(credentials, "POST", path, body),
    }


@pytest.fixture
async def planning_upstream():
    upstream = Upstream()
    upstream.channels = {7: {"id": 7, "name": "Before"}}
    client = _client(upstream)
    engine = SimpleNamespace(
        client=client,
        _load_rules=AsyncMock(return_value=[]),
        _update_rule_stats=AsyncMock(),
    )
    payload = _payload([{"method": "update_channel", "args": [7, {"name": "After"}]}])
    try:
        with (
            patch(
                "routers.channel_pipeline._ensure_engine",
                AsyncMock(return_value=engine),
            ),
            patch(
                "routers.channel_pipeline._compute_pipeline_plan_payload",
                AsyncMock(return_value=payload),
            ),
            patch("routers.channel_pipeline.journal"),
        ):
            yield upstream
    finally:
        await client._client.aclose()


@pytest.fixture
async def backend_http(async_client):
    """Own a loopback socket; no lifespan/background scheduled tasks are started."""
    import uvicorn
    from main import app

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.setblocking(False)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            lifespan="off",
            log_level="critical",
            timeout_graceful_shutdown=5,
        )
    )
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:

        async def ready():
            while not server.started:
                if task.done():
                    await task
                    raise AssertionError("disposable backend stopped before serving")
                await asyncio.sleep(0.01)

        await asyncio.wait_for(ready(), timeout=10)
        yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=15)
        sock.close()


async def run_sidecar_process(interpreter, script, backend_url, projection):
    """Load sidecar modules in a child to avoid backend/config import collisions."""
    root = Path(__file__).resolve().parents[3]
    environment = {
        **os.environ,
        "ECM_URL": backend_url,
        "MCP_SECRETS_DIR": str(projection),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    environment.pop("PYTHONPATH", None)
    process = await asyncio.create_subprocess_exec(
        interpreter,
        "-c",
        script,
        cwd=root / "mcp-server",
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=60)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    assert process.returncode == 0, stdout.decode() + stderr.decode()
    return json.loads(stdout)


@pytest.mark.asyncio
async def test_real_sidecar_client_and_signer_cross_backend_http_pair(
    backend_http, planning_upstream, mcp_auth, tmp_path
):
    # No MCP SDK needed: this exercises the production sidecar HTTP client,
    # endpoint contracts and auth hook with its actual owner-only projection.
    script = """
import asyncio
import json
import ecm_client
from auth_claim import claim_context
from _endpoint_contracts import ENDPOINTS

async def main():
    client = ecm_client.get_ecm_client()
    try:
        with claim_context("run_channel_pipeline", "destructive", confirmed=True):
            plan = await client.call_endpoint(ENDPOINTS["ac_prepare_run"], body={"dry_run": False})
            result = await client.call_endpoint(ENDPOINTS["ac_commit_run"], body={
                "plan_id": plan["plan_id"], "plan_hash": plan["plan_hash"], "phase": "execute",
            })
        print(json.dumps(result))
    finally:
        if ecm_client._client is not None:
            await ecm_client._client.aclose()

asyncio.run(main())
"""
    result = await run_sidecar_process(sys.executable, script, backend_http, tmp_path)
    assert result["status"] == "completed"
    assert planning_upstream.channels[7]["name"] == "After"
    assert planning_upstream.calls == [("PATCH", "/api/channels/channels/7/")]


@pytest.mark.asyncio
async def test_private_sidecar_prepare_commit_updates_upstream_and_persists_execution(
    async_client, test_session, mcp_auth
):
    credentials, _ = mcp_auth
    upstream = Upstream()
    upstream.channels = {7: {"id": 7, "name": "Before"}}
    client = _client(upstream)
    engine = SimpleNamespace(
        client=client,
        _load_rules=AsyncMock(return_value=[]),
        _update_rule_stats=AsyncMock(),
    )
    payload = _payload([{"method": "update_channel", "args": [7, {"name": "After"}]}])
    try:
        with (
            patch(
                "routers.channel_pipeline._ensure_engine",
                AsyncMock(return_value=engine),
            ),
            patch(
                "routers.channel_pipeline._compute_pipeline_plan_payload",
                AsyncMock(return_value=payload),
            ),
            patch("routers.channel_pipeline.journal"),
        ):
            body = {"rule_ids": [7], "dry_run": False}
            prepared = await async_client.post(
                PREFIX + "/prepare",
                json=body,
                headers=signed_headers(credentials, PREFIX + "/prepare", body),
            )
            assert prepared.status_code == 200, prepared.text
            assert upstream.channels[7]["name"] == "Before"
            assert upstream.calls == []
            plan = prepared.json()
            body = {
                "plan_id": plan["plan_id"],
                "plan_hash": plan["plan_hash"],
                "phase": "execute",
            }
            committed = await async_client.post(
                PREFIX + "/commit",
                json=body,
                headers=signed_headers(credentials, PREFIX + "/commit", body),
            )
            assert committed.status_code == 202, committed.text
            assert upstream.channels[7]["name"] == "After"
            row = test_session.get(
                ChannelPipelineExecution, committed.json()["execution_id"]
            )
            assert row is not None and row.status == "completed"
            replay = await async_client.post(
                PREFIX + "/commit",
                json=body,
                headers=signed_headers(credentials, PREFIX + "/commit", body),
            )
            assert replay.status_code == 409
            assert upstream.calls == [("PATCH", "/api/channels/channels/7/")]
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["/prepare", "/commit"])
@pytest.mark.parametrize(
    "credential", ["missing", "invalid", "inbound", "no_claim", "wrong_body"]
)
async def test_pipeline_pair_refuses_invalid_authority_before_engine(
    async_client, mcp_auth, surface, credential
):
    credentials, _ = mcp_auth
    path = PREFIX + surface
    body = (
        {"dry_run": False}
        if surface == "/prepare"
        else {
            "phase": "execute",
            "plan_id": "unknown",
            "plan_hash": "unknown",
        }
    )
    headers = {}
    if credential == "invalid":
        headers = {"Authorization": "Bearer not-a-valid-credential"}
    elif credential == "inbound":
        headers = {"Authorization": f"Bearer {PUBLIC_KEY}"}
    elif credential == "no_claim":
        headers = {"Authorization": f"Bearer {credentials.backend_key}"}
    elif credential == "wrong_body":
        headers = signed_headers(credentials, path, {"different": "body"})
    with patch("routers.channel_pipeline._ensure_engine", AsyncMock()) as engine:
        response = await async_client.post(path, json=body, headers=headers)
    assert response.status_code in {401, 403}, response.text
    engine.assert_not_awaited()


@pytest.mark.asyncio
async def test_private_sidecar_cannot_use_legacy_one_shot_route(async_client, mcp_auth):
    credentials, _ = mcp_auth
    body = {"dry_run": False}
    with patch("routers.channel_pipeline._ensure_engine", AsyncMock()) as engine:
        response = await async_client.post(
            PREFIX,
            json=body,
            headers=signed_headers(credentials, PREFIX, body),
        )
    assert response.status_code == 403, response.text
    engine.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["/prepare", "/commit"])
async def test_non_admin_human_cannot_drive_pipeline_pair(
    async_client, test_session, mcp_auth, surface
):
    user = User(username="pipeline-reader", is_admin=False, is_active=True)
    test_session.add(user)
    test_session.commit()
    token = create_access_token(user.id, user.username, auth_epoch=user.auth_epoch)
    body = (
        {"dry_run": False}
        if surface == "/prepare"
        else {
            "phase": "execute",
            "plan_id": "unknown",
            "plan_hash": "unknown",
        }
    )
    with patch("routers.channel_pipeline._ensure_engine", AsyncMock()) as engine:
        response = await async_client.post(
            PREFIX + surface,
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 403, response.text
    engine.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["hash", "principal"])
async def test_plan_hash_and_principal_binding_survive_authenticated_pair(
    async_client, test_session, mcp_auth, planning_upstream, fault
):
    credentials, _ = mcp_auth
    body = {"dry_run": False}
    prepared = await async_client.post(
        PREFIX + "/prepare",
        json=body,
        headers=signed_headers(credentials, PREFIX + "/prepare", body),
    )
    assert prepared.status_code == 200, prepared.text
    plan = prepared.json()
    body = {
        "plan_id": plan["plan_id"],
        "plan_hash": plan["plan_hash"],
        "phase": "execute",
    }
    invalid_body = {**body, "plan_hash": "wrong"} if fault == "hash" else body
    if fault == "principal":
        user = User(username="different-admin", is_admin=True, is_active=True)
        test_session.add(user)
        test_session.commit()
        token = create_access_token(user.id, user.username, auth_epoch=user.auth_epoch)
        headers = {"Authorization": f"Bearer {token}"}
    else:
        headers = signed_headers(credentials, PREFIX + "/commit", invalid_body)
    denied = await async_client.post(
        PREFIX + "/commit", json=invalid_body, headers=headers
    )
    assert denied.status_code == 409, denied.text
    assert planning_upstream.channels[7]["name"] == "Before"
    assert planning_upstream.calls == []
    # Bad authority must not consume the valid principal's exact plan.
    committed = await async_client.post(
        PREFIX + "/commit",
        json=body,
        headers=signed_headers(credentials, PREFIX + "/commit", body),
    )
    assert committed.status_code == 202, committed.text
    assert planning_upstream.channels[7]["name"] == "After"


@pytest.mark.asyncio
async def test_auth_disabled_prepare_commit_retains_existing_anonymous_posture(
    async_client, mcp_auth, planning_upstream
):
    _, settings = mcp_auth
    settings.require_auth = False
    prepared = await async_client.post(PREFIX + "/prepare", json={"dry_run": False})
    assert prepared.status_code == 200, prepared.text
    plan = prepared.json()
    assert planning_upstream.calls == []
    committed = await async_client.post(
        PREFIX + "/commit",
        json={
            "plan_id": plan["plan_id"],
            "plan_hash": plan["plan_hash"],
            "phase": "execute",
        },
    )
    assert committed.status_code == 202, committed.text
    assert planning_upstream.channels[7]["name"] == "After"
