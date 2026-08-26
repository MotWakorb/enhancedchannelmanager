"""Archive restore must distinguish an empty destination from a failed read."""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from dbas.destination_read import (
    DestinationReadError,
    DestinationUnreadableError,
    ReadObservingClient,
    destination_read_reason,
)
from dbas.preflight import ImportPlan, PlanCategory
from dbas.restore_contracts import (
    EntityType,
    IdRemapTable,
    RestoreReport,
    RollbackLedger,
)
from dbas.restore_orchestrator import default_importer_steps, run_restore
from tasks.dbas_restore import DbasRestoreTask
from tests.tasks.test_dbas_restore_task import (
    _apply_report,
    _dry_run_report,
    _make_task,
    _write_artifact,
)


def _http_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request(
        "GET",
        "http://destination.invalid/api/channels/groups/?token=do-not-disclose",
        headers={"Authorization": "Bearer header-secret-do-not-disclose"},
    )
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(
        "request failed for token=do-not-disclose",
        request=request,
        response=response,
    )


_LOG_SECRET_CORPUS = (
    "destination.invalid",
    "token=do-not-disclose",
    "header-secret-do-not-disclose",
    "request failed for token=do-not-disclose",
)


@pytest.mark.asyncio
async def test_missing_authenticated_probe_fails_closed():
    reason = await destination_read_reason(object())

    assert reason is not None
    assert "authenticated readability check" in reason


@pytest.mark.parametrize("confirm_apply", [False, True])
@pytest.mark.asyncio
async def test_unreadable_destination_is_refused_before_orchestration(
    tmp_path, confirm_apply
):
    artifact = _write_artifact(tmp_path)
    task = _make_task(artifact, confirm_apply=confirm_apply)
    client = AsyncMock()
    client.get_channel_groups.side_effect = _http_error(401)
    dry_run = AsyncMock(return_value=_dry_run_report())
    apply = AsyncMock(return_value=_apply_report())

    with patch("dispatcharr_client.get_client", return_value=client), patch(
        "dbas.restore_orchestrator.run_dry_run", dry_run
    ), patch("dbas.restore_orchestrator.run_restore", apply):
        result = await task.execute()

    assert result.success is False
    assert "authentication" in result.message.lower()
    assert "401" in result.message
    assert "do-not-disclose" not in result.message
    client.get_channel_groups.assert_awaited_once_with()
    dry_run.assert_not_awaited()
    apply.assert_not_awaited()


@pytest.mark.asyncio
async def test_genuinely_empty_destination_still_runs_the_restore_preview(tmp_path):
    artifact = _write_artifact(tmp_path)
    task = _make_task(artifact)
    client = AsyncMock()
    client.get_channel_groups.return_value = []
    dry_run = AsyncMock(return_value=_dry_run_report())

    with patch("dispatcharr_client.get_client", return_value=client), patch(
        "dbas.restore_orchestrator.run_dry_run", dry_run
    ):
        result = await task.execute()

    assert result.success is True
    client.get_channel_groups.assert_awaited_once_with()
    observed_client = dry_run.await_args.kwargs["client"]
    report = dry_run.await_args.kwargs["report"]
    assert isinstance(observed_client, ReadObservingClient)
    assert report.destination_unreadable is None


@pytest.mark.asyncio
async def test_read_failure_after_gate_fails_the_preview_with_the_read_named(tmp_path):
    artifact = _write_artifact(tmp_path)
    task = _make_task(artifact)
    client = AsyncMock()
    client.get_channel_groups.return_value = []
    client.get_m3u_accounts.side_effect = _http_error(503)

    async def run_dry_run(*, client, report, **_kwargs):
        try:
            await client.get_m3u_accounts()
        except DestinationReadError:
            pass  # Mirrors the importer fallback that currently uses existing = [].
        with pytest.raises(DestinationUnreadableError):
            await client.create_m3u_account({"name": "must not be written"})
        return report

    with patch("dispatcharr_client.get_client", return_value=client), patch(
        "dbas.restore_orchestrator.run_dry_run", side_effect=run_dry_run
    ):
        result = await task.execute()

    report = result.details["restore_report"]
    assert result.success is False
    assert "get_m3u_accounts" in report["destination_unreadable"]
    assert "503" in result.message
    assert "do-not-disclose" not in result.message
    client.create_m3u_account.assert_not_awaited()


_SWALLOWED_IMPORTER_READS = (
    # m3u_accounts.py
    "get_m3u_accounts",
    # epg_sources.py
    "get_epg_sources",
    # groups_profiles.py (four category configurations)
    "get_channel_groups",
    "get_channel_profiles",
    "get_stream_profiles",
    "get_server_groups",
    # settings_agents.py
    "get_user_agents",
    "get_dvr_rules",
    "get_recordings",
    "get_core_setting_id_map",
    # channels.py
    "get_channels",
    "get_streams",
    # logos.py
    "get_all_logos_paginated",
    # users.py
    "get_users",
)


@pytest.mark.parametrize("method_name", _SWALLOWED_IMPORTER_READS)
@pytest.mark.asyncio
async def test_every_swallowed_importer_read_marks_the_destination_unreadable(
    method_name
):
    inner = AsyncMock()
    getattr(inner, method_name).side_effect = _http_error(503)
    report = RestoreReport(is_dry_run=True)
    client = ReadObservingClient(inner, report)

    with pytest.raises(DestinationReadError) as read_failure:
        await getattr(client, method_name)()

    assert read_failure.value.operation == method_name
    assert read_failure.value.category == "server_error"
    assert read_failure.value.status_code == 503
    assert method_name in report.destination_unreadable
    assert "503" in report.destination_unreadable
    assert "do-not-disclose" not in report.destination_unreadable


