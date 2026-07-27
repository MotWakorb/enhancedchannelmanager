"""Scored-fuzzy merge_streams + journal provenance (enhancedchannelmanager-jnzst).

Component A: the rule loose_name_match + min_score path resolves the merge
target through the unified scoring core (services.dedup_matcher), enforces the
M1 callsign hard-reject and Q1 no-callsign policy, scopes to the allowlist
(Q2), and journals score + provenance into after_value JSON.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from channel_pipeline_executor import ActionExecutor, ExecutionContext
from channel_pipeline_evaluator import StreamContext


CHANNEL_TVG_ID = "WBAY-DT(ABC)(WBAYDT).us"


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_executor(channels, execution_id=555):
    client = MagicMock()
    client.update_channel = AsyncMock(return_value={})
    return client, ActionExecutor(
        client,
        existing_channels=channels,
        existing_groups=[{"id": 42, "name": "Locals"}, {"id": 99, "name": "Other"}],
        execution_id=execution_id,
    )


def _wbay_channel(group_id=42, streams=None):
    return {
        "id": 1, "name": "ABC: WBAY Green Bay", "tvg_id": CHANNEL_TVG_ID,
        "channel_number": 2, "channel_group_id": group_id,
        "streams": streams if streams is not None else [],
        # Auto-created merge target: the scored-fuzzy path must be allowed to
        # adopt it (enhancedchannelmanager-orzck — manual channels are protected,
        # auto channels are valid merge targets).
        "auto_created": True,
    }


def _scored_action(min_score=0.7, **overrides):
    # Action.from_dict flattens all non-"type" keys into params, so the merge
    # params live at the TOP level of the action dict (not nested).
    action = {
        "type": "merge_streams",
        "target": "auto",
        "loose_name_match": True,
        "min_score": min_score,
        "target_channel_in_group": [42],
    }
    action.update(overrides)
    return action


def _stream(name, sid=201, tvg_id=None):
    return StreamContext(
        stream_id=sid, stream_name=name, m3u_account_id=1,
        m3u_account_name="P", group_name="Locals", tvg_id=tvg_id,
    )


class TestScoredFuzzyResolution:
    def test_positive_merges_into_wbay(self):
        client, ex = _make_executor([_wbay_channel()])
        result = _run(ex.execute(
            _scored_action(), _stream("WI | Green Bay | ABC 2 WBAY"),
            ExecutionContext(), normalization_group_ids=[1],
        ))
        assert result.success is True
        assert result.modified is True
        assert result.entity_id == 1
        client.update_channel.assert_awaited()

    def test_negative_wgba_not_merged(self):
        client, ex = _make_executor([_wbay_channel()])
        result = _run(ex.execute(
            _scored_action(), _stream("WI | Green Bay | NBC WGBA"),
            ExecutionContext(), normalization_group_ids=[1],
        ))
        # M1 hard-reject → no candidate → skipped, NO write.
        assert result.skipped is True
        client.update_channel.assert_not_awaited()

    def test_allowlist_scopes_resolution(self):
        # WBAY channel lives in group 99, allowlist only permits 42 → no match.
        client, ex = _make_executor([_wbay_channel(group_id=99)])
        result = _run(ex.execute(
            _scored_action(), _stream("WI | Green Bay | ABC 2 WBAY"),
            ExecutionContext(), normalization_group_ids=[1],
        ))
        assert result.skipped is True
        client.update_channel.assert_not_awaited()


class TestNoCallsignPolicy:
    def _no_callsign_channel(self):
        return {"id": 5, "name": "Travel Channel", "tvg_id": None,
                "channel_number": 7, "channel_group_id": 42, "streams": [],
                "auto_created": True}

    def test_no_callsign_rejected_by_default(self):
        client, ex = _make_executor([self._no_callsign_channel()])
        result = _run(ex.execute(
            _scored_action(min_score=0.6), _stream("Travel Channel"),
            ExecutionContext(), normalization_group_ids=[1],
        ))
        # Both sides lack a callsign → absent verdict → rejected by default.
        assert result.skipped is True
        client.update_channel.assert_not_awaited()

    def test_no_callsign_admitted_via_opt_in_at_high_score(self):
        client, ex = _make_executor([self._no_callsign_channel()])
        result = _run(ex.execute(
            _scored_action(min_score=0.6, allow_no_callsign=True),
            _stream("Travel Channel"),  # exact → 1.0 >= 0.90
            ExecutionContext(), normalization_group_ids=[1],
        ))
        assert result.modified is True
        client.update_channel.assert_awaited()

    def test_no_callsign_opt_in_still_rejects_below_090(self):
        client, ex = _make_executor([self._no_callsign_channel()])
        result = _run(ex.execute(
            _scored_action(min_score=0.6, allow_no_callsign=True),
            _stream("Travel Adventure Show Network"),  # weak fuzzy < 0.90
            ExecutionContext(), normalization_group_ids=[1],
        ))
        assert result.skipped is True
        client.update_channel.assert_not_awaited()


class TestJournalProvenance:
    def test_journal_records_score_and_provenance(self):
        client, ex = _make_executor([_wbay_channel()])
        with patch("channel_pipeline_executor.journal.log_entries") as log_entries:
            result = _run(ex.execute(
                _scored_action(), _stream("WI | Green Bay | ABC 2 WBAY"),
                ExecutionContext(), normalization_group_ids=[1],
            ))
            ex._flush_journal_buffer()
        assert result.modified is True
        log_entries.assert_called_once()
        entry = log_entries.call_args.kwargs["entries"][0]
        after = entry["after_value"]
        assert "stream_ids" in after
        match = after["match"]
        assert 0.0 <= match["score"] <= 1.0
        assert match["effective_threshold"] == 0.7
        assert match["signal"] in (
            "callsign-exact", "tvg_id-override", "fuzzy-with-callsign",
            "fuzzy-no-callsign-floor",
        )
        assert match["stream_callsign"] == "WBAY"
        assert match["candidate_callsign"] == "WBAY"
        assert match["allowlist_groups"] == [42]
        # FIX 4: rule_id is present in provenance. None when execute() is called
        # without one (this test path); the threaded case is pinned below.
        assert "rule_id" in match
        assert match["rule_id"] is None
        assert entry["batch_id"] == "555"

    def test_journal_records_threaded_rule_id(self):
        # FIX 4: the firing rule's id (threaded from the engine via execute)
        # lands in provenance["rule_id"] for M7 completeness.
        client, ex = _make_executor([_wbay_channel()])
        with patch("channel_pipeline_executor.journal.log_entries") as log_entries:
            result = _run(ex.execute(
                _scored_action(), _stream("WI | Green Bay | ABC 2 WBAY"),
                ExecutionContext(), normalization_group_ids=[1],
                rule_id=4242,
            ))
            ex._flush_journal_buffer()
        assert result.modified is True
        match = log_entries.call_args.kwargs["entries"][0]["after_value"]["match"]
        assert match["rule_id"] == 4242

    def test_legacy_merge_journal_has_no_match_provenance(self):
        # An exact (non-scored) merge: after_value carries stream_ids only.
        client, ex = _make_executor([
            {"id": 1, "name": "ESPN", "tvg_id": "ESPN.US",
             "channel_number": 100, "channel_group_id": 42, "streams": []},
        ])
        action = {"type": "merge_streams", "target": "auto"}
        with patch("channel_pipeline_executor.journal.log_entries") as log_entries:
            _run(ex.execute(action, _stream("ESPN", sid=900),
                            ExecutionContext(), normalization_group_ids=[1]))
            ex._flush_journal_buffer()
        if log_entries.called:
            after = log_entries.call_args.kwargs["entries"][0]["after_value"]
            assert "match" not in after
