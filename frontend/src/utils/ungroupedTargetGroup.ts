/**
 * Where a channel goes when the group it was in is deleted.
 *
 * Bead `enhancedchannelmanager-ayfn9`, live re-drive 2026-08-09.
 *
 * ECM's Delete Group dialog promised "The channels will be moved to
 * 'Ungrouped'", and the commit then issued a bare DELETE that Dispatcharr
 * refused (`400 {"error":"Cannot delete group with associated channels"}`).
 * Reparenting to `null` does not fix it either — a Dispatcharr channel row
 * REQUIRES a group, measured against a live 0.28.2 instance:
 *
 * ```
 * PATCH /api/channels/channels/1/ {"channel_group_id": null}
 *   -> 400 {"channel_group_id":["This field may not be null."]}
 * PATCH /api/channels/channels/1/ {"channel_group_id": 378}
 *   -> 200
 * ```
 *
 * So "Ungrouped" (`channel_group_id === null`) is a state ECM can RENDER — it
 * is a shape Dispatcharr can return for rows ECM did not write, and the channel
 * list buckets them — but never a state ECM can WRITE. The move targets
 * Dispatcharr's own baseline group instead.
 *
 * This module is the frontend half of a contract whose other half is
 * `UNGROUPED_TARGET_GROUP_NAME` in `backend/routers/channels.py`, which performs
 * the actual move. They must agree: the dialog promises what the backend does.
 */

/**
 * The name of Dispatcharr's baseline group.
 *
 * Dispatcharr ships it and falls back to it when a channel is created without a
 * group, which is why a channel committed with no group turns up there
 * (`docs/user_guide/getting-started/your-first-channels.md`).
 */
export const UNGROUPED_TARGET_GROUP_NAME = 'Default Group';

/** The minimum a group has to carry to be resolvable as the target. */
interface NamedGroup {
  id: number;
  name: string;
}

/**
 * Resolve {@link UNGROUPED_TARGET_GROUP_NAME} against a group list.
 *
 * Matched on a trimmed, case-folded name — never on an id. The group is id 1 on
 * a fresh Dispatcharr install and on the drill instance, but that is an
 * observation about a default, not a contract, and an operator can rename or
 * renumber their way out of it.
 *
 * `undefined` means a delete that needs to move channels will fail, which the
 * caller is expected to say up front rather than promise a move that cannot
 * happen.
 */
export function findUngroupedTargetGroup<T extends NamedGroup>(
  groups: T[],
): T | undefined {
  const wanted = UNGROUPED_TARGET_GROUP_NAME.toLowerCase();
  return groups.find((group) => group.name.trim().toLowerCase() === wanted);
}
