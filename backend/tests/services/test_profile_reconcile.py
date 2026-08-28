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

import asyncio
import json
import os
from unittest.mock import AsyncMock

import pytest

from services.profile_reconcile import (
    PIPELINE_OWNERSHIP_MARKER_KEY,
    PIPELINE_OWNERSHIP_MARKER_VALUE,
    PIPELINE_OWNERSHIP_RULE_ID_KEY,
    dedupe_gids_by_effective_group,
    groups_with_selection,
    normalize_group_selections,
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


@pytest.fixture(autouse=True)
def _reset_group_locks():
    """Each test gets fresh per-effective-group locks (created in this test's
    event loop) so a module-level lock from a prior loop is never reused."""
    import services.m3u_group_state as group_state
    import services.profile_reconcile as pr
    group_state._group_locks.clear()
    pr._sweep_in_progress = False
    yield
    group_state._group_locks.clear()
    pr._sweep_in_progress = False


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
        raise_on_channels=False,
        recheck_channels_by_gid=None,
        raise_on_get_channel=False,
    ):
        self.channels_by_gid = channels_by_gid
        self.profiles = profiles
        self.page_size = page_size
        self.fail_profiles = set(fail_profiles or [])
        # When True, get_channels raises — models a hard per-group failure that
        # is NOT caught inside the locked body (Should-Fix 3).
        self.raise_on_channels = raise_on_channels
        # When True, get_channel_profiles raises — models an unreachable
        # Dispatcharr so the universe fetch fails (universe-fetch-failure path).
        self.raise_on_profiles = raise_on_profiles
        # gid -> channels returned by the SECOND get_channels fetch for that gid
        # (the reconcile's pre-write ownership RE-CHECK — Blocker 2a) so a channel
        # can "become pipeline-owned" between the snapshot and the re-check.
        self.recheck_channels_by_gid = recheck_channels_by_gid or {}
        self._fetch_counts: dict[int, int] = {}
        # When True, get_channel raises — models a failed fresh read (Blocker 5).
        self.raise_on_get_channel = raise_on_get_channel
        self.bulk_calls: list[tuple[int, tuple, bool]] = []
        self.get_channels_gids: list[int] = []
        self.update_channel_calls: list[tuple[int, dict]] = []
        # cid -> custom_properties returned by get_channel (models a CONCURRENT
        # custom_properties write between the reconcile snapshot and the PATCH).
        self.fresh_cp_by_id: dict[int, dict] = {}

    async def get_m3u_accounts(self):
        # Normalize (Blocker 3b) queries this; unit tests exercise the reconcile
        # path, not enforced-global propagation, so return no accounts (no-op).
        return []

    async def get_channel(self, channel_id):
        if self.raise_on_get_channel:
            raise RuntimeError("get_channel boom")
        if channel_id in self.fresh_cp_by_id:
            return {"id": channel_id, "custom_properties": self.fresh_cp_by_id[channel_id]}
        for rows in self.channels_by_gid.values():
            for c in rows:
                if c.get("id") == channel_id:
                    return dict(c)
        return {"id": channel_id, "custom_properties": {}}

    async def get_channels(self, page=1, page_size=100, search=None, channel_group=None):
        if self.raise_on_channels:
            raise RuntimeError("get_channels boom")
        if page == 1:
            self._fetch_counts[channel_group] = self._fetch_counts.get(channel_group, 0) + 1
        self.get_channels_gids.append(channel_group)
        # The reconcile fetches a group's channels TWICE (classification, then
        # the pre-write ownership re-check). Serve the re-check override on the
        # 2nd fetch so a channel can flip to pipeline-owned in between.
        if (self._fetch_counts.get(channel_group, 0) >= 2
                and channel_group in self.recheck_channels_by_gid):
            rows = list(self.recheck_channels_by_gid[channel_group])
        else:
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
        # Track max concurrency to prove per-effective-group serialization.
        self._bulk_in_flight = getattr(self, "_bulk_in_flight", 0) + 1
        self.max_concurrent_bulk = max(
            getattr(self, "max_concurrent_bulk", 0), self._bulk_in_flight
        )
        try:
            if getattr(self, "bulk_delay", 0):
                await asyncio.sleep(self.bulk_delay)
            if profile_id in self.fail_profiles:
                raise RuntimeError(f"profile {profile_id} is stale/deleted")
            self.bulk_calls.append(
                (profile_id, tuple(data["channel_ids"]), data["enabled"])
            )
            return {"success": True}
        finally:
            self._bulk_in_flight -= 1

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
async def test_universe_fetch_failure_is_degraded_enable_only():
    """Blocker 3a: universe fetch FAILS -> enable-selected-only, NO disables,
    and status is DEGRADED (enables applied but exclusivity not enforced) — NOT
    a clean 'reconciled'."""
    client = FakeClient({100: [_channel(10)]}, profiles=[1, 2], raise_on_profiles=True)
    settings = {100: _setting(channel_profile_ids=[1])}

    result = await reconcile_group_profiles(client, settings, 100, live_rule_ids=set())

    assert result["status"] == "degraded"
    assert result["error"]
    assert client.enabled_map() == {1: True}
    assert all(enabled is True for _pid, _cids, enabled in client.bulk_calls)


