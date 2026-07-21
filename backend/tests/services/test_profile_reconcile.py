"""Unit + integration tests for group-level profile reconciliation.

GH #720 Part B / bead enhancedchannelmanager-y3m6o. Verifies that
``services.profile_reconcile`` applies a group's stored
``channel_profile_ids`` selection SUBTRACTIVELY to that group's channels via
one bulk profile update per profile (O(P), not O(N*P)), honoring the locked
PO decisions:

* 1(a) absent/empty selection -> read-only NO-OP;
* 2(b) pipeline-owned channels excluded from the reconcile;
* Channel Group Override redirection resolved before enumeration.
"""
from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock

import pytest

from services.profile_reconcile import (
    PIPELINE_OWNERSHIP_MARKER_KEY,
    PIPELINE_OWNERSHIP_MARKER_VALUE,
    groups_with_selection,
    reconcile_all_selected_groups,
    reconcile_group_profiles,
)

_FIXTURE = os.path.join(
    os.path.dirname(__file__),
    "..",
    "fixtures",
    "bd_igqcy",
    "dispatcharr_v0272_m3u_account.json",
)


def _channel(cid: int, *, group: int = 100, owned: bool = False, extra_cp=None) -> dict:
    cp = dict(extra_cp or {})
    if owned:
        cp[PIPELINE_OWNERSHIP_MARKER_KEY] = PIPELINE_OWNERSHIP_MARKER_VALUE
    return {"id": cid, "channel_group": group, "custom_properties": cp}


def _setting(*, channel_profile_ids=None, group_override=None) -> dict:
    cp: dict = {}
    if channel_profile_ids is not None:
        cp["channel_profile_ids"] = channel_profile_ids
    if group_override is not None:
        cp["group_override"] = group_override
    return {"auto_channel_sync": True, "custom_properties": cp}


class FakeClient:
    """Minimal Dispatcharr client double for the reconcile.

    ``channels_by_gid`` maps effective group id -> list of channel dicts;
    ``get_channels`` paginates over that list. ``profiles`` is the universe.
    ``bulk_update_profile_channels`` records ``(profile_id, channel_ids,
    enabled)`` tuples. A profile id in ``fail_profiles`` raises to exercise the
    stale/deleted-id skip path.
    """

    def __init__(self, channels_by_gid, profiles, *, page_size=100, fail_profiles=None):
        self.channels_by_gid = channels_by_gid
        self.profiles = profiles
        self.page_size = page_size
        self.fail_profiles = set(fail_profiles or [])
        self.bulk_calls: list[tuple[int, tuple, bool]] = []
        self.get_channels_gids: list[int] = []

    async def get_channels(self, page=1, page_size=100, search=None, channel_group=None):
        self.get_channels_gids.append(channel_group)
        rows = list(self.channels_by_gid.get(channel_group, []))
        # Paginate deterministically using this client's configured page size.
        start = (page - 1) * self.page_size
        chunk = rows[start:start + self.page_size]
        has_next = start + self.page_size < len(rows)
        return {
            "count": len(rows),
            "next": "http://next" if has_next else None,
            "previous": None,
            "results": chunk,
        }

    async def get_channel_profiles(self):
        return [{"id": pid, "name": f"P{pid}"} for pid in self.profiles]

    async def bulk_update_profile_channels(self, profile_id, data):
        if profile_id in self.fail_profiles:
            raise RuntimeError(f"profile {profile_id} is stale/deleted")
        self.bulk_calls.append(
            (profile_id, tuple(data["channel_ids"]), data["enabled"])
        )
        return {"success": True}

    def enabled_map(self) -> dict:
        """{profile_id: enabled} across all recorded bulk calls."""
        return {pid: enabled for pid, _cids, enabled in self.bulk_calls}


# --------------------------------------------------------------------------
# Decision 1a — absent / empty selection is a read-only NO-OP
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_absent_selection_is_no_op():
    client = FakeClient({100: [_channel(1)]}, profiles=[1, 2])
    settings = {100: _setting()}  # no channel_profile_ids at all

    result = await reconcile_group_profiles(client, settings, 100)

    assert result["status"] == "no_selection"
    assert client.bulk_calls == []
    assert client.get_channels_gids == []  # never even enumerated


@pytest.mark.asyncio
async def test_empty_selection_list_is_no_op():
    client = FakeClient({100: [_channel(1)]}, profiles=[1, 2])
    settings = {100: _setting(channel_profile_ids=[])}

    result = await reconcile_group_profiles(client, settings, 100)

    assert result["status"] == "no_selection"
    assert client.bulk_calls == []


