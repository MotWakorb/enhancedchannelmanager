"""Explicit snapshot probes, NOT passing safety/regression tests.

Run explicitly with backend/pytest.ini and tests.conftest. The default assertions
characterize the reported disclosures on the named source revisions. Set
ECM_DISCLOSURE_EXPECT_SAFE=1 to demand no disclosure instead (expected RED until
fixed). No production transport or credentials are used; upstream calls and the
journal are mocked, while ASGI handlers, service logic, logging and JSON remain
real. This file is outside automatic backend test discovery intentionally.
"""

import asyncio
import io
import logging
import os
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

import httpx
import pytest

from tests.routers.test_profile_reconcile_hooks import _account, _mock_client


SENTINEL = "validation_only_9b81d07b4b1b4d71a63c"
HOST = "synthetic-dispatcharr.invalid"
EXPECT_SAFE = os.environ.get("ECM_DISCLOSURE_EXPECT_SAFE") == "1"


def injected_error(kind):
    if kind == "runtime":
        return RuntimeError(f"{HOST}/parser.py api_key={SENTINEL}\ntrace detail")
    request = httpx.Request("GET", f"http://{HOST}/api/channels/?api_key={SENTINEL}")
    response = httpx.Response(503, request=request)
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        return error
    raise AssertionError("503 positive control did not raise")


def assert_disclosure(surface, *, label, record_property):
    leaked = SENTINEL in surface
    record_property(label + "_sentinel_present", str(leaked).lower())
    if EXPECT_SAFE:
        assert not leaked, f"credential sentinel reached {label}"
    else:
        assert leaked, f"snapshot changed: sentinel did not reach {label}"


@pytest.fixture
def production_log_sinks(tmp_path):
    """Real formatters/handlers on actual service records, including safe-file control."""
    from log_utils import (
        InterProcessRotatingJsonHandler,
        RingBufferHandler,
        install_safe_logging,
        register_sensitive_values,
    )
    from observability import JsonFormatter

    root = logging.getLogger()
    original_level = root.level
    original_factory = logging.getLogRecordFactory()
    console = io.StringIO()
    stream = logging.StreamHandler(console)
    stream.setFormatter(JsonFormatter())
    ring = RingBufferHandler(500)
    ring.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
    logfile = tmp_path / "logs" / "ecm.log"
    persistent = InterProcessRotatingJsonHandler(logfile, max_bytes=1_000_000, backup_count=1)
    persistent.setFormatter(JsonFormatter())
    handlers = (stream, ring, persistent)
    register_sensitive_values(SENTINEL)
    install_safe_logging()
    root.setLevel(logging.INFO)
    for handler in handlers:
        root.addHandler(handler)
    try:
        yield console, ring, logfile
    finally:
        for handler in handlers:
            root.removeHandler(handler)
            handler.close()
        root.setLevel(original_level)
        logging.setLogRecordFactory(original_factory)


@pytest.fixture(autouse=True)
async def isolated_bulk_jobs():
    from routers import channels

    assert not channels._BULK_COMMIT_BACKGROUND_TASKS
    channels._BULK_COMMIT_JOBS.clear()
    yield
    pending = tuple(channels._BULK_COMMIT_BACKGROUND_TASKS)
    if pending:
        await asyncio.gather(*pending)
    channels._BULK_COMMIT_JOBS.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("error_kind", ["runtime", "http_status"])