# --------------------------------------------------------------------------
# Blocker 1 — per-effective-group serialization + revalidation
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_overlapping_reconciles_serialize_and_end_in_later_selection():
    """Two overlapping reconciles of the SAME effective group must serialize
    (their enable/disable phases never interleave) and converge on the LATER
    selection — never zero memberships.

    Run A snapshots selection [1]; run B (the later save) snapshots [2]. Both
    pass a settings_provider returning the CURRENT selection ([2]); after
    acquiring the lock each revalidates to [2], so both apply [2]. A per-call
    delay encourages interleaving, and we assert the lock kept max concurrent
    bulk writes at 1."""
    channels = [_channel(10, group=100)]
    client = FakeClient({100: channels}, profiles=[1, 2])
    client.bulk_delay = 0.005

    current = {100: _setting(channel_profile_ids=[2])}  # latest committed state

    async def _provider():
        return current

    settings_a = {100: _setting(channel_profile_ids=[1])}
    settings_b = {100: _setting(channel_profile_ids=[2])}
    await asyncio.gather(
        reconcile_group_profiles(client, settings_a, 100, live_rule_ids=set(),
                                 settings_provider=_provider),
        reconcile_group_profiles(client, settings_b, 100, live_rule_ids=set(),
                                 settings_provider=_provider),
    )

    # Serialized: never two bulk writes for this group in flight at once.
    assert client.max_concurrent_bulk == 1
    # Converged on the LATER selection [2]; channel never left in zero profiles.
    assert client.enabled_map() == {2: True, 1: False}


@pytest.mark.asyncio
async def test_divergent_snapshots_reacquire_lock_and_serialize():
    """Should-Fix 2: two overlapping reconciles whose PRE-revalidation snapshots
    resolve to DIFFERENT effective groups but revalidate (under the lock) to the
    SAME effective group must still serialize — the lock is re-acquired under
    the group actually mutated, so no interleave strands channels.

    Run A snapshots group 100 with NO override (effective 100); run B snapshots
    100 already overriding into 200 (effective 200). Both revalidate to the
    committed state where 100->200, so both mutate group 200 and must serialize
    under lock 200."""
    channels = [_channel(10, group=200)]
    client = FakeClient({200: channels, 100: []}, profiles=[1, 2])
    client.bulk_delay = 0.005

    committed = {
        100: _setting(channel_profile_ids=[2], group_override=200),
        200: {"auto_channel_sync": False, "custom_properties": {}},
    }

    async def _provider():
        return committed

    snap_a = {100: _setting(channel_profile_ids=[2])}                      # eff 100
    snap_b = {100: _setting(channel_profile_ids=[2], group_override=200)}  # eff 200
    await asyncio.gather(
        reconcile_group_profiles(client, snap_a, 100, live_rule_ids=set(),
                                 settings_provider=_provider),
        reconcile_group_profiles(client, snap_b, 100, live_rule_ids=set(),
                                 settings_provider=_provider),
    )

    assert client.max_concurrent_bulk == 1        # serialized under the SAME lock
    assert client.enabled_map() == {2: True, 1: False}  # applied, never stranded


