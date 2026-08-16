"""Emptying a channel group before it is deleted.

Shared by the two delete paths that both promise the operator, in the very same
confirm dialog, that a non-empty group's channels will be moved somewhere:

- the Edit Mode bulk commit (``routers.channels._run_bulk_commit``, the
  ``deleteChannelGroup`` operation), fixed under bead
  ``enhancedchannelmanager-ayfn9``;
- the immediate delete (``routers.channel_groups.delete_channel_group``), fixed
  under bead ``enhancedchannelmanager-auocn``.

They live here rather than in either router because the whole point of the
second bead was that the two paths must not drift: one dialog makes the promise,
so one implementation has to keep it.

The frontend half of the contract is ``frontend/src/utils/ungroupedTargetGroup.ts``,
which resolves the same group by the same name so the dialog can NAME the real
destination — or say up front that there isn't one.
"""
import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)

#: Called once per channel that has actually been moved, immediately after its
#: PATCH returns: ``(channel_id, channel_name, target_group)``. See the
#: ``on_channel_moved`` parameter of :func:`reparent_group_channels`.
ChannelMovedCallback = Callable[[int, str, dict], None]


# Dispatcharr's baseline channel group — where a channel goes when it belongs to
# nothing in particular.
#
# A Dispatcharr channel row REQUIRES a group. Measured against a live 0.28.2
# instance on 2026-08-09::
#
#     PATCH /api/channels/channels/1/ {"channel_group_id": null}
#       -> 400 {"channel_group_id":["This field may not be null."]}
#     PATCH /api/channels/channels/1/ {"channel_group_id": 378}
#       -> 200
#
# So "Ungrouped" is a state ECM can READ — the frontend buckets channels whose
# ``channel_group_id`` is null, which is a shape Dispatcharr can return for rows
# ECM did not write — but never a state ECM can WRITE. Every other write site in
# the channels router already guards ``if group_id is not None``; the …-ayfn9
# reparent was the one place that sent an explicit null, and it 400'd on the
# first live click.
#
# Dispatcharr ships this group and falls back to it when a channel is created
# without one, which is why a channel committed with no group turns up there
# (docs/user_guide/getting-started/your-first-channels.md). Confirmed present
# exactly once on the drill instance (id=1 of 378 groups) — but resolved BY NAME,
# because "it is 1 on a fresh install" is an observation about a default, not a
# contract, and an operator can renumber or rename their way out of it.
UNGROUPED_TARGET_GROUP_NAME = "Default Group"


class UngroupedTargetUnavailable(ValueError):
    """The group's channels have nowhere to go, so the group must not be deleted.

    A ``ValueError`` so the bulk-commit path, which reports any operation
    exception to the operator as that operation's error text, keeps behaving
    exactly as it did. Named so the immediate-delete endpoint can tell it apart
    from an unrelated failure and answer 400 with the reason rather than 500
    with nothing.
    """


async def resolve_ungrouped_target_group(client) -> Optional[dict]:
    """The group a channel moves to when it has nowhere else to go, or ``None``.

    Matched on a trimmed, case-folded name. Returns the whole group row so the
    caller can log and report the id it actually resolved.
    """
    groups = await client.get_channel_groups()
    wanted = UNGROUPED_TARGET_GROUP_NAME.casefold()
    for group in groups or []:
        if not isinstance(group, dict) or group.get("id") is None:
            continue
        name = group.get("name")
        if isinstance(name, str) and name.strip().casefold() == wanted:
            return group
    return None


async def _still_belongs_to_group(client, channel_id: int, group_id: int, log_prefix: str) -> bool:
    """Is this channel STILL in the group we are emptying? Re-read and say.

    Only ever withholds a write on positive evidence that the channel has
    moved. A re-read that fails, or that comes back in a shape without a
    readable ``channel_group_id``, answers ``True`` — refusing the write on no
    evidence would leave the group non-empty and turn a working delete into
    Dispatcharr's "Cannot delete group with associated channels", which is a
    worse outcome than the race this guards. Same ``isinstance(..., dict)``
    shape discipline as :func:`resolve_ungrouped_target_group`.
    """
    try:
        current = await client.get_channel(channel_id)
    except Exception as exc:  # noqa: BLE001 — any failure means "no evidence"
        logger.warning(
            "%s Could not re-read channel %s before moving it out of group %s (%s); "
            "moving it anyway",
            log_prefix, channel_id, group_id, exc,
        )
        return True

    if not isinstance(current, dict) or "channel_group_id" not in current:
        logger.warning(
            "%s Re-read of channel %s returned no channel_group_id; moving it anyway",
            log_prefix, channel_id,
        )
        return True

    live_group_id = current.get("channel_group_id")
    if live_group_id == group_id:
        return True

    logger.warning(
        "%s Channel %s left group %s before it could be moved (it is in group %s now); "
        "leaving it where it is",
        log_prefix, channel_id, group_id, live_group_id,
    )
    return False


