"""No-secret regressions for 5e3r3/ej4um, crossing real HTTP/log boundaries."""

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