@pytest.mark.asyncio
async def test_sweep_counts_per_group_exception_as_errored(monkeypatch):
    """Should-Fix 3: a group whose reconcile RAISES (get_channels throws, not
    caught in the locked body) is counted in groups_errored so the monitor can
    surface it as a warning instead of a clean success."""
    async def _no_live_rules():
        return set()
    monkeypatch.setattr(
        "services.profile_reconcile._resolve_live_rule_ids", _no_live_rules
    )
    client = FakeClient({100: [_channel(10, group=100)]}, profiles=[1, 2],
                        raise_on_channels=True)
    settings = {100: _setting(channel_profile_ids=[1])}

    result = await reconcile_all_selected_groups(client, settings)

    assert result["groups_errored"] == 1
    assert result["groups_reconciled"] == 0


@pytest.mark.asyncio
async def test_reconcile_acquires_effective_group_lock(monkeypatch):
    """Every entrypoint routes through reconcile_group_profiles, which acquires
    the shared per-effective-group lock — assert the lock for the EFFECTIVE
    group (200, via override) is acquired."""
    import services.profile_reconcile as pr
    acquired = []
    real_get = pr.effective_group_lock

    def _spy(eff):
        acquired.append(eff)
        return real_get(eff)

    monkeypatch.setattr(pr, "effective_group_lock", _spy)
    client = FakeClient({200: [_channel(10, group=200)], 100: []}, profiles=[1, 2])
    settings = {
        100: _setting(channel_profile_ids=[1], group_override=200),
        200: {"auto_channel_sync": False, "custom_properties": {}},
    }

    await reconcile_group_profiles(client, settings, 100, live_rule_ids=set())

    assert acquired == [200]  # locked the EFFECTIVE group, not the source id


# --------------------------------------------------------------------------
# Blocker 2a — pre-write ownership re-check (pipeline vs reconcile)
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pre_write_recheck_drops_channel_that_became_pipeline_owned():
    """Blocker 2a: a channel that was UNOWNED at the initial snapshot but became
    pipeline-owned (a concurrent assign_channel_profile stamped it) BEFORE the
    destructive writes must be DROPPED by the pre-write ownership re-check — its
    membership is NOT overwritten."""
    initial = [_channel(10, group=100)]  # unowned at snapshot
    recheck = [_channel(10, group=100, owned=True, rule_id=_RULE_ID)]  # now owned
    client = FakeClient(
        {100: initial}, profiles=[1, 2],
        recheck_channels_by_gid={100: recheck},
    )
    settings = {100: _setting(channel_profile_ids=[1])}

    result = await reconcile_group_profiles(
        client, settings, 100, live_rule_ids={_RULE_ID}
    )

    # Channel 10 was dropped before any write — membership untouched.
    assert client.bulk_calls == []
    assert result["status"] == "no_channels"
    assert result["channels_excluded"] == 1


@pytest.mark.asyncio
async def test_recheck_fetch_failure_fails_closed_degraded():
    """If the pre-write ownership re-check fetch FAILS, fail closed (no writes),
    degraded — never risk clobbering a possibly-owned channel."""
    class _RecheckFails(FakeClient):
        async def get_channels(self, page=1, page_size=100, search=None, channel_group=None):
            self._fetch_counts[channel_group] = self._fetch_counts.get(channel_group, 0) + (1 if page == 1 else 0)
            if self._fetch_counts.get(channel_group, 0) >= 2:
                raise RuntimeError("recheck boom")
            self.get_channels_gids.append(channel_group)
            return {"count": 1, "next": None, "previous": None,
                    "results": list(self.channels_by_gid.get(channel_group, []))}

    client = _RecheckFails({100: [_channel(10, group=100)]}, profiles=[1, 2])
    settings = {100: _setting(channel_profile_ids=[1])}

    result = await reconcile_group_profiles(client, settings, 100, live_rule_ids=set())

    assert result["status"] == "degraded"
    assert client.bulk_calls == []


# --------------------------------------------------------------------------
# Blocker 4 — fail closed on lock / revalidation failure
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_revalidation_fetch_failure_fails_closed_no_writes():
    """Blocker 4: if the post-lock revalidation fetch FAILS, issue NO writes and
    return degraded (the scheduled sweep retries)."""
    client = FakeClient({100: [_channel(10, group=100)]}, profiles=[1, 2])
    settings = {100: _setting(channel_profile_ids=[1])}

    async def _boom():
        raise RuntimeError("revalidation boom")

    result = await reconcile_group_profiles(
        client, settings, 100, live_rule_ids=set(), settings_provider=_boom
    )

    assert result["status"] == "degraded"
    assert client.bulk_calls == []
    assert client.get_channels_gids == []  # never even enumerated


