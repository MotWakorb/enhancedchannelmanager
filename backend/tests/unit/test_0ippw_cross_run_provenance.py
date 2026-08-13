"""GH #801 / bead 0ippw - provenance must survive the run boundary.

The defect: ``_execute_create_channel`` stamps ``auto_created = True`` on the
in-memory channel dict only. The create payload never carries it and
Dispatcharr never stores it, so the next run reloads every ECM-created channel
WITHOUT the key. ``_is_manual_channel`` reads a missing key as MANUAL, the
``block_manual`` gate rejects the rule's own channels, and the rule re-creates
its whole set every run while the orphan pass deletes the previous one.

THE POINT OF THIS FILE is the run boundary. Every fixture here reloads the
channel exactly as the real Dispatcharr API returns it: the ``auto_created``
key is DROPPED. The existing GH #845 tests hardcode ``auto_created: True`` into
their fixtures, which simulates a within-run world and therefore cannot catch
this. Do not copy that pattern into this file.

The persisted ``managed_channel_ids`` ledger is the provenance source: it
already exists, already survives runs, and the engine already writes it. These
tests assert that a channel in the ledger is auto-created for lookup purposes
even with the marker gone, and that a channel NOT in the ledger is still
protected.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from channel_pipeline_executor import (
    ActionExecutor,
    ExecutionContext,
    StreamContext,
)


TARGET_GROUP_ID = 5
CHANNEL_NAME = "ESPN"


def _stream(stream_id: int, name: str = CHANNEL_NAME) -> StreamContext:
    return StreamContext(
        stream_id=stream_id,
        stream_name=name,
        m3u_account_id=1,
        m3u_account_name="Provider",
        group_name="Sports",
        tvg_id=None,
    )


def _client(create_id: int = 101):
    """Dispatcharr client stub whose create response omits auto_created.

    The real API accepts the create payload, ignores any provenance ECM would
    like to record, and echoes the stored row back. Nothing in that row says
    the channel was auto-created.
    """
    client = MagicMock()
    client.update_channel = AsyncMock(return_value={})

    async def create_channel(payload):
        assert "auto_created" not in payload, (
            "Dispatcharr has no auto_created field on create; a test that sends "
            "one is not reproducing the real API."
        )
        return {
            "id": create_id,
            "name": payload["name"],
            "channel_group_id": payload.get("channel_group_id"),
            "streams": list(payload.get("streams") or []),
        }

    client.create_channel = AsyncMock(side_effect=create_channel)
    return client


def _reloaded_from_dispatcharr(channel_id: int, name: str = CHANNEL_NAME) -> dict:
    """A channel as the NEXT run loads it: no auto_created key at all.

    This is the whole boundary. The reporter confirmed it at the API level:
    fetching a just-created channel returns auto_created False.
    """
    return {
        "id": channel_id,
        "name": name,
        "channel_group_id": TARGET_GROUP_ID,
        "streams": [],
    }


def _create_action(if_exists: str = "merge") -> dict:
    return {
        "type": "create_channel",
        "name_template": "{stream_name}",
        "if_exists": if_exists,
        "group_id": TARGET_GROUP_ID,
    }


async def _run_create(executor, stream_id: int, if_exists: str = "merge"):
    return await executor.execute(
        _create_action(if_exists),
        _stream(stream_id),
        ExecutionContext(),
        rule_target_group_id=TARGET_GROUP_ID,
        match_scope_target_group=True,
        rule_id=1,
    )


class TestCreateChannelAcrossRunBoundary:
    """_execute_create_channel's _find_channel_by_name path."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("if_exists", ["merge", "skip"])
    async def test_second_run_reuses_the_channel_the_first_run_created(self, if_exists):
        run1_client = _client(create_id=101)
        run1 = ActionExecutor(run1_client, existing_channels=[])
        first = await _run_create(run1, stream_id=900, if_exists=if_exists)
        assert first.created is True
        assert first.entity_id == 101

        # The engine persists the created ids into the rule's ledger; the next
        # run reloads the channel from Dispatcharr with the marker gone.
        run2_client = _client(create_id=102)
        run2 = ActionExecutor(
            run2_client,
            existing_channels=[_reloaded_from_dispatcharr(101)],
            managed_channel_ids={101},
        )
        second = await _run_create(run2, stream_id=901, if_exists=if_exists)

        assert second.created is False, (
            "Run 2 created a duplicate: the rule cannot see the channel it "
            "created in run 1."
        )
        assert second.entity_id == 101, "Channel IDs must be stable across runs."
        run2_client.create_channel.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_second_run_merges_the_new_stream_into_the_same_channel(self):
        run2_client = _client(create_id=102)
        run2 = ActionExecutor(
            run2_client,
            existing_channels=[_reloaded_from_dispatcharr(101)],
            managed_channel_ids={101},
        )
        result = await _run_create(run2, stream_id=901, if_exists="merge")

        assert result.success is True
        run2_client.update_channel.assert_awaited_once_with(101, {"streams": [901]})

    @pytest.mark.asyncio
    async def test_hand_built_channel_is_still_protected(self):
        """A channel the ledger does not know is genuinely manual, so the
        block_manual gate must still reject it and create a new auto channel.
        This is the orzck protection, and it must survive the fix."""
        client = _client(create_id=202)
        executor = ActionExecutor(
            client,
            existing_channels=[_reloaded_from_dispatcharr(77)],
            managed_channel_ids=set(),
        )
        result = await _run_create(executor, stream_id=901, if_exists="merge")

        assert result.created is True
        assert result.entity_id == 202
        client.create_channel.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ledger_entry_for_another_channel_does_not_unprotect_this_one(self):
        """The ledger is matched by channel id, not by name."""
        client = _client(create_id=202)
        executor = ActionExecutor(
            client,
            existing_channels=[_reloaded_from_dispatcharr(77)],
            managed_channel_ids={101, 102},
        )
        result = await _run_create(executor, stream_id=901, if_exists="merge")

        assert result.created is True
        assert result.entity_id == 202

    @pytest.mark.asyncio
    async def test_explicit_marker_still_wins_when_present(self):
        """A channel carrying auto_created stays auto-created even with an
        empty ledger: the ledger widens provenance, it does not replace it."""
        client = _client(create_id=202)
        marked = dict(_reloaded_from_dispatcharr(101), auto_created=True)
        executor = ActionExecutor(
            client, existing_channels=[marked], managed_channel_ids=set(),
        )
        result = await _run_create(executor, stream_id=901, if_exists="merge")

        assert result.created is False
        assert result.entity_id == 101


