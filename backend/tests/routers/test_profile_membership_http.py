"""ECM router → real DispatcharrClient → isolated synthetic HTTP destination.

Modern/legacy models exercise create-on-bulk and update-only compatibility.
This does not execute upstream Django or the browser; recorded membership
contracts are covered by tests/integration/test_channel_profile_membership_contract.py.
"""
import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
import dispatcharr_client
import profile_recovery
from auth.tokens import create_access_token
from config import DispatcharrSettings
from database import get_session
from dispatcharr_client import DispatcharrClient
from fastapi import FastAPI
from models import User
from routers import channels, profiles
from starlette.requests import Request


@pytest_asyncio.fixture
async def profile_boundary(monkeypatch, test_session):
    state = {
        "rows": {i: True for i in range(1, 108)},
        "legacy": False, "fault": None, "fault_hits": 0, "requests": [],
    }

    async def handle(reader, writer):
        try:
            head = (await reader.readuntil(b"\r\n\r\n")).decode("ascii")
            request_line, *header_lines = head.strip().split("\r\n")
            method, path, _ = request_line.split()
            headers = dict(line.lower().split(": ", 1) for line in header_lines)
            body = await reader.readexactly(int(headers.get("content-length", "0")))
            data = json.loads(body) if body else None
            state["requests"].append((method, path, data))
            code, response = 200, {}
            if headers.get("x-api-key") != "new-synthetic-profile-test":
                code = 401
            elif method == "GET" and path == "/api/channels/profiles/7/":
                if state["fault"] == "read":
                    state["fault_hits"] += 1
                    code = 503
                else:
                    response = {
                        "id": 7, "name": "New synthetic profile",
                        "channels": [cid for cid, on in state["rows"].items() if on],
                    }
            elif method == "PATCH" and path.startswith("/api/channels/profiles/7/channels/"):
                bulk = path.endswith("/bulk-update/")
                updates = data["channels"] if bulk else [
                    {"channel_id": int(path.split("/")[-2]), "enabled": data["enabled"]}
                ]
                invalid = [item["channel_id"] for item in updates if item["channel_id"] not in range(1, 215)]
                if invalid:
                    code, response = 400, {"invalid_channels": invalid}
                elif state["fault"] == "reject":
                    state["fault_hits"] += 1
                    code = 503
                else:
                    for item in updates:
                        cid = item["channel_id"]
                        if state["fault"] == "partial" and cid == 109:
                            continue
                        if not bulk or not state["legacy"] or cid in state["rows"]:
                            state["rows"][cid] = item["enabled"]
                    if state["fault"] in {"lost", "partial"}:
                        state["fault_hits"] += 1
                        if state["fault"] == "lost":
                            return  # Close after writes land, without a response.
                        code = 503
                    response = {"status": "success"}
            else:
                code, response = 501, {"detail": "SYNTHETIC_UNIMPLEMENTED"}
            payload = json.dumps(response).encode()
            writer.write(
                f"HTTP/1.1 {code} Synthetic\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode() + payload
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    client = DispatcharrClient(DispatcharrSettings(
        url=f"http://127.0.0.1:{port}", auth_method="api_key",
        dispatcharr_api_key="new-synthetic-profile-test",
    ))
    state["client"] = client
    monkeypatch.setattr(profiles, "get_client", lambda: client)
    app = FastAPI()
    app.dependency_overrides[get_session] = lambda: test_session
    app.include_router(profiles.router)
    app.include_router(channels.router)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic-ecm") as local:
            yield local, state
    finally:
        await client.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_homogeneous_batches_cross_real_adapter_and_missing_rows_remain_distinct(profile_boundary, legacy):
    local, state = profile_boundary
    state["legacy"] = legacy
    for enabled, ids in [(True, list(range(108, 215))), (False, list(range(1, 108)))]:
        for offset in range(0, len(ids), 100):
            response = await local.patch("/api/channel-profiles/7/channels/bulk-update", json={
                "channel_ids": ids[offset:offset + 100], "enabled": enabled,
            })
            assert response.status_code == 200
    detail = await local.get("/api/channel-profiles/7")
    assert detail.json()["channels"] == ([] if legacy else list(range(108, 215)))
    assert 108 in state["rows"] if not legacy else 108 not in state["rows"]
    assert state["rows"][1] is False
    patches = [data for method, _, data in state["requests"] if method == "PATCH"]
    assert [len(data["channels"]) for data in patches] == [100, 7, 100, 7]
    assert all(len({item["enabled"] for item in data["channels"]}) == 1 for data in patches)
    assert [item["channel_id"] for data in patches for item in data["channels"]] == list(range(108, 215)) + list(range(1, 108))
    if legacy:
        response = await local.patch("/api/channel-profiles/7/channels/108", json={"enabled": True})
        assert response.status_code == 200
        assert (await local.get("/api/channel-profiles/7")).json()["channels"] == [108]


@pytest.mark.asyncio
@pytest.mark.parametrize("fault,landed", [("reject", []), ("partial", [108]), ("lost", [108, 109])])
async def test_failed_write_response_does_not_replace_destination_readback(profile_boundary, fault, landed):
    local, state = profile_boundary
    state["fault"] = fault
    response = await local.patch("/api/channel-profiles/7/channels/bulk-update", json={
        "channel_ids": [108, 109], "enabled": True,
    })
    assert response.status_code == 500  # Current mapping for upstream 5xx/transport loss.
    assert state["fault_hits"] == 1
    state["fault"] = None
    assert (await local.get("/api/channel-profiles/7")).json()["channels"] == list(range(1, 108)) + landed
    assert len([r for r in state["requests"] if r[0] == "PATCH"]) == 1


@pytest.mark.asyncio
async def test_read_failure_and_invalid_batch_are_not_success(profile_boundary):
    local, state = profile_boundary
    response = await local.patch("/api/channel-profiles/7/channels/bulk-update", json={
        "channel_ids": [108, 999], "enabled": True,
    })
    assert response.status_code == 400
    assert 108 not in state["rows"]
    state["fault"] = "read"
    assert (await local.get("/api/channel-profiles/7")).status_code == 500
    assert state["fault_hits"] == 1


@pytest.mark.asyncio
async def test_create_receipt_fences_recovery_to_the_actual_client(profile_boundary, monkeypatch):
    local, state = profile_boundary
    original = state["client"]
    replacement = DispatcharrClient(original.settings.model_copy(update={
        "url": original.base_url + "/replacement",
    }))
    different_account = DispatcharrClient(original.settings.model_copy(update={
        "dispatcharr_api_key": "different-synthetic-account",
    }))
    active = original
    monkeypatch.setattr(dispatcharr_client, "_client", None)
    monkeypatch.setattr(dispatcharr_client, "_client_settings_hash", None)
    monkeypatch.setattr(dispatcharr_client, "get_settings", lambda: active.settings)
    clients = {(client.base_url, client.settings.dispatcharr_api_key): client for client in (original, replacement, different_account)}
    monkeypatch.setattr(dispatcharr_client, "DispatcharrClient", lambda settings: clients[settings.url, settings.dispatcharr_api_key])
    monkeypatch.setattr(channels, "get_client", dispatcharr_client.get_client)
    monkeypatch.setattr(profiles, "get_client", dispatcharr_client.get_client)
    monkeypatch.setattr(original, "get_channels", AsyncMock(return_value={"results": []}))
    monkeypatch.setattr(channels, "write_journal_rows", lambda *args, **kwargs: 0)
    monkeypatch.setattr(channels.journal, "log_entry", lambda *args, **kwargs: 1)

    async def create(_payload):
        nonlocal active
        active = replacement  # Swap while the actual create still owns the original client.
        return {"id": 108, "name": "Synthetic committed channel"}

    monkeypatch.setattr(original, "create_channel", create)
    try:
        accepted = await local.post("/api/channels/bulk-commit", json={
            "operations": [{"type": "createChannel", "tempId": -1, "name": "Synthetic committed channel"}],
            "profileRecoveryOperator": "anonymous",
        })
        assert accepted.status_code == 202
        for _ in range(100):
            poll = (await local.get(f"/api/channels/bulk-commit/{accepted.json()['job_id']}")).json()
            if poll["status"] != "running":
                break
            await asyncio.sleep(0)
        assert poll["status"] == "completed"
        assert poll["result"]["tempIdMap"] == {"-1": 108}
        binding = poll["result"].get("profileRecoveryBinding")
        assert binding and original.base_url not in binding and original.settings.dispatcharr_api_key not in binding
        assert active is replacement
        headers = {"X-ECM-Profile-Recovery": binding}
        base = "/api/created-channel-profiles/7"
        for path, data in [
            (f"{base}/channels/bulk-update", {"channel_ids": [108], "enabled": True}),
            (f"{base}/channels/108", {"enabled": True}),
        ]:
            assert (await local.patch(path, json=data, headers=headers)).status_code == 409
            assert (await local.patch(path, json=data)).status_code == 409
        assert (await local.get(base, headers=headers)).status_code == 409
        active = different_account
        assert (await local.patch(f"{base}/channels/108", json={"enabled": True}, headers=headers)).status_code == 409
        assert state["requests"] == []

        active = original
        detail = await local.get(base, headers=headers)
        assert detail.status_code == 200
        assert detail.headers["Cache-Control"] == "private, no-store"
        # Swap on the route's next await (body read) after it verified original.
        read_json = Request.json

        async def swap_during_body_read(request):
            nonlocal active
            active = replacement
            return await read_json(request)

        with monkeypatch.context() as patch:
            patch.setattr(Request, "json", swap_during_body_read)
            response = await local.patch(f"{base}/channels/108", json={"enabled": True}, headers=headers)
            assert response.status_code == 200
            assert active is replacement
            assert state["requests"][-1][:2] == ("PATCH", "/api/channels/profiles/7/channels/108/")
            assert state["rows"][108]
            active = original
            response = await local.patch(f"{base}/channels/bulk-update", json={
                "channel_ids": [109], "enabled": True,
            }, headers=headers)
            assert response.status_code == 200
            assert active is replacement
            assert state["requests"][-1][:2] == ("PATCH", "/api/channels/profiles/7/channels/bulk-update/")
            assert state["rows"][109]
    finally:
        await replacement.close()
        await different_account.close()


@pytest.mark.asyncio
async def test_auth_optional_recovery_resolves_the_actual_signed_in_operator(profile_boundary, test_session, monkeypatch):
    local, state = profile_boundary
    first = User(username="first-recovery-operator", is_admin=True)
    second = User(username="second-recovery-operator", is_admin=True)
    test_session.add_all([first, second])
    test_session.commit()
    first_key = f"local#{first.id}"
    first_token = create_access_token(first.id, first.username)
    second_token = create_access_token(second.id, second.username)
    monkeypatch.setattr(profile_recovery, "get_session", lambda: test_session)
    operator = await local.post("/api/channels/bulk-commit", json={
        "operations": [], "profileRecoveryOperator": first_key,
    }, headers={"Authorization": f"Bearer {second_token}"})
    assert operator.status_code == 409

    path = "/api/created-channel-profiles/7/channels/108"
    binding = profile_recovery.recovery_binding(state["client"], first_key)
    headers = {"X-ECM-Profile-Recovery": binding}
    for authorization in [{}, {"Authorization": f"Bearer {second_token}"}]:
        assert (await local.patch(path, json={"enabled": True}, headers=headers | authorization)).status_code == 409
    assert state["requests"] == []
    assert (await local.patch(
        path, json={"enabled": True}, headers=headers | {"Authorization": f"Bearer {first_token}"},
    )).status_code == 200
    assert state["rows"][108]
