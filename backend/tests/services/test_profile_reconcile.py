"""Unit + integration tests for group-level profile reconciliation.

GH #720 Part B / bead enhancedchannelmanager-y3m6o. Verifies that
``services.profile_reconcile`` applies a group's stored
``channel_profile_ids`` selection SUBTRACTIVELY to that group's channels via
one bulk profile update per profile (O(P), not O(N*P)), honoring the locked
PO decisions:

* 1(a) absent/empty selection -> read-only NO-OP;
* 2(b) pipeline-owned channels excluded from the reconcile, WITH handoff back
  to Auto-Sync control when the owning rule is gone/disabled;
* Channel Group Override redirection resolved before enumeration;
* enable-first two-phase apply so a selected-profile write failure can never
  strand channels; truthful ``partial_failure`` status.
"""
from __future__ import annotations

import json
import os

import pytest

from services.profile_reconcile import (
    PIPELINE_OWNERSHIP_MARKER_KEY,
    PIPELINE_OWNERSHIP_MARKER_VALUE,
    PIPELINE_OWNERSHIP_RULE_ID_KEY,
    dedupe_gids_by_effective_group,
    groups_with_selection,
    reconcile_all_selected_groups,
    reconcile_group_profiles,
    resolve_save_reconcile_targets,
)

_FIXTURE = os.path.join(
    os.path.dirname(__file__),
    "..",
    "fixtures",
    "bd_igqcy",
    "dispatcharr_v0272_m3u_account.json",
)

# Rule id used by the pipeline-ownership handoff tests.
_RULE_ID = 7


def _channel(cid: int, *, group: int = 100, owned: bool = False,
             rule_id: int | None = None, extra_cp=None) -> dict:
    cp = dict(extra_cp or {})
    if owned:
        cp[PIPELINE_OWNERSHIP_MARKER_KEY] = PIPELINE_OWNERSHIP_MARKER_VALUE
        if rule_id is not None:
            cp[PIPELINE_OWNERSHIP_RULE_ID_KEY] = rule_id
    return {"id": cid, "channel_group": group, "custom_properties": cp}


def _setting(*, channel_profile_ids=None, group_override=None, conflict=False) -> dict:
    cp: dict = {}
    if channel_profile_ids is not None:
        cp["channel_profile_ids"] = channel_profile_ids
    if group_override is not None:
        cp["group_override"] = group_override
    setting = {"auto_channel_sync": True, "custom_properties": cp}
    if conflict:
        setting["_ecm_channel_profile_conflict"] = True
    return setting


class FakeClient:
    """Minimal Dispatcharr client double for the reconcile.

    ``channels_by_gid`` maps effective group id -> list of channel dicts;
    ``get_channels`` paginates over that list. ``profiles`` is the universe.
    ``bulk_update_profile_channels`` records ``(profile_id, channel_ids,
    enabled)`` tuples. A profile id in ``fail_profiles`` raises to exercise the
    stale/deleted-id skip path. ``update_channel`` records marker-clear PATCHes.
    """

    def __init__(
        self,
        channels_by_gid,
        profiles,
        *,
        page_size=100,
        fail_profiles=None,
        raise_on_profiles=False,
    ):
        self.channels_by_gid = channels_by_gid
        self.profiles = profiles
        self.page_size = page_size
        self.fail_profiles = set(fail_profiles or [])
        # When True, get_channel_profiles raises — models an unreachable
        # Dispatcharr so the universe fetch fails (universe-fetch-failure path).
        self.raise_on_profiles = raise_on_profiles
        self.bulk_calls: list[tuple[int, tuple, bool]] = []
        self.get_channels_gids: list[int] = []
        self.update_channel_calls: list[tuple[int, dict]] = []

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
        if self.raise_on_profiles:
            raise RuntimeError("profile universe unreachable")
        return [{"id": pid, "name": f"P{pid}"} for pid in self.profiles]

    async def bulk_update_profile_channels(self, profile_id, data):
        if profile_id in self.fail_profiles:
            raise RuntimeError(f"profile {profile_id} is stale/deleted")
        self.bulk_calls.append(
            (profile_id, tuple(data["channel_ids"]), data["enabled"])
        )
        return {"success": True}

    async def update_channel(self, channel_id, data):
        self.update_channel_calls.append((channel_id, data))
        return {"id": channel_id, **data}

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

    result = await reconcile_group_profiles(client, settings, 100, live_rule_ids=set())

    assert result["status"] == "no_selection"
    assert client.bulk_calls == []
    assert client.get_channels_gids == []  # never even enumerated


