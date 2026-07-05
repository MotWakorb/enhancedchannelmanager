"""bd-0hjrk.2 — rollback_auto_creation guarantees.

Two behaviors are pinned here:

1. UN-MERGE of merge-only runs (modified entities, 0 created): after rollback
   the touched channel is restored to its pre-run stream list. The decisive
   reversed-restore assertion already lives in
   ``test_channel_pipeline_engine.py::TestChannelPipelineEngineRollback
   .test_rollback_unmerge_multiple_into_same_channel_restores_original``
   (bd-a7okb). We add a focused single-channel un-merge assertion here so the
   guarantee has a home alongside the new refusal test; the cumulative-snapshot
   reversed-order case is NOT duplicated.

2. REFUSAL on no restore data: an execution with ZERO created AND ZERO modified
   entities must NOT be silently marked ``rolled_back``. It is a no-op that
   *looks* like a clean rollback (entities_removed=0 / entities_restored=0).
   The engine now refuses: returns ``success=False`` with an explanatory error
   and leaves ``status`` unchanged. Runs that DID create or modify anything are
   unaffected.
"""
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

from channel_pipeline_engine import ChannelPipelineEngine


def _route_rollback_queries(mock_session, mock_execution):
    """Route the uc51o.5 snapshot-existence probe to None so these LEGACY
    (no-snapshot) rollback tests stay on the delete-created-only path.

    ``rollback_execution`` now probes for an ChannelPipelineSnapshot and diverts to
    the full restore when one exists. A flat ``mock_session.query.return_value``
    returns the same chain for every query, so the probe would see the execution
    mock and wrongly treat the run as snapshotted. These tests model no-snapshot
    runs, so the snapshot query must return None.
    """
    from models import ChannelPipelineSnapshot

    exec_chain = MagicMock()
    exec_chain.filter.return_value.first.return_value = mock_execution
    none_chain = MagicMock()
    none_chain.filter.return_value.first.return_value = None

    def _is_snapshot_arg(a):
        return a is ChannelPipelineSnapshot or getattr(a, "class_", None) is ChannelPipelineSnapshot

    def _query(*args, **kwargs):
        if any(_is_snapshot_arg(a) for a in args):
            return none_chain
        return exec_chain

    mock_session.query.side_effect = _query


class TestRollbackUnmergeMergeOnlyRun:
    """A merge-only run (0 created, modified present) un-merges on rollback."""

    def setup_method(self):
        self.client = MagicMock()
        self.client.delete_channel = AsyncMock()
        self.client.delete_channel_group = AsyncMock()
        self.client.update_channel = AsyncMock()
        self.engine = ChannelPipelineEngine(self.client)

    @patch("channel_pipeline_engine.get_session")
    def test_merge_only_run_restores_pre_run_stream_set(self, mock_get_session):
        """A run that ONLY merged a stream into a pre-existing channel (no
        channels created) restores that channel's pre-run stream list.

        The reversed-order cumulative-snapshot edge case is covered by the
        a7okb test cited in the module docstring; here we assert the simple
        un-merge guarantee for a single-merge merge-only run.
        """
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session

        mock_execution = MagicMock()
        mock_execution.status = "completed"
        mock_execution.mode = "execute"
        # Zero created — this is a merge-only run.
        mock_execution.get_created_entities.return_value = []
        # Pre-run the channel held streams [10]; the run merged 11 in.
        mock_execution.get_modified_entities.return_value = [
            {"type": "channel", "id": 3, "name": "ESPN", "previous": {"streams": [10]}},
        ]
        _route_rollback_queries(mock_session, mock_execution)

        result = asyncio.get_event_loop().run_until_complete(
            self.engine.rollback_execution(1)
        )

        assert result["success"] is True
        assert result["entities_removed"] == 0
        assert result["entities_restored"] == 1
        # The channel is restored to its pre-run stream set — the merged stream
        # is un-merged.
        self.client.update_channel.assert_called_once_with(3, {"streams": [10]})
        assert mock_execution.status == "rolled_back"


class TestRollbackRefusesWhenNoRestoreData:
    """0 created + 0 modified → refuse, do not mark rolled_back."""

    def setup_method(self):
        self.client = MagicMock()
        self.client.delete_channel = AsyncMock()
        self.client.delete_channel_group = AsyncMock()
        self.client.update_channel = AsyncMock()
        self.engine = ChannelPipelineEngine(self.client)

    @patch("channel_pipeline_engine.get_session")
    def test_refuses_and_does_not_mark_rolled_back(self, mock_get_session):
        """An execution with no recorded created/modified entities cannot be
        guaranteed rolled back — refuse rather than silently no-op."""
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session

        mock_execution = MagicMock()
        mock_execution.status = "completed"
        mock_execution.mode = "execute"
        mock_execution.get_created_entities.return_value = []
        mock_execution.get_modified_entities.return_value = []
        _route_rollback_queries(mock_session, mock_execution)

        result = asyncio.get_event_loop().run_until_complete(
            self.engine.rollback_execution(42)
        )

        assert result["success"] is False
        assert "error" in result
        # Message must name the execution and explain why it refuses.
        assert "42" in result["error"]
        assert "cannot guarantee" in result["error"].lower()
        # The decisive guard: status is NOT flipped to rolled_back, and no
        # commit-style mutation of the status happened.
        assert mock_execution.status != "rolled_back"
        # Nothing was deleted or restored.
        self.client.delete_channel.assert_not_called()
        self.client.update_channel.assert_not_called()

    @patch("channel_pipeline_engine.get_session")
    def test_run_with_created_entities_is_unaffected(self, mock_get_session):
        """A run that created entities still rolls back (only the both-empty
        case refuses)."""
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session

        mock_execution = MagicMock()
        mock_execution.status = "completed"
        mock_execution.mode = "execute"
        mock_execution.get_created_entities.return_value = [
            {"type": "channel", "id": 1, "name": "ESPN"},
        ]
        mock_execution.get_modified_entities.return_value = []
        _route_rollback_queries(mock_session, mock_execution)

        result = asyncio.get_event_loop().run_until_complete(
            self.engine.rollback_execution(7)
        )

        assert result["success"] is True
        assert mock_execution.status == "rolled_back"
        self.client.delete_channel.assert_called_once_with(1)

    @patch("channel_pipeline_engine.get_session")
    def test_run_with_only_modified_entities_is_unaffected(self, mock_get_session):
        """A merge-only run (0 created, modified present) still rolls back."""
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session

        mock_execution = MagicMock()
        mock_execution.status = "completed"
        mock_execution.mode = "execute"
        mock_execution.get_created_entities.return_value = []
        mock_execution.get_modified_entities.return_value = [
            {"type": "channel", "id": 3, "name": "ESPN", "previous": {"streams": [10]}},
        ]
        _route_rollback_queries(mock_session, mock_execution)

        result = asyncio.get_event_loop().run_until_complete(
            self.engine.rollback_execution(9)
        )

        assert result["success"] is True
        assert mock_execution.status == "rolled_back"
        self.client.update_channel.assert_called_once_with(3, {"streams": [10]})

    @patch("channel_pipeline_engine.get_session")
    def test_refusal_preserves_already_rolled_back_guard(self, mock_get_session):
        """The already-rolled-back guard still fires before the no-data check."""
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session

        mock_execution = MagicMock()
        mock_execution.status = "rolled_back"
        _route_rollback_queries(mock_session, mock_execution)

        result = asyncio.get_event_loop().run_until_complete(
            self.engine.rollback_execution(1)
        )

        assert result["success"] is False
        assert "already rolled back" in result["error"].lower()
