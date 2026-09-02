import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from channel_pipeline_engine import ChannelPipelineEngine
from channel_pipeline_evaluator import StreamContext
from channel_pipeline_executor import ActionResult
from config import DispatcharrSettings
from models import ChannelPipelineRule


def _rule(*, required=(1, 2), skip_struck=False):
    rule = ChannelPipelineRule(
        name="Provider coverage",
        enabled=True,
        priority=0,
        conditions=json.dumps([{"type": "always"}]),
        actions=json.dumps([{
            "type": "create_channel",
            "name_template": "{stream_name}",
            "if_exists": "merge",
        }]),
        skip_struck_streams=skip_struck,
    )
    rule.id = 7
    rule.set_required_provider_ids(list(required))
    return rule


def _stream(stream_id, name, provider_id, provider_name):
    return StreamContext(
        stream_id=stream_id,
        stream_name=name,
        m3u_account_id=provider_id,
        m3u_account_name=provider_name,
    )


def _run(
    streams, *, required=(1, 2), struck=(), dry_run=True, normalization=None,
    normalization_groups=False, normalization_init_error=None, channel_cap=None,
):
    client = MagicMock()
    engine = ChannelPipelineEngine(client)
    engine._existing_channels = []
    engine._existing_groups = []
    engine._struck_stream_ids = set(struck)
    engine._required_provider_names = {1: "Primary", 2: "Backup", 3: "Extra"}
    engine._reconcile_orphans = AsyncMock()
    engine._update_rule_stats = AsyncMock()
    engine._refresh_dummy_epg_and_retry = AsyncMock()
    execution = MagicMock(id=99)
    rule = _rule(required=required, skip_struck=bool(struck))
    if normalization or normalization_groups:
        rule.set_normalization_group_ids([5])

    executed = []

    async def execute(_self, _action, stream, _ctx, *args, **kwargs):
        executed.append(stream.stream_id)
        _ctx.channels_created += 1
        return ActionResult(
            success=True,
            action_type="create_channel",
            description=f"create {stream.stream_name}",
            created=True,
        )

    settings = DispatcharrSettings()
    if channel_cap is not None:
        settings.max_auto_created_channels_per_run = channel_cap

    normalization_patch = patch(
        "normalization_engine.get_normalization_engine",
        side_effect=normalization_init_error,
        return_value=normalization,
    )
    with patch("channel_pipeline_engine.get_session", return_value=MagicMock()), \
         patch("channel_pipeline_engine.get_settings", return_value=settings), \
         normalization_patch, \
         patch("channel_pipeline_engine.ActionExecutor.execute", new=execute):
        result = asyncio.get_event_loop().run_until_complete(
            engine._process_streams(
                streams,
                [rule],
                execution,
                dry_run=dry_run,
            )
        )
    return result, executed


@pytest.mark.parametrize("dry_run", [True, False], ids=["dry-run", "live"])
def test_complete_cohort_executes_every_required_provider_once(dry_run):
    result, executed = _run([
        _stream(10, "ESPN", 1, "Primary"),
        _stream(20, "ESPN", 2, "Backup"),
    ], dry_run=dry_run)

    assert executed == [10, 20]
    assert result["required_provider_blocks"] == []


def test_missing_provider_blocks_entire_cohort_and_names_requirement():
    result, executed = _run([_stream(10, "ESPN", 1, "Primary")])

    assert executed == []
    assert result["required_provider_blocks"] == [{
        "rule_id": 7,
        "rule_name": "Provider coverage",
        "cohort": "ESPN",
        "missing_provider_ids": [2],
        "missing_providers": ["Backup"],
        "unavailable_provider_ids": [],
        "unavailable_providers": [],
    }]
    assert "Backup" in result["execution_log"][0]["actions_executed"][0]["description"]


def test_missing_provider_blocks_live_run_without_emitting_dry_run_rows():
    result, executed = _run(
        [_stream(10, "ESPN", 1, "Primary")],
        dry_run=False,
    )

    assert executed == []
    assert result["required_provider_blocks"][0]["missing_provider_ids"] == [2]
    assert result["dry_run_results"] == []


def test_struck_required_provider_blocks_cohort_as_unavailable():
    result, executed = _run([
        _stream(10, "ESPN", 1, "Primary"),
        _stream(20, "ESPN", 2, "Backup"),
    ], struck=(20,))

    assert executed == []
    block = result["required_provider_blocks"][0]
    assert block["missing_provider_ids"] == []
    assert block["unavailable_provider_ids"] == [2]
    assert block["unavailable_providers"] == ["Backup"]


def test_healthy_alternative_from_same_provider_satisfies_coverage():
    result, executed = _run([
        _stream(10, "ESPN", 1, "Primary"),
        _stream(20, "ESPN", 2, "Backup"),
        _stream(21, "ESPN", 2, "Backup"),
    ], struck=(20,))

    assert executed == [10, 21]
    assert result["required_provider_blocks"] == []


def test_extra_provider_stream_executes_with_complete_cohort():
    result, executed = _run([
        _stream(10, "ESPN", 1, "Primary"),
        _stream(20, "ESPN", 2, "Backup"),
        _stream(30, "ESPN", 3, "Extra"),
    ])

    assert executed == [10, 20, 30]
    assert result["required_provider_blocks"] == []


def test_same_provider_names_in_unrelated_cohort_do_not_satisfy_requirement():
    result, executed = _run([
        _stream(10, "ESPN", 1, "Primary"),
        _stream(20, "CNN", 2, "Backup"),
    ])

    assert executed == []
    assert {block["cohort"] for block in result["required_provider_blocks"]} == {"ESPN", "CNN"}


