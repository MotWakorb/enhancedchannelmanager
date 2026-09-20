"""GH #1009 / PR #1010: partial-replay evidence across the real boundaries.

The replay unit tests prove the classification on ``PartialReplayError``;
the router test injects a prebuilt error. Neither can see a regression in
how the outcome travels from the upstream socket to the 424 body and into
SQLite. This module drives that whole path with real code:

* the real ``DispatcharrClient`` over a controlled ``httpx.MockTransport``
  that plays Dispatcharr (and can apply a PATCH and then drop the
  response, or answer 4xx/5xx);
* the real ``replay_write_plan`` and its compensation pass;
* the real ``POST /api/auto-creation/run/commit`` handler over ASGI, so
  the assertions are on the HTTP response the MCP consumer receives;
* the real ``_mark_execution_failed`` persistence into the
  ``ChannelPipelineExecution`` row and the ``ChannelPipelineSnapshot``
  recovery evidence.

Only the plan computation is doubled (it returns the stored plan
unchanged, which is what a non-drifted commit sees).
"""
from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy.orm import sessionmaker

from config import DispatcharrSettings
from dispatcharr_client import DispatcharrClient
from models import ChannelPipelineExecution, ChannelPipelineSnapshot
from routers import channel_pipeline as router
from services import mutation_plan_store as store
from services.mutation_plan_store import canonical_hash

COMMIT_URL = "/api/auto-creation/run/commit"
CANARY = "SECRET-TOKEN-8f3a9c-canary"


class Upstream:
    """A scripted Dispatcharr behind ``httpx.MockTransport``.

    ``channels`` is the upstream state; ``script`` maps ``(METHOD, path)``
    to a behaviour: a callable receiving the request and returning an
    ``httpx.Response`` or raising an ``httpx`` transport error. Every request
    is appended to ``calls`` so a test can prove what upstream received.
    """

    def __init__(self) -> None:
        self.channels: dict[int, dict] = {}
        self.calls: list[tuple[str, str]] = []
        self.script: dict[tuple[str, str], object] = {}
        self.next_id = 101

    def handler(self, request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        self.calls.append(key)
        behaviour = self.script.get(key)
        if behaviour is not None:
            return behaviour(request)
        return self.default(request)

    def default(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/api/channels/channels/":
            body = json.loads(request.content or b"{}")
            new_id, self.next_id = self.next_id, self.next_id + 1
            self.channels[new_id] = {"id": new_id, **body}
            return httpx.Response(201, json=self.channels[new_id])
        if request.method == "POST" and path == "/api/channels/logos/":
            return httpx.Response(201, json={"id": 55})
        if path.startswith("/api/channels/channels/") and path.endswith("/"):
            channel_id = int(path.rstrip("/").rsplit("/", 1)[-1])
            if request.method == "PATCH":
                body = json.loads(request.content or b"{}")
                self.channels.setdefault(channel_id, {"id": channel_id}).update(body)
                return httpx.Response(200, json=self.channels[channel_id])
            if request.method == "DELETE":
                self.channels.pop(channel_id, None)
                return httpx.Response(204)
            if request.method == "GET":
                return httpx.Response(200, json=self.channels.get(channel_id, {"id": channel_id}))
        raise AssertionError(f"unexpected upstream call: {request.method} {path}")

    def apply_then_lose_response(self, request: httpx.Request) -> httpx.Response:
        """The PATCH lands upstream; the connection dies before the reply."""
        self.default(request)
        raise httpx.RemoteProtocolError("peer closed connection without sending a response")

    @staticmethod
    def reject(status: int):
        def _reject(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status, json={"detail": "rejected by upstream"})
        return _reject


def _client(upstream: Upstream) -> DispatcharrClient:
    client = DispatcharrClient(
        DispatcharrSettings(url="http://dispatcharr", auth_method="api_key", api_key="k")
    )
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(upstream.handler))
    return client


def _payload(writes: list[dict], snapshot: list[dict] | None = None) -> dict:
    return {
        "request": {"m3u_account_ids": None, "rule_ids": [7]},
        "result": {"event_sync": [], "planned_review_candidates": [], "execution_log": []},
        "write_plan": {
            "writes": [{"kwargs": {}, "event_sync": None, **w} for w in writes],
            "channel_preconditions": {}, "group_preconditions": {}, "profile_preconditions": {},
        },
        "snapshot": snapshot or [],
    }


async def _commit(async_client, test_engine, upstream: Upstream, payload: dict) -> tuple[httpx.Response, DispatcharrClient]:
    fresh_store = store.MutationPlanStore()
    plan = fresh_store.create(
        "channel_pipeline", payload, canonical_hash(router._canonical_pipeline_decision(payload)),
    )
    client = _client(upstream)
    engine = type("Engine", (), {"client": client})()
    try:
        with patch.object(store, "mutation_plan_store", fresh_store), \
             patch.object(router, "_ensure_engine", AsyncMock(return_value=engine)), \
             patch.object(router, "_compute_pipeline_plan_payload", AsyncMock(return_value=payload)), \
             patch.object(router, "get_session", sessionmaker(bind=test_engine)):
            response = await async_client.post(COMMIT_URL, json={
                "plan_id": plan.plan_id, "plan_hash": plan.payload_hash, "phase": "execute",
            })
    finally:
        await client._client.aclose()
    return response, client


def _persisted(test_engine, execution_id: int) -> tuple[ChannelPipelineExecution, dict, dict]:
    session = sessionmaker(bind=test_engine)()
    try:
        execution = session.get(ChannelPipelineExecution, execution_id)
        log = execution.get_execution_log()
        snapshot = session.query(ChannelPipelineSnapshot).filter_by(execution_id=execution_id).one()
        return execution, log[0] if log else {}, snapshot.get_channels_data().get("partial_replay", {})
    finally:
        session.close()


def _everywhere(response: httpx.Response, log_entry: dict, evidence: dict, execution: ChannelPipelineExecution) -> str:
    return " ".join([
        response.text, json.dumps(log_entry), json.dumps(evidence),
        execution.error_message or "",
    ])


# ---------------------------------------------------------------------------
# Item 1: unknown vs rejected vs unattempted, end to end.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_landed_write_with_lost_response_is_unknown_in_http_and_persistence(
    async_client, test_engine,
):
    upstream = Upstream()
    upstream.channels = {7: {"id": 7, "name": "Old"}, 8: {"id": 8, "name": "Doomed"}}
    upstream.script[("PATCH", "/api/channels/channels/7/")] = upstream.apply_then_lose_response
    payload = _payload([
        {"method": "update_channel", "args": [7, {"name": "New"}]},
        {"method": "delete_channel", "args": [8]},
    ])

    response, _ = await _commit(async_client, test_engine, upstream, payload)

    assert response.status_code == 424, response.text
    detail = response.json()["detail"]
    # Upstream DID change channel 7 — the inventory must not say otherwise.
    assert upstream.channels[7]["name"] == "New"
    assert 8 in upstream.channels                      # the delete was never attempted
    assert ("DELETE", "/api/channels/channels/8/") not in upstream.calls
    assert detail["failed_write"] == "update_channel:7"
    assert detail["failed_outcome"] == "unknown"
    assert detail["not_applied"] == ["delete_channel:8"]
    assert "update_channel:7" not in detail["not_applied"]
    assert detail["completed_writes"] == []
    assert detail["pre_mutation"] is False
    assert detail["compensation_errors"] == []

    execution, log_entry, evidence = _persisted(test_engine, detail["execution_id"])
    assert execution.status == "failed"
    assert log_entry["type"] == "partial_replay_failure"
    for persisted in (log_entry, evidence):
        assert persisted["failed_write"] == "update_channel:7"
        assert persisted["failed_outcome"] == "unknown"
        assert persisted["not_applied"] == ["delete_channel:8"]
        assert persisted["pre_mutation"] is False
        assert persisted["completed_targets"] == []


