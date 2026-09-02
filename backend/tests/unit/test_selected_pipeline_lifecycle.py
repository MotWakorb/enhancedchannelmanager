import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import sessionmaker
from unittest.mock import AsyncMock, MagicMock, patch

from channel_pipeline_engine import ChannelPipelineEngine
from models import ChannelPipelineExecution
from routers.channel_pipeline import _create_pending_execution, _mark_execution_failed
from task_engine import _abandon_orphaned_auto_creation_executions


def _outcome(rule_id, name, status="pending", kind="standard", **counts):
    return {
        "rule_id": rule_id,
        "rule_name": name,
        "rule_kind": kind,
        "status": status,
        **counts,
    }


def _rule(rule_id, name, *, event_sync=False, priority=0):
    from tests.event_sync_fixtures import event_sync_config

    config = event_sync_config()
    return SimpleNamespace(
        id=rule_id,
        name=name,
        priority=priority,
        is_event_sync=lambda: event_sync,
        get_event_sync_config=lambda: config if event_sync else None,
        get_actions=lambda: [],
        get_conditions=lambda: [{"type": "always"}],
        get_normalization_group_ids=lambda: [],
        get_managed_channel_ids=lambda: [],
        m3u_account_id=None,
        target_group_id=None,
        stop_on_first_match=False,
        sort_field=None,
        stream_sort_field=None,
        skip_struck_streams=False,
        match_scope_target_group=False,
        match_scope_group_id=None,
        allow_manual_channel_merge=False,
        fold_match_key=False,
    )


def _session_factory(test_session):
    return sessionmaker(
        bind=test_session.get_bind(), expire_on_commit=False,
    )


def _stored_outcomes(factory, execution_id):
    session = factory()
    try:
        execution = session.get(ChannelPipelineExecution, execution_id)
        return execution.status, execution.get_selected_rule_outcomes()
    finally:
        session.close()


def _event_results():
    return {
        "streams_merged": 0,
        "streams_skipped": 0,
        "modified_entities": [],
        "created_entities": [],
        "execution_log": [],
        "dry_run_results": [],
        "rule_match_counts": {},
        "failed_actions": [],
        "channels_created": 0,
    }


def _event_summary(rule_id):
    return {
        "rule_id": rule_id,
        "attached": 1,
        "attach_errors": 0,
        "ambiguous_skipped": 0,
        "unmatched": 0,
        "parse_failed": 0,
        "already_attached": 0,
        "capped": False,
        "cap": 10,
        "cap_overage": 0,
        "attach_entries": [],
        "review_candidates": [],
    }


def _completed_results(*, event_warnings=None):
    return {
        "streams_evaluated": 0,
        "streams_matched": 0,
        "channels_created": 0,
        "channels_updated": 0,
        "groups_created": 0,
        "streams_merged": 0,
        "channels_touched": 0,
        "streams_skipped": 0,
        "streams_removed": 0,
        "channels_removed": 0,
        "channels_moved": 0,
        "pending_merges_added": 0,
        "created_entities": [],
        "modified_entities": [],
        "dry_run_results": [],
        "conflicts": [],
        "execution_log": [],
        "rule_match_counts": {},
        "streams_probed": 0,
        "failed_actions": [],
        "event_sync": [],
        "event_sync_warnings": event_warnings or [],
    }


@pytest.mark.parametrize(
    "stored",
    [
        "not-json",
        "{}",
        "42",
        "null",
        "[]",
        json.dumps([{"rule_id": 1}]),
        json.dumps([_outcome(1, "One"), _outcome(1, "Duplicate")]),
        json.dumps([_outcome(True, "Boolean id")]),
        json.dumps([_outcome(1, "Negative", match_count=-1)]),
        json.dumps([_outcome(1, "Boolean count", error_count=True)]),
        json.dumps([_outcome(1, "Bad status", status="mystery")]),
        json.dumps([_outcome(1, "Bad kind", kind="other")]),
    ],
)
def test_non_null_malformed_selected_storage_stays_selected_and_corrupt(stored):
    execution = ChannelPipelineExecution(
        started_at=datetime.utcnow(),
        status="completed",
        selected_rule_outcomes=stored,
    )

    payload = execution.to_dict()

    assert payload["run_scope"] == "selected"
    assert payload["selected_rule_integrity"] == "corrupt"
    assert payload["selected_rule_ids"] == []
    assert payload["selected_rule_outcomes"] == []


