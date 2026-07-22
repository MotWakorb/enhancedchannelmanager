"""Finding 4 (close B2): the Channel Pipeline's assign_channel_profile write and
a group reconcile acquire the SAME per-effective-group lock, so they can never
touch one effective group's channels concurrently — regardless of adversarial
scheduling — and DIFFERENT groups do not serialize against each other.
"""
from __future__ import annotations

import asyncio

import pytest

import services.profile_reconcile as pr
from channel_pipeline_executor import ActionExecutor, ExecutionContext
from channel_pipeline_evaluator import StreamContext


@pytest.fixture(autouse=True)
def _reset_locks():
    pr._group_locks.clear()
    pr._sweep_in_progress = False
    yield
    pr._group_locks.clear()
    pr._sweep_in_progress = False


class SharedClient:
    """A client shared by an executor assign and a reconcile. Both profile-write
    methods enter a tracked critical section (with a delay) so overlap is
    detectable: serialized => max_concurrent == 1."""

    def __init__(self, all_settings, channels_by_gid, universe, delay=0.01):
        self.all_settings = all_settings
        self.channels_by_gid = channels_by_gid
        self.universe = universe
        self.delay = delay
        self.in_flight = 0
        self.max_concurrent = 0
        self.member: dict = {}          # (channel_id, profile_id) -> enabled
        self.channel_cp: dict = {}      # channel_id -> custom_properties

    async def _cs(self):
        self.in_flight += 1
        self.max_concurrent = max(self.max_concurrent, self.in_flight)
        try:
            await asyncio.sleep(self.delay)
        finally:
            self.in_flight -= 1

    async def get_all_m3u_group_settings(self):
        return self.all_settings

    async def get_m3u_accounts(self):
        return []  # normalize no-op

    async def get_channels(self, page=1, page_size=100, search=None, channel_group=None):
        rows = []
        for r in self.channels_by_gid.get(channel_group, []):
            rows.append({**r, "custom_properties": self.channel_cp.get(r["id"], r.get("custom_properties", {}))})
        return {"count": len(rows), "next": None, "previous": None, "results": rows}

    async def get_channel_profiles(self):
        return [{"id": p, "name": f"P{p}"} for p in self.universe]

    async def get_channel(self, channel_id):
        return {"id": channel_id, "custom_properties": self.channel_cp.get(channel_id, {})}

    async def update_channel(self, channel_id, data):
        self.channel_cp[channel_id] = dict(data.get("custom_properties") or {})
        return {"id": channel_id}

    async def update_profile_channel(self, profile_id, channel_id, data):
        # Executor per-profile membership write.
        await self._cs()
        self.member[(channel_id, profile_id)] = data["enabled"]
        return {}

    async def bulk_update_profile_channels(self, profile_id, data):
        # Reconcile bulk membership write.
        await self._cs()
        for cid in data["channel_ids"]:
            self.member[(cid, profile_id)] = data["enabled"]
        return {}


def _settings(gid, selection):
    return {gid: {"auto_channel_sync": True,
                  "custom_properties": {"channel_profile_ids": selection}}}


async def _run_executor_assign(client, channel_id, group_id, selection, rule_id=99):
    executor = ActionExecutor(
        client,
        existing_channels=[{"id": channel_id, "name": f"CH{channel_id}",
                            "channel_group": group_id, "custom_properties": {}}],
        all_profile_ids=list(client.universe),
    )
    exec_ctx = ExecutionContext()
    exec_ctx.current_channel_id = channel_id
    action = {"type": "assign_channel_profile", "channel_profile_ids": selection}
    stream_ctx = StreamContext(stream_id=1, stream_name="S", m3u_account_id=1)
    return await executor.execute(action, stream_ctx, exec_ctx, rule_id=rule_id)


@pytest.mark.asyncio
async def test_pipeline_assign_and_reconcile_same_group_serialize():
    """A pipeline assign (wants profile 2) and a group reconcile (wants profile
    1) for the SAME effective group must serialize (max concurrent == 1) and the
    pipeline's membership must WIN regardless of scheduling — the outcome no
    longer depends on re-check timing."""
    channels = {100: [{"id": 10, "channel_group": 100, "custom_properties": {}}]}
    client = SharedClient(
        all_settings=_settings(100, [1]),   # group reconcile wants profile 1
        channels_by_gid=channels, universe=[1, 2],
    )
    # Live rule set: rule 99 is live (so the marker keeps the channel pipeline-
    # owned and the reconcile excludes it once stamped).
    async def _live():
        return {99}
    import services.profile_reconcile as _pr
    _pr_orig = _pr._resolve_live_rule_ids
    _pr._resolve_live_rule_ids = _live
    try:
        await asyncio.gather(
            _run_executor_assign(client, 10, 100, [2], rule_id=99),  # pipeline -> [2]
            pr.reconcile_group_profiles(client, _settings(100, [1]), 100),
        )
    finally:
        _pr._resolve_live_rule_ids = _pr_orig

    # Serialized on group 100 — the assign and reconcile writes never overlapped.
    assert client.max_concurrent == 1
    # Pipeline's choice stands: channel 10 enabled in 2, disabled in 1.
    assert client.member.get((10, 2)) is True
    assert client.member.get((10, 1)) is False


@pytest.mark.asyncio
async def test_pipeline_assign_and_reconcile_different_groups_do_not_serialize():
    """A pipeline assign on group 100 and a reconcile on group 200 must NOT
    serialize against each other (no over-locking) — their writes overlap."""
    all_settings = {
        100: {"auto_channel_sync": True, "custom_properties": {"channel_profile_ids": [1]}},
        200: {"auto_channel_sync": True, "custom_properties": {"channel_profile_ids": [1]}},
    }
    channels = {200: [{"id": 20, "channel_group": 200, "custom_properties": {}}]}
    client = SharedClient(all_settings=all_settings, channels_by_gid=channels,
                          universe=[1, 2], delay=0.02)

    async def _live():
        return {99}
    import services.profile_reconcile as _pr
    _pr_orig = _pr._resolve_live_rule_ids
    _pr._resolve_live_rule_ids = _live
    try:
        await asyncio.gather(
            _run_executor_assign(client, 10, 100, [2], rule_id=99),  # group 100
            pr.reconcile_group_profiles(client, all_settings, 200),   # group 200
        )
    finally:
        _pr._resolve_live_rule_ids = _pr_orig

    # Different effective groups -> different locks -> writes overlapped.
    assert client.max_concurrent == 2
