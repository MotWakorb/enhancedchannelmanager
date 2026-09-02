import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.orm import sessionmaker

from channel_pipeline_engine import ChannelPipelineEngine
from models import ChannelPipelineRule


def _rule(name, priority, *, event_sync=False, account_id=None):
    rule = ChannelPipelineRule(
        name=name,
        enabled=True,
        priority=priority,
        m3u_account_id=account_id,
        conditions=json.dumps([{"type": "always"}]),
        actions=json.dumps([{"type": "skip"}]),
    )
    if event_sync:
        from tests.event_sync_fixtures import event_sync_config

        rule.event_sync_config = json.dumps(event_sync_config())
    return rule


def test_request_and_worker_loader_use_fresh_detached_snapshot(test_session):
    from selected_pipeline_rules import load_selected_rule_snapshots

    later = _rule("Initial later", 20)
    first = _rule("Initial first", 10)
    test_session.add_all([later, first])
    test_session.commit()
    factory = sessionmaker(bind=test_session.get_bind())

    with patch("selected_pipeline_rules.get_session", side_effect=factory):
        request_snapshot = load_selected_rule_snapshots([later.id, first.id])

        test_session.query(ChannelPipelineRule).filter_by(id=first.id).update(
            {"name": "Edited before worker"}
        )
        test_session.commit()
        worker_snapshot = load_selected_rule_snapshots([later.id, first.id])

        test_session.query(ChannelPipelineRule).filter_by(id=first.id).update(
            {"name": "Edited after worker"}
        )
        test_session.commit()

    assert [rule.id for rule in request_snapshot] == [first.id, later.id]
    assert [rule.name for rule in worker_snapshot] == [
        "Edited before worker",
        "Initial later",
    ]
    assert worker_snapshot[0].name == "Edited before worker"


def test_selected_rule_validation_rejects_malformed_required_provider_state():
    from selected_pipeline_rules import selected_rule_issues

    rule = _rule("Malformed coverage", 1)
    rule.id = 7
    rule.required_provider_ids = "{}"

    issues = selected_rule_issues(rule)

    assert issues[0]["reason"] == "invalid"
    assert "required_provider_ids is malformed" in issues[0]["errors"]


@pytest.mark.asyncio
async def test_selected_engine_loads_worker_snapshot_before_external_data():
    engine = ChannelPipelineEngine(MagicMock())
    selected = [_rule("Snapshot", 1)]
    selected[0].id = 7
    events = []

    async def load_selected(rule_ids):
        events.append(("snapshot", rule_ids))
        return selected

    async def load_external():
        events.append(("external", None))
        raise RuntimeError("stop after ordering proof")

    engine._load_selected_rule_snapshots = load_selected
    engine._load_existing_data = load_external
    engine._load_rules = AsyncMock()

    with pytest.raises(RuntimeError, match="ordering proof"):
        await engine.run_pipeline(rule_ids=[7], require_all_rule_ids=True)

    assert events == [("snapshot", [7]), ("external", None)]
    engine._load_rules.assert_not_awaited()


@pytest.mark.parametrize(
    ("results", "expected_status", "expected_count", "expected_errors"),
    [
        ({"rule_match_counts": {1: 0}}, "completed", 0, 0),
        ({"rule_match_counts": {1: 3}}, "completed", 3, 0),
        (
            {
                "rule_match_counts": {1: 2},
                "rule_fetch_failures": {1: ["provider failed"]},
                "rule_fetch_successes": {1: 1},
            },
            "completed_with_errors",
            2,
            1,
        ),
        (
            {
                "rule_match_counts": {1: 0},
                "rule_fetch_failures": {1: ["provider failed"]},
                "rule_fetch_successes": {1: 0},
            },
            "failed",
            0,
            1,
        ),
        (
            {"rule_match_counts": {1: 2}, "rule_cap_ids": {1}},
            "capped",
            2,
            0,
        ),
    ],
)
def test_standard_outcomes_are_derived_from_execution_facts(
    results, expected_status, expected_count, expected_errors
):
    rule = SimpleNamespace(id=1, name="Standard", is_event_sync=lambda: False)

    outcome = ChannelPipelineEngine._selected_rule_outcomes([rule], results)[0]

    assert outcome["rule_kind"] == "standard"
    assert outcome["status"] == expected_status
    assert outcome["match_count"] == expected_count
    assert outcome["error_count"] == expected_errors


