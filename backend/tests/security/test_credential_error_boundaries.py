"""No-secret regressions for profile and bulk errors, crossing real HTTP/log boundaries."""

import asyncio
import io
import logging
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from tests.routers.test_profile_reconcile_hooks import _account, _mock_client

SECRET = "synthetic-disclosure-regression-54b632a1"
CAUSE = "synthetic-cause-regression-24d57721"
HOST = "synthetic-upstream.invalid"


def upstream_error(kind):
    if kind == "value":
        error = ValueError(f"{HOST} api_key={SECRET}")
    elif kind == "status":
        request = httpx.Request("POST", f"http://{HOST}/?api_key={SECRET}")
        response = httpx.Response(503, request=request)
        error = httpx.HTTPStatusError(
            "upstream rejected api_key=" + SECRET, request=request, response=response
        )
    else:
        error = RuntimeError(f"{HOST} api_key={SECRET}")
    error.__cause__ = RuntimeError("unlabelled registered credential " + CAUSE)
    return error


@pytest.fixture
def log_outputs(tmp_path):
    from log_utils import (
        InterProcessRotatingJsonHandler,
        RingBufferHandler,
        install_safe_logging,
        register_sensitive_values,
    )
    from observability import JsonFormatter

    register_sensitive_values(SECRET, CAUSE)
    root = logging.getLogger()
    old_level, old_factory = root.level, logging.getLogRecordFactory()
    console = io.StringIO()
    stream = logging.StreamHandler(console)
    ring = RingBufferHandler(500)
    path = tmp_path / "logs" / "ecm.log"
    persistent = InterProcessRotatingJsonHandler(
        path, max_bytes=1_000_000, backup_count=1
    )
    handlers = (stream, ring, persistent)
    for handler in handlers:
        handler.setFormatter(JsonFormatter())
        root.addHandler(handler)
    install_safe_logging()
    root.setLevel(logging.INFO)
    try:
        yield lambda: (
            console.getvalue(),
            "\n".join(ring.get_lines()),
            path.read_text(),
        )
    finally:
        for handler in handlers:
            root.removeHandler(handler)
            handler.close()
        root.setLevel(old_level)
        logging.setLogRecordFactory(old_factory)


def assert_safe_logs(outputs, marker):
    for text in outputs():
        assert marker in text, "the real failing path must emit a diagnostic"
        assert SECRET not in text
        assert CAUSE not in text
        assert "***REDACTED***" in text


@pytest.fixture(autouse=True)
async def own_bulk_jobs():
    from routers import channels

    assert not channels._BULK_COMMIT_BACKGROUND_TASKS
    channels._BULK_COMMIT_JOBS.clear()
    yield
    if channels._BULK_COMMIT_BACKGROUND_TASKS:
        await asyncio.gather(*tuple(channels._BULK_COMMIT_BACKGROUND_TASKS))
    channels._BULK_COMMIT_JOBS.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["runtime", "status", "value"])
@pytest.mark.parametrize(
    "stage", ["revalidation", "universe", "enable", "disable", "sibling"]
)
async def test_profile_save_redacts_logs_preserving_outcomes(
    async_client, log_outputs, kind, stage
):
    client = _mock_client()
    error = upstream_error(kind)
    settings = client.get_all_m3u_group_settings.return_value
    client.get_channels.return_value = {
        "results": [{"id": 500, "custom_properties": {}}],
        "next": None,
    }
    client.get_channel_profiles.return_value = [{"id": 12}, {"id": 13}]
    if stage == "revalidation":
        client.get_all_m3u_group_settings.side_effect = [settings, settings, error]
    elif stage == "universe":
        client.get_channel_profiles.side_effect = error
    elif stage in {"enable", "disable"}:

        async def update(pid, data):
            if (pid == 12 and stage == "enable") or (pid == 13 and stage == "disable"):
                raise error
            return {"ok": True}

        client.bulk_update_profile_channels.side_effect = update
    else:
        client.get_m3u_account.return_value = _account(11, 1304, [12])
        client.get_m3u_accounts.return_value = [
            _account(aid, 1304, [12]) for aid in (11, 22, 33)
        ]

        async def update_sibling(aid, data):
            if aid == 33:
                raise error
            return {"message": "ok"}

        client.update_m3u_group_settings.side_effect = update_sibling
    with (
        patch("routers.m3u.get_client", return_value=client),
        patch("routers.m3u.journal") as journal,
        patch(
            "services.profile_reconcile._resolve_live_rule_ids",
            AsyncMock(return_value=set()),
        ),
    ):
        response = await async_client.patch(
            "/api/m3u/accounts/11/group-settings",
            json={
                "group_settings": [
                    {
                        "channel_group": 1304,
                        "custom_properties": {}
                        if stage == "sibling"
                        else {"channel_profile_ids": [12]},
                    }
                ]
            },
        )
    assert response.status_code == (503 if stage == "sibling" else 200)
    assert SECRET not in response.text and CAUSE not in response.text
    if stage == "sibling":
        assert [
            c.args[0] for c in client.update_m3u_group_settings.await_args_list
        ] == [22, 33]
        assert journal.log_entry.call_args.kwargs["after_value"][
            "cleared_siblings"
        ] == [22]
    else:
        outcome = response.json()["ecm_profile_apply"][0]
        assert outcome["status"] == (
            "degraded" if stage in {"revalidation", "universe"} else "partial_failure"
        )
        assert outcome["profiles_enabled"] == (
            1 if stage in {"universe", "disable"} else 0
        )
        if stage in {"enable", "disable"}:
            assert outcome["failed_profile_ids"] == (
                [12] if stage == "enable" else [13]
            )
    assert_safe_logs(
        log_outputs, "enforced-global" if stage == "sibling" else "PROFILE-RECONCILE"
    )


