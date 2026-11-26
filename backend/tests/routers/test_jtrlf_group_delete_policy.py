"""Recorded producer -> real client -> bulk job -> SQLite/JSON contract tests.

Only Dispatcharr HTTP is substituted. No operator service is contacted. Account
envelopes and non-provider groups below are controlled, synthetic arrangements;
the selected group-setting rows are recorded, allowlisted bundle data.
"""

import asyncio
import copy
import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from config import DispatcharrSettings
from dispatcharr_client import DispatcharrClient
from models import HiddenChannelGroup, JournalEntry


@pytest.fixture
async def provider():
    recorded = json.loads(
        (
            Path(__file__).parents[1] / "fixtures/jtrlf/recorded_group_settings.json"
        ).read_text()
    )
    rows = copy.deepcopy(recorded["rows"])
    state = {
        "accounts": [
            {
                "id": 11,
                "name": "Recorded provider",
                "channel_groups": [
                    {k: v for k, v in row.items() if k != "m3u_account_id"}
                    for row in rows
                ],
            }
        ],
        "groups": [
            {"id": gid, "name": name}
            for gid, name in [
                (207, "USA | MLB Teams"),
                (599, "USA | MLB"),
                (42, "Default Group"),
                (700, "Manual"),
                (701, "Empty"),
                (702, "Rejected"),
            ]
        ],
        "channels": {
            11: {"id": 11, "name": "Member", "channel_group_id": 207, "streams": []}
        },
        "calls": [],
        "reject": {702: {"error": "Cannot delete group with associated channels"}},
        "accounts_status": 200,
    }

    def serve(request):
        path = request.url.path
        method = request.method
        state["calls"].append((method, path))
        if path == "/api/m3u/accounts/":
            return httpx.Response(state["accounts_status"], json=state["accounts"])
        if path == "/api/channels/groups/":
            return httpx.Response(200, json=state["groups"])
        if path == "/api/channels/channels/":
            return httpx.Response(
                200,
                json={
                    "results": list(state["channels"].values()),
                    "next": None,
                    "count": len(state["channels"]),
                },
            )
        if path.startswith("/api/channels/channels/"):
            cid = int(path.rstrip("/").split("/")[-1])
            if method == "PATCH":
                data = json.loads(request.content)
                assert data.get("channel_group_id", 1) is not None
                state["channels"][cid].update(data)
            elif method == "DELETE":
                state["channels"].pop(cid)
                return httpx.Response(204)
            return httpx.Response(200, json=state["channels"][cid])
        if method == "DELETE" and path.startswith("/api/channels/groups/"):
            gid = int(path.rstrip("/").split("/")[-1])
            if gid in state["reject"]:
                body = state["reject"][gid]
                return (
                    httpx.Response(400, json=body)
                    if isinstance(body, dict)
                    else httpx.Response(400, text=body)
                )
            if any(c["channel_group_id"] == gid for c in state["channels"].values()):
                return httpx.Response(
                    400, json={"error": "Cannot delete group with associated channels"}
                )
            if any(
                s["channel_group"] == gid
                for a in state["accounts"]
                for s in a["channel_groups"]
            ):
                return httpx.Response(
                    400,
                    json={"error": "Cannot delete group with M3U account associations"},
                )
            state["groups"][:] = [g for g in state["groups"] if g["id"] != gid]
            return httpx.Response(204)
        raise AssertionError((method, path))

    client = DispatcharrClient(
        DispatcharrSettings(
            url="http://controlled.invalid",
            auth_method="api_key",
            api_key="synthetic-jtrlf-key",
        )
    )
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(serve))
    with (
        patch("routers.channels.get_client", return_value=client),
        patch("routers.channel_groups.get_client", return_value=client),
    ):
        yield state
    await client._client.aclose()


async def _commit(client, operations, **options):
    accepted = await client.post(
        "/api/channels/bulk-commit", json={"operations": operations, **options}
    )
    assert accepted.status_code == 202, accepted.text
    for _ in range(300):
        await asyncio.sleep(0)
        poll = await client.get(
            f"/api/channels/bulk-commit/{accepted.json()['job_id']}"
        )
        body = poll.json()
        if body["status"] == "completed":
            return body["result"]
        assert body["status"] != "failed", body
    pytest.fail("controlled bulk job did not finish")