class TestMergeStreamsAcrossRunBoundary:
    """_find_unique_channel_by_exact_identity's path (bead ukgj9 / GH #845).

    That helper filters candidates with the same never-persisted marker, so it
    inherited this defect rather than avoiding it.
    """

    @staticmethod
    def _normalizer():
        engine = MagicMock()

        def normalize(value, group_ids=None):
            result = MagicMock()
            result.normalized = CHANNEL_NAME
            return result

        engine.normalize.side_effect = normalize
        engine.extract_core_name.return_value = CHANNEL_NAME
        engine.extract_call_sign.return_value = None
        return engine

    async def _merge(self, executor, stream_id: int):
        return await executor.execute(
            {"type": "merge_streams", "target": "auto"},
            _stream(stream_id, name="Provider ESPN"),
            ExecutionContext(),
            normalization_group_ids=[41],
            match_scope_target_group=True,
            rule_scope_group_id=TARGET_GROUP_ID,
            rule_id=1,
        )

    @pytest.mark.asyncio
    async def test_second_run_merges_into_the_rules_own_channel(self):
        client = _client()
        executor = ActionExecutor(
            client,
            existing_channels=[_reloaded_from_dispatcharr(101)],
            normalization_engine=self._normalizer(),
            managed_channel_ids={101},
        )
        result = await self._merge(executor, stream_id=901)

        assert result.success is True
        assert result.skipped is False
        client.update_channel.assert_awaited_once_with(101, {"streams": [901]})

    @pytest.mark.asyncio
    async def test_hand_built_channel_is_still_protected(self):
        client = _client()
        executor = ActionExecutor(
            client,
            existing_channels=[_reloaded_from_dispatcharr(77)],
            normalization_engine=self._normalizer(),
            managed_channel_ids=set(),
        )
        result = await self._merge(executor, stream_id=901)

        assert result.success is False or result.skipped is True
        client.update_channel.assert_not_awaited()