async def commit_and_poll(client, body):
    response = await client.post("/api/channels/bulk-commit", json=body)
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    for _ in range(100):
        await asyncio.sleep(0)
        response = await client.get(f"/api/channels/bulk-commit/{job_id}")
        assert response.status_code == 200
        if response.json()["status"] != "running":
            return response.json()
    raise AssertionError("bulk job did not finish")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["runtime", "status", "value"])
@pytest.mark.parametrize(
    "stage", ["operation", "mixed", "outer", "supervisor", "group", "setup_group"]
)
async def test_bulk_errors_redact_http_and_logs_preserving_ledger(
    async_client, log_outputs, kind, stage
):
    from routers import channels

    client = AsyncMock()
    client.get_channels.return_value = {"results": [], "count": 0, "next": None}
    client.get_streams.return_value = {"results": [], "count": 0, "next": None}
    client.get_channel_groups.return_value = []
    created = {"id": 501, "name": "Synthetic Channel"}
    error = upstream_error(kind)
    client.create_channel.return_value = created
    if stage == "operation":
        client.create_channel.side_effect = error
    elif stage == "mixed":
        client.create_channel.side_effect = [created, error]
    elif stage in {"group", "setup_group"}:
        client.create_channel_group.side_effect = error
    operations = [
        {
            "type": "createChannel",
            "tempId": -1,
            "name": "Synthetic Channel",
            "normalize": False,
        }
    ]
    if stage == "mixed":
        operations.append(
            {
                "type": "createChannel",
                "tempId": -2,
                "name": "Rejected Channel",
                "normalize": False,
            }
        )
    if stage == "group":
        operations = [{"type": "createGroup", "tempId": -1, "name": "Synthetic Group"}]
    original_finalize = channels.finalize_bulk_commit_result
    calls = 0

    def finalize(result, ledger):
        nonlocal calls
        calls += 1
        if stage == "outer" and calls == 1:
            raise error
        return original_finalize(result, ledger)

    with (
        patch(
            "routers.channels.get_client",
            return_value=client,
            side_effect=error if stage == "supervisor" else None,
        ),
        patch("routers.channels.journal"),
        patch("routers.channels.finalize_bulk_commit_result", side_effect=finalize),
    ):
        body = {"operations": operations, "continueOnError": True}
        if stage == "setup_group":
            body["groupsToCreate"] = [{"name": "Synthetic Setup Group"}]
        envelope = await commit_and_poll(async_client, body)
    assert SECRET not in str(envelope) and CAUSE not in str(envelope)
    assert HOST not in str(envelope)
    if stage == "supervisor":
        assert envelope["status"] == "failed"
        assert "Bulk commit failed" in envelope["error"]
        client.create_channel.assert_not_awaited()
    else:
        assert envelope["status"] == "completed"
        result = envelope["result"]
        assert result["success"] is False
        assert result["operationsApplied"] == (1 if stage in {"mixed", "outer"} else 0)
        if stage == "setup_group":
            assert result["operationsFailed"] == 0
            client.create_channel.assert_not_awaited()
        elif stage != "group":
            assert result["operationsFailed"] == (0 if stage == "outer" else 1)
        assert result["errors"]
        assert result["normalizationFailures"] == []
        if stage == "outer":
            assert calls >= 2
            assert result["errors"][-1]["operationId"] == "bulk-commit"
        if stage == "mixed":
            assert result["partial"] is True
    assert_safe_logs(log_outputs, "CHANNELS-BULK")