@pytest.mark.asyncio
@pytest.mark.parametrize("sync", [True, False])
@pytest.mark.parametrize("bulk", [True, False])
async def test_association_hides_before_any_membership_write_and_restores(
    async_client, test_session, provider, sync, bulk
):
    provider["accounts"][0]["channel_groups"][1]["auto_channel_sync"] = sync
    for _ in range(2):
        if bulk:
            result = await _commit(
                async_client, [{"type": "deleteChannelGroup", "groupId": 207}]
            )
            assert (
                result["operationsApplied"],
                result["operationsFailed"],
                result["operationsPartiallyApplied"],
            ) == (1, 0, 0)
            assert result["success"] is True
        else:
            response = await async_client.delete("/api/channel-groups/207")
            assert response.json()["status"] == "hidden"
    assert test_session.query(HiddenChannelGroup).filter_by(group_id=207).count() == 1
    assert provider["channels"][11]["channel_group_id"] == 207
    assert not [c for c in provider["calls"] if c[0] in ("PATCH", "DELETE")]
    visible = (await async_client.get("/api/channel-groups")).json()
    assert 207 not in {g["id"] for g in visible}
    hidden = (await async_client.get("/api/channel-groups/hidden")).json()
    assert [(g["id"], g["name"]) for g in hidden] == [(207, "USA | MLB Teams")]
    restored = await _commit(
        async_client, [{"type": "restoreChannelGroup", "groupId": 207}]
    )
    assert restored["operationsApplied"] == 1
    assert 207 in {
        g["id"] for g in (await async_client.get("/api/channel-groups")).json()
    }
    test_session.expire_all()
    assert test_session.query(HiddenChannelGroup).count() == 0
    if bulk:
        assert (
            test_session.query(JournalEntry)
            .filter_by(action_type="group_hide", entity_id=207)
            .count()
            == 1
        )


@pytest.mark.asyncio
async def test_mixed_batch_keeps_successes_and_reports_landed_moves_without_retry(
    async_client, test_session, provider, tmp_path
):
    provider["channels"][12] = {
        "id": 12,
        "name": "Manual member",
        "channel_group_id": 700,
    }
    provider["channels"][13] = {
        "id": 13,
        "name": "Rejected member",
        "channel_group_id": 702,
    }
    provider["channels"][14] = {
        "id": 14,
        "name": "Second rejected member",
        "channel_group_id": 702,
    }
    result = await _commit(
        async_client,
        [
            {"type": "updateChannel", "channelId": 11, "data": {"name": "Changed"}},
            {"type": "deleteChannelGroup", "groupId": 207},
            {"type": "deleteChannelGroup", "groupId": 700},
            {"type": "deleteChannelGroup", "groupId": 702},
            {"type": "deleteChannelGroup", "groupId": 701},
        ],
        continueOnError=True,
    )
    assert (
        result["operationsApplied"],
        result["operationsFailed"],
        result["operationsPartiallyApplied"],
    ) == (4, 1, 1)
    assert result["success"] is False and result["partial"] is True
    assert provider["channels"][11] == {
        "id": 11,
        "name": "Changed",
        "channel_group_id": 207,
        "streams": [],
    }
    assert provider["channels"][12]["channel_group_id"] == 42
    assert provider["channels"][13]["channel_group_id"] == 42
    assert provider["channels"][14]["channel_group_id"] == 42
    assert {g["id"] for g in provider["groups"]} == {42, 207, 599, 702}
    error = result["errors"][0]
    assert error["groupId"] == 702
    assert error["sideEffectsLanded"] is True
    assert "associated channels" in error["error"]
    assert "stay moved" in error["error"]
    assert "2 channel(s)" in error["error"]
    assert "702" in error["error"]
    assert provider["calls"].count(("DELETE", "/api/channels/groups/702/")) == 1
    rows = (
        test_session.query(JournalEntry)
        .filter(JournalEntry.action_type != "bulk_commit")
        .all()
    )
    assert sorted((r.action_type, r.entity_id) for r in rows) == [
        ("group_delete", 700),
        ("group_delete", 701),
        ("group_hide", 207),
        ("update", 11),
        ("update", 12),
        ("update", 13),
        ("update", 14),
    ]
    # Retain the actual serialized boundary for the disposable browser smoke.
    (tmp_path / "mixed-result.json").write_text(json.dumps(result))
    (tmp_path / "channels-after.json").write_text(
        json.dumps(list(provider["channels"].values()))
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {
            "error": "Cannot delete group with associated channels; http://user:secret@provider.invalid/live/user/secret/9?password=secret",
            "debug": "never-echo-metadata",
        },
        "<html>never-echo-metadata password=secret</html>",
        {"debug": "never-echo-metadata", "password": "secret"},
    ],
)
async def test_group_error_boundary_exposes_validation_not_raw_body_or_urls(
    async_client, provider, body
):
    provider["reject"][702] = body
    result = await _commit(
        async_client, [{"type": "deleteChannelGroup", "groupId": 702}]
    )
    error = result["errors"][0]
    message = error["error"]
    assert "702" in message
    assert "Dispatcharr" in message and "400" in message
    assert "never-echo-metadata" not in json.dumps(error)
    assert "secret" not in message and "provider.invalid" not in message
    assert "http://" not in message
    if isinstance(body, dict) and "error" in body:
        assert "associated channels" in message