class TestEngineSuppliesTheLedger:
    """The wiring: without it the executor's provenance set is always empty in
    production and the two-run fix above never reaches a real run."""

    @staticmethod
    def _rule(rule_id: int, managed_channel_ids: list[int]):
        rule = MagicMock()
        rule.id = rule_id
        rule.name = f"Rule {rule_id}"
        rule.priority = 0
        rule.m3u_account_id = None
        rule.target_group_id = None
        rule.enabled = True
        rule.stop_on_first_match = True
        rule.skip_struck_streams = False
        rule.sort_field = None
        rule.sort_order = "asc"
        rule.sort_regex = None
        rule.starting_channel_number = None
        rule.orphan_action = "none"
        rule.managed_channel_ids = "[]"
        rule.get_managed_channel_ids.return_value = list(managed_channel_ids)
        rule.get_conditions.return_value = [{"type": "always"}]
        rule.get_actions.return_value = [{"type": "create_channel", "params": {}}]
        rule.get_normalization_group_ids.return_value = []
        rule.match_scope_target_group = False
        rule.is_event_sync.return_value = False
        return rule

    def test_executor_receives_the_union_of_every_rules_ledger(self):
        import asyncio
        from unittest.mock import patch

        from channel_pipeline_engine import ChannelPipelineEngine

        client = MagicMock()
        client.assign_channel_numbers = AsyncMock()
        client.get_channels = AsyncMock(return_value={"count": 0, "results": []})
        engine = ChannelPipelineEngine(client)
        engine._existing_channels = []
        engine._existing_groups = []

        execution = MagicMock()
        execution.id = 1

        with patch("channel_pipeline_engine.get_session"), \
                patch("channel_pipeline_engine.ActionExecutor") as executor_cls:
            executor = MagicMock()
            executor.execute = AsyncMock()
            executor.verify_epg_assignments = AsyncMock(return_value=(0, 0, 0))
            executor.prune_merge_streams = AsyncMock()
            executor.reorder_streams_on_channels = AsyncMock(return_value=0)
            executor._channel_by_id = {}
            executor._created_channels = {}
            executor_cls.return_value = executor

            engine._refresh_dummy_epg_and_retry = AsyncMock()
            engine._reconcile_orphans = AsyncMock()
            engine._update_rule_stats = AsyncMock()

            asyncio.get_event_loop().run_until_complete(
                engine._process_streams(
                    [],
                    [self._rule(1, [101, 102]), self._rule(2, [203])],
                    execution,
                    dry_run=False,
                )
            )

        assert executor_cls.call_args.kwargs["managed_channel_ids"] == {101, 102, 203}


class TestManagedChannelIdsDefaults:
    """Direct-construct callers (and every existing test) must be unaffected."""

    def test_omitting_the_ledger_keeps_marker_only_semantics(self):
        executor = ActionExecutor(MagicMock(), existing_channels=[])
        assert executor._is_manual_channel({"id": 1}) is True
        assert executor._is_manual_channel({"id": 1, "auto_created": True}) is False

    def test_ledger_membership_makes_a_channel_auto_created(self):
        executor = ActionExecutor(
            MagicMock(), existing_channels=[], managed_channel_ids={1},
        )
        assert executor._is_manual_channel({"id": 1}) is False
        assert executor._is_manual_channel({"id": 2}) is True

    def test_none_channel_is_not_manual(self):
        executor = ActionExecutor(MagicMock(), existing_channels=[])
        assert executor._is_manual_channel(None) is False