@pytest.mark.asyncio
async def test_confirmed_rejection_control_is_rejected_and_pre_mutation_everywhere(
    async_client, test_engine,
):
    upstream = Upstream()
    upstream.channels = {7: {"id": 7, "name": "Old"}, 8: {"id": 8}}
    upstream.script[("PATCH", "/api/channels/channels/7/")] = Upstream.reject(429)
    payload = _payload([
        {"method": "update_channel", "args": [7, {"name": "New"}]},
        {"method": "delete_channel", "args": [8]},
    ])

    with patch("dispatcharr_client._sleep", new=AsyncMock()):
        response, _ = await _commit(async_client, test_engine, upstream, payload)

    assert response.status_code == 424, response.text
    detail = response.json()["detail"]
    assert upstream.channels[7]["name"] == "Old"        # provably not applied
    assert detail["failed_write"] == "update_channel:7"
    assert detail["failed_outcome"] == "rejected"
    assert detail["pre_mutation"] is True
    assert detail["not_applied"] == ["delete_channel:8"]

    execution, log_entry, evidence = _persisted(test_engine, detail["execution_id"])
    assert execution.status == "failed"
    for persisted in (log_entry, evidence):
        assert persisted["failed_outcome"] == "rejected"
        assert persisted["pre_mutation"] is True
        assert persisted["not_applied"] == ["delete_channel:8"]


