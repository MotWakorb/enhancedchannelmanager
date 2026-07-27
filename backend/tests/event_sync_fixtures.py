"""Shared event_sync test fixtures (beads ti939.1.4 / ti939.2.2 / ti939.2.3).

ONE fixture corpus for the preview endpoint tests
(``tests/routers/test_event_sync_preview.py``) and the Phase 1B lifecycle /
rollback tests (``tests/unit/test_event_sync_lifecycle.py``,
``tests/unit/test_event_sync_rollback_roundtrip.py``) — dry-run parity is
only a meaningful assertion when both sides consume the SAME data.

Also home of :class:`FakeDispatcharrState` + :func:`make_stateful_client`:
a mutable in-memory Dispatcharr that encodes the VERIFIED Dispatcharr
behaviors (ti939 feasibility read of ``apps/m3u/tasks.py
sync_auto_channels``) as fixture semantics, so a future Dispatcharr
behavior change surfaces as a test-fixture conversation rather than
silent production breakage:

* Channel ids (UUIDs upstream) are PRESERVED across refreshes —
  :meth:`FakeDispatcharrState.master_refresh` updates channels in place.
* The sync task has NO code path that resets a channel's stream list —
  foreign (ECM-attached) streams SURVIVE refreshes.
* Channels are deleted only when the master provider drops the stream
  (event over) — :meth:`FakeDispatcharrState.end_event`.
"""
from __future__ import annotations

import copy
from unittest.mock import AsyncMock, MagicMock

MASTER_GROUP_ID = 10
SECONDARY_A = 20
SECONDARY_B = 30

GROUP_NAMES = {
    MASTER_GROUP_ID: "Peacock Events",
    SECONDARY_A: "Fubo Events",
    SECONDARY_B: "DAZN Events",
}

MASTER_CHANNELS = [
    {"id": 55, "name": "Peacock 14: Mercury vs. Aces @ 11 Jul 06:00 PM ET",
     "channel_group_id": MASTER_GROUP_ID},
    {"id": 56, "name": "Peacock 11: IMSA CTMP Qualifying @ 11 Jul 03:55 PM ET",
     "channel_group_id": MASTER_GROUP_ID},
    {"id": 57, "name": "Peacock 40: NO EVENT",
     "channel_group_id": MASTER_GROUP_ID},
]

# Group A: one attach + one ambiguous. Group B: one unmatched + one parse fail.
SECONDARY_STREAMS = {
    "Fubo Events": [
        {"id": 201, "name": "WNBA TV 01: Mercury vs. Aces @ 11 Jul 06:00 PM ET",
         "m3u_account": 1},
        {"id": 202, "name": "IMSA TV 03 : IMSA VPRC at CTMP R2 @ 11 Jul 03:55 PM ET",
         "m3u_account": 1},
    ],
    "DAZN Events": [
        {"id": 301, "name": "DAZN 05: Fury vs. Usyk @ 11 Jul 11:00 PM ET",
         "m3u_account": 2},
        {"id": 302, "name": "DAZN 09: NO EVENT", "m3u_account": 2},
    ],
}

M3U_ACCOUNTS = [
    {"id": 1, "name": "FuboProvider"},
    {"id": 2, "name": "DaznProvider"},
]

# All groups correctly configured: master auto-sync ON, secondaries OFF.
GROUP_SETTINGS_OK = {
    MASTER_GROUP_ID: {"auto_channel_sync": True},
    SECONDARY_A: {"auto_channel_sync": False},
    SECONDARY_B: {"auto_channel_sync": False},
}


def event_sync_config(**overrides) -> dict:
    """A fully-populated, valid event_sync_config for the shared corpus."""
    config = {
        "master_group_id": MASTER_GROUP_ID,
        "secondary_group_ids": [SECONDARY_A, SECONDARY_B],
        "time_window_minutes": 30,
        "attach_threshold": 0.80,
        "max_attach_per_run": 100,
        "enabled": True,
    }
    config.update(overrides)
    return config


def live_master_channels() -> list[dict]:
    """The corpus masters as the ENGINE sees them on a live run.

    Dispatcharr auto-creates master-group channels, so they carry
    ``auto_created=True`` and an explicit (initially empty) stream list.
    """
    return [
        dict(c, auto_created=True, streams=[]) for c in MASTER_CHANNELS
    ]


class FakeDispatcharrState:
    """Mutable in-memory Dispatcharr channel/stream state (see module doc).

    ``channels`` maps channel id -> channel dict; ``secondary_streams`` maps
    channel-group NAME -> list of stream dicts (the shape ``get_streams``
    returns). Mutations by the client under test are recorded on
    ``update_channel_calls`` / ``deleted_channel_ids``.
    """

    def __init__(self, channels: list[dict] | None = None,
                 secondary_streams: dict[str, list[dict]] | None = None):
        self.channels: dict[int, dict] = {
            c["id"]: copy.deepcopy(c) for c in (channels or [])
        }
        self.secondary_streams: dict[str, list[dict]] = copy.deepcopy(
            secondary_streams or {}
        )
        self.update_channel_calls: list[tuple[int, dict]] = []
        self.deleted_channel_ids: list[int] = []

    # --- Dispatcharr-side lifecycle simulations (verified behaviors) ------

    def master_refresh(self, renames: dict[int, str] | None = None) -> None:
        """Simulate a master auto-sync refresh: channels updated IN PLACE
        (same ids), names possibly tweaked, and foreign (ECM-attached)
        streams SURVIVE — the sync task never resets a stream list."""
        for cid, new_name in (renames or {}).items():
            self.channels[cid]["name"] = new_name

    def end_event(self, channel_id: int) -> None:
        """The master provider dropped the event's stream: Dispatcharr
        deletes the channel (master-scoped deletion), which detaches every
        secondary stream with it."""
        del self.channels[channel_id]

    def add_master(self, channel: dict) -> None:
        """A new event materialized in the master group (Dispatcharr created
        the channel from the master account's stream)."""
        self.channels[channel["id"]] = copy.deepcopy(channel)

    # --- Convenience assertions helpers -----------------------------------

    def stream_ids_of(self, channel_id: int) -> list[int]:
        return [
            s["id"] if isinstance(s, dict) else s
            for s in self.channels[channel_id].get("streams", [])
        ]


