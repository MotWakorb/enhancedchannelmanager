"""Regression tests for bead g0uuf — scoped lookup with duplicate channel names.

A user debug bundle surfaced the failure: five channels all literally named
"ESPN", one per group (a supported GH-92 layout). Every executor lookup map
holds ONE channel per key, so a group-scoped ``_find_channel_by_name`` could
only test one arbitrary survivor. When that survivor sat in another group the
lookup reported "not found" even though an exact-name match existed in the
scoped group — ``if_exists=merge_only`` skipped the stream and
``if_exists=merge`` created a duplicate channel in the scoped group.

The fix keeps the legacy single-slot maps (and their winner semantics) but
adds multi-candidate companion indices that each lookup stage scans when the
legacy pick fails its scope/manual gates.
"""
from unittest.mock import MagicMock, patch
import asyncio

from channel_pipeline_evaluator import StreamContext
from channel_pipeline_executor import ActionExecutor, ExecutionContext


def _channel(cid, name, group_id, auto_created=True, **extra):
    ch = {"id": cid, "name": name, "channel_group_id": group_id, "streams": []}
    if auto_created:
        ch["auto_created"] = True
    ch.update(extra)
    return ch


def _espn_twins():
    """Mirror the debug bundle: same name in five groups, load order by id."""
    return [
        _channel(166902, "ESPN", 4451),
        _channel(218340, "ESPN", 4452),
        _channel(218758, "ESPN", 4453),
        _channel(236818, "ESPN", 5210),
        _channel(248009, "ESPN", 4454),
    ]


class TestScopedLookupDuplicateNames:
    """_find_channel_by_name with the same name in several groups."""

    def setup_method(self):
        self.client = MagicMock()

    def _executor(self, channels, groups=None):
        return ActionExecutor(
            self.client, existing_channels=channels,
            existing_groups=groups or [])

    def test_scoped_lookup_finds_in_scope_twin(self):
        """The user repro: scope group 5210 must find id 236818, not None."""
        executor = self._executor(_espn_twins())
        found = executor._find_channel_by_name(
            "ESPN", scope_group_id=5210, block_manual=False)
        assert found is not None, (
            "scoped lookup reported 'not found' although an exact-name match "
            "exists in the scoped group (g0uuf regression)")
        assert found["id"] == 236818

    def test_each_group_scope_resolves_its_own_twin(self):
        executor = self._executor(_espn_twins())
        for group_id, expected_id in [
            (4451, 166902), (4452, 218340), (4453, 218758),
            (5210, 236818), (4454, 248009),
        ]:
            found = executor._find_channel_by_name(
                "ESPN", scope_group_id=group_id, block_manual=False)
            assert found and found["id"] == expected_id, (
                f"scope {group_id}: got {found and found['id']}, "
                f"want {expected_id}")

    def test_unscoped_lookup_preserves_legacy_last_wins(self):
        """No scope → the historical single-slot winner (last loaded) stays."""
        executor = self._executor(_espn_twins())
        found = executor._find_channel_by_name(
            "ESPN", scope_group_id=None, block_manual=False)
        assert found and found["id"] == 248009

    def test_scope_without_matching_twin_returns_none(self):
        executor = self._executor(_espn_twins())
        found = executor._find_channel_by_name(
            "ESPN", scope_group_id=9999, block_manual=False)
        assert found is None

    def test_manual_only_in_scope_twin_still_blocked(self):
        """The orzck manual gate keeps applying to scanned candidates."""
        channels = [
            _channel(1, "ESPN", 10),
            _channel(2, "ESPN", 20, auto_created=False),
        ]
        executor = self._executor(channels)
        found = executor._find_channel_by_name(
            "ESPN", scope_group_id=20, block_manual=True)
        assert found is None
        # wy6l5 breadcrumb: the blocked manual twin is remembered so callers
        # can journal WHY the lookup ended in None.
        assert executor._last_manual_block is not None
        assert executor._last_manual_block["id"] == 2

    def test_manual_twin_found_when_rule_opts_in(self):
        channels = [
            _channel(1, "ESPN", 10),
            _channel(2, "ESPN", 20, auto_created=False),
        ]
        executor = self._executor(channels)
        found = executor._find_channel_by_name(
            "ESPN", scope_group_id=20, block_manual=False)
        assert found and found["id"] == 2

    def test_auto_twin_preferred_over_blocked_manual_twin_in_scope(self):
        """Legacy pick manual + blocked → scan returns the in-scope auto twin."""
        channels = [
            _channel(1, "ESPN", 10),                      # auto, first-loaded
            _channel(2, "ESPN", 10, auto_created=False),  # manual, wins legacy last-wins slot
        ]
        executor = self._executor(channels)
        found = executor._find_channel_by_name(
            "ESPN", scope_group_id=10, block_manual=True)
        assert found and found["id"] == 1

    def test_base_name_scoped_collision(self):
        """Number-prefixed twins: base-name stage scans candidates too."""
        channels = [
            _channel(1, "100 | ESPN", 10),
            _channel(2, "200 | ESPN", 20),
        ]
        executor = self._executor(channels)
        found = executor._find_channel_by_name(
            "ESPN", scope_group_id=20, block_manual=False)
        assert found and found["id"] == 2

    def test_fold_key_scoped_collision(self):
        """Opt-in fold stage (GH #645) scans candidates as well."""
        channels = [
            _channel(1, "Euro Sport", 10),
            _channel(2, "Euro Sport", 20),
        ]
        executor = self._executor(channels)
        found = executor._find_channel_by_name(
            "EuroSport", scope_group_id=20, block_manual=False, fold_key=True)
        assert found and found["id"] == 2