@pytest.mark.asyncio
async def test_empty_selection_list_is_no_op():
    client = FakeClient({100: [_channel(1)]}, profiles=[1, 2])
    settings = {100: _setting(channel_profile_ids=[])}

    result = await reconcile_group_profiles(client, settings, 100, live_rule_ids=set())

    assert result["status"] == "no_selection"
    assert client.bulk_calls == []


@pytest.mark.asyncio
async def test_unknown_group_id_is_no_op():
    client = FakeClient({}, profiles=[1, 2])
    result = await reconcile_group_profiles(client, {}, 999, live_rule_ids=set())
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

    result = await reconcile_group_profiles(client, settings, 100, live_rule_ids=set())

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
async def test_enable_precedes_disable_two_phase():
    """Blocker 1: the selected-profile ENABLE must be issued BEFORE any
    non-selected disable, so a disable can never land while a needed enable is
    still pending."""
    client = FakeClient({100: [_channel(10)]}, profiles=[1, 2, 3])
    settings = {100: _setting(channel_profile_ids=[2])}

    await reconcile_group_profiles(client, settings, 100, live_rule_ids=set())

    # First recorded bulk call is the enable of the selected profile 2.
    assert client.bulk_calls[0][0] == 2
    assert client.bulk_calls[0][2] is True


@pytest.mark.asyncio
async def test_selecting_multiple_profiles():
    client = FakeClient({100: [_channel(10)]}, profiles=[1, 2, 3, 4])
    settings = {100: _setting(channel_profile_ids=[1, 3])}

    await reconcile_group_profiles(client, settings, 100, live_rule_ids=set())

    assert FakeClient.enabled_map(client) == {1: True, 2: False, 3: True, 4: False}


@pytest.mark.asyncio
async def test_bulk_calls_are_o_of_p_not_o_of_np():
    """Cost is one call per profile regardless of channel count."""
    channels = [_channel(i, group=100) for i in range(50)]
    client = FakeClient({100: channels}, profiles=[1, 2, 3])
    settings = {100: _setting(channel_profile_ids=[2])}

    await reconcile_group_profiles(client, settings, 100, live_rule_ids=set())

    # 3 profiles -> 3 bulk calls, NOT 50*3.
    assert len(client.bulk_calls) == 3
    for _pid, cids, _enabled in client.bulk_calls:
        assert len(cids) == 50


# --------------------------------------------------------------------------
# Blocker 1 — enable-first prevents transient-failure stranding
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_selected_enable_failure_aborts_before_any_disable():
    """Blocker 1 (inverse-strand): if enabling a SELECTED profile fails, the
    reconcile MUST abort before issuing any destructive disable — otherwise the
    channels get disabled everywhere while never enabled in the selected
    profile, stranding them in zero profiles.

    Confirmed this FAILS against the pre-fix single-pass loop (which disabled
    1 and 3 before/after the failed enable of 2) and passes with enable-first.
    """
    client = FakeClient(
        {100: [_channel(10)]}, profiles=[1, 2, 3], fail_profiles=[2]
    )
    settings = {100: _setting(channel_profile_ids=[2])}

    result = await reconcile_group_profiles(client, settings, 100, live_rule_ids=set())

    assert result["status"] == "partial_failure"
    assert result["failed_profile_ids"] == [2]
    # CRITICAL: NO disable calls were issued — channels are not stranded.
    assert all(enabled is True for _pid, _cids, enabled in client.bulk_calls)
    assert result["profiles_disabled"] == 0


@pytest.mark.asyncio
async def test_disable_failure_is_partial_failure_but_not_strand():
    """Should-Fix 5 truthful status: a NON-selected profile's disable failing
    is non-destructive (channel merely stays where it shouldn't be), so the
    reconcile continues but reports partial_failure with the failed id."""
    client = FakeClient(
        {100: [_channel(10)]}, profiles=[1, 2, 3], fail_profiles=[2]
    )
    settings = {100: _setting(channel_profile_ids=[1])}

    result = await reconcile_group_profiles(client, settings, 100, live_rule_ids=set())

    assert result["status"] == "partial_failure"
    assert result["failed_profile_ids"] == [2]
    # Selected 1 enabled; 3 disabled; 2 attempted-and-failed.
    assert client.enabled_map() == {1: True, 3: False}