def make_stateful_client(state: FakeDispatcharrState,
                         accounts: list[dict] | None = None) -> MagicMock:
    """A mocked Dispatcharr client backed by ``state``.

    Read methods serve deep copies (the engine mutates its loaded dicts);
    ``update_channel`` writes the payload back into ``state`` so a SECOND
    pipeline run observes the first run's attachments — the property the
    lifecycle scenarios exercise. Group-settings writers are bare AsyncMocks
    so every test doubles as a "never toggles Dispatcharr group settings"
    canary.
    """
    client = MagicMock()

    async def _get_channels(page=1, page_size=100, **kwargs):
        results = [copy.deepcopy(c) for c in state.channels.values()]
        return {"count": len(results), "next": None, "results": results}

    async def _get_channel(channel_id):
        ch = state.channels.get(channel_id)
        if ch is None:
            raise RuntimeError(f"channel {channel_id} not found (404)")
        return copy.deepcopy(ch)

    async def _get_streams(page=1, page_size=100, channel_group_name=None,
                           **kwargs):
        results = [
            copy.deepcopy(s)
            for s in state.secondary_streams.get(channel_group_name, [])
        ]
        return {"count": len(results), "next": None, "results": results}

    async def _update_channel(channel_id, payload):
        state.update_channel_calls.append((channel_id, copy.deepcopy(payload)))
        ch = state.channels.get(channel_id)
        if ch is None:
            raise RuntimeError(f"channel {channel_id} not found (404)")
        for key, value in payload.items():
            ch[key] = copy.deepcopy(value)
        return copy.deepcopy(ch)

    async def _delete_channel(channel_id):
        state.deleted_channel_ids.append(channel_id)
        state.channels.pop(channel_id, None)

    async def _group_name_for_id(group_id):
        return GROUP_NAMES.get(group_id)

    client.get_channels = AsyncMock(side_effect=_get_channels)
    client.get_channel = AsyncMock(side_effect=_get_channel)
    client.get_channel_groups = AsyncMock(return_value=[])
    client.get_channel_profiles = AsyncMock(return_value=[])
    client.get_m3u_accounts = AsyncMock(
        return_value=M3U_ACCOUNTS if accounts is None else accounts
    )
    client._channel_group_name_for_id = AsyncMock(side_effect=_group_name_for_id)
    client.get_streams = AsyncMock(side_effect=_get_streams)
    client.update_channel = AsyncMock(side_effect=_update_channel)
    client.delete_channel = AsyncMock(side_effect=_delete_channel)
    client.get_all_m3u_group_settings = AsyncMock(return_value=GROUP_SETTINGS_OK)

    # Mutating surfaces event_sync must NEVER touch.
    client.create_channel = AsyncMock()
    client.create_channel_group = AsyncMock()
    client.delete_channel_group = AsyncMock()
    client.update_stream = AsyncMock()
    client.update_m3u_group_settings = AsyncMock()
    client.update_channel_group = AsyncMock()
    return client


def make_promote_client(state: FakeDispatcharrState, next_channel_id=900):
    """The shared stateful client + a STATEFUL ``create_channel`` (bead
    ti939.4.1 — promotion is the ONE path allowed to create channels).

    Created channels land in ``state.channels`` with sequential ids from
    ``next_channel_id`` so a SECOND run observes the first run's promoted
    channels (the adoption-idempotence property the promotion scenarios
    exercise). ``delete_channel`` already writes back on the base client.
    Non-promotion tests keep using :func:`make_stateful_client`, whose bare
    ``create_channel`` AsyncMock doubles as the never-creates canary.
    """
    client = make_stateful_client(state)
    counter = {"next": next_channel_id}

    async def _create_channel(data):
        cid = counter["next"]
        counter["next"] += 1
        ch = copy.deepcopy(data)
        ch["id"] = cid
        ch.setdefault("streams", [])
        state.channels[cid] = copy.deepcopy(ch)
        return copy.deepcopy(ch)

    client.create_channel = AsyncMock(side_effect=_create_channel)
    return client


def assert_never_touched_group_settings(client) -> None:
    """The Phase 1 hard constraint: ECM never toggles Dispatcharr group
    settings (auto_channel_sync stays guidance-only UI)."""
    client.update_m3u_group_settings.assert_not_called()
    client.update_channel_group.assert_not_called()


def assert_never_created_or_deleted_channels(client) -> None:
    """ECM never creates or deletes channels in the event_sync feature."""
    client.create_channel.assert_not_called()
    client.delete_channel.assert_not_called()
    client.create_channel_group.assert_not_called()
    client.delete_channel_group.assert_not_called()
