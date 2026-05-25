"""Journal-driven surgical un-merge for fuzzy merges (jnzst Q4).

The surgical un-merge removes ONLY the stream IDs a run added (from the journal
before/after lists) and preserves streams a concurrent edit added afterward —
unlike the snapshot restore, which clobbers concurrent edits. Falls back to the
snapshot restore when journal data is missing.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from auto_creation_engine import AutoCreationEngine


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _route_rollback_queries(mock_session, mock_execution):
    """Route the uc51o.5 AutoCreationSnapshot-existence probe to None so these
    LEGACY (no-snapshot) rollback tests stay on the entity-rollback /
    surgical-unmerge path.

    NOTE: "snapshot restore" in these test names refers to the legacy
    ``_rollback_modified_entity`` modified-entity fallback, NOT the ADR-010
    AutoCreationSnapshot table. ``rollback_execution`` now first probes the
    AutoCreationSnapshot table; a flat ``mock_session.query.return_value``
    returns the same chain for every query, which would make that probe see the
    execution mock and divert to the FULL restore. These runs have no
    AutoCreationSnapshot, so route that probe to None.
    """
    from models import AutoCreationSnapshot

    exec_chain = MagicMock()
    exec_chain.filter.return_value.first.return_value = mock_execution
    none_chain = MagicMock()
    none_chain.filter.return_value.first.return_value = None

    def _is_snapshot_arg(a):
        return a is AutoCreationSnapshot or getattr(a, "class_", None) is AutoCreationSnapshot

    def _query(*args, **kwargs):
        if any(_is_snapshot_arg(a) for a in args):
            return none_chain
        return exec_chain

    mock_session.query.side_effect = _query


class TestAddedStreamIds:
    def test_after_minus_before(self):
        added = AutoCreationEngine._added_stream_ids(
            {"stream_ids": [10]}, {"stream_ids": [10, 11]}
        )
        assert added == [11]

    def test_empty_when_malformed(self):
        assert AutoCreationEngine._added_stream_ids(None, None) == []
        assert AutoCreationEngine._added_stream_ids({}, {}) == []


class TestSurgicalUnmerge:
    def setup_method(self):
        self.client = MagicMock()
        self.client.delete_channel = AsyncMock()
        self.client.delete_channel_group = AsyncMock()
        self.client.update_channel = AsyncMock()
        self.engine = AutoCreationEngine(self.client)

    @patch("auto_creation_engine.journal.get_entries")
    @patch("auto_creation_engine.get_session")
    def test_removes_only_added_streams_and_preserves_later_add(
        self, mock_get_session, mock_get_entries
    ):
        """Pre-run channel had [10]; the run merged 11. AFTER the merge an
        operator added 12 concurrently. Rollback must remove ONLY 11 and keep
        both 10 (original) and 12 (concurrent edit)."""
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session

        mock_execution = MagicMock()
        mock_execution.status = "completed"
        mock_execution.mode = "execute"
        mock_execution.get_created_entities.return_value = []
        # Snapshot-restore would clobber 12 back to [10]; surgical must not.
        mock_execution.get_modified_entities.return_value = [
            {"type": "channel", "id": 3, "name": "WBAY",
             "previous": {"streams": [10]}},
        ]
        _route_rollback_queries(mock_session, mock_execution)

        # Journal records the merge: before [10], after [10, 11].
        mock_get_entries.return_value = {
            "results": [
                {"entity_id": 3, "action_type": "merge_stream",
                 "before_value": {"stream_ids": [10]},
                 "after_value": {"stream_ids": [10, 11],
                                 "match": {"score": 0.95}}},
            ],
        }
        # CURRENT live state includes the concurrently-added 12.
        self.client.get_channel = AsyncMock(
            return_value={"id": 3, "streams": [10, 11, 12]}
        )

        result = _run(self.engine.rollback_execution(7))

        assert result["success"] is True
        # Removed ONLY 11; kept 10 (original) and 12 (concurrent edit).
        self.client.update_channel.assert_called_once_with(3, {"streams": [10, 12]})
        assert mock_execution.status == "rolled_back"

    @patch("auto_creation_engine.journal.get_entries")
    @patch("auto_creation_engine.get_session")
    def test_falls_back_to_snapshot_when_no_journal_entries(
        self, mock_get_session, mock_get_entries
    ):
        """No merge journal entries for the batch → snapshot restore runs."""
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session

        mock_execution = MagicMock()
        mock_execution.status = "completed"
        mock_execution.mode = "execute"
        mock_execution.get_created_entities.return_value = []
        mock_execution.get_modified_entities.return_value = [
            {"type": "channel", "id": 3, "name": "ESPN",
             "previous": {"streams": [10]}},
        ]
        _route_rollback_queries(mock_session, mock_execution)

        mock_get_entries.return_value = {"results": []}  # no journal entries

        result = _run(self.engine.rollback_execution(8))

        assert result["success"] is True
        # Snapshot restore path: full pre-run list restored.
        self.client.update_channel.assert_called_once_with(3, {"streams": [10]})

    @patch("auto_creation_engine.journal.get_entries")
    @patch("auto_creation_engine.get_session")
    def test_surgical_skips_snapshot_restore(
        self, mock_get_session, mock_get_entries
    ):
        """When journal entries exist, the snapshot restore is SKIPPED — only
        the surgical update runs (one update_channel call, not two)."""
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session

        mock_execution = MagicMock()
        mock_execution.status = "completed"
        mock_execution.mode = "execute"
        mock_execution.get_created_entities.return_value = []
        mock_execution.get_modified_entities.return_value = [
            {"type": "channel", "id": 3, "name": "WBAY",
             "previous": {"streams": [10]}},
        ]
        _route_rollback_queries(mock_session, mock_execution)
        mock_get_entries.return_value = {
            "results": [
                {"entity_id": 3, "action_type": "merge_stream",
                 "before_value": {"stream_ids": [10]},
                 "after_value": {"stream_ids": [10, 11]}},
            ],
        }
        self.client.get_channel = AsyncMock(return_value={"id": 3, "streams": [10, 11]})

        _run(self.engine.rollback_execution(9))

        # Exactly one update (the surgical one) — snapshot restore did not run.
        self.client.update_channel.assert_called_once_with(3, {"streams": [10]})
