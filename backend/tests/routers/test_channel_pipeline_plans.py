from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from routers.channel_pipeline import RunPipelineRequest, prepare_auto_creation_pipeline
from services.mutation_plan_store import mutation_plan_store


@pytest.mark.asyncio
async def test_prepare_pipeline_is_unrecorded_and_materializes_server_plan():
    engine = AsyncMock()
    engine.run_pipeline.return_value = {
        "execution_id": None,
        "dry_run_results": [{"stream_id": 4, "would_create": True}],
        "channels_created": 1,
    }
    engine.client = AsyncMock()
    engine._existing_channels = []
    with patch("routers.channel_pipeline._ensure_engine", AsyncMock(return_value=engine)), patch(
        "channel_pipeline_engine.ChannelPipelineEngine", return_value=engine
    ):
        response = await prepare_auto_creation_pipeline(
            RunPipelineRequest(dry_run=False, rule_ids=[3]), _admin=None
        )
    assert response["preview"]["channels_created"] == 1
    assert "execution_id" not in response["preview"]
    engine.run_pipeline.assert_awaited_once_with(
        dry_run=False, triggered_by="api", m3u_account_ids=None,
        rule_ids=[3], record_execution=False, plan_only=True, skip_prerefresh=True,
    )
    consumed = mutation_plan_store.consume(
        response["plan_id"], "channel_pipeline", response["plan_hash"]
    )
    assert consumed.payload["request"]["rule_ids"] == [3]


@pytest.mark.asyncio
async def test_prerefresh_prepare_only_resolves_accounts_and_does_not_refresh_or_plan_writes():
    engine = AsyncMock()
    rule = MagicMock()
    rule.get_event_sync_config.return_value = {
        "refresh_providers_before_run": True,
        "master_group_id": 4,
        "secondary_group_ids": [5],
    }
    engine._load_rules.return_value = [rule]
    engine._resolve_event_sync_refresh_accounts.return_value = {9, 7}
    with patch("routers.channel_pipeline._ensure_engine", AsyncMock(return_value=engine)):
        response = await prepare_auto_creation_pipeline(
            RunPipelineRequest(dry_run=False), _admin=None
        )
    assert response["phase"] == "refresh"
    assert response["preview"] == {"m3u_account_ids_to_refresh": [7, 9]}
    engine.run_pipeline.assert_not_awaited()