@pytest.mark.parametrize(
    ("summary", "warnings", "expected_status", "expected_attach", "expected_errors"),
    [
        ({"rule_id": 2, "attached": 0, "attach_errors": 0}, [], "completed", 0, 0),
        ({"rule_id": 2, "attached": 3, "attach_errors": 0}, [], "completed", 3, 0),
        (
            {"rule_id": 2, "attached": 2, "attach_errors": 1},
            [],
            "completed_with_errors",
            2,
            1,
        ),
        (
            {"rule_id": 2, "attached": 1, "attach_errors": 0, "capped": True},
            [],
            "capped",
            1,
            0,
        ),
        (
            {
                "rule_id": 2,
                "attached": 0,
                "attach_errors": 0,
                "promotion": {"capped": True},
            },
            [],
            "capped",
            0,
            0,
        ),
        (
            None,
            [{"type": "event_sync_fetch_failed", "rule_id": 2, "message": "fetch failed"}],
            "skipped",
            0,
            1,
        ),
        (
            None,
            [{"type": "event_sync_invalid_config", "rule_id": 2, "message": "invalid"}],
            "skipped",
            0,
            1,
        ),
    ],
)
def test_event_sync_outcomes_are_derived_from_attach_skip_and_cap_facts(
    summary, warnings, expected_status, expected_attach, expected_errors
):
    rule = SimpleNamespace(id=2, name="Event", is_event_sync=lambda: True)
    results = {
        "event_sync": [] if summary is None else [summary],
        "event_sync_warnings": warnings,
        # _run_event_sync_rules expands each attach error into one authoritative
        # failed_actions row; summary.attach_errors is display data, not a
        # second error source.
        "failed_actions": [
            {"rule_id": 2, "action_type": "event_sync_attach"}
            for _ in range(summary.get("attach_errors", 0) if summary else 0)
        ],
    }

    outcome = ChannelPipelineEngine._selected_rule_outcomes([rule], results)[0]

    assert outcome["rule_kind"] == "event_sync"
    assert outcome["status"] == expected_status
    assert outcome["attach_count"] == expected_attach
    assert outcome["error_count"] == expected_errors
    if expected_status == "skipped":
        assert outcome["skip_reason"]
    if expected_status == "capped":
        assert outcome["cap_reason"]


def test_mixed_outcomes_keep_canonical_selection_order():
    event = SimpleNamespace(id=2, name="Event", is_event_sync=lambda: True)
    standard = SimpleNamespace(id=8, name="Standard", is_event_sync=lambda: False)
    results = {
        "rule_match_counts": {8: 1},
        "event_sync": [{"rule_id": 2, "attached": 4, "attach_errors": 0}],
    }

    outcomes = ChannelPipelineEngine._selected_rule_outcomes(
        [event, standard], results
    )

    assert [item["rule_id"] for item in outcomes] == [2, 8]
    assert [item["rule_kind"] for item in outcomes] == ["event_sync", "standard"]


@pytest.mark.asyncio
async def test_standard_fetch_records_partial_and_total_source_failures():
    client = MagicMock()
    client.get_m3u_accounts = AsyncMock(return_value=[
        {"id": 10, "name": "Good"},
        {"id": 20, "name": "Bad"},
    ])

    async def fetch(*, m3u_account, **_kwargs):
        if m3u_account == 20:
            raise RuntimeError("source unavailable")
        return {"count": 0, "results": []}

    client.get_streams = AsyncMock(side_effect=fetch)
    engine = ChannelPipelineEngine(client)
    engine._existing_groups = []
    engine._load_stream_stats = AsyncMock()
    good_only = _rule("Good only", 0, account_id=10)
    good_only.id = 1
    failed_only = _rule("Bad only", 1, account_id=20)
    failed_only.id = 2

    await engine._fetch_streams(rules=[good_only, failed_only])

    assert engine._stream_fetch_facts == {
        "rule_fetch_successes": {1: 1, 2: 0},
        "rule_fetch_failures": {
            1: [],
            2: ["M3U account 20: source unavailable"],
        },
    }
