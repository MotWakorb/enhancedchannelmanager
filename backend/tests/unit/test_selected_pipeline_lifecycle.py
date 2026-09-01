import json
from datetime import datetime

import pytest
from unittest.mock import patch

from models import ChannelPipelineExecution
from routers.channel_pipeline import _mark_execution_failed
from task_engine import _abandon_orphaned_auto_creation_executions


def _outcome(rule_id, name, status="pending", kind="standard", **counts):
    return {
        "rule_id": rule_id,
        "rule_name": name,
        "rule_kind": kind,
        "status": status,
        **counts,
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