@pytest.mark.asyncio
async def test_unknown_group_id_is_no_op():
    client = FakeClient({}, profiles=[1, 2])
    result = await reconcile_group_profiles(client, {}, 999)
    assert result["status"] == "no_selection"
    assert client.bulk_calls == []


# --------------------------------------------------------------------------
# Core subtractive / bulk behavior
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_selecting_subset_issues_correct_per_profile_bulk_calls():
    channels = [_channel(10, group=100), _channel(11, group=100)]
    client = FakeClient({100: channels}, profiles=[1, 2, 3])
    settings = {100: _setting(channel_profile_ids=[1])}

    result = await reconcile_group_profiles(client, settings, 100)

    assert result["status"] == "reconciled"
    assert result["channels_scoped"] == 2
    # Exactly one bulk call per profile, carrying BOTH channel ids.
    assert len(client.bulk_calls) == 3
    for _pid, cids, _enabled in client.bulk_calls:
        assert cids == (10, 11)
    assert client.enabled_map() == {1: True, 2: False, 3: False}
    assert result["profiles_enabled"] == 1
    assert result["profiles_disabled"] == 2


@pytest.mark.asyncio
async def test_selecting_multiple_profiles():
    client = FakeClient({100: [_channel(10)]}, profiles=[1, 2, 3, 4])
    settings = {100: _setting(channel_profile_ids=[1, 3])}

    await reconcile_group_profiles(client, settings, 100)

    assert FakeClient.enabled_map(client) == {1: True, 2: False, 3: True, 4: False}


@pytest.mark.asyncio
async def test_selected_profile_outside_universe_still_enabled():
    """A selected id missing from a stale universe fetch is still enabled."""
    client = FakeClient({100: [_channel(10)]}, profiles=[1, 2])
    settings = {100: _setting(channel_profile_ids=[5])}

    await reconcile_group_profiles(client, settings, 100)

    assert client.enabled_map() == {5: True, 1: False, 2: False}


@pytest.mark.asyncio
async def test_bulk_calls_are_o_of_p_not_o_of_np():
    """Cost is one call per profile regardless of channel count."""
    channels = [_channel(i, group=100) for i in range(50)]
    client = FakeClient({100: channels}, profiles=[1, 2, 3])
    settings = {100: _setting(channel_profile_ids=[2])}

    await reconcile_group_profiles(client, settings, 100)

    # 3 profiles -> 3 bulk calls, NOT 50*3.
    assert len(client.bulk_calls) == 3
    for _pid, cids, _enabled in client.bulk_calls:
        assert len(cids) == 50


# --------------------------------------------------------------------------
# Decision 2b — pipeline-owned channels excluded
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pipeline_owned_channel_excluded():
    channels = [
        _channel(10, group=100),
        _channel(11, group=100, owned=True),  # pipeline-owned -> excluded
        _channel(12, group=100),
    ]
    client = FakeClient({100: channels}, profiles=[1, 2])
    settings = {100: _setting(channel_profile_ids=[1])}

    result = await reconcile_group_profiles(client, settings, 100)

    assert result["channels_scoped"] == 2
    assert result["channels_excluded"] == 1
    for _pid, cids, _enabled in client.bulk_calls:
        assert cids == (10, 12)  # 11 never touched


@pytest.mark.asyncio
async def test_all_channels_owned_is_no_channels_no_op():
    channels = [_channel(10, owned=True), _channel(11, owned=True)]
    client = FakeClient({100: channels}, profiles=[1, 2])
    settings = {100: _setting(channel_profile_ids=[1])}

    result = await reconcile_group_profiles(client, settings, 100)

    assert result["status"] == "no_channels"
    assert result["channels_excluded"] == 2
    assert client.bulk_calls == []


# --------------------------------------------------------------------------
# Channel Group Override redirection (selection on source, channels in target)
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_override_redirection_resolves_effective_group():
    # Selection lives on SOURCE group 100, which overrides into TARGET 200.
    # Auto-sync channels land in 200.
    channels = [_channel(10, group=200), _channel(11, group=200)]
    client = FakeClient({200: channels, 100: []}, profiles=[1, 2])
    settings = {
        100: _setting(channel_profile_ids=[1], group_override=200),
        200: {"auto_channel_sync": False, "custom_properties": {}},
    }

    result = await reconcile_group_profiles(client, settings, 100)

    assert result["effective_group_id"] == 200
    assert result["channels_scoped"] == 2
    # Enumerated the TARGET group, not the source.
    assert 200 in client.get_channels_gids
    assert client.enabled_map() == {1: True, 2: False}


