"""Event Sync pre-flight checks (bead enhancedchannelmanager-ti939.1.3).

Verifies the Dispatcharr-side preconditions of an event_sync rule BEFORE a
preview (Phase 1A) or run (Phase 1B) executes:

* the MASTER group has ``auto_channel_sync`` **ON** — Dispatcharr owns the
  master channels' lifecycle; with auto-sync off no master channels exist
  and the whole feature silently matches nothing, so this must surface as
  an explicit failure in results rather than an empty preview;
* every SECONDARY group has ``auto_channel_sync`` **OFF** — a secondary
  with auto-sync on means Dispatcharr is creating duplicate channels from
  a group that event_sync treats as a pure stream source.

**READ-ONLY by contract.** This module inspects group settings via the
Dispatcharr client's ``get_all_m3u_group_settings()`` and must NEVER write
or toggle any Dispatcharr setting — guided toggles are a Phase 2 operator
convenience (epic ti939.3), and even then they are explicit operator
actions, never a side effect of a check. Unit tests pin this with a
client mock exposing only the read method.

Known edge (documented, not coded around): Dispatcharr channel groups are
GLOBAL BY NAME (bd-dgs64, config.py ~L104-117). ``get_all_m3u_group_settings``
collapses per-account settings to one entry per group ID, preferring an
entry with ``auto_channel_sync`` enabled — so a secondary group whose NAME
is also auto-synced by another account reads as auto_channel_sync ON here
and fails the secondary check. That is the correct surface: ANY account
auto-syncing that group ID means Dispatcharr is creating channels from it.
Real event groups are provider-distinct-named.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Machine-readable check identifiers (preview/run results contract).
CHECK_MASTER_AUTO_SYNC_ON = "master_auto_sync_on"
CHECK_SECONDARY_AUTO_SYNC_OFF = "secondary_auto_sync_off"
CHECK_GROUP_SETTINGS_FOUND = "group_settings_found"


def _failure(
    *, group_id: int, role: str, check: str, expected: str, got: str,
    message: str,
) -> dict:
    """One pre-flight failure entry (teaching shape: expected/got/message)."""
    return {
        "group_id": group_id,
        "role": role,
        "check": check,
        "expected": expected,
        "got": got,
        "message": message,
    }


async def check_event_sync_group_settings(client, config: dict) -> dict:
    """Pre-flight an event_sync config against live Dispatcharr group settings.

    Args:
        client: Dispatcharr API client. Only
            ``get_all_m3u_group_settings()`` is called — this helper is
            read-only by contract (see module docstring).
        config: A validated event_sync_config dict (``master_group_id``,
            ``secondary_group_ids``).

    Returns:
        ``{"ok": bool, "failures": [ ... ]}`` where each failure carries
        ``group_id`` / ``role`` / ``check`` / ``expected`` / ``got`` /
        ``message``. ``ok`` is True only when every check passed. A group
        absent from every account's settings fails
        ``group_settings_found`` — scoping to a group no provider carries
        is a stale config worth surfacing, not a silent no-op.
    """
    master_group_id = config.get("master_group_id")
    secondary_group_ids = config.get("secondary_group_ids") or []

    all_settings = await client.get_all_m3u_group_settings()
    failures: list[dict] = []

    master_settings = all_settings.get(master_group_id)
    if master_settings is None:
        failures.append(_failure(
            group_id=master_group_id,
            role="master",
            check=CHECK_GROUP_SETTINGS_FOUND,
            expected="group present in an M3U account's group settings",
            got="no settings for this group ID on any account",
            message=(
                f"Master group {master_group_id} was not found in any M3U "
                f"account's group settings — the group may have been "
                f"removed or renamed by the provider."
            ),
        ))
    elif not master_settings.get("auto_channel_sync"):
        failures.append(_failure(
            group_id=master_group_id,
            role="master",
            check=CHECK_MASTER_AUTO_SYNC_ON,
            expected="auto_channel_sync ON",
            got="auto_channel_sync OFF",
            message=(
                f"Master group {master_group_id} has auto_channel_sync OFF "
                f"in Dispatcharr — Dispatcharr creates no master channels, "
                f"so event_sync has nothing to attach to. Enable "
                f"auto_channel_sync for this group in Dispatcharr "
                f"(ECM never toggles it for you)."
            ),
        ))

    for group_id in secondary_group_ids:
        settings = all_settings.get(group_id)
        if settings is None:
            failures.append(_failure(
                group_id=group_id,
                role="secondary",
                check=CHECK_GROUP_SETTINGS_FOUND,
                expected="group present in an M3U account's group settings",
                got="no settings for this group ID on any account",
                message=(
                    f"Secondary group {group_id} was not found in any M3U "
                    f"account's group settings — the group may have been "
                    f"removed or renamed by the provider."
                ),
            ))
        elif settings.get("auto_channel_sync"):
            failures.append(_failure(
                group_id=group_id,
                role="secondary",
                check=CHECK_SECONDARY_AUTO_SYNC_OFF,
                expected="auto_channel_sync OFF",
                got="auto_channel_sync ON",
                message=(
                    f"Secondary group {group_id} has auto_channel_sync ON "
                    f"in Dispatcharr — Dispatcharr is creating its own "
                    f"channels from this group, which duplicates the master "
                    f"channels event_sync attaches to. Disable "
                    f"auto_channel_sync for this group in Dispatcharr "
                    f"(ECM never toggles it for you)."
                ),
            ))

    if failures:
        logger.warning(
            "[EVENT-SYNC] Pre-flight failed %s check(s) for master=%s "
            "secondaries=%s: %s",
            len(failures), master_group_id, secondary_group_ids,
            [(f["group_id"], f["check"]) for f in failures],
        )
    return {"ok": not failures, "failures": failures}