@pytest.mark.asyncio
async def test_continuous_retarget_fails_closed_no_writes():
    """Blocker 4: if a concurrent override retarget keeps moving the effective
    group under the lock past the bound, fail closed (no writes, degraded)."""
    client = FakeClient({100: [_channel(10, group=100)], 200: [], 300: []},
                        profiles=[1, 2])
    settings = {100: _setting(channel_profile_ids=[1])}

    # Each revalidation returns a DIFFERENT override target, so the effective
    # group never stabilises.
    targets = iter([200, 300, 400, 500, 600])

    async def _retarget_provider():
        t = next(targets)
        return {100: _setting(channel_profile_ids=[1], group_override=t)}

    result = await reconcile_group_profiles(
        client, settings, 100, live_rule_ids=set(),
        settings_provider=_retarget_provider,
    )

    assert result["status"] == "degraded"
    assert client.bulk_calls == []


# --------------------------------------------------------------------------
# Blocker 5 — no stale whole-value write on failed fresh read
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_marker_clear_skipped_on_failed_fresh_read():
    """Blocker 5: when clearing a released channel's marker, a FAILED fresh read
    must SKIP the write entirely (no PATCH from stale snapshot)."""
    ch = _channel(11, group=100, owned=True, rule_id=_RULE_ID)
    client = FakeClient({100: [ch]}, profiles=[1, 2], raise_on_get_channel=True)
    settings = {100: _setting(channel_profile_ids=[1])}

    # rule 7 not live -> channel is "released" -> _clear_ownership_marker runs,
    # but its fresh read fails -> no update_channel PATCH issued.
    await reconcile_group_profiles(client, settings, 100, live_rule_ids=set())

    assert client.update_channel_calls == []  # no stale write


# --------------------------------------------------------------------------
# Blocker 3b — normalize divergent sibling rows every sweep
# --------------------------------------------------------------------------