def test_null_selected_storage_uses_legacy_scope_precedence():
    deleted_single = ChannelPipelineExecution(
        started_at=datetime.utcnow(), rule_id=None, rule_name="Deleted rule"
    )
    run_all = ChannelPipelineExecution(started_at=datetime.utcnow())

    assert deleted_single.to_dict()["run_scope"] == "single"
    assert run_all.to_dict()["run_scope"] == "all"
    assert deleted_single.to_dict()["selected_rule_integrity"] == "not_selected"


def test_valid_selected_storage_preserves_identity_order_and_counts():
    outcomes = [
        _outcome(4, "Cached standard", "completed", match_count=3, error_count=0),
        _outcome(
            9,
            "Cached event sync",
            "capped",
            kind="event_sync",
            attach_count=2,
            error_count=1,
            cap_reason="attach cap reached",
        ),
    ]
    execution = ChannelPipelineExecution(
        started_at=datetime.utcnow(), selected_rule_outcomes=json.dumps(outcomes)
    )

    payload = execution.to_dict()

    assert payload["selected_rule_integrity"] == "valid"
    assert payload["selected_rule_ids"] == [4, 9]
    assert payload["selected_rule_outcomes"] == outcomes


@pytest.mark.asyncio
async def test_standard_phase_checkpoints_before_event_sync_fatal(test_session):
    event = _rule(2, "Event", event_sync=True, priority=0)
    standard = _rule(1, "Standard", priority=10)
    selected = [event, standard]
    factory = _session_factory(test_session)
    with patch("routers.channel_pipeline.get_session", side_effect=factory):
        execution_id = _create_pending_execution(
            mode="dry_run", triggered_by="api", selected_rules=selected,
        )

    engine = ChannelPipelineEngine(MagicMock())
    engine._existing_channels = []
    engine._existing_groups = []

    async def fatal_event_phase(*args, **kwargs):
        _, outcomes = _stored_outcomes(factory, execution_id)
        assert [item["rule_id"] for item in outcomes] == [event.id, standard.id]
        assert [item["status"] for item in outcomes] == ["pending", "completed"]
        raise RuntimeError("event phase fatal")

    engine._run_event_sync_rules = fatal_event_phase
    with patch("channel_pipeline_engine.get_session", side_effect=factory), patch(
        "channel_pipeline_engine.get_settings"
    ) as settings:
        settings.return_value.default_channel_profile_ids = []
        settings.return_value.timezone_preference = "both"
        settings.return_value.max_auto_creation_log_entries = 100
        settings.return_value.max_auto_created_channels_per_run = 100
        with pytest.raises(RuntimeError, match="event phase fatal"):
            await engine._process_streams(
                [], [standard],
                SimpleNamespace(id=execution_id), True,
                triggered_by="api", event_sync_rules=[event],
                selected_rules=selected,
            )

    with patch("routers.channel_pipeline.get_session", side_effect=factory):
        _mark_execution_failed(execution_id, RuntimeError("event phase fatal"))
    status, outcomes = _stored_outcomes(factory, execution_id)
    assert status == "failed"
    assert [item["status"] for item in outcomes] == ["not_run", "completed"]


@pytest.mark.asyncio
async def test_event_sync_fatal_interrupts_current_and_leaves_later_not_run(
    test_session,
):
    current = _rule(2, "Current", event_sync=True)
    later = _rule(3, "Later", event_sync=True)
    factory = _session_factory(test_session)
    with patch("routers.channel_pipeline.get_session", side_effect=factory):
        execution_id = _create_pending_execution(
            mode="dry_run", triggered_by="api", selected_rules=[current, later],
        )

    client = MagicMock()
    client.get_m3u_accounts = AsyncMock(return_value=[])
    client.get_all_m3u_group_settings = AsyncMock(return_value={})
    engine = ChannelPipelineEngine(client)
    engine._fetch_event_sync_secondary_streams = AsyncMock(return_value=[])
    executor = MagicMock()
    executor.execute_event_sync_rule = AsyncMock(
        side_effect=RuntimeError("current rule fatal")
    )

    with patch("channel_pipeline_engine.get_session", side_effect=factory):
        with pytest.raises(RuntimeError, match="current rule fatal"):
            await engine._run_event_sync_rules(
                [current, later], executor, _event_results(), True,
                triggered_by="api", channels_touched_ids=set(),
                execution_id=execution_id,
            )
    with patch("routers.channel_pipeline.get_session", side_effect=factory):
        _mark_execution_failed(execution_id, RuntimeError("current rule fatal"))

    status, outcomes = _stored_outcomes(factory, execution_id)
    assert status == "failed"
    assert [item["status"] for item in outcomes] == ["interrupted", "not_run"]


