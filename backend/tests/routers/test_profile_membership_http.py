"""ECM router → real DispatcharrClient → isolated synthetic HTTP destination.

Modern/legacy models exercise create-on-bulk and update-only compatibility.
This does not execute upstream Django or the browser; recorded membership
contracts are covered by tests/integration/test_channel_profile_membership_contract.py.
"""
import asyncio
import json

import httpx
import pytest
import pytest_asyncio
from config import DispatcharrSettings
from dispatcharr_client import DispatcharrClient
from fastapi import FastAPI
from routers import profiles


@pytest_asyncio.fixture
async def profile_boundary(monkeypatch):
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
    monkeypatch.setattr(profiles, "get_client", lambda: client)
    app = FastAPI()
    app.include_router(profiles.router)
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