@pytest.fixture
async def malformed_header_client():
    """Real HTTPX/httpcore/h11; only a disposable loopback TCP connection."""
    from config import DispatcharrSettings
    from dispatcharr_client import DispatcharrClient

    pending = set()

    async def discard(reader, writer):
        try:
            await reader.read(4096)
        finally:
            writer.close()
            await writer.wait_closed()

    def accept(reader, writer):
        task = asyncio.create_task(discard(reader, writer))
        pending.add(task)
        task.add_done_callback(pending.discard)

    server = await asyncio.start_server(accept, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    client = DispatcharrClient(
        DispatcharrSettings(
            url=f"http://127.0.0.1:{port}",
            auth_method="api_key",
            dispatcharr_api_key=SECRET + "\n",
        )
    )
    try:
        yield client
    finally:
        await client._client.aclose()
        server.close()
        await server.wait_closed()
        if pending:
            await asyncio.wait_for(asyncio.gather(*tuple(pending)), 5)


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["profile", "bulk"])
async def test_real_protocol_error_is_safe_across_http_and_log_sinks(
    async_client, log_outputs, malformed_header_client, surface
):
    if surface == "profile":
        client = _mock_client()
        client.get_channels.return_value = {
            "results": [{"id": 500, "custom_properties": {}}],
            "next": None,
        }
        client.get_channel_profiles.side_effect = (
            malformed_header_client.get_channel_profiles
        )
        with (
            patch("routers.m3u.get_client", return_value=client),
            patch("routers.m3u.journal"),
            patch(
                "services.profile_reconcile._resolve_live_rule_ids",
                AsyncMock(return_value=set()),
            ),
        ):
            response = await async_client.patch(
                "/api/m3u/accounts/11/group-settings",
                json={
                    "group_settings": [
                        {
                            "channel_group": 1304,
                            "custom_properties": {"channel_profile_ids": [12]},
                        }
                    ]
                },
            )
        assert response.status_code == 200
        assert response.json()["ecm_profile_apply"][0]["status"] == "degraded"
        payload = response.text
    else:
        with (
            patch("routers.channels.get_client", return_value=malformed_header_client),
            patch("routers.channels.journal"),
        ):
            envelope = await commit_and_poll(
                async_client,
                {
                    "operations": [
                        {
                            "type": "createChannel",
                            "tempId": -1,
                            "name": "Synthetic Channel",
                            "normalize": False,
                        }
                    ]
                },
            )
        assert envelope["result"]["operationsApplied"] == 0
        assert envelope["result"]["operationsFailed"] == 1
        payload = str(envelope)
    assert SECRET not in payload
    assert "Illegal header value" not in payload
    assert_safe_logs(log_outputs, "DISPATCHARR")
    assert "LocalProtocolError" in log_outputs()[0]
    assert "Illegal header value" in log_outputs()[0]


@pytest.mark.asyncio
async def test_real_upstream_body_is_not_returned_to_bulk_caller(
    async_client, log_outputs
):
    from config import DispatcharrSettings
    from dispatcharr_client import DispatcharrClient

    client = DispatcharrClient(
        DispatcharrSettings(
            url=f"http://{HOST}", auth_method="api_key", dispatcharr_api_key=SECRET
        )
    )
    await client._client.aclose()
    seen = []

    def reject(request):
        seen.append(request)
        assert request.headers["X-API-Key"] == SECRET
        return httpx.Response(400, json={"detail": f"api_key={SECRET}; {HOST}"})

    client._client = httpx.AsyncClient(transport=httpx.MockTransport(reject))
    try:
        with (
            patch("routers.channels.get_client", return_value=client),
            patch("routers.channels.journal"),
        ):
            envelope = await commit_and_poll(
                async_client,
                {
                    "operations": [
                        {
                            "type": "createChannel",
                            "tempId": -1,
                            "name": "Synthetic Channel",
                            "normalize": False,
                        }
                    ]
                },
            )
    finally:
        await client._client.aclose()
    assert len(seen) == 1
    assert envelope["result"]["operationsApplied"] == 0
    assert envelope["result"]["operationsFailed"] == 1
    assert SECRET not in str(envelope) and HOST not in str(envelope)
    assert_safe_logs(log_outputs, "CHANNELS-BULK")