@pytest.mark.parametrize("stage", ["revalidation", "universe", "enable", "disable", "sibling"])
async def test_5e3r3_real_save_service_and_log_sinks(
    async_client, production_log_sinks, record_property, stage, error_kind
):
    client = _mock_client()
    error = injected_error(error_kind)
    settings = client.get_all_m3u_group_settings.return_value
    client.get_channels.return_value = {"results": [{"id": 500, "custom_properties": {}}], "next": None}
    client.get_channel_profiles.return_value = [{"id": 12}, {"id": 13}]
    if stage == "revalidation":
        client.get_all_m3u_group_settings.side_effect = [settings, settings, error]
    elif stage == "universe":
        client.get_channel_profiles.side_effect = error
    elif stage in {"enable", "disable"}:
        async def update_profile(pid, data):
            if (pid == 12 and stage == "enable") or (pid == 13 and stage == "disable"):
                raise error
            return {"ok": True}
        client.bulk_update_profile_channels.side_effect = update_profile
    else:
        client.get_m3u_account.return_value = _account(11, 1304, [12])
        client.get_m3u_accounts.return_value = [_account(aid, 1304, [12]) for aid in (11, 22, 33)]

        async def update_sibling(aid, data):
            if aid == 33:
                raise error
            return {"message": "ok"}
        client.update_m3u_group_settings.side_effect = update_sibling

    with patch("routers.m3u.get_client", return_value=client), patch("routers.m3u.journal") as journal, patch(
        "services.profile_reconcile._resolve_live_rule_ids", AsyncMock(return_value=set())
    ):
        response = await async_client.patch("/api/m3u/accounts/11/group-settings", json={
            "group_settings": [{"channel_group": 1304, "custom_properties":
                                {} if stage == "sibling" else {"channel_profile_ids": [12]}}]
        })

    assert response.status_code == (503 if stage == "sibling" else 200), response.text
    assert SENTINEL not in response.text
    assert HOST not in response.text
    record_property("http_response_sentinel_present", "false")
    if stage == "sibling":
        assert [c.args[0] for c in client.update_m3u_group_settings.await_args_list] == [22, 33]
        assert journal.log_entry.call_args.kwargs["after_value"]["cleared_siblings"] == [22]
    else:
        outcome = response.json()["ecm_profile_apply"][0]
        assert outcome["status"] == ("degraded" if stage in {"revalidation", "universe"} else "partial_failure")
        assert outcome["profiles_enabled"] == (1 if stage in {"universe", "disable"} else 0)
        if stage in {"enable", "disable"}:
            assert outcome["failed_profile_ids"] == ([12] if stage == "enable" else [13])

    console, ring, logfile = production_log_sinks
    persisted = logfile.read_text()
    assert persisted, "file control must contain actual log records"
    assert SENTINEL not in persisted
    assert "***REDACTED***" in persisted
    record_property("persistent_file_sentinel_present", "false")
    assert_disclosure(console.getvalue(), label="json_console", record_property=record_property)
    assert_disclosure("\n".join(ring.get_lines()), label="ring_buffer", record_property=record_property)


async def submit_and_poll(async_client, body):
    response = await async_client.post("/api/channels/bulk-commit", json=body)
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    for _ in range(100):
        await asyncio.sleep(0)
        poll = await async_client.get(f"/api/channels/bulk-commit/{job_id}")
        assert poll.status_code == 200, poll.text
        envelope = poll.json()
        if envelope["status"] in {"completed", "failed"}:
            return envelope
    raise AssertionError("bulk job did not reach a terminal status")


@pytest.mark.asyncio
@pytest.mark.parametrize("error_kind", ["runtime", "http_status"])
@pytest.mark.parametrize("stage", ["operation", "mixed", "outer", "supervisor"])
async def test_ej4um_real_post_job_and_terminal_poll(async_client, record_property, stage, error_kind):
    from routers import channels

    client = AsyncMock()
    client.get_channels.return_value = {"results": [], "count": 0, "next": None}
    client.get_streams.return_value = {"results": [], "count": 0, "next": None}
    created = {"id": 501, "name": "Synthetic Channel"}
    error = injected_error(error_kind)
    client.create_channel.return_value = created
    if stage == "operation":
        client.create_channel.side_effect = error
    elif stage == "mixed":
        client.create_channel.side_effect = [created, error]

    operations = [{"type": "createChannel", "tempId": -1, "name": "Synthetic Channel", "normalize": False}]
    if stage == "mixed":
        operations.append({"type": "createChannel", "tempId": -2, "name": "Rejected Channel", "normalize": False})

    original_finalize = channels.finalize_bulk_commit_result
    finalizations = 0

    def fail_first_finalize(result, ledger):
        nonlocal finalizations
        finalizations += 1
        if stage == "outer" and finalizations == 1:
            raise error
        return original_finalize(result, ledger)

    with patch("routers.channels.get_client", side_effect=error if stage == "supervisor" else None,
               return_value=client), patch("routers.channels.journal"), patch(
        "routers.channels.finalize_bulk_commit_result", side_effect=fail_first_finalize
    ):
        envelope = await submit_and_poll(async_client, {"operations": operations, "continueOnError": True})

    if stage == "supervisor":
        assert envelope["status"] == "failed"
        client.create_channel.assert_not_awaited()
        exposed = envelope["error"]
        record_property("terminal_status", "failed")
    else:
        assert envelope["status"] == "completed"
        result = envelope["result"]
        assert result["success"] is False
        assert result["operationsApplied"] == (1 if stage in {"mixed", "outer"} else 0)
        assert result["operationsFailed"] == (0 if stage == "outer" else 1)
        assert result["normalizationFailures"] == []
        if stage == "outer":
            assert finalizations >= 2
            assert result["errors"][-1]["operationId"] == "bulk-commit"
        if stage == "mixed":
            assert result["partial"] is True
        exposed = "\n".join(item["error"] for item in result["errors"])
        record_property("operations_applied", result["operationsApplied"])
        record_property("operations_failed", result["operationsFailed"])
        record_property("terminal_status", "completed")
    assert HOST in exposed
    assert_disclosure(exposed, label="terminal_http_json", record_property=record_property)