@pytest.mark.asyncio
async def test_prior_event_sync_completion_survives_later_fatal(test_session):
    first = _rule(2, "First", event_sync=True)
    second = _rule(3, "Second", event_sync=True)
    factory = _session_factory(test_session)
    with patch("routers.channel_pipeline.get_session", side_effect=factory):
        execution_id = _create_pending_execution(
            mode="dry_run", triggered_by="api", selected_rules=[first, second],
        )

    client = MagicMock()
    client.get_m3u_accounts = AsyncMock(return_value=[])
    client.get_all_m3u_group_settings = AsyncMock(return_value={})
    engine = ChannelPipelineEngine(client)
    engine._fetch_event_sync_secondary_streams = AsyncMock(return_value=[])
    executor = MagicMock()
    executor._channel_by_id = {}
    executor.execute_event_sync_rule = AsyncMock(side_effect=[
        _event_summary(first.id), RuntimeError("second rule fatal"),
    ])
    engine._apply_event_sync_profile_action = AsyncMock(return_value=None)

    with patch("channel_pipeline_engine.get_session", side_effect=factory):
        with pytest.raises(RuntimeError, match="second rule fatal"):
            await engine._run_event_sync_rules(
                [first, second], executor, _event_results(), True,
                triggered_by="api", channels_touched_ids=set(),
                execution_id=execution_id,
            )
    with patch("routers.channel_pipeline.get_session", side_effect=factory):
        _mark_execution_failed(execution_id, RuntimeError("second rule fatal"))

    status, outcomes = _stored_outcomes(factory, execution_id)
    assert status == "failed"
    assert [item["status"] for item in outcomes] == ["completed", "interrupted"]


@pytest.mark.asyncio
async def test_standard_fatal_interrupts_standard_and_leaves_event_sync_not_run(
    test_session,
):
    standard = _rule(1, "Standard")
    event = _rule(2, "Event", event_sync=True)
    factory = _session_factory(test_session)
    with patch("routers.channel_pipeline.get_session", side_effect=factory):
        execution_id = _create_pending_execution(
            mode="dry_run", triggered_by="api", selected_rules=[standard, event],
        )

    engine = ChannelPipelineEngine(MagicMock())
    engine._load_selected_rule_snapshots = AsyncMock(
        return_value=[standard, event]
    )
    engine._load_existing_data = AsyncMock()
    engine._detect_disabled_normalization_group_warnings = AsyncMock(return_value=[])
    engine._fetch_streams = AsyncMock(side_effect=RuntimeError("standard fatal"))

    with patch("channel_pipeline_engine.get_session", side_effect=factory):
        with pytest.raises(RuntimeError, match="standard fatal"):
            await engine.run_pipeline(
                dry_run=True, triggered_by="api", rule_ids=[1, 2],
                execution_id=execution_id, require_all_rule_ids=True,
            )
    with patch("routers.channel_pipeline.get_session", side_effect=factory):
        _mark_execution_failed(execution_id, RuntimeError("standard fatal"))

    status, outcomes = _stored_outcomes(factory, execution_id)
    assert status == "failed"
    assert [item["status"] for item in outcomes] == ["interrupted", "not_run"]


@pytest.mark.asyncio
async def test_event_sync_fetch_skip_makes_selected_parent_completed_with_errors(
    test_session,
):
    event = _rule(2, "Event", event_sync=True)
    factory = _session_factory(test_session)
    with patch("routers.channel_pipeline.get_session", side_effect=factory):
        execution_id = _create_pending_execution(
            mode="dry_run", triggered_by="api", selected_rules=[event],
        )

    engine = ChannelPipelineEngine(MagicMock())
    engine._load_selected_rule_snapshots = AsyncMock(return_value=[event])
    engine._load_existing_data = AsyncMock()
    engine._detect_disabled_normalization_group_warnings = AsyncMock(return_value=[])
    engine._apply_global_filters = AsyncMock(return_value=([], []))
    engine._process_streams = AsyncMock(return_value=_completed_results(
        event_warnings=[{
            "type": "event_sync_fetch_failed",
            "rule_id": event.id,
            "rule_name": event.name,
            "message": "secondary fetch failed",
        }]
    ))
    engine._update_rule_stats = AsyncMock()

    with patch("channel_pipeline_engine.get_session", side_effect=factory):
        result = await engine.run_pipeline(
            dry_run=True, triggered_by="api", rule_ids=[event.id],
            execution_id=execution_id, require_all_rule_ids=True,
        )

    status, outcomes = _stored_outcomes(factory, execution_id)
    assert result["status"] == status == "completed_with_errors"
    assert outcomes[0]["status"] == "skipped"
    assert outcomes[0]["error_count"] == 1
    assert all(item["status"] not in {"pending", "running"} for item in outcomes)