@pytest.mark.asyncio
async def test_unavailable_association_lookup_fails_closed_before_reparent(
    async_client, provider
):
    provider["accounts_status"] = 503
    result = await _commit(
        async_client, [{"type": "deleteChannelGroup", "groupId": 207}]
    )
    assert (
        result["operationsApplied"],
        result["operationsFailed"],
        result["operationsPartiallyApplied"],
    ) == (0, 1, 0)
    assert not [c for c in provider["calls"] if c[0] in ("PATCH", "DELETE")]
    assert "207" in result["errors"][0]["error"]


@pytest.mark.asyncio
async def test_explicit_channel_deletes_still_land_before_protected_group_hide(
    async_client, provider, test_session
):
    result = await _commit(
        async_client,
        [
            {"type": "deleteChannel", "channelId": 11},
            {"type": "deleteChannelGroup", "groupId": 207},
            {"type": "deleteChannelGroup", "groupId": 599},
        ],
    )
    assert (result["operationsApplied"], result["operationsFailed"]) == (3, 0)
    assert provider["channels"] == {}
    assert test_session.query(HiddenChannelGroup).count() == 2
    assert [c for c in provider["calls"] if c[0] in ("DELETE", "PATCH")] == [
        ("DELETE", "/api/channels/channels/11/"),
    ]


@pytest.mark.asyncio
async def test_unknown_validation_and_known_association_reason_do_not_echo_secrets(
    async_client, provider
):
    from log_utils import register_sensitive_values

    register_sensitive_values("short", "long-synthetic-key")
    provider["reject"][702] = {
        "error": "Cannot delete group with M3U account associations: short long-synthetic-key unregistered-secret"
    }
    result = await _commit(
        async_client, [{"type": "deleteChannelGroup", "groupId": 702}]
    )
    message = result["errors"][0]["error"]
    assert "M3U account associations" in message
    assert not any(
        value in message
        for value in ("short", "long-synthetic-key", "unregistered-secret")
    )
    provider["reject"][702] = {
        "detail": "unknown validation short long-synthetic-key unregistered-secret"
    }
    result = await _commit(
        async_client, [{"type": "deleteChannelGroup", "groupId": 702}]
    )
    message = result["errors"][0]["error"]
    assert "HTTP 400" in message
    assert not any(
        value in message
        for value in (
            "short",
            "long-synthetic-key",
            "unregistered-secret",
            "unknown validation",
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("bulk", [True, False])
async def test_concurrent_hide_unique_conflict_is_an_applied_noop(
    async_client, provider, tmp_path, bulk
):
    """A second real SQLite writer wins between the guard's query and insert."""
    engine = create_engine(f"sqlite:///{tmp_path / 'hide-race.db'}")
    HiddenChannelGroup.__table__.create(engine)
    factory = sessionmaker(bind=engine)

    def losing_session():
        session = factory()

        @event.listens_for(session, "before_flush", once=True)
        def insert_winner(*_args):
            with factory() as winner:
                winner.add(
                    HiddenChannelGroup(group_id=207, group_name="USA | MLB Teams")
                )
                winner.commit()

        return session

    try:
        with patch("channel_group_delete.get_session", side_effect=losing_session):
            if bulk:
                result = await _commit(
                    async_client, [{"type": "deleteChannelGroup", "groupId": 207}]
                )
                assert (result["operationsApplied"], result["operationsFailed"]) == (
                    1,
                    0,
                )
                assert result["errors"] == []
            else:
                response = await async_client.delete("/api/channel-groups/207")
                assert response.status_code == 200
                assert response.json()["status"] == "hidden"
        with factory() as db:
            assert db.query(HiddenChannelGroup).count() == 1
        assert not [c for c in provider["calls"] if c[0] in ("PATCH", "DELETE")]
    finally:
        engine.dispose()