async def reparent_group_channels(
    client,
    group_id: int,
    log_prefix: str = "[GROUPS]",
    on_channel_moved: Optional[ChannelMovedCallback] = None,
) -> int:
    """Move every channel out of ``group_id`` before it is deleted; return how many.

    Bead ``enhancedchannelmanager-ayfn9``. Dispatcharr refuses to delete a group
    that still has channels — drill run 2026-08-08-run17 measured
    ``DELETE /api/channels/groups/377/ -> 400 {"error":"Cannot delete group with
    associated channels"}`` on a 6-channel group and ``204`` on an empty one — so
    a bare delete can only ever succeed on a group that is already empty. ECM's
    own confirm dialog promises the channels move somewhere; this is the step that
    keeps that promise, and it runs BEFORE the delete.

    The destination is :data:`UNGROUPED_TARGET_GROUP_NAME`, resolved by name.
    Reparenting to ``None`` is NOT an option — see that constant.

    The member list is read LIVE rather than from any caller-side snapshot: in the
    bulk path the operator's "Also delete the N channels" option stages
    ``deleteChannel`` ops ahead of the group delete in the same batch, so by the
    time this runs those channels are already gone and a snapshot would name rows
    that no longer exist. A live read finds only what is genuinely still parented
    to the group.

    Raises :class:`UngroupedTargetUnavailable` — and so fails the operation,
    skipping the delete — when the channels cannot be moved. A member that will
    not move must NOT be followed by a delete Dispatcharr is going to reject: the
    reason is the thing the operator needs.

    ``on_channel_moved`` is called once per channel, immediately after its PATCH
    returns, and is how the caller records each move as it lands rather than
    after the whole delete has succeeded (bead ``enhancedchannelmanager-kz089``,
    fix round 3). This is N independent writes, and any of them can be the last
    one: with channels 10 and 11 in the group, 10 moving and 11 raising leaves
    10 genuinely reparented, the group undeleted, and the operation reported as
    a failure. The caller used to record ONE aggregate journal row after
    everything had succeeded, so channel 10's landed move had no row anywhere.
    The return value cannot carry that — it does not survive the raise, and a
    count is not a per-channel fact.

    KNOWN LIMITATION, not closed by anything below: the read is live relative to
    the CALLER, but it is not atomic relative to these writes. Another operator
    who moves a channel to a third group between our read and our write will have
    that move replaced. Dispatcharr offers nothing to make the write conditional
    — measured against the live 0.28.2 schema on 2026-08-15,
    ``GET /api/schema/?format=json`` declares no ``If-Match``, no
    ``If-Unmodified-Since``, no ``ETag`` and no ``412`` anywhere in the document,
    ``PATCH /api/channels/channels/{id}/`` takes the path ``id`` and nothing
    else, and the ``Channel`` serializer carries no version or modified-at field
    to compare against even client-side. There is no compare-and-set to reach for.

    What each write IS preceded by is a re-read (:func:`_still_belongs_to_group`),
    which skips a channel that has already left. That is a CHECK, not atomicity:
    a move landing between the re-read and the PATCH is still lost. Its value is
    that it removes the LONG window — without it, a channel's exposure runs from
    its row being read, through the remaining pages of the scan, the group
    resolution, and every earlier PATCH in this loop, which grows with the size
    of the group. With it, the exposure is one round trip. The cost is one extra
    GET per member on a path that already issues one PATCH per member, and it
    changes the failure from a silent overwrite into a logged skip.
    """
    member_ids: list[int] = []
    # Names are read here because this scan is the only place they are on hand,
    # and ``on_channel_moved`` needs one for the journal's Entity column.
    member_names: dict[int, str] = {}
    page = 1
    while True:
        response = await client.get_channels(page=page, page_size=500)
        for ch in response.get("results", []):
            if ch.get("channel_group_id") == group_id and ch.get("id") is not None:
                member_ids.append(ch["id"])
                name = ch.get("name")
                member_names[ch["id"]] = (
                    name if isinstance(name, str) and name else f"Channel {ch['id']}"
                )
        if not response.get("next"):
            break
        page += 1

    # An already-empty group needs no destination, so it deletes cleanly even on
    # an instance that has no baseline group at all.
    if not member_ids:
        return 0

    target = await resolve_ungrouped_target_group(client)
    if target is None:
        raise UngroupedTargetUnavailable(
            f'Cannot move {len(member_ids)} channel(s) out of this group: no '
            f'"{UNGROUPED_TARGET_GROUP_NAME}" exists to move them to, and '
            f'Dispatcharr does not allow a channel without a group. Create a group '
            f'named "{UNGROUPED_TARGET_GROUP_NAME}", move the channels somewhere '
            f'yourself, or delete the channels along with the group.'
        )
    if target["id"] == group_id:
        raise UngroupedTargetUnavailable(
            f'Cannot delete "{UNGROUPED_TARGET_GROUP_NAME}": it is where channels '
            f'are moved when their group is deleted, so its own '
            f'{len(member_ids)} channel(s) have nowhere to go. Move them to another '
            f'group first, or delete the channels along with the group.'
        )

    logger.info(
        "%s Moving %s channel(s) from group %s to '%s' (id=%s) before deleting it",
        log_prefix, len(member_ids), group_id, target.get("name"), target["id"],
    )
    moved = 0
    for channel_id in member_ids:
        if not await _still_belongs_to_group(client, channel_id, group_id, log_prefix):
            continue
        await client.update_channel(channel_id, {"channel_group_id": target["id"]})
        moved += 1
        if on_channel_moved is not None:
            on_channel_moved(channel_id, member_names[channel_id], target)
    # What actually moved, not what was read — the count is reported to the
    # operator as ``channels_moved``, and a skipped channel did not move.
    return moved