def test_formatted_exception_redacts_registered_values_and_chain():
    from log_utils import redact_exception, register_sensitive_values

    register_sensitive_values(SECRET, CAUSE)
    text = redact_exception(upstream_error("runtime"))
    assert SECRET not in text and CAUSE not in text
    assert "RuntimeError" in text and HOST in text
    assert "direct cause" in text
    assert "***REDACTED***" in text


def test_formatted_exception_fails_closed_if_scrubbing_fails():
    from log_utils import redact_exception

    with patch("log_utils.redact_log_diagnostic", side_effect=RuntimeError(SECRET)):
        assert (
            redact_exception(RuntimeError(SECRET))
            == "Exception diagnostic unavailable."
        )


def test_plain_diagnostic_fails_closed_if_registry_is_unavailable():
    from log_utils import redact_log_diagnostic

    with patch(
        "log_utils.get_registered_sensitive_value_forms",
        side_effect=RuntimeError(SECRET),
    ):
        assert redact_log_diagnostic(SECRET) == "***REDACTED***"


@pytest.mark.asyncio
async def test_unregistered_url_token_is_scrubbed_in_real_profile_warning(
    async_client, log_outputs
):
    token = "unregistered-url-token-68c312ae"
    request = httpx.Request("GET", f"http://{HOST}/profiles?token={token}")
    response = httpx.Response(503, request=request)
    with pytest.raises(httpx.HTTPStatusError) as caught:
        response.raise_for_status()
    client = _mock_client()
    client.get_channels.return_value = {
        "results": [{"id": 500, "custom_properties": {}}],
        "next": None,
    }
    client.get_channel_profiles.side_effect = caught.value
    with (
        patch("routers.m3u.get_client", return_value=client),
        patch("routers.m3u.journal"),
        patch(
            "services.profile_reconcile._resolve_live_rule_ids",
            AsyncMock(return_value=set()),
        ),
    ):
        saved = await async_client.patch(
            "/api/m3u/accounts/11/group-settings",
            json={
                "group_settings": [
                    {
                        "channel_group": 1304,
                        "custom_properties": {"channel_profile_ids": [12]},
                    }
                ]
            },
        )
    assert saved.status_code == 200
    assert saved.json()["ecm_profile_apply"][0]["status"] == "degraded"
    assert token not in saved.text
    console, ring, _ = log_outputs()
    # Persistent-file policy is unchanged; this guard owns the scoped warning sinks.
    for text in (console, ring):
        assert "PROFILE-RECONCILE" in text
        assert token not in text
        assert "REDACTED" in text


# ---------------------------------------------------------------------------
# Operator guidance the sanitizer must keep. Numbering-baseline refusals are
# local, deliberately worded instructions; replacing them with the generic
# foreign-error text would hide what the operator has to do next. The terminal
# envelope's ``error`` is what the MCP bulk_commit_channels tool prints verbatim.
# ---------------------------------------------------------------------------

STALE_SINGLE_GUIDANCE = (
    "Channel 1 was not renumbered: it is on channel number 5, and this request "
    "expected 4. Somebody else changed it. Re-read the channel and decide before "
    "sending this again."
)
MISSING_SINGLE_GUIDANCE = (
    "Channel 1 was not renumbered: the operation carries no expectedNumber, so "
    "ECM cannot check whether anybody else changed its number first."
)
MISSING_RANGE_GUIDANCE = (
    "Channels 1, 2 were not renumbered: the range carries no expectedNumbers "
    "entry for them, so ECM cannot check whether anybody else changed their "
    "numbers first."
)
UNVERIFIABLE_SINGLE_GUIDANCE = (
    "Channel 1 was not renumbered: the channel lineup could not be read, so ECM "
    "could not check whether anybody else changed its number first. Nothing "
    "about this channel was changed."
)
GENERIC_OPERATION_ERROR = (
    "Operation failed; check server logs and the reported results before retrying."
)


def numbering_client(channels_readable=True):
    from tests.routers.test_renumber_baselines import make_client

    return make_client(channels_readable=channels_readable)


