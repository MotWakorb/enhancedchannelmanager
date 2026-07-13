"""enhancedchannelmanager-7wuhd — persist Event Sync run counters + kind flag
on the execution record.

Product footgun: an Event Sync run's execution details showed
``Streams Evaluated: 0 / Streams Matched: 0 / Channels Created: 0`` while the
log showed real activity, because the standard Pass 1/2 counters don't map to
the event_sync model and the real event_sync counters (secondary_streams,
attached, already_attached, ambiguous_skipped, unmatched, parse_failed, ...)
were never persisted as structured fields.

This module pins the additive *persistence* behavior on
``ChannelPipelineExecution``:

  * ``set_event_sync_summary`` / ``get_event_sync_summary`` round-trip a JSON
    array of per-rule summary dicts; NULL reads as ``[]``.
  * ``is_event_sync`` defaults to False (standard run) and persists True for a
    pure event_sync run.
  * ``to_dict`` always exposes both ``is_event_sync`` and ``event_sync_summary``
    so the executions UI can branch its layout unconditionally.
"""
from datetime import datetime

from models import ChannelPipelineExecution


def _make_execution(**overrides) -> ChannelPipelineExecution:
    kwargs = dict(
        mode="execute",
        triggered_by="manual",
        started_at=datetime.utcnow(),
        status="completed",
    )
    kwargs.update(overrides)
    return ChannelPipelineExecution(**kwargs)


class TestEventSyncSummaryAccessors:
    """In-memory get/set accessors (no session required)."""

    def test_get_returns_empty_list_when_unset(self):
        """A fresh row with NULL event_sync_summary reads as []."""
        execution = _make_execution()
        assert execution.get_event_sync_summary() == []

    def test_set_then_get_round_trips(self):
        """set_event_sync_summary stores JSON; get parses it back."""
        summaries = [
            {
                "rule_id": 7,
                "rule_name": "NFL Sunday",
                "secondary_streams": 12,
                "attached": 0,
                "already_attached": 9,
                "ambiguous_skipped": 0,
                "unmatched": 0,
                "parse_failed": 0,
                "attach_errors": 0,
            }
        ]
        execution = _make_execution()
        execution.set_event_sync_summary(summaries)
        assert isinstance(execution.event_sync_summary, str)
        assert execution.get_event_sync_summary() == summaries

    def test_set_empty_stores_null(self):
        """An empty list persists as NULL (mirrors set_warnings)."""
        execution = _make_execution()
        execution.set_event_sync_summary([])
        assert execution.event_sync_summary is None
        assert execution.get_event_sync_summary() == []

    def test_get_tolerates_corrupt_json(self):
        """Malformed JSON reads as [] rather than raising."""
        execution = _make_execution(event_sync_summary="{not valid json")
        assert execution.get_event_sync_summary() == []


class TestEventSyncSummaryToDict:
    """to_dict must always expose both new fields."""

    def test_standard_run_defaults(self):
        """A standard run: is_event_sync False, event_sync_summary []."""
        result = _make_execution().to_dict()
        assert result["is_event_sync"] is False
        assert result["event_sync_summary"] == []

    def test_event_sync_run_exposes_summary_and_flag(self):
        """A pure event_sync run round-trips the flag + summaries in to_dict."""
        summaries = [{"rule_id": 3, "attached": 4, "already_attached": 2}]
        execution = _make_execution(is_event_sync=True)
        execution.set_event_sync_summary(summaries)
        result = execution.to_dict()
        assert result["is_event_sync"] is True
        assert result["event_sync_summary"] == summaries


class TestEventSyncSummaryPersistence:
    """Full DB round-trip: the columns survive commit + reload."""

    def test_columns_persist_across_reload(self, test_session):
        """Persisting an execution with the new columns and reloading returns
        the same values (proves the migration column mapping works)."""
        summaries = [
            {
                "rule_id": 11,
                "rule_name": "MLB",
                "secondary_streams": 20,
                "attached": 5,
                "already_attached": 3,
                "ambiguous_skipped": 1,
                "unmatched": 2,
                "parse_failed": 0,
            }
        ]
        execution = _make_execution(is_event_sync=True)
        execution.set_event_sync_summary(summaries)
        test_session.add(execution)
        test_session.commit()
        exec_id = execution.id
        test_session.expunge_all()

        reloaded = (
            test_session.query(ChannelPipelineExecution)
            .filter(ChannelPipelineExecution.id == exec_id)
            .first()
        )
        assert reloaded is not None
        assert reloaded.is_event_sync is True
        assert reloaded.get_event_sync_summary() == summaries

        d = reloaded.to_dict()
        assert d["is_event_sync"] is True
        assert d["event_sync_summary"] == summaries

    def test_standard_run_persists_false_and_empty(self, test_session):
        """A standard run persists is_event_sync=False and empty summary."""
        execution = _make_execution()
        test_session.add(execution)
        test_session.commit()
        exec_id = execution.id
        test_session.expunge_all()

        reloaded = (
            test_session.query(ChannelPipelineExecution)
            .filter(ChannelPipelineExecution.id == exec_id)
            .first()
        )
        assert reloaded.is_event_sync is False
        assert reloaded.get_event_sync_summary() == []
