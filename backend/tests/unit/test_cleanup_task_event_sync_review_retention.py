"""Event Sync pending-review retention in the Database Cleanup task."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

from models import ChannelPipelineRule, EventSyncReview
from services.event_sync_review_store import purge_stale_pending_reviews
from tasks.cleanup import CleanupTask


DAY_MS = 86_400_000
NOW_MS = 2_000_000_000_000


def _rule(session: Session) -> None:
    session.add(ChannelPipelineRule(
        id=1,
        name="Retention rule",
        conditions="[]",
        actions="[]",
    ))
    session.commit()


def _review(session: Session, row_id: int, last_seen_at: int, status: str = "pending") -> None:
    session.add(EventSyncReview(
        id=row_id,
        rule_id=1,
        provider_id=row_id,
        stream_name_hash=f"hash-{row_id}",
        event_key=f"event-{row_id}",
        status=status,
        created_at=last_seen_at,
        last_seen_at=last_seen_at,
        evidence=json.dumps({}),
    ))
    session.commit()


def test_default_retention_is_disabled_and_round_trips_config():
    task = CleanupTask()
    assert task.event_sync_review_retention_days == 0
    assert task.get_config()["event_sync_review_retention_days"] == 0

    task.update_config({"event_sync_review_retention_days": 30})
    assert task.event_sync_review_retention_days == 30
    task.update_config({"event_sync_review_retention_days": 0})
    assert task.event_sync_review_retention_days == 0
    task.update_config({"event_sync_review_retention_days": 3650})
    assert task.event_sync_review_retention_days == 3650


@pytest.mark.parametrize("value", [-1, 3651, 1.5, True, "30"])
def test_retention_rejects_values_outside_strict_integer_contract(value):
    task = CleanupTask()
    with pytest.raises(ValueError, match="event_sync_review_retention_days"):
        task.update_config({"event_sync_review_retention_days": value})


def test_disabled_retention_does_not_query_or_delete(test_session):
    _rule(test_session)
    _review(test_session, 1, NOW_MS - 100 * DAY_MS)

    outcome = purge_stale_pending_reviews(
        test_session, retention_days=0, now_ms=NOW_MS
    )

    assert outcome == {
        "enabled": False,
        "cutoff_ms": None,
        "deleted": 0,
        "batch_limit": 200,
    }
    assert test_session.query(EventSyncReview).count() == 1


def test_purge_is_strictly_older_preserves_boundary_newer_and_decisions(test_session):
    _rule(test_session)
    cutoff = NOW_MS - 30 * DAY_MS
    _review(test_session, 1, cutoff - 1)
    _review(test_session, 2, cutoff)
    _review(test_session, 3, cutoff + 1)
    _review(test_session, 4, cutoff - 1, status="accepted")

    outcome = purge_stale_pending_reviews(
        test_session, retention_days=30, now_ms=NOW_MS
    )

    assert outcome["deleted"] == 1
    assert outcome["cutoff_ms"] == cutoff
    assert {row.id for row in test_session.query(EventSyncReview).all()} == {2, 3, 4}


def test_purge_is_bounded_deterministic_and_repeatable(test_session):
    _rule(test_session)
    for row_id in range(1, 205):
        _review(test_session, row_id, NOW_MS - (40 * DAY_MS) + row_id)

    first = purge_stale_pending_reviews(
        test_session, retention_days=30, batch_limit=200, now_ms=NOW_MS
    )
    assert first["deleted_ids"] == list(range(1, 201))
    assert test_session.query(EventSyncReview).count() == 4

    second = purge_stale_pending_reviews(
        test_session, retention_days=30, batch_limit=200, now_ms=NOW_MS
    )
    third = purge_stale_pending_reviews(
        test_session, retention_days=30, batch_limit=200, now_ms=NOW_MS
    )
    assert second["deleted_ids"] == [201, 202, 203, 204]
    assert third["deleted"] == 0


def test_purge_rolls_back_and_reports_failure(test_session):
    _rule(test_session)
    _review(test_session, 1, NOW_MS - 40 * DAY_MS)
    _review(test_session, 2, NOW_MS - 40 * DAY_MS)

    with patch.object(test_session, "commit", side_effect=RuntimeError("disk full")):
        with pytest.raises(RuntimeError, match="disk full"):
            purge_stale_pending_reviews(
                test_session, retention_days=30, now_ms=NOW_MS
            )

    assert {row.id for row in test_session.query(EventSyncReview).all()} == {1, 2}


@pytest.mark.asyncio
async def test_cleanup_task_surfaces_purge_failure_in_result(test_session):
    settings = SimpleNamespace(
        auto_creation_snapshot_days=30,
        auto_creation_snapshot_max=50,
        m3u_snapshot_days=90,
        m3u_change_log_days=90,
        unique_client_connection_days=90,
    )
    task = CleanupTask()
    task.event_sync_review_retention_days = 30
    task.vacuum_db = False

    with (
        patch("tasks.cleanup.get_session", return_value=test_session),
        patch("config.get_settings", return_value=settings),
        patch(
            "tasks.cleanup.purge_stale_pending_reviews",
            side_effect=RuntimeError("forced retention failure"),
        ),
    ):
        result = await task.execute()

    assert result.success is True
    assert result.failed_count == 1
    assert result.details["errors"] == [
        "Event Sync review cleanup: forced retention failure"
    ]
