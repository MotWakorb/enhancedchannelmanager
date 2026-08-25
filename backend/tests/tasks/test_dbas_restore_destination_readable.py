"""Archive restore must distinguish an empty destination from a failed read."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from dbas.destination_read import (
    DestinationUnreadableError,
    ReadObservingClient,
    destination_read_reason,
)
from dbas.restore_contracts import RestoreReport
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
    )
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(
        "request failed for token=do-not-disclose",
        request=request,
        response=response,
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
        except httpx.HTTPStatusError:
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

    with pytest.raises(httpx.HTTPStatusError):
        await getattr(client, method_name)()

    assert method_name in report.destination_unreadable
    assert "503" in report.destination_unreadable
    assert "do-not-disclose" not in report.destination_unreadable


@pytest.mark.asyncio
async def test_failed_read_blocks_fallback_mutation_but_allows_rollback_delete():
    inner = AsyncMock()
    inner.get_m3u_accounts.side_effect = _http_error(503)
    report = RestoreReport(is_dry_run=False)
    client = ReadObservingClient(inner, report, reject_mutations=True)

    with pytest.raises(httpx.HTTPStatusError):
        await client.get_m3u_accounts()

    with pytest.raises(DestinationUnreadableError) as refused:
        await client.create_m3u_account({"name": "would duplicate"})

    assert "get_m3u_accounts" in str(refused.value)
    assert "do-not-disclose" not in str(refused.value)
    inner.create_m3u_account.assert_not_awaited()

    await client.delete_m3u_account(42)
    inner.delete_m3u_account.assert_awaited_once_with(42)