# ---------------------------------------------------------------------------
# Item 2: the resolved upstream id, through commit persistence, including a
# compensation failure where locating the remaining resource matters.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dependent_write_failure_names_the_real_created_id_in_http_and_persistence(
    async_client, test_engine,
):
    upstream = Upstream()
    upstream.script[("PATCH", "/api/channels/channels/101/")] = Upstream.reject(400)
    payload = _payload([
        {"method": "create_channel", "args": [{"name": "New"}]},
        {"method": "update_channel", "args": [-1, {"streams": [5]}]},
        {"method": "update_channel", "args": [-1, {"name": "Renamed"}]},
    ])

    response, _ = await _commit(async_client, test_engine, upstream, payload)

    assert response.status_code == 424, response.text
    detail = response.json()["detail"]
    assert ("POST", "/api/channels/channels/") in upstream.calls
    assert ("PATCH", "/api/channels/channels/101/") in upstream.calls
    assert ("DELETE", "/api/channels/channels/101/") in upstream.calls   # compensation hit the real id
    assert 101 not in upstream.channels
    assert detail["failed_index"] == 1
    assert detail["completed_writes"] == ["create_channel#0->101"]
    assert detail["failed_write"] == "update_channel:101"
    assert detail["not_applied"] == ["update_channel:101"]
    assert detail["failed_outcome"] == "rejected"
    assert detail["pre_mutation"] is False
    assert detail["compensation_errors"] == []
    assert "-1" not in response.text

    execution, log_entry, evidence = _persisted(test_engine, detail["execution_id"])
    for persisted in (log_entry, evidence):
        assert persisted["completed_targets"] == ["create_channel#0->101"]
        assert persisted["failed_write"] == "update_channel:101"
        assert persisted["not_applied"] == ["update_channel:101"]
    assert "-1" not in json.dumps(log_entry) and "-1" not in json.dumps(evidence)


@pytest.mark.asyncio
async def test_compensation_failure_leaves_the_created_id_locatable_in_persisted_evidence(
    async_client, test_engine,
):
    upstream = Upstream()
    upstream.script[("PATCH", "/api/channels/channels/101/")] = Upstream.reject(400)
    upstream.script[("DELETE", "/api/channels/channels/101/")] = Upstream.reject(503)
    payload = _payload([
        {"method": "create_channel", "args": [{"name": "New"}]},
        {"method": "update_channel", "args": [-1, {"streams": [5]}]},
    ])

    response, _ = await _commit(async_client, test_engine, upstream, payload)

    assert response.status_code == 424, response.text
    detail = response.json()["detail"]
    assert 101 in upstream.channels                     # the orphan really is still there
    assert detail["completed_writes"] == ["create_channel#0->101"]
    assert detail["failed_write"] == "update_channel:101"
    assert len(detail["compensation_errors"]) == 1
    assert detail["compensation_errors"][0].startswith("create_channel:")
    assert detail["pre_mutation"] is False

    execution, log_entry, evidence = _persisted(test_engine, detail["execution_id"])
    assert execution.status == "failed"
    for persisted in (log_entry, evidence):
        assert persisted["completed_targets"] == ["create_channel#0->101"]
        assert persisted["compensation_errors"] == detail["compensation_errors"]


# ---------------------------------------------------------------------------
# Item 3: a credential-bearing create payload never reaches the 424 body,
# the persisted evidence, or the backend log, while correlation survives.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_credential_bearing_logo_payload_is_absent_from_http_persistence_and_logs(
    async_client, test_engine, caplog,
):
    upstream = Upstream()
    upstream.script[("POST", "/api/channels/logos/")] = Upstream.reject(429)
    logo_url = f"http://provider.example/logo.png?token={CANARY}"
    payload = _payload([
        {"method": "create_logo", "args": [{"name": "L", "url": logo_url}]},
        {"method": "create_channel", "args": [{"name": "New", "tvg_id": CANARY}]},
        {"method": "update_channel", "args": [-2, {"logo_id": -1}]},
    ])

    caplog.set_level(logging.DEBUG)
    with patch("dispatcharr_client._sleep", new=AsyncMock()):
        response, _ = await _commit(async_client, test_engine, upstream, payload)

    assert response.status_code == 424, response.text
    detail = response.json()["detail"]
    # Correlation survives...
    assert isinstance(detail["execution_id"], int)
    assert detail["failed_write"] == "create_logo#0"
    assert detail["not_applied"] == ["create_channel#1", "update_channel:pending(-2)"]
    assert detail["failed_outcome"] == "rejected"
    assert detail["pre_mutation"] is True

    execution, log_entry, evidence = _persisted(test_engine, detail["execution_id"])
    rendered = _everywhere(response, log_entry, evidence, execution)
    # ...and the payload does not: not the token, not the URL, not the tvg_id.
    assert CANARY not in rendered
    assert "provider.example" not in rendered
    assert "logo.png" not in rendered
    # The backend log for the whole commit (client, replay, router) is clean too.
    assert CANARY not in caplog.text
    assert "provider.example" not in caplog.text
    # The stored plan itself is the one place the payload legitimately lives
    # (it is the replay program), and it predates this PR; the assertion here
    # is on the DIAGNOSTIC fields the failure added, not on the plan blob.
    assert log_entry["failed_write"] == "create_logo#0"
    assert evidence["failed_write"] == "create_logo#0"
