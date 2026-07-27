"""Unit tests for the Event Sync pre-flight check (bead ti939.1.3).

``check_event_sync_group_settings`` verifies against MOCKED Dispatcharr
group settings that the master group has auto_channel_sync ON and every
secondary has it OFF, and that a disabled master surfaces as an explicit
failure (otherwise it is a silent whole-feature failure — no master
channels ever exist).

The helper is READ-ONLY by contract: the client mock exposes ONLY
``get_all_m3u_group_settings`` (spec-pinned), so any write/toggle attempt
raises AttributeError and fails the test.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.event_sync_preflight import (
    CHECK_GROUP_SETTINGS_FOUND,
    CHECK_MASTER_AUTO_SYNC_ON,
    CHECK_SECONDARY_AUTO_SYNC_OFF,
    CHECK_STALENESS_RAIL_SNAPSHOTS,
    build_event_sync_master_rule_lookup,
    build_staleness_rail_warning,
    check_event_sync_group_settings,
    count_snapshot_covered_streams,
    resolve_effective_master_group_id,
)


class _ReadOnlyClient:
    """Client double exposing ONLY the read method the helper may use.

    Any other attribute access (e.g. update_m3u_group_settings) raises
    AttributeError — pinning the helper's never-writes contract.
    """

    def __init__(self, settings_by_group_id: dict):
        self.get_all_m3u_group_settings = AsyncMock(
            return_value=settings_by_group_id
        )


def _group(auto_sync: bool) -> dict:
    return {"enabled": True, "auto_channel_sync": auto_sync}


def _config(master=10, secondaries=(20, 30)) -> dict:
    return {"master_group_id": master, "secondary_group_ids": list(secondaries)}


class TestPreflightPasses:
    async def test_master_on_secondaries_off_passes(self):
        client = _ReadOnlyClient({
            10: _group(auto_sync=True),
            20: _group(auto_sync=False),
            30: _group(auto_sync=False),
        })
        result = await check_event_sync_group_settings(client, _config())
        assert result == {"ok": True, "failures": [], "warnings": []}

    async def test_only_the_read_method_is_called(self):
        client = _ReadOnlyClient({
            10: _group(auto_sync=True),
            20: _group(auto_sync=False),
            30: _group(auto_sync=False),
        })
        await check_event_sync_group_settings(client, _config())
        client.get_all_m3u_group_settings.assert_awaited_once_with()


class TestPreflightFailures:
    async def test_master_auto_sync_off_fails(self):
        """A disabled master auto-sync is otherwise a silent feature failure."""
        client = _ReadOnlyClient({
            10: _group(auto_sync=False),
            20: _group(auto_sync=False),
            30: _group(auto_sync=False),
        })
        result = await check_event_sync_group_settings(client, _config())
        assert result["ok"] is False
        assert len(result["failures"]) == 1
        failure = result["failures"][0]
        assert failure["group_id"] == 10
        assert failure["role"] == "master"
        assert failure["check"] == CHECK_MASTER_AUTO_SYNC_ON
        assert failure["expected"] == "auto_channel_sync ON"
        assert failure["got"] == "auto_channel_sync OFF"
        assert "never toggles" in failure["message"]

    async def test_secondary_auto_sync_on_fails(self):
        client = _ReadOnlyClient({
            10: _group(auto_sync=True),
            20: _group(auto_sync=True),   # misconfigured
            30: _group(auto_sync=False),
        })
        result = await check_event_sync_group_settings(client, _config())
        assert result["ok"] is False
        assert len(result["failures"]) == 1
        failure = result["failures"][0]
        assert failure["group_id"] == 20
        assert failure["role"] == "secondary"
        assert failure["check"] == CHECK_SECONDARY_AUTO_SYNC_OFF

    async def test_missing_master_group_fails(self):
        client = _ReadOnlyClient({
            20: _group(auto_sync=False),
            30: _group(auto_sync=False),
        })
        result = await check_event_sync_group_settings(client, _config())
        assert result["ok"] is False
        failure = result["failures"][0]
        assert failure["group_id"] == 10
        assert failure["role"] == "master"
        assert failure["check"] == CHECK_GROUP_SETTINGS_FOUND

    async def test_missing_secondary_group_fails(self):
        client = _ReadOnlyClient({
            10: _group(auto_sync=True),
            20: _group(auto_sync=False),
        })
        result = await check_event_sync_group_settings(client, _config())
        assert result["ok"] is False
        failures = result["failures"]
        assert len(failures) == 1
        assert failures[0]["group_id"] == 30
        assert failures[0]["role"] == "secondary"
        assert failures[0]["check"] == CHECK_GROUP_SETTINGS_FOUND

    async def test_multiple_failures_all_surface(self):
        """Every failing group is reported — not just the first."""
        client = _ReadOnlyClient({
            10: _group(auto_sync=False),  # master OFF
            20: _group(auto_sync=True),   # secondary ON
            # 30 missing entirely
        })
        result = await check_event_sync_group_settings(client, _config())
        assert result["ok"] is False
        checks = {(f["group_id"], f["check"]) for f in result["failures"]}
        assert checks == {
            (10, CHECK_MASTER_AUTO_SYNC_ON),
            (20, CHECK_SECONDARY_AUTO_SYNC_OFF),
            (30, CHECK_GROUP_SETTINGS_FOUND),
        }

    async def test_never_writes_group_settings(self):
        """The read-only contract: no write method exists on the double, and
        the helper completes without needing one even when every check fails."""
        client = _ReadOnlyClient({})
        result = await check_event_sync_group_settings(client, _config())
        assert result["ok"] is False
        assert not hasattr(client, "update_m3u_group_settings")


def _source(auto_sync: bool, target: int) -> dict:
    """An auto-synced SOURCE group whose channels are placed in ``target``."""
    return {
        "enabled": True,
        "auto_channel_sync": auto_sync,
        "custom_properties": {"group_override": target},
    }


class TestChannelGroupOverride:
    """Channel Group Override resolution (bead override).

    Auto-created channels land in the override TARGET group while the
    auto_channel_sync setting lives on the SOURCE group; the pre-flight and
    the master fetch must follow that relationship.
    """

    def test_effective_id_follows_source_to_target(self):
        # Source 95 overrides to target 420 -> master channels live in 420.
        settings = {95: _source(auto_sync=True, target=420)}
        assert resolve_effective_master_group_id(settings, 95) == 420

    def test_effective_id_is_identity_without_override(self):
        settings = {10: _group(auto_sync=True)}
        assert resolve_effective_master_group_id(settings, 10) == 10

    def test_effective_id_of_target_is_itself(self):
        # Picking the target directly: it has no override of its own.
        settings = {95: _source(auto_sync=True, target=420)}
        assert resolve_effective_master_group_id(settings, 420) == 420

    async def test_master_as_override_target_passes(self):
        # Operator points master at the TARGET (420) which has no direct
        # setting; auto-sync ON is read through the source (95).
        client = _ReadOnlyClient({
            95: _source(auto_sync=True, target=420),
            20: _group(auto_sync=False),
        })
        result = await check_event_sync_group_settings(
            client, _config(master=420, secondaries=(20,))
        )
        assert result == {"ok": True, "failures": [], "warnings": []}

    async def test_master_target_with_source_auto_sync_off_fails(self):
        client = _ReadOnlyClient({
            95: _source(auto_sync=False, target=420),
            20: _group(auto_sync=False),
        })
        result = await check_event_sync_group_settings(
            client, _config(master=420, secondaries=(20,))
        )
        assert result["ok"] is False
        assert any(f["check"] == CHECK_MASTER_AUTO_SYNC_ON
                   for f in result["failures"])

    async def test_master_as_override_source_still_passes(self):
        # Operator points master at the auto-synced SOURCE (95) — the natural
        # pick. Pre-flight passes (auto-sync ON); the master FETCH follows the
        # override to 420 (covered by resolve_effective_master_group_id).
        client = _ReadOnlyClient({
            95: _source(auto_sync=True, target=420),
            20: _group(auto_sync=False),
        })
        result = await check_event_sync_group_settings(
            client, _config(master=95, secondaries=(20,))
        )
        assert result == {"ok": True, "failures": [], "warnings": []}

    async def test_prefetched_settings_avoid_a_second_fetch(self):
        settings = {
            95: _source(auto_sync=True, target=420),
            20: _group(auto_sync=False),
        }
        client = _ReadOnlyClient(settings)
        await check_event_sync_group_settings(
            client, _config(master=420, secondaries=(20,)),
            all_settings=settings,
        )
        client.get_all_m3u_group_settings.assert_not_awaited()


class _ProviderScopedClient:
    """Client double exposing BOTH the collapsed and the per-(provider, group)
    read methods — for provider-scoped pre-flight (bead jiscc)."""

    def __init__(self, collapsed: dict, by_provider: dict):
        self.get_all_m3u_group_settings = AsyncMock(return_value=collapsed)
        self.get_m3u_group_settings_by_provider = AsyncMock(
            return_value=by_provider
        )


class TestProviderScopedPreflight:
    """bead jiscc: on a SHARED group, the pre-flight must check the SPECIFIC
    provider's junction row, not the collapsed (auto-sync-ON-preferring) view.
    """

    def _shared_group_config(self):
        # Master = group 10 / provider 3; secondary = group 10 / provider 7.
        return {
            "master": {"group_id": 10, "m3u_account_id": 3},
            "secondary": [{"group_id": 10, "m3u_account_id": 7}],
        }

    async def test_shared_group_diff_providers_passes(self):
        # Provider 3's group-10 row is ON (master), provider 7's is OFF
        # (secondary) — valid, even though the COLLAPSED group 10 reads ON.
        client = _ProviderScopedClient(
            collapsed={10: _group(auto_sync=True)},
            by_provider={
                (3, 10): _group(auto_sync=True),
                (7, 10): _group(auto_sync=False),
            },
        )
        result = await check_event_sync_group_settings(
            client, self._shared_group_config()
        )
        assert result == {"ok": True, "failures": [], "warnings": []}

    async def test_secondary_providers_row_auto_sync_on_fails(self):
        client = _ProviderScopedClient(
            collapsed={10: _group(auto_sync=True)},
            by_provider={
                (3, 10): _group(auto_sync=True),
                (7, 10): _group(auto_sync=True),  # secondary provider ON -> bad
            },
        )
        result = await check_event_sync_group_settings(
            client, self._shared_group_config()
        )
        assert result["ok"] is False
        assert any(f["check"] == CHECK_SECONDARY_AUTO_SYNC_OFF
                   for f in result["failures"])

    async def test_master_provider_not_carrying_group_fails(self):
        client = _ProviderScopedClient(
            collapsed={10: _group(auto_sync=True)},
            by_provider={(7, 10): _group(auto_sync=False)},  # no (3,10) row
        )
        result = await check_event_sync_group_settings(
            client, self._shared_group_config()
        )
        assert result["ok"] is False
        assert any(f["check"] == CHECK_GROUP_SETTINGS_FOUND
                   for f in result["failures"])

    async def test_whole_group_scope_does_not_fetch_by_provider(self):
        # A null-provider (whole-group) config must NOT call the per-provider
        # read — the collapsed path is preserved for legacy configs.
        client = _ProviderScopedClient(
            collapsed={10: _group(True), 20: _group(False)},
            by_provider={},
        )
        await check_event_sync_group_settings(
            client, _config(master=10, secondaries=(20,))
        )
        client.get_m3u_group_settings_by_provider.assert_not_awaited()


# ---------------------------------------------------------------------------
# Cross-rule conflict context (bead yjchp): a failing secondary group that is
# ANOTHER enabled event_sync rule's MASTER must not get "disable auto-sync"
# advice — masters REQUIRE auto_channel_sync ON.
# ---------------------------------------------------------------------------


class _FakeRule:
    """ChannelPipelineRule shape for build_event_sync_master_rule_lookup."""

    def __init__(self, rule_id, name, config, event_sync=True):
        self.id = rule_id
        self.name = name
        self._config = config
        self._event_sync = event_sync

    def is_event_sync(self):
        return self._event_sync

    def get_event_sync_config(self):
        return self._config


class TestBuildMasterRuleLookup:
    def test_maps_enabled_event_sync_masters(self):
        rules = [
            _FakeRule(1, "PPV", {"master_group_id": 356}),
            _FakeRule(2, "Dirtvision", {"master_group_id": 42}),
        ]
        assert build_event_sync_master_rule_lookup(rules) == {
            356: "PPV", 42: "Dirtvision",
        }

    def test_excludes_the_rule_under_check(self):
        rules = [
            _FakeRule(1, "PPV", {"master_group_id": 356}),
            _FakeRule(2, "Dirtvision", {"master_group_id": 42}),
        ]
        lookup = build_event_sync_master_rule_lookup(rules, exclude_rule_id=2)
        assert lookup == {356: "PPV"}

    def test_skips_disabled_configs_and_non_event_sync_rules(self):
        rules = [
            _FakeRule(1, "Disabled", {"master_group_id": 1, "enabled": False}),
            _FakeRule(2, "NotEventSync", None, event_sync=False),
            _FakeRule(3, "NoConfig", None),
        ]
        assert build_event_sync_master_rule_lookup(rules) == {}

    def test_reads_nested_master_scope_shape(self):
        # bead jiscc provider-scoped shape: master group id may live in the
        # nested "master" scope instead of the flat key.
        rules = [_FakeRule(
            1, "Scoped", {"master": {"group_id": 77, "m3u_account_id": 3}},
        )]
        assert build_event_sync_master_rule_lookup(rules) == {77: "Scoped"}


class TestCrossRuleSecondaryConflict:
    async def test_conflicting_master_gets_tailored_message(self):
        # The live shape (user debug bundle): rule "Dirtvision" lists group
        # 356 as a secondary, but 356 is rule "PPV"'s MASTER (auto-sync ON
        # by requirement). The stock advice would break PPV.
        client = _ReadOnlyClient({
            10: _group(auto_sync=True),
            356: _group(auto_sync=True),  # PPV's master, our secondary
        })
        result = await check_event_sync_group_settings(
            client, _config(master=10, secondaries=(356,)),
            other_master_rules={356: "PPV"},
        )
        assert result["ok"] is False
        (failure,) = result["failures"]
        # Machine-readable check id is UNCHANGED (API contract).
        assert failure["check"] == CHECK_SECONDARY_AUTO_SYNC_OFF
        assert failure["conflicting_rule"] == "PPV"
        assert "'PPV'" in failure["message"]
        assert "Do NOT disable auto_channel_sync" in failure["message"]
        assert "remove this group from this rule's secondary groups" \
            in failure["message"]
        # The stock "disable it" advice must be gone.
        assert "Disable auto_channel_sync for it" not in failure["message"]

    async def test_non_conflicting_group_keeps_generic_message(self):
        client = _ReadOnlyClient({
            10: _group(auto_sync=True),
            20: _group(auto_sync=True),  # plain misconfiguration
        })
        result = await check_event_sync_group_settings(
            client, _config(master=10, secondaries=(20,)),
            other_master_rules={356: "PPV"},  # different group
        )
        (failure,) = result["failures"]
        assert failure["check"] == CHECK_SECONDARY_AUTO_SYNC_OFF
        assert "conflicting_rule" not in failure
        assert "Disable auto_channel_sync for it in Dispatcharr" \
            in failure["message"]

    async def test_omitted_lookup_preserves_prior_shape(self):
        # Backward compatibility: callers that never pass the lookup get the
        # exact pre-yjchp failure dict (no conflicting_rule key).
        client = _ReadOnlyClient({
            10: _group(auto_sync=True),
            20: _group(auto_sync=True),
        })
        result = await check_event_sync_group_settings(
            client, _config(master=10, secondaries=(20,))
        )
        (failure,) = result["failures"]
        assert "conflicting_rule" not in failure
        assert set(failure) == {
            "group_id", "role", "check", "expected", "got", "message",
        }


class TestStalenessRailWarning:
    """bead 2ey2y: the inert-rail warning — assume_current_date +
    demote_stale_dateless with ZERO snapshot coverage must surface as an
    explicit pre-flight WARNING; every other combination stays silent."""

    def _rail_config(self, **overrides) -> dict:
        config = {"assume_current_date": True}
        config.update(overrides)
        return config

    def test_fires_when_rail_on_and_nothing_covered(self):
        warning = build_staleness_rail_warning(
            self._rail_config(),
            secondary_stream_count=7,
            snapshot_covered_count=0,
        )
        assert warning is not None
        assert warning["check"] == CHECK_STALENESS_RAIL_SNAPSHOTS
        assert "7 secondary stream(s)" in warning["got"]
        assert "fails open" in warning["message"]
        # Rule-level entry: teaching shape WITHOUT the per-group fields.
        assert set(warning) == {"check", "expected", "got", "message"}

    def test_absent_demote_key_reads_true_and_fires(self):
        # demote_stale_dateless defaults ON (absent-key default-fill, jqwfq)
        # — a stored config without the key still relies on the rail.
        config = self._rail_config()
        assert "demote_stale_dateless" not in config
        assert build_staleness_rail_warning(
            config, secondary_stream_count=1, snapshot_covered_count=0,
        ) is not None

    def test_silent_when_assume_current_date_off(self):
        assert build_staleness_rail_warning(
            {"demote_stale_dateless": True},
            secondary_stream_count=7,
            snapshot_covered_count=0,
        ) is None

    def test_silent_when_demote_rail_disabled(self):
        assert build_staleness_rail_warning(
            self._rail_config(demote_stale_dateless=False),
            secondary_stream_count=7,
            snapshot_covered_count=0,
        ) is None

    def test_silent_when_any_stream_is_covered(self):
        assert build_staleness_rail_warning(
            self._rail_config(),
            secondary_stream_count=7,
            snapshot_covered_count=1,
        ) is None

    def test_silent_when_no_streams_fetched(self):
        # An empty fetch has louder problems than rail coverage — the
        # preview already screams "no secondary streams found".
        assert build_staleness_rail_warning(
            self._rail_config(),
            secondary_stream_count=0,
            snapshot_covered_count=0,
        ) is None


class TestCountSnapshotCoveredStreams:
    """bead 2ey2y: coverage = the stream's (provider, group NAME) pair is
    present in the previous-day lookup. Coverage is not a staleness verdict
    — it means the rail HAS data for that stream."""

    def _resolved(self, provider_id, group_id):
        stream = SimpleNamespace(provider_id=provider_id, group_id=group_id)
        return SimpleNamespace(stream=stream)

    def test_counts_only_streams_whose_group_is_captured(self):
        lookup = {1: {"Fubo Events": frozenset({"A", "B"})}}
        group_names = {34: "Fubo Events", 56: "Dazn Events"}
        resolved = [
            self._resolved(1, 34),   # covered: account 1 captured the group
            self._resolved(1, 56),   # not covered: group absent from lookup
            self._resolved(2, 34),   # not covered: account 2 has no snapshot
            self._resolved(None, 34),  # not covered: unknown provider
        ]
        assert count_snapshot_covered_streams(
            resolved, group_names, lookup) == 1

    def test_unresolvable_group_name_is_uncovered(self):
        lookup = {1: {"Fubo Events": frozenset({"A"})}}
        resolved = [self._resolved(1, 99)]  # 99 missing from group_names
        assert count_snapshot_covered_streams(resolved, {}, lookup) == 0

    def test_empty_lookup_covers_nothing(self):
        resolved = [self._resolved(1, 34), self._resolved(2, 34)]
        assert count_snapshot_covered_streams(
            resolved, {34: "Fubo Events"}, {}) == 0