@pytest.mark.asyncio
async def test_failed_read_blocks_forward_mutations_including_cleanup_delete():
    inner = AsyncMock()
    inner.get_m3u_accounts.side_effect = _http_error(503)
    report = RestoreReport(is_dry_run=False)
    client = ReadObservingClient(inner, report, reject_mutations=True)

    with pytest.raises(DestinationReadError):
        await client.get_m3u_accounts()

    with pytest.raises(DestinationUnreadableError) as refused:
        await client.create_m3u_account({"name": "would duplicate"})

    assert "get_m3u_accounts" in str(refused.value)
    assert "do-not-disclose" not in str(refused.value)
    inner.create_m3u_account.assert_not_awaited()

    with pytest.raises(DestinationUnreadableError):
        await client.delete_m3u_account(42)
    inner.delete_m3u_account.assert_not_awaited()


@pytest.mark.asyncio
async def test_only_explicit_compensation_scope_allows_delete_after_failed_read():
    inner = AsyncMock()
    inner.get_m3u_accounts.side_effect = _http_error(503)
    report = RestoreReport(is_dry_run=False)
    client = ReadObservingClient(inner, report, reject_mutations=True)

    with pytest.raises(DestinationReadError):
        await client.get_m3u_accounts()

    with client.compensation():
        await client.delete_m3u_account(42)

    inner.delete_m3u_account.assert_awaited_once_with(42)
    with pytest.raises(DestinationUnreadableError):
        await client.delete_m3u_account(43)


_REAL_IMPORTER_READ_MATRIX = (
    (EntityType.M3U_ACCOUNT, "get_m3u_accounts", "create_m3u_account", {"id": 1, "name": "M3U"}),
    (EntityType.EPG_SOURCE, "get_epg_sources", "create_epg_source", {"id": 2, "name": "EPG"}),
    (EntityType.CHANNEL_GROUP, "get_channel_groups", "create_channel_group", {"id": 3, "name": "News"}),
    (EntityType.USER_AGENT, "get_user_agents", "create_user_agent", {"id": 4, "name": "Agent"}),
    (EntityType.DVR_RULE, "get_dvr_rules", "create_dvr_rule", {"id": 5, "name": "Rule"}),
    (EntityType.CHANNEL, "get_channels", "create_channel", {"id": 6, "name": "Channel", "channel_number": 6}),
    (
        EntityType.LOGO,
        "get_all_logos_paginated",
        "create_logo",
        {"id": 7, "name": "Logo", "url": "https://cdn.example.invalid/logo.png"},
    ),
)


@pytest.mark.parametrize(
    ("entity_type", "getter", "mutation", "archive_row"),
    _REAL_IMPORTER_READ_MATRIX,
)
@pytest.mark.asyncio
async def test_real_importer_orchestrator_paths_fail_closed_and_log_safely(
    tmp_path, caplog, entity_type, getter, mutation, archive_row
):
    """Each swallowed-read family runs through its production registry step."""
    inner = AsyncMock()
    getattr(inner, getter).side_effect = _http_error(503)
    report = RestoreReport(is_dry_run=False)
    client = ReadObservingClient(inner, report, reject_mutations=True)
    plan = ImportPlan(
        manifest={"schema_version": 1},
        categories=[
            PlanCategory(entity_type=entity_type, entities=[archive_row], selected=True)
        ],
    )
    step = next(s for s in default_importer_steps() if s.entity_type == entity_type)

    with caplog.at_level(logging.WARNING):
        out = await run_restore(
            plan=plan,
            client=client,
            steps=[step],
            report=report,
            ledger=RollbackLedger(restore_id="read-matrix-%s" % entity_type.value),
            remap=IdRemapTable(),
            confirm_apply=True,
            ledger_dir=tmp_path,
        )

    assert getter in out.destination_unreadable
    assert "HTTP 503" in out.destination_unreadable
    getattr(inner, mutation).assert_not_awaited()
    assert "category=server_error" in caplog.text
    assert "status=503" in caplog.text
    for secret in _LOG_SECRET_CORPUS:
        assert secret not in caplog.text
        assert secret not in "\n".join(out.notes)


@pytest.mark.parametrize(
    ("streams", "blocked_delete"),
    (
        ([{"id": 77, "name": "old placeholder", "url": None, "m3u_account": 42}], "delete_stream"),
        ([], "delete_m3u_account"),
    ),
)
@pytest.mark.asyncio
async def test_post_rebind_read_failure_blocks_forward_cleanup_deletes(
    streams, blocked_delete
):
    from dbas.custom_stream_fallback import CUSTOM_STREAM_ACCOUNT_NAME
    from dbas.placeholder_rebind import rebind_placeholder_streams

    inner = AsyncMock()
    inner.get_streams.return_value = streams
    inner.get_channels.return_value = []
    inner.get_m3u_accounts.side_effect = _http_error(503)
    report = RestoreReport(is_dry_run=False)
    client = ReadObservingClient(inner, report, reject_mutations=True)
    ledger = RollbackLedger(restore_id="post-rebind-read")
    ledger.record_created(EntityType.M3U_ACCOUNT, 42, CUSTOM_STREAM_ACCOUNT_NAME)

    await rebind_placeholder_streams(
        client=client,
        report=report,
        ledger=ledger,
        remap=IdRemapTable(),
        archive_channels=[],
        allow_fuzzy=True,
    )

    assert "get_m3u_accounts" in report.destination_unreadable
    getattr(inner, blocked_delete).assert_not_awaited()