@pytest.mark.asyncio
@pytest.mark.parametrize("single", [False, True], ids=["run-all", "single-rule"])
async def test_non_selected_pipeline_runs_never_persist_selected_checkpoints(
    test_session, single,
):
    standard = _rule(1, "Standard")
    factory = _session_factory(test_session)
    engine = ChannelPipelineEngine(MagicMock())
    engine._load_existing_data = AsyncMock()
    engine._load_rules = AsyncMock(return_value=[standard])
    engine._detect_disabled_normalization_group_warnings = AsyncMock(return_value=[])
    engine._fetch_streams = AsyncMock(return_value=[])
    engine._apply_global_filters = AsyncMock(return_value=([], []))
    engine._process_streams = AsyncMock(return_value=_completed_results())
    engine._update_rule_stats = AsyncMock()

    execution_id = None
    if single:
        with patch("routers.channel_pipeline.get_session", side_effect=factory):
            execution_id = _create_pending_execution(
                mode="dry_run", triggered_by="api",
                rule_name=standard.name,
            )

    with patch("channel_pipeline_engine.get_session", side_effect=factory):
        if single:
            result = await engine.run_rule(
                standard.id, dry_run=True, triggered_by="api",
                execution_id=execution_id,
            )
        else:
            result = await engine.run_pipeline(
                dry_run=True, triggered_by="api",
            )

    session = factory()
    try:
        execution = session.get(ChannelPipelineExecution, result["execution_id"])
        assert execution.status == "completed"
        assert execution.selected_rule_outcomes is None
        assert execution.to_dict()["run_scope"] == (
            "single" if single else "all"
        )
    finally:
        session.close()


def test_graceful_failure_preserves_terminal_children_and_terminalizes_nonterminal(
    test_session,
):
    execution = ChannelPipelineExecution(
        mode="execute",
        triggered_by="api",
        started_at=datetime.utcnow(),
        status="running",
    )
    execution.set_selected_rule_outcomes(
        [
            _outcome(1, "Already done", "completed", match_count=2),
            _outcome(2, "Started", "running"),
            _outcome(3, "Untouched", "pending"),
        ]
    )
    test_session.add(execution)
    test_session.commit()

    with patch("routers.channel_pipeline.get_session", return_value=test_session):
        _mark_execution_failed(execution.id, RuntimeError("fatal after first rule"))

    test_session.expire_all()
    stored = test_session.get(ChannelPipelineExecution, execution.id)
    assert stored.status == "failed"
    assert [item["status"] for item in stored.get_selected_rule_outcomes()] == [
        "completed",
        "interrupted",
        "not_run",
    ]


def test_supervisor_failure_terminalizes_parent_even_when_selected_storage_is_corrupt(
    test_session,
):
    execution = ChannelPipelineExecution(
        mode="execute",
        triggered_by="api",
        started_at=datetime.utcnow(),
        status="running",
        selected_rule_outcomes="{broken",
    )
    test_session.add(execution)
    test_session.commit()

    with patch("routers.channel_pipeline.get_session", return_value=test_session):
        _mark_execution_failed(execution.id, RuntimeError("worker failed"))

    test_session.expire_all()
    stored = test_session.get(ChannelPipelineExecution, execution.id)
    assert stored.status == "failed"
    assert stored.selected_rule_outcomes == "{broken"
    assert stored.to_dict()["selected_rule_integrity"] == "corrupt"


def test_hard_crash_abandons_only_nonterminal_selected_children(test_session):
    execution = ChannelPipelineExecution(
        mode="execute",
        triggered_by="api",
        started_at=datetime.utcnow(),
        status="running",
    )
    execution.set_selected_rule_outcomes(
        [
            _outcome(1, "Done", "completed", match_count=1),
            _outcome(2, "Started", "running"),
            _outcome(3, "Queued", "pending"),
        ]
    )
    test_session.add(execution)
    test_session.commit()

    assert _abandon_orphaned_auto_creation_executions(test_session) == 1

    test_session.expire_all()
    stored = test_session.get(ChannelPipelineExecution, execution.id)
    assert stored.status == "abandoned"
    assert [item["status"] for item in stored.get_selected_rule_outcomes()] == [
        "completed",
        "abandoned",
        "abandoned",
    ]