class _NormalizeClient(FakeClient):
    """FakeClient that also serves get_m3u_accounts for the normalize path and
    records group-settings PATCHes."""

    def __init__(self, *args, accounts=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._accounts = accounts or []
        self.group_settings_writes: list[tuple[int, list]] = []

    async def get_m3u_accounts(self):
        return self._accounts

    async def update_m3u_group_settings(self, account_id, data):
        self.group_settings_writes.append((account_id, data.get("group_settings", [])))
        return {"ok": True}


@pytest.mark.asyncio
async def test_normalize_converges_divergent_sibling(monkeypatch):
    """Blocker 3b: the sweep NORMALIZES a divergent sibling row (account 2 has
    [9] while the winning selection is [1]) even though nothing changed this
    request."""
    async def _no_live_rules():
        return set()
    monkeypatch.setattr(
        "services.profile_reconcile._resolve_live_rule_ids", _no_live_rules
    )
    accounts = [
        {"id": 1, "channel_groups": [
            {"channel_group": 100, "custom_properties": {"channel_profile_ids": [1]}}]},
        {"id": 2, "channel_groups": [
            {"channel_group": 100, "custom_properties": {"channel_profile_ids": [9]}}]},
    ]
    client = _NormalizeClient({100: [_channel(10, group=100)]}, profiles=[1, 2],
                              accounts=accounts)
    settings = {100: _setting(channel_profile_ids=[1])}  # winner = [1]

    result = await reconcile_all_selected_groups(client, settings)

    # Account 2's divergent row was rewritten to the winning [1].
    assert result["accounts_normalized"] == 1
    writes = [w for w in client.group_settings_writes if w[0] == 2]
    assert writes
    row = writes[0][1][0]
    assert row["custom_properties"]["channel_profile_ids"] == [1]


@pytest.mark.asyncio
async def test_normalize_account_list_fetch_failure_counted(monkeypatch):
    """Honesty (B1): a failed account-list fetch in normalize is counted as a
    failure (not silently zero), so the sweep reflects it."""
    async def _no_live_rules():
        return set()
    monkeypatch.setattr(
        "services.profile_reconcile._resolve_live_rule_ids", _no_live_rules
    )

    class _NoAccountsClient(_NormalizeClient):
        async def get_m3u_accounts(self):
            raise RuntimeError("accounts fetch boom")

    client = _NoAccountsClient({100: [_channel(10, group=100)]}, profiles=[1, 2])
    settings = {100: _setting(channel_profile_ids=[1])}

    result = await reconcile_all_selected_groups(client, settings)

    assert result["accounts_normalize_failed"] >= 1


@pytest.mark.asyncio
async def test_normalize_rewrites_legacy_string_row_to_int_storage(monkeypatch):
    """Finding 2: normalize rewrites a legacy STRING-typed row (["12"]) to the
    canonical INTEGER list [12] even though the coerced selection matches — so
    the string storage does not persist forever."""
    async def _no_live_rules():
        return set()
    monkeypatch.setattr(
        "services.profile_reconcile._resolve_live_rule_ids", _no_live_rules
    )
    accounts = [
        {"id": 1, "channel_groups": [
            {"channel_group": 100, "custom_properties": {"channel_profile_ids": ["12"]}}]},
    ]
    client = _NormalizeClient({100: [_channel(10, group=100)]}, profiles=[12, 13],
                              accounts=accounts)
    settings = {100: _setting(channel_profile_ids=[12])}  # winner (int) = [12]

    result = await reconcile_all_selected_groups(client, settings)

    assert result["accounts_normalized"] == 1
    writes = [w for w in client.group_settings_writes if w[0] == 1]
    assert writes
    row = writes[0][1][0]
    assert row["custom_properties"]["channel_profile_ids"] == [12]  # int storage


# --------------------------------------------------------------------------
# Findings — coalesce redundant sweeps; cancel during long phases
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_redundant_sweep_returns_queued_not_success(monkeypatch):
    """B2&B3: a sweep already in flight coalesces a second concurrent sweep to a
    DISTINCT NON-TERMINAL ``queued`` outcome — NOT an all-zero completed result
    that a caller could read as success. No trailing pass is scheduled."""
    import services.profile_reconcile as pr

    async def _no_live_rules():
        return set()
    monkeypatch.setattr(pr, "_resolve_live_rule_ids", _no_live_rules)

    started = asyncio.Event()
    release = asyncio.Event()

    class _SlowClient(FakeClient):
        async def get_channels(self, page=1, page_size=100, search=None, channel_group=None):
            started.set()
            await release.wait()
            return await super().get_channels(page, page_size, search, channel_group)

    client = _SlowClient({100: [_channel(10, group=100)]}, profiles=[1, 2])
    settings = {100: _setting(channel_profile_ids=[1])}

    first = asyncio.create_task(reconcile_all_selected_groups(client, settings))
    await started.wait()  # first sweep is mid-flight (blocked in get_channels)
    second = await reconcile_all_selected_groups(client, settings)  # should coalesce
    release.set()
    await first

    assert second.get("status") == "queued"
    # It is NOT a completed sweep — no reconciled/failure counters to read as done.
    assert "groups_reconciled" not in second


@pytest.mark.asyncio
async def test_cancel_during_write_phase_aborts_degraded():
    """Finding: cancellation during the profile-write phase aborts promptly with
    a degraded (cancelled) result — not a clean success."""
    client = FakeClient({100: [_channel(10, group=100)]}, profiles=[1, 2, 3])
    settings = {100: _setting(channel_profile_ids=[1])}

    # Cancel fires after the first bulk write so we abort mid-phase.
    calls = {"n": 0}

    def _cancel():
        calls["n"] += 1
        return calls["n"] > 2  # allow a couple of checks, then cancel

    result = await reconcile_group_profiles(
        client, settings, 100, live_rule_ids=set(), cancel_check=_cancel
    )

    assert result["status"] == "degraded"


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
async def test_marker_clear_preserves_concurrent_custom_properties():
    """Blocker 2 (clobber, removal direction): a concurrent unrelated
    custom_properties write that lands BETWEEN the reconcile snapshot and the
    marker-clear PATCH must survive. _clear_ownership_marker fresh-fetches the
    channel's CURRENT custom_properties right before the merge, so the
    concurrent key is preserved and only the marker keys are dropped."""
    # Snapshot the channel WITHOUT the concurrent key...
    ch = _channel(11, group=100, owned=True, rule_id=_RULE_ID)
    client = FakeClient({100: [ch]}, profiles=[1, 2])
    # ...but a concurrent writer has since added custom_epg_id=42 (get_channel
    # returns the FRESH blob).
    client.fresh_cp_by_id[11] = {
        PIPELINE_OWNERSHIP_MARKER_KEY: PIPELINE_OWNERSHIP_MARKER_VALUE,
        PIPELINE_OWNERSHIP_RULE_ID_KEY: _RULE_ID,
        "custom_epg_id": 42,
    }
    settings = {100: _setting(channel_profile_ids=[1])}

    await reconcile_group_profiles(client, settings, 100, live_rule_ids=set())  # rule 7 not live -> released

    assert len(client.update_channel_calls) == 1
    _cid, body = client.update_channel_calls[0]
    cleared = body["custom_properties"]
    assert cleared["custom_epg_id"] == 42          # concurrent write preserved
    assert PIPELINE_OWNERSHIP_MARKER_KEY not in cleared
    assert PIPELINE_OWNERSHIP_RULE_ID_KEY not in cleared


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

    assert result["status"] == "conflict"
    assert result["conflict"] is True
    assert client.get_channels_gids == []
    assert client.bulk_calls == []


@pytest.mark.asyncio
async def test_normalization_skips_every_row_in_a_conflicted_effective_group():
    settings = {
        100: _setting(channel_profile_ids=[1], group_override=500, conflict=True),
        200: _setting(channel_profile_ids=[2], group_override=500, conflict=True),
    }
    client = FakeClient({}, [])
    client.get_m3u_accounts = AsyncMock(return_value=[{
        "id": 9,
        "channel_groups": [{
            "channel_group": 100,
            "enabled": True,
            "auto_channel_sync": True,
            "custom_properties": {"channel_profile_ids": [2], "keep": "yes"},
        }],
    }])
    client.update_m3u_group_settings = AsyncMock()

    result = await normalize_group_selections(client, settings)

    assert result == {"normalized_accounts": 0, "failed_accounts": 0}
    client.update_m3u_group_settings.assert_not_awaited()


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
    # Dedup: only the TARGET (200) is enumerated; the source (100) never is.
    # (The target is fetched twice per reconcile — once for classification, once
    # for the pre-write ownership re-check — so assert membership, not count.)
    assert 200 in client.get_channels_gids
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


def test_dedupe_uses_lowest_source_id_when_no_target_has_a_selection():
    settings = {
        823: _setting(channel_profile_ids=[6, 7], group_override=665),
        2866: _setting(channel_profile_ids=[14], group_override=665),
    }
    assert dedupe_gids_by_effective_group(settings, [2866, 823]) == [823]
    assert dedupe_gids_by_effective_group(settings, [823, 2866]) == [823]


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


@pytest.mark.parametrize("bad", ["--5", "➂", "٣", "²", "", "-", " ", "5x", "x"])
def test_coerce_profile_id_rejects_garbage_without_raising(bad):
    """Finding 1: strict ASCII parse — '--5', unicode digits ('➂','²','٣'),
    empty/sign-only, and mixed strings all coerce to None (never raise, never
    accept a unicode digit)."""
    from services.profile_reconcile import coerce_profile_id
    assert coerce_profile_id(bad) is None


@pytest.mark.parametrize("good,expected", [(12, 12), ("12", 12), ("-3", -3), ("007", 7)])
def test_coerce_profile_id_accepts_ints_and_ascii_numeric_strings(good, expected):
    from services.profile_reconcile import coerce_profile_id
    assert coerce_profile_id(good) == expected


def test_coerce_profile_id_rejects_bool():
    from services.profile_reconcile import coerce_profile_id
    assert coerce_profile_id(True) is None
    assert coerce_profile_id(False) is None


@pytest.mark.asyncio
async def test_sweep_survives_garbage_stored_selection(monkeypatch):
    """Finding 1: a garbage stored channel_profile_ids (e.g. ['--5']) must NOT
    crash the sweep at groups_with_selection — the bad id is dropped and the
    group is treated as no_selection."""
    async def _no_live_rules():
        return set()
    monkeypatch.setattr(
        "services.profile_reconcile._resolve_live_rule_ids", _no_live_rules
    )
    client = FakeClient({100: [_channel(10, group=100)]}, profiles=[1, 2])
    settings = {100: {"auto_channel_sync": True,
                      "custom_properties": {"channel_profile_ids": ["--5", "➂"]}}}

    result = await reconcile_all_selected_groups(client, settings)  # must not raise

    # No valid ids -> not counted as a selected group, no writes.
    assert client.bulk_calls == []
    assert result["groups_with_selection"] == 0


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