# --------------------------------------------------------------------------
# Stale-selection safety (deleted / partially-deleted / universe-fetch-fail)
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fully_stale_selection_is_safety_no_op():
    """Every selected profile has been DELETED: a total NO-OP, channels
    untouched (not stranded), status stale_selection."""
    client = FakeClient({100: [_channel(10)]}, profiles=[1, 2])
    settings = {100: _setting(channel_profile_ids=[5])}

    result = await reconcile_group_profiles(client, settings, 100, live_rule_ids=set())

    assert result["status"] == "stale_selection"
    assert client.bulk_calls == []
    assert result["profiles_enabled"] == 0
    assert result["profiles_disabled"] == 0


@pytest.mark.asyncio
async def test_partial_stale_selection_ignores_deleted_id():
    """selection=[2, 5], universe=[1, 2, 3]: enable 2, disable 1 and 3, ignore
    the deleted id 5 (never touched)."""
    client = FakeClient({100: [_channel(10)]}, profiles=[1, 2, 3])
    settings = {100: _setting(channel_profile_ids=[2, 5])}

    result = await reconcile_group_profiles(client, settings, 100, live_rule_ids=set())

    assert result["status"] == "reconciled"
    assert client.enabled_map() == {1: False, 2: True, 3: False}
    assert all(pid != 5 for pid, _cids, _en in client.bulk_calls)


@pytest.mark.asyncio
async def test_universe_fetch_failure_degrades_to_enable_only():
    """Universe fetch FAILS -> enable-selected-only, NO disables."""
    client = FakeClient({100: [_channel(10)]}, profiles=[1, 2], raise_on_profiles=True)
    settings = {100: _setting(channel_profile_ids=[1])}

    result = await reconcile_group_profiles(client, settings, 100, live_rule_ids=set())

    assert result["status"] == "reconciled"
    assert client.enabled_map() == {1: True}
    assert all(enabled is True for _pid, _cids, enabled in client.bulk_calls)


# --------------------------------------------------------------------------
# Decision 2b — pipeline-ownership exclusion + HANDOFF (Blocker 2)
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_owned_channel_excluded_when_rule_live():
    """(i) The owning rule is still enabled + assigns profiles -> the channel
    is EXCLUDED from reconcile and its marker is left intact."""
    channels = [
        _channel(10, group=100),
        _channel(11, group=100, owned=True, rule_id=_RULE_ID),
        _channel(12, group=100),
    ]
    client = FakeClient({100: channels}, profiles=[1, 2])
    settings = {100: _setting(channel_profile_ids=[1])}

    result = await reconcile_group_profiles(
        client, settings, 100, live_rule_ids={_RULE_ID}
    )

    assert result["channels_scoped"] == 2
    assert result["channels_excluded"] == 1
    assert result["channels_released"] == 0
    for _pid, cids, _enabled in client.bulk_calls:
        assert cids == (10, 12)  # 11 excluded
    # Marker not cleared while owned.
    assert client.update_channel_calls == []


@pytest.mark.asyncio
async def test_owned_channel_released_when_rule_disabled():
    """(ii) The owning rule is no longer live (disabled) -> the channel is
    RELEASED: it rejoins the reconcile AND its stale marker keys are cleared."""
    channels = [
        _channel(10, group=100),
        _channel(11, group=100, owned=True, rule_id=_RULE_ID,
                 extra_cp={"custom_epg_id": 9}),
    ]
    client = FakeClient({100: channels}, profiles=[1, 2])
    settings = {100: _setting(channel_profile_ids=[1])}

    result = await reconcile_group_profiles(
        client, settings, 100, live_rule_ids=set()  # rule 7 NOT live
    )

    assert result["channels_excluded"] == 0
    assert result["channels_released"] == 1
    assert result["channels_scoped"] == 2  # both 10 and 11 reconciled
    for _pid, cids, _enabled in client.bulk_calls:
        assert cids == (10, 11)
    # Marker cleared on the released channel, preserving other custom_properties.
    assert len(client.update_channel_calls) == 1
    cid, body = client.update_channel_calls[0]
    assert cid == 11
    cleared = body["custom_properties"]
    assert PIPELINE_OWNERSHIP_MARKER_KEY not in cleared
    assert PIPELINE_OWNERSHIP_RULE_ID_KEY not in cleared
    assert cleared["custom_epg_id"] == 9  # unrelated key preserved