async def poll_numbering(async_client, client, operations):
    with (
        patch("routers.channels.get_client", return_value=client),
        patch("routers.channels.journal"),
    ):
        envelope = await commit_and_poll(
            async_client, {"operations": operations, "continueOnError": True}
        )
    assert envelope["status"] == "completed", envelope
    return envelope["result"]


def renumber_op(channel_id, number, expected):
    return {
        "type": "updateChannel",
        "channelId": channel_id,
        "data": {"channel_number": number},
        "expectedNumber": {"number": expected},
    }


@pytest.mark.asyncio
async def test_stale_baseline_guidance_and_payload_survive_terminal_poll(async_client):
    client = numbering_client()
    result = await poll_numbering(async_client, client, [renumber_op(1, 40, 4)])
    client.update_channel.assert_not_awaited()
    assert result["operationsFailed"] == 1
    error = result["errors"][0]
    assert error["error"] == STALE_SINGLE_GUIDANCE
    assert error["concurrentNumberChanges"] == [
        {"channelId": 1, "baselineNumber": 4, "serverNumber": 5},
    ]


@pytest.mark.asyncio
async def test_stale_range_guidance_and_payload_survive_terminal_poll(async_client):
    client = numbering_client()
    result = await poll_numbering(
        async_client,
        client,
        [{
            "type": "bulkAssignChannelNumbers",
            "channelIds": [1, 2],
            "startingNumber": 100,
            "expectedNumbers": {"1": 5, "2": 99},
        }],
    )
    client.assign_channel_numbers.assert_not_awaited()
    error = result["errors"][0]
    assert error["error"] != GENERIC_OPERATION_ERROR
    assert "Somebody else changed" in error["error"]
    assert error["concurrentNumberChanges"] == [
        {"channelId": 2, "baselineNumber": 99, "serverNumber": 6},
    ]


@pytest.mark.asyncio
async def test_unverifiable_baseline_guidance_survives_terminal_poll(async_client):
    client = numbering_client(channels_readable=False)
    result = await poll_numbering(async_client, client, [renumber_op(1, 40, 5)])
    client.update_channel.assert_not_awaited()
    assert result["errors"][0]["error"] == UNVERIFIABLE_SINGLE_GUIDANCE
    # The lineup read failed with foreign text; it must not reach the envelope.
    assert "Dispatcharr unreachable" not in str(result)


async def run_without_baseline(operation, clear):
    """Drive the executor's own backstop, which the request model normally guards."""
    from routers.channels import BulkCommitRequest, _run_bulk_commit

    request = BulkCommitRequest(operations=[operation], continueOnError=True)
    clear(request.operations[0])
    client = numbering_client()
    with (
        patch("routers.channels.get_client", return_value=client),
        patch("routers.channels.journal"),
    ):
        result = await _run_bulk_commit(request, batch_id="batch-no-baseline")
    client.update_channel.assert_not_awaited()
    client.assign_channel_numbers.assert_not_awaited()
    return result


@pytest.mark.asyncio
async def test_missing_single_baseline_backstop_keeps_its_guidance():
    def clear(op):
        op.expectedNumber = None

    result = await run_without_baseline(renumber_op(1, 40, 5), clear)
    assert result["operationsFailed"] == 1
    assert result["errors"][0]["error"] == MISSING_SINGLE_GUIDANCE


@pytest.mark.asyncio
async def test_missing_range_baseline_backstop_keeps_its_guidance():
    def clear(op):
        op.expectedNumbers = {}

    result = await run_without_baseline(
        {
            "type": "bulkAssignChannelNumbers",
            "channelIds": [1, 2],
            "startingNumber": 100,
            "expectedNumbers": {"1": 5, "2": 6},
        },
        clear,
    )
    assert result["operationsFailed"] == 1
    assert result["errors"][0]["error"] == MISSING_RANGE_GUIDANCE


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["runtime", "status", "value"])
async def test_numbering_recovery_entries_do_not_carry_upstream_text(
    async_client, log_outputs, kind
):
    client = numbering_client()
    calls = 0
    error = upstream_error(kind)

    async def update_channel(channel_id, data):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"id": channel_id, **data}
        raise error

    client.update_channel.side_effect = update_channel
    result = await poll_numbering(
        async_client, client, [renumber_op(1, 21, 5), renumber_op(2, 22, 6)]
    )
    recovery = result["numberingRecovery"]
    assert [entry["channelId"] for entry in recovery] == [1]
    assert "ESPN" in recovery[0]["step"] and "5" in recovery[0]["step"]
    assert recovery[0]["error"]
    assert SECRET not in str(result) and CAUSE not in str(result)
    assert HOST not in str(result)
    assert_safe_logs(log_outputs, "CHANNELS-BULK")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["runtime", "status", "value"])
