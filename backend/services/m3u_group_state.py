"""Shared state helpers for profile-aware M3U group operations."""
from __future__ import annotations

import asyncio
import re
from contextlib import AsyncExitStack, asynccontextmanager


# Dispatcharr performs a full-row upsert for these fields. Every write must
# preserve current values that the caller did not explicitly replace.
GROUP_SETTINGS_UPSERT_FIELDS = (
    "enabled",
    "auto_channel_sync",
    "auto_sync_channel_start",
    "auto_sync_channel_end",
    "custom_properties",
)


def merge_group_settings_row(current: dict | None, incoming: dict) -> dict:
    """Overlay a partial group-settings row onto its current stored state."""
    current = current or {}
    merged = dict(incoming)
    if "id" not in merged and current.get("id") is not None:
        merged["id"] = current["id"]
    for field in GROUP_SETTINGS_UPSERT_FIELDS:
        if field not in merged and field in current:
            merged[field] = current[field]
    return merged


def coerce_profile_id(value):
    """Coerce an integer or legacy ASCII-integer string to an integer ID."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if re.fullmatch(r"-?[0-9]+", stripped):
            return int(stripped)
    return None


# All profile-membership writers use this in-process registry so writes for the
# same effective group cannot interleave. Cross-process locking is out of scope.
_group_locks: dict[int, asyncio.Lock] = {}


def effective_group_lock(effective_gid: int) -> asyncio.Lock:
    lock = _group_locks.get(effective_gid)
    if lock is None:
        lock = asyncio.Lock()
        _group_locks[effective_gid] = lock
    return lock


@asynccontextmanager
async def acquire_effective_group_locks(effective_gids):
    """Acquire effective-group locks in a stable, deadlock-free order."""
    async with AsyncExitStack() as stack:
        for gid in sorted(set(effective_gids)):
            await stack.enter_async_context(effective_group_lock(gid))
        yield