def test_rule_normalization_combines_provider_variants_into_one_cohort():
    normalization = MagicMock()
    normalization.normalize.side_effect = lambda _name, group_ids: SimpleNamespace(
        normalized="ESPN"
    )
    result, executed = _run([
        _stream(10, "ESPN US", 1, "Primary"),
        _stream(20, "ESPN UK", 2, "Backup"),
    ], normalization=normalization)

    assert executed == [10, 20]
    assert result["required_provider_blocks"] == []
    assert normalization.normalize.call_count >= 2


def test_rule_without_required_providers_keeps_per_stream_behavior():
    result, executed = _run([
        _stream(10, "ESPN", 1, "Primary"),
    ], required=())

    assert executed == [10]
    assert result["required_provider_blocks"] == []


def test_live_cap_finishes_admitted_cohort_without_starting_next_cohort():
    result, executed = _run([
        _stream(10, "ESPN", 1, "Primary"),
        _stream(40, "CNN", 1, "Primary"),
        _stream(20, "ESPN", 2, "Backup"),
        _stream(50, "CNN", 2, "Backup"),
        _stream(30, "ESPN", 3, "Extra"),
    ], required=(1, 2), dry_run=False, channel_cap=1)

    assert executed == [10, 20, 30]
    assert result["channels_created"] == 3
    assert result["capped"] is True


def test_normalization_failure_blocks_without_claiming_present_providers_missing():
    normalization = MagicMock()
    normalization.normalize.side_effect = RuntimeError("invalid normalization rule")

    result, executed = _run([
        _stream(10, "ESPN US", 1, "Primary"),
        _stream(20, "ESPN UK", 2, "Backup"),
    ], normalization=normalization)

    assert executed == []
    assert len(result["required_provider_blocks"]) == 2
    for block in result["required_provider_blocks"]:
        assert block["missing_provider_ids"] == []
        assert block["unavailable_provider_ids"] == []
        assert block["reason"] == "normalization_failed"
        assert block["normalization_error"] == "invalid normalization rule"
        assert block["stream_id"] in {10, 20}


@pytest.mark.parametrize("dry_run", [True, False], ids=["dry-run", "live"])
@pytest.mark.parametrize(
    "names", [("ESPN", "ESPN"), ("ESPN US", "ESPN UK")],
    ids=["same-raw-name", "different-raw-names"],
)
def test_normalization_initialization_failure_blocks_every_affected_stream(
    dry_run, names
):
    result, executed = _run(
        [
            _stream(10, names[0], 1, "Primary"),
            _stream(20, names[1], 2, "Backup"),
        ],
        dry_run=dry_run,
        normalization_groups=True,
        normalization_init_error=RuntimeError("normalization database unavailable"),
    )

    assert executed == []
    assert len(result["required_provider_blocks"]) == 2
    assert {block["stream_id"] for block in result["required_provider_blocks"]} == {10, 20}
    for block in result["required_provider_blocks"]:
        assert block["reason"] == "normalization_failed"
        assert block["normalization_error"] == "normalization database unavailable"


def test_normalization_initialization_failure_does_not_block_raw_name_coverage():
    result, executed = _run([
        _stream(10, "ESPN", 1, "Primary"),
        _stream(20, "ESPN", 2, "Backup"),
    ], normalization_init_error=RuntimeError("normalization database unavailable"))

    assert executed == [10, 20]
    assert result["required_provider_blocks"] == []


@pytest.mark.parametrize("stored", ["[11]", "{}", "false", "0", "\"\"", "not-json"])
def test_malformed_stored_required_providers_are_safe_to_read_but_remain_invalid(stored):
    rule = _rule()
    rule.required_provider_ids = stored

    assert rule.get_required_provider_ids() == []
    assert rule.get_required_provider_ids_error() is not None


@pytest.mark.parametrize("stored", [None, "null", "[]"])
def test_unconfigured_stored_required_providers_are_valid(stored):
    rule = _rule()
    rule.required_provider_ids = stored

    assert rule.get_required_provider_ids() == []
    assert rule.get_required_provider_ids_error() is None


def test_malformed_required_provider_configuration_blocks_creation():
    rule = _rule()
    rule.required_provider_ids = "{}"
    client = MagicMock()
    engine = ChannelPipelineEngine(client)
    engine._existing_channels = []
    engine._existing_groups = []
    engine._struck_stream_ids = set()
    engine._reconcile_orphans = AsyncMock()
    engine._update_rule_stats = AsyncMock()
    engine._refresh_dummy_epg_and_retry = AsyncMock()
    executed = []

    async def execute(_self, _action, stream, _ctx, *args, **kwargs):
        executed.append(stream.stream_id)
        return ActionResult(success=True, action_type="create_channel", created=True)

    with patch("channel_pipeline_engine.get_session", return_value=MagicMock()), \
         patch("channel_pipeline_engine.get_settings", return_value=DispatcharrSettings()), \
         patch("normalization_engine.get_normalization_engine", return_value=None), \
         patch("channel_pipeline_engine.ActionExecutor.execute", new=execute):
        result = asyncio.get_event_loop().run_until_complete(
            engine._process_streams(
                [_stream(10, "ESPN", 1, "Primary")], [rule], MagicMock(id=99),
                dry_run=False,
            )
        )

    assert executed == []
    assert result["required_provider_blocks"][0]["reason"] == "invalid_configuration"