async def test_range_read_back_failure_is_scrubbed_in_every_log_sink(
    async_client, log_outputs, kind
):
    client = numbering_client()
    client.get_channel.side_effect = upstream_error(kind)
    result = await poll_numbering(
        async_client,
        client,
        [{
            "type": "bulkAssignChannelNumbers",
            "channelIds": [1, 2],
            "expectedNumbers": {"1": 5, "2": 6},
        }],
    )
    client.assign_channel_numbers.assert_awaited_once_with([1, 2], None)
    assert result["success"] is True
    assert result["operationsApplied"] == 1
    assert SECRET not in str(result) and CAUSE not in str(result)
    assert_safe_logs(log_outputs, "could not read its new number back")


# ---------------------------------------------------------------------------
# Scrub policy for operator-safe bulk errors. Guidance built only from ECM text
# and integers passes through untouched, so short registered credential values
# cannot mangle channel numbers in it. Messages that carry names or upstream
# data stay scrubbed.
# ---------------------------------------------------------------------------

SHORT_VALUES = ("100", "5", "7")


def short_value_client(channels_readable=True):
    from tests.routers.test_renumber_baselines import make_client

    return make_client(
        [
            {"id": 7, "name": "ESPN", "channel_number": 100, "streams": []},
            {"id": 100, "name": "TNT", "channel_number": 7, "streams": []},
        ],
        channels_readable=channels_readable,
    )


@pytest.mark.asyncio
async def test_stale_guidance_survives_short_registered_values(async_client):
    from log_utils import register_sensitive_values

    register_sensitive_values(*SHORT_VALUES)
    client = short_value_client()
    result = await poll_numbering(async_client, client, [renumber_op(7, 40, 5)])
    error = result["errors"][0]
    assert error["error"] == (
        "Channel 7 was not renumbered: it is on channel number 100, and this "
        "request expected 5. Somebody else changed it. Re-read the channel and "
        "decide before sending this again."
    )
    assert error["concurrentNumberChanges"] == [
        {"channelId": 7, "baselineNumber": 5, "serverNumber": 100},
    ]


@pytest.mark.asyncio
async def test_unverifiable_guidance_survives_short_registered_values(async_client):
    from log_utils import register_sensitive_values

    register_sensitive_values(*SHORT_VALUES)
    client = short_value_client(channels_readable=False)
    result = await poll_numbering(async_client, client, [renumber_op(100, 40, 5)])
    assert result["errors"][0]["error"].startswith(
        "Channel 100 was not renumbered: the channel lineup could not be read"
    )
    assert "REDACTED" not in result["errors"][0]["error"]


@pytest.mark.asyncio
async def test_missing_baseline_guidance_survives_short_registered_values():
    from log_utils import register_sensitive_values
    from routers.channels import BulkCommitRequest, _run_bulk_commit

    register_sensitive_values(*SHORT_VALUES)
    request = BulkCommitRequest(
        operations=[{
            "type": "bulkAssignChannelNumbers",
            "channelIds": [7, 100],
            "startingNumber": 5,
            "expectedNumbers": {"7": 100, "100": 7},
        }],
        continueOnError=True,
    )
    request.operations[0].expectedNumbers = {}
    with (
        patch("routers.channels.get_client", return_value=short_value_client()),
        patch("routers.channels.journal"),
    ):
        result = await _run_bulk_commit(request, batch_id="batch-short-values")
    assert result["errors"][0]["error"] == (
        "Channels 7, 100 were not renumbered: the range carries no expectedNumbers "
        "entry for them, so ECM cannot check whether anybody else changed their "
        "numbers first."
    )


@pytest.mark.asyncio
async def test_missing_stream_guidance_survives_short_registered_values(async_client):
    from log_utils import register_sensitive_values

    register_sensitive_values(*SHORT_VALUES)
    client = short_value_client()
    result = await poll_numbering(
        async_client,
        client,
        [{"type": "addStreamToChannel", "channelId": 7, "streamId": 100}],
    )
    assert result["errors"][0]["error"] == "Stream 100 does not exist"