# --------------------------------------------------------------------------
# Resilience — stale/deleted selected id, empty group, idempotency
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stale_selected_profile_id_logged_and_skipped():
    client = FakeClient(
        {100: [_channel(10)]}, profiles=[1, 2, 3], fail_profiles=[2]
    )
    settings = {100: _setting(channel_profile_ids=[1])}

    result = await reconcile_group_profiles(client, settings, 100)

    # Profile 2 raised but 1 and 3 still applied; no exception propagated.
    assert result["status"] == "reconciled"
    assert client.enabled_map() == {1: True, 3: False}


@pytest.mark.asyncio
async def test_empty_group_is_cheap_no_op():
    client = FakeClient({100: []}, profiles=[1, 2])
    settings = {100: _setting(channel_profile_ids=[1])}

    result = await reconcile_group_profiles(client, settings, 100)

    assert result["status"] == "no_channels"
    assert result["channels_scoped"] == 0
    assert client.bulk_calls == []


@pytest.mark.asyncio
async def test_idempotent_rerun_produces_identical_calls():
    channels = [_channel(10), _channel(11)]
    settings = {100: _setting(channel_profile_ids=[1])}

    c1 = FakeClient({100: channels}, profiles=[1, 2, 3])
    await reconcile_group_profiles(c1, settings, 100)
    c2 = FakeClient({100: channels}, profiles=[1, 2, 3])
    await reconcile_group_profiles(c2, settings, 100)

    assert c1.bulk_calls == c2.bulk_calls
    assert c1.enabled_map() == {1: True, 2: False, 3: False}


@pytest.mark.asyncio
async def test_pagination_enumerates_all_channels():
    channels = [_channel(i, group=100) for i in range(250)]
    client = FakeClient({100: channels}, profiles=[1], page_size=100)
    settings = {100: _setting(channel_profile_ids=[1])}

    result = await reconcile_group_profiles(client, settings, 100)

    assert result["channels_scoped"] == 250
    for _pid, cids, _enabled in client.bulk_calls:
        assert len(cids) == 250


# --------------------------------------------------------------------------
# reconcile_all_selected_groups sweep + helper
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reconcile_all_selected_groups_sweep():
    client = FakeClient(
        {100: [_channel(10, group=100)], 300: [_channel(30, group=300)]},
        profiles=[1, 2],
    )
    settings = {
        100: _setting(channel_profile_ids=[1]),
        200: _setting(),  # no selection -> skipped
        300: _setting(channel_profile_ids=[2]),
    }

    result = await reconcile_all_selected_groups(client, settings)

    assert result["groups_with_selection"] == 2
    assert result["groups_reconciled"] == 2
    assert result["channels_scoped"] == 2


def test_groups_with_selection_filters_correctly():
    settings = {
        1: _setting(channel_profile_ids=[1]),
        2: _setting(channel_profile_ids=[]),
        3: _setting(),
        4: _setting(channel_profile_ids=[9, 10]),
    }
    assert sorted(groups_with_selection(settings)) == [1, 4]


# --------------------------------------------------------------------------
# Integration — parse a recorded Dispatcharr M3U account fixture
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reconcile_over_recorded_group_settings_fixture():
    """Drive the reconcile off a REAL recorded Dispatcharr account fixture.

    The recorded HD Homerun account carries an auto_channel_sync group
    (id 1304) with an empty custom_properties. We inject an operator's
    channel_profile_ids selection (exactly what the Auto-Sync modal writes)
    and confirm the reconcile scopes the group's channels to it.
    """
    with open(_FIXTURE, encoding="utf-8") as fh:
        account = json.load(fh)

    # Rebuild the get_all_m3u_group_settings shape from the recorded account.
    settings = {}
    for row in account["channel_groups"]:
        gid = row["channel_group"]
        settings[gid] = dict(row)
    # Operator selects profiles 12 (the account's default) — modal writes this
    # into the group's custom_properties.
    settings[1304]["custom_properties"] = {"channel_profile_ids": [12]}

    channels = [_channel(501, group=1304), _channel(502, group=1304)]
    client = FakeClient({1304: channels}, profiles=[12, 13])

    result = await reconcile_group_profiles(client, settings, 1304)

    assert result["status"] == "reconciled"
    assert result["channels_scoped"] == 2
    assert client.enabled_map() == {12: True, 13: False}