@pytest.mark.asyncio
async def test_bulk_success_control_does_not_invent_disclosure(async_client, record_property):
    client = AsyncMock()
    client.create_channel.return_value = {"id": 501, "name": "Synthetic Channel"}
    with patch("routers.channels.get_client", return_value=client), patch("routers.channels.journal"):
        envelope = await submit_and_poll(async_client, {"operations": [
            {"type": "createChannel", "tempId": -1, "name": "Synthetic Channel", "normalize": False}
        ]})
    result = envelope["result"]
    assert result["success"] is True
    assert result["operationsApplied"] == 1 and result["operationsFailed"] == 0
    assert result["errors"] == []
    assert SENTINEL not in str(envelope)
    record_property("terminal_http_json_sentinel_present", "false")


@pytest.fixture
async def real_client_with_mock_http():
    """Real client/header/error construction, with HTTP transport fully local."""
    from config import DispatcharrSettings
    from dispatcharr_client import DispatcharrClient

    client = DispatcharrClient(DispatcharrSettings(
        url=f"http://{HOST}", auth_method="api_key", dispatcharr_api_key=SENTINEL
    ))
    await client._client.aclose()
    requests = []
    response_body = {"detail": "ordinary upstream rejection"}

    def reject(request):
        requests.append(request)
        assert request.headers["X-API-Key"] == SENTINEL
        assert SENTINEL not in str(request.url), "ordinary API keys belong in headers"
        return httpx.Response(400, json=response_body)

    client._client = httpx.AsyncClient(transport=httpx.MockTransport(reject))
    try:
        yield client, requests, response_body
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_ordinary_client_status_errors_do_not_invent_header_key_leaks(
    real_client_with_mock_http, record_property
):
    client, requests, _ = real_client_with_mock_http
    with pytest.raises(httpx.HTTPStatusError) as profile_error:
        await client.get_channel_profiles()
    with pytest.raises(Exception) as create_error:
        await client.create_channel({"name": "Synthetic Channel"})
    assert len(requests) == 2
    assert SENTINEL not in str(profile_error.value)
    assert SENTINEL not in str(create_error.value)
    assert "ordinary upstream rejection" in str(create_error.value)
    record_property("ordinary_status_error_contains_header_key", "false")
    record_property("create_error_includes_upstream_body", "true")


@pytest.mark.asyncio
async def test_real_client_secret_bearing_upstream_body_reaches_terminal_json(
    async_client, real_client_with_mock_http, record_property
):
    client, requests, response_body = real_client_with_mock_http
    # Conditional fault: the upstream body already contains a synthetic secret.
    # This does NOT claim normal Dispatcharr errors echo their authentication key.
    response_body["detail"] = f"upstream diagnostic api_key={SENTINEL}"
    with patch("routers.channels.get_client", return_value=client), patch("routers.channels.journal"):
        envelope = await submit_and_poll(async_client, {"operations": [
            {"type": "createChannel", "tempId": -1, "name": "Synthetic Channel", "normalize": False}
        ]})
    result = envelope["result"]
    assert len(requests) == 1
    assert result["operationsApplied"] == 0 and result["operationsFailed"] == 1
    assert "Channel creation failed: 400" in result["errors"][0]["error"]
    assert_disclosure(str(envelope), label="upstream_body_to_terminal_json", record_property=record_property)