@pytest.mark.asyncio
async def test_upstream_bearing_create_error_keeps_scrubbing(async_client):
    """A create Dispatcharr answered without a usable id names the channel.

    Names are operator/upstream data, so that message stays scrubbed: a
    registered value of at least four characters is removed. A shorter one is
    left for the structural URL rules, as in debug bundles, because an
    unanchored sweep of one to three characters would destroy channel numbers
    and ids. The malformed id itself is reported only by type.
    """
    from log_utils import register_sensitive_values

    register_sensitive_values(SECRET, "tv")
    client = AsyncMock()
    client.get_channels.return_value = {"results": [], "count": 0, "next": None}
    client.get_streams.return_value = {"results": [], "count": 0, "next": None}
    client.get_channel_groups.return_value = []
    unregistered = "returned-id-unregistered-0a9f7c"
    client.create_channel.return_value = {"id": unregistered}
    with (
        patch("routers.channels.get_client", return_value=client),
        patch("routers.channels.journal"),
    ):
        envelope = await commit_and_poll(
            async_client,
            {
                "operations": [{
                    "type": "createChannel",
                    "tempId": -1,
                    "name": f"tv {SECRET}",
                    "normalize": False,
                }],
                "continueOnError": True,
            },
        )
    error = envelope["result"]["errors"][0]
    assert error["applied"] is True
    assert SECRET not in error["error"]
    assert unregistered not in str(envelope)
    assert "'tv ***REDACTED***'" in error["error"]
    assert "(str)" in error["error"]


@pytest.mark.asyncio
async def test_numbering_recovery_reason_is_the_fixed_text(async_client):
    client = numbering_client()
    calls = 0

    async def update_channel(channel_id, data):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"id": channel_id, **data}
        raise upstream_error("runtime")

    client.update_channel.side_effect = update_channel
    result = await poll_numbering(
        async_client, client, [renumber_op(1, 21, 5), renumber_op(2, 22, 6)]
    )
    assert result["numberingRecovery"][0]["error"] == (
        "Dispatcharr did not accept the repair write; check server logs."
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["operation", "mixed", "outer", "group"])
async def test_bulk_journal_rows_carry_no_upstream_text(async_client, stage):
    client = AsyncMock()
    client.get_channels.return_value = {"results": [], "count": 0, "next": None}
    client.get_streams.return_value = {"results": [], "count": 0, "next": None}
    client.get_channel_groups.return_value = []
    created = {"id": 501, "name": "Synthetic Channel"}
    error = upstream_error("status")
    if stage == "operation":
        client.create_channel.side_effect = error
    elif stage == "mixed":
        client.create_channel.side_effect = [created, error]
    else:
        client.create_channel.return_value = created
    if stage == "group":
        client.create_channel_group.side_effect = error
    operations = [
        {"type": "createChannel", "tempId": -1, "name": "Synthetic Channel",
         "normalize": False},
        {"type": "createChannel", "tempId": -2, "name": "Rejected Channel",
         "normalize": False},
    ]
    body = {"operations": operations, "continueOnError": True}
    if stage == "group":
        body["groupsToCreate"] = [{"name": "Synthetic Setup Group"}]
    from routers import channels

    original_finalize = channels.finalize_bulk_commit_result
    calls = 0

    def finalize(result, ledger):
        nonlocal calls
        calls += 1
        if stage == "outer" and calls == 1:
            raise error
        return original_finalize(result, ledger)

    with (
        patch("routers.channels.get_client", return_value=client),
        patch("routers.channels.journal") as journal,
        patch("routers.channels.finalize_bulk_commit_result", side_effect=finalize),
    ):
        envelope = await commit_and_poll(async_client, body)
    assert envelope["status"] == "completed"
    assert journal.mock_calls, "the run must reach the journal"
    recorded = str(journal.mock_calls)
    assert SECRET not in recorded and CAUSE not in recorded
    assert HOST not in recorded


class SyntheticDBAPIError(Exception):
    pass


def sqlalchemy_style_error():
    from sqlalchemy.exc import OperationalError

    return OperationalError(
        "INSERT INTO journal_entries (description) VALUES (?)",
        (f"http://{HOST}/get.php?username=u&password={SECRET}",),
        SyntheticDBAPIError("database is locked"),
    )