class TestMergeActionsWithDuplicateNames:
    """End-to-end create_channel actions over the duplicate-name layout."""

    def setup_method(self):
        self.client = MagicMock()
        self.groups = [
            {"id": 5210, "name": "ESPN"},
            {"id": 4454, "name": "US SPORTS"},
        ]

    def _run(self, executor, if_exists, group_id, stream_id=900):
        action = {
            "type": "create_channel",
            "name_template": "ESPN",
            "if_exists": if_exists,
            "group_id": group_id,
        }
        stream_ctx = StreamContext(
            stream_id=stream_id,
            stream_name="US: ESPN HD",
            m3u_account_id=1,
        )
        exec_ctx = ExecutionContext(dry_run=True)
        with patch("channel_pipeline_executor.journal.log_entries"):
            result = asyncio.get_event_loop().run_until_complete(
                executor.execute(action, stream_ctx, exec_ctx,
                                 match_scope_target_group=True)
            )
        return result, exec_ctx

    def test_merge_only_merges_instead_of_false_not_found(self):
        """merge_only must merge into the in-scope twin, not skip."""
        executor = ActionExecutor(
            self.client, existing_channels=_espn_twins(),
            existing_groups=self.groups)
        result, exec_ctx = self._run(executor, "merge_only", 5210)
        assert result.success is True
        assert "not found" not in result.description, (
            f"merge_only false 'not found' (g0uuf): {result.description!r}")
        assert exec_ctx.merged_channel_ids == {236818}

    def test_merge_does_not_create_duplicate(self):
        """merge (create if new) must merge, not create a same-name twin."""
        executor = ActionExecutor(
            self.client, existing_channels=_espn_twins(),
            existing_groups=self.groups)
        result, exec_ctx = self._run(executor, "merge", 5210)
        assert result.success is True
        assert result.created is False, (
            "merge created a duplicate channel although an exact-name match "
            "exists in the scoped group (g0uuf regression)")
        assert exec_ctx.merged_channel_ids == {236818}

    def test_merge_only_still_skips_when_scope_truly_empty(self):
        """No twin in the scoped group → merge_only keeps skipping."""
        executor = ActionExecutor(
            self.client, existing_channels=_espn_twins(),
            existing_groups=self.groups + [{"id": 7777, "name": "EMPTY"}])
        result, exec_ctx = self._run(executor, "merge_only", 7777)
        assert result.success is True
        assert result.skipped is True
        assert "not found" in result.description
        assert exec_ctx.merged_channel_ids == set()