@pytest.fixture
async def real_client_with_malformed_synthetic_header():
    """Real HTTPX/httpcore/h11 against a private ephemeral loopback listener.

    Only a deliberately malformed synthetic key is used. This tests a concrete
    conditional producer of secret-bearing exceptions, not the normal valid-key
    path or the settings-save UI. No provider or deployed ECM is contacted.
    """
    from config import DispatcharrSettings
    from dispatcharr_client import DispatcharrClient

    connections = set()

    async def discard(reader, writer):
        try:
            await reader.read(4096)
        finally:
            writer.close()
            await writer.wait_closed()

    def accepted(reader, writer):
        task = asyncio.create_task(discard(reader, writer))
        connections.add(task)
        task.add_done_callback(connections.discard)

    server = await asyncio.start_server(accepted, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    client = DispatcharrClient(DispatcharrSettings(
        url=f"http://127.0.0.1:{port}", auth_method="api_key",
        dispatcharr_api_key=SENTINEL + "\n",
    ))
    try:
        yield client
    finally:
        await client._client.aclose()
        server.close()
        await server.wait_closed()
        if connections:
            await asyncio.wait_for(asyncio.gather(*tuple(connections)), timeout=5)


@pytest.mark.asyncio
async def test_malformed_key_real_transport_to_service_logs(
    async_client, production_log_sinks, real_client_with_malformed_synthetic_header, record_property
):
    client = _mock_client()
    client.get_channels.return_value = {"results": [{"id": 500, "custom_properties": {}}], "next": None}
    client.get_channel_profiles.side_effect = real_client_with_malformed_synthetic_header.get_channel_profiles
    with patch("routers.m3u.get_client", return_value=client), patch("routers.m3u.journal"), patch(
        "services.profile_reconcile._resolve_live_rule_ids", AsyncMock(return_value=set())
    ):
        response = await async_client.patch("/api/m3u/accounts/11/group-settings", json={
            "group_settings": [{"channel_group": 1304, "custom_properties": {"channel_profile_ids": [12]}}]
        })
    assert response.status_code == 200
    assert response.json()["ecm_profile_apply"][0]["status"] == "degraded"
    assert SENTINEL not in response.text
    console, ring, logfile = production_log_sinks
    text = console.getvalue()
    assert "Illegal header value" in text and "LocalProtocolError" in text
    assert any("[PROFILE-RECONCILE]" in line and SENTINEL in line for line in ring.get_lines())
    assert SENTINEL not in logfile.read_text()
    record_property("producer", "real_httpx_httpcore_h11_malformed_header")
    assert_disclosure(text, label="real_transport_to_console", record_property=record_property)


@pytest.mark.asyncio
async def test_malformed_key_real_transport_to_terminal_json(
    async_client, real_client_with_malformed_synthetic_header, record_property
):
    with patch("routers.channels.get_client", return_value=real_client_with_malformed_synthetic_header), patch(
        "routers.channels.journal"
    ):
        envelope = await submit_and_poll(async_client, {"operations": [
            {"type": "createChannel", "tempId": -1, "name": "Synthetic Channel", "normalize": False}
        ]})
    result = envelope["result"]
    assert result["operationsApplied"] == 0 and result["operationsFailed"] == 1
    assert "Illegal header value" in result["errors"][0]["error"]
    record_property("producer", "real_httpx_httpcore_h11_malformed_header")
    assert_disclosure(str(envelope), label="real_transport_to_terminal_json", record_property=record_property)


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", [
    ("PATCH", "/api/m3u/accounts/11/group-settings"),
    ("POST", "/api/channels/bulk-commit"),
    ("GET", "/api/channels/bulk-commit/known-synthetic-job-id"),
])
async def test_auth_enabled_anonymous_request_is_refused(async_client, record_property, method, path):
    client = AsyncMock()
    with patch("main.get_auth_settings", return_value=SimpleNamespace(require_auth=True, setup_complete=True)), patch(
        "main.load_mcp_service_credentials", return_value=None
    ), patch("routers.m3u.get_client", return_value=client), patch(
        "routers.channels.get_client", return_value=client
    ):
        response = await async_client.request(method, path, json={})
    assert response.status_code == 401
    assert client.mock_calls == []
    assert SENTINEL not in response.text
    record_property("auth_enabled_anonymous_http_status", "401")