@pytest.mark.asyncio
async def test_owned_channel_released_when_rule_deleted():
    """(iii) The owning rule id is absent from the live set (deleted) -> same
    release + reconcile behavior as disabled."""
    channels = [_channel(11, group=100, owned=True, rule_id=99)]
    client = FakeClient({100: channels}, profiles=[1, 2])
    settings = {100: _setting(channel_profile_ids=[1])}

    result = await reconcile_group_profiles(
        client, settings, 100, live_rule_ids={1, 2, 3}  # 99 deleted
    )

    assert result["channels_released"] == 1
    assert result["channels_scoped"] == 1
    assert client.enabled_map() == {1: True, 2: False}
    assert len(client.update_channel_calls) == 1


@pytest.mark.asyncio
async def test_legacy_marker_without_rule_id_stays_owned(caplog):
    """(iv) A marker with NO rule id (legacy/unshipped) is treated
    conservatively as still-owned (excluded, not released) and warns."""
    import logging
    channels = [_channel(11, group=100, owned=True, rule_id=None)]
    client = FakeClient({100: channels}, profiles=[1, 2])
    settings = {100: _setting(channel_profile_ids=[1])}

    with caplog.at_level(logging.WARNING):
        result = await reconcile_group_profiles(
            client, settings, 100, live_rule_ids=set()
        )

    assert result["channels_excluded"] == 1
    assert result["channels_released"] == 0
    assert result["status"] == "no_channels"  # only channel was excluded
    assert client.update_channel_calls == []  # not cleared
    assert any("no valid rule id" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_unknown_liveness_keeps_marker_owned_conservatively():
    """If live_rule_ids is None (resolution failed) every marker stays OWNED —
    a transient DB failure must never release a pipeline-owned channel."""
    channels = [_channel(11, group=100, owned=True, rule_id=_RULE_ID)]
    client = FakeClient({100: channels}, profiles=[1, 2])
    settings = {100: _setting(channel_profile_ids=[1])}

    result = await reconcile_group_profiles(
        client, settings, 100, live_rule_ids=None
    )

    assert result["channels_excluded"] == 1
    assert result["channels_released"] == 0
    assert client.update_channel_calls == []


@pytest.mark.asyncio
async def test_all_channels_owned_is_no_channels_no_op():
    channels = [
        _channel(10, owned=True, rule_id=_RULE_ID),
        _channel(11, owned=True, rule_id=_RULE_ID),
    ]
    client = FakeClient({100: channels}, profiles=[1, 2])
    settings = {100: _setting(channel_profile_ids=[1])}

    result = await reconcile_group_profiles(
        client, settings, 100, live_rule_ids={_RULE_ID}
    )

    assert result["status"] == "no_channels"
    assert result["channels_excluded"] == 2
    assert client.bulk_calls == []


# --------------------------------------------------------------------------
# Channel Group Override redirection (selection on source, channels in target)
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_override_redirection_resolves_effective_group():
    channels = [_channel(10, group=200), _channel(11, group=200)]
    client = FakeClient({200: channels, 100: []}, profiles=[1, 2])
    settings = {
        100: _setting(channel_profile_ids=[1], group_override=200),
        200: {"auto_channel_sync": False, "custom_properties": {}},
    }

    result = await reconcile_group_profiles(client, settings, 100, live_rule_ids=set())

    assert result["effective_group_id"] == 200
    assert result["channels_scoped"] == 2
    assert 200 in client.get_channels_gids
    assert client.enabled_map() == {1: True, 2: False}


# --------------------------------------------------------------------------
# Resilience — empty group, idempotency, pagination
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_empty_group_is_cheap_no_op():
    client = FakeClient({100: []}, profiles=[1, 2])
    settings = {100: _setting(channel_profile_ids=[1])}

    result = await reconcile_group_profiles(client, settings, 100, live_rule_ids=set())

    assert result["status"] == "no_channels"
    assert result["channels_scoped"] == 0
    assert client.bulk_calls == []


@pytest.mark.asyncio
async def test_idempotent_rerun_produces_identical_calls():
    channels = [_channel(10), _channel(11)]
    settings = {100: _setting(channel_profile_ids=[1])}

    c1 = FakeClient({100: channels}, profiles=[1, 2, 3])
    await reconcile_group_profiles(c1, settings, 100, live_rule_ids=set())
    c2 = FakeClient({100: channels}, profiles=[1, 2, 3])
    await reconcile_group_profiles(c2, settings, 100, live_rule_ids=set())

    assert c1.bulk_calls == c2.bulk_calls
    assert c1.enabled_map() == {1: True, 2: False, 3: False}


@pytest.mark.asyncio
async def test_pagination_enumerates_all_channels():
    channels = [_channel(i, group=100) for i in range(250)]
    client = FakeClient({100: channels}, profiles=[1], page_size=100)
    settings = {100: _setting(channel_profile_ids=[1])}

    result = await reconcile_group_profiles(client, settings, 100, live_rule_ids=set())

    assert result["channels_scoped"] == 250
    for _pid, cids, _enabled in client.bulk_calls:
        assert len(cids) == 250


# --------------------------------------------------------------------------
# Blocker 3 — global-per-group conflict flag surfaced in the result
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_conflict_flag_surfaced_in_result():
    """The reconcile result carries the cross-account conflict flag stamped by
    get_all_m3u_group_settings so the save hook can warn (#9)."""
    client = FakeClient({100: [_channel(10)]}, profiles=[1, 2])
    settings = {100: _setting(channel_profile_ids=[1], conflict=True)}

    result = await reconcile_group_profiles(client, settings, 100, live_rule_ids=set())

    assert result["status"] == "reconciled"
    assert result["conflict"] is True


# --------------------------------------------------------------------------
# reconcile_all_selected_groups sweep + helpers
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reconcile_all_selected_groups_sweep(monkeypatch):
    async def _no_live_rules():
        return set()
    monkeypatch.setattr(
        "services.profile_reconcile._resolve_live_rule_ids", _no_live_rules
    )
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


@pytest.mark.asyncio
async def test_sweep_counts_partial_failure_distinctly(monkeypatch):
    """A sweep separates fully-reconciled groups from partial_failure ones.

    profile 2 is unwritable; group 100 (selects [1]) fails its disable of 2 and
    group 300 (selects [2]) fails its enable of 2 — both land in the
    partial_failure bucket, none in the reconciled bucket."""
    async def _no_live_rules():
        return set()
    monkeypatch.setattr(
        "services.profile_reconcile._resolve_live_rule_ids", _no_live_rules
    )
    client = FakeClient(
        {100: [_channel(10, group=100)], 300: [_channel(30, group=300)]},
        profiles=[1, 2],
        fail_profiles=[2],
    )
    settings = {
        100: _setting(channel_profile_ids=[1]),
        300: _setting(channel_profile_ids=[2]),
    }

    result = await reconcile_all_selected_groups(client, settings)

    assert result["groups_reconciled"] == 0
    assert result["groups_partial_failure"] == 2


@pytest.mark.asyncio
async def test_sweep_dedupes_override_source_and_target_by_effective_group(monkeypatch):
    """Should-Fix 6: source 100 overrides into target 200; both carry a
    selection. Dedupe collapses to effective group 200 and prefers the target's
    own selection ([2]), enumerating 200 exactly once."""
    async def _no_live_rules():
        return set()
    monkeypatch.setattr(
        "services.profile_reconcile._resolve_live_rule_ids", _no_live_rules
    )
    channels = [_channel(10, group=200)]
    client = FakeClient({200: channels, 100: []}, profiles=[1, 2])
    settings = {
        100: _setting(channel_profile_ids=[1], group_override=200),
        200: _setting(channel_profile_ids=[2]),
    }

    result = await reconcile_all_selected_groups(client, settings)

    assert result["groups_reconciled"] == 1
    assert client.get_channels_gids.count(200) == 1
    assert 100 not in client.get_channels_gids
    assert client.enabled_map() == {2: True, 1: False}


@pytest.mark.asyncio
async def test_save_reconcile_target_matches_sweep_winner_no_flap(monkeypatch):
    """Should-Fix 2 (no flap): saving the override SOURCE account must reconcile
    the SAME effective-group winner the sweep picks (the TARGET's selection),
    not the source's — otherwise instant-apply sets [1] and the next monitor
    sweep flips it to [2] every pass.

    S(100) overrides into T(200); channels live in 200. S selects [1], T selects
    [2]. A save that edits only S resolves winner 200 and applies [2] == the
    sweep winner, so a subsequent sweep is a no-op (no flap).
    """
    async def _no_live_rules():
        return set()
    monkeypatch.setattr(
        "services.profile_reconcile._resolve_live_rule_ids", _no_live_rules
    )
    settings = {
        100: _setting(channel_profile_ids=[1], group_override=200),
        200: _setting(channel_profile_ids=[2]),
    }

    # The save hook, editing only the source group 100, targets the same winner
    # the sweep uses.
    save_targets = resolve_save_reconcile_targets(settings, [100])
    sweep_targets = dedupe_gids_by_effective_group(settings, groups_with_selection(settings))
    assert save_targets == sweep_targets == [200]

    # Applying the save target yields the TARGET's selection [2], not the
    # source's [1] — so the sweep won't change anything afterward.
    channels = [_channel(10, group=200)]
    client = FakeClient({200: channels, 100: []}, profiles=[1, 2])
    for gid in save_targets:
        await reconcile_group_profiles(client, settings, gid, live_rule_ids=set())
    assert client.enabled_map() == {2: True, 1: False}


def test_resolve_save_targets_untouched_effective_group_excluded():
    """A save that edits a group whose effective group carries no selection
    anywhere targets nothing (a cleared selection is a no-op)."""
    settings = {
        100: _setting(channel_profile_ids=[1]),
        200: _setting(),  # no selection
    }
    assert resolve_save_reconcile_targets(settings, [200]) == []
    assert resolve_save_reconcile_targets(settings, [100]) == [100]


def test_dedupe_gids_by_effective_group_is_order_independent():
    settings = {
        100: _setting(channel_profile_ids=[1], group_override=200),
        200: _setting(channel_profile_ids=[2]),
    }
    assert dedupe_gids_by_effective_group(settings, [100, 200]) == [200]
    # Reverse order still resolves to the target.
    assert dedupe_gids_by_effective_group(settings, [200, 100]) == [200]


@pytest.mark.asyncio
async def test_sweep_aborts_promptly_on_cancel(monkeypatch):
    """NIT 7: a cancel_check predicate returning True aborts the sweep between
    groups so a long every-pass sweep stops promptly on monitor cancellation."""
    async def _no_live_rules():
        return set()
    monkeypatch.setattr(
        "services.profile_reconcile._resolve_live_rule_ids", _no_live_rules
    )
    client = FakeClient(
        {100: [_channel(10, group=100)], 300: [_channel(30, group=300)]},
        profiles=[1, 2],
    )
    settings = {
        100: _setting(channel_profile_ids=[1]),
        300: _setting(channel_profile_ids=[2]),
    }

    # Cancel immediately — no group should be reconciled.
    result = await reconcile_all_selected_groups(
        client, settings, cancel_check=lambda: True
    )

    assert result["groups_reconciled"] == 0
    assert client.get_channels_gids == []  # nothing enumerated


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
    """Drive the reconcile off a REAL recorded Dispatcharr account fixture."""
    with open(_FIXTURE, encoding="utf-8") as fh:
        account = json.load(fh)

    settings = {}
    for row in account["channel_groups"]:
        gid = row["channel_group"]
        settings[gid] = dict(row)
    settings[1304]["custom_properties"] = {"channel_profile_ids": [12]}

    channels = [_channel(501, group=1304), _channel(502, group=1304)]
    client = FakeClient({1304: channels}, profiles=[12, 13])

    result = await reconcile_group_profiles(client, settings, 1304, live_rule_ids=set())

    assert result["status"] == "reconciled"
    assert result["channels_scoped"] == 2
    assert client.enabled_map() == {12: True, 13: False}