@pytest.mark.parametrize("failure", ["batch_and_row", "base_exception"])
def test_journal_write_failures_log_scrubbed_errors_and_rows(log_outputs, failure):
    from routers.channels import write_journal_rows

    url_secret = "unregistered-row-url-secret-3be1"
    row = {
        "category": "channel",
        "action_type": "create",
        "entity_id": 501,
        "entity_name": f"Synthetic {SECRET}",
        "description": "Created channel",
        "after_value": {"logo_url": f"http://{HOST}/logo.png?token={url_secret}"},
    }
    with patch("routers.channels.journal") as journal:
        if failure == "batch_and_row":
            journal.log_entries.side_effect = sqlalchemy_style_error()
            journal.log_entry.side_effect = sqlalchemy_style_error()
            assert write_journal_rows([row], batch_id="b-1", log_tag="CHANNELS-BULK") == 1
        else:
            journal.log_entries.side_effect = KeyboardInterrupt()
            with pytest.raises(KeyboardInterrupt):
                write_journal_rows([row], batch_id="b-1", log_tag="CHANNELS-BULK")
    for text in log_outputs():
        assert "UNJOURNALLED MUTATION" in text
        assert "501" in text
        assert SECRET not in text and CAUSE not in text
        assert url_secret not in text
    if failure == "batch_and_row":
        console = log_outputs()[0]
        assert "OperationalError" in console
        assert "Traceback" in console


@pytest.mark.asyncio
async def test_login_failure_logs_only_the_exception_type(log_outputs):
    from config import DispatcharrSettings
    from dispatcharr_client import DispatcharrClient

    unregistered = "unlabelled-login-detail-7d1e44"
    client = DispatcharrClient(
        DispatcharrSettings(
            url=f"http://{HOST}", auth_method="password",
            username="operator", password="synthetic-login-pass",
        )
    )
    await client._client.aclose()

    def explode(request):
        raise httpx.ConnectError(f"{unregistered} {HOST}", request=request)

    client._client = httpx.AsyncClient(transport=httpx.MockTransport(explode))
    try:
        with pytest.raises(httpx.ConnectError):
            await client._login()
    finally:
        await client._client.aclose()
    for text in log_outputs():
        assert "Authentication error: ConnectError" in text
        assert unregistered not in text
        assert "Traceback" not in text


@pytest.mark.asyncio
async def test_guidance_passes_through_even_for_long_registered_numbers(async_client):
    """The minimum sweep length is not what protects guidance; the class is."""
    from log_utils import register_sensitive_values
    from tests.routers.test_renumber_baselines import make_client

    register_sensitive_values("1000", "2500")
    client = make_client(
        [{"id": 2500, "name": "ESPN", "channel_number": 1000, "streams": []}]
    )
    result = await poll_numbering(async_client, client, [renumber_op(2500, 40, 999)])
    assert result["errors"][0]["error"] == (
        "Channel 2500 was not renumbered: it is on channel number 1000, and this "
        "request expected 999. Somebody else changed it. Re-read the channel and "
        "decide before sending this again."
    )


@pytest.mark.asyncio
async def test_non_numeric_server_number_is_not_echoed_in_guidance(async_client):
    from tests.routers.test_renumber_baselines import make_client

    odd = f"odd-number {SECRET}"
    client = make_client([{"id": 1, "name": "ESPN", "channel_number": odd, "streams": []}])
    result = await poll_numbering(async_client, client, [renumber_op(1, 40, 5)])
    error = result["errors"][0]
    assert error["error"] == (
        "Channel 1 was not renumbered: it is on an unreadable channel number, and "
        "this request expected 5. Somebody else changed it. Re-read the channel "
        "and decide before sending this again."
    )
    # The structured payload is the server's value as read, unchanged.
    assert error["concurrentNumberChanges"] == [
        {"channelId": 1, "baselineNumber": 5, "serverNumber": odd},
    ]


def test_range_and_patch_guidance_render_non_numeric_server_numbers():
    from routers.channels import _stale_baselines_message

    message = _stale_baselines_message(
        [
            {"channelId": 1, "baselineNumber": 5, "serverNumber": "x-" + SECRET},
            {"channelId": 2, "baselineNumber": 6, "serverNumber": 7.5},
            {"channelId": 3, "baselineNumber": 8, "serverNumber": None},
        ],
        subject="Channels were",
    )
    assert SECRET not in message
    assert "channel 1 is on an unreadable channel number, and this request expected 5" in message
    assert "channel 2 is on channel number 7.5, and this request expected 6" in message
    assert "channel 3 is on channel number None, and this request expected 8" in message
