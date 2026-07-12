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

from unittest.mock import AsyncMock

import pytest

from services.event_sync_preflight import (
    CHECK_GROUP_SETTINGS_FOUND,
    CHECK_MASTER_AUTO_SYNC_ON,
    CHECK_SECONDARY_AUTO_SYNC_OFF,
    check_event_sync_group_settings,
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
        assert result == {"ok": True, "failures": []}

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
        assert result == {"ok": True, "failures": []}

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
        assert result == {"ok": True, "failures": []}

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
        assert result == {"ok": True, "failures": []}

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
